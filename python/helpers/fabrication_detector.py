"""
Fabrication Detector — Inverse Contract Assertion (Gap-7 Universal Fix).

Detects values in generated source code that do NOT exist in the user's
original prompt or content_manifest.json. These are "fabricated" values —
the agent invented pricing, URLs, or brand names the user never specified.

Complements the existing Contract Assertions (check 0.06) which does the
FORWARD check: "is this prompt value in source?" (missing = FAIL).
This module does the INVERSE: "is this source value in the prompt?" (fabrication = FAIL).

Architecture:
    Layer 1 (deterministic): Regex scan source files for price patterns ($X.XX/mo),
    URLs (https://...), and other value patterns. Cross-reference against prompt text.
    Layer 2 (LLM): Reserved for future — semantic similarity for near-matches.

Reuses regex patterns from prompt_contract_parser.py for consistency.

Usage:
    from python.helpers.fabrication_detector import detect_fabricated_values

    result = detect_fabricated_values(source_files, original_prompt)
    # Returns: {"clean": bool, "fabricated": [...], "matched": [...]}
"""

import logging
import os
import re
from typing import Dict, List, Optional, Set

from python.helpers.source_scanner import read_project_files, EXCLUDE_DIRS

logger = logging.getLogger("agix.fabrication_detector")


# ── Reuse patterns from prompt_contract_parser for consistency ────────

# Prices: $N, $N.NN, $N/mo, $N/month, $N/yr, etc.
_PRICE_RE = re.compile(
    r'\$\d+(?:,\d{3})*(?:\.\d{1,2})?(?:/(?:mo|month|yr|year|week|day|hr|hour|user|seat)\w*)?',
    re.IGNORECASE,
)

# URLs: full http(s) URLs
_URL_RE = re.compile(
    r'https?://[^\s)>"\'<,\]]+',
    re.IGNORECASE,
)

# ── Exclusion patterns (NOT fabrication) ─────────────────────────────

# Dev/localhost URLs that are normal in generated code
_DEV_URL_PATTERNS = re.compile(
    r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|'
    r'\[::1\]|host\.docker\.internal)',
    re.IGNORECASE,
)

# Framework/boilerplate URLs that appear in scaffolds
_BOILERPLATE_URL_PATTERNS = re.compile(
    r'https?://(?:'
    r'nextjs\.org|vercel\.com|reactjs\.org|react\.dev|'
    r'tailwindcss\.com|fonts\.googleapis\.com|'
    r'github\.com/vercel|npmjs\.com|'
    r'schema\.org|w3\.org|'
    r'cdn\.jsdelivr\.net|unpkg\.com|'
    r'fonts\.gstatic\.com'
    r')',
    re.IGNORECASE,
)

# CSS/numeric values that look like prices but aren't
_CSS_VALUE_CONTEXT = re.compile(
    r'(?:px|em|rem|vh|vw|%|deg|ms|s)\s*[;,}\)]',
    re.IGNORECASE,
)

# Lines that are clearly comments or imports (reduce false positives)
_COMMENT_LINE_RE = re.compile(r'^\s*(?://|/\*|\*|#|<!--)')


def _normalize_price(price: str) -> str:
    """Normalize a price for comparison: strip whitespace, lowercase."""
    return price.strip().lower()


def _extract_prices_from_text(text: str) -> Set[str]:
    """Extract all price patterns from text, normalized."""
    prices = set()
    for m in _PRICE_RE.finditer(text):
        prices.add(_normalize_price(m.group()))
    return prices


def _extract_urls_from_text(text: str) -> Set[str]:
    """Extract all URLs from text, normalized (lowercase, strip trailing punct)."""
    urls = set()
    for m in _URL_RE.finditer(text):
        url = m.group().rstrip('.,;:!?)"\'>]')
        urls.add(url.lower())
    return urls


def _is_css_context(line: str, match_start: int) -> bool:
    """Check if a $ value is in a CSS context (e.g., var(--price-$0))."""
    # Check if line contains CSS-like patterns near the match
    after = line[match_start:]
    if _CSS_VALUE_CONTEXT.search(after[:30]):
        return True
    # Check for CSS property patterns
    if re.search(r':\s*\$', line[:match_start + 5]):
        return True
    return False


def _is_dev_url(url: str) -> bool:
    """Check if URL is a development/localhost URL."""
    return bool(_DEV_URL_PATTERNS.match(url))


def _is_boilerplate_url(url: str) -> bool:
    """Check if URL is a framework/boilerplate URL."""
    return bool(_BOILERPLATE_URL_PATTERNS.search(url))


