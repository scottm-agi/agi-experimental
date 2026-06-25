"""
Budget Reserve — Proactive budget exhaustion prevention for subordinate agents.

Root Cause (Iteration 208b — MainStreet subordinate budget exhaustion):
    Subordinate agents spend 60-80% of their turn budget on infrastructure
    (npm install, scaffolding, FileGuard recovery, .npmrc writes) without
    ever writing application source code. When the budget expires, the
    parent receives a `None` or `[ITERATION_LIMIT]` result with no usable
    deliverables.

Fix:
    This module provides pure-logic functions for:
    1. Budget utilization tracking (what % of turns have been consumed)
    2. Application code detection (has the agent written ANY actual source
       files, as opposed to infrastructure files?)
    3. Advisory message generation (escalating warnings when budget is being
       wasted on infrastructure)

Usage:
    from python.helpers.budget_reserve import (
        get_budget_utilization,
        is_application_code_path,
        has_written_application_code,
        build_budget_advisory,
    )

Architecture:
    - Stateless functions only — no agent dependency in the core logic
    - The extension (_37_budget_reserve_advisor.py) handles the agent
      integration, history injection, and rate-limiting
"""
from __future__ import annotations

import re
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Budget Utilization
# ═══════════════════════════════════════════════════════════════════════════

def get_budget_utilization(current_turn: int, max_turns: int) -> float:
    """Return the fraction of budget consumed (0.0 to 1.0).

    Args:
        current_turn: How many turns the agent has used so far.
        max_turns: The agent's maximum allowed turns.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 if max_turns <= 0 to avoid
        division by zero.
    """
    if max_turns <= 0:
        return 0.0
    return min(1.0, current_turn / max_turns)


# ═══════════════════════════════════════════════════════════════════════════
# Application Code Detection
# ═══════════════════════════════════════════════════════════════════════════

# Directories that contain application source code
_APP_CODE_DIRS = re.compile(
    r'(?:^|/)'  # Start of string or after /
    r'(?:src|app|pages|components|lib|utils|hooks|services|features|'
    r'layouts|styles|public|views|templates|routes|modules|stores|'
    r'contexts|providers|middleware|api)'
    r'(?:/|$)',  # Must be followed by / or end of string
    re.IGNORECASE,
)

# Infrastructure files that are NOT application code
_INFRA_PATTERNS = re.compile(
    r'(?:^|/)'
    r'(?:\.npmrc|\.mise\.toml|\.env(?:\.\w+)?|package\.json|package-lock\.json|'
    r'tsconfig\.json|next\.config\.\w+|vite\.config\.\w+|tailwind\.config\.\w+|'
    r'postcss\.config\.\w+|jest\.config\.\w+|vitest\.config\.\w+|'
    r'node_modules|\.next|dist|build|\.git|\.gitignore|'
    r'Dockerfile|docker-compose\.\w+|\.dockerignore|'
    r'README\.md|LICENSE|CHANGELOG\.md)$',
    re.IGNORECASE,
)


def is_application_code_path(path: str) -> bool:
    """Classify whether a file path is application source code.

    Application code lives in directories like src/, app/, pages/,
    components/, lib/, styles/, public/, etc.

    Infrastructure files are .npmrc, .mise.toml, package.json,
    node_modules/*, config files, etc.

    Args:
        path: File path (relative or absolute).

    Returns:
        True if the path is application source code.
    """
    # Normalize separators
    path = path.replace("\\", "/")

    # Reject infrastructure files first (more specific)
    if _INFRA_PATTERNS.search(path):
        return False

    # Check if it's in an application code directory
    if _APP_CODE_DIRS.search(path):
        return True

    # Reject root-level config files
    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    if basename.startswith(".") or basename in (
        "package.json", "package-lock.json", "tsconfig.json",
    ):
        return False

    return False


def has_written_application_code(file_paths: list[str]) -> bool:
    """Check if any of the given file paths are application source code.

    Args:
        file_paths: List of file paths that the agent has written to.

    Returns:
        True if at least one path is application source code.
    """
    return any(is_application_code_path(p) for p in file_paths)


# ═══════════════════════════════════════════════════════════════════════════
# Advisory Message Builder
# ═══════════════════════════════════════════════════════════════════════════

# Thresholds
_WARNING_THRESHOLD = 0.6    # 60% budget consumed
_CRITICAL_THRESHOLD = 0.8   # 80% budget consumed


