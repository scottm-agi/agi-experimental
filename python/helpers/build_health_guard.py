"""
Build Health Guard — Prevent destructive dependency cleanup without reinstall.

Addresses: Forgejo #1186 — Destructive node_modules cleanup without re-install.

Root cause (5-Why from MSR_Smoke_1776862075):
  1. Agent gets a build error (missing module)
  2. Agent runs `rm -rf node_modules` as a "fix"
  3. No reinstall is run afterward
  4. ALL subsequent builds fail with Cannot Find Module
  5. Agent enters death spiral until iteration limit

This module provides a pure function that intercepts terminal commands
containing `rm -rf node_modules` (or variants) and appends `&& npm install`
when the command doesn't already include a reinstall step.

Usage:
    from python.helpers.build_health_guard import guard_destructive_cleanup
    command = guard_destructive_cleanup(command)
"""

from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger("agix.build_health_guard")

# Pattern: rm with -r/-f flags followed by a path ending in node_modules
# Matches: rm -rf node_modules, rm -r ./node_modules, rm -rf /app/node_modules/
_RM_NODE_MODULES_PATTERN = re.compile(
    r"\brm\s+(?:-[rRfF]+\s+)*"        # rm with optional flags
    r"(?:[\w./-]*\s+)*"                # optional other dirs before node_modules
    r"[\w./-]*node_modules/?",         # path ending in node_modules
    re.IGNORECASE,
)

# Patterns that indicate a reinstall is already present in the command.
# Checked against the FULL command string (including everything after rm).
_REINSTALL_PATTERNS = [
    re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+ci\b", re.IGNORECASE),
    re.compile(r"\byarn\s+install\b", re.IGNORECASE),
    re.compile(r"\byarn\b(?!\s+\w)", re.IGNORECASE),  # bare `yarn` = install
    re.compile(r"\bpnpm\s+install\b", re.IGNORECASE),
    re.compile(r"\bbun\s+install\b", re.IGNORECASE),
]


def guard_destructive_cleanup(command: Optional[str]) -> Optional[str]:
    """Intercept destructive node_modules removal and auto-append npm install.

    If the command contains `rm -rf node_modules` (or variants) without a
    subsequent `npm install` / `npm ci` / `yarn install` / `pnpm install`,
    appends `&& npm install` to prevent dependency death spirals.

    Args:
        command: The terminal command string. Can be None.

    Returns:
        The (possibly modified) command string, or None if input was None.
    """
    if command is None:
        return None

    if not command.strip():
        return command

    # Check if command removes node_modules
    if not _RM_NODE_MODULES_PATTERN.search(command):
        return command  # No destructive cleanup detected

    # Check if a reinstall is already present
    for pattern in _REINSTALL_PATTERNS:
        if pattern.search(command):
            logger.info(
                "[BUILD GUARD] rm node_modules detected but reinstall already "
                f"present: {command[:80]}"
            )
            return command  # Already safe

    # Destructive cleanup WITHOUT reinstall — append npm install
    logger.warning(
        f"[BUILD GUARD] Intercepted destructive node_modules removal "
        f"without reinstall: {command[:80]}"
    )
    guarded = f"{command} && npm install"
    logger.info(f"[BUILD GUARD] Guarded command: {guarded[:120]}")
    return guarded


# ─── Service-Aware File Guard ───────────────────────────────────────────
#
# Addresses: MainStreet destruction cascade — agents deleting .next/
# while a dev server is actively bound to that directory.
#
# Unlike guard_destructive_cleanup (which auto-heals node_modules removal),
# this guard BLOCKS the command entirely when a service is active.
# You can't auto-heal a deleted .next while the server holds file locks.

# Build output directories that must NOT be deleted while a service is running.
BUILD_DIRS = frozenset({".next", "dist", "build", "out"})

# Default path to the services_mgt state file
_DEFAULT_STATE_FILE = "data/managed_services.json"

