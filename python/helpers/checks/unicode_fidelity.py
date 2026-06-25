"""
Unicode/Emoji Fidelity Check — Detects encoding corruption in generated files.

F-5 Fix: Emoji characters rendered as `??` in generated code. This module
provides a deterministic (L1-only) check that scans text files for common
encoding corruption patterns:

1. Consecutive question marks (3+) — almost never valid code
2. Double question marks inside string literals — likely corrupted emoji
3. Unicode replacement character (U+FFFD) — always corruption
4. Null bytes in text files — always corruption

False positive protection:
- Single `?` → never flagged (ternary, URL query params)
- `??` outside strings → NOT flagged (JS nullish coalescing)
- `?.` → NOT flagged (JS optional chaining)
- `??=` → NOT flagged (JS nullish assignment)
- Non-text files → skipped entirely
"""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger("agix.unicode_fidelity")

# Text file extensions to check
TEXT_EXTENSIONS: frozenset = frozenset({
    ".tsx", ".jsx", ".ts", ".js", ".html", ".css",
    ".md", ".json", ".py", ".txt", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".sh", ".env",
})

# Pre-compiled patterns

# Pattern 1: Three or more consecutive question marks (almost never valid code)
_TRIPLE_QUESTION_RE = re.compile(r"\?{3,}")

# Pattern 2: Double question marks inside string literals.
# Matches ?? that appears inside single quotes, double quotes, or backticks.
# This catches 'Hello ?? World' but NOT `value ?? fallback` (outside strings).
# We look for a quote char, then content including ??, then a closing quote.
_DOUBLE_Q_IN_STRING_RE = re.compile(
    r"""(?:"""
    r"""'[^']*\?\?[^']*'"""      # single-quoted string with ??
    r"""|"[^"]*\?\?[^"]*\""""    # double-quoted string with ??
    r"""|`[^`]*\?\?[^`]*`"""     # template literal with ??
    r""")"""
)

# Pattern 3: Unicode replacement character (U+FFFD)
_REPLACEMENT_CHAR_RE = re.compile("\ufffd")

# Pattern 4: Null byte
_NULL_BYTE_RE = re.compile("\x00")


def check_unicode_fidelity(file_path: str) -> Dict:
    """Check a file for unicode/encoding corruption.

    Args:
        file_path: Absolute path to the file to check.

    Returns:
        {
            "pass": bool,
            "issues": [
                {
                    "line": int,
                    "column": int,
                    "type": str,  # "consecutive_question_marks", "double_question_in_string",
                                  # "replacement_character", "null_byte"
                    "snippet": str,  # Short excerpt around the issue
                }
            ]
        }
    """
    result: Dict = {"pass": True, "issues": []}

    # Check if file exists
    if not os.path.isfile(file_path):
        return result

    # Check extension — skip non-text files
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in TEXT_EXTENSIONS:
        return result

    # Read file content
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except (IOError, OSError) as e:
        logger.debug(f"[UNICODE FIDELITY] Could not read {file_path}: {e}")
        return result

    # Try decoding as UTF-8 (with error replacement to catch issues)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        # If we can't even decode it, try with errors='replace' to find issues
        content = raw.decode("utf-8", errors="replace")

    if not content:
        return result

    issues: List[Dict] = []
    lines = content.split("\n")

    for line_num_0, line in enumerate(lines):
        line_num = line_num_0 + 1  # 1-indexed

        # Check 1: Triple+ consecutive question marks (always suspicious)
        for match in _TRIPLE_QUESTION_RE.finditer(line):
            issues.append({
                "line": line_num,
                "column": match.start() + 1,
                "type": "consecutive_question_marks",
                "snippet": _excerpt(line, match.start(), match.end()),
            })

        # Check 2: Double question marks inside string literals
        # Skip if we already found triple+ on this line (already flagged)
        if not _TRIPLE_QUESTION_RE.search(line):
            for match in _DOUBLE_Q_IN_STRING_RE.finditer(line):
                issues.append({
                    "line": line_num,
                    "column": match.start() + 1,
                    "type": "double_question_in_string",
                    "snippet": _excerpt(line, match.start(), match.end()),
                })

        # Check 3: Unicode replacement character (U+FFFD)
        for match in _REPLACEMENT_CHAR_RE.finditer(line):
            issues.append({
                "line": line_num,
                "column": match.start() + 1,
                "type": "replacement_character",
                "snippet": _excerpt(line, match.start(), match.end()),
            })

        # Check 4: Null bytes
        for match in _NULL_BYTE_RE.finditer(line):
            issues.append({
                "line": line_num,
                "column": match.start() + 1,
                "type": "null_byte",
                "snippet": _excerpt(line, match.start(), match.end()),
            })

    result["issues"] = issues
    result["pass"] = len(issues) == 0
    return result


def _excerpt(line: str, start: int, end: int, context: int = 20) -> str:
    """Extract a short excerpt around the matched region."""
    excerpt_start = max(0, start - context)
    excerpt_end = min(len(line), end + context)
    excerpt = line[excerpt_start:excerpt_end]
    if excerpt_start > 0:
        excerpt = "..." + excerpt
    if excerpt_end < len(line):
        excerpt = excerpt + "..."
    return excerpt
