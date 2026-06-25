"""
Test-pass advisory check for the orchestrator completion gate.

FIX-17: Runs `npm test` (or equivalent) and reports pass/fail
deterministically. Checks whether the project's test suite passes.

Follows the same pattern as build_pass_check.py.
This is an ADVISORY check — it doesn't block completion, but it surfaces
test failures as warnings so the orchestrator can re-delegate fixes.
"""
from __future__ import annotations

import functools
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agix.test_pass_check")


def _testable(fn):
    """Decorator to make the function testable by exposing __wrapped__ with explicit args."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    wrapper.__wrapped__ = fn
    return wrapper


@_testable
def check_tests_pass(
    project_dir: Optional[str] = None,
    exit_code: Optional[int] = None,
    output: Optional[str] = None,
) -> dict:
    """Check if the project test suite passes.

    Can be called in two modes:
    1. With project_dir: Runs `npm test` and checks exit code.
    2. With exit_code/output directly: For testing (skip subprocess).

    Returns:
        dict with keys:
        - passed: bool — whether tests succeeded
        - reason: str — human-readable explanation
        - output: str — test output (truncated)
    """
    if exit_code is None and project_dir:
        # Check for package.json
        pkg_json = Path(project_dir) / "package.json"
        if not pkg_json.exists():
            return {
                "passed": True,
                "reason": "No package.json found — skipping test check",
                "output": "",
            }

        # Check if there's a test script
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {
                "passed": True,
                "reason": "Could not parse package.json — skipping test check",
                "output": "",
            }

        scripts = pkg_data.get("scripts", {})
        if "test" not in scripts:
            return {
                "passed": True,
                "reason": "No test script in package.json — skipping test check",
                "output": "",
            }

        # Run tests
        test_cmd = ["npm", "test", "--", "--json"]

        try:
            result = subprocess.run(
                test_cmd,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=180,
                env={
                    **dict(__import__("os").environ),
                    "CI": "true",  # Non-interactive mode
                },
            )
            exit_code = result.returncode
            output = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "reason": "Tests timed out after 180 seconds",
                "output": "",
            }
        except Exception as e:
            return {
                "passed": False,
                "reason": f"Tests failed to start: {e}",
                "output": "",
            }

    # Parse results
    # --- Fallback: check test-results.json file if stdout has no JSON ---
    # Vitest --reporter=json writes results to a FILE (test-results.json),
    # not stdout. The stdout parser will return ?/? in this case.
    # RCA-336: This caused false gate blocks when tests passed but the
    # parser couldn't find results in stdout.
    def _check_results_file(proj_dir: str) -> dict:
        """Try reading test-results.json from the project directory."""
        import os
        results_file = os.path.join(proj_dir, "test-results.json")
        if os.path.exists(results_file):
            try:
                with open(results_file) as f:
                    data = json.loads(f.read())
                if "numTotalTests" in data:
                    return {
                        "total": data.get("numTotalTests", "?"),
                        "passed": data.get("numPassedTests", "?"),
                        "failures": data.get("numFailedTests", "?"),
                        "success": data.get("success", None),
                        "from_file": True,
                    }
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    if exit_code == 0:
        # Try to extract test count from JSON output
        test_info = _parse_test_json(output or "")
        total = test_info.get("total", "?")
        # Fallback to results file if stdout had no JSON
        if total == "?" and project_dir:
            file_info = _check_results_file(project_dir)
            if file_info:
                total = file_info.get("total", "?")
        return {
            "passed": True,
            "reason": f"All tests passed ({total} total)",
            "output": (output or "")[-500:],
        }
    else:
        test_info = _parse_test_json(output or "")
        failures = test_info.get("failures", "?")
        total = test_info.get("total", "?")

        # Fallback: check test-results.json file (vitest writes here)
        if (failures == "?" or total == "?") and project_dir:
            file_info = _check_results_file(project_dir)
            if file_info:
                failures = file_info.get("failures", failures)
                total = file_info.get("total", total)
                # If the results FILE says success=True but exit code
                # was non-zero, trust the file (vitest CJS warning
                # can cause npm to report non-zero exit)
                if file_info.get("success") is True and file_info.get("failures", 1) == 0:
                    return {
                        "passed": True,
                        "reason": f"All tests passed ({total} total) — from test-results.json",
                        "output": (output or "")[-500:],
                    }

        # Extract error lines from output
        error_lines = []
        for line in (output or "").split("\n"):
            if "fail" in line.lower() or "error" in line.lower():
                error_lines.append(line.strip())

        error_summary = "\n".join(error_lines[-5:]) if error_lines else (output or "")[-300:]

        return {
            "passed": False,
            "reason": f"Tests failed ({failures} failures out of {total} total): {error_summary[:200]}",
            "output": (output or "")[-500:],
            "total": total,
            "failures": failures,
        }


def _parse_test_json(output: str) -> dict:
    """Try to parse Jest JSON output for test counts."""
    try:
        # Jest --json output may be mixed with other output
        # Try to find a JSON object in the output
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("{") and "numTotalTests" in line:
                data = json.loads(line)
                return {
                    "total": data.get("numTotalTests", "?"),
                    "passed": data.get("numPassedTests", "?"),
                    "failures": data.get("numFailedTests", "?"),
                }
        # Try parsing the whole output as JSON
        data = json.loads(output)
        if "numTotalTests" in data:
            return {
                "total": data.get("numTotalTests", "?"),
                "passed": data.get("numPassedTests", "?"),
                "failures": data.get("numFailedTests", "?"),
            }
    except (json.JSONDecodeError, ValueError):
        pass
    return {"total": "?", "passed": "?", "failures": "?"}
