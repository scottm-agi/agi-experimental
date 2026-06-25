"""Requirements Ledger Sanitizer — Strip Secrets Before Persistence.

RCA-ITR5 ISSUE-4: When the orchestrator extracts requirements from user prompts,
the LLM may inadvertently include raw API keys, secret tokens, or §§secret()
redaction markers in the requirement text. This module provides a universal
sanitization layer (Layer 2 — deterministic code) that runs BEFORE the
requirements_ledger.json is written to disk.

Two-pass sanitization:
  1. Strip §§secret(KEY_NAME) and §§REDACTED_PATTERN tokens
  2. Strip known API key patterns (sk-*, re_*, AIza*, etc.)
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

# Regex patterns for known API key formats (from secret_redactor.py)
_API_KEY_PATTERNS = [
    # OpenAI (includes sk-proj-*, sk-svcacct-*, and legacy sk-* formats)
    re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),
    # Stripe
    re.compile(r'\bsk_(?:test|live)_[A-Za-z0-9]{10,}'),
    re.compile(r'\bpk_(?:test|live)_[A-Za-z0-9]{10,}'),
    re.compile(r'\bwhsec_[A-Za-z0-9]{10,}'),
    # Resend
    re.compile(r'\bre_[A-Za-z0-9]{10,}'),
    # Google
    re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'),
    # GitHub
    re.compile(r'\bgh" + "p_[A-Za-z0-9_]{10,}'),
    re.compile(r'\bgh" + "o_[A-Za-z0-9_]{10,}'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{10,}'),
    # Perplexity
    re.compile(r'\bpplx-[A-Za-z0-9]{10,}'),
    # OpenRouter
    re.compile(r'\bsk-or-v[0-9]-[A-Za-z0-9]{10,}'),
    # Generic long hex/base64 tokens (>32 chars, likely keys)
    re.compile(r'(?<=[=: ])[A-Za-z0-9+/]{40,}={0,2}(?=\s|$)'),
]

# §§secret(KEY_NAME) or §§REDACTED_PATTERN markers
_REDACTION_TOKEN_PATTERN = re.compile(r'§§(?:secret\([^)]*\)|REDACTED_PATTERN)')


def sanitize_ledger_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize a single requirements ledger entry.

    Strips secret tokens and API key patterns from the 'text' field.
    Returns a new dict (does not mutate the input).

    Args:
        entry: A requirements ledger entry dict with 'text' field.

    Returns:
        Sanitized copy of the entry.
    """
    result = copy.copy(entry)
    text = result.get("text")

    if text is None or text == "":
        return result

    text = str(text)

    # Pass 1: Strip §§secret() and §§REDACTED_PATTERN tokens
    text = _REDACTION_TOKEN_PATTERN.sub("", text)

    # Pass 2: Strip known API key patterns
    for pattern in _API_KEY_PATTERNS:
        text = pattern.sub("[REDACTED]", text)

    # Clean up leftover whitespace/punctuation from removal
    text = re.sub(r'\s*:\s*$', '', text)  # Trailing colon after removed value
    text = re.sub(r'\s{2,}', ' ', text)   # Collapse multiple spaces
    text = text.strip()

    result["text"] = text
    return result


