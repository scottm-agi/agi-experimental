"""
Recursive Delete Tool -- Safe directory deletion with project scope enforcement.

RCA-333 Layer 2: Agents MUST use this tool instead of `rm -rf` via terminal.
Enforces the same allowlist as protected_paths_guard.py but provides a
dedicated tool interface with clear error messages.

Usage by agents:
    {
        "tool_name": "recursive_delete",
        "tool_args": {"path": "/agix/usr/projects/my_app/.next"}
    }

Validation:
    1. Path must be absolute
    2. Path must be inside a project directory
    3. Path must target a KNOWN-SAFE subdirectory (programmatic allowlist)
    4. Project roots, system paths, and source code dirs are ALWAYS blocked
"""

from __future__ import annotations

import os
import shutil
import stat
import logging
from typing import Tuple, Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.recursive_delete")

# ── Reuse the same allowlist from protected_paths_guard ──
from python.helpers.protected_paths_guard import (
    SAFE_CLEANUP_DIRS,
    _PROJECT_ROOTS,
    _SYSTEM_CRITICAL_PATHS,
)
from python.helpers import projects


def is_valid_delete_path(
    path: Optional[str],
    allowed_project_dir: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate whether a path is safe for recursive deletion.

    Programmatic examination: checks the path against the allowlist
    without requiring human approval.

    Args:
        path: Absolute path to validate.
        allowed_project_dir: If provided, the path MUST be inside this
            exact project directory. Prevents cross-project deletions.
            Should be the absolute project folder for the current chat.

    Returns:
        Tuple of (is_allowed, explanation_message).
    """
    if not path:
        return False, "BLOCKED: No path provided."

    if not isinstance(path, str):
        return False, "BLOCKED: Path must be a string."

    if not path.startswith("/"):
        return False, "BLOCKED: Path must be an absolute path (starting with /)."

    # Normalize: strip trailing slashes
    normalized = path.rstrip("/")
    if not normalized:
        normalized = "/"

    # Check system-critical paths
    for critical in _SYSTEM_CRITICAL_PATHS:
        critical_norm = critical.rstrip("/")
        if not critical_norm:
            critical_norm = "/"
        if normalized == critical_norm:
            return False, (
                f"BLOCKED: '{path}' is a system-critical path. "
                f"Recursive deletion of system paths is forbidden."
            )

    # ── Chat-scope enforcement ──────────────────────────────────────────────
    # If the caller provides the current chat's project dir, the path MUST
    # be inside it. Agents cannot delete from a different project.
    if allowed_project_dir:
        allowed_norm = allowed_project_dir.rstrip("/")
        if not normalized.startswith(allowed_norm + "/"):
            # Derive the attempted project name for a clear error message
            attempted_project = "unknown"
            for root in _PROJECT_ROOTS:
                if normalized.startswith(root):
                    after = normalized[len(root):].split("/", 1)
                    if after:
                        attempted_project = after[0]
                    break
            allowed_project = os.path.basename(allowed_norm)
            return False, (
                f"BLOCKED: Path is outside the current chat's project scope. "
                f"This chat is scoped to project '{allowed_project}'. "
                f"The requested path targets '{attempted_project}'. "
                f"recursive_delete can only operate within '{allowed_project}'."
            )

    # ── Project directory check ─────────────────────────────────────────────
    inside_project = False
    project_name = ""
    subpath = ""

    for root in _PROJECT_ROOTS:
        if normalized.startswith(root):
            after_root = normalized[len(root):]
            parts = after_root.split("/", 1)
            project_name = parts[0] if parts else ""

            if not project_name:
                return False, (
                    f"BLOCKED: '{path}' is the projects parent directory. "
                    f"Only subdirectories within specific projects can be deleted."
                )

            if len(parts) < 2 or not parts[1]:
                return False, (
                    f"BLOCKED: '{path}' is a project root directory. "
                    f"Project roots cannot be deleted via this tool. "
                    f"Use this tool to delete subdirectories within the project."
                )

            subpath = parts[1]
            inside_project = True
            break

    if not inside_project:
        return False, (
            f"BLOCKED: '{path}' is not inside a recognized project directory. "
            f"This tool only operates within project directories under "
            f"{', '.join(_PROJECT_ROOTS)}."
        )

    # ── Safe allowlist check ────────────────────────────────────────────────
    for safe_dir in SAFE_CLEANUP_DIRS:
        if subpath == safe_dir or subpath.startswith(safe_dir + "/"):
            return True, (
                f"Allowed: '{subpath}' is a known-safe cleanup directory "
                f"in project '{project_name}'."
            )

    # Not on allowlist — blocked by default
    safe_dirs_display = ", ".join(sorted(
        d for d in SAFE_CLEANUP_DIRS if "/" not in d
    )[:10])
    return False, (
        f"BLOCKED: '{subpath}' in project '{project_name}' is not on the "
        f"safe-cleanup allowlist. Allowed directories: {safe_dirs_display}, etc. "
        f"Use an alternative approach: move files instead of deleting, or target "
        f"only specific files using individual file operations."
    )


class RecursiveDelete(Tool):
    """Safe recursive directory deletion with project scope enforcement."""

    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path")

        if not path:
            return Response(
                message=(
                    "Error: Missing 'path' argument. "
                    "Provide the absolute path to the directory to delete."
                ),
                break_loop=False,
            )

        # ── Resolve current chat's project directory (chat-scope enforcement) ──
        # Uses the same pattern as requirements_actions.py.
        # This ensures the tool can ONLY delete within the project this chat
        # is scoped to — not any other project on the system.
        allowed_project_dir: Optional[str] = None
        try:
            project_name = projects.get_context_project_name(self.agent.context)
            if project_name:
                allowed_project_dir = projects.get_project_folder(project_name)
        except Exception as e:
            logger.warning(
                f"[recursive_delete] Could not resolve chat project scope: {e}. "
                f"Falling back to global project-root check."
            )

        # Validate the path — with chat-scope enforcement if resolvable
        is_allowed, reason = is_valid_delete_path(path, allowed_project_dir)

        if not is_allowed:
            logger.warning(
                f"[recursive_delete] {reason} (requested: {path})"
            )
            return Response(
                message=(
                    f"\U0001f6e1\ufe0f **BLOCKED by recursive_delete tool**\n\n"
                    f"{reason}\n\n"
                    f"Use this tool only for safe build artifacts and caches "
                    f"within the current chat's project directory."
                ),
                break_loop=False,
            )

        # Path is approved -- perform the deletion
        abs_path = os.path.abspath(path)

        if not os.path.exists(abs_path):
            return Response(
                message=f"Directory '{path}' does not exist. Nothing to delete.",
                break_loop=False,
            )

        if not os.path.isdir(abs_path):
            return Response(
                message=(
                    f"'{path}' is a file, not a directory. "
                    f"Use `rm` for individual files, not recursive_delete."
                ),
                break_loop=False,
            )

        try:
            def _onerror(func, fpath, exc_info):
                """Handle rmtree errors by fixing permissions and retrying."""
                try:
                    os.chmod(fpath, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    func(fpath)
                except Exception:
                    pass

            shutil.rmtree(abs_path, onerror=_onerror)

            if os.path.exists(abs_path):
                return Response(
                    message=(
                        f"Warning: Directory '{path}' still exists after deletion "
                        f"attempt. Possible file lock or mount issue."
                    ),
                    break_loop=False,
                )

            logger.info(f"[recursive_delete] Successfully deleted: {path}")
            return Response(
                message=f"Successfully deleted directory: `{path}`",
                break_loop=False,
            )

        except Exception as e:
            logger.error(f"[recursive_delete] Failed to delete '{path}': {e}")
            return Response(
                message=f"Error deleting '{path}': {e}",
                break_loop=False,
            )
