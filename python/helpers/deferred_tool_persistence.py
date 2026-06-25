"""#14 (P2): Deferred Tool Call Persistence.

When the batch fence defers a tool call, persist it to agent.data
so it survives conversation pauses and can be recovered on next loop.

Root cause (RCA-ITR42, SS-2):
    Deferred tools stored only in message content (conversation history),
    not in agent.data. If conversation paused before re-submit, the
    deferred tool call was silently lost.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger("agix.deferred_tool_persistence")

# Maximum number of deferred tools to keep (prevents unbounded growth)
MAX_DEFERRED_TOOLS = 10


def persist_deferred_tool(
    agent_data: dict,
    tool_name: str,
    tool_args: Dict[str, Any],
    reason: str = "",
) -> None:
    """Persist a deferred tool call to agent.data for recovery.

    Args:
        agent_data: The agent's data dict.
        tool_name: Name of the deferred tool.
        tool_args: Arguments that were passed to the tool.
        reason: Why the tool was deferred (e.g., "batch_fence").
    """
    deferred = agent_data.get("_deferred_tool_calls", [])

    entry = {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "reason": reason,
        "deferred_at": datetime.now(timezone.utc).isoformat(),
    }

    deferred.append(entry)

    # Cap at MAX_DEFERRED_TOOLS — keep most recent
    if len(deferred) > MAX_DEFERRED_TOOLS:
        deferred = deferred[-MAX_DEFERRED_TOOLS:]

    agent_data["_deferred_tool_calls"] = deferred

    logger.info(
        "#14: Persisted deferred tool %s (reason: %s). "
        "Total deferred: %d",
        tool_name, reason, len(deferred),
    )


def recover_deferred_tools(agent_data: dict) -> List[Dict[str, Any]]:
    """Recover and clear all deferred tool calls.

    Returns the list of deferred tools and clears the persistence key.
    The caller is responsible for re-submitting them.

    Args:
        agent_data: The agent's data dict.

    Returns:
        List of deferred tool entries (may be empty).
    """
    deferred = agent_data.get("_deferred_tool_calls", [])

    if deferred:
        logger.info(
            "#14: Recovering %d deferred tool(s): %s",
            len(deferred),
            [d["tool_name"] for d in deferred],
        )
        agent_data["_deferred_tool_calls"] = []

    return deferred
