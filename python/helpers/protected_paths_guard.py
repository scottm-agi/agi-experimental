"""
Protected Paths Guard -- Block destructive commands targeting orchestrator state
AND enforce universal rm -rf prohibition.

Two layers of protection:

1. ORIGINAL (RCA-232): Block deletion of protected orchestrator state files
   (.agix.proj, requirements_ledger.json, etc.) and whole-directory wipes
   (rm -rf . / rm -rf *).

2. NEW (RCA-333): Universal rm -rf block. ALL recursive deletion commands are
   blocked by default. The ONLY exception is targeting a KNOWN-SAFE build/cache
   subdirectory inside a project (e.g., .next, dist, build, tmp, coverage).
   The allowlist is examined PROGRAMMATICALLY -- no human approval needed.

Usage:
    from python.helpers.protected_paths_guard import guard_protected_paths
    block_msg = guard_protected_paths(command)
    if block_msg:
        return Response(message=block_msg, break_loop=False)
"""

from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger("agix.protected_paths_guard")


# ── Protected file/directory names ──
# These orchestrator state artifacts must NEVER be deleted by sub-agent commands.
PROTECTED_PATHS = {
    ".agix.proj",
    ".agix.proj",                   # legacy meta dir
    "requirements_ledger.json",  # legacy root location
    "requirements-ledger.json",  # canonical docs/ location
    ".write_ledger.json",
    "content_manifest.json",     # legacy root location
    "content-manifest.json",     # canonical docs/ location
    "decomposition-index.json",  # canonical docs/ location
}


# ── Known-safe subdirectory patterns for programmatic approval ──
# These are build artifacts and caches that are always safe to delete.
# The path after the project root must START with one of these basenames.
SAFE_CLEANUP_DIRS = {
    ".next",
    ".turbo",
    ".cache",
    ".parcel-cache",
    ".nuxt",
    ".output",
    ".svelte-kit",
    "dist",
    "build",
    "out",
    "tmp",
    "coverage",
    ".coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules/.cache",       # cache subdir only, NOT node_modules root
    "node_modules/.vite",
    "node_modules/.tmp",
    # Agent-created temp staging dirs (legitimate workspace-internal scratch space)
    "tmp_scaffold",              # Next.js scaffold staging area
    "tmp_staging",               # Generic agent staging area
    "_scaffold",                 # Alternate scaffold temp dir
    "tmp_build",                 # Build temp dir
    "tmp_install",               # Install temp dir
    "scaffold_temp",             # AF-2a (ITR-49): Scaffold temp directory
}

# AF-2a: Prefix-based fallback for future-proofing scaffold naming
SAFE_CLEANUP_PREFIXES = ("tmp_", "_tmp", "scaffold", "staging_")


# ── Destructive command patterns ──

# 1. Whole-directory wipe: rm -rf . / rm -rf ./ / rm -rf *
_WIPE_ALL_PATTERN = re.compile(
    r"\brm\s+"                 # rm command
    r"(?:-[rRfFdv]+\s+)*"     # optional flags (-rf, -r, -f, etc.)
    r"(?:\.\s*$|\./\s*$|\*)",  # targets: ".", "./", "*"
    re.MULTILINE,
)

# 2. Targeted deletion of a protected file/dir:
#    rm [-rf] <protected_name>  OR  rmdir <protected_name>
# Built dynamically from PROTECTED_PATHS.
_PROTECTED_NAMES_ESCAPED = "|".join(re.escape(p) for p in PROTECTED_PATHS)
_RM_PROTECTED_PATTERN = re.compile(
    r"\b(?:rm|rmdir)\s+"                           # rm or rmdir
    r"(?:-[rRfFdvI]+\s+)*"                         # optional flags
    r"(?:[^\s;|&]*/)?"                             # optional leading path
    r"(?:" + _PROTECTED_NAMES_ESCAPED + r")\b",    # protected filename
)

# 3. xargs piped deletion
_XARGS_RM_PATTERN = re.compile(
    r"xargs\s+(?:rm|rmdir)\s+"
    r"(?:-[rRfFdvI]+\s+)*"
    r"(?:" + _PROTECTED_NAMES_ESCAPED + r")\b",
)

