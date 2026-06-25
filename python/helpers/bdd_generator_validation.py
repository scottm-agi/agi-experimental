"""
BDD Scenario Generation Module

Extracted from skeleton_generator.py as part of P0-3 decomposition.
Handles all BDD (Behavior-Driven Development) related functionality:
  - BDD skeleton generation from requirements ledger
  - BDD coverage gate (REQ-ID presence check)
  - BDD content coverage (noun overlap + boilerplate detection)
  - BDD error-path coverage (integration requirements)
  - BDD price cross-reference validation
  - BDD literal cross-checker
  - BDD behavioral consistency (manifest routing inversions)
  - BDD REQ-ID enforcement (deterministic injection)
  - BDD structured tool validation + assembly
  - Feature sub-type classification
  - Compliance sub-type classification
  - Feature file generation (.feature Gherkin files)
  - Scenario manifest generation (YAML)

Architecture:
  - This module provides the BDD layer of the requirements pipeline
  - Consumed by: requirements tool (Phase 2 gate), orchestrator, architect
  - Imports shared utilities from manifest_parser.py and skeleton_generator.py
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from python.helpers.gate_config import BDD_COVERAGE_THRESHOLD
from python.helpers.source_scanner import read_project_files as _ss_read_project_files

logger = logging.getLogger("agix.bdd_generator")


# ─── Category-Specific THEN Clauses (U-12 Prevention) ───────────────────
# Categories with known structural requirements get concrete THEN clauses
# instead of generic [FILL — architect enriches] stubs. This ensures the
# code agent's TDD test enforces content density at WRITE time, not just
# file existence.
_CATEGORY_THEN_CLAUSES = {
    "page": (
        "    Then the page file contains real content sections (hero, features, CTAs, footer)\n"
        "    And the page does NOT contain placeholder text (Lorem ipsum, TODO, TBD)\n"
        "    And the page contains multiple semantic HTML sections with descriptive content"
    ),
    "integration": (
        "    Then the source file imports the integration SDK or uses an HTTP client for API calls\n"
        "    And the source file reads the API key via environment variables or secret management (NOT hardcoded)\n"
        "    And the integration test verifies the import exists and env var is referenced\n"
        "    And the test does NOT mock the entire API response — it verifies real SDK usage\n"
        "    And the integration handles error responses gracefully"
    ),
    "integration_endpoint": (
        "    Then the API route handler returns a valid response matching the shared type contract\n"
        "    And the route handles missing/invalid parameters with appropriate error codes"
    ),
    # T-1: URL requirements must assert presence in source code
    "url": (
        "    Then the URL value from content_manifest.json appears in at least one source file\n"
        "    And the URL is wired as a clickable link, button action, or environment variable reference\n"
        "    And the URL is NOT only in .env — it must be consumed by a UI component"
    ),
    # T-1: Scaffold cleanup must assert boilerplate is removed
    "scaffold_cleanup": (
        "    Then the project config 'name' is NOT a scaffold default ('scaffold-temp', 'my-app', etc.)\n"
        "    And the project config 'name' matches the slugified project name from content_manifest.json\n"
        "    And .env files do NOT contain default credentials ('johndoe', 'randompassword', 'mydb')\n"
        "    And README.md does NOT contain scaffold boilerplate ('Create Next App', 'bootstrapped with')"
    ),
    # T-1: Delivery standards must assert project-specific content
    "delivery": (
        "    Then README.md contains the project name from content_manifest.json\n"
        "    And README.md contains setup instructions and tech stack description\n"
        "    And README.md does NOT contain 'Create Next App' or scaffold boilerplate"
    ),
    # T-1: Design token requirements must assert consumption in source
    "design": (
        "    Then the global styles file contains CSS custom properties matching design-tokens.json keys\n"
        "    And no hardcoded color values appear in page-level components\n"
        "    And at least one source file imports or references design-tokens.json"
    ),
    # F-13 (ITR-12): Complete THEN clauses for all missing categories
    "feature": (
        "    Then the feature implementation handles the core use case correctly\n"
        "    And the feature handles edge cases (empty input, invalid data, boundary values)\n"
        "    And the feature has unit tests covering happy path and error cases"
    ),
    "branding": (
        "    Then the brand element (name, logo, colors, copy) matches the content_manifest.json exactly\n"
        "    And no placeholder brand values remain (Company Name, Your Brand, etc.)\n"
        "    And the brand element is visible on the appropriate page(s)"
    ),
    "copy": (
        "    Then the copy text matches the content_manifest.json specification\n"
        "    And the copy does NOT contain placeholder text (Lorem ipsum, TODO, TBD)\n"
        "    And the tone/voice matches the brand guidelines from the manifest"
    ),
    "content_constraint": (
        "    Then the content respects the specified constraint (word count, character limit, format)\n"
        "    And the constraint is enforced programmatically via validation logic\n"
        "    And validation errors are shown when the constraint is violated"
    ),
    "ui_element": (
        "    Then the UI element is visible and interactive on the specified page\n"
        "    And the element has proper styling matching design-tokens.json\n"
        "    And the element is accessible (proper ARIA labels, keyboard navigation)"
    ),
    "deployment": (
        "    Then the deployment configuration is complete and valid\n"
        "    And environment variables are documented in .env.example\n"
        "    And the build command succeeds without errors"
    ),
    # F-12-L1 (ITR-12): Compliance THEN clause with data persistence
    "compliance": (
        "    Then the compliance requirement is enforced in both UI and backend\n"
        "    And the compliance state is persisted in the database (e.g., opt-out records stored)\n"
        "    And the system prevents further contact after opt-out\n"
        "    And compliance audit trail is maintained"
    ),
    # WB-4: Compliance sub-type THEN clauses — regulation-specific acceptance criteria
    "compliance_email": (
        "    Then all marketing/transactional emails include a physical mailing address\n"
        "    And every email contains a working unsubscribe link\n"
        "    And sender identification (From name and address) is accurate and not deceptive\n"
        "    And opt-out requests are honored within 10 business days\n"
        "    And email subject lines are not deceptive or misleading\n"
        "    And the opt-out mechanism is clearly visible (not hidden in fine print)"
    ),
    "compliance_privacy": (
        "    Then a privacy policy page exists and is linked from the footer\n"
        "    And the privacy policy describes what data is collected and how it is used\n"
        "    And a cookie consent banner is displayed to new visitors\n"
        "    And a data deletion mechanism exists (e.g., account deletion or erasure request form)\n"
        "    And user data is not shared with third parties without explicit consent\n"
        "    And data collection is limited to what is necessary for the stated purpose"
    ),
    "compliance_accessibility": (
        "    Then all images have descriptive alt text attributes\n"
        "    And the entire application is keyboard navigable (Tab, Enter, Escape)\n"
        "    And color contrast meets WCAG AA standards (4.5:1 for normal text, 3:1 for large)\n"
        "    And all form inputs have associated label elements\n"
        "    And ARIA landmarks are used for major page sections (nav, main, footer)\n"
        "    And interactive elements have visible focus indicators"
    ),
    # F-2 (ITR-15): Feature sub-category THEN clauses
    "feature_api": (
        "    Then the API/service endpoint returns structured data\n"
        "    And the endpoint reads secrets from environment variables (NOT hardcoded)\n"
        "    And the endpoint handles error responses gracefully (rate limits, timeouts, 4xx/5xx)\n"
        "    And the endpoint has integration tests verifying real SDK/HTTP usage"
    ),
    "feature_workflow": (
        "    Then the workflow executes steps in the specified sequence\n"
        "    And the workflow handles edge cases (missing inputs, timeouts, partial failures)\n"
        "    And the workflow produces observable side effects (DB writes, API calls, emails sent)\n"
        "    And the workflow timing/scheduling matches the specification"
    ),
    "feature_ui": (
        "    Then the UI component renders all required data from props or API\n"
        "    And the component is responsive and accessible\n"
        "    And the component uses design tokens from design-tokens.json (no hardcoded color values)\n"
        "    And the component has visual states for loading, empty, error, and success"
    ),
    "feature_data": (
        "    Then the calculation/scoring produces correct results for known inputs\n"
        "    And the algorithm handles edge cases (zero values, missing data, boundary conditions)\n"
        "    And the data processing is deterministic (same input → same output)\n"
        "    And the results are stored/cached appropriately for downstream consumers"
    ),
    # F1-a: UI shell — shared navigation/layout for multi-page apps
    "ui_shell": (
        "    Then the root layout includes a shared navigation component\n"
        "    And the navigation contains links to all application routes\n"
        "    And the navigation component is visible on every page\n"
        "    And the layout provides consistent structure across all pages\n"
        "    And individual page.tsx files do NOT duplicate the navigation component\n"  # F-11 negative
    ),
    # F-3 (ITR-25): Infrastructure requirements — build, config, env coherence
    "infra": (
        "    Then layout.tsx/layout.jsx does NOT contain 'use client' directive\n"
        "    And all @/ import aliases in tsconfig.json resolve to existing directories\n"
        "    And every process.env.X reference in src/ has a corresponding .env entry\n"
        "    And package.json contains all packages imported in src/ files\n"
        "    And tailwind.config content paths include src/**/*.{ts,tsx}\n"
        "    And the build command (npm run build) exits with code 0"
    ),
    # F-11: Navigation exclusivity — nav lives in root layout only
    "nav_exclusivity": (
        "    Then the root layout contains exactly one <nav> element\n"
        "    And page.tsx files do NOT contain <nav> elements\n"
        "    And the navigation is defined ONLY in the root layout component\n"
    ),
    # F-11: Color mode consistency across all pages
    "theme_consistency": (
        "    Then ALL page.tsx files use the SAME color mode (all light OR all dark)\n"
        "    And the color mode matches the architecture document declaration\n"
        "    And no page uses conflicting dark:/light: utility classes\n"
    ),
    # F-10: Design token consumption — components use tokens, not raw Tailwind
    "design_token_consumption": (
        "    Then component files use design token classes (bg-primary-*, text-accent-*)\n"
        "    And component files do NOT use default Tailwind colors (slate-*, gray-*, zinc-*)\n"
        "    And global styles file contains CSS custom properties from design-tokens.json\n"
        "    And tailwind.config maps token names to custom properties\n"
    ),
    # F-12: Cross-cutting files — error, loading, not-found boundaries
    "cross_cutting_files": (
        "    Then the project contains error.tsx for error boundaries\n"
        "    And the project contains loading.tsx for loading states\n"
        "    And the project contains not-found.tsx for 404 pages\n"
        "    And these files are in the app root directory\n"
    ),
    # F-13: Dashboard data sourcing — no hardcoded statistics
    "dynamic_data": (
        "    Then dashboard statistics come from database queries (Prisma/fetch)\n"
        "    And dashboard page files do NOT contain hardcoded large numbers in JSX\n"
        "    And data loading uses server components or API routes\n"
    ),
}


# ─── F-2 (ITR-15): Feature Sub-Category Classification ──────────────────
# Universal keyword patterns to sub-classify features into specific types.

_FEATURE_SUBTYPE_PATTERNS = {
    "feature_api": re.compile(
        r'\b(?:api|endpoint|fetch(?:ing)?|search(?:ing)?|engine|query|scrap(?:e|ing)|scan(?:ning)?|webhook)\b',
        re.IGNORECASE,
    ),
    "feature_workflow": re.compile(
        r'\b(?:sequence|automation|queue|schedul(?:e|ing)|drip|pipeline|cron|trigger|batch)\b',
        re.IGNORECASE,
    ),
    "feature_ui": re.compile(
        r'\b(?:page|dashboard|table|display(?:ing)?|view|form|button|interface|component|render(?:ing)?)\b',
        re.IGNORECASE,
    ),
    "feature_data": re.compile(
        r'\b(?:scor(?:e|ing)|calculat(?:e|ion)|algorithm|analytics|metrics|aggregat(?:e|ion)|rank(?:ing)?|rate)\b',
        re.IGNORECASE,
    ),
}


from python.helpers.bdd_generator_helpers import _classify_feature_subtype, _classify_compliance_subtype, _resolve_category, _classify_sentiment, _classify_destination, _extract_manifest_conditions, _extract_key_nouns, _extract_bdd_steps, _is_boilerplate_scenario
from python.helpers.bdd_generator_creation import generate_bdd_skeleton, generate_feature_files, generate_scenario_manifest, assemble_bdd_from_structured, auto_correct_bdd_literals

from python.helpers.bdd_generator_constants import (
    _CATEGORY_THEN_CLAUSES,
    _FEATURE_SUBTYPE_PATTERNS,
    _COMPLIANCE_SUBTYPE_PATTERNS,
    _BDD_CATEGORIES,
    _ERROR_PATH_KEYWORDS,
    _HAPPY_TRIGGERS,
    _UNHAPPY_TRIGGERS,
    _PUBLIC_DESTINATIONS,
    _PRIVATE_DESTINATIONS,
    _SCENARIO_BLOCK_RE,
    _THEN_LINE_RE,
    _WHEN_LINE_RE,
    _STOP_WORDS,
    _BOILERPLATE_PATTERNS,
)


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

def enforce_bdd_req_traceability(project_dir: str) -> Dict[str, Any]:
    """Deterministically inject missing [REQ-xxx] tags into architect BDD.

    Reads the architect-enriched docs/bdd-scenarios.md and the test skeleton.
    For each bdd_needed requirement whose REQ-ID is NOT found in the BDD text,
    finds the best-matching Scenario line by word overlap and injects [REQ-xxx].

    This function is IDEMPOTENT — running it twice produces the same output.
    It never removes existing [REQ-xxx] tags.

    Args:
        project_dir: Path to the project directory.

    Returns:
        Dict with:
            enforced: bool — whether enforcement was performed
            injected_count: int — number of REQ-IDs injected
            already_present: int — number already in the text
            coverage: Dict — result of check_bdd_coverage after enforcement
    """
    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    skeleton_path = os.path.join(project_dir, "docs", "test-skeleton.json")

    if not os.path.isfile(bdd_path) or not os.path.isfile(skeleton_path):
        return {
            "enforced": False,
            "injected_count": 0,
            "already_present": 0,
            "coverage": {"pass": False, "coverage": 0.0, "missing": []},
        }

    try:
        with open(bdd_path) as f:
            bdd_text = f.read()
        with open(skeleton_path) as f:
            skeleton_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[BDD ENFORCE] Failed to read files: {e}")
        return {
            "enforced": False,
            "injected_count": 0,
            "already_present": 0,
            "coverage": {"pass": False, "coverage": 0.0, "missing": []},
        }

    skeleton_reqs = skeleton_data.get("requirements", [])
    bdd_needed = [r for r in skeleton_reqs if r.get("bdd_needed", False)]

    if not bdd_needed:
        return {
            "enforced": True,
            "injected_count": 0,
            "already_present": 0,
            "coverage": {"pass": True, "coverage": 1.0, "missing": []},
        }

    # Classify: already present vs missing
    already_present = []
    missing_reqs = []
    for req in bdd_needed:
        req_id = req.get("req_id", "")
        if req_id and req_id in bdd_text:
            already_present.append(req_id)
        elif req_id:
            missing_reqs.append(req)

    if not missing_reqs:
        # All REQ-IDs already present — just return coverage
        cov = check_bdd_coverage(skeleton_reqs, bdd_text)
        return {
            "enforced": True,
            "injected_count": 0,
            "already_present": len(already_present),
            "coverage": cov,
        }

    # Build word-set for each missing requirement (for text matching)
    _STOP = {
        'the', 'and', 'for', 'with', 'from', 'that', 'this', 'are', 'was',
        'will', 'has', 'have', 'not', 'all', 'can', 'should', 'must', 'when',
        'each', 'every', 'any', 'their', 'they', 'into', 'also', 'it', 'is',
        'be', 'to', 'of', 'in', 'on', 'at', 'by', 'an', 'or', 'if', 'a',
    }

    def _words(text: str) -> set:
        return {w.lower() for w in re.findall(r'[a-zA-Z0-9$/.@:_-]{3,}', text)
                if w.lower() not in _STOP}

    req_word_sets = [(req, _words(req.get("text", ""))) for req in missing_reqs]

    # Parse BDD lines and find Scenario lines for injection
    bdd_lines = bdd_text.split("\n")
    scenario_lines = []
    for i, line in enumerate(bdd_lines):
        stripped = line.strip()
        if stripped.startswith("Scenario:") or stripped.startswith("Feature:"):
            scenario_lines.append((i, stripped, _words(stripped)))

    # Match each missing requirement to the best Scenario line
    injected_count = 0
    used_lines = set()  # Prevent same line getting multiple injections

    for req, req_words in req_word_sets:
        req_id = req["req_id"]
        if not req_words:
            continue

        # Score each scenario line by word overlap
        best_idx = -1
        best_score = 0
        for line_idx, line_text, line_words in scenario_lines:
            if line_idx in used_lines:
                continue
            if req_id in line_text:  # Already has this REQ-ID
                continue
            overlap = len(req_words & line_words)
            score = overlap + (0.5 if line_text.startswith("Scenario:") else 0)
            if score > best_score:
                best_score = score
                best_idx = line_idx

        # Require at least 1 word overlap to prevent random injection
        if best_idx >= 0 and best_score >= 1:
            original_line = bdd_lines[best_idx]
            if f"[{req_id}]" not in original_line:
                bdd_lines[best_idx] = f"{original_line} [{req_id}]"
                used_lines.add(best_idx)
                injected_count += 1
                logger.debug(
                    f"[BDD ENFORCE] Injected [{req_id}] on line {best_idx + 1} "
                    f"(score={best_score}): {original_line.strip()[:60]}"
                )

    # Rewrite the BDD file with injected tags
    if injected_count > 0:
        updated_bdd = "\n".join(bdd_lines)
        with open(bdd_path, "w") as f:
            f.write(updated_bdd)
        logger.info(
            f"[BDD ENFORCE] Injected {injected_count} REQ-IDs into "
            f"docs/bdd-scenarios.md ({len(already_present)} already present)"
        )
    else:
        updated_bdd = bdd_text

    # Run coverage check on the enforced BDD
    cov = check_bdd_coverage(skeleton_reqs, updated_bdd)

    if not cov["pass"]:
        logger.warning(
            f"[BDD ENFORCE] Coverage still below threshold after enforcement: "
            f"{cov['covered']}/{cov['total_bdd_needed']} = {cov['coverage']:.1%}. "
            f"Missing: {cov['missing']}"
        )
    else:
        logger.info(
            f"[BDD ENFORCE] Coverage PASSED after enforcement: "
            f"{cov['covered']}/{cov['total_bdd_needed']} = {cov['coverage']:.1%}"
        )

    return {
        "enforced": True,
        "injected_count": injected_count,
        "already_present": len(already_present),
        "coverage": cov,
    }

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

def validate_bdd_behavioral_consistency(project_dir: str) -> List[Dict[str, str]]:
    """Cross-check BDD THEN clauses against manifest conditional routing.

    Reads docs/bdd-scenarios.md and content_manifest.json.
    For each BDD scenario with a THEN clause involving routing/direction,
    checks if the manifest has a conflicting conditional routing pattern.

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of dicts with {scenario, then_clause, manifest_pattern, issue}.
        Empty list if no issues found.
    """
    from python.helpers.manifest_parser import _find_manifest_path

    manifest_path = _find_manifest_path(project_dir)
    if not manifest_path:
        return []

    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.exists(bdd_path):
        return []

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        with open(bdd_path) as f:
            bdd_content = f.read()
    except (json.JSONDecodeError, IOError):
        return []

    manifest_conditions = _extract_manifest_conditions(manifest)
    if not manifest_conditions:
        return []

    mismatches: List[Dict[str, str]] = []

    for match in _SCENARIO_BLOCK_RE.finditer(bdd_content):
        scenario_name = match.group('name').strip()
        scenario_body = match.group('body')
        full_text = scenario_name + '\n' + scenario_body

        when_text = ' '.join(_WHEN_LINE_RE.findall(full_text))
        then_matches = _THEN_LINE_RE.findall(full_text)
        then_text = ' '.join(then_matches)

        trigger_sentiment = _classify_sentiment(when_text)
        if not trigger_sentiment:
            trigger_sentiment = _classify_sentiment(scenario_name)
        then_destination = _classify_destination(then_text)

        if not trigger_sentiment or not then_destination:
            continue

        for cond in manifest_conditions:
            cond_sentiment = _classify_sentiment(cond.get('condition', ''))
            cond_destination = _classify_destination(cond.get('action', ''))

            if not cond_sentiment or not cond_destination:
                continue

            if cond_sentiment == trigger_sentiment and cond_destination != then_destination:
                mismatches.append({
                    'scenario': scenario_name,
                    'then_clause': then_text[:120],
                    'manifest_pattern': f"if {cond.get('condition', '')} → {cond.get('action', '')}",
                    'issue': 'INVERTED',
                })

    # ── Layer 2: Semantic validation for inconclusive L1 scenarios ────
    # When L1 couldn't classify (both sentiment or destination are None),
    # attempt Layer 2 semantic validation using the user prompt.
    prompt_path = os.path.join(project_dir, "docs", "prompt.md")
    prompt_text = ""
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path) as pf:
                prompt_text = pf.read()
        except IOError:
            pass

    if prompt_text:
        for match in _SCENARIO_BLOCK_RE.finditer(bdd_content):
            scenario_name = match.group('name').strip()
            scenario_body = match.group('body')
            full_text = scenario_name + '\n' + scenario_body

            when_text = ' '.join(_WHEN_LINE_RE.findall(full_text))
            then_matches = _THEN_LINE_RE.findall(full_text)
            then_text = ' '.join(then_matches)

            trigger_sentiment = _classify_sentiment(when_text)
            if not trigger_sentiment:
                trigger_sentiment = _classify_sentiment(scenario_name)
            then_destination = _classify_destination(then_text)

            # Only use L2 for scenarios L1 couldn't fully classify
            if trigger_sentiment and then_destination:
                continue  # L1 already handled this

            # L2 validation
            l2_result = _semantic_validate_bdd_routing(
                prompt_text=prompt_text,
                bdd_scenario_text=full_text,
                use_llm=True,
            )
            if l2_result.get("valid") is False and l2_result.get("confidence", 0) > 0.6:
                mismatches.append({
                    'scenario': scenario_name,
                    'then_clause': then_text[:120],
                    'manifest_pattern': f"L2 semantic: {l2_result.get('reasoning', '')[:100]}",
                    'issue': 'INVERTED_L2',
                })

    if mismatches:
        logger.warning(
            f"[BDD BEHAVIORAL CONSISTENCY] Found {len(mismatches)} routing "
            f"inversions between BDD and manifest: "
            f"{[m['scenario'] for m in mismatches]}"
        )
    else:
        logger.info("[BDD BEHAVIORAL CONSISTENCY] All BDD routing matches manifest ✅")

    return mismatches

def validate_bdd_conditional_completeness(project_dir: str) -> List[Dict[str, str]]:
    """Check that ALL manifest conditional branches are covered by separate BDD scenarios.

    ITR-32 SS-5: When the manifest has conditional routing (e.g., happy→Google,
    unhappy→private), the BDD should have separate scenarios for each branch.
    A single collapsed scenario like "customer gets a link" is insufficient.

    This extends validate_bdd_behavioral_consistency() which checks for inversions.
    This function checks for completeness — are all branches covered at all?

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of gap dicts with {condition, action, missing_branch}.
        Empty list if all branches are covered or no conditions exist.
    """
    from python.helpers.manifest_parser import _find_manifest_path

    manifest_path = _find_manifest_path(project_dir)
    if not manifest_path:
        return []

    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.exists(bdd_path):
        return []

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        with open(bdd_path) as f:
            bdd_content = f.read()
    except (json.JSONDecodeError, IOError):
        return []

    manifest_conditions = _extract_manifest_conditions(manifest)
    if not manifest_conditions:
        return []

    # Build a set of sentiments covered by BDD scenarios
    covered_sentiments: set = set()
    for match in _SCENARIO_BLOCK_RE.finditer(bdd_content):
        scenario_name = match.group('name').strip()
        scenario_body = match.group('body')
        full_text = scenario_name + '\n' + scenario_body

        when_text = ' '.join(_WHEN_LINE_RE.findall(full_text))
        then_text = ' '.join(_THEN_LINE_RE.findall(full_text))

        # Check sentiment in WHEN/scenario name AND destination in THEN
        trigger_sentiment = _classify_sentiment(when_text)
        if not trigger_sentiment:
            trigger_sentiment = _classify_sentiment(scenario_name)
        then_destination = _classify_destination(then_text)

        if trigger_sentiment and then_destination:
            covered_sentiments.add((trigger_sentiment, then_destination))
        elif trigger_sentiment:
            # Scenario mentions sentiment but has no routing destination
            covered_sentiments.add((trigger_sentiment, None))

    # Check each manifest condition for BDD coverage
    gaps: List[Dict[str, str]] = []
    for cond in manifest_conditions:
        cond_sentiment = _classify_sentiment(cond.get('condition', ''))
        cond_destination = _classify_destination(cond.get('action', ''))

        if not cond_sentiment:
            continue

        # Check if this branch is covered by any BDD scenario
        is_covered = False
        for covered_sent, covered_dest in covered_sentiments:
            if covered_sent == cond_sentiment:
                is_covered = True
                break

        if not is_covered:
            gaps.append({
                'condition': cond.get('condition', ''),
                'action': cond.get('action', ''),
                'missing_branch': (
                    f"{cond_sentiment} → {cond_destination or 'unknown'}: "
                    f"No BDD scenario covers the '{cond_sentiment}' branch"
                ),
            })

    if gaps:
        logger.warning(
            f"[BDD CONDITIONAL COMPLETENESS] {len(gaps)} conditional branches "
            f"in manifest have no matching BDD scenario: "
            f"{[g['missing_branch'] for g in gaps]}"
        )
    else:
        logger.info(
            "[BDD CONDITIONAL COMPLETENESS] All manifest conditional branches "
            "are covered by BDD scenarios ✅"
        )

    return gaps

def check_bdd_content_coverage(
    requirements: List[Dict],
    bdd_text: str,
) -> Dict[str, Any]:
    """Check BDD scenario CONTENT coverage, not just REQ-ID tag presence.

    For each requirement:
      1. Check if REQ-ID appears in bdd_text at all → if not, it's a GAP
      2. If REQ-ID appears, extract the scenario steps
      3. Check if scenario steps are boilerplate → if so, SHALLOW
      4. Check noun overlap between REQ text and BDD steps → if none, SHALLOW

    Args:
        requirements: List of requirement dicts with 'req_id' and 'text'.
        bdd_text: The full BDD scenarios markdown text to scan.

    Returns:
        Dict with coverage_pct, gaps, shallow_scenarios, details.
    """
    if not requirements:
        return {
            "coverage_pct": 1.0,
            "gaps": [],
            "shallow_scenarios": [],
            "details": [],
        }

    gaps: List[str] = []
    shallow: List[str] = []
    details: List[Dict[str, Any]] = []
    fully_covered = 0

    for req in requirements:
        req_id = req.get("req_id", "")
        req_text = req.get("text", "")

        detail = {
            "req_id": req_id,
            "status": "unknown",
            "reason": "",
        }

        if req_id not in bdd_text:
            gaps.append(req_id)
            detail["status"] = "missing"
            detail["reason"] = "REQ-ID not found in any BDD scenario"
            details.append(detail)
            continue

        step_text = _extract_bdd_steps(bdd_text, req_id)

        if _is_boilerplate_scenario(step_text):
            shallow.append(req_id)
            detail["status"] = "shallow"
            detail["reason"] = "BDD scenario steps are boilerplate/generic"
            details.append(detail)
            continue

        req_nouns = _extract_key_nouns(req_text)
        step_nouns = _extract_key_nouns(step_text)
        overlap = req_nouns & step_nouns

        if not overlap:
            shallow.append(req_id)
            detail["status"] = "shallow"
            detail["reason"] = (
                f"No noun overlap between REQ text and BDD steps. "
                f"REQ nouns: {sorted(req_nouns)[:10]}"
            )
            details.append(detail)
            continue

        fully_covered += 1
        detail["status"] = "covered"
        detail["reason"] = f"Noun overlap: {sorted(overlap)[:5]}"
        details.append(detail)

    total = len(requirements)
    coverage_pct = fully_covered / total if total > 0 else 1.0

    result = {
        "coverage_pct": coverage_pct,
        "gaps": gaps,
        "shallow_scenarios": shallow,
        "details": details,
    }

    if gaps or shallow:
        logger.warning(
            f"[BDD CONTENT] Coverage: {fully_covered}/{total} = {coverage_pct:.1%}. "
            f"Gaps: {gaps}. Shallow: {shallow}"
        )
    else:
        logger.info(
            f"[BDD CONTENT] Full coverage: {fully_covered}/{total} = {coverage_pct:.1%}"
        )

    return result

def _semantic_validate_bdd_routing(
    prompt_text: str,
    bdd_scenario_text: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Layer 2 semantic validation of BDD routing against user prompt.

    Two-layer architecture:
      Layer 1 (fast): Regex-based sentiment/destination extraction from both
          the prompt and the BDD scenario.  Compares directions.
      Layer 2 (optional): LLM-assisted semantic review when L1 is inconclusive
          or when use_llm is True and L1 detects a potential mismatch.

    Args:
        prompt_text: The original user prompt text.
        bdd_scenario_text: The BDD scenario text to validate.
        use_llm: If True, invoke LLM for Layer 2 when needed. Default True.
            Set False in tests or when no LLM is available.

    Returns:
        Dict with:
            valid: bool | None — True=correct, False=inverted, None=inconclusive
            confidence: float (0.0-1.0)
            reasoning: str — human-readable explanation
            layer: str — 'L1' or 'L2'
    """
    # ── Layer 1: Regex-based fast filter ──────────────────────────────
    prompt_sentiment = _classify_sentiment(prompt_text)
    prompt_destination = _classify_destination(prompt_text)
    bdd_sentiment = _classify_sentiment(bdd_scenario_text)
    bdd_destination = _classify_destination(bdd_scenario_text)

    # If both prompt and BDD have clear sentiment+destination → L1 decisive
    if prompt_sentiment and prompt_destination and bdd_sentiment and bdd_destination:
        if prompt_sentiment == bdd_sentiment:
            if prompt_destination == bdd_destination:
                return {
                    "valid": True,
                    "confidence": 0.9,
                    "reasoning": (
                        f"L1: Prompt says {prompt_sentiment}→{prompt_destination}, "
                        f"BDD says {bdd_sentiment}→{bdd_destination}. Match."
                    ),
                    "layer": "L1",
                }
            else:
                return {
                    "valid": False,
                    "confidence": 0.85,
                    "reasoning": (
                        f"L1: Prompt says {prompt_sentiment}→{prompt_destination}, "
                        f"BDD says {bdd_sentiment}→{bdd_destination}. INVERTED."
                    ),
                    "layer": "L1",
                }
        # Different sentiments — can't compare directions
        return {
            "valid": None,
            "confidence": 0.3,
            "reasoning": (
                f"L1: Prompt sentiment={prompt_sentiment}, BDD sentiment={bdd_sentiment}. "
                f"Different triggers — cannot compare routing."
            ),
            "layer": "L1",
        }

    # L1 inconclusive — missing sentiment or destination from one side
    l1_result = {
        "valid": None,
        "confidence": 0.0,
        "reasoning": (
            f"L1 inconclusive: prompt_sentiment={prompt_sentiment}, "
            f"prompt_dest={prompt_destination}, bdd_sentiment={bdd_sentiment}, "
            f"bdd_dest={bdd_destination}"
        ),
        "layer": "L1",
    }

    if not use_llm:
        return l1_result

    # ── Layer 2: LLM semantic review ─────────────────────────────────
    try:
        import asyncio
        from python.helpers.agent_models import call_utility_model_impl
        import python.models as models

        model = models.get_model("utility", "")
        if not model:
            logger.warning("[BDD L2] No utility model — falling back to L1")
            return l1_result

        system_prompt = (
            "You are a BDD routing validator. Given a user prompt and a BDD scenario, "
            "determine if the BDD scenario's routing/direction is CONSISTENT with "
            "the user prompt's intent.\n\n"
            "Respond with ONLY a JSON object (no markdown):\n"
            '{"valid": true/false, "confidence": 0.0-1.0, "reasoning": "explanation"}'
        )
        user_message = (
            f"User prompt:\n{prompt_text[:2000]}\n\n"
            f"BDD scenario:\n{bdd_scenario_text[:2000]}\n\n"
            f"Does the BDD scenario's routing direction match the user prompt's intent?"
        )

        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                response, _, _, _ = pool.submit(
                    asyncio.run,
                    model.unified_call(
                        system_message=system_prompt,
                        user_message=user_message,
                        timeout=30,
                        agix_retry_attempts=2,
                    ),
                ).result(timeout=35)
        else:
            response, _, _, _ = loop.run_until_complete(
                model.unified_call(
                    system_message=system_prompt,
                    user_message=user_message,
                    timeout=30,
                    agix_retry_attempts=2,
                )
            )

        import json as _json
        result = _json.loads(response.strip())
        return {
            "valid": bool(result.get("valid", None)),
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": str(result.get("reasoning", "parsed from LLM")),
            "layer": "L2",
        }

    except Exception as e:
        logger.warning(f"[BDD L2] LLM call failed: {e} — falling back to L1")
        return l1_result


