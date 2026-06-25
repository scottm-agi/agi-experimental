"""
Pre-Delegation Env Bridge — Bridge secrets to .env.local before delegation.

RCA-232 Fix 4 (MISS 3): Secrets stored via `secret_set` go to the AGIX
vault, NOT to `.env.local`. When code agents try to use env vars, they
don't exist. This module bridges the gap by creating/updating `.env.local`
from vault secrets BEFORE delegation begins.

F-10: Returns a structured EnvBridgeResult instead of bare bool so that
the orchestrator can warn about missing secrets.

F-7 (RCA-400): Adds placeholder value detection. When vault secrets have
dummy values like 'val1', 'test', 'changeme', the bridge logs a WARNING
and populates result.placeholder_keys so the orchestrator can act on it.

Usage:
    from python.helpers.pre_delegation_env_bridge import ensure_env_before_delegation
    result = ensure_env_before_delegation(project_dir, project_name)
    if result:  # backward compat — truthy when provisioned
        ...
    if result.missing_keys:  # keys the prompt needs but vault lacks
        ...
    if result.placeholder_keys:  # keys with dummy/placeholder values
        ...
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

from python.helpers import config_db
from python.helpers.secrets_helper import is_system_api_key

logger = logging.getLogger("agix.pre_delegation_env_bridge")

# ── F-7 (RCA-400): Known placeholder / dummy values ──
# These are values that agents or users sometimes store in the vault as
# stand-ins.  They look like secrets syntactically (KEY=VALUE) but will
# cause 500 errors at runtime because no API will accept them.
PLACEHOLDER_VALUES: frozenset = frozenset({
    "val1", "val2", "val3",
    "test", "testing",
    "placeholder", "changeme", "change_me",
    "xxx", "xxxx", "xxxxxxxx",
    "your_key_here", "your-key-here",
    "replace_me", "replace-me",
    "todo", "fixme",
    "none", "null", "undefined",
    "example", "demo", "sample",
    "secret", "password", "api_key",
    "insert_key", "insert-key",
    "key", "value",
})

# Minimum length for a value to be considered "real".  Most API keys,
# database URLs, and tokens are >= 10 characters.  Shorter values are
# almost certainly placeholders or port numbers passed by mistake.
_MIN_VALUE_LENGTH = 10

# ── F-5 (ITR-25): Template marker pattern ──
# Detects {{...}} double-brace template markers that users sometimes store
# in the vault as stand-ins for real values.  e.g., {{RESEND_API_KEY}},
# Bearer {{TOKEN}}, etc.
_TEMPLATE_MARKER_RE = re.compile(r'\{\{[^}]+\}\}')


def detect_placeholder_values(secrets: Dict[str, str]) -> List[str]:
    """Detect vault secrets whose values look like placeholders.

    A value is considered a placeholder if:
    1. It is empty, OR
    2. Its lowercased form is in PLACEHOLDER_VALUES, OR
    3. Its length is < _MIN_VALUE_LENGTH, OR
    4. It contains a {{...}} template marker (F-5, ITR-25).

    Args:
        secrets: Dict of env_var_name → env_var_value.

    Returns:
        Sorted list of env var names whose values are placeholders.
    """
    if not secrets:
        return []
    flagged: List[str] = []
    for key, value in secrets.items():
        val = value.strip()
        if (
            not val
            or val.lower() in PLACEHOLDER_VALUES
            or len(val) < _MIN_VALUE_LENGTH
            or _TEMPLATE_MARKER_RE.search(val)  # F-5: {{...}} template markers
        ):
            flagged.append(key)
    return sorted(flagged)


# ── F-6 (RCA-470): Framework subdirectory discovery ──────────────────────
# Framework config files that indicate a directory needs its own .env file.
# When a project has `web/next.config.mjs`, Next.js reads .env from `web/`,
# NOT from the project root. Same for Vite, Nuxt, Remix, Docker, etc.
_FRAMEWORK_CONFIG_FILES: frozenset = frozenset({
    # Next.js
    "next.config.mjs", "next.config.js", "next.config.ts",
    # Vite
    "vite.config.ts", "vite.config.js", "vite.config.mjs",
    # Nuxt
    "nuxt.config.ts", "nuxt.config.js",
    # Remix
    "remix.config.js", "remix.config.ts",
    # Docker
    "docker-compose.yml", "docker-compose.yaml",
    # Wrangler (Cloudflare Workers)
    "wrangler.toml", "wrangler.json",
})

# Directories to skip during discovery (performance + correctness)
_SKIP_DIRS: frozenset = frozenset({
    "node_modules", ".git", ".next", ".nuxt", "dist", "build",
    "__pycache__", ".venv", "venv", ".turbo", ".vercel",
})


def discover_env_targets(project_dir: str) -> List[str]:
    """Discover all directories in a project that need .env files.

    F-6 (RCA-470): Scans the project tree for directories containing
    framework config files (next.config.*, vite.config.*, docker-compose.yml,
    etc.). Each such directory is returned as a target for .env file writing.

    The project root is always included as the first target.

    Args:
        project_dir: Absolute path to the project root directory.

    Returns:
        Deduplicated, sorted list of directory paths that need .env files.
        The project root is always the first element.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return [project_dir] if project_dir else []

    targets = {os.path.normpath(project_dir)}  # Always include root

    # Walk one level deep first (most projects have web/, app/, etc.)
    # Then walk deeper for monorepo structures (packages/web/, etc.)
    for root, dirs, files in os.walk(project_dir):
        # Skip irrelevant directories
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        # Check if this directory has a framework config
        for f in files:
            if f in _FRAMEWORK_CONFIG_FILES:
                targets.add(os.path.normpath(root))
                break  # One match per directory is enough

    # Return sorted, deduplicated list with project root first
    root_norm = os.path.normpath(project_dir)
    result = [root_norm]
    for t in sorted(targets):
        if t != root_norm:
            result.append(t)
    return result

