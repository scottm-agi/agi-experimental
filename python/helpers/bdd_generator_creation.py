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

logger = logging.getLogger("agix.bdd_generator")

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
from python.helpers.tdd_generator_constants import _SDK_NAMES


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

def generate_bdd_skeleton(project_dir: str) -> Optional[str]:
    """Generate BDD scenario skeleton from requirements ledger.

    Only for web/fullstack projects. Detection: package.json exists
    AND at least one requirement has a BDD-applicable category.

    Args:
        project_dir: Path to the project directory.

    Returns:
        BDD skeleton markdown string, or None if not applicable.
    """
    # Import shared utilities from sibling modules
    from python.helpers.skeleton_generator import (
        _load_ledger, _is_web_project, _is_untestable_requirement,
    )
    from python.helpers.manifest_parser import _load_manifest_dict

    if not _is_web_project(project_dir):
        return None

    ledger = _load_ledger(project_dir)
    if not ledger:
        return None

    requirements = ledger.get("requirements", [])
    bdd_reqs = [
        r for r in requirements
        if r.get("category", "feature") in _BDD_CATEGORIES
        and not _is_untestable_requirement(r)
    ]

    if not bdd_reqs:
        bdd_reqs = requirements

    lines = [
        "# BDD Scenarios Skeleton",
        "",
        "Auto-generated from requirements_ledger.json.",
        "Architect: enrich each scenario with GIVEN/WHEN/THEN details.",
        "",
    ]

    # RCA-461 R-3: Load manifest dict BEFORE the loop for value injection
    from python.helpers.manifest_parser import _match_literals_by_category
    manifest_dict = _load_manifest_dict(project_dir)

    # RCA-464: Categories that need content-fidelity exact-text assertions
    _CONTENT_FIDELITY_CATEGORIES = {"copy", "content", "branding"}

    for req in bdd_reqs:
        req_id = req.get("id", "REQ-???")
        text = req.get("text", "")
        category = req.get("category", "feature")
        # F-5 fix: resolve feature/compliance sub-types for richer THEN clauses
        resolved_cat = _resolve_category(category, text)
        short_text = text[:80] + ("..." if len(text) > 80 else "")

        then_clause = _CATEGORY_THEN_CLAUSES.get(resolved_cat)

        # RCA-461 R-3: Get manifest literals for this requirement's category
        manifest_literals = []
        if manifest_dict:
            manifest_literals = _match_literals_by_category(
                category, manifest_dict, req_text=text
            )

        if then_clause:
            lines.extend([
                f"## Feature: {short_text} [{req_id}]",
                "",
                f"  Scenario: Verify {category} requirement [{req_id}]",
                f"    Given the {category} requirement [{req_id}] is implemented",
                f"    When the source file is inspected",
                then_clause,
            ])
            # RCA-464: Content-fidelity — inject exact text for copy/content/branding
            if category in _CONTENT_FIDELITY_CATEGORIES and text:
                lines.append(f'    And the source code contains exactly "{text}"')
            # RCA-461 R-3: Append manifest-value assertions after THEN clause
            for lit in manifest_literals[:5]:
                lines.append(f'    And the source code contains "{lit}"')
            lines.append("")
        else:
            lines.extend([
                f"## Feature: {short_text} [{req_id}]",
                "",
                f"  Scenario: Verify {category} requirement [{req_id}]",
                f"    Given [FILL — architect enriches]",
                f"    When [FILL — architect enriches]",
                f"    Then [FILL — architect enriches]",
            ])
            # RCA-464: Content-fidelity — inject exact text for copy/content/branding
            if category in _CONTENT_FIDELITY_CATEGORIES and text:
                lines.append(f'    And the source code contains exactly "{text}"')
            # RCA-461 R-3: Even for unfilled scenarios, add manifest assertions
            for lit in manifest_literals[:5]:
                lines.append(f'    And the source code contains "{lit}"')
            lines.append("")

    # F-2 (ITR-25): Integration-aware BDD
    manifest_dict = _load_manifest_dict(project_dir)
    manifest_integrations = manifest_dict.get("integrations", [])
    if manifest_integrations:
        lines.extend([
            "",
            "## Integration SDK Requirements (from manifest)",
            "",
        ])
        for integ in manifest_integrations:
            integ_name = integ if isinstance(integ, str) else integ.get("name", "")
            integ_type = integ.get("type", "service") if isinstance(integ, dict) else ""
            if integ_name:
                # Look up the actual npm package name from _SDK_NAMES
                pkg_name = _SDK_NAMES.get(integ_name.lower(), integ_name.lower())
                lines.extend([
                    f"  Scenario: Verify {integ_name} SDK integration",
                    f"    Given the manifest declares {integ_name} as an integration",
                    f"    When the source code is inspected",
                    f"    Then at least one source file should import from the '{pkg_name}' package",
                    f"    And the {pkg_name} package should be listed in package.json dependencies",
                    f"    And the {integ_name} API key is read from environment variables",
                    f"    And the {integ_name} SDK is called with real parameters (not mocked)",
                    "",
                ])

    content = "\n".join(lines)

    # Persist to docs/
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    bdd_path = os.path.join(docs_dir, "bdd-scenarios.md")

    # ITR-20 F-1: Read-before-write — never overwrite enriched BDD.
    validation_path = os.path.join(docs_dir, ".bdd_validation.json")
    if os.path.isfile(validation_path):
        logger.info(
            "[BDD SKELETON] Skipping — .bdd_validation.json exists "
            "(architect already enriched via save_bdd_scenarios)"
        )
        if os.path.isfile(bdd_path):
            with open(bdd_path, "r") as f:
                return f.read()
        return None

    if os.path.isfile(bdd_path):
        with open(bdd_path, "r") as f:
            existing = f.read()
        SKELETON_MARKERS = [
            "Auto-generated from requirements_ledger.json",
            "[FILL — architect enriches]",
        ]
        is_skeleton = any(marker in existing for marker in SKELETON_MARKERS)
        if not is_skeleton and len(existing.strip()) > 100:
            logger.info(
                "[BDD SKELETON] Skipping — existing content is enriched "
                "(no skeleton markers detected)"
            )
            return existing

    with open(bdd_path, "w") as f:
        f.write(content)

    logger.info(
        f"[BDD SKELETON] Generated BDD skeleton for {len(bdd_reqs)} requirements"
    )
    return content