def _normalize_source_files(source_files) -> Dict[str, str]:
    """Normalize source_files to Dict[str, str] regardless of input format.

    scan_project_sources() returns List[Dict] with keys {path, content, lines}.
    The fabrication detector expects Dict[str, str] (path → content).
    This function bridges the gap so both formats work.

    Args:
        source_files: Either Dict[str, str] or List[Dict] from scan_project_sources().

    Returns:
        Dict[str, str] mapping file paths to file contents.
    """
    if isinstance(source_files, dict):
        return source_files

    if isinstance(source_files, list):
        normalized: Dict[str, str] = {}
        for entry in source_files:
            if isinstance(entry, dict) and "path" in entry and "content" in entry:
                normalized[entry["path"]] = entry["content"]
        return normalized

    return {}


def detect_fabricated_values(
    source_files,
    original_prompt: str,
    manifest: Optional[dict] = None,
) -> dict:
    """Detect values in source code that are NOT in the original prompt.

    Args:
        source_files: Dict of {relative_path: file_content} OR List[Dict] from
            scan_project_sources() with keys {path, content, lines}. Both
            formats are accepted and normalized internally.
        original_prompt: The raw user prompt text.
        manifest: Optional content_manifest.json dict for additional cross-reference.

    Returns:
        {
            "clean": bool — True if no fabricated values found,
            "fabricated": [
                {"type": "price", "value": "$99/mo", "file": "src/pricing.tsx", "line": 5},
                ...
            ],
            "matched": [
                {"type": "price", "value": "$49/mo", "file": "src/pricing.tsx", "line": 3},
                ...
            ],
        }
    """
    # Normalize source_files to Dict[str, str] — handles both Dict and List[Dict]
    source_files = _normalize_source_files(source_files)

    # Extract all known values from prompt + manifest
    prompt_prices = _extract_prices_from_text(original_prompt)
    prompt_urls = _extract_urls_from_text(original_prompt)

    # Also extract from manifest if provided
    if manifest and isinstance(manifest, dict):
        manifest_str = _flatten_manifest_to_text(manifest)
        prompt_prices |= _extract_prices_from_text(manifest_str)
        prompt_urls |= _extract_urls_from_text(manifest_str)

    fabricated: List[dict] = []
    matched: List[dict] = []

    for file_path, content in source_files.items():
        # Skip test files, config files, lock files
        if _should_skip_file(file_path):
            continue

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            # Skip comment lines
            if _COMMENT_LINE_RE.match(line):
                continue

            # Check prices in this line
            for m in _PRICE_RE.finditer(line):
                price = _normalize_price(m.group())

                # Skip CSS/numeric contexts
                if _is_css_context(line, m.start()):
                    continue

                # Skip trivial prices ($0, $1 used in logic)
                if price in ("$0", "$1", "$0.00", "$1.00"):
                    continue

                finding = {
                    "type": "price",
                    "value": m.group(),
                    "file": file_path,
                    "line": line_num,
                }

                if price in prompt_prices:
                    matched.append(finding)
                else:
                    fabricated.append(finding)

            # Check URLs in this line
            for m in _URL_RE.finditer(line):
                url = m.group().rstrip('.,;:!?)"\'>]')
                url_lower = url.lower()

                # Exclude dev/localhost URLs
                if _is_dev_url(url):
                    continue

                # Exclude framework boilerplate URLs
                if _is_boilerplate_url(url):
                    continue

                finding = {
                    "type": "url",
                    "value": url,
                    "file": file_path,
                    "line": line_num,
                }

                if url_lower in prompt_urls:
                    matched.append(finding)
                else:
                    fabricated.append(finding)

    return {
        "clean": len(fabricated) == 0,
        "fabricated": fabricated,
        "matched": matched,
    }


def _flatten_manifest_to_text(manifest: dict) -> str:
    """Recursively flatten manifest dict to a text string for pattern extraction."""
    parts: List[str] = []

    def _walk(obj):
        if isinstance(obj, str):
            parts.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(manifest)
    return " ".join(parts)


def _should_skip_file(file_path: str) -> bool:
    """Check if a file should be skipped during fabrication scan."""
    # Test files are allowed to have mock data
    if any(p in file_path for p in (
        ".test.", ".spec.", "__tests__", "test_", "tests/",
        "fixtures/", "mocks/", "__mocks__",
    )):
        return True

    # Config/lock/styling files (styling files contain boilerplate URLs)
    if any(file_path.endswith(ext) for ext in (
        ".json", ".lock", ".yaml", ".yml", ".toml",
        ".env", ".env.local", ".env.example",
        ".config.ts", ".config.js", ".config.mjs",
        ".css", ".scss", ".sass",
    )):
        return True

    # node_modules, .next, etc.
    if any(d in file_path for d in (
        "node_modules/", ".next/", ".git/", "__pycache__/",
        "dist/", "build/", ".turbo/", ".vercel/",
    )):
        return True

    return False


