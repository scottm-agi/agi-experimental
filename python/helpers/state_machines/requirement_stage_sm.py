"""Requirement Stage State Machine.

RCA-475: Tracks the lifecycle of a single requirement stage (bdd, tdd, or code).
Lifecycle: pending → assigned → delegation_returned → completed → verified
With branches for: skipped, regressed, partial, failed (terminal).
"""
from __future__ import annotations

from python.helpers.agent_state_machine import AgentStateMachine


class RequirementStageSM(AgentStateMachine):
    """State machine for a single requirement stage (bdd/tdd/code)."""

    WAL_ENABLED = True

    VALID_STATUSES = frozenset({
        "pending",
        "assigned",
        "delegation_returned",
        "completed",
        "verified",
        "regressed",
        "skipped",
        "partial",
        "failed",
        "unverified",
    })

    VALID_TRANSITIONS = {
        "pending": frozenset({
            "assigned",
            "completed",      # auto-complete (e.g. pre-existing artifact)
            "skipped",         # not applicable
        }),
        "assigned": frozenset({
            "delegation_returned",
            "completed",       # direct completion
            "failed",
            "pending",         # de-assignment / reassignment
        }),
        "delegation_returned": frozenset({
            "completed",
            "failed",
            "partial",
            "pending",         # retry delegation
        }),
        "completed": frozenset({
            "verified",
            "regressed",
        }),
        "verified": frozenset(),          # terminal
        "regressed": frozenset({
            "pending",         # retry
        }),
        "skipped": frozenset(),           # terminal
        "partial": frozenset(),           # terminal
        "failed": frozenset(),            # terminal
        "unverified": frozenset({
            "completed",       # re-verified successfully
            "pending",         # needs re-work
        }),
    }

    INITIAL_STATUS = "pending"
