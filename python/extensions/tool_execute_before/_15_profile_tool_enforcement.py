from __future__ import annotations
"""
Profile-based Tool Enforcement Extension

Blocks tool execution when the tool is not in the agent's profile's
allowed categories (from ontology.json). This is the hard enforcement layer —
tools not in your profile are BANNED, not just discouraged.

This applies to ALL tools (native + MCP), unlike the MCP-only filtering
in _10_system_prompt.py.
"""

from typing import Any
from python.helpers.extension import Extension
from python.helpers.tool import Response
import logging

logger = logging.getLogger("agix.profile_tool_enforcement")


class ProfileToolEnforcement(Extension):
    """
    Hard-blocks tools that are not in the agent's ontology profile.

    Uses ontology.json to determine which tool categories an agent profile
    is allowed to use. Any tool not in an allowed category is blocked with
    a clear error message telling the agent to delegate instead.

    On repeat blocks (same agent, same tool), escalates the message severity
    and sets break_loop=True to terminate the agent's retry loop.

    RCA: Iteration 208 — orchestrator post-build stall caused by infinite
    blocked→delegate→return→retry→blocked loop with no escalation.
    """

    # Track how many times each (agent_number, tool_name) pair has been blocked.
    # Class-level so it persists across execute() calls within a conversation.
    _block_counts: dict[tuple[int, str], int] = {}

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs
    ):
        if not tool_name:
            return None

        profile = getattr(self.agent.config, "profile", None) or "default"

        try:
            from python.helpers.tool_selector import ToolSelector
            selector = ToolSelector.get_instance()
        except Exception:
            return None  # If ToolSelector isn't available, don't block

        # Check if this tool is allowed for the profile
        if selector.should_include_tool(tool_name, profile):
            return None  # Tool is allowed, proceed

        # ── Retry tracking ───────────────────────────────────────────
        key = (self.agent.number, tool_name)
        self._block_counts[key] = self._block_counts.get(key, 0) + 1
        block_count = self._block_counts[key]

        # Determine if this profile can delegate.
        # Two delegation mechanisms exist:
        #   - "orchestration" → call_subordinate (multiagentdev, architect, alex, etc.)
        #   - "routing" → route_to_agent (default profile)
        # Both are delegation-capable; only the tool name differs.
        profiles = selector._ontology.get("profiles", {})
        profile_categories = profiles.get(profile, profiles.get("default", []))
        can_delegate = (
            "orchestration" in profile_categories
            or "routing" in profile_categories
        )

        action = "Delegate to subordinate" if can_delegate else "Use available tools"
        logger.warning(
            f"[PROFILE_ENFORCEMENT] BLOCKED tool '{tool_name}' for profile "
            f"'{profile}' (agent #{self.agent.number}). {action}. "
            f"Block count: {block_count}."
        )

        # ── Escalate on repeat blocks ────────────────────────────────
        if block_count >= 2:
            from python.helpers.tool_enforcement_messages import build_escalated_message
            message = build_escalated_message(
                tool_name=tool_name,
                profile=profile,
                block_count=block_count,
                profile_categories=profile_categories,
            )
            logger.warning(
                f"[PROFILE_ENFORCEMENT] ESCALATED — breaking loop for agent "
                f"#{self.agent.number} after {block_count} blocks of '{tool_name}'."
            )
            return Response(
                message=message,
                break_loop=True,
                additional={"failure_reason": "tool_blocked"},
            )

        # ── First-time block: standard message ───────────────────────
        from python.helpers.tool_enforcement_messages import build_blocked_message
        # Pass delegation_type so the message references the correct tool
        delegation_type = (
            "routing" if "routing" in profile_categories
            else "orchestration" if "orchestration" in profile_categories
            else None
        )
        message = build_blocked_message(
            tool_name=tool_name,
            profile=profile,
            can_delegate=can_delegate,
            profile_categories=profile_categories,
            delegation_type=delegation_type,
        )

        return Response(
            message=message,
            break_loop=False,
            additional={"failure_reason": "tool_blocked"},
        )
