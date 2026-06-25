"""Consolidated agent.data state dataclasses for P1-2 key cleanup.

Replaces ~58 individual agent.data keys with 7 structured state objects:
- LoopState: 12 loop-control keys → 1 dataclass
- TerminationState: 3 termination keys → 1 dataclass
- ToolFailureState: 13 tool failure keys → 1 dataclass
- GateState: 8 gate runtime state keys → 1 dataclass
- RedelegationState: 6 redelegation guard keys → 1 dataclass
- BuildState: 5 build error tracking keys → 1 dataclass
- GateCheckBlockCounts: 17 ITR-29 counter keys → 1 dict (no dataclass needed)

Each dataclass provides:
- Default values for all fields
- Type annotations
- to_dict() for serialization
- from_dict() classmethod for deserialization (ignores extra fields, handles None)

Architecture:
    from python.helpers.agent_data_state import LoopState, ToolFailureState, ...

    # Writer:
    loop_state = data.get("_loop_state", LoopState())
    loop_state.same_msg_repeat_count += 1
    data["_loop_state"] = loop_state

    # Reader:
    loop_state = data.get("_loop_state", LoopState())
    if loop_state.same_msg_repeat_count > threshold: ...

See: plan_p1_2_agent_data_cleanup.md §7 for full consolidation mapping.
"""

from dataclasses import dataclass, field, fields
from typing import Any


def _to_dict(obj: Any) -> dict:
    """Convert a dataclass to a dict, handling sets → lists for JSON safety."""
    result = {}
    for f in fields(obj):
        val = getattr(obj, f.name)
        if isinstance(val, set):
            result[f.name] = sorted(val)  # Convert sets to sorted lists
        else:
            result[f.name] = val
    return result


def _from_dict(cls, d: dict):
    """Create a dataclass from a dict, ignoring extra keys and handling type coercion.

    - Extra keys not in the dataclass are silently ignored.
    - Set-typed fields receive lists and convert them back to sets.
    """
    valid_fields = {f.name: f for f in fields(cls)}
    kwargs = {}
    for name, f in valid_fields.items():
        if name in d:
            val = d[name]
            # Convert lists back to sets for set-typed fields
            if f.default_factory is not None:
                try:
                    default = f.default_factory()
                except TypeError:
                    default = f.default
                if isinstance(default, set) and isinstance(val, list):
                    val = set(val)
            kwargs[name] = val
    return cls(**kwargs)


# ===========================================================================
# Group A: LoopState (replaces 12 keys → 1 key)
# ===========================================================================

@dataclass
class LoopState:
    """Consolidated loop detection and death spiral state.

    Replaces 12 individual agent.data keys with a single structured object.

    Writers: agent.py (primary), _38_verification_spiral_guard.py
    Readers: agent.py, _10_structural_guards.py, _45_intelligent_supervisor.py,
             _22_multiagentdev_completion_gate.py, orchestrator_gate_common.py

    Old key mapping:
        _same_message_repeat_count      → same_msg_repeat_count
        _same_message_cumulative_count  → same_msg_cumulative
        _semantic_repeat_count          → semantic_repeat_count
        _semantic_cumulative_count      → semantic_cumulative
        _empty_response_retries         → empty_response_retries
        _empty_response_cycles          → empty_response_cycles
        _empty_pressure_condensed       → empty_pressure_condensed
        _total_null_iterations          → total_null_iterations
        _truncation_retries             → truncation_retries
        _consecutive_blocked_tools      → consecutive_blocked_tools
        _last_tool_was_blocked          → last_tool_was_blocked
        _escape_hatch                   → escape_hatch
    """
    # Message repetition tracking
    same_msg_repeat_count: int = 0
    same_msg_cumulative: int = 0
    semantic_repeat_count: int = 0
    semantic_cumulative: int = 0

    # Empty/null response tracking
    empty_response_retries: int = 0
    empty_response_cycles: int = 0
    empty_pressure_condensed: bool = False
    total_null_iterations: int = 0
    truncation_retries: int = 0

    # Blocked tool tracking
    consecutive_blocked_tools: int = 0
    last_tool_was_blocked: bool = False

    # Escape hatch / termination
    escape_hatch: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for storage/logging."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LoopState":
        """Deserialize from dict, ignoring unknown fields."""
        return _from_dict(cls, d)


# ===========================================================================
# Group A: TerminationState (replaces 3 keys → 1 key)
# ===========================================================================

@dataclass
class TerminationState:
    """Consolidated termination/completion signaling.

    Replaces 3 overlapping completion signals.

    Writers: agent.py, _22_gate.py, response.py, _38_guard.py, _45_supervisor.py
    Readers: agent.py, _22_gate.py, response.py, _45_supervisor.py, gate_common.py

    Old key mapping:
        _delivery_complete → delivery_complete
        _force_response    → force_response
        _tool_call_dedup   → tool_call_dedup
    """
    delivery_complete: bool = False
    force_response: bool = False
    tool_call_dedup: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TerminationState":
        """Deserialize from dict."""
        return _from_dict(cls, d)


# ===========================================================================
# Group B: ToolFailureState (replaces 13 keys → 1 key)
# ===========================================================================