# 5. RCA-340: find -exec/-delete guard — catches find commands that delete files.
#    This MUST be checked BEFORE rm-specific checks because find -exec rm targets
#    are `{}` placeholders, not resolvable paths.
_FIND_DELETE_PATTERN = re.compile(
    r"\bfind\s+"                        # find command
    r"[^\n;|&]*?"                        # any args before -exec/-delete
    r"(?:"
    r"-exec\s+(?:rm|rmdir)\b"           # -exec rm / -exec rmdir
    r"|-delete\b"                        # -delete flag
    r")",
    re.IGNORECASE,
)

# 4. RCA-333: Universal recursive rm pattern -- catches ALL rm -r/-rf commands
#    with any absolute or relative path argument.
_RM_RECURSIVE_PATTERN = re.compile(
    r"\brm\s+"                       # rm command
    r"(?:-[rRfFdvI]+\s+)*"          # flags that MUST include -r or -R
    r"(/[^\s;|&]+)",                 # captures the absolute path target
)

# 5. System-critical path prefixes that are ALWAYS blocked
_SYSTEM_CRITICAL_PATHS = [
    "/agix/usr/projects/",   # projects parent (without trailing name)
    "/agix/usr/projects",    # projects parent (exact)
    "/agix/usr/",
    "/agix/usr",
    "/agix/",
    "/agix",
    "/agix/",
    "/agix",
    "/",
]

# Project directory roots where agents operate
_PROJECT_ROOTS = [
    "/agix/usr/projects/",
    "/agix/usr/projects/",
]


def _has_recursive_flag(flags_portion: str) -> bool:
    """Check if the flags contain -r or -R (recursive)."""
    return bool(re.search(r"[rR]", flags_portion))


def _extract_rm_targets(cmd: str) -> list:
    """Extract all path targets from rm commands in a command string.

    Handles chained commands (&&, ||, ;) by scanning the full string.
    Returns list of (flags, path) tuples.
    """
    results = []
    # Match: rm [-flags] path [path...]
    # We need to find all rm invocations in the command
    for match in re.finditer(
        r"\brm\s+((?:-[rRfFdvI]+\s+)*)([^\s;|&]+(?:\s+[^\s;|&]+)*)",
        cmd
    ):
        flags = match.group(1)
        paths_str = match.group(2)
        # Split paths (rm can take multiple path arguments)
        for path in paths_str.split():
            if path.startswith("-"):
                # This is another flag, not a path
                flags += path + " "
                continue
            results.append((flags, path))
    return results


def _is_safe_cleanup_path(path: str) -> bool:
    """Check if a path targets a known-safe build/cache directory inside a project.

    Programmatic approval: the path must be:
    1. Inside a project directory (e.g., /agix/usr/projects/<name>/...)
    2. The subdirectory after the project name must match a SAFE_CLEANUP_DIRS entry

    Returns True if the path is safe to delete, False otherwise.
    """
    # Normalize: strip trailing slashes
    normalized = path.rstrip("/")

    for root in _PROJECT_ROOTS:
        if not normalized.startswith(root):
            continue

        # Extract the part after the project root
        # e.g., /agix/usr/projects/my_app/.next -> after root = "my_app/.next"
        after_root = normalized[len(root):]

        # Must have at least project_name/subdir
        parts = after_root.split("/", 1)
        if len(parts) < 2 or not parts[1]:
            # This is the project root itself (no subdir) - NOT safe
            return False

        project_name = parts[0]
        subpath = parts[1]  # e.g., ".next", "node_modules/.cache", "dist"

        if not project_name:
            return False

        # Check against allowlist
        # The subpath must start with (or exactly match) a safe dir
        for safe_dir in SAFE_CLEANUP_DIRS:
            if subpath == safe_dir or subpath.startswith(safe_dir + "/"):
                return True

        # AF-2a: Prefix-based fallback — any directory matching a safe prefix is allowed
        subdir_name = subpath.split("/")[0]  # First path component only
        if any(subdir_name.startswith(p) for p in SAFE_CLEANUP_PREFIXES):
            return True

        # Not on the allowlist -- blocked by default
        return False

    # Not inside a known project root -- not safe
    return False


