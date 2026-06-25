"""
Source Scanner — Single source of truth for project file walking and search.
============================================================================

Consolidates 4+ parallel file-scanning implementations into one canonical
module. All verification gates (contract assertions, manifest checks, BDD
validation, content-presence checks) MUST use this module for file walking.

Constants:
    SOURCE_EXTENSIONS — extensions to include in source scans
    EXCLUDE_DIRS — directories to prune (Fix-286-E compliant)
    EXCLUDE_FILES — gate-generated metadata files to skip (Fix-286-E)

Functions:
    scan_project_sources() — walk project tree, return structured file data
    get_combined_source_text() — return all source as lowercase string
    search_literal() — search pre-loaded sources for a literal
    literal_exists() — convenience: walk + search in one call
    list_project_files() — lightweight path-only file listing (OVL-3)
    read_project_files() — read files into {path: content} dict (OVL-3)

Usage:
    from python.helpers.source_scanner import (
        scan_project_sources,
        get_combined_source_text,
        search_literal,
        literal_exists,
        list_project_files,
        read_project_files,
        SOURCE_EXTENSIONS,
        EXCLUDE_DIRS,
        EXCLUDE_FILES,
    )
"""

from __future__ import annotations

import os
import logging
from typing import Dict, List, Optional, Set

from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS

logger = logging.getLogger("agix.source_scanner")


# ──────────────────────────────────────────────────────────────────────
# Canonical constants — the SINGLE source of truth
# ──────────────────────────────────────────────────────────────────────

# Source file extensions to search (union of all former implementations)
SOURCE_EXTENSIONS: Set[str] = {
    # JavaScript / TypeScript ecosystem
    ".ts", ".tsx", ".js", ".jsx", ".mjs",
    # Other frontend frameworks
    ".vue", ".svelte", ".astro",
    # Stylesheets
    ".css", ".scss", ".sass",
    # Markup / data
    ".html", ".json", ".md",
    # Python
    ".py",
    # Configuration
    ".yaml", ".yml", ".toml",
    # Environment / database
    ".env", ".prisma", ".graphql",
}

# Directories to prune during os.walk (Fix-286-E compliant)
# DUP-3: Now sourced from the canonical shared module.
EXCLUDE_DIRS: Set[str] = set(DEFAULT_PROJECT_SKIP_DIRS)

# Gate-generated metadata files that must NEVER be searched.
# RCA-ITER9-1: The contract runner was matching its own output file
# (requirements_contract.json) which contains resolved slugs, causing
# guaranteed false-positive passes on model_name assertions.
EXCLUDE_FILES: Set[str] = {
    # Gate-generated metadata (own output — must never match against itself)
    "requirements_contract.json",
    "decomposition-index.json",
    "decomposition_index.json",  # underscore variant
    "requirements-ledger.json",
    "requirements_ledger.json",  # underscore variant
    "content-manifest.json",
    "content_manifest.json",  # underscore variant
    "verification_sitemap.json",
    # Planning/pipeline artifacts (prompt-derived, not deliverable source)
    "architect-plan.json",
    "architect_plan.json",  # underscore variant
    "lit_plan.json",
    "navigation-map.md",
    # BDD scenario definition files — contain assertion text (e.g.,
    # 'Then page does NOT contain "Create Next App"') that would
    # false-positive match against the source scan.
    "bdd-scenarios.md",
    # Manifest and write-ledger: contain requirement text that would
    # false-positive match against the source scan.
    "requirements_manifest.md",
    ".write_ledger.json",
}

# ─── F-4 (RCA-343 ISSUE-4): Behavioral Scan Exclusions ───────────────
# .env files contain configuration values (API keys, URLs, emails) that
# cause false-positive matches during behavioral pattern scanning.
# They should be included in STRUCTURAL checks (existence) but excluded
# from BEHAVIORAL scans (content matching).
# .env.example IS a deliverable and should NOT be excluded.
_BEHAVIORAL_SCAN_EXCLUDE_GLOBS = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
    ".env.test",
}


# ──────────────────────────────────────────────────────────────────────
# Core scanning functions
# ──────────────────────────────────────────────────────────────────────

def walk_project_files(
    project_dir: str,
    extensions: Optional[Set[str]] = None,
    exclude_behavioral: bool = False,
) -> List[str]:
    """Walk the project tree and return relative file paths.

    Args:
        project_dir: Absolute path to the project root.
        extensions: Optional extension filter.
        exclude_behavioral: If True, exclude .env* files (F-4, RCA-343).

    Returns:
        List of relative file paths.
    """
    exts = extensions if extensions is not None else SOURCE_EXTENSIONS
    paths: List[str] = []

    if not os.path.isdir(project_dir):
        return paths

    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in filenames:
            if name in EXCLUDE_FILES:
                continue
            # F-4: Exclude .env* files from behavioral scans
            if exclude_behavioral and name in _BEHAVIORAL_SCAN_EXCLUDE_GLOBS:
                continue
            ext = os.path.splitext(name)[1].lower()
            stem = os.path.splitext(name)[0].lower()
            if ext not in exts and stem not in exts:
                continue
            filepath = os.path.join(root, name)
            relpath = os.path.relpath(filepath, project_dir)
            paths.append(relpath)

    return paths