def generate_feature_files(project_dir: str) -> List[str]:
    """Generate executable Gherkin .feature files from requirements ledger.

    Groups BDD-applicable requirements by category, producing one .feature
    file per category in ``specs/features/<category>.feature``.

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of absolute paths to created/existing .feature files.
    """
    from python.helpers.skeleton_generator import (
        _load_ledger, _is_web_project, _is_untestable_requirement,
    )

    if not _is_web_project(project_dir):
        return []

    ledger = _load_ledger(project_dir)
    if not ledger:
        return []

    requirements = ledger.get("requirements", [])

    bdd_reqs = [
        r for r in requirements
        if r.get("category", "feature") in _BDD_CATEGORIES
        and not _is_untestable_requirement(r)
    ]

    if not bdd_reqs:
        return []

    category_groups: Dict[str, List[Dict]] = {}
    for req in bdd_reqs:
        cat = req.get("category", "feature")
        category_groups.setdefault(cat, []).append(req)

    features_dir = os.path.join(project_dir, "specs", "features")
    os.makedirs(features_dir, exist_ok=True)

    created_files: List[str] = []
    _SKELETON_MARKER = "# Auto-generated by AGIX skeleton_generator"

    for category, reqs in sorted(category_groups.items()):
        feature_path = os.path.join(features_dir, f"{category}.feature")

        if os.path.isfile(feature_path):
            with open(feature_path, "r") as f:
                existing = f.read()
            if (
                _SKELETON_MARKER not in existing
                and len(existing.strip()) > 50
            ):
                created_files.append(feature_path)
                continue

        lines: List[str] = [
            f"Feature: {category.replace('_', ' ').title()} requirements",
            f"  {_SKELETON_MARKER}",
            "",
        ]

        for req in reqs:
            req_id = req.get("id", "REQ-???")
            text = req.get("text", "")
            short_text = text[:80] + ("..." if len(text) > 80 else "")
            cat = req.get("category", "feature")
            # F-5 fix: resolve feature/compliance sub-types
            resolved_cat = _resolve_category(cat, text)

            lines.append(f"  @{req_id}")

            scenario_name = f"Verify {resolved_cat} requirement {req_id} - {short_text}"
            lines.append(f"  Scenario: {scenario_name}")

            then_clause = _CATEGORY_THEN_CLAUSES.get(resolved_cat)

            if then_clause:
                lines.append(
                    f"    Given the {cat} requirement [{req_id}] is implemented"
                )
                lines.append("    When the source file is inspected")
                then_lines = [
                    tl.strip()
                    for tl in then_clause.strip().split("\n")
                    if tl.strip()
                ]
                for tl in then_lines:
                    lines.append(f"    {tl}")
                # RCA-464: Content-fidelity — inject exact text for copy/content/branding
                if cat in {"copy", "content", "branding"} and text:
                    lines.append(f'    And the source code contains exactly "{text}"')
            else:
                lines.append(
                    f"    Given the {cat} requirement [{req_id}] is implemented"
                )
                lines.append("    When the implementation is verified")
                lines.append(
                    f"    Then the {cat} requirement [{req_id}] meets acceptance criteria"
                )
                # RCA-464: Content-fidelity — inject exact text for copy/content/branding
                if cat in {"copy", "content", "branding"} and text:
                    lines.append(f'    And the source code contains exactly "{text}"')

            lines.append("")

        content = "\n".join(lines)
        with open(feature_path, "w") as f:
            f.write(content)

        created_files.append(feature_path)
        logger.info(
            f"[FEATURE FILES] Generated {feature_path} with "
            f"{len(reqs)} scenarios"
        )

    logger.info(
        f"[FEATURE FILES] Generated {len(created_files)} .feature files "
        f"in specs/features/"
    )
    return created_files

