"""
TDD Red→Green Cycle Validator.

FIX-B: Enforces that agents follow the Red→Green TDD cycle:
  1. Write tests that FAIL (red baseline)
  2. Write code to make them PASS (green transition)
  3. No regressions (green→red is forbidden)

Architecture:
    - capture_red_baseline(project_dir, timeout=60) → Dict
        Runs the project's test suite, captures per-test pass/fail results,
        writes red-baseline.json to project_dir/docs/.

    - validate_red_baseline_quality(baseline) → (bool, str)
        Validates that ≥30% of tests are RED (failing). If <30% fail,
        the baseline is garbage — the agent either didn't write real tests
        or wrote tests that trivially pass.

    - verify_green_transition(project_dir, timeout=60) → (bool, Dict)
        Re-runs the test suite and compares against the red baseline.
        Requires ≥80% of RED tests to transition to GREEN, with
        0 regressions (GREEN→RED is forbidden).

Key Design Decisions (USER APPROVED):
    - Per-test results stored as {test_name: 'pass'|'fail'}
    - 30s timeout for test execution
    - ≥30% red ratio required for valid baseline
    - ≥80% red→green transition required to pass
    - 0 regressions (green→red) allowed
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agix.tdd_red_green_validator")

# ── Constants ──
RED_RATIO_THRESHOLD = 0.50       # ≥50% of tests must fail for valid baseline
GREEN_TRANSITION_THRESHOLD = 0.99  # ≥99% of RED tests must become GREEN
BASELINE_FILENAME = "red-baseline.json"


TEST_CONFIG_FILENAME = "test-config.json"


def _read_test_config(project_dir: str) -> Optional[Dict[str, str]]:
    """Read the agent-written test config.

    DESIGN: The agent knows the framework — it picked it, scaffolded it,
    and configured the test runner. During TDD stub generation, it writes
    docs/test-config.json with the test command. We just read and run it.

    Expected format:
        {"test_command": "npm test -- --verbose", "parse_format": "node"}

    Args:
        project_dir: Path to the project root directory.

    Returns:
        Dict with test_command and parse_format, or None if not found.
    """
    config_path = os.path.join(project_dir, "docs", TEST_CONFIG_FILENAME)
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if "test_command" in config:
            return config
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Could not read test config at %s: %s", config_path, e)
    return None


def _detect_test_command(project_dir: str) -> Tuple[List[str], str]:
    """Determine the test command and output format for a project.

    Priority 1: docs/test-config.json (agent-written, always correct)
    Priority 2: File-marker heuristics (fallback for legacy/unconfigured)

    Heuristics (only if no test-config.json):
      - package.json → npm test -- --verbose (node output)
      - pyproject.toml / setup.py → pytest -v (pytest output)
      - Cargo.toml → cargo test (generic output)
      - go.mod → go test ./... -v (generic output)
      - Gemfile → bundle exec rspec (generic output)
      - Default → pytest

    Args:
        project_dir: Path to the project root directory.

    Returns:
        (command_list, parse_format) where parse_format is
        "node", "pytest", or "generic"
    """
    # Priority 1: Agent-written config
    config = _read_test_config(project_dir)
    if config:
        cmd_str = config["test_command"]
        parse_format = config.get("parse_format", "generic")
        logger.info("Using agent-configured test command: %s", cmd_str)
        return cmd_str.split(), parse_format

    # Priority 2: File-marker heuristics
    if os.path.isfile(os.path.join(project_dir, "package.json")):
        return ["npm", "test", "--", "--verbose", "--forceExit"], "node"
    if os.path.isfile(os.path.join(project_dir, "pyproject.toml")):
        return ["python", "-m", "pytest", "-v", "--tb=no", "--no-header"], "pytest"
    if os.path.isfile(os.path.join(project_dir, "setup.py")):
        return ["python", "-m", "pytest", "-v", "--tb=no", "--no-header"], "pytest"
    if os.path.isfile(os.path.join(project_dir, "Cargo.toml")):
        return ["cargo", "test", "--", "--format=terse"], "generic"
    if os.path.isfile(os.path.join(project_dir, "go.mod")):
        return ["go", "test", "./...", "-v"], "generic"
    if os.path.isfile(os.path.join(project_dir, "Gemfile")):
        return ["bundle", "exec", "rspec", "--format", "documentation"], "generic"

    # Default fallback
    return ["python", "-m", "pytest", "-v", "--tb=no", "--no-header"], "pytest"


_EMPTY_RESULT: Dict[str, Any] = {
    "tests": [],
    "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
}


def _run_tests_json(project_dir: str, timeout: int = 60) -> Dict[str, Any]:
    """Run the project's test suite and return structured results.

    DESIGN: Agent-first, heuristic-fallback.
      1. Read docs/test-config.json (agent tells us the command)
      2. Fall back to file-marker heuristics
      3. Run the command, parse output by format

    For Node projects, checks node_modules exists first.

    Args:
        project_dir: Path to the project root directory.
        timeout: Maximum seconds to wait for test execution.

    Returns:
        Dict with 'tests' (list of {nodeid, outcome}) and 'summary'.
    """
    cmd, parse_format = _detect_test_command(project_dir)

    # Node guard: node_modules must exist
    if parse_format == "node" or (cmd and cmd[0] in ("npm", "npx", "yarn", "pnpm")):
        node_modules = os.path.join(project_dir, "node_modules")
        if not os.path.isdir(node_modules):
            logger.warning(
                "node_modules not found at %s — cannot run tests "
                "(npm install hasn't run yet?)",
                node_modules,
            )
            return dict(_EMPTY_RESULT)

    # Select parser
    if parse_format == "node":
        parse_fn = _parse_node_test_output
    elif parse_format == "pytest":
        parse_fn = _parse_pytest_output
    else:
        # Generic: try node parser first (handles ✓/✕), fall back to pytest
        parse_fn = _parse_generic_test_output

    try:
        logger.info("Running tests: %s (cwd=%s)", " ".join(cmd), project_dir)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_dir,
        )
        return parse_fn(result.stdout + "\n" + result.stderr)

    except subprocess.TimeoutExpired:
        logger.warning("Test execution timed out after %ds", timeout)
        return dict(_EMPTY_RESULT)
    except FileNotFoundError:
        logger.error("Test command not found: %s", cmd[0] if cmd else "?")
        return dict(_EMPTY_RESULT)
    except Exception as exc:
        logger.error("Error running tests: %s", exc)
        return dict(_EMPTY_RESULT)


def _parse_node_test_output(output: str) -> Dict[str, Any]:
    """Parse npm test output into structured results.

    DESIGN: Handles output from ANY JS/TS test runner (jest, vitest,
    mocha, etc.) by parsing universal symbols:
      ✓/√ → passed
      ✕/×  → failed
      ↓ [skipped] → skipped (vitest)
      ○ skipped → skipped (jest)

    Also handles PASS/FAIL file headers (jest) and summary lines
    from both jest and vitest formats.

    Args:
        output: Raw npm test stdout+stderr.

    Returns:
        Dict with 'tests' and 'summary'.
    """
    import re

    tests: List[Dict[str, str]] = []
    current_file = ""

    for line in output.splitlines():
        stripped = line.strip()

        # Track current test file (jest PASS/FAIL headers)
        file_match = re.match(r"^(PASS|FAIL)\s+(.+)$", stripped)
        if file_match:
            current_file = file_match.group(2)
            continue

        # Pass: ✓ or √
        if stripped.startswith("✓") or stripped.startswith("√"):
            name = re.sub(r"\s*\(\d+\s*m?s\)\s*$", "", stripped[1:].strip())
            nodeid = f"{current_file} > {name}" if current_file else name
            tests.append({"nodeid": nodeid, "outcome": "passed"})

        # Fail: ✕ or ×
        elif stripped.startswith("✕") or stripped.startswith("×"):
            name = re.sub(r"\s*\(\d+\s*m?s\)\s*$", "", stripped[1:].strip())
            nodeid = f"{current_file} > {name}" if current_file else name
            tests.append({"nodeid": nodeid, "outcome": "failed"})

        # Skip (vitest): ↓ test name [skipped]
        elif stripped.startswith("↓"):
            name = re.sub(r"\s*\[skipped\]\s*$", "", stripped[1:].strip())
            nodeid = f"{current_file} > {name}" if current_file else name
            tests.append({"nodeid": nodeid, "outcome": "skipped"})

        # Skip (jest): ○ skipped test name
        elif stripped.startswith("○"):
            name = stripped[1:].strip()
            name = re.sub(r"^skipped\s+", "", name)
            nodeid = f"{current_file} > {name}" if current_file else name
            tests.append({"nodeid": nodeid, "outcome": "skipped"})

    # Count from parsed tests
    passed = sum(1 for t in tests if t["outcome"] == "passed")
    failed = sum(1 for t in tests if t["outcome"] == "failed")
    skipped = sum(1 for t in tests if t["outcome"] == "skipped")
    total = len(tests)

    # Fallback: parse summary line if no individual tests found
    if total == 0:
        for line in output.splitlines():
            # Jest format: "Tests:       2 failed, 3 passed, 6 total"
            # Vitest format: "Tests  2 failed | 3 passed | 6 total"
            summary_match = re.search(
                r"Tests[:\s]+.*?(\d+)\s+total", line, re.IGNORECASE
            )
            if summary_match:
                total = int(summary_match.group(1))
                p_match = re.search(r"(\d+)\s+passed", line)
                f_match = re.search(r"(\d+)\s+failed", line)
                s_match = re.search(r"(\d+)\s+skipped", line)
                passed = int(p_match.group(1)) if p_match else 0
                failed = int(f_match.group(1)) if f_match else 0
                skipped = int(s_match.group(1)) if s_match else 0
                break

    return {
        "tests": tests,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
    }


def _parse_pytest_output(output: str) -> Dict[str, Any]:
    """Parse pytest output into structured test results.

    Handles pytest's default output format:
      - Lines like 'test_file.py::TestClass::test_name PASSED'
      - Summary line like '3 passed, 2 failed in 0.05s'

    Args:
        output: Raw pytest stdout+stderr.

    Returns:
        Dict with 'tests' and 'summary'.
    """
    import re

    tests: List[Dict[str, str]] = []

    for line in output.splitlines():
        line = line.strip()
        # Match pytest verbose output: 'test_file.py::Class::test PASSED/FAILED/SKIPPED'
        match = re.match(
            r"^(.+::\S+)\s+(PASSED|FAILED|SKIPPED|ERROR)\s*$",
            line,
            re.IGNORECASE,
        )
        if match:
            nodeid = match.group(1)
            outcome = match.group(2).lower()
            # Normalize: PASSED→passed, FAILED→failed
            tests.append({"nodeid": nodeid, "outcome": outcome})

    # Parse summary
    passed = sum(1 for t in tests if t["outcome"] == "passed")
    failed = sum(1 for t in tests if t["outcome"] == "failed")
    skipped = sum(1 for t in tests if t["outcome"] == "skipped")
    total = len(tests)

    return {
        "tests": tests,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
    }


def _parse_generic_test_output(output: str) -> Dict[str, Any]:
    """Parse test output from any unknown runner.

    Tries node parser first (handles ✓/✕ symbols used by most
    modern test runners), then falls back to pytest parser.
    Returns whichever finds more tests.

    Args:
        output: Raw test stdout+stderr.

    Returns:
        Dict with 'tests' and 'summary'.
    """
    node_result = _parse_node_test_output(output)
    pytest_result = _parse_pytest_output(output)

    # Return whichever found more tests
    node_total = node_result.get("summary", {}).get("total", 0)
    pytest_total = pytest_result.get("summary", {}).get("total", 0)

    if node_total >= pytest_total:
        return node_result
    return pytest_result


def capture_red_baseline(project_dir: str, timeout: int = 60) -> Dict[str, Any]:
    """Capture the RED baseline of the project's test suite.

    Runs all tests and records per-test pass/fail status. The baseline
    is written to project_dir/docs/red-baseline.json.

    Args:
        project_dir: Path to the project root directory.
        timeout: Maximum seconds to wait for test execution.

    Returns:
        Dict with keys:
          - per_test: {test_name: 'pass'|'fail'}
          - total: total test count
          - passed: passed count
          - failed: failed count
          - skipped: skipped count
          - red_ratio: failed / (total - skipped), 0.0 if no non-skipped tests
    """
    result = _run_tests_json(project_dir, timeout=timeout)

    # Build per-test dict
    per_test: Dict[str, str] = {}
    for test in result.get("tests", []):
        nodeid = test["nodeid"]
        outcome = test["outcome"]
        if outcome == "passed":
            per_test[nodeid] = "pass"
        elif outcome in ("failed", "error"):
            per_test[nodeid] = "fail"
        # skipped tests are not included in per_test (they're irrelevant to TDD)

    summary = result.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)

    # Calculate red_ratio — avoid division by zero
    non_skipped = total - skipped
    red_ratio = (failed / non_skipped) if non_skipped > 0 else 0.0

    # FIX-15: Prevent saving 0-test baseline when stubs exist
    if total == 0:
        stubs_dir = os.path.join(project_dir, "docs", "tdd")
        if os.path.isdir(stubs_dir) and any(
            os.path.isfile(os.path.join(stubs_dir, f))
            for f in os.listdir(stubs_dir)
        ):
            logger.error(
                "RED baseline has 0 tests but docs/tdd/ contains stubs. "
                "Test runner cannot discover tests — NOT saving empty baseline."
            )
            return None

    baseline = {
        "per_test": per_test,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "red_ratio": red_ratio,
    }

    # Write to docs directory
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    baseline_path = os.path.join(docs_dir, BASELINE_FILENAME)

    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2)

    logger.info(
        "Red baseline captured: %d total, %d failed, %d passed, %d skipped, ratio=%.2f → %s",
        total, failed, passed, skipped, red_ratio, baseline_path,
    )

    return baseline


def validate_red_baseline_quality(baseline: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate that a red baseline has sufficient test failures.

    A valid baseline requires:
      - At least 1 test exists
      - ≥30% of non-skipped tests are RED (failing)

    This prevents agents from gaming the system by writing tests that
    trivially pass — the "red" phase must actually produce failures.

    Args:
        baseline: Dict with 'total', 'failed', 'skipped', 'red_ratio' keys.

    Returns:
        (valid: bool, message: str) — message explains why invalid.
    """
    total = baseline.get("total", 0)
    failed = baseline.get("failed", 0)
    skipped = baseline.get("skipped", 0)
    red_ratio = baseline.get("red_ratio", 0.0)

    if total == 0:
        return False, "INVALID: No tests found in baseline. Write tests first."

    if total - skipped == 0:
        return False, "INVALID: All tests are skipped. No non-skipped tests to validate."

    if red_ratio < RED_RATIO_THRESHOLD:
        return (
            False,
            f"INVALID: Red ratio {red_ratio:.2f} is below threshold {RED_RATIO_THRESHOLD}. "
            f"Only {failed}/{total - skipped} non-skipped tests are failing. "
            f"Write meaningful tests that FAIL before implementation.",
        )

    return (
        True,
        f"VALID: Red ratio {red_ratio:.2f} ({failed}/{total - skipped} failing). "
        f"Baseline quality is acceptable.",
    )


