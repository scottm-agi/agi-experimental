"""
Centralized Secret Redactor (RCA-358 / F-21)

Replaces known secret VALUES in text with §§secret(KEY_NAME) placeholders,
and catches common API key patterns even without vault matches.

Usage:
    from python.helpers.secret_redactor import redact_known_secrets
    
    redacted = redact_known_secrets(text, secrets={"STRIPE_KEY": "sk_" + "live_..."})
    # → "key=§§secret(STRIPE_KEY)"
"""

from __future__ import annotations

import re
import logging
from typing import Dict, Optional

logger = logging.getLogger("agix.secret_redactor")

# Minimum secret value length to avoid false positives with common substrings
_MIN_SECRET_LENGTH = 4

# ─── Pattern-based redaction (Layer 2) ────────────────────────────────────────
# These catch common API key formats even when not in the vault.
# Ordered by specificity (most specific first).

_PATTERN_REDACTIONS = [
    # Stripe live/test keys: sk_" + "live_xxx, sk_" + "test_xxx
    re.compile(r'\bsk_" + "live_[A-Za-z0-9]{10,}\b'),
    re.compile(r'\bsk_" + "test_[A-Za-z0-9]{10,}\b'),
    # OpenRouter: sk-or-' + 'v1-xxx or sk-or-xxx
    re.compile(r'\bsk-or-(?:v1-)?[A-Za-z0-9]{10,}[A-Za-z0-9\-]*\b'),
    # OpenAI-style: sk-proj-xxx or sk-xxx (but not sk-or which is OpenRouter)
    re.compile(r'\bsk-(?!or-)(?:proj-)?[A-Za-z0-9]{10,}\b'),
    # GitHub tokens
    re.compile(r'\bgh" + "p_[A-Za-z0-9_]{10,}\b'),
    re.compile(r'\bgh" + "o_[A-Za-z0-9_]{10,}\b'),
    re.compile(r'\bghs_[A-Za-z0-9_]{10,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{10,}\b'),
    # Google API keys
    re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'),
    # Perplexity
    re.compile(r'\bpplx-[A-Za-z0-9]{10,}\b'),
    # Resend
    re.compile(r'\bre_[A-Za-z0-9]{10,}\b'),
    # Bearer JWT tokens: Bearer eyJ...
    re.compile(r'(?<=Bearer\s)eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+'),
]


def redact_known_secrets(
    text: Optional[str],
    *,
    secrets: Optional[Dict[str, str]] = None,
    project_name: str = "",
) -> str:
    """
    Redact known secret values and common API key patterns from text.

    Two-layer approach:
      Layer 1 (Vault): Replace exact vault secret values with §§secret(KEY_NAME)
      Layer 2 (Pattern): Replace common API key patterns with §§REDACTED_PATTERN

    Args:
        text: The text to redact. None is treated as empty string.
        secrets: Dict of {KEY_NAME: secret_value} from vault. If None,
                 attempts to load from config_db (global + project scope).
        project_name: Optional project name for scoped secret lookup.

    Returns:
        Text with secrets replaced by safe placeholders.
    """
    if text is None:
        return ""
    if not text:
        return text

    # Load secrets from vault if not provided
    if secrets is None:
        secrets = _load_vault_secrets(project_name)

    result = text

    # ── Layer 1: Vault-based redaction (highest priority) ──────────────────
    # Sort by value length descending so longer secrets are matched first
    # (prevents partial matches when one secret is a substring of another)
    sorted_secrets = sorted(
        secrets.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )

    for key_name, secret_value in sorted_secrets:
        if not secret_value or len(secret_value) < _MIN_SECRET_LENGTH:
            continue
        # Skip if the value is itself a redacted placeholder
        if secret_value.startswith("§§secret("):
            continue
        result = result.replace(secret_value, f"§§secret({key_name})")

    # ── Layer 2: Pattern-based redaction ───────────────────────────────────
    # Only redact patterns that weren't already caught by vault redaction
    for pattern in _PATTERN_REDACTIONS:
        result = pattern.sub("§§REDACTED_PATTERN", result)

    return result


def _load_vault_secrets(project_name: str = "") -> Dict[str, str]:
    """
    Load secrets from the config database vault.

    Merges global and project-scoped secrets.
    Returns empty dict on any failure (graceful degradation).
    """
    try:
        from python.helpers.config_db import get_secrets

        all_secrets = {}
        # Global secrets
        try:
            all_secrets.update(get_secrets("global"))
        except Exception:
            pass

        # Project-scoped secrets (override global)
        if project_name:
            try:
                all_secrets.update(get_secrets(project_name))
            except Exception:
                pass

        return all_secrets
    except ImportError:
        logger.debug("config_db not available; vault redaction disabled")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load vault secrets for redaction: {e}")
        return {}
