"""
BDD Scenario Validator — BDD Frontend Quality Pipeline

Parses Gherkin-format `bdd-scenarios.md` from a project directory and validates
mechanical acceptance criteria against source code via static analysis.

Root cause: Frontend pages were skeletal because no structured acceptance
criteria existed to verify against. This validator provides mechanical,
LLM-free validation of BDD scenarios.

Usage:
    from python.helpers.validators.bdd_scenarios import (
        parse_bdd_scenarios,
        validate_bdd_scenarios,
    )

    # Parse scenarios from project docs/bdd-scenarios.md
    features = parse_bdd_scenarios("/path/to/project")

    # Validate source code against scenarios
    result = validate_bdd_scenarios("/path/to/project")
    # Returns: {"passed": bool, "total_scenarios": int, "passed_count": int, "failures": [...]}
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agix.validators.bdd_scenarios")

# ─── Data Types ───────────────────────────────────────────────────────


@dataclass
class BddClause:
    """A single Then/And clause from a BDD scenario."""
    text: str
    element_tag: Optional[str] = None
    min_count: int = 1

    def __post_init__(self):
        """Extract element tag and count from clause text."""
        self._parse_element_and_count()

    def _parse_element_and_count(self):
        # Pattern: ≥N <tag> or >= N <tag>
        count_tag = re.search(r"[≥>=]+\s*(\d+)\s+<(\w+)>", self.text)
        if count_tag:
            self.min_count = int(count_tag.group(1))
            self.element_tag = count_tag.group(2)
            return

        # Pattern: just <tag> (no count → default 1)
        tag_only = re.search(r"<(\w+)>", self.text)
        if tag_only:
            self.element_tag = tag_only.group(1)
            self.min_count = 1


@dataclass
class BddScenario:
    """A Scenario block with a name, given context, and clauses."""
    name: str
    given: Optional[str] = None
    clauses: List[BddClause] = field(default_factory=list)
    blocking: bool = False  # True when tagged [COMPLIANCE: BLOCKING]


@dataclass
class BddFeature:
    """A Feature block containing multiple scenarios."""
    name: str
    scenarios: List[BddScenario] = field(default_factory=list)


# ─── Source Extensions (canonical import) ─────────────────────────────

from python.helpers.source_scanner import (
    get_combined_source_text,
    SOURCE_EXTENSIONS,
    EXCLUDE_DIRS,
)


# ─── Parser ───────────────────────────────────────────────────────────


def parse_bdd_scenarios(project_dir: str) -> Optional[List[BddFeature]]:
    """Parse bdd-scenarios.md from project docs/ directory.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        None if bdd-scenarios.md doesn't exist.
        List[BddFeature] if file exists (may be empty for malformed files).
    """
    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.isfile(bdd_path):
        return None

    try:
        with open(bdd_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, OSError) as e:
        logger.warning(f"[BDD] Failed to read {bdd_path}: {e}")
        return None

    return _parse_gherkin_md(content)


def _parse_gherkin_md(content: str) -> List[BddFeature]:
    """Parse Gherkin-format markdown into BddFeature objects.

    Supports:
        Feature: <name>
          Scenario: <name>
            Given <context>
            Then <clause>
            And <clause>
    """
    features: List[BddFeature] = []
    current_feature: Optional[BddFeature] = None
    current_scenario: Optional[BddScenario] = None

    for line in content.splitlines():
        stripped = line.strip()

        # Feature line
        feature_match = re.match(r"^Feature:\s*(.+)$", stripped)
        if feature_match:
            current_feature = BddFeature(name=feature_match.group(1).strip())
            features.append(current_feature)
            current_scenario = None
            continue

        # Scenario line — parse [COMPLIANCE: BLOCKING] tag if present
        scenario_match = re.match(r"^Scenario:\s*(.+)$", stripped)
        if scenario_match and current_feature is not None:
            raw_name = scenario_match.group(1).strip()
            is_blocking = "[COMPLIANCE: BLOCKING]" in raw_name.upper()
            # Strip the tag from the display name
            clean_name = re.sub(
                r"\s*\[COMPLIANCE:\s*BLOCKING\]\s*", "", raw_name, flags=re.IGNORECASE
            ).strip()
            current_scenario = BddScenario(
                name=clean_name, blocking=is_blocking
            )
            current_feature.scenarios.append(current_scenario)
            continue

        # Given line
        given_match = re.match(r"^Given\s+(.+)$", stripped)
        if given_match and current_scenario is not None:
            current_scenario.given = given_match.group(1).strip()
            continue

        # Then line
        then_match = re.match(r"^Then\s+(.+)$", stripped)
        if then_match and current_scenario is not None:
            current_scenario.clauses.append(BddClause(text=then_match.group(1).strip()))
            continue

        # And line (continuation of Then)
        and_match = re.match(r"^And\s+(.+)$", stripped)
        if and_match and current_scenario is not None:
            current_scenario.clauses.append(BddClause(text=and_match.group(1).strip()))
            continue

    return features


# ─── Source Code Reader ───────────────────────────────────────────────


def _read_all_source_content(project_dir: str) -> str:
    """Read and concatenate all source files in the project.

    Delegates to python.helpers.source_scanner.get_combined_source_text().
    Now scans the FULL project tree (not just src/app/components/lib/pages)
    for improved coverage of non-standard layouts.

    Returns a single lowercase string with all source code for regex scanning.
    """
    return get_combined_source_text(project_dir)


def _read_css_content(project_dir: str) -> str:
    """Read all CSS files for design system validation.

    Delegates to python.helpers.source_scanner.get_combined_source_text()
    with CSS-only extension filter. Now scans the full project tree.
    """
    return get_combined_source_text(project_dir, extensions={".css", ".scss", ".sass"})


# ─── Clause Verifiers ─────────────────────────────────────────────────


def _verify_element_count(clause: BddClause, all_source: str) -> Optional[dict]:
    """Verify a clause about element tag count.

    Returns None if clause passes, or a failure dict.
    """
    if clause.element_tag is None:
        return None  # Not an element-count clause — skip

    tag = clause.element_tag
    # Count opening tags in source: <section, <nav, <footer, etc.
    # Match both JSX (<section>) and HTML-style (<section )
    pattern = rf"<{re.escape(tag)}[\s>/]"
    actual_count = len(re.findall(pattern, all_source, re.IGNORECASE))

    if actual_count >= clause.min_count:
        return None  # Pass

    return {
        "clause": f"≥{clause.min_count} <{tag}>",
        "actual": actual_count,
    }


def _verify_css_custom_properties(clause: BddClause, css_content: str) -> Optional[dict]:
    """Verify CSS custom property (--*) count clauses.

    Returns None if clause passes, or a failure dict.
    """
    text_lower = clause.text.lower()
    if "custom propert" not in text_lower and "--*" not in text_lower:
        return None  # Not a CSS custom property clause

    # Extract required count
    count_match = re.search(r"[≥>=]+\s*(\d+)", clause.text)
    required = int(count_match.group(1)) if count_match else 1

    # Count --property declarations in CSS
    actual = len(re.findall(r"--[\w-]+\s*:", css_content))

    if actual >= required:
        return None  # Pass

    return {
        "clause": f"≥{required} CSS custom properties",
        "actual": actual,
    }


# ─── NOT_CONTAINS Verifier ────────────────────────────────────────────

# Pattern: Then page does NOT contain "Create Next App"
_NOT_CONTAINS_RE = re.compile(
    r'(?:page|code|source|site)\s+does\s+NOT\s+contain\s+"([^"]+)"',
    re.IGNORECASE,
)


def _verify_not_contains(clause: BddClause, all_source: str) -> Optional[dict]:
    """Verify a literal string is ABSENT from source code.

    Handles clauses like:
        Then page does NOT contain "Create Next App"
        And page does NOT contain "generated by"

    Returns None if the clause is not a NOT-contains assertion OR if the
    string is absent (pass). Returns a failure dict if the string IS found.
    """
    match = _NOT_CONTAINS_RE.search(clause.text)
    if not match:
        return None  # Not a NOT-contains clause — skip

    needle = match.group(1)

    # Case-insensitive search — if found, it's a FAILURE
    if needle.lower() in all_source.lower():
        return {
            "clause": f'does NOT contain "{needle}"',
            "actual": f'FOUND "{needle}" in source code (should be absent)',
        }

    return None  # Pass — string absent


# ─── CSS Value Verifier ───────────────────────────────────────────────

# Pattern: Then CSS contains "--background" with value "#0a0a0f"
_CSS_VALUE_RE = re.compile(
    r'CSS\s+contains\s+"([^"]+)"\s+with\s+value\s+"([^"]+)"',
    re.IGNORECASE,
)


def _verify_css_value(clause: BddClause, css_content: str) -> Optional[dict]:
    """Verify a specific CSS property has the expected value.

    Handles clauses like:
        Then CSS contains "--background" with value "#0a0a0f"
        Then CSS contains "color-scheme" with value "dark"

    Returns None if the clause is not a CSS value assertion OR if the
    value matches (pass). Returns a failure dict if the value is wrong.
    """
    match = _CSS_VALUE_RE.search(clause.text)
    if not match:
        return None  # Not a CSS value clause — skip

    prop_name = match.group(1)
    expected_value = match.group(2)

    # Search for the property:value pattern in CSS
    # Handles both `--background: #0a0a0f` and `color-scheme: dark`
    prop_pattern = re.compile(
        rf"{re.escape(prop_name)}\s*:\s*([^;}}\n]+)",
        re.IGNORECASE,
    )
    prop_match = prop_pattern.search(css_content)

    if not prop_match:
        return {
            "clause": f'CSS "{prop_name}" with value "{expected_value}"',
            "actual": f'Property "{prop_name}" NOT FOUND in CSS',
        }

    actual_value = prop_match.group(1).strip().rstrip(";")
    if expected_value.lower() in actual_value.lower():
        return None  # Pass — value matches

    return {
        "clause": f'CSS "{prop_name}" with value "{expected_value}"',
        "actual": f'Found "{prop_name}: {actual_value}" (expected "{expected_value}")',
    }


# ─── Content String Verifier ──────────────────────────────────────────

# Patterns for content string clauses:
#   Then page contains "literal text"
#   Then code imports "@stripe/stripe-js"
#   And page contains "$200/mo"
_CONTENT_STRING_RE = re.compile(
    r'(?:page|code|source|site)\s+contains\s+"([^"]+)"',
    re.IGNORECASE,
)
_IMPORT_RE = re.compile(
    r'code\s+imports\s+"([^"]+)"',
    re.IGNORECASE,
)


def _verify_content_string(clause: BddClause, all_source: str) -> Optional[dict]:
    """Verify literal string presence in source code.

    Handles clauses like:
        Then page contains "Sign Up for Free"
        Then code imports "@stripe/stripe-js"

    Returns None if the clause is not a content assertion OR if the string
    is found (pass). Returns a failure dict if the string is missing.

    NOTE: This must be checked AFTER _verify_not_contains() to avoid
    false matches on "does NOT contain" clauses.
    """
    # Skip NOT-contains clauses — those are handled by _verify_not_contains
    if _NOT_CONTAINS_RE.search(clause.text):
        return None

    # Try content string pattern first
    match = _CONTENT_STRING_RE.search(clause.text)
    if not match:
        match = _IMPORT_RE.search(clause.text)
    if not match:
        return None  # Not a content clause — skip

    needle = match.group(1)

    # Case-insensitive search for the literal string
    if needle.lower() in all_source.lower():
        return None  # Pass — string found

    return {
        "clause": f'contains "{needle}"',
        "actual": "NOT FOUND in source code",
    }


# ─── Validator ────────────────────────────────────────────────────────


def validate_bdd_scenarios(project_dir: str) -> Optional[dict]:
    """Validate project source code against BDD acceptance scenarios.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        None if bdd-scenarios.md doesn't exist (skip).
        Dict with validation results:
            - passed: bool — all scenarios satisfied
            - total_scenarios: int
            - passed_count: int
            - failures: List[dict] — each with scenario, clause, actual,
              and optionally severity/blocking
            - has_blocking_failures: bool — True if any COMPLIANCE: BLOCKING
              scenario failed
    """
    features = parse_bdd_scenarios(project_dir)
    if features is None:
        return None

    all_source = _read_all_source_content(project_dir)
    css_content = _read_css_content(project_dir)

    total_scenarios = 0
    passed_count = 0
    failures: List[dict] = []
    has_blocking_failures = False

    for feature in features:
        for scenario in feature.scenarios:
            total_scenarios += 1
            scenario_failed = False

            for clause in scenario.clauses:
                failure = None

                # Try NOT-contains verification (branding — RCA-235)
                failure = _verify_not_contains(clause, all_source)

                # Try CSS value verification (design system — RCA-235)
                if failure is None:
                    failure = _verify_css_value(clause, css_content)

                # Try content string verification (RCA-244)
                if failure is None:
                    failure = _verify_content_string(clause, all_source)

                # Try element count verification
                if failure is None:
                    failure = _verify_element_count(clause, all_source)

                # Try CSS custom property verification
                if failure is None:
                    failure = _verify_css_custom_properties(clause, css_content)

                if failure is not None:
                    failure["scenario"] = scenario.name
                    # Tag severity based on BLOCKING flag
                    if scenario.blocking:
                        failure["severity"] = "critical"
                        failure["blocking"] = True
                        has_blocking_failures = True
                    else:
                        failure["severity"] = "warning"
                        failure["blocking"] = False
                    failures.append(failure)
                    scenario_failed = True
                    continue

                # For clauses we can't mechanically verify (no recognized pattern),
                # we skip them (graceful degradation — not all clauses are verifiable
                # via static analysis; E2E agent handles the rest)

            if not scenario_failed:
                passed_count += 1

    passed = len(failures) == 0

    logger.info(
        f"[BDD] Validation complete: {passed_count}/{total_scenarios} scenarios passed, "
        f"{len(failures)} clause failures"
        + (" (BLOCKING failures present!)" if has_blocking_failures else "")
    )

    return {
        "passed": passed,
        "total_scenarios": total_scenarios,
        "passed_count": passed_count,
        "failures": failures,
        "has_blocking_failures": has_blocking_failures,
    }
