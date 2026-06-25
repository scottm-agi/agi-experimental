from __future__ import annotations
import re
import threading
import time
import os
from io import StringIO
from dataclasses import dataclass
from typing import Dict, Optional, List, Literal, Set, Callable, Tuple, TYPE_CHECKING
from dotenv.parser import parse_stream
from python.helpers.errors import RepairableException
from python.helpers import files
from python.helpers import config_db

if TYPE_CHECKING:
    from python.agent import AgentContext


# New alias-based placeholder format §§secret(KEY)
ALIAS_PATTERN = r"§§secret\(([A-Za-z_][A-Za-z0-9_]*)\)"
LEGACY_SECRETS_FILE = "tmp/secrets.env"

# Patterns that indicate a value is a placeholder and should NOT be migrated to DB
PLACEHOLDER_PATTERNS = [
    "REPLACE",
    "PLACEHOLDER", 
    "ACTION_REQUIRED",
    "CHANGE_ME",
    "INSERT_",
    "YOUR_",
    "TODO",
    "TEST-KEY",
    "-TEST-CONTENT",
]

# ============================================================================
# SYSTEM API KEY FILTER
# These are LLM/tool provider API keys managed via .env / Settings > API Keys.
# They must NOT appear in the Secrets store, Secrets UI, or secrets DB.
# Agents access them via get_api_key() / os.environ — NOT via secrets.
# ============================================================================
SYSTEM_API_KEY_NAMES = frozenset({
    # OpenRouter
    "OPENROUTER_API_KEY", "API_KEY_OPENROUTER",
    # OpenAI
    "OPENAI_API_KEY", "API_KEY_OPENAI",
    # Anthropic
    "ANTHROPIC_API_KEY", "API_KEY_ANTHROPIC",
    # Google
    "GOOGLE_API_KEY", "API_KEY_GOOGLE",
    # Groq
    "GROQ_API_KEY", "API_KEY_GROQ",
    # Mistral
    "MISTRAL_API_KEY", "API_KEY_MISTRAL",
    # DeepSeek
    "DEEPSEEK_API_KEY", "API_KEY_DEEPSEEK",
    # xAI
    "XAI_API_KEY", "API_KEY_XAI",
    # Venice
    "VENICE_API_KEY", "API_KEY_VENICE",
    # Tool/Search providers
    "PERPLEXITY_API_KEY", "API_KEY_PERPLEXITY",
    "TAVILY_API_KEY", "API_KEY_TAVILY",
    "CONTEXT7_API_KEY", "API_KEY_CONTEXT7",
    "FIRECRAWL_API_KEY", "API_KEY_FIRECRAWL",
})

# ============================================================================
# UNIFIED OUTPUT FILTER — SYSTEM_KEYS_BLOCKLIST
# Single source of truth for ALL keys that must be filtered from .env.local
# output. Merges SYSTEM_API_KEY_NAMES (LLM provider keys managed via .env)
# with container infrastructure keys (formerly SYSTEM_BLOCKLIST in
# secret_materializer.py).
#
# Used by:
#   - ensure_env_before_delegation()  → filter before writing .env.local
#   - secret_materializer.py          → imported as SYSTEM_BLOCKLIST alias
#   - load_secrets()                  → via is_system_api_key() subset
#
# ADR-83 / System 1: Consolidates 2 independent filter lists into 1.
# ============================================================================
SYSTEM_KEYS_BLOCKLIST: frozenset = SYSTEM_API_KEY_NAMES | frozenset({
    # Container infrastructure (from former SYSTEM_BLOCKLIST in secret_materializer.py)
    "ROOT_PASSWORD",
    "RFC_PASSWORD",
    "FLASK_SECRET_KEY",
    "TIKTOKEN_CACHE_DIR",
    "TOKENIZERS_PARALLELISM",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "WEB_UI_DIR",
    "WORK_DIR",
    "LOG_LEVEL",
    "LOG_DIR",
    "PIP_CACHE_DIR",
    "XDG_CACHE_HOME",
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
})


def is_system_api_key(key: str) -> bool:
    """Check if a key is a system API key managed via .env, not secrets.
    
    System API keys are LLM/tool provider keys that are managed through
    the .env file and Settings > API Keys UI. They should NOT appear in
    the Secrets store, which is for user credentials like tokens, passwords,
    and webhook secrets.
    """
    return key.upper().strip() in SYSTEM_API_KEY_NAMES


def is_placeholder_value(value: str) -> bool:
    """Check if a value looks like a placeholder that shouldn't be stored.
    
    Returns True if the value contains common placeholder patterns.
    """
    if not value:
        return False
    upper = value.upper()
    return any(pattern in upper for pattern in PLACEHOLDER_PATTERNS)


