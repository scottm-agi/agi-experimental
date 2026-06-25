"""
TimeBudgetInjector — Prevents time-budget exhaustion in long-running agents.
============================================================================

Tracks elapsed time since agent init and injects warnings at configurable
thresholds to redirect the agent toward unfinished work instead of
continuing verification loops.

Root cause (ADR-019, Iteration 151 RCA-4):
    Orchestrator spent 55% of runtime (50 of 90 minutes) on verification
    loops, leaving 0% for missing features. No mechanism existed to
    communicate "you've spent 60% of time with 47% features missing."

Usage:
    injector = TimeBudgetInjector(budget_minutes=90)

    # Check periodically (e.g., every N tool calls):
    warning = injector.check()
    if warning:
        await agent.hist_add_warning(warning)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger("agix.time_budget_injector")


class TimeBudgetInjector:
    """Tracks elapsed time and injects warnings at configurable thresholds."""

    def __init__(
        self,
        budget_minutes: int = 90,
        warn_pct: float = 0.6,
        urgent_pct: float = 0.8,
        cooldown_seconds: int = 300,
    ):
        """
        Args:
            budget_minutes: Total time budget in minutes.
            warn_pct: Fraction elapsed before first warning (0.0-1.0).
            urgent_pct: Fraction elapsed before urgent warning (0.0-1.0).
            cooldown_seconds: Minimum seconds between warnings to prevent spam.
        """
        self.budget_minutes = budget_minutes
        self.warn_pct = warn_pct
        self.urgent_pct = urgent_pct
        self.cooldown_seconds = cooldown_seconds
        self._start_time = time.time()
        self._last_warn_time: Optional[float] = None

    def elapsed_minutes(self) -> float:
        """Minutes elapsed since start."""
        return (time.time() - self._start_time) / 60.0

    def remaining_minutes(self) -> float:
        """Minutes remaining in budget."""
        return max(0.0, self.budget_minutes - self.elapsed_minutes())

    def elapsed_pct(self) -> float:
        """Fraction of budget elapsed (0.0-1.0+)."""
        if self.budget_minutes <= 0:
            return float("inf")
        return self.elapsed_minutes() / self.budget_minutes

    def check(self) -> Optional[str]:
        """Check elapsed time and return warning if threshold exceeded.

        Returns:
            Warning string if threshold exceeded and not in cooldown,
            None otherwise.
        """
        pct = self.elapsed_pct()

        # Below warning threshold
        if pct < self.warn_pct:
            return None

        # Cooldown check
        if self._last_warn_time is not None:
            since_last = time.time() - self._last_warn_time
            if since_last < self.cooldown_seconds:
                return None

        self._last_warn_time = time.time()
        elapsed = round(self.elapsed_minutes())
        remaining = round(self.remaining_minutes())

        if pct >= self.urgent_pct:
            logger.warning(
                f"TimeBudgetInjector: URGENT — {elapsed}min elapsed, "
                f"{remaining}min remaining ({pct:.0%} of budget)"
            )
            return self._urgent_warning(elapsed, remaining, pct)
        else:
            logger.info(
                f"TimeBudgetInjector: WARNING — {elapsed}min elapsed, "
                f"{remaining}min remaining ({pct:.0%} of budget)"
            )
            return self._standard_warning(elapsed, remaining, pct)

    def _standard_warning(self, elapsed: int, remaining: int, pct: float) -> str:
        return (
            f"## ⏰ TIME CHECK ({elapsed} minutes elapsed, {remaining} remaining)\n"
            f"\n"
            f"You have used {pct:.0%} of your time budget ({elapsed}/{self.budget_minutes} min).\n"
            f"\n"
            f"If core features are still unimplemented, **prioritize feature "
            f"completion over verification of existing features**.\n"
            f"\n"
            f"Consider:\n"
            f"- Are there missing features that haven't been built yet?\n"
            f"- Is the current verification loop making meaningful progress?\n"
            f"- Would it be better to move to the next phase?\n"
        )

    def _urgent_warning(self, elapsed: int, remaining: int, pct: float) -> str:
        return (
            f"## 🚨 TIME CRITICAL ({elapsed} minutes elapsed, {remaining} remaining)\n"
            f"\n"
            f"You have used {pct:.0%} of your time budget. Only {remaining} minutes remain.\n"
            f"\n"
            f"You MUST prioritize remaining unimplemented features NOW. "
            f"**Stop all verification, debugging, and polish work.** Focus "
            f"exclusively on building features that don't exist yet.\n"
        )