def generate_scenario_manifest(project_dir: str) -> Optional[Dict]:
    """Generate scenario-manifest.yaml mapping REQ-IDs to test commands.

    Args:
        project_dir: Path to the project directory.

    Returns:
        Parsed manifest dict, or None if not a web project.
    """
    import yaml as _yaml

    from python.helpers.skeleton_generator import (
        _load_ledger, _is_web_project, _is_untestable_requirement,
        _CATEGORY_TEST_TYPE,
    )

    if not _is_web_project(project_dir):
        return None

    ledger = _load_ledger(project_dir)
    if not ledger:
        return {"scenarios": []}

    requirements = ledger.get("requirements", [])

    bdd_reqs = [
        r for r in requirements
        if r.get("category", "feature") in _BDD_CATEGORIES
        and not _is_untestable_requirement(r)
    ]

    scenarios: List[Dict] = []
    for req in bdd_reqs:
        req_id = req.get("id", "REQ-???")
        text = req.get("text", "")
        category = req.get("category", "feature")
        # F-5 fix: resolve feature/compliance sub-types
        resolved_cat = _resolve_category(category, text)
        short_text = text[:80] + ("..." if len(text) > 80 else "")

        test_type = _CATEGORY_TEST_TYPE.get(resolved_cat, "unit")
        feature_path = f"specs/features/{resolved_cat}.feature"
        scenario_name = (
            f"Verify {category} requirement {req_id} - {short_text}"
        )

        acceptance_targets: Dict[str, Dict[str, str]] = {}

        if test_type in ("unit", "literal", "config"):
            acceptance_targets["unit"] = {
                "command": f"pnpm test:unit --grep \"{req_id}\""
            }

        if test_type == "integration":
            acceptance_targets["unit"] = {
                "command": f"pnpm test:unit --grep \"{req_id}\""
            }
            acceptance_targets["integration"] = {
                "command": f"pnpm test:integration --grep \"{req_id}\""
            }

        acceptance_targets["e2e"] = {
            "command": f"pnpm test:bdd --grep \"{req_id}\""
        }

        scenarios.append({
            "id": req_id,
            "title": short_text,
            "feature": feature_path,
            "scenario": scenario_name,
            "acceptance_targets": acceptance_targets,
        })

    manifest = {"scenarios": scenarios}

    specs_dir = os.path.join(project_dir, "specs")
    os.makedirs(specs_dir, exist_ok=True)
    manifest_path = os.path.join(specs_dir, "scenario-manifest.yaml")

    with open(manifest_path, "w") as f:
        _yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    logger.info(
        f"[SCENARIO MANIFEST] Generated manifest with "
        f"{len(scenarios)} scenario entries"
    )
    return manifest