def alias_for_key(key: str, placeholder: str = "§§secret({key})") -> str:
    # Return alias string for given key in upper-case
    key = key.upper()
    return placeholder.format(key=key)


@dataclass
class EnvLine:
    raw: str
    type: Literal["pair", "comment", "blank", "other"]
    key: Optional[str] = None
    value: Optional[str] = None
    inline_comment: Optional[str] = (
        None  # preserves trailing inline comment including leading spaces and '#'
    )


class StreamingSecretsFilter:
    """Stateful streaming filter that masks secrets on the fly.

    - Replaces full secret values with placeholders §§secret(KEY) when detected.
    - Holds the longest suffix of the current buffer that matches any secret prefix
      (with minimum trigger length of 3) to avoid leaking partial secrets across chunks.
    - On finalize(), any unresolved partial is masked with '***'.
    """

    def __init__(self, key_to_value: Dict[str, str], min_trigger: int = 3):
        self.min_trigger = max(1, int(min_trigger))
        # Map value -> key for placeholder construction
        self.value_to_key: Dict[str, str] = {
            v: k for k, v in key_to_value.items() if isinstance(v, str) and v
        }
        # Only keep non-empty values
        self.secret_values: List[str] = [v for v in self.value_to_key.keys() if v]
        # Precompute all prefixes for quick suffix matching
        self.prefixes: Set[str] = set()
        for v in self.secret_values:
            for i in range(self.min_trigger, len(v) + 1):
                self.prefixes.add(v[:i])
        self.max_len: int = max((len(v) for v in self.secret_values), default=0)

        # Internal buffer of pending text that is not safe to flush yet
        self.pending: str = ""

    def _replace_full_values(self, text: str) -> str:
        """Replace all full secret values with placeholders in the given text."""
        # Sort by length desc to avoid partial overlaps
        for val in sorted(self.secret_values, key=len, reverse=True):
            if not val:
                continue
            key = self.value_to_key.get(val, "")
            if key:
                text = text.replace(val, alias_for_key(key))
        return text

    def _longest_suffix_prefix(self, text: str) -> int:
        """Return length of longest suffix of text that is a known secret prefix.
        Returns 0 if none found (or only shorter than min_trigger)."""
        max_check = min(len(text), self.max_len)
        for length in range(max_check, self.min_trigger - 1, -1):
            suffix = text[-length:]
            if suffix in self.prefixes:
                return length
        return 0

    def process_chunk(self, chunk: str) -> str:
        if not chunk:
            return ""

        self.pending += chunk

        # Replace any full secret occurrences first
        self.pending = self._replace_full_values(self.pending)

        # Determine the longest suffix that could still form a secret
        hold_len = self._longest_suffix_prefix(self.pending)
        if hold_len > 0:
            # Flush everything except the hold suffix
            emit = self.pending[:-hold_len]
            self.pending = self.pending[-hold_len:]
        else:
            # Safe to flush everything
            emit = self.pending
            self.pending = ""

        return emit

    def finalize(self) -> str:
        """Flush any remaining buffered text. If pending contains an unresolved partial
        (i.e., a prefix of a secret >= min_trigger), mask it with *** to avoid leaks."""
        if not self.pending:
            return ""

        hold_len = self._longest_suffix_prefix(self.pending)
        if hold_len > 0:
            safe = self.pending[:-hold_len]
            # Mask unresolved partial
            result = safe + "***"
        else:
            result = self.pending
        self.pending = ""
        return result


