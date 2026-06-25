"""
Tool Failure Reset — message_loop_start extension

Ported from Roo-Code Task.ts:2768-2771.

At the START of each new message loop iteration:
1. Resets _tool_failed_in_current_turn to False so that old failures
   don't leak into a new turn.
2. Checks _consecutive_mistake_count — if >= THRESHOLD, injects a
   system prompt nudge telling the agent to change approach.
3. F-8: At FORCE_STOP threshold, sets loop_data.is_done = True to
   enforce the circuit breaker (advisory hints are ignored at 8+).

NOTE: _consecutive_mistake_count is NOT reset here. It is only reset
to 0 on a successful tool execution (handled by _12_tool_failure_tracker).

Hooks into: message_loop_start (order 06 — very early, before any context injection)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from python.helpers.extension import Extension

logger = logging.getLogger("agix.tool_failure_reset")

# After this many consecutive tool errors, inject guidance
CONSECUTIVE_MISTAKE_THRESHOLD = 3

# After this many, force topic reset
CONSECUTIVE_MISTAKE_HARD_LIMIT = 8

# F-8: After this many consecutive mistakes, force-stop the loop.
# At 8 the agent gets advisory guidance; at 15 the loop is terminated.
# Without this enforcement tier, advisory hints are ignored and the
# counter reaches 20+ in production.
CONSECUTIVE_MISTAKE_FORCE_STOP = 15


class ToolFailureReset(Extension):
    """Reset per-turn tool failure flag and escalate on consecutive errors."""

    async def execute(self, loop_data: Optional[Any] = None, **kwargs) -> None:
        # ─── 1. Reset per-turn flag ──────────────────────────────────
        self.agent.data["_tool_failed_in_current_turn"] = False

        # ─── 2. Consecutive mistake escalation ───────────────────────
        count = self.agent.data.get("_consecutive_mistake_count", 0)

        # F-8: FORCE STOP — enforcement tier (highest priority)
        if count >= CONSECUTIVE_MISTAKE_FORCE_STOP:
            stop_reason = (
                f"Force-stop: {count} consecutive tool mistakes with no recovery"
            )
            if loop_data and hasattr(loop_data, "is_done"):
                loop_data.is_done = True
            if loop_data and hasattr(loop_data, "stop_reason"):
                loop_data.stop_reason = stop_reason
            if loop_data and hasattr(loop_data, "system"):
                guidance = (
                    f"🛑 FORCE STOP: {count} consecutive tool errors. "
                    f"The loop has been terminated. No further tool calls "
                    f"will be processed."
                )
                loop_data.system.append(guidance)

            # Emit L2 escalation signal for supervisor awareness
            signals: list = self.agent.data.get("_l2_escalation_signals", [])
            signals.append({
                "source": "tool_failure_reset",
                "detector": "consecutive_mistake_force_stop",
                "severity": "critical",
                "detail": (
                    f"Force-stopped agent after {count} consecutive tool "
                    f"mistakes. Agent {self.agent.agent_name} could not "
                    f"recover despite advisory hints at thresholds "
                    f"{CONSECUTIVE_MISTAKE_THRESHOLD} and "
                    f"{CONSECUTIVE_MISTAKE_HARD_LIMIT}."
                ),
            })
            self.agent.data["_l2_escalation_signals"] = signals

            logger.error(
                f"[TOOL FAILURE RESET] {self.agent.agent_name}: "
                f"FORCE STOP — {count} consecutive mistakes, "
                f"loop terminated (is_done=True)"
            )

        elif count >= CONSECUTIVE_MISTAKE_HARD_LIMIT:
            # Hard limit — inject strong reset guidance
            # RCA-400 F-2: Check which tools are blocked to avoid
            # contradictory guidance (telling agent to use a blocked tool).
            blocked_tools = self.agent.data.get("_tracker_blocked_tools", set())
            code_exec_blocked = (
                "code_execution_tool" in blocked_tools
                or "code_execution" in blocked_tools
            )

            if code_exec_blocked:
                file_check_advice = (
                    "2. Check what files actually exist (use `read_file`, "
                    "`list_dir`, or `search_files` — code_execution_tool "
                    "is currently blocked)\n"
                )
            else:
                file_check_advice = (
                    "2. Check what files actually exist (use code_execution_tool "
                    "to run 'ls' and 'cat')\n"
                )

            guidance = (
                f"🚨 CRITICAL: You have made {count} consecutive tool errors. "
                f"Your current approach is NOT working. STOP and completely "
                f"change your strategy:\n\n"
                f"1. Re-read the original task requirements\n"
                f"{file_check_advice}"
                f"3. Fix the root cause instead of retrying the same command\n"
                f"4. If a dependency is missing, install it first\n"
                f"5. If a file path is wrong, verify the correct path\n\n"
                f"Do NOT repeat the same failing command."
            )
            if loop_data and hasattr(loop_data, "system"):
                loop_data.system.append(guidance)
            logger.error(
                f"[TOOL FAILURE RESET] {self.agent.agent_name}: "
                f"HARD LIMIT — {count} consecutive mistakes, injecting "
                f"strong guidance"
            )

        elif count >= CONSECUTIVE_MISTAKE_THRESHOLD:
            # Soft threshold — inject warning guidance
            guidance = (
                f"⚠️ CONSECUTIVE ERRORS ({count}): Your last {count} tool "
                f"executions all returned errors. Before trying again:\n\n"
                f"1. Read the error messages carefully\n"
                f"2. Verify file paths and module names are correct\n"
                f"3. Check that required dependencies are installed\n"
                f"4. Consider a different approach if the current one keeps failing\n\n"
                f"If you keep getting errors, use code_execution_tool to inspect "
                f"the actual file system state before making more changes."
            )
            if loop_data and hasattr(loop_data, "system"):
                loop_data.system.append(guidance)
            logger.warning(
                f"[TOOL FAILURE RESET] {self.agent.agent_name}: "
                f"{count} consecutive mistakes, injecting guidance"
            )

        # ─── P2-C: ADAPTER SYNC — consolidate raw keys → typed state ──
        # Runs after _tool_failed_in_current_turn reset and any escalation.
        # WRAP-not-replace: raw keys still drive all decisions.
        from python.helpers.agent_data_adapter import sync_tool_failure_state
        sync_tool_failure_state(self.agent.data)
