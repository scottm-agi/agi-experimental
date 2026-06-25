"""
Phase Checkpoint Persistence — O(1) restart recovery.

SS-1/5: Persists phase completion state to .agix.proj/checkpoint.json
so that after a container restart the reconciler can skip the full artifact
scan for already-completed phases.

Architecture:
  - persist_phase_checkpoint(): Write/update checkpoint after phase status change
  - load_checkpoint(): Read checkpoint (returns None if missing)
  - validate_checkpoint(): Verify artifacts still exist on disk
  - increment_restart_counter(): Track restart count for escalation
  - detect_excessive_restarts(): DETECTOR 11 logic (used by structural guards)

Checkpoint file location: <project_dir>/.agix.proj/checkpoint.json
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.phase_checkpoint")

# ─── Constants ──────────────────────────────────────────────────────
CHECKPOINT_DIR = ".agix.proj"
CHECKPOINT_FILE = "checkpoint.json"
CHECKPOINT_VERSION = 1

# Restart thresholds for DETECTOR 11
RESTART_ADVISORY_THRESHOLD = 3   # 3+ → advisory warning
RESTART_ESCALATION_THRESHOLD = 6  # 6+ → high severity escalation


def _checkpoint_path(project_dir: str) -> str:
    """Return the absolute path to checkpoint.json."""
    return os.path.join(project_dir, CHECKPOINT_DIR, CHECKPOINT_FILE)


def _empty_checkpoint() -> Dict[str, Any]:
    """Return a fresh checkpoint structure."""
    return {
        "version": CHECKPOINT_VERSION,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "phases": {},
        "restart_count": 0,
    }


def persist_phase_checkpoint(
    project_dir: str,
    phase_id: str,
    status: str,
    artifacts: List[str],
) -> None:
    """Write/update checkpoint.json with phase completion state.

    Creates .agix.proj/ directory and checkpoint.json if they don't exist.
    Updates the entry for phase_id, preserving other phases' data.

    Args:
        project_dir: Absolute path to the project directory.
        phase_id: Phase identifier (e.g., "2.0", "2.5").
        status: Phase status (e.g., "completed", "in_progress").
        artifacts: List of relative artifact paths for this phase.
    """
    path = _checkpoint_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Load existing or create fresh
    data = _read_checkpoint_file(path) or _empty_checkpoint()

    now = datetime.now(timezone.utc).isoformat()
    data["last_updated"] = now

    phase_entry: Dict[str, Any] = {
        "status": status,
        "artifacts": artifacts,
    }

    if status == "completed":
        phase_entry["completed_at"] = now
    elif status == "in_progress":
        phase_entry["started_at"] = now

    data["phases"][phase_id] = phase_entry

    _write_checkpoint_file(path, data)
    logger.info(
        f"[CHECKPOINT] Persisted phase {phase_id} status={status} "
        f"({len(artifacts)} artifacts)"
    )


def load_checkpoint(project_dir: str) -> Optional[Dict[str, Any]]:
    """Load checkpoint.json. Returns None if missing or corrupt.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        Parsed checkpoint dict, or None if the file doesn't exist.
    """
    path = _checkpoint_path(project_dir)
    return _read_checkpoint_file(path)


def validate_checkpoint(
    project_dir: str,
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate checkpoint by verifying artifacts exist on disk.

    For each phase marked "completed", checks that at least one artifact
    exists on disk with >50 bytes (matching the reconciler's threshold).
    Phases whose artifacts are all missing get status changed to "needs_rework".

    Args:
        project_dir: Absolute path to the project directory.
        checkpoint: The checkpoint dict (from load_checkpoint).

    Returns:
        A new checkpoint dict (deep copy) with validated statuses.
    """
    import copy
    validated = copy.deepcopy(checkpoint)

    for phase_id, phase_data in validated.get("phases", {}).items():
        if phase_data.get("status") != "completed":
            continue  # Only validate completed phases

        artifacts = phase_data.get("artifacts", [])
        if not artifacts:
            continue  # No artifacts to check

        has_valid_artifact = False
        for artifact in artifacts:
            full_path = os.path.join(project_dir, artifact)
            if os.path.isfile(full_path) and os.path.getsize(full_path) > 50:
                has_valid_artifact = True
                break
            elif os.path.isdir(full_path) and os.listdir(full_path):
                has_valid_artifact = True
                break

        if not has_valid_artifact:
            phase_data["status"] = "needs_rework"
            phase_data["rework_reason"] = "artifacts_missing_on_disk"
            logger.warning(
                f"[CHECKPOINT] Phase {phase_id} marked needs_rework: "
                f"no valid artifacts found on disk"
            )

    return validated


def increment_restart_counter(project_dir: str) -> int:
    """Increment and return the restart counter in checkpoint.json.

    Creates checkpoint.json if it doesn't exist.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        The new restart_count value after incrementing.
    """
    path = _checkpoint_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    data = _read_checkpoint_file(path) or _empty_checkpoint()
    data["restart_count"] = data.get("restart_count", 0) + 1
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    _write_checkpoint_file(path, data)
    logger.info(
        f"[CHECKPOINT] Restart counter incremented to {data['restart_count']}"
    )
    return data["restart_count"]


def detect_excessive_restarts(project_dir: str) -> Optional[Dict[str, Any]]:
    """DETECTOR 11 logic: check restart_count against thresholds.

    This function is called by _10_structural_guards.py to detect
    excessive container restarts.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        None if below threshold, or a signal dict with detector/severity/detail.
    """
    checkpoint = load_checkpoint(project_dir)
    if checkpoint is None:
        return None

    restart_count = checkpoint.get("restart_count", 0)

    if restart_count >= RESTART_ESCALATION_THRESHOLD:
        return {
            "detector": "excessive_restarts",
            "severity": "high",
            "detail": (
                f"{restart_count} container restarts detected (threshold: "
                f"{RESTART_ESCALATION_THRESHOLD}). Pipeline may be in a "
                f"crash loop. Investigate root cause before continuing."
            ),
            "restart_count": restart_count,
        }
    elif restart_count >= RESTART_ADVISORY_THRESHOLD:
        return {
            "detector": "excessive_restarts",
            "severity": "medium",
            "detail": (
                f"{restart_count} container restarts detected (advisory "
                f"threshold: {RESTART_ADVISORY_THRESHOLD}). Monitor for "
                f"crash loop pattern."
            ),
            "restart_count": restart_count,
        }

    return None


# ─── Internal Helpers ───────────────────────────────────────────────

def _read_checkpoint_file(path: str) -> Optional[Dict[str, Any]]:
    """Read and parse checkpoint.json. Returns None on any failure."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"[CHECKPOINT] Failed to read {path}: {e}")
        return None


def _write_checkpoint_file(path: str, data: Dict[str, Any]) -> None:
    """Write checkpoint data to disk atomically (write-then-rename)."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except (IOError, OSError) as e:
        logger.error(f"[CHECKPOINT] Failed to write {path}: {e}")
        # Clean up temp file if rename failed
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
