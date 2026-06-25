"""
MCP Per-Tool Circuit Breaker — Blacklists failing tool names after 3 retries.

When an agent calls a wrong MCP tool name (e.g., `get-library-docs` instead
of `query-docs`), it retries 10+ times. This tracker blacklists individual
tool names after N consecutive failures and provides guidance.

Usage:
    from python.helpers.mcp_tool_tracker import MCPToolTracker

    tracker = MCPToolTracker(max_failures=3)
    # In MCPTool.execute() error handler:
    tracker.record_failure(server_name, tool_name)
    if tracker.is_blacklisted(server_name, tool_name):
        warning = tracker.get_warning(server_name, tool_name)
"""

import logging
import threading
from typing import Dict, Optional, Tuple

logger = logging.getLogger("agix.mcp_tool_tracker")


class MCPToolTracker:
    """Tracks per-tool MCP failures and blacklists after threshold.

    Tracking is keyed by (server_name, tool_name) tuple.
    Thread-safe via lock for concurrent agent access.
    """

    def __init__(self, max_failures: int = 3):
        self.max_failures = max_failures
        self._failures: Dict[Tuple[str, str], int] = {}
        self._blacklisted: Dict[Tuple[str, str], bool] = {}
        self._lock = threading.Lock()

    def record_failure(self, server_name: str, tool_name: str) -> None:
        """Record a failure for a specific tool on a specific server."""
        key = (server_name, tool_name)
        with self._lock:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.max_failures:
                self._blacklisted[key] = True
                logger.warning(
                    f"[MCP_TOOL_TRACKER] Blacklisted tool '{tool_name}' on "
                    f"server '{server_name}' after {count} consecutive failures"
                )

    def record_success(self, server_name: str, tool_name: str) -> None:
        """Record a success — resets the failure counter for this tool."""
        key = (server_name, tool_name)
        with self._lock:
            self._failures.pop(key, None)
            self._blacklisted.pop(key, None)

    def is_blacklisted(self, server_name: str, tool_name: str) -> bool:
        """Check if a tool is currently blacklisted."""
        key = (server_name, tool_name)
        with self._lock:
            return self._blacklisted.get(key, False)

    def get_warning(self, server_name: str, tool_name: str) -> Optional[str]:
        """Get a formatted warning if tool is blacklisted, else None."""
        if not self.is_blacklisted(server_name, tool_name):
            return None
        key = (server_name, tool_name)
        count = self._failures.get(key, 0)
        return (
            f"⚠️ Tool '{tool_name}' on server '{server_name}' has been "
            f"BLOCKED after {count} consecutive failures. "
            f"This tool name may be incorrect. "
            f"Try listing available tools for this server, or use a different "
            f"tool name. Do NOT retry this exact tool name."
        )

    def reset(self) -> None:
        """Clear all tracking data (e.g., on new task/conversation)."""
        with self._lock:
            self._failures.clear()
            self._blacklisted.clear()

    def get_stats(self) -> Dict[str, int]:
        """Get current tracking statistics."""
        with self._lock:
            return {
                "tracked_tools": len(self._failures),
                "blacklisted_tools": sum(1 for v in self._blacklisted.values() if v),
            }
