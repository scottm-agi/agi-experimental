"""
Prompt-Text Secret Extractor — F-6 Fix

Parses user prompt text for API key patterns and stores them in the vault.
This ensures secrets provided inline in user messages are captured BEFORE
delegation, so the env bridge can materialize them into .env.local files.

Design:
  - Pattern 1: ENV_VAR_NAME=secret_value (explicit key=value pairs)
  - Pattern 2: Standalone API key prefixes (e.g., sk-or-' + 'v1-xxx without KEY=)
  - Known prefix → env var name mapping for auto-labeling standalone keys
  - De-duplication: explicit KEY=value takes precedence over standalone matches
  - Minimum value length (8 chars) to avoid false positives on short tokens

Usage:
    from python.helpers.prompt_secret_extractor import extract_secrets_from_text, store_extracted_secrets
    secrets = extract_secrets_from_text(user_prompt)
    count = store_extracted_secrets("my-project", secrets)
"""
from __future__ import annotations

import logging
import re
from typing import List, Dict

logger = logging.getLogger("agix.prompt_secret_extractor")

# Minimum length for a secret value to be considered valid.
# Prevents false positives on short tokens like "re_do" or "sk-ip".
_MIN_VALUE_LENGTH = 8

# ── Known API Key Prefixes → Default Env Var Name ────────────────────────
# When a standalone key is found (no ENV_VAR= prefix), we label it with
# the most likely env var name based on the prefix.
_PREFIX_TO_ENV_VAR: List[tuple[str, str]] = [
    # Order matters — longer prefixes MUST come first to avoid partial matches
    ("sk-or-' + 'v1-", "OPENROUTER_API_KEY"),
    ("sk-or-",    "OPENROUTER_API_KEY"),
    ("pk_live_",  "STRIPE_PUBLISHABLE_KEY"),
    ("pk_test_",  "STRIPE_PUBLISHABLE_KEY"),
    ("sk_" + "live_",  "STRIPE_SECRET_KEY"),
    ("sk_" + "test_",  "STRIPE_SECRET_KEY"),
    ("whsec_",    "STRIPE_WEBHOOK_SECRET"),
    ("re_",       "RESEND_API_KEY"),
    ("AIza",      "GOOGLE_API_KEY"),
    ("pplx-",     "PERPLEXITY_API_KEY"),
]

# ── Regex: Pattern 1 — ENV_VAR=value ────────────────────────────────────
# Matches: OPENROUTER_API_KEY=sk-or-' + 'v1-xxx or KEY="value" or KEY='value'
_ENV_VAR_PATTERN = re.compile(
    r"""
    \b                               # word boundary
    ([A-Z][A-Z0-9_]{2,})             # Key: uppercase env var name (3+ chars)
    =                                # literal equals
    ["']?                            # optional opening quote
    (                                # Value group:
      (?:sk-or-' + 'v1-|sk-or-|sk_" + "live_|sk_" + "test_|pk_live_|pk_test_|
         whsec_|re_|AIza|pplx-)      # must start with a known prefix
      [A-Za-z0-9_\-./+]+            # rest of the value
    )
    ["']?                            # optional closing quote
    \b                               # word boundary (approx: value ends at whitespace/punct)
    """,
    re.VERBOSE,
)

# ── Regex: Pattern 2 — Standalone API key (no KEY= prefix) ──────────────
# Matches known key prefixes followed by enough characters to be a real key.
_STANDALONE_PATTERNS: List[tuple[str, re.Pattern]] = [
    ("OPENROUTER_API_KEY",      re.compile(r"\bsk-or-' + 'v1-[A-Za-z0-9_\-]{4,}\b")),
    ("OPENROUTER_API_KEY",      re.compile(r"\bsk-or-[A-Za-z0-9_\-]{6,}\b")),
    ("STRIPE_PUBLISHABLE_KEY",  re.compile(r"\bpk_live_[A-Za-z0-9_]{6,}\b")),
    ("STRIPE_PUBLISHABLE_KEY",  re.compile(r"\bpk_test_[A-Za-z0-9_]{6,}\b")),
    ("STRIPE_SECRET_KEY",       re.compile(r"\bsk_" + "live_[A-Za-z0-9_]{6,}\b")),
    ("STRIPE_SECRET_KEY",       re.compile(r"\bsk_" + "test_[A-Za-z0-9_]{6,}\b")),
    ("STRIPE_WEBHOOK_SECRET",   re.compile(r"\bwhsec_[A-Za-z0-9_]{6,}\b")),
    ("RESEND_API_KEY",          re.compile(r"\bre_[A-Za-z0-9_]{6,}\b")),
    ("GOOGLE_API_KEY",          re.compile(r"\bAIza[A-Za-z0-9_\-]{10,}\b")),
    ("PERPLEXITY_API_KEY",      re.compile(r"\bpplx-[A-Za-z0-9_\-]{6,}\b")),
]