# Pattern: rm with -r/-f flags followed by a build directory name
_RM_BUILD_DIR_PATTERN = re.compile(
    r"\brm\s+(?:-[rRfF]+\s+)*"        # rm with optional flags
    r"(?:[\w./-]*\s+)*"                # optional other dirs before target
    r"[\w./-]*(?:"                      # path possibly ending in one of BUILD_DIRS
    + "|".join(re.escape(d) for d in BUILD_DIRS)
    + r")/?(?:\s|$|;|&&|\|)",          # terminated by whitespace, EOL, or shell operator
    re.IGNORECASE,
)


def _is_service_active(
    project_dir: str,
    state_file: Optional[str] = None,
) -> bool:
    """Check if any service is actively running for the given project directory.

    Reads the managed_services.json state file written by services_mgt.py.
    A service is considered active if its `cwd` matches the project_dir
    and its `status` is "running".

    Args:
        project_dir: Absolute path to the project directory.
        state_file: Path to the state file. Defaults to _DEFAULT_STATE_FILE.

    Returns:
        True if an active service is found for this project.
    """
    import json
    import os

    state_path = state_file or _DEFAULT_STATE_FILE
    if not os.path.isfile(state_path):
        return False

    try:
        with open(state_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return False

    # Handle both formats: bare array [...] and {"services": [...]}
    if isinstance(data, list):
        services = data
    elif isinstance(data, dict):
        services = data.get("services", [])
    else:
        return False

    if not services:
        return False

    # Normalize project_dir for comparison
    norm_project = os.path.normpath(project_dir)

    for svc in services:
        svc_cwd = svc.get("cwd", "")
        # Real format uses presence of pid + started_at as "running"
        # Test format uses explicit "status": "running"
        svc_status = svc.get("status", "").lower()
        has_pid = svc.get("pid") is not None
        is_running = svc_status == "running" or (has_pid and svc_status != "stopped")
        if is_running and os.path.normpath(svc_cwd) == norm_project:
            return True

    return False


def _extract_build_dirs_from_command(command: str) -> list:
    """Extract which build directories are targeted by an rm command."""
    found = []
    for d in BUILD_DIRS:
        # Match the dir name as a word boundary or at path end
        pattern = re.compile(
            r"\brm\s+(?:-[rRfF]+\s+).*?" + re.escape(d) + r"/?(?:\s|$|;|&&|\|)",
            re.IGNORECASE,
        )
        if pattern.search(command):
            found.append(d)
    return found


def guard_service_aware_cleanup(
    command: Optional[str],
    project_dir: Optional[str] = None,
    state_file_override: Optional[str] = None,
) -> Optional[str]:
    """Block rm -rf on build directories when a dev server is active.

    Unlike guard_destructive_cleanup (which auto-heals), this BLOCKS
    the command entirely — you cannot safely delete .next/ while a
    server holds file descriptors on it.

    Args:
        command: The terminal command string. Can be None.
        project_dir: Path to the project directory. Used to check
                     if a service is active for this project.
        state_file_override: Override path to managed_services.json
                            (for testing).

    Returns:
        Error message string if BLOCKED, None if allowed through.
    """
    if not command or not command.strip():
        return None

    # Check if command removes any build directories
    targeted = _extract_build_dirs_from_command(command)
    if not targeted:
        return None  # Not targeting build dirs — allow through

    # If no project_dir, we can't check service state — allow through
    if not project_dir:
        return None

    # Check if a service is actively running for this project
    if not _is_service_active(project_dir, state_file_override):
        return None  # No active service — safe to delete

    # BLOCKED: Service is active and command targets build directories
    dirs_str = ", ".join(targeted)
    logger.warning(
        f"[SERVICE GUARD] BLOCKED deletion of build dir(s) [{dirs_str}] "
        f"while service is active in {project_dir}"
    )
    return (
        f"BLOCKED: Cannot delete build directory [{dirs_str}] while a dev server "
        f"is actively running in this project. Deleting build output while the "
        f"server holds file descriptors on it will corrupt the build.\n\n"
        f"To fix: First stop the service using services_mgt with "
        f"action=stop_service, then retry the cleanup command."
    )
