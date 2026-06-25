"""RetryBudget Bridge — Shadow-Mode Dual-Write Adapter.

Phase 2 of the P0-1 Death Spiral Unification migration
(plan_p0_1_death_spiral_unification.md §6, Steps 2-5).

This module provides thin shadow-write functions that intercept events
from the existing loop detectors and dual-write to the new
RetryBudgetManager. The old counters STILL DRIVE ALL DECISIONS —
the shadow calls are passive observers that log divergences.

Safety guarantee:
    Every shadow function wraps its body in try/except so a bug in
    the new budget system can NEVER break existing production behavior.

Usage in existing code (example):
    # In same_message_bridge.py, after bridge_same_message_to_l1():
    from python.helpers.retry_budget_bridge import shadow_same_message_event
    shadow_same_message_event(
        agent_data=agent.data,
        repeat_count=repeat_count,
        tool_name=tool_name,
        old_decision_would_stop=should_hard_stop,
    )

Divergence logging:
    When ``old_decision_would_stop`` is provided, the bridge compares
    it with the new budget's decision and logs a WARNING if they
    disagree. These logs are the primary signal for verifying
    behavior parity before switching to budget-driven decisions.

RCA references: RCA-252, RCA-260, RCA-263 (same-message)
                RCA-281 (build loop)
                RCA-316, RCA-345 (circuit breaker / death spiral)
"""

from __future__ import annotations

import logging
from typing import Optional

from python.helpers.retry_budget import (
    RetryBudgetManager,
    RetryState,
    OperationType,
    RetryDecision,
)

logger = logging.getLogger("agix.retry_budget_bridge")


# =============================================================================
# FIX-029: Migration Phase Control
# =============================================================================


class MigrationPhase:
    """4-phase migration for counter fragmentation unification.

    SHADOW_ONLY: Old counters drive decisions, new budget system is passive observer.
    DUAL_MODE: New budget system drives decisions, old counters still track.
                If old and new disagree, log a WARNING with details.
                This is the safe validation phase before full migration.
    NEW_PRIMARY_OLD_FALLBACK: New budget system drives decisions, falls back
                              to old if no budget manager.
    NEW_ONLY: Only new budget system drives decisions, old counters removed.
    """
    SHADOW_ONLY = "shadow_only"
    DUAL_MODE = "dual_mode"
    NEW_PRIMARY_OLD_FALLBACK = "new_primary_old_fallback"
    NEW_ONLY = "new_only"


# Module-level migration phase (default: SHADOW_ONLY — safe starting point)
_MIGRATION_PHASE = MigrationPhase.SHADOW_ONLY


def get_migration_phase() -> str:
    """Get the current migration phase.

    Returns:
        One of MigrationPhase.SHADOW_ONLY, NEW_PRIMARY_OLD_FALLBACK, or NEW_ONLY.
    """
    return _MIGRATION_PHASE


def set_migration_phase(phase: str) -> None:
    """Set the migration phase.

    Args:
        phase: One of MigrationPhase.SHADOW_ONLY, NEW_PRIMARY_OLD_FALLBACK, or NEW_ONLY.
    """
    global _MIGRATION_PHASE
    valid = {
        MigrationPhase.SHADOW_ONLY,
        MigrationPhase.DUAL_MODE,
        MigrationPhase.NEW_PRIMARY_OLD_FALLBACK,
        MigrationPhase.NEW_ONLY,
    }
    if phase not in valid:
        raise ValueError(f"Invalid migration phase: {phase}. Must be one of {valid}")
    _MIGRATION_PHASE = phase
    logger.info(f"RetryBudget migration phase set to: {phase}")


# =============================================================================
# Budget Accessor
# =============================================================================


def get_or_create_budget(agent_data: dict) -> RetryBudgetManager:
    """Get or create the RetryBudgetManager from agent.data.

    If ``_retry_budget`` is missing or is not a ``RetryBudgetManager``
    instance, creates a new one and stores it in agent_data.

    Args:
        agent_data: The agent's ``self.data`` dict.

    Returns:
        The RetryBudgetManager instance.
    """
    existing = agent_data.get("_retry_budget")
    if isinstance(existing, RetryBudgetManager):
        return existing

    budget = RetryBudgetManager()
    agent_data["_retry_budget"] = budget
    return budget


# =============================================================================
# P0-1: Decision-Returning Functions (budget-driven)
# =============================================================================


def decide_same_message(
    agent_data: dict,
    repeat_count: int,
    tool_name: str | None = None,
) -> RetryDecision:
    """Budget-driven decision for same-message detection.

    Replaces the old `should_hard_stop_same_message()` counter-based decision.
    Returns RetryDecision.action: 'retry' (continue), 'escalate' (signal supervisor),
    'force_complete' (hard stop), or 'terminal' (absorbing stop).
    """
    budget = get_or_create_budget(agent_data)
    decision = budget.record_failure(
        OperationType.SAME_MESSAGE_EXACT,
        context=f"repeat_count={repeat_count}",
        tool_name=tool_name,
    )
    return decision


