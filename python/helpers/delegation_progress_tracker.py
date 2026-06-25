"""
Delegation Progress Tracker — RCA-352.

Stateless module that tracks delegation frequency and detects progress stalls
by comparing requirements ledger snapshots at fixed intervals.

Problem (RCA-352): Orchestrator made 39 call_subordinate calls in 97 minutes
because all 3 existing loop detectors were bypassed. Those detectors check
for *identical* delegations — but the orchestrator was sending *different*
messages that all failed to make progress.

Solution: Track total delegations and periodically snapshot the number of
completed requirements. If completions plateau across checkpoints, emit
a stall signal for supervisor escalation.

All state lives in agent_data['_delegation_progress'] — survives context
condensation with no extra persistence layer needed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from python.helpers.task_hash import strip_delegation_context
from python.helpers.requirements_ledger import _ensure_ledger

logger = logging.getLogger("agix.delegation_progress_tracker")


# ─── Internal Helpers ────────────────────────────────────────────────────

def _ensure_progress(agent_data: dict) -> dict:
    """Ensure _delegation_progress exists in agent_data and return it."""
    if "_delegation_progress" not in agent_data:
        agent_data["_delegation_progress"] = {
            "total_count": 0,
            "checkpoints": [],
            "recent_messages": [],
            "stall_count": 0,
        }
    progress = agent_data["_delegation_progress"]
    if not isinstance(progress, dict):
        agent_data["_delegation_progress"] = {
            "total_count": 0,
            "checkpoints": [],
            "recent_messages": [],
            "stall_count": 0,
        }
        progress = agent_data["_delegation_progress"]
    return progress


def _escalation_severity(stall_count: int) -> str:
    """Map stall count to escalation severity.

    stall_count == 1 → 'warning'
    stall_count >= 2 → 'critical'
    """
    if stall_count <= 0:
        return "info"
    if stall_count == 1:
        return "warning"
    return "critical"


def _count_completed_reqs(agent_data: dict) -> int:
    """Count completed/verified requirements from the ledger."""
    ledger = _ensure_ledger(agent_data)
    return len([
        r for r in ledger.get("requirements", [])
        if r.get("status") in ("completed", "verified")
    ])


# ─── Public API ──────────────────────────────────────────────────────────

def record_delegation(agent_data: dict, message: str, profile: str) -> dict:
    """Record a delegation and return progress state.

    Increments total_count, stores message preview in recent_messages
    (last 10, first 200 chars stripped via strip_delegation_context).

    Args:
        agent_data: The agent.data dict.
        message: The delegation message text.
        profile: The target subordinate profile name.

    Returns:
        The current _delegation_progress dict.
    """
    progress = _ensure_progress(agent_data)

    # Increment count
    progress["total_count"] = progress.get("total_count", 0) + 1

    # Create stripped preview (first 200 chars)
    stripped = strip_delegation_context(message)
    preview = stripped[:200]

    # Add to recent_messages
    entry = {
        "delegation_num": progress["total_count"],
        "message_preview": preview,
        "profile": profile,
    }
    recent = progress.get("recent_messages", [])
    recent.append(entry)
    # Keep only last 10
    if len(recent) > 10:
        recent = recent[-10:]
    progress["recent_messages"] = recent

    return progress


def check_progress(agent_data: dict) -> Optional[dict]:
    """Check if a progress stall is detected.

    Called at every 10th delegation (when total_count % 10 == 0).

    1. Count completed REQs from the requirements ledger.
    2. Take a checkpoint snapshot.
    3. Compare with previous checkpoint:
       - If current_completed == previous_completed → stall detected
       - If progress made → reset stall_count to 0
    4. Return stall signal dict or None.

    Args:
        agent_data: The agent.data dict.

    Returns:
        Stall signal dict if stall detected, None otherwise.
    """
    progress = _ensure_progress(agent_data)
    total = progress.get("total_count", 0)

    # Only checkpoint at every 10th delegation
    if total == 0 or total % 10 != 0:
        return None

    # Count completed requirements
    reqs_completed = _count_completed_reqs(agent_data)

    # Take checkpoint snapshot
    checkpoint = {
        "at_delegation": total,
        "reqs_completed": reqs_completed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    checkpoints = progress.get("checkpoints", [])

    # Compare with previous checkpoint (if any)
    if checkpoints:
        previous = checkpoints[-1]
        prev_completed = previous.get("reqs_completed", 0)

        if reqs_completed <= prev_completed:
            # STALL: No new requirements completed
            progress["stall_count"] = progress.get("stall_count", 0) + 1
            stall_count = progress["stall_count"]

            checkpoints.append(checkpoint)
            progress["checkpoints"] = checkpoints

            stall_signal = {
                "detector": "delegation_stall",
                "severity": _escalation_severity(stall_count),
                "detail": (
                    f"No progress in last 10 delegations "
                    f"(delegations {previous['at_delegation']}→{total}, "
                    f"completed REQs stuck at {reqs_completed})"
                ),
                "stall_count": stall_count,
                "recent_messages": list(progress.get("recent_messages", [])),
            }

            logger.warning(
                f"[DELEGATION PROGRESS] Stall detected: "
                f"stall_count={stall_count}, severity={stall_signal['severity']}, "
                f"reqs_completed={reqs_completed}"
            )

            return stall_signal
        else:
            # Progress! Reset stall count
            progress["stall_count"] = 0
            checkpoints.append(checkpoint)
            progress["checkpoints"] = checkpoints

            logger.info(
                f"[DELEGATION PROGRESS] Progress confirmed: "
                f"{prev_completed}→{reqs_completed} completed REQs"
            )
            return None
    else:
        # First checkpoint — no previous to compare
        checkpoints.append(checkpoint)
        progress["checkpoints"] = checkpoints
        return None


def get_delegation_count(agent_data: dict) -> int:
    """Get total delegation count."""
    progress = agent_data.get("_delegation_progress", {})
    return progress.get("total_count", 0)


def get_stall_count(agent_data: dict) -> int:
    """Get consecutive stall count."""
    progress = agent_data.get("_delegation_progress", {})
    return progress.get("stall_count", 0)
