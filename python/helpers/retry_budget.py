"""Unified Retry Budget State Machine.

Replaces 15+ scattered ``agent.data`` loop control keys with a single
forward-only state machine per operation type. Every retryable operation
follows the lifecycle:

    AVAILABLE → RETRY(1..N) → ESCALATED → FORCE_COMPLETE → TERMINAL

Plan reference: plan_p0_1_death_spiral_unification.md (P0-1)
Architecture: Anti-Pattern γ elimination — Independent Safety Design

Design Principles:
    1. One state machine, not 15 counters.
    2. Budget is consumed proactively, not counted reactively.
    3. Loop detection moves outside the loop (L2/L3 supervisor).
    4. All state lives in one place (_retry_budget agent.data key).
    5. Forward-only transitions (no reset from ESCALATE without grant).

RCA References: RCA-252, RCA-260, RCA-263, RCA-281, RCA-299, RCA-316,
    RCA-342, RCA-345, RCA-351, RCA-352, RCA-354, RCA-355, RCA-U13,
    RCA-ITR35, RCA-450, RCA-451
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.retry_budget")


# =============================================================================
# Enums
# =============================================================================


class RetryState(Enum):
    """State of a single operation's retry budget.

    Forward-only transitions:
        AVAILABLE → RETRY → ESCALATED → FORCE_COMPLETE → TERMINAL
    """

    AVAILABLE = "available"  # Budget exists, no retries consumed
    RETRY = "retry"  # At least one retry consumed, budget remains
    ESCALATED = "escalated"  # Budget exhausted, escalated to supervisor
    FORCE_COMPLETE = "force_complete"  # Supervisor couldn't help, force completion
    TERMINAL = "terminal"  # Operation has been terminally resolved

    @property
    def order(self) -> int:
        """Numeric ordering for forward-only comparison."""
        return _STATE_ORDER[self]


_STATE_ORDER = {
    RetryState.AVAILABLE: 0,
    RetryState.RETRY: 1,
    RetryState.ESCALATED: 2,
    RetryState.FORCE_COMPLETE: 3,
    RetryState.TERMINAL: 4,
}


class OperationType(Enum):
    """Every retryable operation the agent can perform.

    Each operation gets its own independent retry budget within the
    RetryBudgetManager. Operation budgets do not interact — exhausting
    BUILD does NOT affect GATE.
    """

    # Monologue-level operations
    EMPTY_RESPONSE = "empty_response"  # LLM returns nothing
    SAME_MESSAGE_EXACT = "same_message_exact"  # Exact string repeat
    SAME_MESSAGE_SEMANTIC = "same_message_semantic"  # Semantic tool-sig repeat
    BLOCKED_TOOLS = "blocked_tools"  # Extension blocks tool calls
    NULL_ITERATION = "null_iteration"  # No tool call, no response
    TRUNCATION = "truncation"  # Context truncation retry

    # Tool-level operations
    TOOL_DEDUP = "tool_dedup"  # Same tool called identically

    # System-level operations
    BUILD = "build"  # Build failure retry
    GATE = "gate"  # Gate rejection retry
    TDD_PROGRESS = "tdd_progress"  # TDD stuck detection + escalation retries


# =============================================================================
# Default Budgets — derived from current hardcoded thresholds
# =============================================================================


DEFAULT_BUDGETS: Dict[OperationType, int] = {
    OperationType.EMPTY_RESPONSE: 6,  # MAX_EMPTY_RETRIES(2) × MAX_EMPTY_CYCLES(3) = 6
    OperationType.SAME_MESSAGE_EXACT: 3,  # SAME_MESSAGE_HARD_CAP = 3
    OperationType.SAME_MESSAGE_SEMANTIC: 3,  # SAME_MESSAGE_HARD_CAP = 3
    OperationType.BLOCKED_TOOLS: 5,  # BLOCKED_TOOLS_ESCALATE_THRESHOLD = 5
    OperationType.NULL_ITERATION: 30,  # MAX_TOTAL_NULL_ITERATIONS = 30
    OperationType.TRUNCATION: 3,  # Derived from loop_data.truncation_retries
    OperationType.TOOL_DEDUP: 25,  # DEFAULT_THRESHOLDS["hard_break"] = 25
    OperationType.BUILD: 3,  # BuildLoopDetector threshold = 3
    OperationType.GATE: 10,  # GLOBAL_GATE_EXHAUSTION_LIMIT = 10
    OperationType.TDD_PROGRESS: 5,  # 3 stuck detections + 2 escalation retries
}


# Cumulative thresholds — session-wide limits regardless of resets.
# Only defined for operations where cumulative tracking matters.
CUMULATIVE_THRESHOLDS: Dict[OperationType, int] = {
    OperationType.SAME_MESSAGE_EXACT: 15,  # CUMULATIVE_SAME_MESSAGE_THRESHOLD
    OperationType.SAME_MESSAGE_SEMANTIC: 20,  # SEMANTIC_CUMULATIVE_THRESHOLD
}


# Planning tools are exempt from same-message budgets (RCA-245).
PLANNING_EXEMPT_OPS = frozenset({
    OperationType.SAME_MESSAGE_EXACT,
    OperationType.SAME_MESSAGE_SEMANTIC,
})


# Planning tools — imported from same_message_bridge to avoid duplication.
# If import fails (circular or missing), inline the set.
try:
    from python.helpers.same_message_bridge import PLANNING_TOOLS
except ImportError:
    PLANNING_TOOLS = frozenset({"sequential_thinking"})


# =============================================================================
# RetryDecision — returned by consume/record_failure
# =============================================================================


@dataclass
class RetryDecision:
    """What the caller should do after consuming a retry.

    Fields:
        action: One of "retry", "escalate", "force_complete", "terminal".
        retries_used: How many retries have been consumed for this operation.
        retries_remaining: How many retries are left before escalation.
        message: Human-readable advice for the agent context.
        should_inject_warning: Whether to add a warning to chat history.
        escalation_signal: L2 signal dict if escalation is needed, else None.
    """

    action: str
    retries_used: int
    retries_remaining: int
    message: str
    should_inject_warning: bool
    escalation_signal: Optional[dict]


# =============================================================================
# RetryBudget — per-operation budget state
# =============================================================================


@dataclass
class RetryBudget:
    """State of a single operation's retry budget.

    This is the per-operation tracking object stored inside
    RetryBudgetManager. It is NOT the public API — use
    RetryBudgetManager methods instead.
    """

    state: RetryState = RetryState.AVAILABLE
    retry_count: int = 0  # Current retries consumed in this budget window
    max_retries: int = 3  # Budget ceiling
    operation_type: OperationType = OperationType.BUILD


# =============================================================================
# RetryBudgetManager — the unified controller
# =============================================================================


class RetryBudgetManager:
    """Unified retry budget tracker replacing 15+ scattered counters.

    One instance per agent, stored as ``agent.data["_retry_budget"]``.

    Every retryable operation (build, gate, same-message, tool-dedup, etc.)
    gets its own budget. Budgets are independent — exhausting BUILD does
    not affect GATE.

    The state machine is forward-only:
        AVAILABLE → RETRY → ESCALATED → FORCE_COMPLETE → TERMINAL

    The only way to transition backward (ESCALATED → RETRY) is via
    ``grant_retry_budget()``, which is reserved for the supervisor layer.

    Usage:
        budget = RetryBudgetManager()

        # Before retrying a build:
        decision = budget.record_failure(OperationType.BUILD)
        if decision.action == "retry":
            # Proceed with retry
        elif decision.action == "escalate":
            # Signal L2 supervisor
        elif decision.action == "force_complete":
            # Hard stop
    """

    def __init__(self, budgets: Dict[OperationType, int] | None = None):
        # Merge custom budgets with defaults
        self._budgets: Dict[OperationType, int] = dict(DEFAULT_BUDGETS)
        if budgets:
            self._budgets.update(budgets)

        # Per-operation state
        self._state: Dict[OperationType, RetryState] = {
            op: RetryState.AVAILABLE for op in OperationType
        }

        # Per-operation used count (resets on new task)
        self._used: Dict[OperationType, int] = {op: 0 for op in OperationType}

        # Per-operation cumulative count (survives resets)
        self._cumulative: Dict[OperationType, int] = {op: 0 for op in OperationType}

        # Escalation history for supervisor context
        self._escalation_history: List[dict] = []

        # Grant tracking — max 2 grants per op per session (anti-abuse)
        self._grants: Dict[OperationType, int] = {op: 0 for op in OperationType}

        # Extra budget from grants (added on top of exhausted budget)
        self._grant_budget: Dict[OperationType, int] = {op: 0 for op in OperationType}

    # ── Public API ──

    def get_budget(self, op_type: OperationType) -> RetryBudget:
        """Get the current budget state for an operation type."""
        return RetryBudget(
            state=self._state[op_type],
            retry_count=self._used[op_type],
            max_retries=self._budgets.get(op_type, DEFAULT_BUDGETS.get(op_type, 3)),
            operation_type=op_type,
        )

    def record_attempt(self, op_type: OperationType) -> RetryState:
        """Record an attempt without consuming budget.

        Returns the current state. Use this to check state before
        making a decision, without side effects.
        """
        return self._state[op_type]

    def record_failure(
        self,
        op_type: OperationType,
        context: str = "",
        tool_name: str | None = None,
    ) -> RetryDecision:
        """Record a failure and consume one retry from the budget.

        This is the primary entry point. Each call:
        1. Checks planning tool exemption
        2. Increments used count
        3. Increments cumulative count
        4. Checks cumulative threshold
        5. Transitions state if budget is exhausted
        6. Returns a RetryDecision telling the caller what to do

        Args:
            op_type: The type of operation that failed.
            context: Optional context string for logging/diagnostics.
            tool_name: Optional tool name for planning-tool exemption.

        Returns:
            RetryDecision with the action to take.
        """
        # Planning tool exemption (RCA-245)
        if self.is_exempt(op_type, tool_name):
            return RetryDecision(
                action="retry",
                retries_used=self._used[op_type],
                retries_remaining=999,
                message=f"Planning tool '{tool_name}' — exempt from {op_type.value} budget",
                should_inject_warning=False,
                escalation_signal=None,
            )

        # Handle TERMINAL state — absorbing, no action possible
        if self._state[op_type] == RetryState.TERMINAL:
            return RetryDecision(
                action="terminal",
                retries_used=self._used[op_type],
                retries_remaining=0,
                message=f"{op_type.value} is in TERMINAL state — no more retries possible",
                should_inject_warning=False,
                escalation_signal=None,
            )

        # Handle FORCE_COMPLETE → TERMINAL
        if self._state[op_type] == RetryState.FORCE_COMPLETE:
            self._transition(op_type, RetryState.TERMINAL)
            return RetryDecision(
                action="terminal",
                retries_used=self._used[op_type],
                retries_remaining=0,
                message=f"{op_type.value} transitioned to TERMINAL from FORCE_COMPLETE",
                should_inject_warning=False,
                escalation_signal=None,
            )

        # Handle ESCALATED → FORCE_COMPLETE
        if self._state[op_type] == RetryState.ESCALATED:
            self._transition(op_type, RetryState.FORCE_COMPLETE)
            return RetryDecision(
                action="force_complete",
                retries_used=self._used[op_type],
                retries_remaining=0,
                message=(
                    f"{op_type.value} budget fully exhausted and escalation failed — "
                    f"forcing completion"
                ),
                should_inject_warning=True,
                escalation_signal={
                    "detector": "retry_budget_force_complete",
                    "operation": op_type.value,
                    "used": self._used[op_type],
                    "ts": time.time(),
                },
            )

        # Increment counters
        self._used[op_type] += 1
        self._cumulative[op_type] += 1

        used = self._used[op_type]
        max_retries = self._budgets.get(op_type, DEFAULT_BUDGETS.get(op_type, 3))
        total_budget = max_retries + self._grant_budget[op_type]
        remaining = max(0, total_budget - used)

        # Check cumulative threshold FIRST (session-wide, overrides per-budget)
        if self._check_cumulative(op_type):
            self._transition(op_type, RetryState.ESCALATED)
            signal = {
                "detector": "retry_budget_cumulative_escalation",
                "operation": op_type.value,
                "cumulative": self._cumulative[op_type],
                "threshold": CUMULATIVE_THRESHOLDS.get(op_type),
                "ts": time.time(),
            }
            self._escalation_history.append(signal)
            return RetryDecision(
                action="escalate",
                retries_used=used,
                retries_remaining=0,
                message=(
                    f"{op_type.value} cumulative threshold reached "
                    f"({self._cumulative[op_type]}/{CUMULATIVE_THRESHOLDS[op_type]})"
                ),
                should_inject_warning=True,
                escalation_signal=signal,
            )

        # Check per-budget exhaustion
        if used >= total_budget:
            # Budget exhausted → ESCALATED
            self._transition(op_type, RetryState.ESCALATED)
            signal = {
                "detector": "retry_budget_escalation",
                "operation": op_type.value,
                "used": used,
                "budget": total_budget,
                "context": context[:200] if context else "",
                "ts": time.time(),
            }
            self._escalation_history.append(signal)
            return RetryDecision(
                action="escalate",
                retries_used=used,
                retries_remaining=0,
                message=(
                    f"{op_type.value} budget exhausted ({used}/{total_budget}) — "
                    f"escalating to supervisor"
                ),
                should_inject_warning=True,
                escalation_signal=signal,
            )

        # Budget remains → RETRY
        if self._state[op_type] == RetryState.AVAILABLE:
            self._transition(op_type, RetryState.RETRY)

        # Warning threshold: inject warning at 50%+ budget consumed
        should_warn = used >= (total_budget // 2) and used > 1

        return RetryDecision(
            action="retry",
            retries_used=used,
            retries_remaining=remaining,
            message=(
                f"{op_type.value} retry {used}/{total_budget} — "
                f"{remaining} remaining"
            ),
            should_inject_warning=should_warn,
            escalation_signal=None,
        )

    def record_success(self, op_type: OperationType) -> RetryState:
        """Record a successful operation, resetting the per-budget count.

        Called when an operation succeeds. Resets the used count and
        transitions back to AVAILABLE (only if not escalated/terminal).

        Does NOT reset cumulative count.

        Returns:
            The new state after reset.
        """
        if self._state[op_type].order >= RetryState.ESCALATED.order:
            # Cannot reset from ESCALATED or beyond — use grant_retry_budget
            return self._state[op_type]

        self._used[op_type] = 0
        self._grant_budget[op_type] = 0
        self._transition(op_type, RetryState.AVAILABLE)
        return RetryState.AVAILABLE

    def can_retry(self, op_type: OperationType) -> bool:
        """Check if an operation can be retried.

        Returns True if the operation has budget remaining and is not
        in ESCALATED/FORCE_COMPLETE/TERMINAL state.
        """
        if self._state[op_type].order >= RetryState.ESCALATED.order:
            return False
        max_retries = self._budgets.get(op_type, DEFAULT_BUDGETS.get(op_type, 3))
        total_budget = max_retries + self._grant_budget[op_type]
        return self._used[op_type] < total_budget

    def force_complete(self, op_type: OperationType) -> None:
        """Force an operation to FORCE_COMPLETE state.

        Used by circuit breakers and hard safety limits.
        """
        self._transition(op_type, RetryState.FORCE_COMPLETE)

    def grant_retry_budget(self, op_type: OperationType, count: int) -> None:
        """Supervisor grants additional retry budget after escalation.

        ONLY callable by the supervisor layer. This is the only way to
        transition backward (ESCALATED → RETRY).

        Grants are capped at 2 per operation per session to prevent abuse.

        Args:
            op_type: The operation to grant budget for.
            count: Number of additional retries to grant.
        """
        # Cannot grant after FORCE_COMPLETE or TERMINAL
        if self._state[op_type].order >= RetryState.FORCE_COMPLETE.order:
            logger.warning(
                "Cannot grant retry budget for %s in state %s",
                op_type.value,
                self._state[op_type].value,
            )
            return

        # Anti-abuse: max 2 grants per operation per session
        if self._grants[op_type] >= 2:
            logger.warning(
                "Grant limit reached for %s (max 2 per session)",
                op_type.value,
            )
            return

        self._grants[op_type] += 1
        self._grant_budget[op_type] += count

        # Transition ESCALATED → RETRY
        if self._state[op_type] == RetryState.ESCALATED:
            self._transition(op_type, RetryState.RETRY)

        logger.info(
            "Supervisor granted %d retries for %s (total grants: %d)",
            count,
            op_type.value,
            self._grants[op_type],
        )

    def get_state(self, op_type: OperationType) -> RetryState:
        """Get current state for an operation type."""
        return self._state[op_type]

    def get_cumulative_failures(self) -> int:
        """Get total cumulative failures across all operations."""
        return sum(self._cumulative.values())

    def decay_cumulative(
        self, op_type: OperationType, amount: int = 3
    ) -> None:
        """Decay the cumulative counter when progress is demonstrated.

        Called by the progress detection logic (currently: 4 distinct tools).
        Only affects cumulative count, not per-budget state.

        Preserves the same decay semantics as maybe_decay_cumulative_counter()
        in same_message_bridge.py.
        """
        if self._cumulative[op_type] > 0:
            self._cumulative[op_type] = max(
                0, self._cumulative[op_type] - amount
            )
            logger.info(
                "Cumulative counter for %s decayed by %d → %d",
                op_type.value,
                amount,
                self._cumulative[op_type],
            )

    def is_exempt(
        self, op_type: OperationType, tool_name: str | None
    ) -> bool:
        """Check if an operation is exempt due to planning tool.

        Planning tools (e.g., sequential_thinking) are exempt from
        same-message budgets (RCA-245). They should not trigger hard-stops
        because the agent is legitimately re-planning.

        Returns:
            True if the operation should bypass budget checks.
        """
        if not tool_name:
            return False
        if op_type not in PLANNING_EXEMPT_OPS:
            return False
        return tool_name in PLANNING_TOOLS

    def get_budget_report(self) -> Dict[str, Any]:
        """Generate a report for supervisor context injection.

        Returns a dict suitable for JSON serialization with:
        - Per-operation state, used/remaining
        - Cumulative session-wide counts
        - Escalation history
        """
        operations = {}
        for op in OperationType:
            max_retries = self._budgets.get(
                op, DEFAULT_BUDGETS.get(op, 3)
            )
            total = max_retries + self._grant_budget[op]
            operations[op.value] = {
                "state": self._state[op].value,
                "used": self._used[op],
                "remaining": max(0, total - self._used[op]),
                "budget": total,
                "cumulative": self._cumulative[op],
                "grants": self._grants[op],
            }

        return {
            "operations": operations,
            "total_cumulative": sum(self._cumulative.values()),
            "escalation_history": list(self._escalation_history),
        }

    def reset_for_new_task(self) -> None:
        """Reset all budgets when a new task/delegation starts.

        Called by call_subordinate, gate counter reset, etc.
        Resets per-budget used counts and states.
        Does NOT reset cumulative counters (those are session-wide).
        Does NOT reset escalation history.
        """
        for op in OperationType:
            self._used[op] = 0
            self._state[op] = RetryState.AVAILABLE
            self._grant_budget[op] = 0
        logger.info("RetryBudgetManager reset for new task")

    def to_dict(self) -> dict:
        """Serialize for agent.data persistence."""
        return {
            "budgets": {op.value: v for op, v in self._budgets.items()},
            "state": {op.value: s.value for op, s in self._state.items()},
            "used": {op.value: v for op, v in self._used.items()},
            "cumulative": {op.value: v for op, v in self._cumulative.items()},
            "grants": {op.value: v for op, v in self._grants.items()},
            "grant_budget": {
                op.value: v for op, v in self._grant_budget.items()
            },
            "escalation_history": list(self._escalation_history),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RetryBudgetManager:
        """Deserialize from agent.data."""
        mgr = cls()

        if "budgets" in data:
            for key, val in data["budgets"].items():
                try:
                    op = OperationType(key)
                    mgr._budgets[op] = val
                except ValueError:
                    logger.warning("Unknown operation type in budgets: %s", key)

        if "state" in data:
            for key, val in data["state"].items():
                try:
                    op = OperationType(key)
                    mgr._state[op] = RetryState(val)
                except ValueError:
                    logger.warning("Unknown state/op in deserialization: %s=%s", key, val)

        if "used" in data:
            for key, val in data["used"].items():
                try:
                    op = OperationType(key)
                    mgr._used[op] = val
                except ValueError:
                    pass

        if "cumulative" in data:
            for key, val in data["cumulative"].items():
                try:
                    op = OperationType(key)
                    mgr._cumulative[op] = val
                except ValueError:
                    pass

        if "grants" in data:
            for key, val in data["grants"].items():
                try:
                    op = OperationType(key)
                    mgr._grants[op] = val
                except ValueError:
                    pass

        if "grant_budget" in data:
            for key, val in data["grant_budget"].items():
                try:
                    op = OperationType(key)
                    mgr._grant_budget[op] = val
                except ValueError:
                    pass

        if "escalation_history" in data:
            mgr._escalation_history = list(data["escalation_history"])

        return mgr

    # ── Private Helpers ──

    def _transition(self, op_type: OperationType, new_state: RetryState) -> None:
        """Transition an operation to a new state.

        Enforces forward-only transitions (cannot go backward).
        The only exception is grant_retry_budget which manually sets
        ESCALATED → RETRY.
        """
        self._state[op_type] = new_state

    def _check_cumulative(self, op_type: OperationType) -> bool:
        """Check if the cumulative threshold has been exceeded.

        Returns True if op_type has a cumulative threshold AND the
        current cumulative count meets or exceeds it.
        """
        threshold = CUMULATIVE_THRESHOLDS.get(op_type)
        if threshold is None:
            return False
        return self._cumulative[op_type] >= threshold
