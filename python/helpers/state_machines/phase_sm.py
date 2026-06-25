"""Phase State Machine.

RCA-475: Tracks phase lifecycle with forward-only progression as a GOAL.
The done gate handles completion validation per-requirement.
Forward-only is aspirational — completed→pending is ALLOWED (audit-logged)
so the gate system can revert phases when requirements regress.
Terminal states: verified, skipped.
"""
from __future__ import annotations

from python.helpers.agent_state_machine import AgentStateMachine


class PhaseStateMachine(AgentStateMachine):
    """State machine for orchestrator phase progression.

    Forward progression is the GOAL, but not a hard block.
    The done gate validates completion per-requirement.
    """

    WAL_ENABLED = True

    VALID_STATUSES = frozenset({
        "pending",
        "in_progress",
        "partially_completed",
        "completed",
        "verified",
        "skipped",
        "blocked",
        "deferred",
    })

    VALID_TRANSITIONS = {
        "pending": frozenset({
            "in_progress",
            "completed",           # auto-complete (trivial phase)
            "skipped",             # not applicable
            "blocked",
            "deferred",
        }),
        "in_progress": frozenset({
            "completed",
            "partially_completed",
            "blocked",
        }),
        "partially_completed": frozenset({
            "completed",
            "in_progress",         # resume work
            "blocked",
        }),
        "completed": frozenset({
            "verified",            # gate passed
            "pending",             # requirement regressed — gate reverts phase
            "in_progress",         # more work needed
        }),
        "verified": frozenset(),           # terminal
        "skipped": frozenset(),            # terminal
        "blocked": frozenset({
            "pending",             # unblocked
            "in_progress",         # resume directly
        }),
        "deferred": frozenset({
            "pending",             # un-deferred
        }),
    }

    INITIAL_STATUS = "pending"

