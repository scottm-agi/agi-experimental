"""
BDD Coverage & Validation Checks

Extracted from bdd_generator.py as part of modularization.

Contains:
  - check_bdd_coverage: REQ-ID coverage gate
  - check_bdd_error_paths: error-path coverage for integrations
  - validate_bdd_prices: cross-reference BDD vs manifest prices
  - validate_bdd_scenario_input: structured scenario validation
"""

import logging
import re
from typing import Any, Dict, List

from python.helpers.gate_config import BDD_COVERAGE_THRESHOLD

logger = logging.getLogger("agix.bdd_generator")


# ─── F-4 (ITR-18): BDD Error-Path Coverage Check ────────────────────────

# Keywords that indicate error-path coverage in BDD text
_ERROR_PATH_KEYWORDS = re.compile(
    r'\b(?:error|fail|failure|bounce|rate\s*limit|timeout|invalid|'
    r'500|4xx|401|403|429|unavailable|retry|exception|denied|rejected)\b',
    re.IGNORECASE,
)


# ─── BDD Coverage Gate (F-2, ITR-11) ────────────────────────────────────

def check_bdd_coverage(
    skeleton_reqs: List[Dict],
    bdd_text: str,
    threshold: float = BDD_COVERAGE_THRESHOLD,
) -> Dict[str, Any]:
    """Check that BDD scenarios text covers sufficient bdd_needed requirements.

    Universal gate function — works for any project. Scans BDD text for
    REQ-ID references and computes coverage against the skeleton.

    Args:
        skeleton_reqs: List of skeleton requirement dicts. Each must have
            'req_id' (str) and 'bdd_needed' (bool).
        bdd_text: The full BDD scenarios markdown text to scan.
        threshold: Minimum coverage ratio (default 0.90 = 90%).

    Returns:
        Dict with:
            coverage: float (0.0–1.0)
            pass: bool (coverage >= threshold)
            total_bdd_needed: int
            covered: int
            missing: List[str] — REQ-IDs that need BDD but aren't covered
    """
    # Filter to only bdd_needed requirements
    bdd_needed = [r for r in skeleton_reqs if r.get("bdd_needed", False)]

    if not bdd_needed:
        # No BDD requirements → vacuous truth, 100% coverage
        return {
            "coverage": 1.0,
            "pass": True,
            "total_bdd_needed": 0,
            "covered": 0,
            "missing": [],
        }

    # Scan BDD text for REQ-ID references
    covered_ids = set()
    for req in bdd_needed:
        req_id = req.get("req_id", "")
        if req_id and req_id in bdd_text:
            covered_ids.add(req_id)

    total = len(bdd_needed)
    covered = len(covered_ids)
    coverage = covered / total if total > 0 else 1.0

    missing = [r["req_id"] for r in bdd_needed if r["req_id"] not in covered_ids]

    result = {
        "coverage": coverage,
        "pass": coverage >= threshold,
        "total_bdd_needed": total,
        "covered": covered,
        "missing": missing,
    }

    if not result["pass"]:
        logger.warning(
            f"[BDD COVERAGE] Gate FAILED: {covered}/{total} = {coverage:.1%} "
            f"(threshold: {threshold:.0%}). Missing: {missing}"
        )
    else:
        logger.info(
            f"[BDD COVERAGE] Gate PASSED: {covered}/{total} = {coverage:.1%}"
        )

    return result


