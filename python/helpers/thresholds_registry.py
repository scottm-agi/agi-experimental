"""
Thresholds Registry — P1-5 Systems Audit Fix

Single source of truth for ALL loop, budget, gate, and coordination thresholds.
Replaces 26+ scattered constants across 10+ files.

Systems Audit Finding H-20:
    23+ threshold constants scattered across agent.py, supervisor_redirect_cap.py,
    _12_tool_failure_tracker.py, _06_tool_failure_reset.py, verification_ledger.py,
    _10_structural_guards.py, _45_intelligent_supervisor.py, _27_delegation_loop_hook.py,
    and others. No central registry, no documentation of relationships between thresholds.

Usage:
    from python.helpers.thresholds_registry import Thresholds

    if count >= Thresholds.SUPERVISOR_REDIRECT_CAP:
        suppress_redirect()
"""

from __future__ import annotations


class Thresholds:
    """Centralized threshold constants.

    All loop limits, budget caps, gate counters, and coordination thresholds
    should be defined here. Individual files should import from this registry.

    Naming convention: SUBSYSTEM_METRIC_TYPE
    Example: DELEGATION_LOOP_THRESHOLD, BUDGET_ABSOLUTE_CEILING
    """

    # =========================================================================
    # Agent Core — Budget & Iteration Limits
    # =========================================================================

    # Maximum iterations per agent run (profile-specific overrides exist)
    PROFILE_MAX_DEFAULT = 1000
    SUBORDINATE_MAX = 200
    FAN_OUT_MAX = 100

    # C-6: Absolute ceiling for dynamic budget scaling
    # System may legitimately run 24hrs, but this catches runaway scaling
    ABSOLUTE_BUDGET_CEILING = 5000

    # R-4: Budget multiplier per decomposed task
    BUDGET_PER_TASK = 5

    # Maximum outer restart loops
    MAX_OUTER_RESTARTS = 10

    # =========================================================================
    # Empty Response Handling (C-3)
    # =========================================================================

    # Per-cycle empty retries before condensation attempt
    MAX_EMPTY_RETRIES_PER_CYCLE = 1  # Lowered from 2 (C-3 fix)

    # Total condensation cycles before circuit breaker
    MAX_EMPTY_RESPONSE_CYCLES = 2  # Lowered from 3 (C-3 fix)

    # =========================================================================
    # Pause / Intervention (C-5)
    # =========================================================================

    # Hard timeout for paused state (safety net)
    PAUSE_TIMEOUT = 300  # 5 minutes

    # Time before escalating to supervisor instead of spinning
    PAUSE_SUPERVISOR_ESCALATION = 60  # 1 minute

    # =========================================================================
    # Supervisor & Redirect (C-4)
    # =========================================================================

    # Consecutive redirects before suppression
    SUPERVISOR_REDIRECT_CAP = 2  # Lowered from 3 (C-4 fix)

    # =========================================================================
    # Tool Failure Tracking
    # =========================================================================

    # Same tool retry limit before escalation
    MAX_SAME_TOOL_RETRIES = 2

    # Circuit breaker thresholds
    CIRCUIT_BREAKER_WARN = 5
    CIRCUIT_BREAKER_ESCALATE = 8

    # Consecutive mistake thresholds
    CONSECUTIVE_MISTAKE_THRESHOLD = 3
    CONSECUTIVE_MISTAKE_HARD_LIMIT = 8
    CONSECUTIVE_MISTAKE_FORCE_STOP = 15

    # Hint escalation tiers
    HINT_ESCALATION_REDIRECT = 2
    HINT_ESCALATION_BLOCK = 4

    # Cooldown: turns before unblocking a tool
    COOLDOWN_UNBLOCK_AFTER = 3

    # H-16: After this many advisory hints on NEVER_BLOCK tools, escalate
    NEVER_BLOCK_ADVISORY_CAP = 8

    # =========================================================================
    # Verification & Gate Lifecycle
    # =========================================================================

    # Per-check retry limit before marking unfixable
    MAX_ATTEMPTS_PER_CHECK = 3

    # Global block budget across all gates
    GLOBAL_BLOCK_BUDGET = 10

    # =========================================================================
    # Delegation Loop Detection
    # =========================================================================

    # Identical delegation threshold (soft warning)
    DELEGATION_LOOP_THRESHOLD = 3

    # Hard block after N identical delegations
    DELEGATION_LOOP_HARD_LIMIT = 5

    # =========================================================================
    # Structural Guards
    # =========================================================================

    # L1/L2 cooldown (turns between guard interventions)
    L1_COOLDOWN_TURNS = 5
    L2_COOLDOWN_TURNS = 5

    # Tool repetition threshold
    TOOL_REPETITION_THRESHOLD = 8

    # =========================================================================
    # Hint Coordination (P1-4)
    # =========================================================================

    # Maximum non-critical hints per agent per turn
    HINT_MAX_PER_TURN = 3