def decide_semantic_repeat(
    agent_data: dict,
    repeat_count: int,
    tool_name: str | None = None,
) -> RetryDecision:
    """Budget-driven decision for semantic repeat detection."""
    budget = get_or_create_budget(agent_data)
    decision = budget.record_failure(
        OperationType.SAME_MESSAGE_SEMANTIC,
        context=f"semantic_repeat_count={repeat_count}",
        tool_name=tool_name,
    )
    return decision


def decide_gate_block(
    agent_data: dict,
    check_name: str = "",
) -> RetryDecision:
    """Budget-driven decision for gate block events."""
    budget = get_or_create_budget(agent_data)
    decision = budget.record_failure(
        OperationType.GATE,
        context=f"check={check_name}",
    )
    return decision



# =============================================================================
# OVL-1b: DUAL_MODE Decision Functions
# =============================================================================


def dual_mode_decide_same_message(
    agent_data: dict,
    repeat_count: int,
    tool_name: str | None = None,
    old_would_stop: bool = False,
) -> RetryDecision:
    """DUAL_MODE-aware decision for same-message detection.

    Behavior depends on migration phase:
      - SHADOW_ONLY: Old counter drives. Returns a synthetic RetryDecision
        based on old_would_stop. Budget is recorded as shadow.
      - DUAL_MODE: New budget drives the decision. Old counter's opinion
        (old_would_stop) is compared, and divergences are logged.
      - NEW_PRIMARY_OLD_FALLBACK / NEW_ONLY: New budget drives exclusively.

    Args:
        agent_data: The agent's ``self.data`` dict.
        repeat_count: Consecutive same-message count from old system.
        tool_name: Optional tool name for planning-tool exemption.
        old_would_stop: Whether the old counter system would hard-stop.

    Returns:
        RetryDecision with the action to take.
    """
    phase = get_migration_phase()
    budget = get_or_create_budget(agent_data)

    # Always record in the budget (for tracking/comparison)
    budget_decision = budget.record_failure(
        OperationType.SAME_MESSAGE_EXACT,
        context=f"repeat_count={repeat_count}",
        tool_name=tool_name,
    )

    if phase == MigrationPhase.SHADOW_ONLY:
        # Old counter drives — return a synthetic decision based on old_would_stop
        if old_would_stop:
            return RetryDecision(
                action="escalate",
                retries_used=budget_decision.retries_used,
                retries_remaining=0,
                message=f"same_message_exact: old counter says STOP (repeat_count={repeat_count})",
                should_inject_warning=True,
                escalation_signal=None,
            )
        else:
            return RetryDecision(
                action="retry",
                retries_used=budget_decision.retries_used,
                retries_remaining=budget_decision.retries_remaining,
                message=f"same_message_exact: old counter says CONTINUE (repeat_count={repeat_count})",
                should_inject_warning=False,
                escalation_signal=None,
            )

    elif phase == MigrationPhase.DUAL_MODE:
        # New budget drives, but compare with old for divergence logging
        _log_divergence(
            "dual_mode_same_message",
            OperationType.SAME_MESSAGE_EXACT,
            old_would_stop,
            budget_decision.action,
        )
        return budget_decision

    else:
        # NEW_PRIMARY_OLD_FALLBACK or NEW_ONLY — budget drives exclusively
        return budget_decision


def _log_divergence(
    event_type: str,
    op_type: OperationType,
    old_would_stop: bool,
    new_action: str,
) -> None:
    """Log a divergence between old counter decision and new budget decision.

    Only called when ``old_decision_would_stop`` is explicitly provided.

    A divergence means the old system and new system disagree on whether
    to continue or stop. These are the critical signals for migration
    verification.

    Args:
        event_type: Human-readable name of the shadow event.
        op_type: The OperationType that was consumed.
        old_would_stop: Whether the old counter system would hard-stop.
        new_action: The RetryDecision.action from the new budget system.
    """
    new_would_stop = new_action in ("escalate", "force_complete", "terminal")

    if old_would_stop != new_would_stop:
        logger.warning(
            "RetryBudget DIVERGENCE [%s/%s]: old_would_stop=%s, "
            "new_action=%s (new_would_stop=%s)",
            event_type,
            op_type.value,
            old_would_stop,
            new_action,
            new_would_stop,
        )


# =============================================================================
# Shadow Event Functions — one per detector
# =============================================================================


def shadow_same_message_event(
    agent_data: dict | None,
    repeat_count: int,
    tool_name: str | None = None,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for same-message exact detection.

    Called alongside ``bridge_same_message_to_l1()`` in same_message_bridge.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        repeat_count: Consecutive same-message count from old system.
        tool_name: Optional tool name for planning-tool exemption.
        old_decision_would_stop: If provided, the old system's hard-stop
            decision. Used to detect divergences.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.SAME_MESSAGE_EXACT,
            context=f"repeat_count={repeat_count}",
            tool_name=tool_name,
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "same_message_exact",
                OperationType.SAME_MESSAGE_EXACT,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow same_message_event failed (non-fatal)", exc_info=True
        )