def scan_project_sources(
    project_dir: str,
    extensions: Optional[Set[str]] = None,
    exclude_behavioral: bool = False,
) -> List[Dict]:
    """Walk the full project tree and return structured file data.

    Each returned dict has:
        - path: relative path from project_dir
        - content: full file content as string
        - lines: list of (line_number, line_text) tuples

    Args:
        project_dir: Absolute path to the project root directory.
        extensions: Optional override for which extensions to include.
                    If None, uses SOURCE_EXTENSIONS. Pass a subset
                    (e.g., {".css", ".scss"}) for CSS-only scans.
        exclude_behavioral: If True, exclude .env* files from results.
                    Use for behavioral pattern scans (F-4, RCA-343).

    Returns:
        List of dicts, one per source file found.
    """
    exts = extensions if extensions is not None else SOURCE_EXTENSIONS
    files: List[Dict] = []

    if not os.path.isdir(project_dir):
        return files

    for root, dirs, filenames in os.walk(project_dir):
        # Prune excluded directories (in-place modification)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for name in filenames:
            # Skip gate-generated metadata files (Fix-286-E)
            if name in EXCLUDE_FILES:
                continue

            # F-4: Exclude .env* files from behavioral scans
            if exclude_behavioral and name in _BEHAVIORAL_SCAN_EXCLUDE_GLOBS:
                continue

            ext = os.path.splitext(name)[1].lower()
            # Handle dotfiles like .env, .env.local:
            # os.path.splitext(".env") → (".env", "")
            # os.path.splitext(".env.local") → (".env", ".local")
            # So we also check the stem against the extension set.
            stem = os.path.splitext(name)[0].lower()
            if ext not in exts and stem not in exts:
                continue

            filepath = os.path.join(root, name)
            relpath = os.path.relpath(filepath, project_dir)

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                lines = [(i + 1, line) for i, line in enumerate(content.split("\n"))]
                files.append({
                    "path": relpath,
                    "content": content,
                    "lines": lines,
                })
            except (IOError, OSError):
                continue

    return files


def get_combined_source_text(
    project_dir: str,
    extensions: Optional[Set[str]] = None,
) -> str:
    """Return all source content concatenated and lowercased.

    This is the shared replacement for the inline os.walk + concatenate
    patterns used by check_content_presence() and BDD validators.

    Args:
        project_dir: Absolute path to the project root.
        extensions: Optional extension filter (e.g., {".css"}).

    Returns:
        A single lowercase string containing all source file contents.
    """
    sources = scan_project_sources(project_dir, extensions=extensions)
    return "\n".join(s["content"] for s in sources).lower()


# ─── F-6 (RCA-343 ISSUE-6): Comment Detection ────────────────────────
# Regex to detect if a line is a comment (JS/TS/CSS/Python/HTML)
_COMMENT_LINE_RE = __import__('re').compile(
    r'^\s*(?://|#|/\*|\*|<!--)',
)


def search_literal(
    sources: List[Dict],
    needle: str,
    case_insensitive: bool = True,
    exclude_comments: bool = False,
) -> List[str]:
    """Search pre-loaded source files for a literal string.

    Args:
        sources: List of source dicts from scan_project_sources().
        needle: The literal string to search for.
        case_insensitive: If True (default), performs case-insensitive match.
        exclude_comments: If True (F-6, RCA-343), skip lines that are
                          comments. Used for model slug verification.

    Returns:
        List of "file:line" match locations (e.g., ["src/page.tsx:42"]).
    """
    matches: List[str] = []
    search_needle = needle.lower() if case_insensitive else needle

    for source_file in sources:
        content_check = source_file["content"].lower() if case_insensitive else source_file["content"]
        if search_needle not in content_check:
            continue

        for line_num, line_text in source_file["lines"]:
            line_check = line_text.lower() if case_insensitive else line_text
            if search_needle in line_check:
                # F-6: Skip comment-only lines if requested
                if exclude_comments and _COMMENT_LINE_RE.match(line_text):
                    continue
                matches.append(f"{source_file['path']}:{line_num}")

    return matches


def literal_exists(
    project_dir: str,
    needle: str,
    case_insensitive: bool = True,
) -> bool:
    """Convenience: check if a literal exists in any project source file.

    Replaces requirements_manifest._search_project_for_literal().

    Args:
        project_dir: Absolute path to the project root.
        needle: The literal string to search for.
        case_insensitive: If True (default), performs case-insensitive match.

    Returns:
        True if the needle is found in any source file, False otherwise.
    """
    sources = scan_project_sources(project_dir)
    matches = search_literal(sources, needle, case_insensitive=case_insensitive)
    return len(matches) > 0


