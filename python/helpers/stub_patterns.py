"""
Universal Stub Patterns — Shared Module (DUP-2)
================================================

Single source of truth for all stub/placeholder detection patterns
used across the AGIX framework. Consolidates patterns from:

1. post_execution_req_verifier.py  — _STUB_PATTERNS + endpoint stubs (OVL-2 merged)
2. bdd_implementation_verifier.py  — _DEFERRED_STUB_PATTERNS
3. _33_stub_detection_gate.py      — STUB_PATTERNS

Note: stub_endpoint_detector.py was merged INTO post_execution_req_verifier.py
by OVL-2. Its patterns are now sourced from this shared module via the verifier.

Provides:
- UNIVERSAL_STUB_PATTERNS: list[re.Pattern] — The union of ALL unique patterns
- STUB_PATTERN_CATEGORIES: dict mapping category name → list of patterns
- is_stub_content(text) → bool — Quick check if text matches any stub pattern
- find_stubs_in_text(text) → list[dict] — Returns all matches with line numbers

Consumers should import from here instead of defining local pattern lists.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# ──────────────────────────────────────────────────────────────────────
# Pattern Categories
# ──────────────────────────────────────────────────────────────────────
# Each category groups related patterns for documentation and filtering.
# The UNIVERSAL_STUB_PATTERNS list is the flat union of all categories.


# Category 1: TODO/FIXME/HACK/XXX markers in comments
_TODO_MARKERS = [
    re.compile(r'(?://|#)\s*TODO', re.IGNORECASE),
    re.compile(r'(?://|#)\s*FIXME', re.IGNORECASE),
    re.compile(r'(?://|#)\s*HACK', re.IGNORECASE),
    re.compile(r'(?://|#)\s*XXX', re.IGNORECASE),
    # Word-boundary variants (from stub_endpoint_detector)
    re.compile(r'\bTODO\b', re.IGNORECASE),
    re.compile(r'\bFIXME\b', re.IGNORECASE),
    re.compile(r'\bPLACEHOLDER\b', re.IGNORECASE),
    re.compile(r'\bIMPLEMENT\s+ME\b', re.IGNORECASE),
]

# Category 2: Empty/null returns
_EMPTY_RETURNS = [
    re.compile(r'return\s*\[\]\s*;?\s*$'),
    re.compile(r'return\s*\{\}\s*;?\s*$'),
]

# Category 3: Not-implemented signals
_NOT_IMPLEMENTED = [
    re.compile(r'not\s+implemented', re.IGNORECASE),
    re.compile(r'\bNOT\s+IMPLEMENTED\b', re.IGNORECASE),
    re.compile(r'raise\s+NotImplementedError'),
    re.compile(r'throw\s+new\s+Error\s*\(\s*["\']not\s+implemented', re.IGNORECASE),
    re.compile(r'^\s+pass\s*$', re.MULTILINE),
]

# Category 4: Placeholder/template text
_PLACEHOLDER_MARKERS = [
    re.compile(r'placeholder', re.IGNORECASE),
    # Stub values like STUB_ID, STUB_URL (SS-9)
    re.compile(r'\bSTUB_\w+\b'),
    # Skeleton comments from stub detection gate
    re.compile(r'#\s*skeleton'),
]

# Category 5: Deferred implementation stubs (from BDD verifier)
# These indicate code agent wrote a comment instead of implementing
_DEFERRED_STUBS = [
    re.compile(r'(?://|#).*\b[Ii]n a real\b', re.IGNORECASE),
    re.compile(r'(?://|#).*\b[Ff]or this phase\b', re.IGNORECASE),
    re.compile(r'(?://|#).*\b[Tt]his (?:would|should)\s+(?:use|call|connect|integrate)\b', re.IGNORECASE),
    re.compile(r'(?://|#).*\bplaceholder\s+implementation\b', re.IGNORECASE),
    re.compile(r'(?://|#)\s*TODO:?\s*(?:integrate|implement|call|connect)\b', re.IGNORECASE),
    re.compile(r'(?://|#)\s*FIXME:?\s*(?:integrate|implement)\b', re.IGNORECASE),
    re.compile(r'(?://|#).*\btemplate logic\b', re.IGNORECASE),
    re.compile(r'(?://|#).*\bwill implement\b', re.IGNORECASE),
    re.compile(r'(?://|#).*\bstub\b', re.IGNORECASE),
]

# Category 6: Framework scaffold boilerplate (F-3, RCA-461)
# Default metadata/content from scaffold generators that must be replaced
_SCAFFOLD_BOILERPLATE = [
    re.compile(r'Create Next App', re.IGNORECASE),
    re.compile(r'Get started by editing', re.IGNORECASE),
    re.compile(r'Powered by.*Vercel', re.IGNORECASE),
    re.compile(r'Welcome to.*Create React App', re.IGNORECASE),
    re.compile(r'Vite \+ React', re.IGNORECASE),
    re.compile(r'Deploy your Next\.js', re.IGNORECASE),
]


# ──────────────────────────────────────────────────────────────────────
# Category map (for filtering by concern)
# ──────────────────────────────────────────────────────────────────────

STUB_PATTERN_CATEGORIES: Dict[str, List[re.Pattern]] = {
    "todo_markers": _TODO_MARKERS,
    "empty_returns": _EMPTY_RETURNS,
    "not_implemented": _NOT_IMPLEMENTED,
    "placeholder_markers": _PLACEHOLDER_MARKERS,
    "deferred_stubs": _DEFERRED_STUBS,
    "scaffold_boilerplate": _SCAFFOLD_BOILERPLATE,
}


# ──────────────────────────────────────────────────────────────────────
# Flat universal list (union of all categories)
# ──────────────────────────────────────────────────────────────────────

UNIVERSAL_STUB_PATTERNS: List[re.Pattern] = []
for _category_patterns in STUB_PATTERN_CATEGORIES.values():
    UNIVERSAL_STUB_PATTERNS.extend(_category_patterns)


# ──────────────────────────────────────────────────────────────────────
# Convenience helpers
# ──────────────────────────────────────────────────────────────────────


def is_stub_content(text: str) -> bool:
    """Return True if 'text' matches any known stub indicator pattern.

    This is the simple boolean API — for detailed results, use
    `find_stubs_in_text()` below.
    """
    for pattern in UNIVERSAL_STUB_PATTERNS:
        if pattern.search(text):
            return True
    return False


def find_stubs_in_text(text: str) -> List[Dict[str, Any]]:
    """Scan text line-by-line and return every stub indicator found.

    Each result is a dict with:
        - line: int — 1-based line number
        - text: str — the matched line (stripped)
        - pattern: str — the regex pattern that matched
        - category: str — the category name (e.g., 'todo_markers')
    """
    results: List[Dict[str, Any]] = []
    lines = text.splitlines()

    for line_num, line in enumerate(lines, 1):
        for category_name, patterns in STUB_PATTERN_CATEGORIES.items():
            for pattern in patterns:
                if pattern.search(line):
                    results.append({
                        "line": line_num,
                        "text": line.strip(),
                        "pattern": pattern.pattern,
                        "category": category_name,
                    })
                    break  # One match per category per line is enough
            # Don't break outer loop — a line could match multiple categories

    return results


# ──────────────────────────────────────────────────────────────────────
# F-2 (RCA-461): File-level project stub scanner
# ──────────────────────────────────────────────────────────────────────

# Directories to skip during project scanning
_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".next", "__pycache__", ".cache",
    "dist", "build", ".svelte-kit", "coverage", ".turbo",
    "vendor", ".venv", "venv", "env",
})

# Source file extensions to scan
_SOURCE_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".py", ".rb", ".go",
    ".java", ".rs", ".svelte", ".vue", ".astro", ".css",
    ".scss", ".html", ".prisma", ".graphql",
})


def scan_project_for_stubs(project_dir: str) -> Dict[str, Any]:
    """Recursively scan project source files for stub patterns.

    F-2 (RCA-461): Restores file-level stub scanning. Validates actual
    project FILES, not just response text.

    Args:
        project_dir: Absolute path to the project root directory.

    Returns:
        Dict with total_stubs, files_with_stubs, total_files_scanned,
        stub_ratio, and stubs list (capped at 50).
    """
    import os

    all_stubs: List[Dict[str, Any]] = []
    files_with_stubs = 0
    total_files_scanned = 0

    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in _SOURCE_EXTENSIONS:
                continue

            filepath = os.path.join(root, filename)
            total_files_scanned += 1

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (IOError, OSError):
                continue

            matches = find_stubs_in_text(content)
            if matches:
                files_with_stubs += 1
                rel_path = os.path.relpath(filepath, project_dir)
                for match in matches:
                    if len(all_stubs) < 50:
                        all_stubs.append({
                            "file": rel_path,
                            "line": match["line"],
                            "text": match["text"],
                            "pattern": match["pattern"],
                            "category": match["category"],
                        })

    stub_ratio = (
        files_with_stubs / total_files_scanned
        if total_files_scanned > 0
        else 0.0
    )

    return {
        "total_stubs": len(all_stubs),
        "files_with_stubs": files_with_stubs,
        "total_files_scanned": total_files_scanned,
        "stub_ratio": stub_ratio,
        "stubs": all_stubs,
    }