def verify_green_transition(
    project_dir: str,
    timeout: int = 60,
) -> Tuple[bool, Dict[str, Any]]:
    """Verify that RED tests have transitioned to GREEN.

    Loads the red baseline from project_dir/docs/red-baseline.json,
    re-runs the test suite, and compares results:
      - ≥80% of RED tests must now pass (red→green transition)
      - 0 regressions: GREEN tests must not become RED (green→red forbidden)

    If no baseline exists, this function skips (returns True) so it
    doesn't block agents that haven't set up TDD yet.

    Args:
        project_dir: Path to the project root directory.
        timeout: Maximum seconds to wait for test execution.

    Returns:
        (passed: bool, details: Dict) — details include transition stats.
    """
    baseline_path = os.path.join(project_dir, "docs", BASELINE_FILENAME)

    if not os.path.exists(baseline_path):
        return True, {
            "reason": "Skipped — no red-baseline.json found",
            "red_to_green_ratio": 0.0,
            "regressions": 0,
        }

    # Load baseline
    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    per_test_baseline = baseline.get("per_test", {})

    if not per_test_baseline:
        return True, {
            "reason": "Skipped — baseline has no per-test data",
            "red_to_green_ratio": 0.0,
            "regressions": 0,
        }

    # Run tests again
    result = _run_tests_json(project_dir, timeout=timeout)

    # Build current per-test dict
    current_per_test: Dict[str, str] = {}
    for test in result.get("tests", []):
        nodeid = test["nodeid"]
        outcome = test["outcome"]
        if outcome == "passed":
            current_per_test[nodeid] = "pass"
        elif outcome in ("failed", "error"):
            current_per_test[nodeid] = "fail"

    # Calculate transitions
    red_tests = [name for name, status in per_test_baseline.items() if status == "fail"]
    green_tests = [name for name, status in per_test_baseline.items() if status == "pass"]

    red_to_green = 0  # RED tests that are now GREEN (good)
    red_stayed_red = 0  # RED tests that are still RED (bad)
    regressions = 0  # GREEN tests that became RED (very bad)
    regressed_tests: List[str] = []

    for test_name in red_tests:
        current_status = current_per_test.get(test_name, "fail")
        if current_status == "pass":
            red_to_green += 1
        else:
            red_stayed_red += 1

    for test_name in green_tests:
        current_status = current_per_test.get(test_name, "pass")
        if current_status == "fail":
            regressions += 1
            regressed_tests.append(test_name)

    # Calculate red→green ratio
    total_red = len(red_tests)
    red_to_green_ratio = (red_to_green / total_red) if total_red > 0 else 0.0

    details = {
        "red_to_green": red_to_green,
        "red_stayed_red": red_stayed_red,
        "total_red_baseline": total_red,
        "red_to_green_ratio": red_to_green_ratio,
        "regressions": regressions,
        "regressed_tests": regressed_tests,
    }

    # Fail conditions
    if regressions > 0:
        details["reason"] = (
            f"FAILED: {regressions} regression(s) detected — "
            f"GREEN tests became RED: {regressed_tests}"
        )
        logger.error(details["reason"])
        return False, details

    if red_to_green_ratio < GREEN_TRANSITION_THRESHOLD:
        details["reason"] = (
            f"FAILED: Only {red_to_green_ratio:.0%} of RED tests transitioned to GREEN "
            f"(threshold: {GREEN_TRANSITION_THRESHOLD:.0%}). "
            f"{red_to_green}/{total_red} RED→GREEN, {red_stayed_red} still RED."
        )
        logger.warning(details["reason"])
        return False, details

    details["reason"] = (
        f"PASSED: {red_to_green_ratio:.0%} of RED tests transitioned to GREEN "
        f"({red_to_green}/{total_red}), 0 regressions."
    )
    logger.info(details["reason"])
    return True, details


