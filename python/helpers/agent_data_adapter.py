"""Dual-write adapter for P1-2 agent.data key consolidation.

Keeps old individual keys in sync with new consolidated dataclass objects.
Each sync_*() function reads old keys and populates the corresponding
_*_state dataclass. Old keys are NEVER deleted — they still drive all
decisions during migration.

Architecture:
    # After any write to _same_message_repeat_count:
    from python.helpers.agent_data_adapter import sync_loop_state
    sync_loop_state(agent.data)

    # Read with fallback:
    from python.helpers.agent_data_adapter import get_loop_state
    loop_state = get_loop_state(agent.data)

CRITICAL: All sync_*() functions are wrapped in try/except so they
NEVER break existing behavior. They are purely additive.

See: plan_p1_2_agent_data_cleanup.md §7 for full consolidation mapping.
"""

import logging

logger = logging.getLogger("agix.agent_data_adapter")


# ===========================================================================
# Key → Field mappings (old key → dataclass field name)
# ===========================================================================

_LOOP_STATE_MAP = {
    "_same_message_repeat_count": "same_msg_repeat_count",
    "_same_message_cumulative_count": "same_msg_cumulative",
    "_semantic_repeat_count": "semantic_repeat_count",
    "_semantic_cumulative_count": "semantic_cumulative",
    "_empty_response_retries": "empty_response_retries",
    "_empty_response_cycles": "empty_response_cycles",
    "_empty_pressure_condensed": "empty_pressure_condensed",
    "_total_null_iterations": "total_null_iterations",
    "_truncation_retries": "truncation_retries",
    "_consecutive_blocked_tools": "consecutive_blocked_tools",
    "_last_tool_was_blocked": "last_tool_was_blocked",
    "_escape_hatch": "escape_hatch",
}

_TERMINATION_STATE_MAP = {
    "_delivery_complete": "delivery_complete",
    "_force_response": "force_response",
    "_tool_call_dedup": "tool_call_dedup",
}

_TOOL_FAILURE_STATE_MAP = {
    "_tool_failed_in_current_turn": "failed_in_current_turn",
    "_consecutive_mistake_count": "consecutive_mistake_count",
    "_last_consecutive_fail_tool": "last_consecutive_fail_tool",
    "_circuit_breaker_triggered": "circuit_breaker_triggered",
    "_circuit_breaker_tool": "circuit_breaker_tool",
    "_circuit_breaker_count": "circuit_breaker_count",
    "_tool_failure_counts": "failure_counts",
    "_block_cooldown_counter": "block_cooldown_counter",
    "_timeout_command_counts": "timeout_command_counts",
    "_tool_failure_error_context": "error_context",
    "_auth_error_tracker": "auth_error_tracker",
    "_session_hint_counts": "session_hint_counts",
}

_GATE_STATE_MAP = {
    "_orchestrator_completion_blocks": "completion_blocks",
    "_error_state_bypassed": "error_state_bypassed",
    "_error_state_bypass_phase": "error_state_bypass_phase",
    "_error_state_degraded": "error_state_degraded",
    "_last_bypass_failing_check": "last_bypass_failing_check",
    "_last_gate_failing_check": "last_gate_failing_check",
    "_consecutive_duplicate_responses": "consecutive_dup_responses",
    "_last_blocked_response": "last_blocked_response",
}

_REDELEGATION_STATE_MAP = {
    "_gate_redelegation_tracker": "tracker",
    "_last_gate_block_details": "last_block_details",
    "_gate_block_history": "block_history",
    "_compound_deadlock_signals": "compound_deadlock_signals",
    "_compound_deadlock_override": "compound_deadlock_override",
    "_relevant_delegation_profiles": "relevant_profiles",
}

_BUILD_STATE_MAP = {
    "_build_retry_count": "retry_count",
    "_test_retry_count": "test_retry_count",
    "_same_build_error_count": "same_error_count",
    "_last_build_error": "last_error",
    "_attempted_fixes": "attempted_fixes",
}


# ===========================================================================
# Generic sync helper
# ===========================================================================

def _sync_state(agent_data: dict, state_key: str, state_cls, key_map: dict) -> None:
    """Generic dual-write sync: read old keys → populate dataclass.

    Args:
        agent_data: The agent.data dict
        state_key: Key to store the dataclass (e.g. "_loop_state")
        state_cls: The dataclass class to instantiate
        key_map: Old key → dataclass field name mapping
    """
    try:
        # Get or create the state object
        existing = agent_data.get(state_key)
        if not isinstance(existing, state_cls):
            # Create fresh if missing or corrupted
            existing = state_cls()

        # Copy old key values into dataclass fields
        for old_key, field_name in key_map.items():
            if old_key in agent_data:
                setattr(existing, field_name, agent_data[old_key])

        agent_data[state_key] = existing
    except Exception as e:
        # NEVER crash — this is additive only
        logger.debug(f"[ADAPTER] sync {state_key} failed (non-fatal): {e}")