def _is_system_critical_path(path: str) -> bool:
    """Check if a path is a system-critical path that must NEVER be deleted."""
    normalized = path.rstrip("/")
    if not normalized:
        normalized = "/"

    for critical in _SYSTEM_CRITICAL_PATHS:
        critical_norm = critical.rstrip("/")
        if not critical_norm:
            critical_norm = "/"
        if normalized == critical_norm:
            return True

    return False


def _is_project_root_path(path: str) -> bool:
    """Check if a path is a project root (exactly projects/<name>, no subdirs)."""
    normalized = path.rstrip("/")
    for root in _PROJECT_ROOTS:
        if not normalized.startswith(root):
            continue
        after_root = normalized[len(root):]
        # Must be non-empty (has project name) but no slash (no subdir)
        if after_root and "/" not in after_root:
            return True
    return False


def guard_protected_paths(command: Optional[str]) -> Optional[str]:
    """Check if a terminal command would destroy protected state or violate rm -rf policy.

    Args:
        command: The shell command string. Can be None or empty.

    Returns:
        A blocking message string if the command is dangerous, or None if safe.
    """
    if not command or not isinstance(command, str):
        return None

    # Normalize for scanning: strip leading/trailing whitespace
    cmd = command.strip()
    if not cmd:
        return None

    # Skip non-destructive commands early (performance optimization)
    if not _could_be_destructive(cmd):
        return None

    # Check 1: Whole-directory wipe (rm -rf . / rm -rf * / rm -rf ./)
    if _WIPE_ALL_PATTERN.search(cmd):
        logger.warning(
            f"[PROTECTED_PATHS_GUARD] BLOCKED whole-directory wipe: {cmd[:120]}"
        )
        return _block_message(cmd, "Whole-directory wipe (rm -rf . / rm -rf *)")

    # Check 2: Targeted deletion of protected files
    if _RM_PROTECTED_PATTERN.search(cmd):
        # Find which protected path was targeted
        targeted = _find_targeted_path(cmd)
        logger.warning(
            f"[PROTECTED_PATHS_GUARD] BLOCKED deletion of protected path "
            f"'{targeted}': {cmd[:120]}"
        )
        return _block_message(cmd, f"Deletion of protected file: {targeted}")

    # Check 3: xargs-piped deletion
    if _XARGS_RM_PATTERN.search(cmd):
        targeted = _find_targeted_path(cmd)
        logger.warning(
            f"[PROTECTED_PATHS_GUARD] BLOCKED xargs deletion of protected path "
            f"'{targeted}': {cmd[:120]}"
        )
        return _block_message(cmd, f"xargs deletion of protected file: {targeted}")

    # Check 5 (RCA-340): find -exec/-delete guard
    if _FIND_DELETE_PATTERN.search(cmd):
        logger.warning(
            f"[PROTECTED_PATHS_GUARD] BLOCKED find -exec/-delete command: "
            f"{cmd[:120]}"
        )
        return _block_message_find(cmd)

    # Check 4 (RCA-333): Universal rm -rf guard
    # Extract all rm targets and check each one
    targets = _extract_rm_targets(cmd)
    for flags, path in targets:
        # Only care about recursive deletes
        if not _has_recursive_flag(flags):
            continue

        # Only care about absolute paths for the universal guard
        # (relative paths are handled by Check 1 above: rm -rf . / rm -rf *)
        if not path.startswith("/"):
            continue

        # System-critical paths: ALWAYS block
        if _is_system_critical_path(path):
            logger.warning(
                f"[PROTECTED_PATHS_GUARD] BLOCKED rm -rf on system-critical "
                f"path '{path}': {cmd[:120]}"
            )
            return _block_message_rm_rf(cmd, path, "system-critical path")

        # Project root paths: ALWAYS block
        if _is_project_root_path(path):
            logger.warning(
                f"[PROTECTED_PATHS_GUARD] BLOCKED rm -rf on project root "
                f"'{path}': {cmd[:120]}"
            )
            return _block_message_rm_rf(cmd, path, "project root directory")

        # Project subdirectory: check programmatic allowlist
        if _is_safe_cleanup_path(path):
            logger.info(
                f"[PROTECTED_PATHS_GUARD] ALLOWED rm -rf on safe cleanup "
                f"path '{path}'"
            )
            continue  # This target is safe, check next

        # Default: BLOCK -- path is not on allowlist
        logger.warning(
            f"[PROTECTED_PATHS_GUARD] BLOCKED rm -rf on non-allowlisted "
            f"path '{path}': {cmd[:120]}"
        )
        return _block_message_rm_rf(cmd, path, "path not on safe-cleanup allowlist")

    return None