def build_budget_advisory(
    utilization: float,
    has_app_code: bool,
    turns_remaining: int,
) -> Optional[str]:
    """Generate a budget advisory message based on utilization and code status.

    Rules:
    - Below 60% utilization → No advisory (agent has plenty of budget)
    - 60-79% WITHOUT app code → WARNING: Prioritize writing code
    - 60-79% WITH app code → No advisory (agent is making progress)
    - 80%+ WITHOUT app code → CRITICAL: Write code NOW or return
    - 80%+ WITH app code → WARNING: Finish up and call response

    Args:
        utilization: Budget utilization ratio (0.0 to 1.0).
        has_app_code: Whether the agent has written any application code.
        turns_remaining: How many turns the agent has left.

    Returns:
        Advisory message string, or None if no advisory needed.
    """
    if utilization < _WARNING_THRESHOLD:
        return None

    if utilization >= _CRITICAL_THRESHOLD:
        if not has_app_code:
            return (
                f"## 🔴 CRITICAL: Budget Reserve Exhausted — {turns_remaining} turns remaining\n\n"
                f"You have consumed {utilization:.0%} of your turn budget **without writing any "
                f"application source code**. You have only **{turns_remaining} turns left**.\n\n"
                f"### MANDATORY ACTION:\n"
                f"1. **STOP** all infrastructure work (npm install, config files, scaffolding)\n"
                f"2. **WRITE APPLICATION CODE NOW** — create the actual source files "
                f"(pages, components, API routes, styles)\n"
                f"3. If you cannot complete the task in {turns_remaining} turns, call the "
                f"`response` tool immediately with a status report of what you accomplished "
                f"and what remains, so the orchestrator can re-delegate\n\n"
                f"**DO NOT** spend another turn on infrastructure. Write code or return."
            )
        else:
            return (
                f"## ⚠️ Budget Reserve Low — {turns_remaining} turns remaining\n\n"
                f"You have consumed {utilization:.0%} of your turn budget. You have written "
                f"some application code, but only **{turns_remaining} turns remain**.\n\n"
                f"### ACTION:\n"
                f"1. **Finish** your current task as quickly as possible\n"
                f"2. If you cannot complete in {turns_remaining} turns, call the `response` "
                f"tool with your progress so the orchestrator can continue\n"
                f"3. Do NOT start new infrastructure tasks — focus on code delivery"
            )

    # 60-79% utilization
    if not has_app_code:
        return (
            f"## ⚠️ PRIORITIZE APPLICATION CODE — {turns_remaining} turns remaining\n\n"
            f"You have consumed {utilization:.0%} of your turn budget but have **not yet "
            f"written any application source code** (files in src/, app/, pages/, "
            f"components/, lib/, styles/, etc.).\n\n"
            f"### GUIDANCE:\n"
            f"1. Complete any critical infrastructure (npm install) in the next 1-2 turns\n"
            f"2. Then **immediately** start writing application code — this is your "
            f"primary deliverable\n"
            f"3. Prioritize the highest-impact files first (main page, key components)\n"
            f"4. If you run out of budget, call `response` with your progress report"
        )

    # 60-79% with app code → no advisory
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Critical Budget Enforcement — Forced Response (RCA-316c Component 2)
# ═══════════════════════════════════════════════════════════════════════════

# At this utilization level, the agent MUST call the response tool.
# This is NOT advisory — it's a system-level instruction injected as a
# system message that the LLM cannot ignore.
CRITICAL_UTILIZATION_THRESHOLD = 0.95