def check_bdd_error_paths(
    skeleton_reqs: List[Dict],
    bdd_text: str,
) -> Dict[str, Any]:
    """Check that integration requirements have error-path BDD scenarios.

    ADVISORY check (no gate block). Filters skeleton_reqs to integration-
    category requirements and verifies each has at least one error/failure
    scenario in the BDD text.

    Args:
        skeleton_reqs: List of skeleton requirement dicts with 'req_id'
            and 'category' fields.
        bdd_text: The full BDD scenarios markdown text to scan.

    Returns:
        Dict with:
            has_error_paths: bool — True if all integration reqs have error paths
            total_integration_reqs: int
            covered: int — count of integration reqs with error-path scenarios
            missing: List[str] — REQ-IDs without error-path coverage
    """
    integration_categories = {'integration', 'integration_endpoint'}
    integration_reqs = [
        r for r in skeleton_reqs
        if r.get('category', '') in integration_categories
    ]

    if not integration_reqs:
        return {
            'has_error_paths': True,
            'total_integration_reqs': 0,
            'covered': 0,
            'missing': [],
        }

    covered = []
    missing = []

    for req in integration_reqs:
        req_id = req.get('req_id', '')
        if not req_id:
            continue

        # Find all BDD text segments that reference this REQ-ID
        has_error = False
        lines = bdd_text.split('\n')
        for i, line in enumerate(lines):
            if req_id in line:
                # Check a window of ±10 lines around the REQ-ID mention
                window_start = max(0, i - 5)
                window_end = min(len(lines), i + 10)
                window_text = '\n'.join(lines[window_start:window_end])
                if _ERROR_PATH_KEYWORDS.search(window_text):
                    has_error = True
                    break

        if has_error:
            covered.append(req_id)
        else:
            missing.append(req_id)

    all_covered = len(missing) == 0

    if missing:
        logger.warning(
            f"[BDD ERROR PATHS] Advisory: {len(missing)} integration requirements "
            f"lack error-path BDD scenarios: {missing}"
        )
    else:
        logger.info(
            f"[BDD ERROR PATHS] All {len(covered)} integration requirements "
            f"have error-path coverage ✅"
        )

    return {
        'has_error_paths': all_covered,
        'total_integration_reqs': len(integration_reqs),
        'covered': len(covered),
        'missing': missing,
    }


# ─── F-7 (ITR-18): BDD Price Cross-Reference Validation ─────────────────

def validate_bdd_prices(
    bdd_text: str,
    manifest: Dict,
) -> Dict[str, Any]:
    """Cross-reference prices between BDD text and manifest pricing.

    Extracts all $NNN patterns from BDD text and all prices from the
    manifest's 'pricing' section. Checks that every BDD price appears
    in the manifest prices.

    Args:
        bdd_text: The full BDD scenarios markdown text.
        manifest: The content_manifest.json dict.

    Returns:
        Dict with:
            consistent: bool — True if all BDD prices match manifest
            bdd_prices: List[str] — unique $NNN values found in BDD
            manifest_prices: List[str] — unique $NNN values from manifest
            mismatches: List[dict] — each with 'bdd_price' key for non-matching prices
    """
    from python.helpers.manifest_parser import _extract_strings

    # Extract prices from BDD text
    bdd_price_matches = re.findall(r'\$[\d,]+', bdd_text)
    bdd_prices = sorted(set(bdd_price_matches))

    # Extract prices from manifest pricing section
    pricing_section = manifest.get('pricing', {})
    manifest_strings: List[str] = []
    _extract_strings(pricing_section, manifest_strings)

    manifest_price_values = set()
    for s in manifest_strings:
        for match in re.findall(r'\$[\d,]+', s):
            manifest_price_values.add(match)
    manifest_prices = sorted(manifest_price_values)

    # If no BDD prices or no manifest prices, consider consistent
    if not bdd_prices or not manifest_prices:
        return {
            'consistent': True,
            'bdd_prices': bdd_prices,
            'manifest_prices': manifest_prices,
            'mismatches': [],
        }

    # Check each BDD price against manifest prices
    mismatches = []
    for bp in bdd_prices:
        if bp not in manifest_price_values:
            mismatches.append({'bdd_price': bp})

    consistent = len(mismatches) == 0

    if mismatches:
        logger.warning(
            f"[BDD PRICE CHECK] Found {len(mismatches)} price mismatches: "
            f"{[m['bdd_price'] for m in mismatches]} not in manifest {manifest_prices}"
        )
    else:
        logger.info("[BDD PRICE CHECK] All BDD prices match manifest ✅")

    return {
        'consistent': consistent,
        'bdd_prices': bdd_prices,
        'manifest_prices': manifest_prices,
        'mismatches': mismatches,
    }


