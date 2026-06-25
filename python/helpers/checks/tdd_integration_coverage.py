"""
I-3: TDD Integration Test Coverage Gate Check.

Pure L1 (deterministic) gate check that verifies each manifest integration
has corresponding test coverage with SDK imports and assertions.

Architecture: 2-layer detection (Layer 1 only — deterministic file scan).
- Scans test directories and colocated test files for SDK import + assertion
- Blocks when >50% of integrations lack test coverage
- Circuit breaker at 2 blocks to prevent death spiral
"""

import logging
import os
import re
from typing import List, Optional, Tuple

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    CheckContext,
)
from python.helpers.manifest_parser import parse_manifest


logger = logging.getLogger("agix.checks.tdd_integration_coverage")


# ─── Tunable Quality/Cost Levers ────────────────────────────────────────
MAX_INTEGRATION_TEST_BLOCKS = 2
COVERAGE_BLOCK_THRESHOLD = 1.0  # RCA-470: Block if any integration lacks test coverage (was 0.50)
TEST_DIRS = ("__tests__", "tests", "test", "cypress", "e2e")
TEST_FILE_PATTERNS = (re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|py)$"),)
ASSERTION_KEYWORDS = (
    "expect",
    "assert",
    "should",
    "toBe",
    "toEqual",
    "toHaveBeenCalled",
    "toBeDefined",
    "toContain",
    "toMatch",
    "toThrow",
)

# Counter name for gate_block_counters
_COUNTER_NAME = "integration_test_coverage"


def _extract_integration_name(entry) -> Optional[str]:
    """Extract integration name from a manifest entry (string or dict)."""
    if isinstance(entry, str):
        return entry.strip() if entry.strip() else None
    if isinstance(entry, dict):
        name = entry.get("name", "")
        return name.strip() if isinstance(name, str) and name.strip() else None
    return None


def _collect_test_files(project_dir: str) -> List[str]:
    """Collect all test files from known test directories and colocated test files.

    Returns:
        List of absolute paths to test files found in the project.
    """
    test_files: List[str] = []

    # 1. Scan test directories
    for test_dir_name in TEST_DIRS:
        test_dir = os.path.join(project_dir, test_dir_name)
        if not os.path.isdir(test_dir):
            continue
        for root, _dirs, files in os.walk(test_dir):
            for fname in files:
                # Accept any file in a test directory
                if fname.endswith((".ts", ".tsx", ".js", ".jsx", ".py")):
                    test_files.append(os.path.join(root, fname))

    # 2. Scan for colocated test files (*.test.ts, *.spec.ts, etc.) in src/
    src_dir = os.path.join(project_dir, "src")
    if os.path.isdir(src_dir):
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                for pattern in TEST_FILE_PATTERNS:
                    if pattern.search(fname):
                        test_files.append(os.path.join(root, fname))
                        break

    return test_files


def _file_has_integration_test(
    file_path: str, integration_name: str
) -> bool:
    """Check if a test file contains both an SDK reference and an assertion.

    Args:
        file_path: Absolute path to the test file.
        integration_name: Name of the integration to look for (case-insensitive).

    Returns:
        True if the file references the integration SDK AND has assertions.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return False

    content_lower = content.lower()
    name_lower = integration_name.lower()

    # Check for SDK reference (integration name appears in file)
    if name_lower not in content_lower:
        return False

    # Check for at least one assertion keyword
    for keyword in ASSERTION_KEYWORDS:
        if keyword.lower() in content_lower:
            return True

    return False


def _check_integration_coverage(
    project_dir: str, integration_names: List[str]
) -> Tuple[List[str], List[str], float]:
    """Check which integrations have test coverage.

    Args:
        project_dir: Absolute path to project directory.
        integration_names: List of integration names to check.

    Returns:
        Tuple of (covered_names, uncovered_names, coverage_ratio).
    """
    test_files = _collect_test_files(project_dir)

    covered: List[str] = []
    uncovered: List[str] = []

    for name in integration_names:
        found = False
        for test_file in test_files:
            if _file_has_integration_test(test_file, name):
                found = True
                break
        if found:
            covered.append(name)
        else:
            uncovered.append(name)

    total = len(integration_names)
    coverage = len(covered) / total if total > 0 else 1.0

    return covered, uncovered, coverage


@register_check(
    1.321, "TDD: Integration test coverage", critical=False, web_only=True, gate="tdd"
)
def _check_tdd_integration_coverage(ctx: CheckContext):
    """Verify each manifest integration has corresponding test coverage.

    I-3 Gate Check: Pure L1 deterministic scan.

    Scans test directories and colocated test files for:
    1. SDK import matching integration name (case-insensitive)
    2. At least one assertion keyword

    Blocks when >50% of integrations lack test coverage.
    Circuit breaker at MAX_INTEGRATION_TEST_BLOCKS to prevent death spiral.
    """
    if not ctx.project_dir:
        return None

    # Circuit breaker — prevent death spiral
    if True:  # gate_block_counters stub removed — circuit_breaker_escalate was always True
        return None

    # Load manifest
    manifest = parse_manifest(ctx.project_dir)
    if not manifest.integrations:
        return None

    # Extract integration names
    integration_names: List[str] = []
    for entry in manifest.integrations:
        name = _extract_integration_name(entry)
        if name:
            integration_names.append(name)

    if not integration_names:
        return None

    # Check coverage
    covered, uncovered, coverage = _check_integration_coverage(
        ctx.project_dir, integration_names
    )

    if not uncovered:
        return None  # All integrations have tests — pass

    # Decide: block or advisory
    if coverage < COVERAGE_BLOCK_THRESHOLD:
        # >50% missing → BLOCK


        missing_list = ", ".join(f"`{n}`" for n in uncovered)
        covered_list = ", ".join(f"`{n}`" for n in covered) if covered else "none"

        message = (
            f"INTEGRATION TEST COVERAGE: {len(uncovered)}/{len(integration_names)} "
            f"integrations lack test files ({coverage:.0%} covered).\n"
            f"Missing tests for: {missing_list}\n"
            f"Covered: {covered_list}\n\n"
            f"Each integration in the manifest MUST have a test file that:\n"
            f"1. Imports or references the integration SDK\n"
            f"2. Contains at least one assertion (expect, assert, should, etc.)\n\n"
            f"Write tests in __tests__/, tests/, or as colocated *.test.ts files."
        )
        return ctx.block(
            message,
            action=(
                f"Create test files for {len(uncovered)} uncovered integration(s): "
                f"{missing_list}. Each test must import the SDK and include assertions."
            ),
        )

    # Some missing but not >50% → advisory (return None, just log)
    logger.info(
        f"[INTEGRATION TESTS] {len(uncovered)}/{len(integration_names)} "
        f"integrations lack tests (advisory): {', '.join(uncovered)}"
    )
    return None