def assemble_bdd_from_structured(scenarios: List[Dict[str, Any]]) -> str:
    """Assemble Gherkin-formatted BDD markdown from structured LLM input.

    Groups scenarios by Feature name and formats them with REQ-IDs
    embedded on Scenario lines. This is the deterministic formatter —
    the LLM provides the intelligence (what to test), the tool provides
    the structure (format, REQ-ID placement).

    Output follows Cucumber-compatible Gherkin format (Queen's Gherkin):
    - One Feature per output block
    - 2-space indentation for body elements
    - User story under Feature (As a / I want / So that)
    - Background for shared preconditions
    - REQ-IDs on Scenario lines as tags: [REQ-xxx]
    - Blank line between scenarios

    Args:
        scenarios: List of dicts with keys:
            req_ids: List[str] — REQ-IDs this scenario covers (REQUIRED)
            feature: str — Feature name
            scenario: str — Scenario name
            given: str — Given clause
            when: str — When clause
            then: List[str] — Then clauses (first = "Then", rest = "And")
            user_story: Optional[Dict] with as_a, i_want, so_that
            background: Optional[str] — shared Given for all scenarios in feature

    Returns:
        Cucumber-compatible Gherkin string with REQ-IDs.
    """
    from collections import OrderedDict

    # Group scenarios by feature
    features: OrderedDict = OrderedDict()
    for s in scenarios:
        feature_name = s.get("feature", "Uncategorized")
        if feature_name not in features:
            features[feature_name] = {
                "scenarios": [],
                "user_story": None,
                "background": None,
            }
        features[feature_name]["scenarios"].append(s)
        if s.get("user_story") and not features[feature_name]["user_story"]:
            features[feature_name]["user_story"] = s["user_story"]
        if s.get("background") and not features[feature_name]["background"]:
            features[feature_name]["background"] = s["background"]

    lines = []

    for feature_name, feature_data in features.items():
        feature_scenarios = feature_data["scenarios"]
        user_story = feature_data["user_story"]
        background = feature_data["background"]

        lines.append(f"Feature: {feature_name}")

        if user_story and isinstance(user_story, dict):
            as_a = user_story.get("as_a", "")
            i_want = user_story.get("i_want", "")
            so_that = user_story.get("so_that", "")
            if as_a:
                lines.append(f"  As a {as_a}")
            if i_want:
                lines.append(f"  I want {i_want}")
            if so_that:
                lines.append(f"  So that {so_that}")

        lines.append("")

        if background:
            lines.append("  Background:")
            lines.append(f"    Given {background}")
            lines.append("")

        for s in feature_scenarios:
            scenario_name = s.get("scenario", "Unnamed")
            scenario_req_tag = " ".join(f"[{rid}]" for rid in s.get("req_ids", []))
            lines.append(f"  Scenario: {scenario_name} {scenario_req_tag}".rstrip())

            given = s.get("given", "")
            when = s.get("when", "")
            then_clauses = s.get("then", [])

            if given:
                lines.append(f"    Given {given}")
            if when:
                lines.append(f"    When {when}")
            for i, t in enumerate(then_clauses):
                keyword = "Then" if i == 0 else "And"
                lines.append(f"    {keyword} {t}")
            lines.append("")

    return "\n".join(lines)

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


