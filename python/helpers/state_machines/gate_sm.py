"""Gate Progress State Machine.

RCA-475: Tracks gate (bdd, tdd, done) progress through the quality pipeline.
Terminal states: passed, partial_escape (no transitions out).
"""
from __future__ import annotations

from python.helpers.agent_state_machine import AgentStateMachine


class GateProgressSM(AgentStateMachine):
    """State machine for quality gate progression."""

    WAL_ENABLED = True

    VALID_STATUSES = frozenset({"pending", "passed", "partial_escape"})
    VALID_TRANSITIONS = {
        "pending": frozenset({"passed", "partial_escape"}),
        "passed": frozenset(),
        "partial_escape": frozenset(),
    }
    INITIAL_STATUS = "pending"
