"""Chat-scoped decomposition index reconciliation.

ARCH-RCSIG Wave 3B: Ensures decomposition_index.json is resilient to
container restarts by stamping it with the current chat session ID.

When a new chat session begins, implementation-and-beyond phase statuses
(IMPLEMENTATION, INTEGRATION, VERIFICATION, DELIVERY) that were marked
'completed' by the previous session are reset to 'pending' — because
those completions are only valid within the session that produced them.

Planning and design phase completions are preserved because they are
verified by artifact existence (files on disk survive restarts).

Consumers:
    - requirements.py (called during init action)
    - call_subordinate.py (could call on first delegation per chat)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("agent.decomp_chat_scope")

# Phase categories that should be RESET on new chat session.
# These are "runtime" phases — their completion is tied to the session.
_RUNTIME_PHASE_MAJORS = frozenset({3, 4, 5, 6, 7})

# Phase categories that should be PRESERVED across chat sessions.
# These are "artifact" phases — their completion can be verified from disk.
# _ARTIFACT_PHASE_MAJORS = frozenset({0, 1, 2})  # Not used directly


def _extract_major(seq: Any) -> int | None:
    """Extract the integer major part from a phase sequence.

    "3.1" → 3, "0.5" → 0, "2.3" → 2, "abc" → None
    """
    if seq is None:
        return None
    s = str(seq).strip()
    if not s:
        return None
    import re
    m = re.match(r"^(\d+)", s)
    return int(m.group(1)) if m else None


def reconcile_chat_session(
    project_dir: str,
    current_chat_id: str,
) -> Dict[str, Any]:
    """Reconcile decomposition_index.json for the current chat session.

    If the stored _chat_id differs from current_chat_id, this means
    a new session has started (e.g., container restart). In that case:
    - Implementation/integration/verification/delivery phases with
      status='completed' are reset to 'pending'
    - Planning/design phases are preserved (verified by artifact existence)
    - The _chat_id is updated to current_chat_id

    Args:
        project_dir: Absolute path to the project directory.
        current_chat_id: The current chat session ID.

    Returns:
        Dict with keys:
            phases_reset: Number of phases reset to pending
            chat_id_set: Whether _chat_id was first-time set
    """
    result = {"phases_reset": 0, "chat_id_set": False}

    try:
        from python.helpers.projects import get_decomp_index_path
        decomp_path = get_decomp_index_path(project_dir)
    except Exception:
        decomp_path = os.path.join(project_dir, "docs", "decomposition-index.json")

    if not os.path.isfile(decomp_path):
        return result

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp_data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return result

    # Handle both list and dict formats
    is_dict_format = isinstance(decomp_data, dict)
    if is_dict_format:
        phases_list = (
            decomp_data.get("tasks")
            or decomp_data.get("milestones")
            or decomp_data.get("phases")
            or []
        )
        stored_chat_id = decomp_data.get("_chat_id", "")
    else:
        # Plain list format — wrap in dict for chat_id storage
        phases_list = decomp_data if isinstance(decomp_data, list) else []
        stored_chat_id = ""

    if not isinstance(phases_list, list):
        return result

    # First-time: no stored chat_id → just set it, don't reset anything
    if not stored_chat_id:
        if is_dict_format:
            decomp_data["_chat_id"] = current_chat_id
        else:
            decomp_data = {"phases": phases_list, "_chat_id": current_chat_id}
        with open(decomp_path, "w", encoding="utf-8") as f:
            json.dump(decomp_data, f, indent=2)
        result["chat_id_set"] = True
        return result

    # Same chat → no changes needed
    if stored_chat_id == current_chat_id:
        return result

    # NEW CHAT SESSION — reset runtime phases
    reset_count = 0
    for phase in phases_list:
        seq = phase.get("seq", phase.get("phase_seq", phase.get("sequence", "")))
        status = phase.get("status", "")

        # Only reset phases that are "completed" — pending stays pending
        if status not in ("completed", "done"):
            continue

        major = _extract_major(seq)
        if major is not None and major in _RUNTIME_PHASE_MAJORS:
            phase["status"] = "pending"
            phase["_reset_reason"] = f"chat session changed: {stored_chat_id} → {current_chat_id}"
            # Preserve old evidence for audit trail
            if "completion_evidence" in phase:
                phase["_prev_completion_evidence"] = phase.pop("completion_evidence")
            reset_count += 1
            logger.info(
                f"[DECOMP-CHAT-SCOPE] Reset phase {seq} to pending "
                f"(chat session changed: {stored_chat_id} → {current_chat_id})"
            )

    result["phases_reset"] = reset_count

    # Update chat_id
    if is_dict_format:
        decomp_data["_chat_id"] = current_chat_id
        # Update phases in the wrapper
        for key in ("tasks", "milestones", "phases"):
            if key in decomp_data:
                decomp_data[key] = phases_list
                break
    else:
        decomp_data = {"phases": phases_list, "_chat_id": current_chat_id}

    with open(decomp_path, "w", encoding="utf-8") as f:
        json.dump(decomp_data, f, indent=2)

    if reset_count > 0:
        logger.warning(
            f"[DECOMP-CHAT-SCOPE] New chat session detected — "
            f"reset {reset_count} runtime phase(s) to pending"
        )

    return result
