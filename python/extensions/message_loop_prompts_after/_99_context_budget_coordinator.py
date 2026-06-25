"""
Context Budget Coordinator — message_loop_prompts_after extension (order 99).

Runs LAST in the prompts_after phase to enforce a total character budget on
extras_temporary. When the aggregate size of all injected extras exceeds the
budget, lower-priority items are truncated to fit.

5-Why RCA (F-1, MSR_Smoke_1777847233): Multiple extensions inject into
extras_temporary without coordination. When ErrorLedger + ToolFailureTracker +
TimeBudget + RecallMemories all fire simultaneously, the total injection exceeds
the model's context window, causing empty model completions. The model returns
"" because the system prompt + extras alone consume the entire budget.

Root cause: No aggregate cap on extras_temporary injection size.
Fix: This coordinator runs at position 99 (after all other injectors) and
trims lowest-priority items to stay within a configurable character budget.

Priority order (highest → lowest, configurable):
  1. recall_memories — core agent memory (never truncated)
  2. depth_instructions — delegation context
  3. project_extras — user project state
  4. error_awareness — error ledger injection
  5. time_budget — time remaining info
  6. All other keys — truncated first
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from python.helpers.extension import Extension

if TYPE_CHECKING:
    from python.agent import LoopData

logger = logging.getLogger("agix.extensions.context_budget_coordinator")

# Default budget: 12,000 chars (~3,000 tokens). This leaves room for the
# system prompt (~4K tokens), user message, and tool results.
DEFAULT_BUDGET_CHARS = 12000

# Priority order: items earlier in this list are preserved first.
# Items NOT in this list are treated as lowest priority (truncated first).
PRIORITY_ORDER = [
    "recall_memories",       # Core agent memory — highest priority
    "depth_instructions",    # Delegation context — structural
    "project_extras",        # User's project state
    "error_awareness",       # Error ledger — important but verbose
    "time_budget",           # Time remaining — useful but small
]

# Maximum chars per individual extra (even high-priority items are bounded)
MAX_SINGLE_EXTRA_CHARS = 4000


def _apply_budget(extras: dict, budget: int = DEFAULT_BUDGET_CHARS) -> dict:
    """Apply character budget to extras_temporary dict.

    Preserves high-priority items first, truncating low-priority items
    until total character count is within budget.

    Args:
        extras: The extras_temporary dict from LoopData.
        budget: Maximum total characters allowed.

    Returns:
        A new dict with items trimmed to fit within budget.
    """
    if not extras:
        return extras

    # Calculate total size
    total = sum(len(str(v)) for v in extras.values())
    if total <= budget:
        return extras

    logger.info(
        f"[CONTEXT BUDGET] Total extras: {total} chars, budget: {budget} chars. "
        f"Trimming {total - budget} chars."
    )

    # Sort keys by priority (low priority first = truncated first)
    def priority_key(key: str) -> int:
        try:
            return PRIORITY_ORDER.index(key)
        except ValueError:
            return -1  # Not in priority list = lowest priority (truncated first)

    sorted_keys = sorted(extras.keys(), key=priority_key)

    result = {}
    remaining_budget = budget

    # Process in REVERSE priority order (highest first, gets budget first)
    for key in reversed(sorted_keys):
        value = str(extras[key])
        value_len = len(value)

        if remaining_budget <= 0:
            # No budget left — skip this item entirely
            logger.warning(
                f"[CONTEXT BUDGET] Dropped '{key}' ({value_len} chars) — budget exhausted"
            )
            continue

        # Cap individual items at MAX_SINGLE_EXTRA_CHARS
        if value_len > MAX_SINGLE_EXTRA_CHARS:
            value = value[:MAX_SINGLE_EXTRA_CHARS] + "\n...[truncated by context budget]"
            value_len = len(value)

        if value_len <= remaining_budget:
            result[key] = value
            remaining_budget -= value_len
        else:
            # Partial fit — truncate to remaining budget
            truncated = value[:remaining_budget] + "\n...[truncated by context budget]"
            result[key] = truncated
            remaining_budget = 0
            logger.info(
                f"[CONTEXT BUDGET] Truncated '{key}' from {value_len} to {len(truncated)} chars"
            )

    final_total = sum(len(str(v)) for v in result.values())
    logger.info(
        f"[CONTEXT BUDGET] After trimming: {final_total} chars "
        f"({len(result)}/{len(extras)} items kept)"
    )

    return result


class ContextBudgetCoordinator(Extension):
    """Enforces a total character budget on extras_temporary.

    Runs at position 99 (LAST in prompts_after phase) to cap the aggregate
    size of all injected extras. This prevents empty model completions caused
    by context window overflow.
    """

    async def execute(self, loop_data: "LoopData" = None, **kwargs):
        if not loop_data:
            return

        extras = loop_data.extras_temporary
        if not extras:
            return

        # Read budget from agent config, with default fallback
        budget = DEFAULT_BUDGET_CHARS
        if hasattr(self.agent, 'config'):
            budget = getattr(self.agent.config, 'context_budget_chars', DEFAULT_BUDGET_CHARS)

        loop_data.extras_temporary = _apply_budget(extras, budget)
