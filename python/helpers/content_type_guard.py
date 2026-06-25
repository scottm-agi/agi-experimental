"""
Content Type Guard — Detects semantic mismatches between file extension and content.

RCA-262 Error 1: During truncated LLM responses, partial tool call output
can be erroneously concatenated with the NEXT tool call, causing CSS content
to be written to .ts files, email HTML to tsconfig.json, etc.

This guard provides an advisory warning (not a hard block) when the content
type doesn't match the file extension. Advisory because there are legitimate
edge cases (e.g., template literals containing HTML in .ts files).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional


# ── Extension → expected content signatures ──────────────────

# These patterns indicate content that is DEFINITIVELY a specific type.
# We use multiple signals to reduce false positives.

_CSS_SIGNALS = [
    re.compile(r"@tailwind\s+(base|components|utilities)", re.IGNORECASE),
    re.compile(r"@import\s+['\"]"),
    re.compile(r"[.#]\w+\s*\{[^}]*\}", re.DOTALL),  # CSS selectors with braces
    re.compile(r":\s*(flex|grid|block|none|absolute|relative)\s*;"),
    re.compile(r"(margin|padding|font-size|background-color)\s*:\s*"),
]

_HTML_SIGNALS = [
    re.compile(r"<html[\s>]", re.IGNORECASE),
    re.compile(r"<head[\s>].*</head>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<body[\s>]", re.IGNORECASE),
    re.compile(r"<!DOCTYPE\s+html>", re.IGNORECASE),
]

_TYPESCRIPT_JS_SIGNALS = [
    re.compile(r"^import\s+\{.*\}\s+from\s+['\"]", re.MULTILINE),
    re.compile(r"^export\s+(async\s+)?function\s+\w+", re.MULTILINE),
    re.compile(r"^export\s+(default\s+)?class\s+\w+", re.MULTILINE),
    re.compile(r":\s*(string|number|boolean|Promise|NextResponse)\b"),
    re.compile(r"(interface|type)\s+\w+\s*\{"),
]

_JSON_SIGNALS = [
    re.compile(r'^\s*\{', re.MULTILINE),
]

# Map file extension to content signatures that SHOULD NOT be present
_MISMATCH_RULES: dict[str, list[tuple[str, list[re.Pattern]]]] = {
    # TypeScript/JavaScript files should NOT contain raw CSS
    ".ts": [("CSS/stylesheet", _CSS_SIGNALS), ("HTML/email", _HTML_SIGNALS)],
    ".tsx": [("CSS/stylesheet", _CSS_SIGNALS), ("HTML/email", _HTML_SIGNALS)],
    ".js": [("CSS/stylesheet", _CSS_SIGNALS), ("HTML/email", _HTML_SIGNALS)],
    ".jsx": [("CSS/stylesheet", _CSS_SIGNALS), ("HTML/email", _HTML_SIGNALS)],
    # CSS files should NOT contain TypeScript/JS code
    ".css": [("TypeScript/JavaScript", _TYPESCRIPT_JS_SIGNALS)],
    ".scss": [("TypeScript/JavaScript", _TYPESCRIPT_JS_SIGNALS)],
    # JSON config files should NOT contain HTML or code
    ".json": [("HTML/email", _HTML_SIGNALS), ("CSS/stylesheet", _CSS_SIGNALS)],
}

# Minimum number of signals required to flag a mismatch
# (prevents false positives from template literals, inline styles, etc.)
_MIN_SIGNAL_THRESHOLD = 3


def detect_content_type_mismatch(
    file_path: str,
    content: str,
    threshold: int = _MIN_SIGNAL_THRESHOLD,
) -> Optional[str]:
    """
    Check if file content semantically matches the file extension.

    Args:
        file_path: The target file path (only the extension matters).
        content: The file content to validate.
        threshold: Minimum number of signals needed to flag a mismatch.

    Returns:
        Advisory warning string if mismatch detected, None if OK.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in _MISMATCH_RULES:
        return None  # Unknown extension — no opinion

    # Special case: JSON files — try parsing first
    if ext == ".json":
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
                return None  # Valid JSON → no mismatch
            except (json.JSONDecodeError, ValueError):
                pass  # Not valid JSON, continue checking

    for content_type_name, signals in _MISMATCH_RULES[ext]:
        matches = sum(1 for sig in signals if sig.search(content))
        if matches >= threshold:
            return (
                f"⚠️ CONTENT TYPE MISMATCH: File '{os.path.basename(file_path)}' "
                f"(extension {ext}) appears to contain {content_type_name} content "
                f"({matches} signals detected). This may indicate truncation-induced "
                f"cross-contamination. Verify you are writing the correct content "
                f"to the correct file."
            )

    return None