@dataclass
class ToolFailureState:
    """Consolidated tool failure tracking.

    Replaces 13 individual keys all owned by _12_tool_failure_tracker.py.
    NOTE: _tracker_blocked_tools is kept as a separate key (active read/write/propagate cycle).
    NOTE: _l2_escalation_signals is kept as a separate key (multi-writer: L1 guards + tracker).

    Writers: _12_tool_failure_tracker.py
    Readers: _12_tool_failure_tracker.py, _10_structural_guards.py,
             _45_intelligent_supervisor.py, orchestrator_gate_common.py

    Old key mapping:
        _tool_failed_in_current_turn  → failed_in_current_turn
        _consecutive_mistake_count    → consecutive_mistake_count
        _last_consecutive_fail_tool   → last_consecutive_fail_tool
        _circuit_breaker_triggered    → circuit_breaker_triggered
        _circuit_breaker_tool         → circuit_breaker_tool
        _circuit_breaker_count        → circuit_breaker_count
        _tool_failure_counts          → failure_counts
        _block_cooldown_counter       → block_cooldown_counter
        _timeout_command_counts       → timeout_command_counts
        _tool_failure_error_context   → error_context
        _auth_error_tracker           → auth_error_tracker
        _session_hint_counts          → session_hint_counts
    """
    # Per-turn state
    failed_in_current_turn: bool = False
    consecutive_mistake_count: int = 0
    last_consecutive_fail_tool: str = ""

    # Circuit breaker
    circuit_breaker_triggered: bool = False
    circuit_breaker_tool: str = ""
    circuit_breaker_count: int = 0

    # Per-tool tracking
    failure_counts: dict = field(default_factory=dict)
    block_cooldown_counter: int = 0
    timeout_command_counts: dict = field(default_factory=dict)
    error_context: dict = field(default_factory=dict)

    # Special tracking
    auth_error_tracker: dict = field(default_factory=dict)
    session_hint_counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ToolFailureState":
        """Deserialize from dict."""
        return _from_dict(cls, d)


# ===========================================================================
# Group D: GateState (replaces 8 keys → 1 key)
# ===========================================================================

@dataclass
class GateState:
    """Consolidated gate runtime state.

    Writers: orchestrator_gate_common.py, _22_multiagentdev_completion_gate.py
    Readers: orchestrator_gate_common.py, _45_intelligent_supervisor.py,
             gate_rejection_cap.py, redelegation_guard.py

    Old key mapping:
        _orchestrator_completion_blocks    → completion_blocks
        _error_state_bypassed              → error_state_bypassed
        _error_state_bypass_phase          → error_state_bypass_phase
        _error_state_degraded              → error_state_degraded
        _last_bypass_failing_check         → last_bypass_failing_check
        _last_gate_failing_check           → last_gate_failing_check
        _consecutive_duplicate_responses   → consecutive_dup_responses
        _last_blocked_response             → last_blocked_response
    """
    completion_blocks: int = 0
    error_state_bypassed: bool = False
    error_state_bypass_phase: int = 0
    error_state_degraded: bool = False
    last_bypass_failing_check: str = ""
    last_gate_failing_check: str = ""
    consecutive_dup_responses: int = 0
    last_blocked_response: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GateState":
        """Deserialize from dict."""
        return _from_dict(cls, d)


# ===========================================================================
# Group E: RedelegationState (replaces 6 keys → 1 key)
# ===========================================================================

@dataclass
class RedelegationState:
    """Consolidated redelegation guard state.

    Writer: redelegation_guard.py
    Readers: redelegation_guard.py, orchestrator_gate_common.py,
             _45_intelligent_supervisor.py

    Old key mapping:
        _gate_redelegation_tracker      → tracker
        _last_gate_block_details        → last_block_details
        _gate_block_history             → block_history
        _compound_deadlock_signals      → compound_deadlock_signals
        _compound_deadlock_override     → compound_deadlock_override
        _relevant_delegation_profiles   → relevant_profiles
    """
    tracker: dict = field(default_factory=dict)
    last_block_details: dict = field(default_factory=dict)
    block_history: list = field(default_factory=list)
    compound_deadlock_signals: dict = field(default_factory=dict)
    compound_deadlock_override: bool = False
    relevant_profiles: set = field(default_factory=set)

    def to_dict(self) -> dict:
        """Serialize to dict (sets → sorted lists for JSON safety)."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RedelegationState":
        """Deserialize from dict (lists → sets for set-typed fields)."""
        return _from_dict(cls, d)


# ===========================================================================
# Group F: BuildState (replaces 5 keys → 1 key)
# ===========================================================================

@dataclass
class BuildState:
    """Consolidated build error tracking.

    Writer: node_project.py
    Readers: node_project.py, _22_multiagentdev_completion_gate.py

    Old key mapping:
        _build_retry_count      → retry_count
        _test_retry_count       → test_retry_count
        _same_build_error_count → same_error_count
        _last_build_error       → last_error
        _attempted_fixes        → attempted_fixes
    """
    retry_count: int = 0
    test_retry_count: int = 0
    same_error_count: int = 0
    last_error: str = ""
    attempted_fixes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BuildState":
        """Deserialize from dict."""
        return _from_dict(cls, d)
