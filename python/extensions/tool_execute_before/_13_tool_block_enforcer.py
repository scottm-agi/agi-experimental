"""
Tool Block Enforcer — tool_execute_before extension

RCA-289 Wiring Fix: Converts the advisory-only TIER 3 tool "block" from
_12_tool_failure_tracker.py into a hard enforcement gate.

Previously, when a tool was added to `_tracker_blocked_tools`, the tracker
injected a WARNING message but no gate actually prevented the tool from
executing. This extension reads the `_tracker_blocked_tools` set and returns
a Response (hard block) before the tool can execute.

Hooks into: tool_execute_before (order 13 — early, before most gates)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.tool_block_enforcer")


class ToolBlockEnforcer(Extension):
    """Hard-blocks tools that the ToolFailureTracker has marked as blocked.

    Reads agent.data["_tracker_blocked_tools"] (a set of tool name strings)
    and returns a Response if the current tool is in the set. This is the
    enforcement mechanism that makes TIER 3 blocks effective.

    The block is session-level and automatically clears when the tracker
    resets (on success or at session end).
    """

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs,
    ) -> Optional[Response]:
        if not tool_name:
            return None

        # Read the tracker's blocked tools set
        blocked_tools: set = self.agent.data.get("_tracker_blocked_tools", set())

        if not blocked_tools or tool_name not in blocked_tools:
            return None  # Tool not blocked, proceed normally

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "tool_block_enforcer", suffix=tool_name):
            return None

        # Tool is blocked — hard reject
        logger.warning(
            f"[TOOL BLOCK ENFORCER] BLOCKED execution of '{tool_name}' "
            f"for agent {self.agent.agent_name} — tool is in "
            f"_tracker_blocked_tools (TIER 3 block)"
        )

        # Get error context for the rejection message
        error_ctx = self.agent.data.get("_tool_failure_error_context", {})
        last_error = error_ctx.get(tool_name, "(no error details)")[:200]

        # SS-5 Fix: Profile-aware recommendations instead of hardcoded tool names
        from python.helpers.blocked_response_builder import get_profile_aware_tool_recommendations
        agent_profile = getattr(self.agent.config, "profile", "") or ""
        profile_recs = get_profile_aware_tool_recommendations(
            tool_name, agent_profile
        )

        message = (
            f"🚫 TOOL EXECUTION BLOCKED: `{tool_name}` has been temporarily "
            f"blocked due to repeated failures in this session.\n\n"
            f"**Last error**: {last_error}\n\n"
            f"You MUST use a different approach:\n"
            f"{profile_recs}\n\n"
            f"DO NOT attempt to call `{tool_name}` again — it will be blocked."
        )


        return Response(
            message=message,
            break_loop=False,
            additional={"failure_reason": "tool_blocked_tier3"},
        )
