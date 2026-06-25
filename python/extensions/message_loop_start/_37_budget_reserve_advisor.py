"""
Budget Reserve Advisor — Proactive budget exhaustion prevention extension.

Extension Point: message_loop_start (fires every turn)
Order: 37 (after project auto-detect at 35, before remote intervention at 40)

Root Cause (Iteration 208b + 209 — MainStreet budget exhaustion):
    Delegated agents (subordinates AND multiagentdev peers) burn 60-80%
    of their turn budget on infrastructure without writing application
    source code. The parent receives a None result with no deliverables.

Fix (Iteration 209 — scope broadening):
    This extension monitors budget utilization every turn and injects
    escalating advisory messages when a delegated agent is wasting turns
    on infrastructure instead of writing application code.

Architecture:
    - Fires for ALL delegated agents (TASK/BACKGROUND context, or _superior)
    - Skips only the root USER-facing chat agent (no overhead for human chat)
    - Rate-limited: max 3 advisory injections per monologue
    - Uses budget_reserve.py for pure logic (testable without agent)
    - Scans agent history for file write tool calls to detect app code
"""
from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING

from python.helpers.extension import Extension

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.budget_reserve_advisor")

# Max advisory injections per monologue to avoid flooding
_MAX_ADVISORIES = 3


class BudgetReserveAdvisor(Extension):
    """Inject budget advisories when subordinates waste turns on infrastructure."""

    async def execute(self, **kwargs):
        """Fire at message_loop_start to check budget reserve."""
        agent = self.agent
        loop_data = kwargs.get("loop_data")

        # ── GATE 1: Only for delegated agents (not root user chat) ──
        # RCA Iteration 209: The old check (`agent.data.get('_superior')`)
        # missed multiagentdev peer agents, which operate in TASK context
        # without a superior. Fix: fire for ANY non-USER-root agent.
        from python.agent import Agent
        from python.helpers.agent_core.base import AgentContextType
        is_root_user = (
            agent.context.type == AgentContextType.USER
            and not agent.data.get(Agent.DATA_NAME_SUPERIOR)
        )
        if is_root_user:
            return  # Root user-facing chat agent — skip

        # ── GATE 2: Rate limit advisories ──
        advisory_count = agent.data.get("_budget_reserve_advisory_count", 0)
        if advisory_count >= _MAX_ADVISORIES:
            return  # Already injected max advisories

        # ── GATE 3: Get budget utilization ──
        from python.helpers.budget_reserve import (
            get_budget_utilization,
            has_written_application_code,
            build_budget_advisory,
            build_critical_response_directive,
            CRITICAL_UTILIZATION_THRESHOLD,
        )

        current_turn = getattr(agent, "_total_monologue_iterations", 0)
        max_turns = agent.get_max_turns()
        utilization = get_budget_utilization(current_turn, max_turns)

        # ── CRITICAL CHECK (RCA-316c): Force response at 95%+ budget ──
        # This is NOT advisory — it's a system-level instruction that forces
        # the agent to call the response tool. Bypasses MAX_ADVISORIES because
        # it's a forced exit, not an advisory.
        if utilization >= CRITICAL_UTILIZATION_THRESHOLD:
            turns_remaining = max(0, max_turns - current_turn)
            critical_directive = build_critical_response_directive(
                utilization, turns_remaining, max_turns
            )
            if critical_directive:
                logger.error(
                    f"[BUDGET_RESERVE] {agent.agent_name}: CRITICAL — "
                    f"utilization={utilization:.0%}, forcing response directive. "
                    f"Remaining={turns_remaining} of {max_turns}"
                )
                # Inject as system message for maximum LLM compliance
                await agent.hist_add_warning(message=critical_directive)
                return  # Don't inject advisory too — critical overrides

        # Only check at 60%+ utilization (don't waste cycles on early turns)
        if utilization < 0.6:
            return

        # ── GATE 4: Detect application code writes ──
        file_paths = self._extract_file_paths_from_history(agent)
        has_app_code = has_written_application_code(file_paths)

        # ── BUILD & INJECT ADVISORY ──
        turns_remaining = max(0, max_turns - current_turn)
        advisory = build_budget_advisory(utilization, has_app_code, turns_remaining)

        if advisory:
            agent.data["_budget_reserve_advisory_count"] = advisory_count + 1
            logger.warning(
                f"[BUDGET_RESERVE] {agent.agent_name}: "
                f"utilization={utilization:.0%}, has_app_code={has_app_code}, "
                f"remaining={turns_remaining}, advisory #{advisory_count + 1}"
            )
            await agent.hist_add_warning(message=advisory)

    @staticmethod
    def _extract_file_paths_from_history(agent: "Agent") -> list[str]:
        """Extract file paths from write_to_file/replace_in_file tool calls in history.

        Scans agent's message history for AI messages containing tool calls
        that write files. Returns list of file paths found.
        """
        file_paths: list[str] = []
        try:
            messages = getattr(agent.history, "messages_all", [])
            for msg in messages:
                if not getattr(msg, "ai", False):
                    continue
                content = getattr(msg, "content", "")
                if not isinstance(content, str):
                    continue

                # Match file paths from write_to_file / replace_in_file calls
                # Common patterns in tool call JSON:
                #   "filename": "/path/to/file"
                #   "file": "path/to/file"
                #   "path": "path/to/file"
                path_matches = re.findall(
                    r'"(?:filename|file|path)"\s*:\s*"([^"]+)"', content
                )
                for p in path_matches:
                    if p not in file_paths:
                        file_paths.append(p)
        except Exception as e:
            logger.debug(f"Failed to extract file paths from history: {e}")

        return file_paths
