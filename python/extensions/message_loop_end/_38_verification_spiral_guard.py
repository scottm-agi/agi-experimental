"""
Verification Spiral Guard — Detects and stops read-only loops.

Extension Point: message_loop_end (fires after each tool execution)
Order: 38 (after organize_history at 10, before promise_checker at 45)

Root Cause (ITR-29f — 2026-06-05):
    Code agent subordinates enter a "verification spiral" where they:
    1. Fix a small issue
    2. Verify the fix (read_file, code_execution)
    3. Discover a new issue during verification
    4. Fix that issue
    5. Verify again → discover yet another issue → repeat

    Each iteration stamps _last_tool_activity (resetting idle timeout)
    and produces valid tool calls (so empty-response breaker doesn't fire).
    The existing budget_reserve_advisor fires at 60% of 200 = iter 120,
    far too late to prevent 20+ iteration stalls.

Fix:
    Track "iterations since last file write". After a threshold of
    consecutive read-only iterations, inject escalating wrap-up directives.
    At circuit_break threshold, also sets _force_response to force exit.

Architecture:
    - Uses verification_spiral_detector.py for pure logic (testable)
    - Extension handles agent integration only (data access, history injection)
    - Only fires for subordinates (agents with _superior in data)
"""
from __future__ import annotations

import logging
import re
from typing import Any, TYPE_CHECKING

from python.helpers.extension import Extension

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.verification_spiral")

# ── Regex to extract tool name from LLM responses ──
# Matches patterns like: <tool_name>write_to_file</tool_name>
# or "tool_name": "write_to_file"
_TOOL_NAME_PATTERNS = [
    re.compile(r'<tool_name>\s*(\w+)\s*</tool_name>'),
    re.compile(r'"tool_name"\s*:\s*"(\w+)"'),
    re.compile(r'"name"\s*:\s*"(\w+)"'),
]


class VerificationSpiralGuard(Extension):
    """Inject escalating directives when subordinates stall in read-only loops."""

    async def execute(self, loop_data: Any = None, **kwargs):
        """Fire at message_loop_end to check verification spiral state."""
        agent = self.agent

        # ── GATE 1: Only for subordinates ──
        from python.agent import Agent
        is_subordinate = bool(agent.data.get(Agent.DATA_NAME_SUPERIOR))
        if not is_subordinate:
            return

        # ── Extract last tool name from agent's response ──
        tool_name = self._extract_last_tool_name(agent)
        if not tool_name:
            return  # No tool detected — skip this iteration

        # ── Extract tool result for code_execution heuristic ──
        tool_result = self._extract_tool_result(agent)

        # ── Update counter ──
        from python.helpers.verification_spiral_detector import (
            update_write_counter,
            get_spiral_action,
        )
        counter = update_write_counter(agent.data, tool_name, tool_result)

        # ── Get spiral action ──
        # ITR-30 SS-4: Extract profile from agent_name for profile-aware thresholds
        profile = self._extract_profile(agent)
        action_result = get_spiral_action(counter, is_subordinate=True, profile=profile)
        action = action_result["action"]
        message = action_result["message"]

        if action == "none":
            # Log periodically for debugging
            if counter > 0 and counter % 4 == 0:
                logger.debug(
                    f"[VERIFICATION_SPIRAL] {agent.agent_name}: "
                    f"counter={counter}, last_tool={tool_name} — no action"
                )
            return

        # ── Inject message ──
        if action in ("warn", "hard"):
            logger.warning(
                f"[VERIFICATION_SPIRAL] {agent.agent_name}: "
                f"action={action}, counter={counter}, last_tool={tool_name}"
            )
            await agent.hist_add_warning(message=message)

        elif action == "circuit_break":
            logger.error(
                f"[VERIFICATION_SPIRAL] {agent.agent_name}: "
                f"CIRCUIT BREAK — counter={counter}, last_tool={tool_name}. "
                f"Forcing response."
            )
            await agent.hist_add_warning(message=message)
            # Signal the loop to force a response on the next iteration
            agent.data["_force_response"] = True

    @staticmethod
    def _extract_last_tool_name(agent: "Agent") -> str:
        """Extract the most recent tool name from agent's message history.

        Scans the last few messages for tool call patterns to identify
        what tool was just executed.

        Returns:
            Tool name string, or empty string if none found.
        """
        try:
            messages = getattr(agent.history, "messages_all", [])
            if not messages:
                return ""

            # Check last 3 messages for tool name patterns
            for msg in reversed(messages[-3:]):
                content = getattr(msg, "content", "")
                if not isinstance(content, str):
                    # Could be a dict with tool info
                    if isinstance(content, dict):
                        tool_name = content.get("tool_name", "")
                        if tool_name:
                            return tool_name
                    continue

                for pattern in _TOOL_NAME_PATTERNS:
                    match = pattern.search(content)
                    if match:
                        return match.group(1)
        except Exception as e:
            logger.debug(f"[VERIFICATION_SPIRAL] Failed to extract tool name: {e}")

        return ""

    @staticmethod
    def _extract_tool_result(agent: "Agent") -> str:
        """Extract the most recent tool result from agent's message history.

        Used for code_execution_tool heuristic — we need the command/output
        to determine if it was a write command.

        Returns:
            Tool result string, or empty string if none found.
        """
        try:
            messages = getattr(agent.history, "messages_all", [])
            if not messages:
                return ""

            # Check last 3 messages for tool results
            for msg in reversed(messages[-3:]):
                content = getattr(msg, "content", "")
                if isinstance(content, dict):
                    tool_result = content.get("tool_result", "")
                    if tool_result:
                        return str(tool_result)
                elif isinstance(content, str):
                    # Look for code_execution output patterns
                    if "code_execution" in content.lower():
                        return content
        except Exception as e:
            logger.debug(f"[VERIFICATION_SPIRAL] Failed to extract tool result: {e}")

        return ""

    @staticmethod
    def _extract_profile(agent: "Agent") -> str:
        """Extract agent profile type from agent_name.

        ITR-30 SS-4: Maps agent names to profile types for
        profile-aware spiral thresholds.

        Mapping:
            *frontend* → "frontend"
            *architect* → "architect"
            *research* → "researcher"
            *code* → "code"
            fallback → None (uses default thresholds)

        Returns:
            Profile name string, or None if unrecognized.
        """
        name = (getattr(agent, "agent_name", "") or "").lower()
        if "frontend" in name:
            return "frontend"
        elif "architect" in name:
            return "architect"
        elif "research" in name:
            return "researcher"
        elif "code" in name:
            return "code"
        elif "orchestrat" in name:
            return "orchestrator"
        # FIX-020: Use centralized profile registry for orchestrator detection
        from python.helpers.profile_registry import is_orchestrator
        if is_orchestrator(name):
            return "orchestrator"
        return None
