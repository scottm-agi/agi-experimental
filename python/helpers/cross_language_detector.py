"""
Cross-Language Syntax Detector.

U-11 Fix: No programmatic gate validates that written code uses syntax
appropriate for the target file's language. The L0 advisory fix only covers
the `code` profile — hacker, mcp_builder, debug, security_auditor have zero
cross-language guidance. 0 of 44 tool_execute_after extensions check for
cross-language contamination.

This module provides a deterministic regex-based scanner that maps file
extensions to anti-patterns from other languages.
"""
from __future__ import annotations

import re
from typing import List, Dict, Optional

# ── Anti-Pattern Maps ──────────────────────────────────────────────────
# Each key is a file extension group, value is a list of (pattern, description)
# tuples that indicate contamination from another language.

# Python patterns that should NOT appear in JS/TS files
_PYTHON_IN_JS_PATTERNS = [
    (re.compile(r'^\s*def\s+\w+\s*\(', re.MULTILINE), "Python function definition (def)"),
    (re.compile(r'^\s*class\s+\w+\s*:', re.MULTILINE), "Python class with colon"),
    (re.compile(r'^\s*import\s+\w+\s*$', re.MULTILINE), "Python bare import"),
    (re.compile(r'^\s*from\s+\w+\s+import\s+', re.MULTILINE), "Python from...import"),
    (re.compile(r'^\s*elif\s+', re.MULTILINE), "Python elif (not else if)"),
    (re.compile(r'\bprint\s*\(', re.MULTILINE), "Python print() call"),
    (re.compile(r'\bself\.\w+', re.MULTILINE), "Python self.attribute"),
    (re.compile(r'^\s*except\s+\w+', re.MULTILINE), "Python except clause"),
    (re.compile(r'\bTrue\b|\bFalse\b|\bNone\b', re.MULTILINE), "Python True/False/None"),
]

# JS/TS patterns that should NOT appear in Python files
_JS_IN_PYTHON_PATTERNS = [
    (re.compile(r'\bconst\s+\w+\s*=', re.MULTILINE), "JavaScript const declaration"),
    (re.compile(r'\blet\s+\w+\s*=', re.MULTILINE), "JavaScript let declaration"),
    (re.compile(r'\bvar\s+\w+\s*=', re.MULTILINE), "JavaScript var declaration"),
    (re.compile(r'=>', re.MULTILINE), "JavaScript arrow function"),
    (re.compile(r'\bconsole\.log\s*\(', re.MULTILINE), "JavaScript console.log"),
    (re.compile(r'function\s+\w+\s*\(', re.MULTILINE), "JavaScript function declaration"),
    (re.compile(r'\bnull\b', re.MULTILINE), "JavaScript null (not None)"),
    (re.compile(r'\bundefined\b', re.MULTILINE), "JavaScript undefined"),
]

# Map file extensions to anti-pattern checks
_EXTENSION_MAP: Dict[str, List[tuple]] = {
    ".tsx": _PYTHON_IN_JS_PATTERNS,
    ".jsx": _PYTHON_IN_JS_PATTERNS,
    ".ts": _PYTHON_IN_JS_PATTERNS,
    ".js": _PYTHON_IN_JS_PATTERNS,
    ".mjs": _PYTHON_IN_JS_PATTERNS,
    ".py": _JS_IN_PYTHON_PATTERNS,
}

# Minimum contamination signals to trigger a warning
_MIN_SIGNALS = 3


def detect_cross_language_contamination(
    filename: str,
    content: str,
    min_signals: int = _MIN_SIGNALS,
) -> Optional[Dict]:
    """Check if file content has syntax from the wrong programming language.

    Args:
        filename: The filename (used to determine expected language).
        content: The file content to scan.
        min_signals: Minimum number of contamination signals to report.

    Returns:
        None if no contamination detected.
        Dict with:
            - contaminated: bool
            - signals: list of description strings
            - expected_language: str
            - detected_language: str
            - severity: "high" | "medium" | "low"
    """
    if not filename or not content:
        return None

    # Determine expected language from extension
    ext = ""
    for e in _EXTENSION_MAP:
        if filename.endswith(e):
            ext = e
            break

    if not ext:
        return None  # Unknown extension — skip

    patterns = _EXTENSION_MAP[ext]
    signals = []

    for pattern, description in patterns:
        matches = pattern.findall(content)
        if matches:
            signals.append(description)

    if len(signals) < min_signals:
        return None  # Below threshold

    # Determine languages
    if ext in {".tsx", ".jsx", ".ts", ".js", ".mjs"}:
        expected = "JavaScript/TypeScript"
        detected = "Python"
    else:
        expected = "Python"
        detected = "JavaScript/TypeScript"

    # Severity based on signal count
    if len(signals) >= 6:
        severity = "high"
    elif len(signals) >= 4:
        severity = "medium"
    else:
        severity = "low"

    return {
        "contaminated": True,
        "signals": signals,
        "signal_count": len(signals),
        "expected_language": expected,
        "detected_language": detected,
        "severity": severity,
    }


def check_file_for_contamination(filepath: str) -> Optional[Dict]:
    """Convenience wrapper that reads a file and checks for contamination."""
    import os
    if not os.path.isfile(filepath):
        return None

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return None

    return detect_cross_language_contamination(
        os.path.basename(filepath), content
    )
