from __future__ import annotations
from typing import Optional
import re
from python.helpers.tool import Tool, Response
from python.helpers.secrets_helper import get_secrets_manager, get_default_secrets_manager, get_project_secrets_manager
from python.helpers import projects
from python.helpers.print_style import PrintStyle

# Patterns that indicate a value is likely NOT a secret and should be a parameter
NON_SECRET_KEY_PATTERNS = [
    r'.*_url$',           # URLs are typically config, not secrets
    r'.*_uri$',           # URIs are typically config
    r'.*_path$',          # File paths
    r'.*_dir$',           # Directories
    r'.*_count$',         # Numeric counters
    r'.*_limit$',         # Limits
    r'.*_size$',          # Sizes
    r'.*_enabled$',       # Boolean flags
    r'.*_disabled$',      # Boolean flags
    r'.*_name$',          # Names
    r'.*_id$',            # IDs (unless specifically API ID)
    r'.*_version$',       # Versions
    r'.*_mode$',          # Mode settings
    r'.*_type$',          # Type settings
    r'.*_format$',        # Format settings
    r'.*_timeout$',       # Timeouts
    r'.*_interval$',      # Intervals
    r'.*_port$',          # Port numbers
    r'.*_host$',          # Hostnames
]

# Patterns that ARE secrets (override non-secret patterns)
SECRET_KEY_PATTERNS = [
    r'.*api[_-]?key.*',
    r'.*secret.*',
    r'.*token.*',
    r'.*password.*',
    r'.*passwd.*',
    r'.*credential.*',
    r'.*auth.*key.*',
    r'.*private[_-]?key.*',
    r'.*access[_-]?key.*',
    r'.*bearer.*',
    r'.*oauth.*',
]

# Valid key naming pattern: UPPER_SNAKE_CASE with optional hyphens for project prefix
# e.g., GITHUB_TOKEN, BITDRAMA-FORGEJO-TOKEN
VALID_KEY_PATTERN = re.compile(r'^[A-Z][A-Z0-9]*([_-][A-Z0-9]+)*$')

def is_likely_secret(key: str) -> bool:
    """Check if a key name suggests it IS a secret."""
    key_lower = key.lower()
    for pattern in SECRET_KEY_PATTERNS:
        if re.match(pattern, key_lower):
            return True
    return False

def is_likely_non_secret(key: str) -> bool:
    """Check if a key name suggests it is NOT a secret (config/parameter)."""
    key_lower = key.lower()
    # First check if it looks like a secret - if so, it's fine
    if is_likely_secret(key):
        return False
    # Then check if it matches non-secret patterns
    for pattern in NON_SECRET_KEY_PATTERNS:
        if re.match(pattern, key_lower):
            return True
    return False

def normalize_key(key: str) -> str:
    """Normalize a key to UPPER_SNAKE_CASE with hyphens for project prefixes.
    
    Examples:
        'github_token' -> 'GITHUB_TOKEN'
        'bitdrama-github-token' -> 'BITDRAMA-GITHUB-TOKEN'
    """
    key = key.strip().upper()
    key = key.replace(' ', '_')
    key = re.sub(r'[_]{2,}', '_', key)
    key = re.sub(r'[-]{2,}', '-', key)
    return key


