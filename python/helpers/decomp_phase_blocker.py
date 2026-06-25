"""
Decomposition Phase Blocker — marks phases as 'blocked' in decomposition_index.json.

RCA-462 ROOT CAUSE FIX: When the topic loop detector HARD_BLOCKs a delegation,
the corresponding phase in decomposition_index.json must transition to 'blocked'.
Without this, the phase stays 'pending', causing the phases_incomplete gate to
block the response → deadlock.

The 'blocked' status is in PHASE_DONE_STATUSES (RCA-460), so the gate skips it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("agix.decomp_phase_blocker")


def _get_decomp_path(project_dir: str) -> str:
    """Get the path to decomposition_index.json."""
    return os.path.join(project_dir, "decomposition_index.json")


def _extract_phase_seq(topic: str) -> Optional[str]:
    """Extract a phase sequence number from a topic string.

    E.g., "Phase 4.5" → "4.5", "Phase 3.1.2: Discovery" → "3.1.2"
    """
    match = re.search(r"[Pp]hase\s+([\d.]+)", topic)
    return match.group(1) if match else None


def _topic_matches_phase(topic: str, phase: dict) -> bool:
    """Check if a topic string matches a decomposition phase.

    Matches by:
    1. Exact title match (case-insensitive)
    2. Substring match (topic in title or title in topic)
    3. Phase sequence number match (e.g., "Phase 4.5" → seq "4.5")
    """
    title = phase.get("title", "")
    seq = phase.get("seq", "")

    topic_lower = topic.lower()
    title_lower = title.lower()

    # Exact match
    if topic_lower == title_lower:
        return True

    # Substring match (either direction)
    if topic_lower in title_lower or title_lower in topic_lower:
        return True

    # Phase sequence match
    extracted_seq = _extract_phase_seq(topic)
    if extracted_seq and extracted_seq == seq:
        return True

    return False


def mark_phase_blocked(project_dir: str, topic: str) -> bool:
    """Mark a phase as 'blocked' in decomposition_index.json.

    RCA-462: Called when the topic loop detector HARD_BLOCKs a delegation.
    Finds the phase that matches the blocked topic and sets its status
    to 'blocked'. This prevents the phases_incomplete gate from creating
    a deadlock.

    Args:
        project_dir: Absolute path to the project directory.
        topic: The topic/message that was HARD_BLOCKed (used to find
               the matching phase).

    Returns:
        True if a phase was found and updated, False otherwise.
    """
    decomp_path = _get_decomp_path(project_dir)
    if not os.path.isfile(decomp_path):
        logger.debug(f"[DECOMP BLOCKER] No decomposition_index.json at {decomp_path}")
        return False

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"[DECOMP BLOCKER] Failed to read decomp index: {e}")
        return False

    # Handle both dict-wrapped and list formats
    is_dict = isinstance(decomp, dict)
    if is_dict:
        phases = (
            decomp.get("tasks")
            or decomp.get("milestones")
            or decomp.get("phases")
            or []
        )
    elif isinstance(decomp, list):
        phases = decomp
    else:
        return False

    # Find and update matching phase(s)
    updated = False
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        if _topic_matches_phase(topic, phase):
            old_status = phase.get("status", "pending")
            phase["status"] = "blocked"
            updated = True
            logger.info(
                f"[DECOMP BLOCKER] Phase {phase.get('seq', '?')}: "
                f"'{phase.get('title', '?')}' → blocked "
                f"(was '{old_status}', topic='{topic[:60]}')"
            )

    if not updated:
        logger.debug(
            f"[DECOMP BLOCKER] No phase matched topic '{topic[:80]}' "
            f"in {len(phases)} phases"
        )
        return False

    # Write back
    try:
        with open(decomp_path, "w", encoding="utf-8") as f:
            json.dump(decomp if is_dict else phases, f, indent=2)
        logger.info("[DECOMP BLOCKER] Updated decomposition_index.json")
        return True
    except (IOError, OSError) as e:
        logger.error(f"[DECOMP BLOCKER] Failed to write decomp index: {e}")
        return False