class SecretsManager:
    PLACEHOLDER_PATTERN = ALIAS_PATTERN
    MASK_VALUE = "************" # Standardize on longer mask for UI consistency
    MASK_VALUES = ["***", "********", "************", "****PSWD****", "*********"]

    @classmethod
    def is_masked_value(cls, value: str) -> bool:
        """Check if a value is a masked placeholder (e.g. *** or ************)."""
        if not value: return False
        if value in cls.MASK_VALUES: return True
        # Also check if it's purely stars of at least 3 chars
        if len(value) >= 3 and all(c == "*" for c in value): return True
        return False

    _instances: Dict[Tuple[str, ...], "SecretsManager"] = {}
    _secrets_cache: Optional[Dict[str, str]] = None
    _last_raw_text: Optional[str] = None

    @classmethod
    def get_instance(cls, *secrets_files: str) -> "SecretsManager":
        if not secrets_files:
            secrets_files = (LEGACY_SECRETS_FILE,)
        key = tuple(secrets_files)
        if key not in cls._instances:
            cls._instances[key] = cls(*secrets_files)
        return cls._instances[key]

    def __init__(self, *files_list: str):
        self._lock = threading.RLock()
        self._state = threading.local()
        # instance-level list of secrets files (used for scope discovery)
        self._files: Tuple[str, ...] = tuple(files_list) if files_list else (LEGACY_SECRETS_FILE,)
        self._raw_snapshots: Dict[str, str] = {}
        self._secrets_cache = None
        self._cache_is_inherited = None
        self._last_raw_text = None
        self._last_sync_time: float = 0.0  # Throttle sync_to_environ

    def read_secrets_raw(self) -> str:
        """Read raw secrets file content from local filesystem (same system)."""
        parts: List[str] = []
        self._raw_snapshots = {}

        for path in self._files:
            try:
                content = files.read_file(path)
            except Exception:
                content = ""

            self._raw_snapshots[path] = content
            parts.append(content)

        combined = "\n".join(parts)
        self._last_raw_text = combined
        return combined

    def _write_secrets_raw(self, content: str):
        """Write raw secrets file content to local filesystem."""
        # Target the primary file (first in the list)
        if not self._files:
             raise RuntimeError("No secrets files configured for write.")
        files.write_file(self._files[0], content)

    def _get_scope_from_path(self, path: str) -> str:
        """Determine scope from a specific file path."""
        # Use absolute paths for reliable comparison
        abs_path = files.get_abs_path(path)
        abs_default = files.get_abs_path(LEGACY_SECRETS_FILE)

        # Check global
        if abs_path == abs_default or (os.path.basename(abs_path) == "secrets.env" and "tmp" not in abs_path and ".agix.proj" not in abs_path and ".agix.proj" not in abs_path):
            return "global"
        
        # Project-specific: extracts project name (supports both .agix.proj and legacy .agix.proj)
        for meta_dir in (".agix.proj", ".agix.proj"):
            if meta_dir in abs_path:
                norm_path = abs_path.replace("\\", "/")
                parts = norm_path.split(meta_dir)
                if len(parts) > 0:
                    # Extract project name from path like /path/to/project/.agix.proj/secrets.env
                    project_path = parts[0].rstrip("/")
                    return os.path.basename(project_path) or "global"
        
        # Chat-specific: extracts chat ID
        # Expected path: .../tmp/chats/{chat_id}/secrets.env
        if "tmp/chats" in abs_path:
            norm_path = abs_path.replace("\\", "/")
            parts = norm_path.split("/")
            try:
                chats_idx = parts.index("chats")
                if len(parts) > chats_idx + 1:
                    return parts[chats_idx + 1]
            except ValueError:
                pass
                
        return "global"

    def _get_scope(self) -> str:
        """Determine primarily active scope (first in list)."""
        if not self._files:
            return "global"
        return self._get_scope_from_path(self._files[0])

    def load_secrets(self, inherit: bool = True, include_external: bool = False) -> Dict[str, str]:
        """Load secrets from DB (with file fallback for migration) across all configured scopes."""
        with self._lock:
            if self._secrets_cache is not None and self._cache_is_inherited == inherit and not include_external:
                return self._secrets_cache

            merged_secrets: Dict[str, str] = {}
        
            # If not inheriting, we only load the primary scope
            files_to_load = self._files if inherit else (self._files[:1] if self._files else ())

            # Iterate through scopes (discovered from file paths)
            for path in reversed(files_to_load):
                scope = self._get_scope_from_path(path)
                
                # Try DB first (Source of Truth)
                scope_secrets = config_db.get_secrets(scope)
                
                if scope_secrets:
                    # PrintStyle.debug(f"[SecretsManager] Loaded {len(scope_secrets)} secrets from DB for scope '{scope}'")
                    pass  # DB loaded successfully
                else:
                    # Fallback to file for migration if DB is empty for this scope
                    try:
                        if os.path.exists(path):
                            content = files.read_file(path)
                            scope_secrets = self.parse_env_content(content)
                            # Filter out placeholder values before migration
                            # This prevents "REPLACE_ME" type placeholders from polluting the DB
                            scope_secrets = {
                                k: v for k, v in scope_secrets.items()
                                if not is_placeholder_value(v)
                            }
                            # Migrate to DB if we found real (non-placeholder) secrets
                            if scope_secrets:
                                config_db.set_secrets(scope_secrets, scope)
                                from python.helpers.print_style import PrintStyle # local import
                                PrintStyle.info(f"[SecretsManager] Migrated {len(scope_secrets)} secrets from {path} to DB")
                    except Exception:
                        scope_secrets = {}
                
                if scope_secrets:
                    merged_secrets.update(scope_secrets)

            # Filter out system API keys that may have leaked into the DB
            # from previous env_integrity syncs or the old env→secrets bridge.
            # API keys belong in .env, not the secrets store.
            merged_secrets = {
                k: v for k, v in merged_secrets.items()
                if not is_system_api_key(k)
            }

            # -----------------------------------------------------------------
            # Unified Retrieval: Include environment fallback
            # -----------------------------------------------------------------
            # NOTE (2026-06-15): The env→secrets bridge that previously pulled
            # ALL env vars matching API_KEY/TOKEN/PASSWORD/SECRET into the secrets
            # dict has been REMOVED. It was the root cause of API keys appearing
            # in the Secrets UI (screenshot evidence from user).
            #
            # System API keys (OPENROUTER_API_KEY, etc.) are managed via .env /
            # Settings > API Keys and accessed by agents through:
            #   - get_api_key() in credentials.py
            #   - os.environ fallback in get_secret()
            #   - env_integrity.startup_sync() for Railway env
            #
            # User secrets (GITHUB_TOKEN, webhook secrets, passwords) live in
            # the secrets DB and are loaded above from config_db.get_secrets().
            #
            # The env→secrets bridge is no longer needed and was causing:
            # 1. API keys appearing in Secrets UI
            # 2. 'None' values being persisted as real secrets
            # 3. Circular sync between env and secrets
            if include_external:
                # Only pull non-API-key credentials from env as a safety net.
                # API keys are explicitly excluded — they have their own path.
                for env_key, env_val in os.environ.items():
                    if env_val and not self.is_masked_value(env_val):
                        k_up = env_key.upper()
                        if is_system_api_key(k_up):
                            continue  # API keys are NOT secrets
                        if any(pattern in k_up for pattern in ["TOKEN", "PASSWORD", "SECRET"]):
                            if k_up not in merged_secrets:
                                merged_secrets[k_up] = env_val

            # Special case for raw text (legacy UI support)
            # We use the primary scope's raw content
            primary_scope = self._get_scope()
            self._last_raw_text = config_db.get_secrets_as_env(primary_scope)

            # Only cache if not including external (since external is dynamic)
            if not include_external:
                self._secrets_cache = merged_secrets
                self._cache_is_inherited = inherit
            
            return merged_secrets

    def sync_to_environ(self, force: bool = False):
        """Synchronize GLOBAL secrets to os.environ for sub-process accessibility.
        
        IMPORTANT: Only global-scoped secrets are synced to os.environ.
        Project-scoped secrets are NOT synced because os.environ is process-global
        and would cause cross-project conflicts. Project secrets are accessed via:
        - DB reads (manager.get_secret())
        - .env files (for Node.js apps via process.env)
        
        Also maps standard A0 keys (API_KEY_X) to common tool formats (X_API_KEY)
        for maximum compatibility with LiteLLM and other libraries.
        
        Throttled to run at most once every 30 seconds unless force=True.
        """
        # Only sync if this is a global-scoped manager
        scope = self._get_scope()
        if scope != "global":
            return  # Project secrets must NOT leak into os.environ
        
        now = time.monotonic()
        if not force and (now - self._last_sync_time) < 30.0:
            return  # Already synced recently
        self._last_sync_time = now
        secrets = self.load_secrets(inherit=False)  # Only global secrets, no inheritance
        for k, v in secrets.items():
            if v and not self.is_masked_value(v):
                os.environ[k] = v
                
                # Map API_KEY_X to X_API_KEY (e.g., API_KEY_OPENROUTER -> OPENROUTER_API_KEY)
                if k.startswith("API_KEY_"):
                    provider_part = k.replace("API_KEY_", "", 1)
                    if provider_part:
                        alt_key = f"{provider_part}_API_KEY"
                        if alt_key not in os.environ or not os.environ[alt_key]:
                            os.environ[alt_key] = v
                        
                        # Special handling for tokens
                        alt_token = f"{provider_part}_API_TOKEN"
                        if alt_token not in os.environ or not os.environ[alt_token]:
                            os.environ[alt_token] = v

    def save_secrets(self, secrets_content: str):
        """Save secrets content to file and update cache"""
        with self._lock:
            self._write_secrets_raw(secrets_content)
        self._invalidate_all_caches()

    def save_secrets_with_merge(self, submitted_content: str, replace: bool = True):
        """Merge submitted content with existing secrets, preserving masked values.
        - Existing keys keep their value when submitted as MASK_VALUE (***).
        - If replace=True: Keys present in existing but omitted from submitted are deleted.
        - If replace=False: Existing keys not in submitted are preserved.
        - New keys with non-masked values are added.
        """
        from python.helpers.print_style import PrintStyle
        
        scope = self._get_scope()
        
        with self._lock:
            # IMPORTANT: Get existing secrets for JUST this scope (no inheritance)
            # to avoid flattening global secrets into project file
            existing_secrets = self.load_secrets(inherit=False)
            
            # Parse submitted content
            submitted_secrets = self.parse_env_content(submitted_content)
            
            # Minimal debug logging for save operations
            PrintStyle.debug(f"[SecretsManager] save_secrets_with_merge: scope={scope}, existing={len(existing_secrets)}, submitted={len(submitted_secrets)}")
            
            # Safeguard: If submitted is empty but we have existing secrets, 
            # and the content is just whitespace, it might be an accidental wipe due to UI loading failure.
            # We only allow explicit deletion if the user really intended to.
            if not submitted_secrets and existing_secrets and not submitted_content.strip() and replace:
                # If we have existing data but got an empty string, it's likely a load failure in UI.
                # Skip saving to prevent data loss.
                PrintStyle.info(f"[SecretsManager] Skipping save - empty submission with existing data (preventing accidental wipe)")
                return

            # Build merged secrets dict
            merged_secrets = existing_secrets.copy() if not replace else {}
            
            for key, value in submitted_secrets.items():
                # Strip system API keys — they belong in .env, not secrets DB
                if is_system_api_key(key):
                    continue
                if self.is_masked_value(value):
                    # Preserve existing value for masked entries
                    if key in existing_secrets:
                        merged_secrets[key] = existing_secrets[key]
                    # else: masked but not in existing - skip
                elif is_placeholder_value(value) and key in existing_secrets and not is_placeholder_value(existing_secrets[key]):
                    # If new value is a placeholder, but existing is real, preserve existing
                    merged_secrets[key] = existing_secrets[key]
                else:
                    # Use new value (either not a placeholder, or existing was also a placeholder)
                    merged_secrets[key] = value
            

            
            # Save to DB atomically
            config_db.set_secrets(merged_secrets, scope, replace=replace)
            
            # Also write to file for backward compatibility
            # Ensure we escape double quotes if they exist in values
            lines = []
            for k, v in merged_secrets.items():
                v_escaped = str(v).replace('"', '\\"')
                lines.append(f'{k}="{v_escaped}"')
            merged_text = "\n".join(lines)
            self._write_secrets_raw(merged_text)
            
            # Update raw text cache
            self._last_raw_text = merged_text
            
            PrintStyle.debug(f"[SecretsManager] Saved {len(merged_secrets)} secrets to scope={scope}")
        
        self._invalidate_all_caches()

    def get_secret(self, key: str, default: Optional[str] = None, include_external: bool = True) -> Optional[str]:
        """
        Get a secret by key.
        Searches loaded secrets (db/files) and optionally falls back to os.environ.
        
        Args:
            key: Secret key
            default: Default value if not found
            include_external: Whether to fall back to os.environ. Set to False for strict DB checks.
        """
        k_up = key.upper().strip()
        
        # Load secrets. load_secrets internally filters based on its own logic, 
        # but get_secret handles the final OS fallback.
        secrets = self.load_secrets(include_external=include_external)
        val = secrets.get(k_up)

        if (val is None or val == "None") and include_external:
            # Direct OS check as final fallback if enabled
            val = os.environ.get(k_up)

        if val is None or val == "None" or self.is_masked_value(val):
            return default
        return val
        

    def set_secret(self, key: str, value: str):
        """Sets a specific secret in the target scope.
        
        Uses config_db.set_secret directly to avoid .env serialization issues
        with JSON values that contain quotes and special characters.
        """
        key = key.upper().strip()
        
        # Guard: Strip KEY= prefix if value accidentally includes it
        # This happens when agents pass "KEY=value" as the value parameter
        if isinstance(value, str) and value.upper().startswith(f"{key}="):
            value = value[len(key) + 1:]  # Strip "KEY=" prefix
            # Also strip surrounding quotes if present (e.g. KEY="value")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
        
        scope = self._get_scope()
        
        with self._lock:
            # Use config_db directly to avoid .env parsing issues with JSON
            config_db.set_secret(key, value, scope)
            
            # Also update the file for backward compatibility (best-effort)
            try:
                secrets = self.load_secrets(inherit=False)
                secrets[key] = value
                # Use double quotes and escape existing ones for consistency
                lines = []
                for k, v in secrets.items():
                    v_escaped = str(v).replace('"', '\\"')
                    lines.append(f'{k}="{v_escaped}"')
                merged_text = "\n".join(lines)
                self._write_secrets_raw(merged_text)
            except Exception:
                pass  # File write is best-effort, DB is authoritative
            
            self._invalidate_all_caches()
        # Only sync to os.environ for global secrets — project secrets go to
        # .env files and DB only (os.environ is process-global and would conflict)
        if scope == "global":
            self.sync_to_environ(force=True)

    def delete_secret(self, key: str):
        """Deletes a specific secret from the target scope.
        
        Uses config_db.delete_secret and updates the file for backward compatibility.
        """
        key = key.upper().strip()
        scope = self._get_scope()
        
        with self._lock:
            # Delete from DB (Authoritative)
            config_db.delete_secret(key, scope)
            
            # Also update the file for backward compatibility
            try:
                secrets = self.load_secrets(inherit=False)
                if key in secrets:
                    del secrets[key]
                    # Rewrite the file without this key
                    lines = []
                    for k, v in secrets.items():
                        v_escaped = str(v).replace('"', '\\"')
                        lines.append(f'{k}="{v_escaped}"')
                    merged_text = "\n".join(lines)
                    self._write_secrets_raw(merged_text)
            except Exception:
                pass # File write is best-effort, DB is authoritative
                
            self._invalidate_all_caches()
        
        # Remove from os.environ if present
        if key in os.environ:
            del os.environ[key]
        # Also check for common tool format aliases
        if key.startswith("API_KEY_"):
            provider_part = key.replace("API_KEY_", "", 1)
            if provider_part:
                alt_key = f"{provider_part}_API_KEY"
                if alt_key in os.environ:
                    del os.environ[alt_key]
                alt_token = f"{provider_part}_API_TOKEN"
                if alt_token in os.environ:
                    del os.environ[alt_token]


    def get_keys(self, include_external: bool = True) -> List[str]:
        """Get list of secret keys"""
        secrets = self.load_secrets(include_external=include_external)
        return list(secrets.keys())

    def get_secrets_for_prompt(self) -> str:
        """Get formatted string of secret keys for system prompt"""
        content = self.read_secrets_raw()
        if not content:
            return ""

        env_lines = self.parse_env_lines(content)
        return self._serialize_env_lines(
            env_lines,
            with_values=False,
            with_comments=True,
            with_blank=True,
            with_other=True,
            key_formatter=alias_for_key,
        )

    def create_streaming_filter(self) -> "StreamingSecretsFilter":
        """Create a streaming-aware secrets filter snapshotting current secret values."""
        return StreamingSecretsFilter(self.load_secrets())

    def replace_placeholders(self, text: str) -> str:
        """Replace secret placeholders with actual values"""
        if not text:
            return text

        # Import locally to avoid circular dependencies if any
        from python.helpers.errors import MissingSecretException

        secrets = self.load_secrets()

        def replacer(match):
            key = match.group(1)
            key = key.upper()
            if key in secrets:
                return secrets[key]
            else:
                available_keys = ", ".join(secrets.keys())
                error_msg = f"Secret placeholder '{alias_for_key(key)}' not found in secrets store.\n"
                error_msg += f"Available secrets: {available_keys}"

                raise MissingSecretException(error_msg)

        return re.sub(self.PLACEHOLDER_PATTERN, replacer, text)

    def get_missing_placeholders(self, text: str) -> List[str]:
        """Identify missing secret placeholders in the text without raising an exception."""
        if not text:
            return []

        secrets = self.load_secrets()
        missing = []

        matches = re.finditer(self.PLACEHOLDER_PATTERN, text)
        for match in matches:
            key = match.group(1).upper()
            if key not in secrets:
                missing.append(alias_for_key(key))

        return list(set(missing))  # Return unique missing placeholders



    def mask_values(self, text: str, placeholder: Optional[str] = None) -> str:
        """Mask all secret values found in the given text.
        Uses a thread-local flag to prevent infinite recursion during logging.
        """
        if not text or not isinstance(text, str):
            return text
            
        # Prevent infinite recursion if load_secrets or get_secrets logs something
        if getattr(self._state, "is_masking", False):
            return text
            
        try:
            self._state.is_masking = True
            secrets = self.load_secrets(inherit=True, include_external=False) # Use DB secrets for masking
            if not secrets:
                return text
                
            sorted_secrets = sorted(
                [(k, v) for k, v in secrets.items() if v and len(v) > 3 and not self.is_masked_value(v)],
                key=lambda x: len(x[1]),
                reverse=True
            )
            
            masked_text = text
            for key, value in sorted_secrets:
                # Only mask if value is long enough to avoid false positives (e.g. "key")
                if len(value) > 4:
                    if placeholder and "{key}" in placeholder:
                        masked_text = masked_text.replace(value, placeholder.format(key=key))
                    else:
                        masked_text = masked_text.replace(value, f"{{{{SECRET_{key}}}}}")
            return masked_text
        finally:
            self._state.is_masking = False

    def _is_sensitive_key(self, key: str) -> bool:
        """Heuristic to check if a key likely contains sensitive information."""
        k = key.upper()
        # If it's a known common secret name or contains these patterns
        patterns = [
            "KEY", "TOKEN", "PASSWORD", "PWD", "SECRET", "AUTH",
            "CREDENTIAL", "APIKEY", "ACCESS", "PRIVATE", "CERT", "JSON",
            "TOKEN", "SESSION", "PAT", "SID", "JWT",
        ]
        return any(p in k for p in patterns)

    def is_masked_value(self, value: str) -> bool:
        """Check if a value appears to already be masked."""
        if not value or not isinstance(value, str):
             return False
        return value.startswith("§§secret") or value.startswith("{{SECRET_") or value == self.MASK_VALUE

    def get_masked_secrets(self) -> str:
        """Get content with values masked for frontend display"""
        return self.get_formatted_secrets(masked=True)

    def get_formatted_secrets(self, masked: bool = True) -> str:
        """Get content with values (optionally masked) for frontend display.
        
        IMPORTANT: Uses database as the authoritative source of truth.
        File content is only used for preserving comments/formatting.
        All keys from DB are included, even if not in file.
        """
        # Load secrets from the truth source (DB + files merged)
        # Note: We use inherit=False because the UI usually manages one scope at a time
        secrets_map = self.load_secrets(inherit=False)
        
        # Strip system API keys from display — they belong in Settings > API Keys
        secrets_map = {k: v for k, v in secrets_map.items() if not is_system_api_key(k)}
        
        # Get raw content for formatting (comments, order, etc.)
        content = self.read_secrets_raw()
        
        if not content:
            # If file is empty but we have data in DB, generate a basic env content
            if secrets_map:
                if masked:
                    env_content = "\n".join([f'{k}="{self.MASK_VALUE}"' for k in sorted(secrets_map.keys())])
                else:
                    env_content = "\n".join([f'{k}="{v}"' for k, v in sorted(secrets_map.items())])
                return env_content
            return ""

        # Parse lines to preserve comments/order
        env_lines = self.parse_env_lines(content)
        
        # Strip system API keys from file-sourced lines — they belong in .env
        env_lines = [
            ln for ln in env_lines
            if ln.type != "pair" or ln.key is None or not is_system_api_key(ln.key)
        ]
        
        # Track which keys from DB we've seen in the file
        processed_keys: Set[str] = set()

        # Replace values with mask for keys present in the truth source
        for ln in env_lines:
            if ln.type == "pair" and ln.key is not None:
                key = ln.key.upper()
                processed_keys.add(key)
                # If key is in our truth source, update its value
                if key in secrets_map:
                    if masked and secrets_map[key] != "":
                        ln.value = self.MASK_VALUE
                    else:
                        ln.value = secrets_map[key]
        
        # CRITICAL FIX: Add any keys from DB that weren't in the file
        # This ensures all database secrets are displayed in UI
        for key in sorted(secrets_map.keys()):
            if key not in processed_keys:
                value = self.MASK_VALUE if masked and secrets_map[key] != "" else secrets_map[key]
                env_lines.append(EnvLine(
                    raw=f'{key}="{value}"',
                    type="pair",
                    key=key,
                    value=value,
                    inline_comment=None
                ))
        
        # Re-serialize
        return self._serialize_env_lines(env_lines)

    def parse_env_content(self, content: str) -> Dict[str, str]:
        """Parse .env format content into key-value dict using python-dotenv. Keys are always uppercase.
        
        Hardens parsing by ignoring keys that appear to be malformed JSON fragments 
        (e.g., containing }, ], or starting with quotes).
        """
        env: Dict[str, str] = {}
        for binding in parse_stream(StringIO(content)):
            if binding.key and not binding.error:
                key = binding.key.strip()
                # Ignore keys that look like JSON fragments leaked from UI/API errors
                # Filtering keys starting with quote or containing JSON delimiters
                if any(c in key for c in ('}', ']', '{', '[', ':', '"')):
                    continue
                env[key.upper()] = binding.value or ""

        return env


    # Backward-compatible alias for callers using the old private method name
    def _parse_env_content(self, content: str) -> Dict[str, str]:
        return self.parse_env_content(content)

    def clear_cache(self):
        """Clear the secrets cache"""
        with self._lock:
            self._secrets_cache = None
            self._cache_is_inherited = None
            self._raw_snapshots = {}
            self._last_raw_text = None
            self._last_sync_time = 0.0  # Reset throttle so next access re-syncs

    @classmethod
    def _invalidate_all_caches(cls):
        for instance in cls._instances.values():
            instance.clear_cache()

    # ---------------- Internal helpers for parsing/merging ----------------

    def parse_env_lines(self, content: str) -> List[EnvLine]:
        """Parse env file into EnvLine objects using python-dotenv, preserving comments and order.
        We reconstruct key_part and inline_comment based on the original string.
        """
        lines: List[EnvLine] = []
        for binding in parse_stream(StringIO(content)):
            orig = getattr(binding, "original", None)
            raw = getattr(orig, "string", "") if orig is not None else ""
            if binding.key and not binding.error:
                # Determine key_part and inline_comment from original line
                line_text = raw.rstrip("\n")
                # Fallback to composed key_part if original not available
                if "=" in line_text:
                    left, right = line_text.split("=", 1)
                else:
                    right = ""
                # Try to extract inline comment by scanning right side to comment start, respecting quotes
                in_single = False
                in_double = False
                esc = False
                comment_index = None
                for i, ch in enumerate(right):
                    if esc:
                        esc = False
                        continue
                    if ch == "\\":
                        esc = True
                        continue
                    if ch == "'" and not in_double:
                        in_single = not in_single
                        continue
                    if ch == '"' and not in_single:
                        in_double = not in_double
                        continue
                    if ch == "#" and not in_single and not in_double:
                        comment_index = i
                        break
                inline_comment = None
                if comment_index is not None:
                    inline_comment = right[comment_index:]
                lines.append(
                    EnvLine(
                        raw=line_text,
                        type="pair",
                        key=binding.key,
                        value=binding.value or "",
                        inline_comment=inline_comment,
                    )
                )
            else:
                # Comment, blank, or other lines
                raw_line = raw.rstrip("\n")
                if raw_line.strip() == "":
                    lines.append(EnvLine(raw=raw_line, type="blank"))
                elif raw_line.lstrip().startswith("#"):
                    lines.append(EnvLine(raw=raw_line, type="comment"))
                else:
                    lines.append(EnvLine(raw=raw_line, type="other"))
        return lines

    def _serialize_env_lines(
        self,
        lines: List[EnvLine],
        with_values=True,
        with_comments=True,
        with_blank=True,
        with_other=True,
        key_delimiter="",
        key_formatter: Optional[Callable[[str], str]] = None,
    ) -> str:
        out: List[str] = []
        for ln in lines:
            if ln.type == "pair" and ln.key is not None:
                left_raw = ln.key
                left = left_raw.upper()
                val = ln.value if ln.value is not None else ""
                comment = ln.inline_comment or ""
                formatted_key = (
                    key_formatter(left)
                    if key_formatter
                    else f"{key_delimiter}{left}{key_delimiter}"
                )
                val_part = f'="{val}"' if with_values else ""
                # Ensure we handle comments correctly - parse_env_lines already extracts them
                comment_part = f" {comment}" if with_comments and comment else ""
                out.append(f"{formatted_key}{val_part}{comment_part}")
            elif ln.type == "blank" and with_blank:
                out.append(ln.raw)
            elif ln.type == "comment" and with_comments:
                out.append(ln.raw)
            elif ln.type == "other" and with_other:
                out.append(ln.raw)
        return "\n".join(out)


