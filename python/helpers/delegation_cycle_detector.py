"""Delegation Cycle Detector — detects stuck delegation patterns.

FIX-017: Provides L1 deterministic detection of delegation cycles
where the same phase fails consecutively, plus outcome tracking.

Architecture:
    - track_delegation_outcome(): Records each delegation result per phase
    - check_delegation_stuck(): Returns (stuck, msg) when 3+ consecutive failures
    - MAX_PHASE_CONSECUTIVE_FAILURES = 3

Used by call_subordinate.py:
    - BEFORE delegation: check_delegation_stuck() → block if stuck
    - AFTER delegation: track_delegation_outcome() → record result
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("agix.delegation_cycle_detector")

# Maximum consecutive failures on the same phase before declaring stuck
MAX_PHASE_CONSECUTIVE_FAILURES = 3

# Maximum history entries per phase (prevent unbounded growth)
MAX_PHASE_HISTORY = 10


def check_delegation_stuck(
    agent_data: dict,
    phase_key: str,
) -> tuple[bool, str]:
    """Check if a phase has too many consecutive delegation failures.

    Counts consecutive failures from the END of the history. A success
    anywhere in the sequence resets the count.

    Args:
        agent_data: The agent's data dict.
        phase_key: The phase identifier (e.g., "3", "5.1").

    Returns:
        Tuple of (stuck: bool, message: str).
        - stuck=True, message=<warning> if >= MAX_PHASE_CONSECUTIVE_FAILURES.
        - stuck=False, message="" otherwise.
    """
    phase_attempts = agent_data.get("_phase_delegation_attempts", {})
    phase_history = phase_attempts.get(phase_key, [])

    if not phase_history:
        return False, ""

    # Count consecutive failures from the end
    consecutive_failures = 0
    failure_statuses = {"failed", "partial", "escalated", "cancelled"}
    for attempt in reversed(phase_history):
        if attempt.get("status") in failure_statuses:
            consecutive_failures += 1
        else:
            break

    if consecutive_failures >= MAX_PHASE_CONSECUTIVE_FAILURES:
        msg = (
            f"🛑 DELEGATION CYCLE DETECTED: Phase '{phase_key}' has failed "
            f"{consecutive_failures} consecutive delegations. "
            f"Deliver current results via `response` tool NOW."
        )
        logger.warning(msg)
        return True, msg

    return False, ""


def track_delegation_outcome(
    agent_data: dict,
    phase_key: str,
    status: str,
) -> None:
    """Record a delegation outcome for cycle detection.

    Args:
        agent_data: The agent's data dict.
        phase_key: The phase identifier.
        status: Result status (e.g., "completed", "failed", "partial").
    """
    phase_attempts = agent_data.setdefault("_phase_delegation_attempts", {})
    phase_history = phase_attempts.setdefault(phase_key, [])

    phase_history.append({
        "status": status,
        "timestamp": time.time(),
    })

    # Cap at MAX_PHASE_HISTORY entries
    if len(phase_history) > MAX_PHASE_HISTORY:
        phase_attempts[phase_key] = phase_history[-MAX_PHASE_HISTORY:]
