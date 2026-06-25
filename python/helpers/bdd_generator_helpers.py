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



def _classify_feature_subtype(text: str) -> str:
    """Sub-classify a feature requirement using universal keyword patterns.

    Returns the most specific sub-type that matches the text, or 'feature'
    as a fallback if no sub-type keywords are found.

    Sub-categories (universal, work for any project):
      - feature_api: API, endpoint, fetch, search, engine, query, scrape, scan, webhook
      - feature_workflow: sequence, automation, queue, schedule, drip, pipeline, cron, trigger, batch
      - feature_ui: page, dashboard, table, display, view, form, button, interface, component, render
      - feature_data: score, calculate, algorithm, analytics, metrics, aggregate, rank, rate

    Args:
        text: The feature requirement text.

    Returns:
        Sub-type string (e.g., 'feature_api') or 'feature' fallback.
    """
    for subtype, pattern in _FEATURE_SUBTYPE_PATTERNS.items():
        if pattern.search(text):
            return subtype
    return "feature"

def _classify_compliance_subtype(text: str) -> str:
    """Sub-classify a compliance requirement using universal keyword patterns.

    WB-4 fix: Compliance was an undifferentiated bucket. CAN-SPAM, GDPR,
    ADA all got identical generic THEN clauses. This function sub-classifies
    compliance requirements to get regulation-specific acceptance criteria.

    Sub-categories (universal, work for any project):
      - compliance_email: CAN-SPAM, email compliance, unsubscribe, physical address
      - compliance_privacy: GDPR, CCPA, privacy policy, data protection, cookie consent
      - compliance_accessibility: ADA, WCAG, accessibility, screen reader, alt text

    Args:
        text: The compliance requirement text.

    Returns:
        Sub-type string (e.g., 'compliance_email') or 'compliance' fallback.
    """
    for subtype, pattern in _COMPLIANCE_SUBTYPE_PATTERNS.items():
        if pattern.search(text):
            return subtype
    return "compliance"

def _resolve_category(category: str, text: str) -> str:
    """Resolve a raw ledger category to its most specific sub-type.

    For 'feature' requirements, sub-classifies into feature_api, feature_workflow,
    feature_ui, or feature_data using keyword patterns. For 'compliance', sub-classifies
    into compliance_email, compliance_privacy, or compliance_accessibility.
    All other categories are returned unchanged.

    This bridges the gap where _classify_feature_subtype() existed but was never
    called in BDD generation paths (F-5 / SS-10 dead-code fix).

    Args:
        category: Raw category from requirements ledger (e.g., 'feature', 'page').
        text: The requirement text for keyword-based sub-classification.

    Returns:
        Resolved category string (e.g., 'feature_api', 'compliance_email', or original).
    """
    if category == "feature":
        return _classify_feature_subtype(text)
    if category == "compliance":
        return _classify_compliance_subtype(text)
    return category

def _classify_sentiment(text: str) -> Optional[str]:
    """Classify text as 'happy', 'unhappy', or None."""
    has_happy = bool(_HAPPY_TRIGGERS.search(text))
    has_unhappy = bool(_UNHAPPY_TRIGGERS.search(text))
    if has_happy and not has_unhappy:
        return 'happy'
    if has_unhappy and not has_happy:
        return 'unhappy'
    return None

def _classify_destination(text: str) -> Optional[str]:
    """Classify THEN text as 'public', 'private', or None."""
    has_public = bool(_PUBLIC_DESTINATIONS.search(text))
    has_private = bool(_PRIVATE_DESTINATIONS.search(text))
    if has_public and not has_private:
        return 'public'
    if has_private and not has_public:
        return 'private'
    return None

def _extract_manifest_conditions(manifest: dict) -> List[Dict[str, str]]:
    """Extract conditional routing patterns from content_manifest.json.

    Looks for:
    1. Explicit condition dicts: {"if": "happy", "then": "redirect to Google"}
    2. Nested "conditions" arrays in any manifest value
    3. String values with arrow patterns: "Happy → Google"

    Returns:
        List of {condition, action} dicts.
    """
    conditions = []

    def _walk(obj, path=""):
        if isinstance(obj, dict):
            if_val = obj.get("if") or obj.get("condition") or obj.get("when")
            then_val = obj.get("then") or obj.get("action") or obj.get("redirect") or obj.get("route")
            if if_val and then_val:
                conditions.append({"condition": str(if_val), "action": str(then_val)})

            for key, value in obj.items():
                _walk(value, f"{path}.{key}")

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

        elif isinstance(obj, str):
            arrow_matches = re.findall(
                r'(\b(?:happy|unhappy|positive|negative|5.?star|1.?star|good|bad|satisfied|unsatisfied)\b[^→\->]*)'
                r'(?:→|->|\bto\b)\s*'
                r'(.+)',
                obj, re.IGNORECASE
            )
            for trigger, destination in arrow_matches:
                conditions.append({"condition": trigger.strip(), "action": destination.strip()})

    _walk(manifest)
    return conditions

def _extract_key_nouns(text: str, min_length: int = 3) -> set:
    """Extract key nouns from text for overlap checking.

    Args:
        text: Input text to extract nouns from.
        min_length: Minimum word length to include (default 3).

    Returns:
        Set of lowercased key words.
    """
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return {
        w for w in words
        if len(w) >= min_length and w not in _STOP_WORDS
    }

def _extract_bdd_steps(bdd_text: str, req_id: str) -> str:
    """Extract BDD scenario step text for a specific REQ-ID.

    Finds the scenario tagged with the given REQ-ID and extracts
    the Given/When/Then/And step lines.

    Args:
        bdd_text: Full BDD scenarios text.
        req_id: The REQ-ID to find (e.g., "REQ-001").

    Returns:
        Concatenated step text for the scenario, or empty string if not found.
    """
    lines = bdd_text.split("\n")
    in_scenario = False
    step_lines = []

    for line in lines:
        stripped = line.strip()

        if req_id in stripped and (
            stripped.startswith("Scenario:") or
            stripped.startswith("Scenario Outline:")
        ):
            in_scenario = True
            continue

        if in_scenario:
            if (stripped.startswith("Scenario:") or
                    stripped.startswith("Feature:") or
                    stripped.startswith("##")):
                break

            if stripped and any(
                stripped.startswith(kw)
                for kw in ("Given", "When", "Then", "And", "But")
            ):
                step_lines.append(stripped)

    return " ".join(step_lines)

def _is_boilerplate_scenario(step_text: str) -> bool:
    """Check if BDD scenario steps are generic boilerplate.

    A scenario is boilerplate if its Then clauses only contain
    generic assertions like "the feature works correctly".

    Args:
        step_text: Concatenated step text from the scenario.

    Returns:
        True if the scenario appears to be boilerplate.
    """
    if not step_text.strip():
        return True

    then_parts = []
    for part in re.split(r"\b(?:Then|And)\b", step_text):
        part = part.strip()
        if part:
            then_parts.append(part)

    if not then_parts:
        return True

    all_boilerplate = all(
        _BOILERPLATE_PATTERNS.search(part)
        for part in then_parts
        if not part.startswith("Given") and not part.startswith("When")
    )

    return all_boilerplate
