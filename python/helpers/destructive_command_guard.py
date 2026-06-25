"""Destructive Command Guard — blocks rm -rf and similar shell commands.

RCA: ITR-35 — agents executed `rm -rf tmp/` 26 times, destroying workspace
artifacts. The orchestrator LLM hallucinated destructive instructions in
delegation briefs, and no pre-execution filter existed.

This module provides `is_destructive_command()` for use by a tool_execute_before
extension that intercepts code_execution_tool calls.

Complements `protected_paths_guard.py` (RCA-333, absolute path enforcement)
by covering project-relative destructive commands that the existing guard
does not catch.
"""
from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger("agix.destructive_command_guard")

# ── Patterns that are explicitly ALLOWED despite being destructive-looking ──
# These are safe cleanup targets that don't contain source code.
# Each pattern matches a specific rm -rf invocation on a known-safe target.
ALLOWED_CLEANUP_PATTERNS = [
    # node_modules (whole or subcaches) — with optional absolute path prefix
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?node_modules(?:/|\s|$)'),
    # .next — generated build cache directory (no source code), same as dist/build.
    # RCA-ITR32 Class A: Corrected from .next/cache-only. The entire .next/ dir is
    # generated output — zero user-authored files. Blocking .next deletion forces
    # agents into stale cache problems. Supersedes RCA-ITR34-BL1.
    # RCA-239: Fixed to accept absolute paths (e.g. /agix/usr/projects/xyz/.next)
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?\\.next(?:/|\s|$)'),
    # dist, build, coverage — standard build output directories
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?dist(?:/|\s|$)'),
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?build(?:/|\s|$)'),
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?coverage(?:/|\s|$)'),
    # _scaffold, scaffold-temp — temporary scaffold workspace directories.
    # RCA-ITR32 Class A: node_project_scaffold.py:728 generates `rm -rf _scaffold`
    # as part of scaffold_merge_commands. Must be whitelisted so the tool's own
    # cleanup command isn't blocked by the guard.
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?_scaffold(?:/|\s|$)'),
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?scaffold-temp(?:/|\s|$)'),
    re.compile(r'^rm\s+(-[a-zA-Z]*\s+)?(/?(\S+/)?)?tmp_scaffold(?:/|\s|$)'),
]

# ── Destructive command patterns ──
# These patterns detect commands that recursively delete directories.

# 1. rm with -r (recursive) flag, with any target
_RM_RECURSIVE = re.compile(
    r'\brm\s+'                 # rm command
    r'-[a-zA-Z]*[rR]'         # flags that include -r or -R
)

# 2. find with -delete flag
_FIND_DELETE = re.compile(
    r'\bfind\s+'               # find command
    r'.*\s+-delete\b'          # -delete flag anywhere after
)

# 3. rm -rf with shell variables (could expand to anything)
_RM_VARIABLE = re.compile(
    r'\brm\s+'                 # rm command
    r'-[a-zA-Z]*[rR]'         # recursive flag
    r'[a-zA-Z]*\s+'           # rest of flags
    r'\$'                      # shell variable
)


def _split_compound_command(command: str) -> list:
    """Split a compound command (&&, ||, ;) into individual parts."""
    return re.split(r'\s*(?:&&|\|\||;)\s*', command)


def _is_allowed_cleanup(part: str) -> bool:
    """Check if a command part matches an explicitly allowed cleanup pattern.

    The part is checked against the raw form and also stripped of a leading
    cd/path prefix (e.g., ``cd /project && rm -rf dist/`` yields ``rm -rf dist/``
    as a separate part after compound splitting).
    """
    stripped = part.strip()
    if not stripped:
        return False

    for pattern in ALLOWED_CLEANUP_PATTERNS:
        if pattern.search(stripped):
            return True

    return False


def _is_destructive_part(part: str) -> bool:
    """Check if a single command part contains a destructive pattern.

    A part is destructive if it:
    - Uses rm with recursive flags (-r, -rf, -Rf, etc.) on a non-allowed target
    - Uses find with -delete
    - Uses rm -r with shell variable expansion
    """
    stripped = part.strip()
    if not stripped:
        return False

    # Check for rm with recursive flag
    if _RM_RECURSIVE.search(stripped):
        return True

    # Check for find -delete
    if _FIND_DELETE.search(stripped):
        return True

    return False


def is_destructive_command(command: Optional[str]) -> bool:
    """Check if a shell command contains destructive patterns.

    Splits compound commands (&&, ||, ;) and checks each part individually.
    Parts matching ALLOWED_CLEANUP_PATTERNS are skipped.

    Args:
        command: Shell command string. Can be None or empty.

    Returns:
        True if the command contains destructive patterns that should be blocked.
    """
    if not command:
        return False

    parts = _split_compound_command(command)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Check if this part is an allowed cleanup command
        if _is_allowed_cleanup(part):
            continue

        # Check if this part is destructive
        if _is_destructive_part(part):
            logger.warning(
                f"[DESTRUCTIVE COMMAND GUARD] Blocked: {part[:100]}"
            )
            return True

    return False