# ---------------------------------------------------------------------------
# F-5: Content Fidelity Exact-Match Checker (Defense-in-Depth)
# ---------------------------------------------------------------------------

# Fields whose values are prose / internal notes — NOT expected verbatim in code.
_NON_CRITICAL_KEYS = frozenset({
    "description", "notes", "summary", "comment", "comments",
    "internal_notes", "dev_notes",
})

# Regex patterns that identify "critical" literal values worth checking
_PRICE_RE = re.compile(r'\$[\d,]+(?:/\w+)?')
_URL_RE = re.compile(r'https?://[^\s"\'<>]+')
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
# Multi-word capitalized phrases (brand/business names, plan names, taglines)
_NAME_RE = re.compile(r'(?:[A-Z][a-z]+(?:\s+[A-Za-z][a-z]*)+)')

# Source file extensions to scan
_SOURCE_EXTENSIONS = frozenset({
    '.ts', '.tsx', '.js', '.jsx', '.vue', '.svelte',
    '.py', '.rb', '.go', '.rs', '.java', '.kt',
    '.html', '.css', '.scss', '.json', '.yaml', '.yml',
    '.md', '.mdx', '.astro', '.php',
})


def _extract_critical_strings(
    obj: Any,
    out: List[str],
    *,
    _parent_key: str = "",
) -> None:
    """Recursively extract string values, skipping non-critical keys."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key.lower() in _NON_CRITICAL_KEYS:
                continue
            _extract_critical_strings(val, out, _parent_key=key)
    elif isinstance(obj, list):
        for item in obj:
            _extract_critical_strings(item, out, _parent_key=_parent_key)
    elif isinstance(obj, str):
        if obj.strip():
            out.append(obj)


def _extract_literals(raw_strings: List[str]) -> List[str]:
    """Distill raw manifest strings into discrete critical literals.

    From each string, extracts:
      - Dollar prices  ($200/mo, $1,500, etc.)
      - URLs           (https://example.com)
      - Emails         (help@example.com)
      - Multi-word capitalized names  ("Acme Widgets Co")

    Remaining short strings (≤ 80 chars) that ARE the entire value are also
    kept as-is — they're likely plan names, taglines, CTAs etc.

    Returns a de-duplicated, sorted list.
    """
    literals: set[str] = set()

    for s in raw_strings:
        found_something = False

        for match in _PRICE_RE.findall(s):
            literals.add(match)
            found_something = True

        for match in _URL_RE.findall(s):
            literals.add(match)
            found_something = True

        for match in _EMAIL_RE.findall(s):
            literals.add(match)
            found_something = True

        for match in _NAME_RE.findall(s):
            literals.add(match)
            found_something = True

        # If the entire string is short and we didn't extract sub-parts,
        # keep it verbatim (plan names, taglines, CTAs, etc.)
        if not found_something and len(s) <= 80:
            literals.add(s)

    return sorted(literals)


def _read_all_source(project_dir: str) -> str:
    """Concatenate all source files under ``src/`` into one string."""
    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        return ""

    # OVL-3: Use centralized scanner instead of inline os.walk
    file_contents = _ss_read_project_files(src_dir, extensions=_SOURCE_EXTENSIONS)
    return "\n".join(file_contents.values())


def check_manifest_code_fidelity(project_dir: str) -> Dict[str, Any]:
    """Verify generated source code contains exact manifest literal values.

    This is a **defense-in-depth** checker that complements upstream
    architecture improvements.  It loads ``docs/content-manifest.json``,
    extracts every critical literal (prices, URLs, emails, names), and checks
    that each one appears verbatim in at least one source file under ``src/``.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        Dict with:
            matched:        List[str] — literals found in source
            missing:        List[str] — literals NOT found in source
            fidelity_score: float 0.0–1.0 — fraction of literals matched
    """
    manifest_path = os.path.join(project_dir, "docs", "content-manifest.json")

    # No manifest → nothing to check, considered fully faithful
    if not os.path.isfile(manifest_path):
        return {"matched": [], "missing": [], "fidelity_score": 1.0}

    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"[FIDELITY] Cannot read manifest: {exc}")
        return {"matched": [], "missing": [], "fidelity_score": 1.0}

    # 1. Extract raw strings (skipping non-critical keys)
    raw_strings: List[str] = []
    _extract_critical_strings(manifest, raw_strings)

    # 2. Distill into discrete critical literals
    literals = _extract_literals(raw_strings)

    if not literals:
        return {"matched": [], "missing": [], "fidelity_score": 1.0}

    # 3. Read all source content
    all_source = _read_all_source(project_dir)

    # 4. Check each literal
    matched: List[str] = []
    missing: List[str] = []
    for lit in literals:
        if lit in all_source:
            matched.append(lit)
        else:
            missing.append(lit)

    total = len(matched) + len(missing)
    score = len(matched) / total if total > 0 else 1.0

    if missing:
        logger.warning(
            f"[FIDELITY] {len(missing)}/{total} manifest literals missing from "
            f"source: {missing[:10]}{'…' if len(missing) > 10 else ''}"
        )
    else:
        logger.info(
            f"[FIDELITY] All {total} manifest literals matched in source ✅"
        )

    return {
        "matched": matched,
        "missing": missing,
        "fidelity_score": round(score, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════
# F-3 (SS-3): BDD Boilerplate Ratio Check — Defense-in-Depth
# Complements WP-2 architect mandate for domain-specific BDD.
# ═══════════════════════════════════════════════════════════════════════════

# Patterns that indicate generic/template BDD scenarios
_GENERIC_THEN_PATTERNS = re.compile(
    r"(?:the\s+(?:code|feature|requirement|implementation)\s+"
    r"(?:must|should|works?|is)\s+(?:match|correct|implement|function))|"
    r"(?:source\s+file\s+is\s+inspected)|"
    r"(?:code\s+must\s+match\s+the\s+specification)|"
    r"(?:requirement\s+.*?is\s+implemented)",
    re.IGNORECASE,
)

# Scenario header pattern
_SCENARIO_HEADER_RE = re.compile(
    r"^#+\s*(?:Scenario|Feature)\s*[:.]?\s*",
    re.IGNORECASE | re.MULTILINE,
)


def check_bdd_boilerplate_ratio(
    bdd_text: str,
    max_boilerplate_ratio: float = 0.5,
) -> dict:
    """Compute the boilerplate ratio of BDD scenarios.

    A scenario is "boilerplate" if its Then/And clauses only contain
    generic template patterns (e.g., "the code must match the specification")
    rather than domain-specific assertions (e.g., "the price is $200/mo").

    This is defense-in-depth for the WP-2 architect mandate that requires
    domain-specific BDD scenarios. When the architect produces proper BDD,
    this check passes easily. When the architect is bypassed or fails,
    this gate catches the generic templates.

    Args:
        bdd_text: Full BDD scenarios markdown text.
        max_boilerplate_ratio: Maximum acceptable ratio (0.0-1.0).
            Default 0.5 means >50% boilerplate fails.

    Returns:
        Dict with boilerplate_ratio (0.0-1.0), quality_pass (bool),
        total_scenarios, boilerplate_count, domain_specific_count.
    """
    if not bdd_text or not bdd_text.strip():
        return {
            "boilerplate_ratio": 1.0,
            "quality_pass": False,
            "total_scenarios": 0,
            "boilerplate_count": 0,
            "domain_specific_count": 0,
        }

    # Split into scenarios
    scenarios = _SCENARIO_HEADER_RE.split(bdd_text)
    # Filter out empty/header-only blocks
    scenarios = [s.strip() for s in scenarios if s.strip() and len(s.strip()) > 20]

    if not scenarios:
        return {
            "boilerplate_ratio": 1.0,
            "quality_pass": False,
            "total_scenarios": 0,
            "boilerplate_count": 0,
            "domain_specific_count": 0,
        }

    boilerplate_count = 0
    for scenario in scenarios:
        # Use BOTH the existing helper AND our broader pattern check
        if _is_boilerplate_scenario(scenario) or _GENERIC_THEN_PATTERNS.search(scenario):
            boilerplate_count += 1

    total = len(scenarios)
    ratio = boilerplate_count / total if total > 0 else 1.0

    return {
        "boilerplate_ratio": ratio,
        "quality_pass": ratio <= max_boilerplate_ratio,
        "total_scenarios": total,
        "boilerplate_count": boilerplate_count,
        "domain_specific_count": total - boilerplate_count,
    }
