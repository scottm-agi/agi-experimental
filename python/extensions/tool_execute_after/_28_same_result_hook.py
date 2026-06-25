"""
Same-Result Loop Hook — tool_execute_after extension.

Runs AFTER every tool execution. If the tool succeeded (no error),
feeds the result to SameResultTracker. When the same tool produces
N identical consecutive results, injects a warning into the agent's
history to break the stuck loop.

Hooks into: tool_execute_after (order 28)

Root cause: Iteration 151, Issue 7 — sequential_thinking MCP returns
identical success content N times. same_error_tracker only catches
errors, not identical successes.
"""

from __future__ import annotations

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.same_result_tracker import SameResultTracker

logger = logging.getLogger("agix.same_result_hook")

# Tools excluded from same-result tracking.
# These naturally produce identical outputs (e.g., "response" always
# returns the response text, "update_task_list" returns the list state).
EXCLUDED_TOOLS = {
    "response",
    "update_task_list",
    "memories_retrieve",
    "memories_save",
    "memories_delete",
    "memories_forget",
    "code_execution_tool",  # Output varies by code
}

# Singleton tracker — shared across all agents in the process
_global_tracker = SameResultTracker(threshold=3)


class SameResultHook(Extension):
    """Detect stuck-success loops where a tool returns identical results.

    After each successful tool execution, hashes the result and checks
    for N consecutive identical outputs. If detected, injects a warning
    diagnostic into the agent's history.
    """

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict = None,
        tool_result: Any = None,
        **kwargs,
    ):
        if not tool_name:
            return

        # Skip excluded meta-tools
        if tool_name.lower() in EXCLUDED_TOOLS:
            return

        # Only track successful results (non-empty, no error prefix)
        result_str = str(tool_result) if tool_result else ""
        if not result_str or result_str.startswith("Error:"):
            return

        agent_id = (
            getattr(self.agent, "agent_name", "") or str(id(self.agent))
        )

        warning = _global_tracker.check(agent_id, tool_name, result_str)

        if warning:
            await self.agent.hist_add_warning(warning)
            logger.warning(
                f"[SAME-RESULT HOOK] {agent_id}: "
                f"Tool '{tool_name}' stuck — identical result "
                f"{_global_tracker.get_count(agent_id, tool_name)}x"
            )
