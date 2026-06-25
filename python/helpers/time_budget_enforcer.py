"""
Time Budget Enforcer — Hard wall-clock enforcement for delegation paths.

FIX-010 (GAP-2, F-2, IL-3): Prevents runs from exceeding their time budget
by BLOCKING new delegations once the budget is exhausted.

Key design decisions:
- Does NOT kill running agents — only blocks NEW delegations
- Uses wall-clock time (time.monotonic) for reliability
- Fires at 120% of budget (OVERTIME_FACTOR) to allow some grace
- Configurable budget via set_run_budget() or defaults

Architecture ref: §13.2 of gates-escapehatches-loops-architecture.md
"""

import logging
import time
from typing import Tuple

logger = logging.getLogger("agix.time_budget")

# ── Defaults ─────────────────────────────────────────────────────────────
# Default maximum run duration in seconds (90 minutes).
MAX_RUN_DURATION_SECONDS = 90 * 60

# Allow 100% overtime before hard cut (FIX-017).
# At 200% of budget, block new delegations. ABSOLUTE LAST RESORT.
# The intelligent supervisor + delegation cycle detector should catch
# stuck patterns well before this fires. Per user directive: 200%.
OVERTIME_FACTOR = 2.0

# agent.data key for the run start timestamp.
RUN_START_KEY = "_run_start_time"

# agent.data key for custom budget override (seconds).
RUN_BUDGET_KEY = "_run_budget_seconds"


def set_run_start(agent_data: dict) -> None:
    """Record the run start time if not already set.

    Call this once at the beginning of an orchestrator run.
    Uses time.monotonic() for reliable elapsed-time measurement
    (immune to wall-clock adjustments).

    Args:
        agent_data: The agent's data dict (agent.data).
    """
    if RUN_START_KEY not in agent_data:
        agent_data[RUN_START_KEY] = time.monotonic()
        logger.info("TimeBudgetEnforcer: Run start recorded")


def set_run_budget(agent_data: dict, budget_seconds: int) -> None:
    """Override the default time budget for this run.

    Args:
        agent_data: The agent's data dict (agent.data).
        budget_seconds: The time budget in seconds. Must be positive.
    """
    if budget_seconds <= 0:
        raise ValueError(f"Budget must be positive, got {budget_seconds}")
    agent_data[RUN_BUDGET_KEY] = budget_seconds
    logger.info(f"TimeBudgetEnforcer: Custom budget set to {budget_seconds}s")


def get_elapsed_seconds(agent_data: dict) -> float:
    """Get seconds elapsed since run start.

    Returns 0.0 if no start time has been recorded.

    Args:
        agent_data: The agent's data dict (agent.data).

    Returns:
        Elapsed seconds since run start, or 0.0 if not started.
    """
    start = agent_data.get(RUN_START_KEY)
    if start is None:
        return 0.0
    return time.monotonic() - start


def get_budget_seconds(agent_data: dict) -> int:
    """Get the effective time budget in seconds.

    Returns the custom budget if set, otherwise the default.

    Args:
        agent_data: The agent's data dict (agent.data).

    Returns:
        Budget in seconds.
    """
    return agent_data.get(RUN_BUDGET_KEY, MAX_RUN_DURATION_SECONDS)


def check_time_budget(agent_data: dict) -> Tuple[bool, str]:
    """Check if the run has exceeded its time budget.

    Fires at OVERTIME_FACTOR × budget (default 120%).
    Does NOT kill running agents — only provides a signal to block
    new delegations.

    Args:
        agent_data: The agent's data dict (agent.data).

    Returns:
        Tuple of (exceeded: bool, message: str).
        - exceeded=False, message="" if within budget or no start time.
        - exceeded=True, message=<warning> if budget exceeded.
    """
    start = agent_data.get(RUN_START_KEY)
    if start is None:
        return False, ""

    elapsed = time.monotonic() - start
    budget = get_budget_seconds(agent_data)
    max_allowed = budget * OVERTIME_FACTOR

    if elapsed >= max_allowed:
        elapsed_min = int(elapsed / 60)
        budget_min = int(budget / 60)
        msg = (
            f"🛑 TIME BUDGET EXCEEDED: {elapsed_min} minutes elapsed "
            f"(budget: {budget_min} min, hard limit: {int(max_allowed/60)} min). "
            f"Blocking further delegations. Deliver NOW with best-effort results."
        )
        logger.warning(f"TimeBudgetEnforcer: {msg}")
        return True, msg

    return False, ""


def get_budget_utilization(agent_data: dict) -> float:
    """Get the budget utilization as a fraction (0.0 to 1.0+).

    Useful for advisory messages ("you've used 80% of your time budget").

    Args:
        agent_data: The agent's data dict (agent.data).

    Returns:
        Fraction of budget consumed. 0.0 if no start time.
        Values > 1.0 indicate overtime.
    """
    start = agent_data.get(RUN_START_KEY)
    if start is None:
        return 0.0
    elapsed = time.monotonic() - start
    budget = get_budget_seconds(agent_data)
    if budget <= 0:
        return 0.0
    return elapsed / budget
