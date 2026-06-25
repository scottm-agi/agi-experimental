"""
IO-Breaker — Deterministic supervisor tools for breaking I/O blocks.

These are the tools that transform the supervisor from a passive observer
(can only inject messages) into an active controller (can directly manipulate
agent state). All operations are DETERMINISTIC — no LLM calls needed.

The supervisor (3rd agent / outside observer) uses these to implement the
8-step escalation ladder when agents stall:

  Step 1-3: Existing nudge/redirect/escalate (already in monitoring.py)
  Step 4: break_pause — clear context.paused
  Step 5: cancel_task — cancel stuck asyncio task via TaskRegistry
  Step 6: reset_state — clear error counters, gate state
  Step 7: force_return — set is_done=True + deliver partial results
  Step 8: escalate_human — absolute last resort

User feedback (2026-04-22): "The deep diagnostic read is deterministic data
reads (chat, errors, gate state), NOT LLM-powered."

See: rca_asyncio_blocking_coe.md, supervisor_io_breaker.md
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from python.helpers.requirements_ledger import get_delegation_ledger_for_gate


if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.supervisor.io_breaker")


class IOBreaker:
    """Deterministic IO-breaking tools for the supervisor agent.

    All methods are static — they operate on agent references passed in.
    No LLM calls, no network, no side effects beyond state mutation.
    """

    @staticmethod
    def break_pause(agent: "Agent") -> bool:
        """Clear context.paused on a target agent.

        This is the primary tool for recovering from the #1 blocking bug:
        structural guards or other code setting context.paused=True with
        no way to clear it.

        Args:
            agent: The agent to unpause

        Returns:
            True if the agent was paused and is now unpaused,
            False if already unpaused.
        """
        if not hasattr(agent, "context") or not agent.context:
            return False

        if not agent.context.paused:
            logger.debug(
                f"[IO-BREAKER] break_pause: {agent.agent_name} already unpaused"
            )
            return False

        agent.context.paused = False
        logger.warning(
            f"[IO-BREAKER] break_pause: cleared context.paused on "
            f"{agent.agent_name}"
        )
        return True

    @staticmethod
    def cancel_task(composite_id: str) -> bool:
        """Cancel a stuck asyncio task via the TaskRegistry.

        Args:
            composite_id: "{agent_name}@{context_id}" format

        Returns:
            True if found and cancelled, False if not found.
        """
        from python.helpers.task_registry import TaskRegistry

        registry = TaskRegistry.instance()
        result = registry.cancel_task(composite_id)

        if result:
            logger.warning(
                f"[IO-BREAKER] cancel_task: cancelled {composite_id}"
            )
        else:
            logger.debug(
                f"[IO-BREAKER] cancel_task: {composite_id} not found in registry"
            )
        return result

    @staticmethod
    async def force_return(agent: "Agent", message: str) -> None:
        """Force an agent to stop and deliver partial results.

        Sets is_done=True and injects a supervisor warning into history
        so the agent's response loop breaks cleanly.

        Args:
            agent: The agent to force-stop
            message: Message explaining why the stop was forced
        """
        # Set loop termination
        if hasattr(agent, "loop_data") and agent.loop_data:
            agent.loop_data.is_done = True
            agent.loop_data.stop_reason = (
                f"Supervisor force_return: {message}"
            )

        # Inject warning into history
        try:
            await agent.hist_add_warning(
                message=(
                    f"🛑 **SUPERVISOR FORCE RETURN**: {message}\n\n"
                    f"Delivering best-effort results now."
                )
            )
        except Exception as e:
            logger.error(
                f"[IO-BREAKER] force_return: failed to inject warning "
                f"into {agent.agent_name}: {e}"
            )

        # Also clear paused if set (belt-and-suspenders)
        if hasattr(agent, "context") and agent.context:
            agent.context.paused = False

        logger.warning(
            f"[IO-BREAKER] force_return: stopped {agent.agent_name} — "
            f"{message}"
        )

    @staticmethod
    def reset_state(agent: "Agent") -> None:
        """Reset error counters, gate state, and duplicate state.

        Gives the agent a clean slate to attempt recovery after
        the supervisor redirects it.

        Args:
            agent: The agent whose state to reset
        """
        # Reset core counters
        agent._error_count = 0
        agent._failed_tool_count = 0

        # Reset gate state
        data = agent.data if hasattr(agent, "data") else {}
        keys_to_zero = [
            "_consecutive_gate_rejections",
            "_orchestrator_completion_blocks",
            "_consecutive_duplicate_responses",
            "_consecutive_mistake_count",
        ]
        keys_to_remove = [
            "_error_state_bypassed",
            "_last_blocked_response",
        ]

        for key in keys_to_zero:
            data[key] = 0
        for key in keys_to_remove:
            data.pop(key, None)

        logger.info(
            f"[IO-BREAKER] reset_state: cleared counters for "
            f"{agent.agent_name}"
        )

    @staticmethod
    def read_agent_state(agent: "Agent") -> Dict[str, Any]:
        """Read deterministic diagnostic state from an agent.

        This is the "deep diagnostic read" — purely deterministic,
        no LLM calls. Returns structured data the supervisor can use
        to make decisions about which escalation step to apply.

        Returns a dict with:
        - agent_name, number, absolute_turns
        - error_count, failed_tool_count
        - is_paused, is_done
        - gate_rejections, completion_blocks
        - tool_failures (dict of tool → count)
        - recent_chat (last 5 messages, truncated)
        - delegation_ledger (last 5 entries)
        """
        data = agent.data if hasattr(agent, "data") else {}

        # Recent chat (last 5 messages, truncated to 200 chars each)
        recent_chat = []
        try:
            if hasattr(agent, "history"):
                history = agent.history.output() if hasattr(agent.history, "output") else []
                for msg in history[-5:]:
                    role = getattr(msg, "role", "?")
                    content = getattr(msg, "content", "")
                    if callable(getattr(msg, "output_text", None)):
                        content = msg.output_text()
                    content = str(content)[:200] if content else ""
                    recent_chat.append({"role": role, "content": content})
        except Exception:
            pass

        # ── Delegation loop detector state ──
        # Exposes per-hash attempt/failure counts so the supervisor's
        # _quick_assess_agent() can see accumulated delegation failures
        # synchronously (complement to async REPEATED_TASK_FAILURE signal).
        delegation_loop_state: Dict[str, Any] = {}
        try:
            from python.extensions.tool_execute_before._27_delegation_loop_hook import _global_detector
            agent_name = getattr(agent, "agent_name", "")
            if agent_name:
                attempt_counts = dict(
                    _global_detector._attempt_counts.get(agent_name, {})
                )
                failure_counts = dict(
                    _global_detector._failure_counts.get(agent_name, {})
                )
                delegation_loop_state = {
                    "total_unique_tasks": len(attempt_counts),
                    "total_attempts": (
                        sum(attempt_counts.values()) if attempt_counts else 0
                    ),
                    "total_failures": (
                        sum(failure_counts.values()) if failure_counts else 0
                    ),
                    "threshold_exceeded": (
                        any(
                            v >= _global_detector.failure_threshold
                            for v in failure_counts.values()
                        )
                        if failure_counts
                        else False
                    ),
                    "max_failures_single_task": (
                        max(failure_counts.values()) if failure_counts else 0
                    ),
                }
        except Exception:
            pass

        return {
            "agent_name": getattr(agent, "agent_name", "unknown"),
            "agent_number": getattr(agent, "number", -1),
            "absolute_turns": getattr(agent, "_absolute_turns", 0),
            "error_count": getattr(agent, "_error_count", 0),
            "failed_tool_count": getattr(agent, "_failed_tool_count", 0),
            "is_paused": (
                agent.context.paused
                if hasattr(agent, "context") and agent.context
                else False
            ),
            "is_done": (
                agent.loop_data.is_done
                if hasattr(agent, "loop_data") and agent.loop_data
                else False
            ),
            "gate_rejections": data.get("_consecutive_gate_rejections", 0),
            "completion_blocks": 0,  # gate_block_counters stub removed — was always 0
            "consecutive_mistakes": data.get("_consecutive_mistake_count", 0),
            "tool_failures": data.get("_tool_failure_counts", {}),
            "recent_chat": recent_chat,
            "delegation_ledger": get_delegation_ledger_for_gate(data)[-5:],
            "execution_status": (
                agent.context._execution_status
                if hasattr(agent, "context") and agent.context
                   and hasattr(agent.context, "_execution_status")
                else None
            ),
            "delegation_loop_state": delegation_loop_state,
        }
