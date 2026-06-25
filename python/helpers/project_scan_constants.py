"""
Project Scan Constants — Shared skip-dir and extension constants.
=================================================================

Single source of truth for directory-exclusion sets and file-extension
sets used across all project-scanning code paths. Before this module,
11+ files maintained their own copies of ``_SKIP_DIRS`` / ``EXCLUDE_DIRS``
with slightly different entries, leading to inconsistent scanning behavior.

All consumers should import from here:

    from python.helpers.project_scan_constants import (
        DEFAULT_PROJECT_SKIP_DIRS,
        DEFAULT_PROJECT_EXTENSIONS,
        should_skip_dir,
        filter_walk_dirs,
    )

DUP-3 consolidation.
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────────────
# DEFAULT_PROJECT_SKIP_DIRS — Union of ALL skip-dir entries from 11+ sources
# ──────────────────────────────────────────────────────────────────────

DEFAULT_PROJECT_SKIP_DIRS: frozenset[str] = frozenset({
    # ── Package manager artifacts ──
    "node_modules",
    "vendor",
    "bower_components",

    # ── Build outputs ──
    "dist",
    "build",
    "out",
    ".output",
    "target",

    # ── Framework-specific build/cache dirs ──
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".expo",
    ".parcel-cache",

    # ── Version control ──
    ".git",

    # ── Caches & intermediates ──
    "__pycache__",
    ".turbo",
    ".cache",
    "coverage",

    # ── Deployment / hosting ──
    ".vercel",

    # ── Python virtual environments ──
    "venv",
    ".venv",

    # ── IDE / editor dirs ──
    ".idea",
    ".vscode",

    # ── Gate / pipeline artifacts (AGIX-specific) ──
    ".agix.proj",
    "backup_init",

    # ── Temporary dirs ──
    "tmp",
    "temp",
})


# ──────────────────────────────────────────────────────────────────────
# DEFAULT_PROJECT_EXTENSIONS — Common source file extensions for scanning
# ──────────────────────────────────────────────────────────────────────

DEFAULT_PROJECT_EXTENSIONS: frozenset[str] = frozenset({
    # JavaScript / TypeScript ecosystem
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    # Other frontend frameworks
    ".vue", ".svelte", ".astro",
    # Stylesheets
    ".css", ".scss", ".sass", ".less",
    # Markup / data
    ".html", ".json", ".md",
    # Python
    ".py",
    # Configuration
    ".yaml", ".yml", ".toml",
    # Environment / database / API
    ".env", ".prisma", ".graphql", ".gql", ".sql",
})


# ──────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────


def should_skip_dir(dirname: str) -> bool:
    """Quick check: should this directory name be skipped during project scans?

    Args:
        dirname: The directory base name (not a path).

    Returns:
        True if the directory should be pruned from os.walk traversal.
    """
    return dirname in DEFAULT_PROJECT_SKIP_DIRS


def filter_walk_dirs(
    dirs: list[str],
    extra_skip: set[str] | None = None,
) -> list[str]:
    """In-place filter helper for os.walk — removes skip dirs from *dirs*.

    Designed to be used as::

        for root, dirs, files in os.walk(project_dir):
            filter_walk_dirs(dirs)
            # or: filter_walk_dirs(dirs, extra_skip={".mydir"})

    Args:
        dirs: The mutable directory list from os.walk (modified **in-place**).
        extra_skip: Optional additional directory names to skip on top of
            the default set.

    Returns:
        The same *dirs* list (for convenience chaining), after pruning.
    """
    skip = DEFAULT_PROJECT_SKIP_DIRS
    if extra_skip:
        skip = skip | frozenset(extra_skip)

    dirs[:] = [d for d in dirs if d not in skip]
    return dirs