# ── U-6 (ITR-29): Mock Data Source Scanner ────────────────────────────────
# Detects hardcoded mock data patterns in source files that indicate the
# code agent baked in fake data instead of wiring real API/DB sources.

# Fake person names commonly used by agents as mock data
_FAKE_NAMES = re.compile(
    r'\b(?:Alice\s+Smith|Bob\s+Jones|Jane\s+Doe|John\s+Doe|Test\s+User)\b',
    re.IGNORECASE,
)

# Hardcoded string IDs like 'r1', 'r2', 'r3' in arrays
_HARDCODED_ID_SEQ = re.compile(
    r"""['"]([a-z])\d+['"]\s*[,\]]""",
    re.IGNORECASE,
)

# Smoke test / TODO comments indicating mock data
_SMOKE_TEST_COMMENT = re.compile(
    r'//\s*[Ff]or\s+smoke\s+test',
)
_TODO_REAL_DATA = re.compile(
    r'//\s*TODO:\s*use\s+real\s+data',
    re.IGNORECASE,
)

# Source file extensions to scan
_SOURCE_EXTENSIONS = {'.ts', '.tsx', '.js', '.jsx'}


def detect_mock_data_in_source(project_dir: str) -> dict:
    """Scan source files for hardcoded mock data patterns.

    Detects patterns that indicate an agent baked in fake data instead of
    wiring real API/database sources:
    - Fake person names: Alice Smith, Bob Jones, Jane Doe, John Doe, Test User
    - Hardcoded ID sequences: 'r1', 'r2', 'r3' (3+ sequential string IDs)
    - Smoke test comments: '// For smoke test'
    - TODO-real-data comments: '// TODO: use real data'

    Excludes test files, __tests__ dirs, node_modules, .next, dist, build.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        {
            "clean": bool — True if no mock data patterns found,
            "mock_patterns": [
                {"pattern": str, "file": str, "line": int, "context": str},
                ...
            ]
        }
    """
    mock_patterns: List[dict] = []

    # OVL-3: Use centralized scanner to read project files.
    # Custom skip_dirs merge the default set with test/mock directories.
    _extra_skip = {'__tests__', '__mocks__'}
    files = read_project_files(
        project_dir,
        extensions=_SOURCE_EXTENSIONS,
        skip_dirs=EXCLUDE_DIRS | _extra_skip,
    )

    for rel_path, content in files.items():
        # Skip test files
        fname = os.path.basename(rel_path)
        if any(marker in fname for marker in (
            '.test.', '.spec.', 'test_', '_test.',
        )):
            continue

        # Skip files inside test/mock/fixture directories
        if any(d in rel_path for d in (
            '__tests__', '__mocks__', 'fixtures/', 'mocks/',
            'node_modules/',
        )):
            continue

        lines = content.split('\n')
        for line_num, line in enumerate(lines, 1):
            # Check fake names
            if _FAKE_NAMES.search(line):
                mock_patterns.append({
                    'pattern': 'fake_name',
                    'file': rel_path,
                    'line': line_num,
                    'context': line.strip()[:120],
                })

            # Check smoke test comments
            if _SMOKE_TEST_COMMENT.search(line):
                mock_patterns.append({
                    'pattern': 'smoke_test_comment',
                    'file': rel_path,
                    'line': line_num,
                    'context': line.strip()[:120],
                })

            # Check TODO real data comments
            if _TODO_REAL_DATA.search(line):
                mock_patterns.append({
                    'pattern': 'todo_real_data',
                    'file': rel_path,
                    'line': line_num,
                    'context': line.strip()[:120],
                })

        # Check for hardcoded ID sequences (3+ sequential string IDs)
        id_matches = _HARDCODED_ID_SEQ.findall(content)
        if len(id_matches) >= 3:
            # Find the first occurrence line number for context
            for line_num, line in enumerate(lines, 1):
                if _HARDCODED_ID_SEQ.search(line):
                    mock_patterns.append({
                        'pattern': 'hardcoded_id_sequence',
                        'file': rel_path,
                        'line': line_num,
                        'context': f'{len(id_matches)} sequential string IDs found',
                    })
                    break

    return {
        'clean': len(mock_patterns) == 0,
        'mock_patterns': mock_patterns,
    }
