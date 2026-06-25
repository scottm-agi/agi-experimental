"""
Project Directory Garbage Collection — Cleans up stale test iteration dirs.

After many smoke test iterations, Docker accumulates orphaned project
directories in /agix/usr/projects/. This module identifies stale
projects and safely removes them.

Safety rules:
- Never delete 'default' project
- Never delete projects referenced by active chats
- Only delete projects with no file modified in 48+ hours

Usage:
    from python.helpers.project_gc import identify_stale_projects, cleanup_stale_projects

    stale = identify_stale_projects("/agix/usr/projects", active_chats=set())
    result = cleanup_stale_projects("/agix/usr/projects", active_chats=set(), dry_run=True)
"""

import os
import shutil
import time
import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger("agix.project_gc")

# Project names that should NEVER be deleted
PROTECTED_PROJECTS = {"default"}

# Hours of inactivity before a project is considered stale
STALE_THRESHOLD_HOURS = 48


def _get_newest_mtime(directory: str) -> float:
    """Get the most recent modification time of any file in a directory tree.

    Returns 0 if directory is empty or no files found.
    """
    newest = 0.0
    try:
        for root, dirs, files in os.walk(directory):
            # Skip node_modules and .next — they have thousands of files
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", "dist", ".git")]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime > newest:
                        newest = mtime
                except OSError:
                    continue
    except OSError:
        pass

    # Also check the directory itself
    try:
        dir_mtime = os.path.getmtime(directory)
        if dir_mtime > newest:
            newest = dir_mtime
    except OSError:
        pass

    return newest


def identify_stale_projects(
    projects_dir: str,
    active_chats: Set[str],
    threshold_hours: float = STALE_THRESHOLD_HOURS,
) -> List[str]:
    """Identify stale project directories.

    A project is stale if:
    1. It is not in PROTECTED_PROJECTS (e.g., 'default')
    2. It is not referenced by any active chat
    3. No file in the project has been modified in threshold_hours

    Args:
        projects_dir: Path to the projects root directory
        active_chats: Set of project directory names referenced by active chats
        threshold_hours: Hours of inactivity before considered stale

    Returns:
        List of absolute paths to stale project directories
    """
    if not os.path.isdir(projects_dir):
        return []

    stale = []
    cutoff = time.time() - (threshold_hours * 3600)

    try:
        entries = os.listdir(projects_dir)
    except OSError:
        return []

    for entry in entries:
        entry_path = os.path.join(projects_dir, entry)
        if not os.path.isdir(entry_path):
            continue

        # Rule 1: Never delete protected projects
        if entry in PROTECTED_PROJECTS:
            continue

        # Rule 2: Never delete active chat projects
        if entry in active_chats:
            continue

        # Rule 3: Check if any file is newer than cutoff
        newest_mtime = _get_newest_mtime(entry_path)
        if newest_mtime < cutoff:
            stale.append(entry_path)

    return stale


def cleanup_stale_projects(
    projects_dir: str,
    active_chats: Set[str],
    dry_run: bool = True,
    threshold_hours: float = STALE_THRESHOLD_HOURS,
) -> Dict[str, Any]:
    """Clean up stale project directories.

    Args:
        projects_dir: Path to the projects root directory
        active_chats: Set of project directory names referenced by active chats
        dry_run: If True, only report what would be removed
        threshold_hours: Hours of inactivity before considered stale

    Returns:
        Dict with 'would_remove' (dry_run) or 'removed' (actual) list
    """
    stale = identify_stale_projects(projects_dir, active_chats, threshold_hours)

    if dry_run:
        if stale:
            logger.info(
                f"[PROJECT_GC] DRY RUN: Would remove {len(stale)} stale projects: "
                f"{[os.path.basename(s) for s in stale]}"
            )
        return {"would_remove": stale, "removed": []}

    removed = []
    for path in stale:
        try:
            from python.helpers import files
            if files.delete_dir(path):
                removed.append(path)
                logger.info(f"[PROJECT_GC] Removed stale project: {os.path.basename(path)}")
            else:
                logger.error(f"[PROJECT_GC] Failed to remove {path} after retries")
        except OSError as e:
            logger.error(f"[PROJECT_GC] Failed to remove {path}: {e}")

    return {"would_remove": [], "removed": removed}
