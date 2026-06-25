"""
Time Budget Injector — message_loop_prompts_after extension (order 77).

Injects time-budget warnings into agent context when configurable time
thresholds are exceeded. Prevents verification death spirals by redirecting
agents toward unfinished features.

Root cause (ADR-019, Iteration 151):
    Orchestrator spent 55% of runtime on verification loops, leaving 0%
    for missing features. No time awareness mechanism existed.

Position 77 places it after error awareness (76) and before depth
instructions (80), so time warnings appear alongside error context.
"""

from __future__ import annotations

import logging
from typing import Optional

from python.helpers.extension import Extension
from python.agent import LoopData
from python.helpers.time_budget_injector import TimeBudgetInjector

logger = logging.getLogger("agix.time_budget_hook")

# Per-context injectors — created lazily on first use
_injectors: dict[str, TimeBudgetInjector] = {}


def _get_injector(context_id: str) -> TimeBudgetInjector:
    """Get or create a TimeBudgetInjector for a context."""
    if context_id not in _injectors:
        _injectors[context_id] = TimeBudgetInjector(
            budget_minutes=90,  # matches smoke test timeout
            warn_pct=0.6,
            urgent_pct=0.8,
            cooldown_seconds=300,  # 5 min between warnings
        )
    return _injectors[context_id]


class TimeBudgetHook(Extension):
    """Inject time-budget warnings into agent loop context."""

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        context_id = self.agent.context.id if self.agent.context else None
        if not context_id:
            return

        injector = _get_injector(context_id)
        warning = injector.check()

        if warning:
            loop_data.extras_temporary["time_budget"] = warning
            logger.warning(
                f"[TIME BUDGET HOOK] {self.agent.agent_name}: "
                f"Injected time warning at {injector.elapsed_pct():.0%} elapsed"
            )