def _get_project_secrets(project_name: str) -> Dict[str, str]:
    """Retrieve secrets for a project from the AGIX vault.

    Reads directly from the project-scoped secrets DB, bypassing
    load_secrets()'s SYSTEM_API_KEY_NAMES filter. The env bridge writes
    to .env.local where API keys legitimately belong — the filter exists
    to keep them out of the Secrets UI, not out of .env.local.

    Prior fix attempts (RCA-243, RCA-265, RCA-345, RCA-232) all proposed
    new filter bypasses but were never wired. Per ADR-83: we read from
    the existing config_db.get_secrets() directly instead.

    Args:
        project_name: The project name to look up secrets for.

    Returns:
        Dict of secret_name → secret_value. Empty dict if no secrets.
    """
    try:
        # Read project-scoped secrets directly — no is_system_api_key filter.
        # The prompt_secret_extractor stores keys like OPENROUTER_API_KEY in
        # the project scope intentionally. load_secrets() filters them out
        # because they match SYSTEM_API_KEY_NAMES, but the env bridge needs
        # them in .env.local for the code agent to use.
        project_secrets = config_db.get_secrets(project_name) or {}

        # Also inherit from global scope (lower priority) — but only
        # non-system keys from global, since global system keys are the
        # infrastructure's own keys, not project-specific.
        global_secrets = config_db.get_secrets("global") or {}
        for key, value in global_secrets.items():
            if key not in project_secrets and not is_system_api_key(key):
                project_secrets[key] = value

        if project_secrets:
            logger.info(
                f"[ENV BRIDGE] Retrieved {len(project_secrets)} secrets for "
                f"project '{project_name}': {sorted(project_secrets.keys())}"
            )
        return project_secrets
    except Exception as e:
        logger.warning(f"[ENV BRIDGE] Could not retrieve secrets for {project_name}: {e}")
        return {}

# ── System 5 (ADR-82): Manifest secrets via shared parse_manifest() ──
# Previously had independent _MANIFEST_SEARCH_PATHS + _MANIFEST_SECRET_SECTIONS
# + 70-line _get_manifest_secrets() with its own json.load. Now delegates to
# the single parser which handles path discovery, JSON parsing, secret section
# merging, integration API key extraction, and value sanitization.