class SecretSet(Tool):
    """
    Stores a secret in the secure secrets store.
    Secrets are stored encrypted and can be scoped globally or per-project.
    
    Use this for API keys, tokens, passwords, and other sensitive credentials.
    
    Key Naming Convention:
    - Use UPPER_SNAKE_CASE: `GITHUB_TOKEN`, `OPENROUTER_API_KEY`
    - For project-specific secrets, prefix with project: `BITDRAMA-GITHUB-TOKEN`
    - Structure: `<PROJECT>-<SERVICE>-<TYPE>` for clarity
    
    IMPORTANT: 
    - Secrets are masked in logs and chat history for security
    - Project-scoped secrets override global secrets with the same key
    - Use global scope for secrets shared across all projects
    - Non-secret values (URLs, paths, config) should use parameter_set instead
    """

    async def execute(
        self, 
        key: str = "",
        value: str = "",
        scope: str = "project",
        secrets: Optional[dict] = None,
        **kwargs
    ) -> Response:
        """
        Stores secret(s) with verification.
        
        Supports two modes:
        1. Single-key: provide key + value
        2. Batch: provide secrets dict {"KEY": "value", ...}
        
        Args:
            key (str): The unique identifier for the secret (single-key mode).
            value (str): The secret value to store (single-key mode).
            scope (str): Where to store: "project" (default) or "global".
            secrets (dict): Batch mode — dict of key-value pairs to store.
        """
        # Batch mode: store multiple secrets in one call
        if secrets and isinstance(secrets, dict):
            return await self._execute_batch(secrets, scope)
        
        # Single-key mode (original behavior)
        return await self._execute_single(key, value, scope)
    
    async def _execute_batch(self, secrets: dict, scope: str) -> Response:
        """Store multiple secrets atomically with per-key verification."""
        try:
            project_name = projects.get_context_project_name(self.agent.context)
            
            if scope == "project" and not project_name:
                return Response(
                    message="Error: No active project. Use scope='global' or activate a project first.",
                    break_loop=False
                )
            
            if scope == "project":
                manager = get_project_secrets_manager(project_name)
                scope_display = f"project '{project_name}'"
            else:
                manager = get_default_secrets_manager()
                scope_display = "global"
            
            results = []
            failures = []
            
            for raw_key, raw_value in secrets.items():
                key = normalize_key(raw_key)
                value = str(raw_value) if raw_value else ""
                
                if not key:
                    failures.append(f"❌ Empty key (from '{raw_key}')")
                    continue
                if not value:
                    failures.append(f"❌ '{key}': empty value")
                    continue
                if not VALID_KEY_PATTERN.match(key):
                    failures.append(f"❌ '{key}': invalid format (use UPPER_SNAKE_CASE)")
                    continue
                
                # Store
                manager.set_secret(key, value)
                
                # Verify
                readback = manager.get_secret(key)
                if readback is None:
                    failures.append(f"⚠️ '{key}': stored but verification FAILED (readback=None)")
                else:
                    results.append(f"✅ '{key}'")
            
            # Build result message
            msg_parts = [f"Batch secret_set in {scope_display}:"]
            if results:
                msg_parts.append(f"  Stored & verified: {', '.join(results)}")
            if failures:
                msg_parts.append(f"  Failures: {'; '.join(failures)}")
            msg_parts.append(f"  Total: {len(results)} stored, {len(failures)} failed")
            
            result_msg = "\n".join(msg_parts)
            PrintStyle.hint(f"Batch stored {len(results)} secrets in {scope_display}.")
            return Response(message=result_msg, break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error in batch secret_set: {e}", break_loop=True)

    async def _execute_single(self, key: str, value: str, scope: str) -> Response:
        """
        Stores a single secret value with verification (original behavior).
        """
        try:
            # Normalize key
            original_key = key
            key = normalize_key(key)
            
            if not key:
                return Response(message="Error: Key cannot be empty.", break_loop=False)
            
            if not value:
                return Response(message="Error: Value cannot be empty.", break_loop=False)
            
            # Validate key format
            if not VALID_KEY_PATTERN.match(key):
                return Response(
                    message=f"Error: Key '{key}' does not follow naming convention. "
                           f"Use UPPER_SNAKE_CASE with optional hyphens for prefixes. "
                           f"Examples: GITHUB_TOKEN, BITDRAMA-FORGEJO-TOKEN, OPENROUTER_API_KEY",
                    break_loop=False
                )
            
            # Normalization notice
            norm_msg = ""
            if key != original_key.strip():
                norm_msg = f"(key normalized: '{original_key.strip()}' → '{key}') "
            
            # Check if this looks like a non-secret (config/parameter)
            if is_likely_non_secret(key):
                norm_msg += (
                    f"⚠️ WARNING: '{key}' looks like a config parameter, not a secret. "
                    f"Consider using parameter_set instead. Proceeding anyway. "
                )
                PrintStyle(font_color="orange", bold=True).print(norm_msg)
            
            project_name = projects.get_context_project_name(self.agent.context)
            
            if scope == "project":
                if not project_name:
                    return Response(
                        message="Error: No active project. Use scope='global' or activate a project first.",
                        break_loop=False
                    )
                manager = get_project_secrets_manager(project_name)
                scope_display = f"project '{project_name}'"
                
                # Deduplication: inform if global also has this key
                global_manager = get_default_secrets_manager()
                global_value = global_manager.get_secret(key)
                if global_value is not None:
                    norm_msg += (
                        f"ℹ️ Note: '{key}' also exists globally. "
                        f"Project secret takes precedence. "
                    )
            else:  # global
                manager = get_default_secrets_manager()
                scope_display = "global"
                
                # Deduplication: warn if project already has this key
                if project_name:
                    proj_manager = get_project_secrets_manager(project_name)
                    proj_keys = proj_manager.get_keys()
                    if key in proj_keys:
                        norm_msg += (
                            f"⚠️ DEDUP WARNING: '{key}' already exists at project level "
                            f"(project '{project_name}'). Project-level takes precedence. "
                        )
                        PrintStyle(font_color="orange", bold=True).print(norm_msg)
            
            # Store the secret
            manager.set_secret(key, value)
            
            # POST-SET VERIFICATION: Read back and confirm
            readback = manager.get_secret(key)
            if readback is None:
                error_msg = (
                    f"⚠️ VERIFICATION FAILED: Set secret '{key}' but readback returned None. "
                    f"Secret may not have persisted correctly."
                )
                PrintStyle(font_color="red", bold=True).print(error_msg)
                return Response(message=error_msg, break_loop=False)
            
            # Don't compare full value (could be encrypted differently) — just verify it's present
            result_msg = f"{norm_msg}✅ Secret '{key}' stored in {scope_display} scope. [Verified: present]"
            PrintStyle.hint(f"Stored secret '{key}' in {scope_display} scope. [Verified]")
            return Response(message=result_msg, break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error storing secret: {e}", break_loop=True)


if __name__ == "__main__":
    pass
