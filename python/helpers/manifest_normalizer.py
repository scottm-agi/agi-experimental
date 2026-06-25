"""Schema-Driven Manifest Normalizer — F-3 implementation.

Replaces hardcoded alias dicts (_STRIPE_ALIASES, _CALENDLY_ALIASES) with a
2-layer normalization system:

  L1: Exact match + fuzzy string matching (SequenceMatcher ratio >= threshold)
  L2: Unknown keys are PRESERVED (never silently dropped) + logged

The old hardcoded dicts are kept as OPTIONAL "common hints" for performance,
but the system ALWAYS falls through to dynamic resolution for unknown keys.

Key principle: Unknown keys must NEVER be silently dropped.
"""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.manifest_normalizer")

# ── Common Hints (performance optimization, NOT primary mechanism) ──
# These provide instant resolution for known-common aliases.
# Unknown keys that don't match hints fall through to fuzzy matching.
_COMMON_HINTS: Dict[str, str] = {
    "stripe_monthly_link": "stripe_monthly_url",
    "stripe_monthly": "stripe_monthly_url",
    "stripe_prepaid_link": "stripe_prepaid_url",
    "stripe_prepaid": "stripe_prepaid_url",
    "stripe_annual_link": "stripe_annual_url",
    "stripe_annual": "stripe_annual_url",
    "booking_link": "calendly_url",
    "booking_url": "calendly_url",
    "cal_link": "calendly_url",
    "cal_url": "calendly_url",
    "scheduling_url": "calendly_url",
    "scheduling": "calendly_url",
    "links": "urls",
    "founder_name": "founder_name",
    "founder_email": "founder_email",
}


def fuzzy_match_key(
    key: str,
    canonical_keys: List[str],
    threshold: float = 0.75,
) -> Optional[str]:
    """Match an unknown key to the best canonical key using fuzzy string matching.

    Uses difflib.SequenceMatcher for Ratcliff/Obershelp similarity scoring.

    Args:
        key: The unknown key to match.
        canonical_keys: List of valid canonical keys to match against.
        threshold: Minimum similarity ratio to consider a match (0.0-1.0).

    Returns:
        The best matching canonical key if above threshold, or None.
    """
    if not canonical_keys:
        return None

    # L0: Exact match (fastest path)
    if key in canonical_keys:
        return key

    # L1: Check common hints first (performance optimization)
    hint = _COMMON_HINTS.get(key)
    if hint and hint in canonical_keys:
        return hint

    # L2: Fuzzy matching via SequenceMatcher
    best_match: Optional[str] = None
    best_ratio: float = 0.0

    key_lower = key.lower()
    for canonical in canonical_keys:
        ratio = SequenceMatcher(None, key_lower, canonical.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = canonical

    if best_ratio >= threshold:
        return best_match

    return None


def normalize_manifest_keys(
    manifest: Dict[str, Any],
    canonical_schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize manifest keys to match canonical schema using fuzzy matching.

    Recursively walks the manifest dict. For each key:
      1. Exact match → pass through
      2. Fuzzy match → remap key and log the remapping
      3. No match → PRESERVE the key as-is (never drop)

    Args:
        manifest: The input manifest dict to normalize.
        canonical_schema: Dict defining canonical key structure.
            Values can be None (leaf) or dict (nested section).

    Returns:
        New dict with keys normalized to match canonical schema.
        Unknown keys are preserved, not dropped.
    """
    if not isinstance(manifest, dict):
        return manifest

    if not isinstance(canonical_schema, dict):
        return manifest

    canonical_keys = list(canonical_schema.keys())
    result: Dict[str, Any] = {}

    for key, value in manifest.items():
        # Try to match this key to a canonical key
        matched_key = fuzzy_match_key(key, canonical_keys)

        if matched_key and matched_key != key:
            # Fuzzy match found — remap the key
            logger.info(
                f"[MANIFEST NORMALIZER] Remapped key '{key}' → '{matched_key}' "
                f"(fuzzy match)"
            )
            target_key = matched_key
        elif matched_key:
            # Exact match — use as-is
            target_key = key
        else:
            # No match — PRESERVE the key (never drop)
            logger.debug(
                f"[MANIFEST NORMALIZER] Unknown key '{key}' preserved as-is "
                f"(no fuzzy match found)"
            )
            target_key = key

        # Recursively normalize nested dicts
        schema_value = canonical_schema.get(target_key)
        if isinstance(value, dict) and isinstance(schema_value, dict):
            result[target_key] = normalize_manifest_keys(value, schema_value)
        else:
            result[target_key] = value

    return result