# Backward-compat: document which manifest sections are treated as secrets.
# parse_manifest() internalises this logic, but tests verify these keys exist.
_MANIFEST_SECRET_SECTIONS: frozenset = frozenset({
    "api_keys",
    "secrets",
    "secrets_provided",
})


def _get_manifest_secrets(project_dir: str) -> Dict[str, str]:
    """Extract API-key-like secrets from content_manifest.json.

    System 5 (ADR-82): Delegates to parse_manifest() which handles:
    1. Path discovery (3-location search)
    2. JSON parsing with error handling
    3. Secret section merging (api_keys + secrets + secrets_provided)
    4. Integration API key extraction (name → {NAME}_API_KEY)
    5. Value sanitization (strip embedded \\n, \\r, \\t)

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        Dict of {ENV_VAR_NAME: value}. Empty dict if no manifest or no secrets.
    """
    from python.helpers.manifest_parser import parse_manifest

    manifest = parse_manifest(project_dir)
    result = manifest.secrets

    if result:
        logger.info(
            f"[ENV BRIDGE] Extracted {len(result)} secrets from content_manifest.json: "
            f"{sorted(result.keys())}"
        )

    return result



@dataclass
class EnvBridgeResult:
    """Structured result from ensure_env_before_delegation.

    Attributes:
        provisioned: True if .env.local was created/updated with vault secrets.
        written_keys: Env var names that were written from the vault.
        missing_keys: Env var names that the prompt needs but the vault
                      doesn't have (populated from prompt_env_keys).
        placeholder_keys: Env var names whose values look like placeholders
                          (F-7, RCA-400). These keys ARE in the vault but
                          their values are dummy/test strings.
        invalid_keys: Env var names that failed API health check (U-1, ITR-29).
                      These keys ARE in .env.local but their values were
                      rejected by the service (401/403).
    """

    provisioned: bool = False
    written_keys: List[str] = field(default_factory=list)
    missing_keys: List[str] = field(default_factory=list)
    placeholder_keys: List[str] = field(default_factory=list)
    invalid_keys: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Backward compat: truthy when provisioned=True."""
        return self.provisioned


def build_env_var_section(bridged: EnvBridgeResult) -> str:
    """Build a markdown section listing available environment variables.

    SS-5 (ITR-23): The env bridge writes secrets to .env.local but never
    tells the subordinate agent WHICH keys are available. This function
    generates a structured section for injection into the delegation message
    so the subordinate knows exactly which env vars it can use.

    FIX-4 (RCA-F4): Also generates a section when ONLY missing_keys are
    present (no written_keys). Previously the early return on empty
    written_keys silently dropped missing_keys guidance, leaving the
    subordinate to guess which env vars exist.

    Args:
        bridged: The EnvBridgeResult from ensure_env_before_delegation().

    Returns:
        A markdown string with the env var section, or empty string if
        no keys of any kind are present.
    """
    if bridged is None:
        return ""

    # FIX-4: Check ALL key lists, not just written_keys
    has_any_keys = (
        bridged.written_keys
        or bridged.missing_keys
        or bridged.placeholder_keys
        or bridged.invalid_keys
    )
    if not has_any_keys:
        return ""

    lines = []

    # Section: provisioned keys
    if bridged.written_keys:
        lines.append("### ENV VARS PROVISIONED")
        lines.append("The following env vars are available in `.env.local`:")
        for key in bridged.written_keys:
            lines.append(f"- `{key}` ✅ READY")

    # Section: missing keys — guide subordinate to write real code anyway
    if bridged.missing_keys:
        lines.append("")
        lines.append("### ENV VARS NOT YET PROVISIONED")
        lines.append(
            "The following env vars are NOT in `.env.local` yet — "
            "write real SDK code using `process.env.VAR_NAME` anyway:"
        )
        for key in bridged.missing_keys:
            lines.append(f"- `{key}` — write production code, NOT mocks")

    if bridged.placeholder_keys:
        lines.append("")
        lines.append(
            "⚠️ **Placeholder values detected** — the following keys have "
            "dummy/test values that will likely cause API errors at runtime:"
        )
        for key in bridged.placeholder_keys:
            lines.append(f"- `{key}`")

    # U-1 (ITR-29): Warn about keys that failed health check
    if bridged.invalid_keys:
        lines.append("")
        lines.append(
            "🔴 **INVALID API keys** — the following keys failed health check "
            "(401/403 from the service). Do NOT write integration code that "
            "depends on these services — use mock/stub patterns instead:"
        )
        for key in bridged.invalid_keys:
            lines.append(f"- `{key}` — **INVALID** (service rejected this key)")

    return "\n".join(lines)



def ensure_env_before_delegation(
    project_dir: Optional[str],
    project_name: str,
    prompt_env_keys: Optional[List[str]] = None,
) -> EnvBridgeResult:
    """Create/update .env.local from vault secrets before delegation.

    Merges vault secrets into the existing .env.local (if any), preserving
    user-set values. New secrets are appended; existing keys are updated
    only if they came from the vault (not manually set).

    Args:
        project_dir: Absolute path to the project directory.
        project_name: Project name for secret lookup.
        prompt_env_keys: Optional list of env var names the prompt expects.
            Keys not found in the vault will appear in missing_keys so the
            orchestrator can warn about them.

    Returns:
        EnvBridgeResult — truthy when provisioned, with written_keys and
        missing_keys for structured introspection.
    """
    if not project_dir or not os.path.isdir(project_dir):
        # Compute missing even when we can't provision
        _missing = list(prompt_env_keys) if prompt_env_keys else []
        return EnvBridgeResult(provisioned=False, written_keys=[], missing_keys=_missing)

    # Retrieve secrets from vault
    secrets = _get_project_secrets(project_name)

    # ── G-2 (ITR-24): Merge from secrets.env if present ──
    # secrets.env is a user-maintained file with real API keys that
    # supplements the vault. Vault values take precedence.
    secrets_env_path = os.path.join(project_dir, "secrets.env")
    secrets_env_vars: Dict[str, str] = {}
    if os.path.isfile(secrets_env_path):
        try:
            with open(secrets_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip()
                        if key and value:
                            secrets_env_vars[key] = value
            logger.info(
                f"[ENV BRIDGE] Read {len(secrets_env_vars)} vars from secrets.env"
            )
        except Exception as e:
            logger.warning(f"[ENV BRIDGE] Could not read secrets.env: {e}")

    # ── Fix 1: Merge from content_manifest.json (lowest priority) ──
    # The manifest may contain API keys provided as plaintext in the user
    # prompt (e.g. Resend key). These are extracted from api_keys, secrets,
    # and integrations sections. Priority: vault > secrets.env > manifest.
    manifest_secrets: Dict[str, str] = _get_manifest_secrets(project_dir) if project_dir else {}

    # Compute missing keys: prompt expects them but no source has them
    _missing: List[str] = []
    if prompt_env_keys:
        all_key_set = set(secrets.keys()) | set(secrets_env_vars.keys()) | set(manifest_secrets.keys())
        _missing = [k for k in prompt_env_keys if k not in all_key_set]

    if not secrets and not secrets_env_vars and not manifest_secrets:
        logger.debug(f"No secrets found for project '{project_name}' — skipping env bridge")
        return EnvBridgeResult(provisioned=False, written_keys=[], missing_keys=_missing)

    env_path = os.path.join(project_dir, ".env.local")

    # Read existing .env.local if present
    existing_vars: Dict[str, str] = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        existing_vars[key.strip()] = value.strip()
        except Exception as e:
            logger.warning(f"Could not read existing .env.local: {e}")

    # Merge: manifest first (lowest), secrets.env (medium), vault (highest).
    # Existing user values are preserved for keys not in any source.
    merged = dict(existing_vars)
    merged.update(manifest_secrets)  # Lowest priority
    merged.update(secrets_env_vars)  # Medium priority
    for key, value in secrets.items():
        merged[key] = value  # Highest priority

    # ── F-7 (RCA-400): Detect placeholder values ──
    # Check all sources: manifest, secrets.env, and vault
    all_new_secrets = dict(manifest_secrets)
    all_new_secrets.update(secrets_env_vars)
    all_new_secrets.update(secrets)
    _placeholders = detect_placeholder_values(all_new_secrets)
    if _placeholders:
        logger.warning(
            f"[ENV BRIDGE] Placeholder values detected for project '{project_name}': "
            f"{', '.join(_placeholders)}. These values are likely not real API keys."
        )

    # Write merged .env.local
    try:
        with open(env_path, "w") as f:
            f.write("# Auto-generated by AGIX pre-delegation env bridge\n")
            f.write("# Vault secrets merged before subordinate delegation\n\n")
            placeholder_set = set(_placeholders)
            for key, value in sorted(merged.items()):
                if key in placeholder_set:
                    f.write(f"# WARNING: Placeholder value\n")
                    f.write(f"{key}=\n")
                else:
                    f.write(f"{key}={value}\n")
        written = sorted(set(
            list(secrets.keys()) + list(secrets_env_vars.keys()) + list(manifest_secrets.keys())
        ))

        # F-6 (RCA-470): Copy .env.local to all framework subdirectories.
        # Next.js reads .env from the directory containing next.config.mjs,
        # not from the project root. Same for Vite, Nuxt, Docker, etc.
        import shutil
        env_targets = discover_env_targets(project_dir)
        subdir_copies = 0
        for target_dir in env_targets:
            target_norm = os.path.normpath(target_dir)
            root_norm = os.path.normpath(project_dir)
            if target_norm != root_norm:
                target_env = os.path.join(target_dir, ".env.local")
                try:
                    shutil.copy2(env_path, target_env)
                    subdir_copies += 1
                    logger.info(
                        f"[ENV BRIDGE] Copied .env.local → {target_env}"
                    )
                except Exception as copy_err:
                    logger.warning(
                        f"[ENV BRIDGE] Failed to copy .env.local to {target_dir}: {copy_err}"
                    )

        logger.info(
            f"[ENV BRIDGE] Created/updated .env.local with "
            f"{len(secrets)} vault + {len(secrets_env_vars)} secrets.env "
            f"+ {len(manifest_secrets)} manifest vars "
            f"({len(merged)} total vars) for project '{project_name}'"
            f"{f' + {subdir_copies} subdir copies' if subdir_copies else ''}"
        )
        return EnvBridgeResult(
            provisioned=True,
            written_keys=written,
            missing_keys=_missing,
            placeholder_keys=_placeholders,
        )
    except Exception as e:
        logger.warning(f"Failed to write .env.local: {e}")
        return EnvBridgeResult(provisioned=False, written_keys=[], missing_keys=_missing)



# ── U-1 (ITR-29): API Key Health Check ──────────────────────────────────
# Lightweight HTTP calls to known service endpoints to validate keys
# BEFORE wasting agent iterations on invalid credentials.
#
# Why: In MSR_Smoke_1780675145, the Perplexity API key was expired but
# the framework wasted 8+ iterations trying code-level fixes before the
# auth_error_detector escape hatch finally fired. A 2-second health check
# would have caught it before any code delegation.

_SERVICE_HEALTH_CHECKS: Dict[str, Dict[str, Any]] = {
    "OPENROUTER_API_KEY": {
        "url": "https://openrouter.ai/api/v1/auth/key",
        "method": "GET",
        "headers_template": {"Authorization": "Bearer {key}"},
        "invalid_status": {401, 403},
        "timeout": 5,
    },
    "RESEND_API_KEY": {
        "url": "https://api.resend.com/api-keys",
        "method": "GET",
        "headers_template": {"Authorization": "Bearer {key}"},
        "invalid_status": {401, 403},
        "timeout": 5,
    },
    "PERPLEXITY_API_KEY": {
        "url": "https://api.perplexity.ai/chat/completions",
        "method": "POST",
        "headers_template": {"Authorization": "Bearer {key}"},
        "body": {
            "model": "llama-3.1-sonar-small-128k-online",
            "messages": [{"role": "user", "content": "ping"}],
        },
        "invalid_status": {401, 403},
        "timeout": 10,
    },
}


def _check_key_health(key_name: str, key_value: str) -> Dict[str, Any]:
    """Check if an API key is valid by making a lightweight HTTP request.

    Args:
        key_name: The env var name (e.g., 'OPENROUTER_API_KEY').
        key_value: The actual API key value.

    Returns:
        {
            "valid": bool | None — True=valid, False=invalid, None=unknown,
            "status": int — HTTP status code (0 if connection failed),
            "error": str — Error message if applicable.
        }
    """
    if requests is None:
        return {"valid": None, "error": "requests library not available"}

    config = _SERVICE_HEALTH_CHECKS.get(key_name)
    if not config:
        return {"valid": None, "error": f"No health check configured for {key_name}"}

    # Build headers with actual key
    headers = {}
    for h_key, h_template in config.get("headers_template", {}).items():
        headers[h_key] = h_template.format(key=key_value)

    timeout = config.get("timeout", 5)
    invalid_status = config.get("invalid_status", {401, 403})

    try:
        method = config.get("method", "GET").upper()
        if method == "POST":
            response = requests.post(
                config["url"],
                headers=headers,
                json=config.get("body"),
                timeout=timeout,
            )
        else:
            response = requests.get(
                config["url"],
                headers=headers,
                timeout=timeout,
            )

        status = response.status_code

        if status in invalid_status:
            return {"valid": False, "status": status, "error": f"Service rejected key ({status})"}

        # 200, 201, 204, etc. — key is valid
        if 200 <= status < 300:
            return {"valid": True, "status": status}

        # 429 (rate limit), 500+ (server error) — key might be valid but service is down
        return {"valid": None, "status": status, "error": f"Service returned {status} (indeterminate)"}

    except requests.exceptions.Timeout:
        return {"valid": None, "status": 0, "error": "Connection timeout"}
    except requests.exceptions.ConnectionError:
        return {"valid": None, "status": 0, "error": "Connection failed"}
    except Exception as e:
        return {"valid": None, "status": 0, "error": str(e)}


def validate_api_keys(env_vars: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Validate known service API keys with lightweight HTTP health checks.

    Only checks keys that have a configured health check endpoint in
    _SERVICE_HEALTH_CHECKS. Unknown keys are silently skipped.
    Placeholder values (from detect_placeholder_values) are also skipped.

    Args:
        env_vars: Dict of env_var_name → env_var_value.

    Returns:
        Dict of {key_name: {valid: bool|None, status: int, error: str}}
        Only includes keys that were actually checked.
    """
    if not env_vars:
        return {}

    results: Dict[str, Dict[str, Any]] = {}

    # Skip placeholder values — they'd waste HTTP calls
    placeholders = set(detect_placeholder_values(env_vars))

    for key_name, key_value in env_vars.items():
        # Only check keys we have health check configs for
        if key_name not in _SERVICE_HEALTH_CHECKS:
            continue

        # Skip placeholder values
        if key_name in placeholders:
            continue

        logger.info(f"[ENV BRIDGE] Health-checking {key_name}...")
        result = _check_key_health(key_name, key_value)

        if result.get("valid") is False:
            logger.warning(
                f"[ENV BRIDGE] ⚠️ {key_name} is INVALID "
                f"(status={result.get('status')}: {result.get('error')})"
            )
        elif result.get("valid") is True:
            logger.info(f"[ENV BRIDGE] ✅ {key_name} is valid")
        else:
            logger.info(
                f"[ENV BRIDGE] ❓ {key_name} health indeterminate: "
                f"{result.get('error')}"
            )

        results[key_name] = result

    return results
