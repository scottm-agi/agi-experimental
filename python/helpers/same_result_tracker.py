"""
Same-Result Loop Tracker — detects N identical SUCCESSFUL tool outputs.

Complement to same_error_tracker.py which only catches identical errors.
Catches "stuck success" loops where an agent keeps calling a tool and getting
the exact same result, indicating it's stuck in a loop expecting different
results from the same action.

Root cause: Iteration 151, Issue 7 — sequential_thinking MCP returns
identical success content N times. Agent loops forever because
same_error_tracker only catches errors, not identical successes.

Architecture:
    - Tracks by (agent_id, tool_name) → {last_hash, consecutive_count}
    - After N identical results (default 3), returns warning diagnostic
    - Uses MD5 of result content (first 2000 chars) for comparison
    - Auto-resets when a different result is seen for the same tool

Usage:
    tracker = SameResultTracker(threshold=3)
    warning = tracker.check("agent-1", "sequential_thinking", result_text)
    if warning:
        await agent.hist_add_warning(warning)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("agix.same_result_tracker")


class SameResultTracker:
    """Detect N identical SUCCESSFUL tool outputs.

    Tracks consecutive identical results per (agent_id, tool_name) pair.
    When the same result is seen N times in a row, returns a warning
    diagnostic string. A different result resets the counter.
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        # agent_id → tool_name → {"last_hash": str, "count": int}
        self._trackers: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"last_hash": "", "count": 0})
        )

    def check(
        self, agent_id: str, tool_name: str, result: str
    ) -> Optional[str]:
        """Record a successful tool result. Returns warning if threshold hit.

        Args:
            agent_id: The agent that executed the tool.
            tool_name: Name of the tool.
            result: The successful result string.

        Returns:
            None if below threshold.
            Warning diagnostic string if N identical results detected.
        """
        # Hash first 2000 chars to keep memory bounded
        from python.helpers.hashing import content_hash_short
        result_hash = content_hash_short(
            result[:2000], length=12
        )

        tracker = self._trackers[agent_id][tool_name]

        if tracker["last_hash"] == result_hash:
            tracker["count"] += 1
        else:
            # Different result — reset counter
            tracker["last_hash"] = result_hash
            tracker["count"] = 1

        count = tracker["count"]

        if count >= self.threshold:
            logger.warning(
                f"SameResultTracker: {agent_id} tool='{tool_name}' "
                f"returned identical result {count}x (hash={result_hash})"
            )
            return (
                f"⚠️ SAME-RESULT LOOP DETECTED: Tool '{tool_name}' returned "
                f"identical result {count} consecutive times "
                f"(hash={result_hash}). "
                f"The agent is stuck — each call produces the same output. "
                f"You MUST change your approach: use a different tool, "
                f"different parameters, or skip this step entirely."
            )

        return None

    def reset(self, agent_id: str) -> None:
        """Clear all tracking for an agent."""
        self._trackers.pop(agent_id, None)

    def get_count(self, agent_id: str, tool_name: str) -> int:
        """Get the current consecutive count for a tool."""
        return self._trackers.get(agent_id, {}).get(
            tool_name, {"count": 0}
        )["count"]
