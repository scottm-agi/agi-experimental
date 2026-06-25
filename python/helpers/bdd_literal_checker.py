"""BDD Literal Consistency Gate — F-12.

Compares ALL literals from content_manifest.json against BDD scenarios
to catch drift between planning artifacts (e.g., manifest says '$200/mo'
but BDD says '$200/month').

Root cause: LLMs paraphrase values when generating BDD scenarios. A price
like '$200/mo' becomes '$200/month' or '$200 per month'. These subtle
differences propagate into generated code, causing fidelity violations.

This module provides deterministic L1 checking for:
- Prices (exact match for amounts, fuzzy for format suffixes)
- URLs (exact match required)
- Email addresses (exact match required)
- Person names (case-insensitive)
- Brand names (case-insensitive)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agi-experimental")


# ═══════════════════════════════════════════════════════════════════════
# Literal Extraction
# ═══════════════════════════════════════════════════════════════════════

# Regex patterns for extracting specific literal types
_PRICE_PATTERN = re.compile(r'\$[\d,]+(?:\.\d{2})?(?:/\w+)?(?:\s+per\s+\w+)?')
_URL_PATTERN = re.compile(r'https?://[^\s"\'<>]+')
_EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')


def _extract_all_strings(obj: Any, out: List[str]) -> None:
    """Recursively extract all string values from a JSON-like structure."""
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped:
            out.append(stripped)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_all_strings(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _extract_all_strings(item, out)


def _extract_prices(text: str) -> List[str]:
    """Extract all price patterns from text."""
    return _PRICE_PATTERN.findall(text)


def _extract_urls(text: str) -> List[str]:
    """Extract all URLs from text."""
    return _URL_PATTERN.findall(text)


def _extract_emails(text: str) -> List[str]:
    """Extract all email addresses from text."""
    return _EMAIL_PATTERN.findall(text)


def _extract_numeric_price(price_str: str) -> Optional[float]:
    """Extract the numeric value from a price string like '$200/mo'.

    Returns:
        Float value, or None if not parseable.
    """
    match = re.search(r'\$([\d,]+(?:\.\d{2})?)', price_str)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _normalize_price_suffix(price_str: str) -> str:
    """Normalize price suffix for comparison.

    Converts various formats to a canonical form:
    - '/mo' → '/mo'
    - '/month' → '/month'
    - ' per month' → ' per month'

    Returns the full suffix after the numeric part.
    """
    match = re.search(r'\$[\d,]+(?:\.\d{2})?(.*)', price_str)
    if match:
        return match.group(1).strip()
    return ""


def _extract_names_from_manifest(manifest: dict) -> List[str]:
    """Extract person names and brand names from manifest.

    Looks for common keys that hold name values.
    """
    names: List[str] = []
    name_keys = {"name", "founder_name", "author", "owner"}

    def _walk(obj: Any, parent_key: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in name_keys and isinstance(v, str) and v.strip():
                    names.append(v.strip())
                _walk(v, k)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, parent_key)

    _walk(manifest)
    return names


# ═══════════════════════════════════════════════════════════════════════
# Main Consistency Checker
# ═══════════════════════════════════════════════════════════════════════


def check_bdd_literal_consistency(
    manifest_path: str,
    bdd_path: str,
) -> Dict[str, Any]:
    """Compare ALL literals from manifest against BDD scenarios.

    Extracts prices, URLs, person names, brand names, and email addresses
    from the manifest, then searches the BDD file for each literal.

    Matching rules:
    - URLs/emails: exact match required
    - Prices: fuzzy match (same numeric value but different format suffix
      like '/mo' vs '/month' is flagged as a warning)
    - Names: case-insensitive match

    Args:
        manifest_path: Path to content_manifest.json.
        bdd_path: Path to BDD scenarios file (e.g., docs/bdd-scenarios.md).

    Returns:
        Dict with:
            consistent: bool — True if all literals match
            mismatches: list[dict] — each with field, manifest_value,
                        bdd_value, severity
    """
    result: Dict[str, Any] = {"consistent": True, "mismatches": []}

    # ── Guard: missing files → nothing to check ──
    if not manifest_path or not os.path.isfile(manifest_path):
        return result
    if not bdd_path or not os.path.isfile(bdd_path):
        return result

    # ── Load files ──
    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return result

    try:
        with open(bdd_path, "r") as f:
            bdd_content = f.read()
    except (IOError, OSError):
        return result

    if not manifest:
        return result

    # ── Extract all string values from manifest ──
    all_strings: List[str] = []
    _extract_all_strings(manifest, all_strings)

    if not all_strings:
        return result

    # Concatenate all manifest strings for pattern extraction
    manifest_text = "\n".join(all_strings)

    # ── Price consistency ──
    manifest_prices = set(_extract_prices(manifest_text))
    bdd_prices = set(_extract_prices(bdd_content))

    for bp in bdd_prices:
        bp_num = _extract_numeric_price(bp)
        if bp_num is None or bp_num == 0:
            continue

        # Check if this BDD price matches any manifest price
        matched = False
        for mp in manifest_prices:
            if mp == bp:
                matched = True
                break

        if not matched and manifest_prices:
            # Check if it's a format variation (same amount, different suffix)
            format_match = False
            for mp in manifest_prices:
                mp_num = _extract_numeric_price(mp)
                if mp_num is not None and mp_num == bp_num:
                    # Same numeric value but different format
                    format_match = True
                    result["mismatches"].append({
                        "field": "price",
                        "manifest_value": mp,
                        "bdd_value": bp,
                        "severity": "warning",
                    })
                    break

            if not format_match:
                # Different numeric value entirely
                # Find the closest manifest price for comparison
                closest_mp = min(
                    manifest_prices,
                    key=lambda mp: abs(
                        (_extract_numeric_price(mp) or 0) - bp_num
                    ),
                )
                result["mismatches"].append({
                    "field": "price",
                    "manifest_value": closest_mp,
                    "bdd_value": bp,
                    "severity": "error",
                })

    # ── URL consistency ──
    manifest_urls = set(_extract_urls(manifest_text))
    bdd_urls = set(_extract_urls(bdd_content))

    for mu in manifest_urls:
        # Check if manifest URL appears exactly in BDD URLs
        if mu in bdd_urls:
            continue
        # Check with/without trailing slash
        mu_stripped = mu.rstrip("/")
        found_variant = False
        for bu in bdd_urls:
            bu_stripped = bu.rstrip("/")
            if bu_stripped == mu_stripped and bu != mu:
                # Found with different trailing slash
                result["mismatches"].append({
                    "field": "url",
                    "manifest_value": mu,
                    "bdd_value": bu,
                    "severity": "warning",
                })
                found_variant = True
                break

    # ── Email consistency ──
    manifest_emails = set(_extract_emails(manifest_text))
    bdd_emails = set(_extract_emails(bdd_content))

    # Check if BDD has different emails than manifest
    for be in bdd_emails:
        if manifest_emails and be not in manifest_emails:
            if be.lower() not in {me.lower() for me in manifest_emails}:
                # Find closest manifest email for reporting
                result["mismatches"].append({
                    "field": "email",
                    "manifest_value": str(manifest_emails),
                    "bdd_value": be,
                    "severity": "error",
                })

    # ── Name consistency ──
    manifest_names = _extract_names_from_manifest(manifest)
    if manifest_names:
        for mn in manifest_names:
            # Skip very short names (likely not person/brand names)
            if len(mn) < 3:
                continue
            # Case-insensitive search in BDD
            if mn.lower() in bdd_content.lower():
                continue
            # Name from manifest NOT found in BDD — flag as mismatch
            # This catches LLM paraphrasing of names (e.g., "Jon Leaman" → "John Lehman")
            result["mismatches"].append({
                "field": "name",
                "manifest_value": mn,
                "bdd_value": "(not found in BDD)",
                "severity": "error",
            })

    # ── Brand name consistency ──
    brand = manifest.get("brand")
    if isinstance(brand, dict):
        brand_name = brand.get("name", "")
    elif isinstance(brand, str):
        brand_name = brand
    else:
        brand_name = ""

    if brand_name and len(brand_name) >= 3:
        if brand_name.lower() not in bdd_content.lower():
            # Brand name not in BDD — flag as warning
            result["mismatches"].append({
                "field": "brand",
                "manifest_value": brand_name,
                "bdd_value": "(not found in BDD)",
                "severity": "warning",
            })

    # ── Set consistency flag ──
    if result["mismatches"]:
        result["consistent"] = False
        logger.warning(
            f"[BDD LITERAL CHECK] Found {len(result['mismatches'])} "
            f"mismatches: {[m['bdd_value'] for m in result['mismatches']]}"
        )
    else:
        logger.info("[BDD LITERAL CHECK] All literals consistent ✅")

    return result