def sanitize_ledger(ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize all entries in a requirements ledger.

    Args:
        ledger: List of requirement dicts.

    Returns:
        New list with sanitized entries.
    """
    return [sanitize_ledger_entry(entry) for entry in ledger]


# ─── ITR-20 F-11: Manifest secret sanitization ──────────────────────────
#
# When the LLM extracts a content_manifest.json from the user prompt,
# it may include raw API keys as string values. This function deep-walks
# the manifest and replaces any matching values with env-var references
# like {{STRIPE_SECRET_KEY}}.
#
# Pattern → env-var mapping is ordered MOST-SPECIFIC-FIRST so that
# sk_" + "test_ (Stripe) matches before the generic sk- (OpenAI).

# Ordered list of (compiled_regex, env_var_name) — most specific first.
_MANIFEST_SECRET_MAP: List[tuple] = [
    # Stripe (must precede generic sk- to avoid false match)
    (re.compile(r'^sk_(?:test|live)_[A-Za-z0-9]{10,}$'), "{{STRIPE_SECRET_KEY}}"),
    (re.compile(r'^pk_(?:test|live)_[A-Za-z0-9]{10,}$'), "{{STRIPE_PUBLISHABLE_KEY}}"),
    (re.compile(r'^whsec_[A-Za-z0-9]{10,}$'), "{{STRIPE_WEBHOOK_SECRET}}"),
    # OpenRouter (sk-or-v prefix — must precede generic sk-)
    (re.compile(r'^sk-or-v[0-9]-[A-Za-z0-9]{10,}$'), "{{OPENROUTER_API_KEY}}"),
    # OpenAI (generic sk- after Stripe/OpenRouter exclusions)
    (re.compile(r'^sk-[A-Za-z0-9_-]{20,}$'), "{{OPENAI_API_KEY}}"),
    # Resend
    (re.compile(r'^re_[A-Za-z0-9]{10,}$'), "{{RESEND_API_KEY}}"),
    # Google
    (re.compile(r'^AIza[0-9A-Za-z_-]{35}$'), "{{GOOGLE_API_KEY}}"),
    # GitHub
    (re.compile(r'^gh" + "p_[A-Za-z0-9_]{10,}$'), "{{GITHUB_TOKEN}}"),
    (re.compile(r'^gh" + "o_[A-Za-z0-9_]{10,}$'), "{{GITHUB_TOKEN}}"),
    (re.compile(r'^github_pat_[A-Za-z0-9_]{10,}$'), "{{GITHUB_TOKEN}}"),
    # Perplexity
    (re.compile(r'^pplx-[A-Za-z0-9]{10,}$'), "{{PERPLEXITY_API_KEY}}"),
    # Generic long token (fallback — only if no specific pattern matched)
    (re.compile(r'^[A-Za-z0-9+/]{40,}={0,2}$'), "{{API_SECRET}}"),
]


def _sanitize_value(value: str) -> str:
    """Replace a single string value with its env-var ref if it matches a secret pattern."""
    # Skip already-sanitized env-var references
    if value.startswith("{{") and value.endswith("}}"):
        return value
    for pattern, env_var in _MANIFEST_SECRET_MAP:
        if pattern.match(value):
            return env_var
    return value


def _deep_sanitize(obj: Any) -> Any:
    """Recursively walk a JSON-like structure and sanitize string values."""
    if isinstance(obj, dict):
        return {k: _deep_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_sanitize(item) for item in obj]
    if isinstance(obj, str):
        return _sanitize_value(obj)
    return obj


def sanitize_manifest_secrets(manifest: dict) -> dict:
    """Deep-walk a content manifest and replace raw API keys with env-var references.

    ITR-20 F-11: When the orchestrator extracts a content_manifest.json
    from the user prompt, it may include raw API key values. This function
    replaces them with standardized env-var references so secrets are never
    persisted to disk.

    Mapping examples:
        sk-proj-abc123...     → {{OPENAI_API_KEY}}
        sk_" + "test_abc123...     → {{STRIPE_SECRET_KEY}}
        pk_live_abc123...     → {{STRIPE_PUBLISHABLE_KEY}}
        whsec_abc123...       → {{STRIPE_WEBHOOK_SECRET}}
        re_abc123...          → {{RESEND_API_KEY}}
        AIzaSyA...            → {{GOOGLE_API_KEY}}
        gh" + "p_abc123...         → {{GITHUB_TOKEN}}
        pplx-abc123...        → {{PERPLEXITY_API_KEY}}
        sk-or-' + 'v1-abc123...    → {{OPENROUTER_API_KEY}}

    Args:
        manifest: The content manifest dict.

    Returns:
        A new dict with secrets replaced (input is not mutated).
    """
    # Deep copy to avoid mutating input
    sanitized = copy.deepcopy(manifest)
    return _deep_sanitize(sanitized)
