"""
Pre-Phase-5 Full Project Snapshot via rsync.

Creates a complete rsync-based snapshot of the project directory before
Phase 5 (verification/fix) begins, so that verification agents cannot
permanently damage working code. Supports snapshot, restore, and diff.

Architecture:
    - Snapshot goes to <project_dir>/tmp/pre_phase5_snapshot/
    - Rsync excludes: node_modules, .next, .git, tmp/, __pycache__,
      dist/, build/, .turbo, coverage/
    - Includes everything else: src/, prisma/, package.json, etc.

Entry points:
    create_snapshot(project_dir)  — rsync project → snapshot dir
    restore_from_snapshot(project_dir) — rsync snapshot → project dir
    diff_against_snapshot(project_dir) — compare current vs snapshot
    snapshot_exists(project_dir)  — check if snapshot dir exists
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

SNAPSHOT_SUBDIR = "tmp/pre_phase5_snapshot"

# Rsync exclude patterns — heavy/generated/transient dirs
_RSYNC_EXCLUDES = [
    "node_modules",
    ".next",
    ".git",
    "tmp/",
    "__pycache__",
    "dist/",
    "build/",
    ".turbo",
    "coverage/",
]


# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════

def _snapshot_path(project_dir: str) -> str:
    """Return the absolute snapshot directory path."""
    return os.path.join(project_dir, SNAPSHOT_SUBDIR)


def _build_rsync_excludes() -> list[str]:
    """Build rsync --exclude flags from the exclusion list."""
    args: list[str] = []
    for pattern in _RSYNC_EXCLUDES:
        args.extend(["--exclude", pattern])
    return args


def _count_files_and_bytes(directory: str) -> tuple[int, int]:
    """Walk directory tree and count files + total bytes."""
    file_count = 0
    total_bytes = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            filepath = os.path.join(root, name)
            try:
                total_bytes += os.path.getsize(filepath)
                file_count += 1
            except OSError:
                pass
    return file_count, total_bytes


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def create_snapshot(project_dir: str) -> dict:
    """Rsync the ENTIRE project (minus excludes) to the snapshot directory.

    Creates/overwrites <project_dir>/tmp/pre_phase5_snapshot/.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        {success: bool, snapshot_path: str, file_count: int, total_bytes: int}
    """
    snap_dir = _snapshot_path(project_dir)
    os.makedirs(snap_dir, exist_ok=True)

    # rsync -a --delete ensures exact mirror; trailing / on source is critical
    src = project_dir.rstrip("/") + "/"
    dst = snap_dir.rstrip("/") + "/"

    cmd = [
        "rsync", "-a", "--delete",
        *_build_rsync_excludes(),
        src, dst,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logger.error("[SNAPSHOT] rsync failed: %s", exc.stderr)
        return {
            "success": False,
            "snapshot_path": snap_dir,
            "file_count": 0,
            "total_bytes": 0,
        }

    file_count, total_bytes = _count_files_and_bytes(snap_dir)

    logger.info(
        "[SNAPSHOT] Created: %s (%d files, %d bytes)",
        snap_dir, file_count, total_bytes,
    )

    return {
        "success": True,
        "snapshot_path": snap_dir,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def restore_from_snapshot(project_dir: str) -> dict:
    """Rsync the snapshot BACK to the project directory.

    Restores all snapshotted files. Does NOT touch excluded directories
    (node_modules, .next, etc.) because rsync excludes apply to restore too.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        {success: bool, files_restored: int}
    """
    snap_dir = _snapshot_path(project_dir)

    if not os.path.isdir(snap_dir):
        logger.warning("[SNAPSHOT] No snapshot found at %s", snap_dir)
        return {"success": False, "files_restored": 0}

    src = snap_dir.rstrip("/") + "/"
    dst = project_dir.rstrip("/") + "/"

    cmd = [
        "rsync", "-a",
        *_build_rsync_excludes(),
        src, dst,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logger.error("[SNAPSHOT] rsync restore failed: %s", exc.stderr)
        return {"success": False, "files_restored": 0}

    file_count, _ = _count_files_and_bytes(snap_dir)

    logger.info("[SNAPSHOT] Restored %d files from %s", file_count, snap_dir)

    return {"success": True, "files_restored": file_count}


def diff_against_snapshot(project_dir: str) -> dict:
    """Compare current project state against the snapshot.

    Uses rsync --dry-run --itemize-changes to detect differences.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        {changed: list[str], added: list[str], deleted: list[str]}
    """
    snap_dir = _snapshot_path(project_dir)
    result: dict = {"changed": [], "added": [], "deleted": []}

    if not os.path.isdir(snap_dir):
        logger.warning("[SNAPSHOT] No snapshot to diff against at %s", snap_dir)
        return result

    # Compare current project → snapshot to find what changed/added
    # rsync --dry-run --itemize-changes shows what WOULD be transferred
    src = project_dir.rstrip("/") + "/"
    dst = snap_dir.rstrip("/") + "/"

    cmd = [
        "rsync", "-a", "--dry-run", "--itemize-changes",
        *_build_rsync_excludes(),
        src, dst,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        output = proc.stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("[SNAPSHOT] rsync diff failed: %s", exc)
        return result

    # Parse rsync --itemize-changes output
    # Format: YXcstpoguax path/to/file
    # Y = update type: < = sent, > = received, c = created, h = hardlink, . = unchanged, * = message
    # X = file type: f = file, d = directory, L = symlink, etc.
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or len(line) < 12:
            continue

        # Extract flags and path
        flags = line[:11]
        filepath = line[12:] if len(line) > 12 else line[11:]
        filepath = filepath.strip()

        if not filepath or filepath.endswith("/"):
            continue  # Skip directories

        file_type = flags[1] if len(flags) > 1 else ""
        if file_type != "f":
            continue  # Only track files

        update_type = flags[0]
        change_flags = flags[2:] if len(flags) > 2 else ""

        # Determine change type
        if update_type == "c" or (change_flags and change_flags[0] == "+"):
            # New file (created / not in destination)
            result["added"].append(filepath)
        elif any(c not in ".  " for c in change_flags):
            # Content changed
            result["changed"].append(filepath)

    # Check for deleted files: files in snapshot but NOT in current project
    # Reverse the comparison direction
    cmd_rev = [
        "rsync", "-a", "--dry-run", "--itemize-changes",
        *_build_rsync_excludes(),
        dst, src,
    ]

    try:
        proc_rev = subprocess.run(cmd_rev, capture_output=True, text=True)
        output_rev = proc_rev.stdout
    except (subprocess.CalledProcessError, OSError):
        return result

    for line in output_rev.strip().splitlines():
        line = line.strip()
        if not line or len(line) < 12:
            continue

        flags = line[:11]
        filepath = line[12:] if len(line) > 12 else line[11:]
        filepath = filepath.strip()

        if not filepath or filepath.endswith("/"):
            continue

        file_type = flags[1] if len(flags) > 1 else ""
        if file_type != "f":
            continue

        update_type = flags[0]
        change_flags = flags[2:] if len(flags) > 2 else ""

        # Files that exist in snapshot but not in project = deleted
        if update_type == "c" or (change_flags and change_flags[0] == "+"):
            # This file exists in snapshot but not in current project
            if filepath not in result["added"]:
                result["deleted"].append(filepath)

    return result


def snapshot_exists(project_dir: str) -> bool:
    """Check if a pre-Phase-5 snapshot exists.

    Returns True if the snapshot directory exists AND contains at least
    one file (not just an empty dir).

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        True if a snapshot is available for restore.
    """
    snap_dir = _snapshot_path(project_dir)
    if not os.path.isdir(snap_dir):
        return False

    # Check for at least one file (avoid false positive on empty dir)
    try:
        for _root, _dirs, files in os.walk(snap_dir):
            if files:
                return True
    except OSError:
        pass

    return False
