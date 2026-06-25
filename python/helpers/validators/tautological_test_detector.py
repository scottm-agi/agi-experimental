"""
WB-6: Tautological Test Detector

Detects test assertions that test nothing — e.g., expect('07:30').toBe('07:30').
These pass all existing gates because test EXISTENCE is gated (quality.py:730)
but test QUALITY is not.

Usage:
    from python.helpers.validators.tautological_test_detector import detect_tautological_tests
    result = detect_tautological_tests("/path/to/project")

Returns:
    None if no test files found.
    Dict with: tautological_count, findings (list), total_assertions (int).
"""

import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger("agix.tautological_test_detector")


# ─── Tautology Detection Patterns ────────────────────────────────────────
# These regex patterns detect assertions where the expected value is identical
# to the actual value — meaning the test asserts nothing about the code.

# Match expect('X').toBe('X') or expect('X').toEqual('X') — single quotes
_TAUTOLOGY_STRING_SINGLE = re.compile(
    r"""expect\(\s*'([^']+)'\s*\)\s*\.(?:toBe|toEqual|toContain|toStrictEqual)\(\s*'(\1)'\s*\)"""
)

# Match expect("X").toBe("X") or expect("X").toEqual("X") — double quotes
_TAUTOLOGY_STRING_DOUBLE = re.compile(
    r"""expect\(\s*"([^"]+)"\s*\)\s*\.(?:toBe|toEqual|toContain|toStrictEqual)\(\s*"\1"\s*\)"""
)

# Match expect(true).toBe(true) or expect(false).toBe(false) etc.
_TAUTOLOGY_BOOLEAN = re.compile(
    r"""expect\(\s*(true|false|null|undefined)\s*\)\s*\.(?:toBe|toEqual|toStrictEqual)\(\s*\1\s*\)"""
)

# Match expect(NUMBER).toBe(SAME_NUMBER)
_TAUTOLOGY_NUMBER = re.compile(
    r"""expect\(\s*(\d+(?:\.\d+)?)\s*\)\s*\.(?:toBe|toEqual|toStrictEqual)\(\s*\1\s*\)"""
)

# All tautology patterns with descriptive labels
_TAUTOLOGY_PATTERNS = [
    (_TAUTOLOGY_STRING_SINGLE, "string_tautology_single"),
    (_TAUTOLOGY_STRING_DOUBLE, "string_tautology_double"),
    (_TAUTOLOGY_BOOLEAN, "boolean_tautology"),
    (_TAUTOLOGY_NUMBER, "number_tautology"),
]

# Count total assertions (to report ratio)
_ASSERTION_PATTERN = re.compile(
    r"""expect\(.*?\)\s*\.(?:toBe|toEqual|toContain|toStrictEqual|"""
    r"""toBeInTheDocument|toBeDefined|toBeNull|toBeUndefined|"""
    r"""toHaveBeenCalled|toHaveBeenCalledWith|toMatch|toThrow|"""
    r"""toHaveLength|toBeTruthy|toBeFalsy|toBeGreaterThan|"""
    r"""toBeLessThan|toBeInstanceOf|toHaveProperty|toMatchSnapshot)"""
)

# Test file extensions
_TEST_EXTENSIONS = {".test.ts", ".test.tsx", ".test.js", ".test.jsx",
                    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx"}


def _is_test_file(filename: str) -> bool:
    """Check if a filename is a test file by extension."""
    for ext in _TEST_EXTENSIONS:
        if filename.endswith(ext):
            return True
    return False


def _find_test_files(project_dir: str) -> List[str]:
    """Find all test files in the project directory.

    Searches for *.test.{ts,tsx,js,jsx} and *.spec.{ts,tsx,js,jsx} files
    in any directory, including __tests__/ directories.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        List of absolute paths to test files.
    """
    test_files = []
    skip_dirs = {"node_modules", ".next", "dist", "build", ".git"}

    for root, dirs, files in os.walk(project_dir):
        # Prune directories we don't want to search
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for fname in files:
            if _is_test_file(fname):
                test_files.append(os.path.join(root, fname))

    return test_files


def _scan_file_for_tautologies(filepath: str, project_dir: str) -> tuple:
    """Scan a single test file for tautological assertions.

    Args:
        filepath: Absolute path to a test file.
        project_dir: Project root for computing relative paths.

    Returns:
        Tuple of (findings_list, total_assertion_count).
        Each finding: {"file": str, "line": int, "pattern": str}
    """
    findings = []
    total_assertions = 0

    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return findings, 0

    for line_no, line in enumerate(lines, 1):
        # Count total assertions
        total_assertions += len(_ASSERTION_PATTERN.findall(line))

        # Check each tautology pattern
        for pattern, label in _TAUTOLOGY_PATTERNS:
            matches = pattern.finditer(line)
            for match in matches:
                rel_path = os.path.relpath(filepath, project_dir)
                findings.append({
                    "file": rel_path,
                    "line": line_no,
                    "pattern": f"{label}: {match.group(0).strip()[:120]}",
                })

    return findings, total_assertions


def detect_tautological_tests(project_dir: str) -> Optional[Dict]:
    """Detect tautological test assertions in a project.

    Walks the project directory for test files and scans each for patterns
    like expect('X').toBe('X') that test nothing.

    WB-6 root cause: Test EXISTENCE is gated but test QUALITY is not.
    This function provides the missing quality check.

    Args:
        project_dir: Absolute path to the project root directory.

    Returns:
        None if no test files are found.
        Dict with:
            - tautological_count (int): Number of tautological assertions found.
            - findings (list): List of dicts with file, line, pattern for each finding.
            - total_assertions (int): Total assertion count across all test files.
    """
    test_files = _find_test_files(project_dir)

    if not test_files:
        return None

    all_findings = []
    total_assertions = 0

    for filepath in test_files:
        file_findings, file_assertions = _scan_file_for_tautologies(filepath, project_dir)
        all_findings.extend(file_findings)
        total_assertions += file_assertions

    return {
        "tautological_count": len(all_findings),
        "findings": all_findings,
        "total_assertions": total_assertions,
    }