def _could_be_destructive(cmd: str) -> bool:
    """Quick pre-check: does the command contain any destructive keywords?

    This avoids expensive regex scans on benign commands like `echo` or `ls`.
    RCA-340: Added 'find ' to catch find -exec rm and find -delete.
    """
    cmd_lower = cmd.lower()
    return (
        "rm " in cmd_lower
        or "rm\t" in cmd_lower
        or "rmdir " in cmd_lower
        or ("find " in cmd_lower and ("-exec" in cmd_lower or "-delete" in cmd_lower))
    )


def _find_targeted_path(cmd: str) -> str:
    """Extract the specific protected path name being targeted."""
    for path in PROTECTED_PATHS:
        if path in cmd:
            return path
    return "<unknown>"


def _block_message(command: str, reason: str) -> str:
    """Format a user-facing block message for protected paths."""
    return (
        f"\U0001f6e1\ufe0f **BLOCKED by Protected Paths Guard**\n\n"
        f"**Reason**: {reason}\n"
        f"**Command**: `{command[:200]}`\n\n"
        f"This command would destroy critical orchestrator state files. "
        f"Protected paths: {', '.join(sorted(PROTECTED_PATHS))}.\n\n"
        f"If you need to re-scaffold, use `npx create-*` which overwrites "
        f"source files without deleting the project root. Or delete specific "
        f"If you need to clean up workspace, use the `recursive_delete` tool "
        f"which is scoped to authorized directories (e.g., node_modules, build artifacts)."
    )


def _block_message_rm_rf(command: str, path: str, reason: str) -> str:
    """Format a user-facing block message for universal rm -rf guard."""
    safe_dirs_display = ", ".join(sorted(
        d for d in SAFE_CLEANUP_DIRS if "/" not in d
    )[:10])
    return (
        f"\U0001f6e1\ufe0f **BLOCKED by rm -rf Guard (RCA-333)**\n\n"
        f"**Reason**: Recursive deletion of {reason}\n"
        f"**Target path**: `{path}`\n"
        f"**Command**: `{command[:200]}`\n\n"
        f"**Policy**: `rm -rf` is restricted. Only safe build/cache dirs are allowed "
        f"for automated cleanup: {safe_dirs_display}.\n\n"
        f"**Action**: Use the `recursive_delete` tool for safe cleanup. "
        f"If you are attempting to clean a temp scaffold, ensure the path is "
        f"within an authorized temporary directory."
    )


def _block_message_find(command: str) -> str:
    """Format a user-facing block message for find -exec/-delete guard (RCA-340)."""
    return (
        f"\U0001f6e1\ufe0f **BLOCKED by find -exec Guard (RCA-340)**\n\n"
        f"**Reason**: `find` with `-exec rm`/`-exec rmdir`/`-delete` can "
        f"recursively delete files without explicit path targeting.\n"
        f"**Command**: `{command[:200]}`\n\n"
        f"**Policy**: `find -exec rm` is blocked because the `{{}}` placeholder "
        f"cannot be statically validated. Use the `recursive_delete` tool "
        f"instead, or delete specific directories with `rm -rf <subdir>/`.\n\n"
        f"If you need to selectively clean up files, list them first with "
        f"`find ... -print` and then delete individually."
    )
