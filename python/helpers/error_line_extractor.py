"""Intelligent error-aware line extraction from build output.

RCA-365 F-12: Replaces blind positional truncation with signal-aware
extraction. Instead of cutting build output at an arbitrary character
position (which destroys error signatures needed for pattern matching),
this module extracts ONLY lines containing error signals, plus surrounding
context lines.

Functions:
    extract_error_lines(raw_output, max_chars, context_lines) → str
"""

from __future__ import annotations

import re
from typing import List, Set, Tuple


# ── Error signal patterns ───────────────────────────────────────────────
# These compiled patterns identify lines that contain error-relevant info.
# Order doesn't matter — all patterns are checked against every line.

_ERROR_SIGNAL_PATTERNS: List[re.Pattern] = [
    # General error keywords
    re.compile(r"\bError\b", re.IGNORECASE),
    re.compile(r"\berror:", re.IGNORECASE),
    re.compile(r"\bTypeError\b", re.IGNORECASE),
    re.compile(r"\bCannot\b", re.IGNORECASE),
    re.compile(r"\bModule not found\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bFAIL\b"),

    # Stack trace lines
    re.compile(r"^\s+at\s+", re.MULTILINE),

    # Next.js specific
    re.compile(r"\bprerendering\b", re.IGNORECASE),
    re.compile(r"\buseContext\b"),
    re.compile(r"\buseClient\b"),

    # Build tool error prefixes (>, ×, ✖)
    re.compile(r"^>(?:\s|\d)", re.MULTILINE),
    re.compile(r"^×", re.MULTILINE),
    re.compile(r"^✖", re.MULTILINE),

    # File:line references like filename.ts(10,5) or filename.tsx:10:5
    re.compile(r"\w+\.\w+\(\d+,\d+\)"),
    re.compile(r"\w+\.\w+:\d+:\d+"),
]


def _is_error_line(line: str) -> bool:
    """Check if a line matches any error signal pattern."""
    for pattern in _ERROR_SIGNAL_PATTERNS:
        if pattern.search(line):
            return True
    return False


def extract_error_lines(
    raw_output: str,
    max_chars: int = 5000,
    context_lines: int = 2,
) -> str:
    """Extract error-relevant lines from build output with surrounding context.

    Instead of blind positional truncation, scans every line for error signals
    and extracts matching lines plus ``context_lines`` lines above and below.

    Error signal patterns matched:
    - Lines containing: Error, error:, TypeError, Cannot, Module not found, failed, FAIL
    - Lines containing: at (stack traces)
    - Lines containing: prerendering, useContext, useClient (Next.js specific)
    - Lines starting with: >, ×, ✖ (build tool error prefixes)
    - Lines containing file:line references like filename.ts(line,col)

    Args:
        raw_output: The full build stdout/stderr text.
        max_chars: Maximum characters in the returned string (default 5000).
        context_lines: Number of surrounding lines to include around each
            error line (default 2).

    Returns:
        Extracted error lines capped at max_chars, or empty string if no
        error lines found.
    """
    if not raw_output or not raw_output.strip():
        return ""

    lines = raw_output.split("\n")
    total_lines = len(lines)

    # ── Phase 1: Identify all error line indices ────────────────────────
    error_indices: Set[int] = set()
    for i, line in enumerate(lines):
        if _is_error_line(line):
            error_indices.add(i)

    if not error_indices:
        return ""

    # ── Phase 2: Expand with context lines ──────────────────────────────
    include_indices: Set[int] = set()
    for idx in error_indices:
        start = max(0, idx - context_lines)
        end = min(total_lines - 1, idx + context_lines)
        for i in range(start, end + 1):
            include_indices.add(i)

    # ── Phase 3: Build output maintaining original order ────────────────
    sorted_indices = sorted(include_indices)
    result_lines: List[str] = []
    for idx in sorted_indices:
        result_lines.append(lines[idx])

    result = "\n".join(result_lines)

    # ── Phase 4: Cap at max_chars ───────────────────────────────────────
    if len(result) > max_chars:
        result = result[:max_chars]

    return result
