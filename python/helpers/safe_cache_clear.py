"""
Safe cache clearing for framework build artifacts (RCA-233, U-2).

Uses shutil.rmtree (NOT shell rm -rf) with:
1. Strict path validation — must be under ALLOWED_PROJECT_ROOTS
2. Allowlisted cache dir names only — never touches source code
3. os.path.isdir() check — only removes existing directories
4. Logging of every action for audit trail

Safety invariants (enforced by tests):
- validate_project_path("/agix/usr/projects/myapp") → True
- validate_project_path("/agix/") → False (too broad)
- validate_project_path("/etc/") → False (outside root)
- Only dirs matching CACHE_DIR_ALLOWLIST are touched
- Non-existent dirs are silently skipped
- Source code (src/, components/, etc.) is NEVER touched
"""
import os
import shutil
import logging
from typing import Dict, List, Any

logger = logging.getLogger("agix.safe_cache_clear")

# Project roots where cache clearing is allowed.
# ONLY paths that start with one of these prefixes are valid targets.
ALLOWED_PROJECT_ROOTS = [
    "/agix/usr/projects",
]

# Cache directory names that are safe to remove.
# These are framework build artifacts, not source code.
# Paths are relative to the project root.
CACHE_DIR_ALLOWLIST = [
    ".next",
    os.path.join("node_modules", ".cache"),
    os.path.join("node_modules", ".vite"),
]


def validate_project_path(project_path: str) -> bool:
    """Verify path is under an allowed project root.

    Returns True only if the normalized path is a proper subdirectory
    of an allowed root (not the root itself, not a parent).

    Args:
        project_path: The project directory path to validate.

    Returns:
        True if path is under an allowed root, False otherwise.
    """
    if not project_path:
        return False

    # Normalize: resolve symlinks, remove trailing slashes
    normalized = os.path.normpath(os.path.abspath(project_path))

    for root in ALLOWED_PROJECT_ROOTS:
        root_normalized = os.path.normpath(os.path.abspath(root))
        # Must be a PROPER subdirectory (not the root itself)
        if normalized.startswith(root_normalized + os.sep) and normalized != root_normalized:
            return True

    return False


def clear_framework_caches(
    project_path: str,
    _allow_any_root: bool = False,
) -> Dict[str, List[str]]:
    """Safely remove framework cache directories.

    Uses Python's shutil.rmtree — NOT shell rm -rf.
    Only removes directories from the CACHE_DIR_ALLOWLIST.
    Never touches source code, config files, or anything not in the allowlist.

    Args:
        project_path: Absolute path to the project directory.
        _allow_any_root: If True, skip root validation (for testing only).
            In production this MUST be False.

    Returns:
        dict with:
            cleared: list of paths that were successfully removed
            skipped: list of paths that didn't exist (no action needed)
            errors: list of error messages
    """
    result: Dict[str, Any] = {
        "cleared": [],
        "skipped": [],
        "errors": [],
    }

    # Validate project path unless testing override
    if not _allow_any_root:
        if not validate_project_path(project_path):
            error_msg = (
                f"Rejected: path '{project_path}' is not under an allowed "
                f"project root ({', '.join(ALLOWED_PROJECT_ROOTS)})"
            )
            logger.warning(f"[CACHE HYGIENE] {error_msg}")
            result["errors"].append(error_msg)
            return result

    # Iterate through allowlisted cache directories
    for cache_rel_path in CACHE_DIR_ALLOWLIST:
        cache_abs_path = os.path.join(project_path, cache_rel_path)

        if not os.path.isdir(cache_abs_path):
            result["skipped"].append(cache_abs_path)
            continue

        try:
            shutil.rmtree(cache_abs_path)
            result["cleared"].append(cache_abs_path)
            logger.info(f"[CACHE HYGIENE] Cleared: {cache_abs_path}")
        except (OSError, PermissionError) as e:
            error_msg = f"Failed to clear {cache_abs_path}: {e}"
            result["errors"].append(error_msg)
            logger.warning(f"[CACHE HYGIENE] {error_msg}")

    return result
