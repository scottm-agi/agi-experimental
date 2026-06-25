"""
Line-Number Corruption Detector

Defense-in-depth: Detects when file content appears to contain line-number
prefixes from `cat -n` or similar tools. These prefixes corrupt source files
when written back.

Patterns detected:
- `N: code` (colon-space, most common cat -n format)
- `     N\\tcode` (tab-separated, alternate cat -n format)

The detector requires ≥3 consecutive matching lines at the start of the
content to trigger, avoiding false positives from:
- Markdown numbered lists (1. Item)
- Code containing number constants
- Comments with numbered steps
"""
from __future__ import annotations

import re
from typing import Optional

# Pattern: line starts with digits followed by ': ' (cat -n colon format)
_COLON_PATTERN = re.compile(r"^\d+:\s")

# Pattern: line starts with optional whitespace, digits, then tab (cat -n tab format)
_TAB_PATTERN = re.compile(r"^\s*\d+\t")

# Minimum consecutive matching lines to trigger (avoids false positives)
_THRESHOLD = 3


def detect_line_number_corruption(content: str) -> Optional[str]:
    """
    Check if content appears to contain line-number prefixes.

    Args:
        content: The file content to check.

    Returns:
        Warning message if corruption detected, None otherwise.
    """
    if not content:
        return None

    lines = content.split("\n")
    if len(lines) < _THRESHOLD:
        return None

    # Check consecutive lines from the start
    consecutive_colon = 0
    consecutive_tab = 0

    for i, line in enumerate(lines[:20]):  # Only check first 20 lines
        if _COLON_PATTERN.match(line):
            consecutive_colon += 1
        else:
            # Reset only if we haven't met threshold yet
            if consecutive_colon < _THRESHOLD:
                consecutive_colon = 0

        if _TAB_PATTERN.match(line):
            consecutive_tab += 1
        else:
            if consecutive_tab < _THRESHOLD:
                consecutive_tab = 0

    if consecutive_colon >= _THRESHOLD or consecutive_tab >= _THRESHOLD:
        return (
            "⚠️ Content appears to contain line-number prefixes "
            "(likely from `cat -n` output). This will corrupt the file. "
            "Use the `read_file` tool instead of `cat -n` to read files."
        )

    return None