def regex_exists(
    project_dir: str,
    pattern: str,
    extensions: Optional[Set[str]] = None,
) -> bool:
    """Check if a regex pattern matches any content in project source files.

    U-8 (RCA-339): Used by the behavioral requirement verification gate to
    run verify_pattern regexes (extracted by prompt_contract_parser) against
    the actual source tree. This enables deterministic Layer-1 verification
    that scheduling, scoring, and other behavioral logic is implemented.

    Args:
        project_dir: Absolute path to the project root.
        pattern: Regex pattern string to search for.
        extensions: Optional extension filter.

    Returns:
        True if the pattern matches at least one source file, False otherwise.
    """
    import re

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.warning(f"[SOURCE_SCANNER] Invalid regex pattern: {pattern}")
        return False

    sources = scan_project_sources(project_dir, extensions=extensions)
    for source_file in sources:
        if compiled.search(source_file["content"]):
            return True
    return False


# ──────────────────────────────────────────────────────────────────────
# OVL-3: Lightweight wrappers for common os.walk patterns
# ──────────────────────────────────────────────────────────────────────


def list_project_files(
    project_dir: str,
    extensions: Optional[Set[str]] = None,
    skip_dirs: Optional[Set[str]] = None,
    max_files: int = 5000,
) -> List[str]:
    """Lightweight project file listing — absolute paths only, no content.

    Replaces the boilerplate os.walk + skip-dir + extension filter pattern
    used across 17+ modules. Consumers that only need file paths (not
    content) should prefer this over scan_project_sources().

    Args:
        project_dir: Absolute path to the project root.
        extensions: Optional extension filter (e.g., {".tsx", ".ts"}).
                    If None, accepts ALL file extensions (no filtering).
                    Pass SOURCE_EXTENSIONS to match scan_project_sources().
        skip_dirs: Optional directory names to skip. If None, uses
                   DEFAULT_PROJECT_SKIP_DIRS from project_scan_constants.
                   Pass a custom set to override entirely, or merge with
                   ``DEFAULT_PROJECT_SKIP_DIRS | {"extra_dir"}``.
        max_files: Maximum number of files to return (default: 5000).
                   Prevents runaway scans on very large project trees.

    Returns:
        List of absolute file paths.
    """
    skip = skip_dirs if skip_dirs is not None else EXCLUDE_DIRS
    paths: List[str] = []

    if not os.path.isdir(project_dir):
        return paths

    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in filenames:
            if len(paths) >= max_files:
                return paths
            if extensions is not None:
                ext = os.path.splitext(name)[1].lower()
                if ext not in extensions:
                    continue
            paths.append(os.path.join(root, name))

    return paths


def read_project_files(
    project_dir: str,
    extensions: Optional[Set[str]] = None,
    skip_dirs: Optional[Set[str]] = None,
    max_files: int = 1000,
    max_file_size: int = 100_000,
) -> Dict[str, str]:
    """Read project files — returns {relative_path: content}.

    Replaces os.walk + open + read patterns. Skips binary files and files
    exceeding max_file_size. Returns relative paths as keys for portability.

    Args:
        project_dir: Absolute path to the project root.
        extensions: Optional extension filter (e.g., {".tsx", ".ts"}).
                    If None, accepts ALL file extensions (no filtering).
                    Pass SOURCE_EXTENSIONS to match scan_project_sources().
        skip_dirs: Optional directory names to skip. If None, uses
                   DEFAULT_PROJECT_SKIP_DIRS.
        max_files: Maximum number of files to return (default: 1000).
        max_file_size: Maximum file size in bytes (default: 100KB).
                       Files larger than this are silently skipped.

    Returns:
        Dict mapping relative file paths to their text content.
    """
    skip = skip_dirs if skip_dirs is not None else EXCLUDE_DIRS
    result: Dict[str, str] = {}

    if not os.path.isdir(project_dir):
        return result

    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in filenames:
            if len(result) >= max_files:
                return result
            if extensions is not None:
                ext = os.path.splitext(name)[1].lower()
                if ext not in extensions:
                    continue
            filepath = os.path.join(root, name)

            # Skip files over size limit
            try:
                file_size = os.path.getsize(filepath)
                if file_size > max_file_size:
                    continue
            except OSError:
                continue

            # Skip binary files
            try:
                with open(filepath, "r", encoding="utf-8", errors="strict") as f:
                    content = f.read()
            except (UnicodeDecodeError, IOError, OSError):
                continue

            relpath = os.path.relpath(filepath, project_dir)
            result[relpath] = content

    return result