def _get_state(agent_data: dict, state_key: str, state_cls, key_map: dict):
    """Generic read accessor: return consolidated state, falling back to old keys.

    Args:
        agent_data: The agent.data dict
        state_key: Key where the dataclass is stored
        state_cls: The dataclass class
        key_map: Old key → dataclass field name mapping

    Returns:
        An instance of state_cls, populated from either the consolidated key
        or constructed from old individual keys as fallback.
    """
    # Prefer consolidated state if it exists and is the right type
    existing = agent_data.get(state_key)
    if isinstance(existing, state_cls):
        return existing

    # Fallback: construct from old keys
    kwargs = {}
    for old_key, field_name in key_map.items():
        if old_key in agent_data:
            kwargs[field_name] = agent_data[old_key]

    try:
        return state_cls(**kwargs)
    except Exception:
        return state_cls()


# ===========================================================================
# Per-group sync functions
# ===========================================================================

def sync_loop_state(agent_data: dict) -> None:
    """Sync old loop keys → LoopState dataclass.

    Call after any loop counter increment (e.g. _same_message_repeat_count,
    _semantic_repeat_count, _empty_response_retries, etc.).
    """
    from python.helpers.agent_data_state import LoopState
    _sync_state(agent_data, "_loop_state", LoopState, _LOOP_STATE_MAP)


def sync_build_state(agent_data: dict) -> None:
    """Sync old build keys → BuildState dataclass.

    Call after any build counter increment (e.g. _build_retry_count).
    """
    from python.helpers.agent_data_state import BuildState
    _sync_state(agent_data, "_build_state", BuildState, _BUILD_STATE_MAP)


def sync_tool_failure_state(agent_data: dict) -> None:
    """Sync old tool failure keys → ToolFailureState dataclass.

    Call after any tool failure tracking update.
    """
    from python.helpers.agent_data_state import ToolFailureState
    _sync_state(agent_data, "_tool_failure_state", ToolFailureState, _TOOL_FAILURE_STATE_MAP)


def sync_gate_state(agent_data: dict) -> None:
    """Sync old gate keys → GateState dataclass.

    Call after any gate state update.
    """
    from python.helpers.agent_data_state import GateState
    _sync_state(agent_data, "_gate_state", GateState, _GATE_STATE_MAP)


def sync_redelegation_state(agent_data: dict) -> None:
    """Sync old redelegation keys → RedelegationState dataclass.

    Call after any redelegation guard state update.
    """
    from python.helpers.agent_data_state import RedelegationState
    _sync_state(agent_data, "_redelegation_state", RedelegationState, _REDELEGATION_STATE_MAP)


def sync_termination_state(agent_data: dict) -> None:
    """Sync old termination keys → TerminationState dataclass.

    Call after any termination signaling update.
    """
    from python.helpers.agent_data_state import TerminationState
    _sync_state(agent_data, "_termination_state", TerminationState, _TERMINATION_STATE_MAP)


def sync_all_states(agent_data: dict) -> None:
    """Convenience: sync all 6 state groups.

    Useful for periodic full-sync, e.g. at monologue_start.
    """
    sync_loop_state(agent_data)
    sync_build_state(agent_data)
    sync_tool_failure_state(agent_data)
    sync_gate_state(agent_data)
    sync_redelegation_state(agent_data)
    sync_termination_state(agent_data)


# ===========================================================================
# Read accessors with fallback
# ===========================================================================

def get_loop_state(agent_data: dict):
    """Read consolidated LoopState, falling back to old keys if needed."""
    from python.helpers.agent_data_state import LoopState
    return _get_state(agent_data, "_loop_state", LoopState, _LOOP_STATE_MAP)


def get_build_state(agent_data: dict):
    """Read consolidated BuildState, falling back to old keys if needed."""
    from python.helpers.agent_data_state import BuildState
    return _get_state(agent_data, "_build_state", BuildState, _BUILD_STATE_MAP)


def get_tool_failure_state(agent_data: dict):
    """Read consolidated ToolFailureState, falling back to old keys if needed."""
    from python.helpers.agent_data_state import ToolFailureState
    return _get_state(agent_data, "_tool_failure_state", ToolFailureState, _TOOL_FAILURE_STATE_MAP)


def get_gate_state(agent_data: dict):
    """Read consolidated GateState, falling back to old keys if needed."""
    from python.helpers.agent_data_state import GateState
    return _get_state(agent_data, "_gate_state", GateState, _GATE_STATE_MAP)


def get_redelegation_state(agent_data: dict):
    """Read consolidated RedelegationState, falling back to old keys if needed."""
    from python.helpers.agent_data_state import RedelegationState
    return _get_state(agent_data, "_redelegation_state", RedelegationState, _REDELEGATION_STATE_MAP)


def get_termination_state(agent_data: dict):
    """Read consolidated TerminationState, falling back to old keys if needed."""
    from python.helpers.agent_data_state import TerminationState
    return _get_state(agent_data, "_termination_state", TerminationState, _TERMINATION_STATE_MAP)