def check_zero_test_baseline(
    project_dir: str,
) -> Tuple[bool, str]:
    """Fail if TDD stubs exist but zero tests were discovered.

    C-2: This gate catches the scenario where TDD stubs are generated
    (docs/tdd/ has files) but the test runner discovers 0 tests
    in the RED baseline. This means stubs were written but are invisible
    to the test runner — a silent failure that wastes the entire TDD cycle.

    Logic:
      - No stubs dir → PASS (skip, TDD not configured)
      - Stubs exist + no baseline file → FAIL (baseline was never captured)
      - Stubs exist + baseline has 0 tests → FAIL (runner can't find tests)
      - Stubs exist + baseline has >0 tests → PASS

    Args:
        project_dir: Path to the project root directory.

    Returns:
        (passed: bool, message: str)
    """
    stubs_dir = os.path.join(project_dir, "docs", "tdd")

    # No stubs directory → skip (TDD not configured for this project)
    if not os.path.isdir(stubs_dir):
        return True, "SKIP: No docs/tdd/ directory — TDD not configured."

    # Check if stubs directory actually has files
    stub_files = [
        f for f in os.listdir(stubs_dir)
        if os.path.isfile(os.path.join(stubs_dir, f))
    ]
    if not stub_files:
        return True, "SKIP: docs/tdd/ exists but is empty."

    # Stubs exist — check for baseline
    baseline_path = os.path.join(project_dir, "docs", BASELINE_FILENAME)
    if not os.path.isfile(baseline_path):
        return (
            False,
            f"FAIL: {len(stub_files)} TDD stubs exist in docs/tdd/ "
            f"but no {BASELINE_FILENAME} was captured. "
            f"Run capture_red_baseline() first.",
        )

    # Read baseline
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        return (
            False,
            f"FAIL: Could not read {BASELINE_FILENAME}: {e}",
        )

    total = baseline.get("total", 0)
    if total == 0:
        return (
            False,
            f"FAIL: {len(stub_files)} TDD stubs exist but RED baseline shows "
            f"0 tests discovered. The test runner cannot find the stubs. "
            f"Check test directory wiring (_write_stubs_to_test_dir).",
        )

    return (
        True,
        f"PASS: {total} tests discovered from {len(stub_files)} TDD stubs.",
    )