# ─── RCA-470: Auto-Inject Missing Delivery Standard BDD Scenarios ────────

# Categories whose REQs are auto-injected by the skeleton generator
# but often missed by LLM-driven BDD enrichment.
_DELIVERY_STANDARD_CATEGORIES = {"scaffold_cleanup", "delivery", "infra"}


def inject_missing_delivery_bdd(
    existing_bdd_text: str,
    skeleton_reqs: List[Dict[str, Any]],
) -> str:
    """Auto-inject BDD scenarios for delivery standard REQs missing from BDD text.

    RCA-470 FIX: REQ-SCAFFOLD-001 and other delivery standards are injected
    into test-skeleton.json by the framework, but the LLM-driven BDD enrichment
    (Phase 2.7) reliably misses them. At 100% BDD coverage threshold, this
    blocks the gate. This function deterministically generates BDD scenarios
    from templates for any missing delivery/scaffold/infra REQs.

    The user's philosophy: "we are relying on tests to prove no boilerplate —
    our TDD should already have 0 boilerplate content & test that's true of
    our code." This function ensures those tests always exist.

    Args:
        existing_bdd_text: Current BDD scenarios markdown text.
        skeleton_reqs: List of skeleton requirement dicts from test-skeleton.json.

    Returns:
        Updated BDD text with missing delivery standard scenarios appended.
    """
    from python.helpers.bdd_generator_constants import _CATEGORY_THEN_CLAUSES

    # Find which delivery REQs are missing from the BDD text
    missing = []
    for req in skeleton_reqs:
        if not req.get("bdd_needed", False):
            continue
        category = req.get("category", "")
        if category not in _DELIVERY_STANDARD_CATEGORIES:
            continue
        req_id = req.get("req_id", "")
        if not req_id:
            continue
        if req_id in existing_bdd_text:
            continue  # Already covered
        missing.append(req)

    if not missing:
        return existing_bdd_text

    # Generate scenarios from templates
    injected_lines = [
        "",
        "",
        "# ── Auto-Injected Delivery Standard Scenarios (RCA-470) ──",
        "",
    ]
    for req in missing:
        req_id = req.get("req_id", "REQ-???")
        text = req.get("text", "")
        category = req.get("category", "feature")
        short_text = text[:80] + ("..." if len(text) > 80 else "")

        then_clause = _CATEGORY_THEN_CLAUSES.get(category, "")
        if then_clause:
            injected_lines.extend([
                f"Feature: {short_text} [{req_id}]",
                "",
                f"  Scenario: Verify {category} standard [{req_id}]",
                f"    Given the project build is complete",
                f"    When the {category} requirement [{req_id}] is verified",
                then_clause,
                "",
            ])
        else:
            injected_lines.extend([
                f"Feature: {short_text} [{req_id}]",
                "",
                f"  Scenario: Verify {category} standard [{req_id}]",
                f"    Given the project build is complete",
                f"    When the {category} requirement [{req_id}] is verified",
                f"    Then the requirement is satisfied",
                "",
            ])

    logger.info(
        f"[BDD AUTO-INJECT] Injected {len(missing)} delivery standard scenarios: "
        f"{[r['req_id'] for r in missing]}"
    )

    return existing_bdd_text + "\n".join(injected_lines)
