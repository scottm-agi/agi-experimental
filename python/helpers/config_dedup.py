"""
Config Dedup Guard — prevents conflicting JS/TS/MJS config files.
================================================================

When `create-next-app` (or similar scaffolders) generates a project, they
often create `.js` config files (e.g., `tailwind.config.js`) with empty
or default values. Agent code then creates `.ts` versions with correct
settings. But Node.js resolves `.js` before `.ts`, so the empty one wins.

This module detects and resolves these conflicts by removing the
lower-priority file when a higher-priority version exists.

Priority order (highest to lowest):
  .ts > .mjs > .js

Root cause (ADR-018, MSR Iteration 150):
    tailwind.config.js (content: []) AND tailwind.config.ts (correct paths)
    both existed. .js took precedence → ALL utility classes purged in production.

Usage:
    from python.helpers.config_dedup import resolve_config_conflicts

    # After scaffold, clean up conflicts:
    removed = resolve_config_conflicts("/path/to/project")
    for f in removed:
        print(f"Removed conflicting config: {f}")
"""

from __future__ import annotations

import logging
import os
from typing import List, Dict

logger = logging.getLogger("agix.config_dedup")

# Known config file basenames that commonly conflict.
# Each tuple: (basename_without_ext, list_of_extensions_from_lowest_to_highest_priority)
# When multiple extensions exist for the same basename, lower-priority files are removed.
KNOWN_CONFLICT_PAIRS: list[tuple[str, list[str]]] = [
    ("tailwind.config", [".js", ".mjs", ".ts"]),
    ("postcss.config",  [".js", ".mjs", ".ts"]),
    ("next.config",     [".js", ".mjs", ".ts"]),
    ("vite.config",     [".js", ".mjs", ".ts"]),
    ("tsconfig",        [".json",]),  # Single — no conflict possible, but reserved
    ("eslint.config",   [".js", ".mjs", ".ts"]),
]


def find_conflicting_configs(project_dir: str) -> List[Dict[str, str]]:
    """Find config files that have conflicting JS/TS/MJS variants.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        List of dicts with keys: basename, keep, remove, keep_path, remove_path
    """
    conflicts = []

    for basename, extensions in KNOWN_CONFLICT_PAIRS:
        if len(extensions) < 2:
            continue  # No conflict possible with single extension

        # Find which extensions exist
        existing = {}
        for ext in extensions:
            full_path = os.path.join(project_dir, f"{basename}{ext}")
            if os.path.isfile(full_path):
                existing[ext] = full_path

        if len(existing) < 2:
            continue  # No conflict — only 0 or 1 variant exists

        # The highest-priority extension (last in the list) wins
        sorted_exts = [ext for ext in extensions if ext in existing]
        winner = sorted_exts[-1]
        winner_path = existing[winner]

        for ext in sorted_exts[:-1]:
            conflicts.append({
                "basename": basename,
                "keep": f"{basename}{winner}",
                "remove": f"{basename}{ext}",
                "keep_path": winner_path,
                "remove_path": existing[ext],
            })

    return conflicts


def resolve_config_conflicts(
    project_dir: str,
    dry_run: bool = False,
) -> List[str]:
    """Resolve config conflicts by removing lower-priority files.

    Args:
        project_dir: Absolute path to the project directory.
        dry_run: If True, report what would be removed without deleting.

    Returns:
        List of file paths that were removed (or would be, in dry_run mode).
    """
    conflicts = find_conflicting_configs(project_dir)

    if not conflicts:
        return []

    removed = []
    for conflict in conflicts:
        remove_path = conflict["remove_path"]
        keep_file = conflict["keep"]
        remove_file = conflict["remove"]

        if dry_run:
            logger.info(
                f"[CONFIG DEDUP] Would remove '{remove_file}' "
                f"(keeping '{keep_file}') in {project_dir}"
            )
            removed.append(remove_path)
        else:
            try:
                os.remove(remove_path)
                removed.append(remove_path)
                logger.warning(
                    f"[CONFIG DEDUP] Removed '{remove_file}' "
                    f"(keeping '{keep_file}') in {project_dir}"
                )
            except OSError as e:
                logger.error(
                    f"[CONFIG DEDUP] Failed to remove '{remove_path}': {e}"
                )

    return removed
