"""
BDD Literal Cross-Checking & Auto-Correction

Extracted from bdd_generator.py as part of modularization.

Contains:
  - validate_bdd_literals: cross-check BDD prices against manifest
  - auto_correct_bdd_literals: auto-fix price mismatches in BDD
"""

import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger("agix.bdd_generator")


# ─── ITR-14 I-1: BDD Literal Cross-Checker (L1) ────────────────────────

def validate_bdd_literals(project_dir: str) -> List[Dict[str, str]]:
    """Cross-check BDD scenario literals against content_manifest.json.

    Deterministic L1 tool that finds price/URL mismatches between what the
    architect wrote in docs/bdd-scenarios.md and what the manifest says.

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of dicts with 'field', 'manifest_value', 'bdd_value', 'line'
        for each mismatch. Empty list = all good.
    """
    from python.helpers.manifest_parser import parse_manifest, _extract_strings

    manifest = parse_manifest(project_dir)

    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.exists(bdd_path):
        return []

    try:
        with open(bdd_path) as f:
            bdd_content = f.read()
    except IOError:
        return []

    mismatches: List[Dict[str, str]] = []

    # Extract all price values from manifest (System 5: uses parsed pricing)
    manifest_prices: List[str] = []
    _extract_strings(manifest.pricing, manifest_prices)
    # Also check branding and scenarios for prices
    _extract_strings(manifest.branding, manifest_prices)
    _extract_strings(manifest.scenarios, manifest_prices)
    manifest_price_values = set()
    for lit in manifest_prices:
        for match in re.findall(r'\$[\d,]+(?:/\w+)?', lit):
            manifest_price_values.add(match)

    # Extract all price values from BDD
    bdd_lines = bdd_content.split('\n')
    for i, line in enumerate(bdd_lines, 1):
        bdd_prices = re.findall(r'\$[\d,]+(?:/\w+)?', line)
        for bp in bdd_prices:
            if bp not in manifest_price_values and manifest_price_values:
                mismatches.append({
                    'field': 'price',
                    'manifest_value': str(manifest_price_values),
                    'bdd_value': bp,
                    'line': i,
                    'line_content': line.strip(),
                })

    if mismatches:
        logger.warning(
            f"[BDD LITERAL CHECK] Found {len(mismatches)} price mismatches "
            f"between BDD and manifest: {[m['bdd_value'] for m in mismatches]}"
        )
    else:
        logger.info("[BDD LITERAL CHECK] All BDD literals match manifest ✅")

    return mismatches


# ─── F-6 (ITR-42): BDD Price Literal Auto-Correction ───────────────────


def auto_correct_bdd_literals(project_dir: str) -> List[Dict[str, str]]:
    """Auto-correct price mismatches in BDD scenarios using manifest as truth.

    ITR-42 F-6: The LLM-generated BDD scenarios often contain hallucinated
    prices. This function detects mismatches and corrects them in-place,
    replacing each wrong price with the closest numeric match from the manifest.

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of dicts with 'old_price', 'new_price', 'line' for each correction.
        Empty list if no corrections needed or files are missing.
    """
    from python.helpers.manifest_parser import parse_manifest, _extract_strings

    manifest = parse_manifest(project_dir)

    bdd_path = os.path.join(project_dir, 'docs', 'bdd-scenarios.md')
    if not os.path.exists(bdd_path):
        return []

    try:
        with open(bdd_path) as f:
            bdd_content = f.read()
    except IOError:
        return []

    # Extract manifest prices (System 5: uses parsed pricing section)
    manifest_strings: List[str] = []
    _extract_strings(manifest.pricing, manifest_strings)

    manifest_price_values: set = set()
    for s in manifest_strings:
        for match in re.findall(r'\$[\d,]+', s):
            manifest_price_values.add(match)

    if not manifest_price_values:
        return []

    def _parse_price(price_str: str) -> int:
        """Parse '$49' or '$1,200' to int."""
        return int(price_str.replace('$', '').replace(',', ''))

    def _closest_manifest_price(bdd_price: str) -> str:
        """Find the manifest price with smallest numeric distance."""
        try:
            bdd_num = _parse_price(bdd_price)
        except ValueError:
            return bdd_price
        best = min(
            manifest_price_values,
            key=lambda mp: abs(_parse_price(mp) - bdd_num),
        )
        return best

    # Find and correct mismatches
    corrections: List[Dict[str, str]] = []
    lines = bdd_content.split('\n')
    modified = False

    for i, line in enumerate(lines):
        bdd_prices = re.findall(r'\$[\d,]+', line)
        for bp in bdd_prices:
            if bp not in manifest_price_values:
                new_price = _closest_manifest_price(bp)
                if new_price != bp:
                    lines[i] = lines[i].replace(bp, new_price, 1)
                    corrections.append({
                        'old_price': bp,
                        'new_price': new_price,
                        'line': str(i + 1),
                    })
                    modified = True

    if modified:
        with open(bdd_path, 'w') as f:
            f.write('\n'.join(lines))
        logger.info(
            f"[BDD AUTOCORRECT] Fixed {len(corrections)} price mismatches: "
            f"{[(c['old_price'], c['new_price']) for c in corrections]}"
        )

    return corrections
