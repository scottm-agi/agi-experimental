"""
Tool Failure Tracker

F-7: Classifies tool failures as permanent (401/403) vs transient (5xx, timeouts)
and maintains a session-level blacklist for permanently failed tools.

Root cause (SS-7): perplexity_ask failed with 401 Unauthorized 33 times before
the agent switched to tavily. 401 is a permanent failure (wrong API key) but
the tracker treated it as transient, allowing endless retries.

Fix: Classify 401/403 as PERMANENT on first occurrence. Add tool to session
blacklist and surface hints to the LLM so it switches immediately.

Permanent status codes (will NOT self-heal):
  - 401 Unauthorized: Invalid/expired API key
  - 403 Forbidden: Insufficient permissions

Transient status codes (MAY self-heal with retry):
  - 429 Rate Limited: Wait and retry
  - 500 Internal Server Error: Server-side issue
  - 502/503/504 Gateway errors: Infrastructure issues
  - 0/None: Network timeout or connection refused
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("agix.tool_failure_tracker")

# Status codes that indicate permanent, non-recoverable failures
PERMANENT_STATUS_CODES = {401, 403}


def classify_tool_failure(
    tool_name: str,
    error_message: str,
    status_code: Optional[int] = None,
) -> str:
    """Classify a tool failure as permanent or transient.

    Permanent failures (401/403) indicate authentication or authorization
    issues that will NOT self-heal — retrying wastes tokens and time.

    Transient failures (5xx, timeouts, rate limits) MAY resolve with
    retry after backoff.

    Args:
        tool_name: Name of the failed tool (e.g., "perplexity_ask")
        error_message: The error message from the tool
        status_code: HTTP status code if available

    Returns:
        "permanent" for 401/403, "transient" for everything else
    """
    if status_code in PERMANENT_STATUS_CODES:
        logger.warning(
            f"[TOOL FAILURE] PERMANENT failure for {tool_name}: "
            f"HTTP {status_code} — {error_message[:100]}"
        )
        return "permanent"

    logger.info(
        f"[TOOL FAILURE] Transient failure for {tool_name}: "
        f"HTTP {status_code} — {error_message[:100]}"
    )
    return "transient"


class ToolFailureTracker:
    """Session-level tracker for tool failures with permanent blacklisting.

    Maintains a record of all tool failures and classifies them. Tools
    with permanent failures are blacklisted for the session — the tracker
    generates hints that tell the LLM to use alternatives.

    Usage:
        tracker = ToolFailureTracker()
        tracker.record_failure("perplexity_ask", "401 Unauthorized", 401)
        hint = tracker.get_hint("perplexity_ask")
        # → "Tool 'perplexity_ask' is permanently unavailable (HTTP 401). ..."
    """

    def __init__(self) -> None:
        self._failures: Dict[str, List[Dict]] = {}
        self._blacklisted: Dict[str, Dict] = {}

    def record_failure(
        self,
        tool_name: str,
        error_message: str,
        status_code: Optional[int] = None,
    ) -> str:
        """Record a tool failure and classify it.

        If the failure is permanent, the tool is immediately blacklisted
        for the remainder of the session.

        Args:
            tool_name: Name of the failed tool
            error_message: The error message
            status_code: HTTP status code if available

        Returns:
            Classification: "permanent" or "transient"
        """
        classification = classify_tool_failure(tool_name, error_message, status_code)

        # Record the failure
        if tool_name not in self._failures:
            self._failures[tool_name] = []
        self._failures[tool_name].append({
            "error_message": error_message,
            "status_code": status_code,
            "classification": classification,
        })

        # Blacklist on permanent failure
        if classification == "permanent" and tool_name not in self._blacklisted:
            self._blacklisted[tool_name] = {
                "reason": error_message,
                "status_code": status_code,
            }
            logger.warning(
                f"[TOOL FAILURE] Tool '{tool_name}' BLACKLISTED for session "
                f"(HTTP {status_code}: {error_message[:100]})"
            )

        return classification

    def get_hint(self, tool_name: str) -> Optional[str]:
        """Get an advisory hint for a tool, if it has been blacklisted.

        Returns a human-readable hint that the LLM can use to avoid
        retrying a permanently failed tool and switch to alternatives.

        Args:
            tool_name: Name of the tool to check

        Returns:
            Hint string if tool is blacklisted, None otherwise
        """
        if tool_name not in self._blacklisted:
            return None

        info = self._blacklisted[tool_name]
        status = info.get("status_code", "unknown")
        reason = info.get("reason", "unknown error")

        return (
            f"Tool '{tool_name}' is permanently unavailable (HTTP {status}). "
            f"Reason: {reason}. This tool has been blacklisted for this session. "
            f"Use an alternative tool instead."
        )

    def is_blacklisted(self, tool_name: str) -> bool:
        """Check if a tool is blacklisted.

        Args:
            tool_name: Name of the tool to check

        Returns:
            True if the tool has been permanently blacklisted
        """
        return tool_name in self._blacklisted

    def get_all_blacklisted(self) -> List[str]:
        """Get all blacklisted tool names.

        Returns:
            List of blacklisted tool names
        """
        return list(self._blacklisted.keys())

    def get_failure_count(self, tool_name: str) -> int:
        """Get the total number of recorded failures for a tool.

        Args:
            tool_name: Name of the tool

        Returns:
            Number of recorded failures
        """
        return len(self._failures.get(tool_name, []))