def get_secrets_manager(context: "AgentContext|str|None" = None) -> SecretsManager:
    from python.helpers import projects

    # default secrets file (Legacy fallback)
    secret_files = [LEGACY_SECRETS_FILE]

    # use AgentContext from contextvars if no context provided
    if not context:
        from python.agent import AgentContext
        context = AgentContext.current()

    # merged with project secrets if active
    if context:
        chat_id = context if isinstance(context, str) else context.id
        project = projects.get_context_project_name(context)
        if project:
            # Project secrets override global ones
            project_file = files.get_abs_path(projects.get_project_meta_folder(project), "secrets.env")
            secret_files.insert(0, project_file)
            
        # Chat secrets override everything
        if chat_id:
            chat_file = files.get_abs_path("tmp/chats", chat_id, "secrets.env")
            if chat_file not in secret_files:
                secret_files.insert(0, chat_file)

    return SecretsManager.get_instance(*secret_files)

def get_project_secrets_manager(project_name: str, merge_with_global: bool = False) -> SecretsManager:
    from python.helpers import projects

    # Project secrets override global ones, so project file must be FIRST in the list
    secret_files = [files.get_abs_path(projects.get_project_meta_folder(project_name), "secrets.env")]

    if merge_with_global:
        secret_files.append(LEGACY_SECRETS_FILE)

    return SecretsManager.get_instance(*secret_files)

def get_default_secrets_manager() -> SecretsManager:
    return SecretsManager.get_instance(LEGACY_SECRETS_FILE)
