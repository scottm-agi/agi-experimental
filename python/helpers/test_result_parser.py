"""Test Result Parser — parse test runner output.

Migrated from test_execution_gate.py (deleted as part of gate system removal).
"""
import re
from typing import Any, Dict


def parse_test_result(
    output: str,
) -> Dict[str, Any]:
    """Parse test runner output to extract pass/fail verdict.
    
    Supports:
      - Vitest: "Tests  5 passed (5)" / "Tests  3 passed | 1 failed (4)"
      - Jest:   "Tests: 8 passed, 8 total" / "Tests: 2 failed, 6 passed, 8 total"
      - pytest: "15 passed in 0.05s" / "2 failed, 13 passed in 0.05s"
    
    Args:
        output: Raw stdout/stderr from the test runner
    
    Returns:
        Dict with 'passed', 'tests_passed', 'tests_failed', 'confidence'
    """
    if not output or not output.strip():
        return {
            "passed": False,
            "tests_passed": 0,
            "tests_failed": 0,
            "confidence": "low",
            "reason": "Empty test output",
        }
    
    tests_passed = 0
    tests_failed = 0
    confidence = "low"
    
    # ── Vitest format ──
    # "Tests  5 passed (5)" or "Tests  3 passed | 1 failed (4)"
    vitest_match = re.search(
        r"Tests\s+(\d+)\s+passed(?:\s*\|\s*(\d+)\s+failed)?",
        output,
        re.IGNORECASE,
    )
    if vitest_match:
        tests_passed = int(vitest_match.group(1))
        tests_failed = int(vitest_match.group(2) or 0)
        confidence = "high"
    
    # ── Jest format ──
    # "Tests: 8 passed, 8 total" or "Tests: 2 failed, 6 passed, 8 total"
    if confidence == "low":
        jest_match = re.search(
            r"Tests:\s+(?:(\d+)\s+failed,\s*)?(\d+)\s+passed,\s*(\d+)\s+total",
            output,
            re.IGNORECASE,
        )
        if jest_match:
            tests_failed = int(jest_match.group(1) or 0)
            tests_passed = int(jest_match.group(2))
            confidence = "high"
    
    # ── pytest format ──
    # "15 passed in 0.05s" or "2 failed, 13 passed in 0.05s"
    if confidence == "low":
        pytest_match = re.search(
            r"(?:(\d+)\s+failed,?\s*)?(\d+)\s+passed\s+in\s+[\d.]+s",
            output,
            re.IGNORECASE,
        )
        if pytest_match:
            tests_failed = int(pytest_match.group(1) or 0)
            tests_passed = int(pytest_match.group(2))
            confidence = "high"
    
    # ── Playwright format ──
    # "X passed" or "X failed, Y passed" (simpler format)
    # Also: "X passed (Xs)" with timing
    if confidence == "low":
        playwright_match = re.search(
            r"(\d+)\s+passed(?:\s*\(|\s|$)",
            output,
            re.IGNORECASE,
        )
        if playwright_match:
            tests_passed = int(playwright_match.group(1))
            # Check for failures too
            pw_fail = re.search(r"(\d+)\s+failed", output, re.IGNORECASE)
            if pw_fail:
                tests_failed = int(pw_fail.group(1))
            confidence = "high"
    
    # ── Cypress format ──
    # "All specs passed!" or "Passing: X" / "Failing: Y"
    if confidence == "low":
        if re.search(r"All specs passed", output, re.IGNORECASE):
            # All passed but no count available — mark as passed
            tests_passed = 1  # At least 1
            tests_failed = 0
            confidence = "medium"  # No exact count
        else:
            cypress_pass = re.search(
                r"Passing:\s*(\d+)", output, re.IGNORECASE,
            )
            cypress_fail = re.search(
                r"Failing:\s*(\d+)", output, re.IGNORECASE,
            )
            if cypress_pass:
                tests_passed = int(cypress_pass.group(1))
                tests_failed = int(cypress_fail.group(1)) if cypress_fail else 0
                confidence = "high"
    
    # ── Error detection ──
    # If output contains error signals and we have no pass counts, treat as failure
    if confidence == "low":
        error_signals = [
            "error:", "Error:", "Cannot find module",
            "SyntaxError", "TypeError", "ImportError",
            "FAIL", "FAILED",
        ]
        for signal in error_signals:
            if signal in output:
                return {
                    "passed": False,
                    "tests_passed": 0,
                    "tests_failed": 0,
                    "confidence": "medium",
                    "reason": f"Error detected in output: {signal}",
                }
    
    passed = confidence in ("high", "medium") and tests_failed == 0 and tests_passed > 0
    
    return {
        "passed": passed,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "confidence": confidence,
    }


# ── RCA-475 GAP-4: Test Run Command Detection ───────────────────────────

# Patterns that indicate a test runner was executed
_TEST_COMMAND_PATTERNS = [
    re.compile(r"\bnpm\s+test\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+(vitest|jest|playwright|cypress)\b", re.IGNORECASE),
    re.compile(r"\b(vitest|jest)\s+run\b", re.IGNORECASE),
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\bpython\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+run\s+test\b", re.IGNORECASE),
    re.compile(r"\byarn\s+test\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+test\b", re.IGNORECASE),
    re.compile(r"\bbun\s+test\b", re.IGNORECASE),
]


def detect_test_run_commands(
    commands: list,
) -> Dict[str, Any]:
    """Detect if test runner commands were executed.

    RCA-475 GAP-4: Migrated from deleted test_execution_gate.py.
    Scans a list of command strings for known test runner patterns.

    Args:
        commands: List of command strings from tool history.

    Returns:
        Dict with ``executed`` (bool) and ``matched_commands`` (list).
    """
    if not commands:
        return {"executed": False, "matched_commands": []}

    matched = []
    for cmd in commands:
        if not isinstance(cmd, str):
            continue
        for pattern in _TEST_COMMAND_PATTERNS:
            if pattern.search(cmd):
                matched.append(cmd)
                break

    return {
        "executed": len(matched) > 0,
        "matched_commands": matched,
    }