def shadow_semantic_repeat_event(
    agent_data: dict | None,
    repeat_count: int,
    tool_name: str | None = None,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for semantic same-message detection.

    Called alongside the semantic repeat detection in agent.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        repeat_count: Consecutive semantic repeat count from old system.
        tool_name: Optional tool name for planning-tool exemption.
        old_decision_would_stop: If provided, the old system's decision.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.SAME_MESSAGE_SEMANTIC,
            context=f"semantic_repeat_count={repeat_count}",
            tool_name=tool_name,
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "semantic_repeat",
                OperationType.SAME_MESSAGE_SEMANTIC,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow semantic_repeat_event failed (non-fatal)", exc_info=True
        )


def shadow_build_failure_event(
    agent_data: dict | None,
    project_dir: str,
    error_snippet: str = "",
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for build_loop_detector.record_failure().

    Called alongside ``BuildLoopDetector.record_failure()`` in
    build_loop_detector.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        project_dir: Absolute path to the project directory.
        error_snippet: First ~200 chars of the build error output.
        old_decision_would_stop: If provided, whether old detector
            returned a diagnostic (i.e., loop detected).
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.BUILD,
            context=f"project={project_dir} error={error_snippet[:200]}",
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "build_failure",
                OperationType.BUILD,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow build_failure_event failed (non-fatal)", exc_info=True
        )


def shadow_build_success_event(
    agent_data: dict | None,
    project_dir: str,
) -> None:
    """Shadow-write for build_loop_detector.record_success().

    Called alongside ``BuildLoopDetector.record_success()`` in
    build_loop_detector.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        project_dir: Absolute path to the project directory.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        budget.record_success(OperationType.BUILD)
    except Exception:
        logger.debug(
            "Shadow build_success_event failed (non-fatal)", exc_info=True
        )


def shadow_empty_response_event(
    agent_data: dict | None,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for empty response retry counter.

    Called alongside ``_empty_response_retries`` increment in agent.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        old_decision_would_stop: If provided, whether old system would stop.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.EMPTY_RESPONSE,
            context="empty_response_retry",
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "empty_response",
                OperationType.EMPTY_RESPONSE,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow empty_response_event failed (non-fatal)", exc_info=True
        )


def shadow_blocked_tools_event(
    agent_data: dict | None,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for consecutive blocked tools counter.

    Called alongside ``_consecutive_blocked_tools`` increment in agent.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        old_decision_would_stop: If provided, whether old system would stop.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.BLOCKED_TOOLS,
            context="blocked_tools_increment",
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "blocked_tools",
                OperationType.BLOCKED_TOOLS,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow blocked_tools_event failed (non-fatal)", exc_info=True
        )


def shadow_null_iteration_event(
    agent_data: dict | None,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for null iteration counter.

    Called alongside ``_total_null_iterations`` increment in agent.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        old_decision_would_stop: If provided, whether old system would stop.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.NULL_ITERATION,
            context="null_iteration_increment",
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "null_iteration",
                OperationType.NULL_ITERATION,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow null_iteration_event failed (non-fatal)", exc_info=True
        )


def shadow_truncation_event(
    agent_data: dict | None,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for truncation retry counter.

    Called alongside ``_truncation_retries`` increment in agent.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        old_decision_would_stop: If provided, whether old system would stop.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.TRUNCATION,
            context="truncation_retry",
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "truncation",
                OperationType.TRUNCATION,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow truncation_event failed (non-fatal)", exc_info=True
        )


def shadow_tool_dedup_event(
    agent_data: dict | None,
    tool_name: str,
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for tool dedup guard (Phases 1-5 in loop_detection).

    Called alongside tool dedup detection in agent_process_tools.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        tool_name: Name of the duplicated tool.
        old_decision_would_stop: If provided, whether old system would stop.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.TOOL_DEDUP,
            context=f"tool_dedup={tool_name}",
            tool_name=tool_name,
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "tool_dedup",
                OperationType.TOOL_DEDUP,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow tool_dedup_event failed (non-fatal)", exc_info=True
        )


def shadow_gate_rejection_event(
    agent_data: dict | None,
    gate_name: str = "",
    old_decision_would_stop: bool | None = None,
) -> None:
    """Shadow-write for gate rejection counter.

    Called alongside ``increment_gate_rejection_counter()`` and
    ``increment_global_gate_counter()`` in gate_rejection_cap.py.

    Args:
        agent_data: The agent's ``self.data`` dict.
        gate_name: Name of the gate that rejected.
        old_decision_would_stop: If provided, whether old system would stop.
    """
    try:
        if agent_data is None:
            return
        budget = get_or_create_budget(agent_data)
        decision = budget.record_failure(
            OperationType.GATE,
            context=f"gate_rejection={gate_name}",
        )
        if old_decision_would_stop is not None:
            _log_divergence(
                "gate_rejection",
                OperationType.GATE,
                old_decision_would_stop,
                decision.action,
            )
    except Exception:
        logger.debug(
            "Shadow gate_rejection_event failed (non-fatal)", exc_info=True
        )
