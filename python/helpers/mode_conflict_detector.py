"""
Mode Conflict Detector — Fix 4 from RCA comprehensive audit.

Detects when a quality gate's block message advises "delegate to fix X"
but the agent's current mode blocks delegation tools. In this case,
the agent is trapped: it can't fix the issue itself (gate blocks delivery)
and can't delegate (mode blocks tools). The only escape is to escalate
to the parent with a mode_conflict signal.
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger("agix.mode_conflict_detector")

# Patterns indicating the gate message advises delegation
_DELEGATION_ADVICE_PATTERNS = [
    re.compile(r"delegate\b", re.IGNORECASE),
    re.compile(r"call_subordinate", re.IGNORECASE),
    re.compile(r"subordinate.*fix", re.IGNORECASE),
    re.compile(r"profile\s*=\s*['\"]?(code|frontend)", re.IGNORECASE),
]

# Modes that block delegation tools
_MODES_BLOCKING_DELEGATION = {"code", "frontend", "architect", "research"}


def contains_delegation_advice(message: str) -> bool:
    """Check if a gate block message contains delegation advice.

    Args:
        message: The gate block/rejection message text.

    Returns:
        True if the message advises the agent to delegate work.
    """
    return any(p.search(message) for p in _DELEGATION_ADVICE_PATTERNS)


def mode_blocks_delegation(mode: str) -> bool:
    """Check if a given mode blocks delegation tools.

    Args:
        mode: The current mode slug (e.g., 'code', 'orchestrator').

    Returns:
        True if this mode blocks delegation tools.
    """
    return mode.lower() in _MODES_BLOCKING_DELEGATION


def detect_mode_conflict(
    block_message: str,
    current_mode: str,
) -> str | None:
    """Detect and produce escalation when delegation is advised but blocked.

    Args:
        block_message: The gate block/rejection message.
        current_mode: The agent's current mode slug.

    Returns:
        Escalation message if conflict detected, None otherwise.
    """
    if not contains_delegation_advice(block_message):
        return None

    if not mode_blocks_delegation(current_mode):
        return None

    logger.warning(
        f"[MODE CONFLICT] Gate advises delegation but mode '{current_mode}' "
        f"blocks delegation tools — escalating to parent"
    )

    return (
        f"🔴 MODE CONFLICT DETECTED\n\n"
        f"The quality gate is advising you to delegate work, but your current "
        f"mode ({current_mode}) blocks delegation tools.\n\n"
        f"You CANNOT fix this yourself AND you CANNOT delegate.\n\n"
        f"**Action Required**: escalate to your parent orchestrator. "
        f"Report this mode conflict so the parent can re-delegate with "
        f"the correct profile, or perform the fix itself.\n\n"
        f"Do NOT retry the same tool — it will be blocked again.\n"
    )