# ─── BDD Structured Tool (ITR-14 ISS-2 CORRECT FIX) ─────────────────────

def validate_bdd_scenario_input(
    scenarios: List[Dict[str, Any]],
    skeleton_reqs: List[Dict[str, Any]],
    threshold: float = BDD_COVERAGE_THRESHOLD,
) -> Dict[str, Any]:
    """Validate structured BDD scenario input against the test skeleton.

    Called by the requirements tool's save_bdd_scenarios action. Checks:
    1. All req_ids in scenarios exist in the skeleton
    2. Coverage of bdd_needed requirements >= threshold
    3. Returns missing REQ-IDs so the LLM can fill them in

    Args:
        scenarios: List of dicts with keys:
            req_ids: List[str] — REQ-IDs this scenario covers
            feature: str — Feature name
            scenario: str — Scenario name
            given: str — Given clause
            when: str — When clause
            then: List[str] — Then clauses
        skeleton_reqs: List of dicts with req_id and bdd_needed from skeleton.
        threshold: Coverage threshold (default 0.90).

    Returns:
        Dict with pass, coverage, invalid_req_ids, missing_req_ids, covered_req_ids
    """
    # Build lookup of valid REQ-IDs
    valid_req_ids = {r.get("req_id") for r in skeleton_reqs if r.get("req_id")}
    bdd_needed_ids = {
        r.get("req_id") for r in skeleton_reqs
        if r.get("bdd_needed", False) and r.get("req_id")
    }

    # Collect all req_ids from scenarios
    covered_ids = set()
    invalid_ids = set()
    for scenario in scenarios:
        for req_id in scenario.get("req_ids", []):
            if req_id in valid_req_ids:
                covered_ids.add(req_id)
            else:
                invalid_ids.add(req_id)
        # RCA-456: Auto-strip invalid (hallucinated) REQ-IDs from the scenario.
        # The LLM sometimes invents plausible-looking REQ-{hex} IDs that don't
        # exist in the skeleton. Strip them so downstream consumers (TDD
        # generator, coverage checker) don't get confused.
        if valid_req_ids:  # Only strip if we have a skeleton to validate against
            scenario["req_ids"] = [
                r for r in scenario.get("req_ids", []) if r in valid_req_ids
            ]

    # Coverage = covered bdd_needed / total bdd_needed
    covered_bdd = covered_ids & bdd_needed_ids
    total_bdd = len(bdd_needed_ids)
    coverage = len(covered_bdd) / total_bdd if total_bdd > 0 else 1.0
    missing_ids = sorted(bdd_needed_ids - covered_ids)

    result = {
        "pass": coverage >= threshold,
        "coverage": coverage,
        "total_bdd_needed": total_bdd,
        "covered": len(covered_bdd),
        "covered_req_ids": sorted(covered_ids),
        "missing_req_ids": missing_ids,
        "invalid_req_ids": sorted(invalid_ids),
    }

    # ── F-6 (ITR-15): Granularity Advisory Hints ──────────────────────
    granularity_hints = []
    total_reqs_in_scenarios = 0

    for scenario in scenarios:
        req_ids = scenario.get("req_ids", [])
        req_count = len(req_ids)
        total_reqs_in_scenarios += req_count

        if req_count >= 4:
            scenario_name = scenario.get("scenario", "unnamed")
            granularity_hints.append({
                "scenario": scenario_name,
                "req_count": req_count,
                "req_ids": req_ids[:10],
                "suggestion": (
                    f"Consider splitting this scenario into {max(2, req_count // 3)} "
                    f"more focused scenarios. Each scenario should test a single "
                    f"behavior — {req_count} requirements in one scenario may mask "
                    f"individual failures during verification."
                ),
            })

    scenario_count = len(scenarios) if scenarios else 1
    avg_ratio = total_reqs_in_scenarios / scenario_count

    result["avg_reqs_per_scenario"] = avg_ratio
    result["granularity_hints"] = granularity_hints

    if granularity_hints:
        logger.info(
            f"[BDD TOOL] Granularity advisory: {len(granularity_hints)} scenario(s) "
            f"have 4+ REQ-IDs (avg {avg_ratio:.1f} reqs/scenario). "
            f"Consider splitting for better test isolation."
        )

    # ── F-1 (ITR-16): BDD Template Quality Detection ─────────────────
    TEMPLATE_PATTERNS = [
        # Feature templates
        "the feature implementation handles the core use case correctly",
        "the feature handles edge cases (empty input, invalid data, boundary values)",
        "the feature has unit tests covering happy path and error cases",
        # Integration templates
        "the source file imports the integration sdk or uses an http client",
        "the source file reads the api key via environment variables or secret management",
        "the integration test verifies the import exists and env var is referenced",
        "the test does not mock the entire api response",
        "the integration handles error responses gracefully",
        # Page templates
        "the page file contains real content sections",
        "the page does not contain placeholder text",
        "the page contains multiple semantic html sections",
        # Generic inspection patterns
        "the source file is inspected",
        "is implemented",
    ]

    TEMPLATE_QUALITY_THRESHOLD = 0.80

    templated_count = 0
    template_quality_hints = []

    for scenario in scenarios:
        text_parts = []
        text_parts.append(scenario.get("given", ""))
        text_parts.append(scenario.get("when", ""))
        then_clauses = scenario.get("then", [])
        if isinstance(then_clauses, list):
            text_parts.extend(then_clauses)
        elif isinstance(then_clauses, str):
            text_parts.append(then_clauses)

        combined_text = " ".join(text_parts).lower().strip()

        is_templated = False
        matched_patterns = []
        for pattern in TEMPLATE_PATTERNS:
            if pattern.lower() in combined_text:
                is_templated = True
                matched_patterns.append(pattern)

        if is_templated:
            templated_count += 1
            scenario_name = scenario.get("scenario", "unnamed")
            req_ids = scenario.get("req_ids", [])
            template_quality_hints.append({
                "scenario": scenario_name,
                "req_ids": req_ids,
                "matched_patterns": matched_patterns[:3],
                "suggestion": (
                    f"This scenario uses generic template language. "
                    f"Enrich with domain-specific Given/When/Then using "
                    f"values from the content manifest (names, prices, URLs, "
                    f"API endpoints, specific behaviors)."
                ),
            })

    templated_ratio = (
        templated_count / len(scenarios) if scenarios else 0.0
    )
    quality_pass = templated_ratio < TEMPLATE_QUALITY_THRESHOLD

    result["template_quality"] = {
        "templated_count": templated_count,
        "total_scenarios": len(scenarios),
        "templated_ratio": templated_ratio,
        "quality_pass": quality_pass,
        "threshold": TEMPLATE_QUALITY_THRESHOLD,
        "quality_hints": template_quality_hints,
    }

    if not quality_pass:
        logger.warning(
            f"[BDD TOOL] Template quality FAILED: {templated_count}/{len(scenarios)} "
            f"scenarios ({templated_ratio:.0%}) use generic templates. "
            f"Enrich with domain-specific Given/When/Then before Phase 3."
        )
    elif template_quality_hints:
        logger.info(
            f"[BDD TOOL] Template quality advisory: {templated_count}/{len(scenarios)} "
            f"scenarios use generic templates (below {TEMPLATE_QUALITY_THRESHOLD:.0%} threshold)."
        )

    if not result["pass"]:
        logger.warning(
            f"[BDD TOOL] Coverage {coverage:.1%} < {threshold:.0%}. "
            f"Missing {len(missing_ids)} REQ-IDs: {missing_ids[:10]}"
        )
    else:
        logger.info(
            f"[BDD TOOL] Coverage PASSED: {len(covered_bdd)}/{total_bdd} "
            f"= {coverage:.1%}"
        )

    return result