# ── Patterns that indicate a value is a placeholder, NOT a real secret ───
_PLACEHOLDER_INDICATORS = [
    "YOUR_", "REPLACE", "PLACEHOLDER", "ACTION_REQUIRED",
    "CHANGE_ME", "INSERT_", "TODO", "EXAMPLE", "_HERE",
]


def _is_placeholder(value: str) -> bool:
    """Check if a value looks like a placeholder rather than a real secret."""
    upper = value.upper()
    return any(p in upper for p in _PLACEHOLDER_INDICATORS)


def _is_env_reference(text: str, match_start: int) -> bool:
    """Check if a match is inside an env-var reference (not an assignment).
    
    E.g., 'process.env.OPENROUTER_API_KEY' or 'os.environ.get("KEY")'
    should NOT be treated as a secret assignment.
    """
    # Look at the 30 chars before the match for context
    prefix = text[max(0, match_start - 30):match_start]
    env_refs = ["process.env.", "os.environ", "os.getenv", "getenv("]
    return any(ref in prefix for ref in env_refs)


def extract_secrets_from_text(text: str) -> List[Dict[str, str]]:
    """Extract API keys and secrets from user prompt text.
    
    Scans for two patterns:
      1. ENV_VAR_NAME=secret_value (explicit key=value pairs)
      2. Standalone API key prefixes (auto-labeled with likely env var name)
    
    Args:
        text: The user prompt text to scan.
        
    Returns:
        List of dicts with 'key_name' and 'key_value' keys.
        De-duplicated: each key_name appears at most once.
    """
    if not text or not text.strip():
        return []

    found: Dict[str, str] = {}  # key_name → key_value (dedup by key_name)
    matched_values: set = set()  # track matched values to avoid double-matching

    # ── Pattern 1: Explicit ENV_VAR=value ──
    for match in _ENV_VAR_PATTERN.finditer(text):
        key_name = match.group(1)
        key_value = match.group(2)

        # Skip env var references (not assignments)
        if _is_env_reference(text, match.start()):
            continue

        # Skip placeholders
        if _is_placeholder(key_value):
            continue

        # Skip too-short values
        if len(key_value) < _MIN_VALUE_LENGTH:
            continue

        found[key_name] = key_value
        matched_values.add(key_value)

    # ── Pattern 2: Standalone API keys (only if not already matched by Pattern 1) ──
    for env_var_name, pattern in _STANDALONE_PATTERNS:
        for match in pattern.finditer(text):
            key_value = match.group(0)

            # Skip if already captured by Pattern 1
            if key_value in matched_values:
                continue

            # Skip env var references
            if _is_env_reference(text, match.start()):
                continue

            # Skip placeholders
            if _is_placeholder(key_value):
                continue

            # Skip too-short values
            if len(key_value) < _MIN_VALUE_LENGTH:
                continue

            # Only add if we haven't already found this env var name
            if env_var_name not in found:
                found[env_var_name] = key_value
                matched_values.add(key_value)

    return [{"key_name": k, "key_value": v} for k, v in found.items()]


def store_extracted_secrets(project_name: str, secrets: List[Dict[str, str]]) -> int:
    """Store extracted secrets in the project vault.
    
    Args:
        project_name: The project name for scoped secret storage.
        secrets: List of {key_name, key_value} dicts from extract_secrets_from_text().
        
    Returns:
        Number of secrets successfully stored. Returns 0 on error.
    """
    if not secrets:
        return 0

    try:
        from python.helpers.secrets_helper import get_project_secrets_manager
        manager = get_project_secrets_manager(project_name)
    except Exception as e:
        logger.warning(f"Cannot access vault for project '{project_name}': {e}")
        return 0

    stored = 0
    for secret in secrets:
        key_name = secret.get("key_name", "")
        key_value = secret.get("key_value", "")
        if not key_name or not key_value:
            continue

        try:
            manager.set_secret(key_name, key_value)
            stored += 1
            logger.info(f"[PROMPT EXTRACTOR] Stored {key_name} for project '{project_name}'")
        except Exception as e:
            logger.warning(f"[PROMPT EXTRACTOR] Failed to store {key_name}: {e}")

    return stored