def build_critical_response_directive(
    utilization: float,
    turns_remaining: int,
    max_turns: int,
) -> Optional[str]:
    """Build a MANDATORY response directive at critical budget utilization.

    Unlike ``build_budget_advisory`` (which is advisory), this directive
    is injected as a system-level message that forces the agent to call
    the ``response`` tool on its next turn. This prevents "budget death"
    where the agent hits the iteration limit and returns a raw string
    that the orchestrator misinterprets as a failure, triggering a
    cascading re-delegation loop.

    RCA-316c: Without this enforcement, agents burn 100% of their budget
    without ever calling ``response``. The parent receives
    ``[ITERATION_LIMIT] ...`` which it classifies as an error and
    re-delegates — but the re-delegation will also hit the limit.
    This creates 5-19 cascading re-delegations of the same task.

    Args:
        utilization: Budget utilization ratio (0.0 to 1.0).
        turns_remaining: How many turns the agent has left.
        max_turns: The agent's maximum allowed turns.

    Returns:
        MANDATORY directive string if utilization >= 95%, else None.
    """
    if utilization < CRITICAL_UTILIZATION_THRESHOLD:
        return None

    return (
        f"## 🛑 MANDATORY: CALL `response` TOOL NOW — {turns_remaining} turns remaining\n\n"
        f"**YOU HAVE CONSUMED {utilization:.0%} OF YOUR TURN BUDGET ({max_turns} total turns).**\n\n"
        f"### MANDATORY — YOU MUST DO THIS ON YOUR VERY NEXT ACTION:\n\n"
        f"Call the `response` tool with a structured status report containing:\n\n"
        f"1. **What you completed** — list all files created/modified with brief descriptions\n"
        f"2. **What remains** — list any incomplete tasks\n"
        f"3. **Blockers** — any issues that prevented progress\n\n"
        f"**DO NOT** attempt any more tool calls. **DO NOT** write more code.\n"
        f"**DO NOT** run any commands. Your ONLY action must be calling `response`.\n\n"
        f"If you do NOT call `response`, you will hit the iteration limit and your\n"
        f"work will be reported as a failure. Calling `response` ensures your progress\n"
        f"is properly delivered to the orchestrator.\n\n"
        f"This is a system-level instruction. It cannot be overridden."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Smart Dynamic Budget — Layer 1: LLM-Driven Budget Planning
# (RCA-362 — Orchestrator budget exhaustion during planning)
# ═══════════════════════════════════════════════════════════════════════════

# Budget formula constants
_TURNS_PER_DELEGATION = 7   # prep + delegate + wait + gate-check + retry + verify + track
_VERIFICATION_OVERHEAD = 20  # Fixed overhead for verification, reporting, cleanup


def calculate_llm_budget(
    estimated_delegations: int,
    total_phases: int = 0,
    total_requirements: int = 0,
) -> int:
    """Calculate a recommended iteration budget for the orchestrator.

    Called by the orchestrator AFTER decomposition, when it knows scope.
    The formula is: delegations * 7 + 20 overhead.

    Each delegation needs ~7 turns:
        1. Preparation (build delegation brief)
        2. Delegate (call delegate tool)
        3. Wait (poll for subordinate completion)
        4. Gate-check (run completion gate)
        5. Possible retry (if gate fails)
        6. Verify (check deliverables)
        7. Track (update phase/requirement status)

    Args:
        estimated_delegations: How many delegate calls the orchestrator expects.
        total_phases: Number of decomposition phases (informational).
        total_requirements: Number of tracked requirements (informational).

    Returns:
        Recommended iteration budget (int). Always >= _VERIFICATION_OVERHEAD.
    """
    delegations = max(0, int(estimated_delegations))
    return delegations * _TURNS_PER_DELEGATION + _VERIFICATION_OVERHEAD


# ═══════════════════════════════════════════════════════════════════════════
# Smart Dynamic Budget — Layer 2: Phase-Aware Budget Advisory
# (RCA-362 — Proactive phase-aware budget guidance)
# ═══════════════════════════════════════════════════════════════════════════

# Phase prefixes that indicate implementation work
_IMPLEMENTATION_PHASE_PREFIXES = ("3",)  # Phase 3.x = implementation


def build_phase_aware_budget_advisory(
    utilization: float,
    phases_completed: list[str],
    phases_pending: list[str],
    turns_remaining: int,
) -> str | None:
    """Generate a phase-aware budget advisory for the orchestrator.

    This fires when the orchestrator has consumed >60% of its budget
    AND implementation phases (Phase 3.x) have NOT yet started. It tells
    the orchestrator to skip optional planning and start delegating code.

    Args:
        utilization: Budget utilization ratio (0.0 to 1.0).
        phases_completed: List of phase sequence strings already completed.
        phases_pending: List of phase sequence strings still pending.
        turns_remaining: How many turns the orchestrator has left.

    Returns:
        Advisory string if conditions met, else None.
    """
    if utilization < _WARNING_THRESHOLD:
        return None

    # Check if any implementation phases (3.x) are still pending
    impl_phases_pending = [
        p for p in phases_pending
        if any(p.startswith(prefix) for prefix in _IMPLEMENTATION_PHASE_PREFIXES)
    ]

    if not impl_phases_pending:
        # Implementation is done or not in scope — no phase advisory needed
        return None

    # Implementation phases are pending AND budget is >60% consumed
    impl_list = ", ".join(impl_phases_pending[:5])
    if len(impl_phases_pending) > 5:
        impl_list += f" (+{len(impl_phases_pending) - 5} more)"

    return (
        f"## 🟠 BUDGET ADVISORY: Phase 3 Implementation Has Not Started — "
        f"{turns_remaining} turns remaining\n\n"
        f"You have consumed **{utilization:.0%}** of your iteration budget, but "
        f"**implementation phases have not started**: {impl_list}\n\n"
        f"### MANDATORY ACTION:\n"
        f"1. **SKIP** any remaining optional planning phases (design refinement, "
        f"additional BDD scenarios, etc.)\n"
        f"2. **START DELEGATING implementation phases NOW** — Phase 3 tasks are "
        f"the highest priority\n"
        f"3. Each delegation consumes ~7 turns — plan your remaining budget carefully\n"
        f"4. You have **{turns_remaining} turns** left for {len(impl_phases_pending)} "
        f"implementation phases\n\n"
        f"**Budget math**: {len(impl_phases_pending)} phases × 7 turns/phase = "
        f"{len(impl_phases_pending) * 7} turns needed. "
        f"You have {turns_remaining} remaining.\n\n"
        f"Spending more turns on planning when implementation hasn't started is a "
        f"budget exhaustion anti-pattern."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Smart Dynamic Budget — Budget Status Exposure
# (Exposes iteration/phase status to orchestrator via tool responses)
# ═══════════════════════════════════════════════════════════════════════════

def build_budget_status(
    iterations_used: int,
    iterations_total: int,
    phases_pending: list[str] | None = None,
) -> dict:
    """Build a budget status dict for inclusion in tool responses.

    This status dict is injected into requirements tool responses so the
    orchestrator always has visibility into its budget consumption and
    remaining work.

    Args:
        iterations_used: How many iterations have been consumed.
        iterations_total: Total iteration budget.
        phases_pending: List of pending phase sequences (optional).

    Returns:
        Dict with keys: used, total, remaining, phases_pending.
    """
    remaining = max(0, iterations_total - iterations_used)
    return {
        "used": iterations_used,
        "total": iterations_total,
        "remaining": remaining,
        "phases_pending": phases_pending or [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Budget-Aware Delegation Cap (F-4 — ITR-49)
# ═══════════════════════════════════════════════════════════════════════════

# Historical average: each requirement takes ~3.75 minutes (225 seconds)
# to complete within a subordinate agent (build + test + verify cycle).
_SECONDS_PER_REQUIREMENT = 225

# Maximum wave size — even with unlimited time, waves shouldn't exceed
# the static cap because LLM context degrades with too many requirements.
_MAX_WAVE_SIZE_CAP = None  # Loaded lazily from gate_config


def _get_max_wave_size() -> int:
    """Lazy-load MAX_REQUIREMENTS_PER_DELEGATION from gate_config."""
    global _MAX_WAVE_SIZE_CAP
    if _MAX_WAVE_SIZE_CAP is None:
        try:
            from python.helpers.gate_config import MAX_REQUIREMENTS_PER_DELEGATION
            _MAX_WAVE_SIZE_CAP = MAX_REQUIREMENTS_PER_DELEGATION
        except ImportError:
            _MAX_WAVE_SIZE_CAP = 8  # Fallback
    return _MAX_WAVE_SIZE_CAP


def calculate_delegation_budget(
    timeout_seconds: int | float,
    num_requirements: int,
) -> dict:
    """Calculate a budget-aware delegation cap based on timeout and requirement count.

    Uses historical data (225s/requirement average) to determine how many
    requirements a subordinate can reasonably complete within its timeout.
    Returns wave recommendations for splitting oversized delegations.

    F-4 (ITR-49): Replaces static advisory-only warning with budget-aware
    hard guidance. A delegation with 13 requirements on a 1800s timeout
    should be split into waves.

    Args:
        timeout_seconds: The subordinate's timeout in seconds.
        num_requirements: Number of requirements in this delegation.

    Returns:
        Dict with keys:
            max_requirements (int): Budget-aware max for this timeout.
            recommended_wave_size (int): Optimal batch size per wave.
            num_waves (int): How many waves needed (0 if no requirements).
            timeout_per_requirement (int): Seconds budgeted per requirement.
            over_budget (bool): Whether num_requirements exceeds the budget.
    """
    import math

    max_wave_size = _get_max_wave_size()
    timeout_seconds = max(0, float(timeout_seconds))

    # Budget-aware max: how many requirements fit in the timeout
    if _SECONDS_PER_REQUIREMENT > 0:
        budget_max = int(timeout_seconds // _SECONDS_PER_REQUIREMENT)
    else:
        budget_max = num_requirements

    # Cap the wave size at the static MAX_REQUIREMENTS_PER_DELEGATION
    # LLM context degrades with too many requirements regardless of time.
    effective_max = min(budget_max, max_wave_size)
    effective_max = max(effective_max, 1)  # At least 1

    # Wave calculation
    if num_requirements <= 0:
        return {
            "max_requirements": effective_max,
            "recommended_wave_size": effective_max,
            "num_waves": 0,
            "timeout_per_requirement": _SECONDS_PER_REQUIREMENT,
            "over_budget": False,
        }

    over_budget = num_requirements > effective_max
    num_waves = math.ceil(num_requirements / effective_max) if over_budget else 1
    recommended_wave_size = min(effective_max, max_wave_size)

    return {
        "max_requirements": effective_max,
        "recommended_wave_size": recommended_wave_size,
        "num_waves": num_waves,
        "timeout_per_requirement": _SECONDS_PER_REQUIREMENT,
        "over_budget": over_budget,
    }
