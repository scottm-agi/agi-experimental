"""
Service Retry Tracker — ITR-29d

Tracks per-port failure history for services_mgt to prevent infinite
restart loops. When the same port fails N times consecutively, blocks
further attempts and injects actionable guidance ("fix the build first").

Root cause (RCA-ITR29d): services_mgt processes each call independently
with no memory of past failures. The agent calls it 16 times in a loop
because:
- No retry budget per port
- Interleaved read_file/replace_in_file successes reset generic counters
- No build-readiness check before starting

This tracker is stored on agent.data["_service_retry_tracker"] so it
persists across iterations within a delegation.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("agix.service_retry_tracker")

# Default max retries before blocking
DEFAULT_MAX_RETRIES = 3


class ServiceRetryTracker:
    """Track per-port service start/restart failures.

    After max_retries consecutive failures on the same port, blocks
    further attempts and provides a diagnostic telling the agent to
    fix the underlying issue (build errors, port conflicts) first.

    Args:
        max_retries: Maximum failures per port before blocking.
    """

    def __init__(self, max_retries: int = DEFAULT_MAX_RETRIES):
        self.max_retries = max_retries
        self._failures: Dict[int, List[str]] = {}  # port → [error messages]

    def record_failure(self, port: int, error_msg: str) -> None:
        """Record a failure for a port.

        Args:
            port: The port number that failed.
            error_msg: Description of the failure.
        """
        port = int(port)
        if port not in self._failures:
            self._failures[port] = []
        self._failures[port].append(error_msg[:300])  # Cap message length
        logger.warning(
            f"[SERVICE RETRY] Port {port}: failure #{len(self._failures[port])} — "
            f"{error_msg[:100]}"
        )

    def record_success(self, port: int) -> None:
        """Reset failure counter for a port after successful start.

        Args:
            port: The port that started successfully.
        """
        port = int(port)
        if port in self._failures:
            logger.info(
                f"[SERVICE RETRY] Port {port}: success — resetting "
                f"counter (was {len(self._failures[port])} failures)"
            )
            del self._failures[port]

    def get_failure_count(self, port: int) -> int:
        """Get the number of consecutive failures for a port."""
        return len(self._failures.get(int(port), []))

    def should_block(self, port: int) -> bool:
        """Check if a port has exceeded its retry budget.

        Args:
            port: The port to check.

        Returns:
            True if the port should be blocked from further attempts.
        """
        return self.get_failure_count(port) >= self.max_retries

    def get_last_errors(self, port: int) -> List[str]:
        """Get the list of error messages for a port."""
        return list(self._failures.get(int(port), []))

    def get_block_message(self, port: int) -> Optional[str]:
        """Get the diagnostic message when a port is blocked.

        Args:
            port: The blocked port.

        Returns:
            Actionable diagnostic message, or None if not blocked.
        """
        if not self.should_block(port):
            return None

        errors = self.get_last_errors(port)
        count = len(errors)

        # Analyze failure patterns to give specific advice
        all_errors = " ".join(errors).lower()
        has_build_errors = any(kw in all_errors for kw in [
            "build failed", "eslint", "ts2307", "typescript",
            "cannot find module", "parsing error", "syntax error",
        ])
        has_port_conflict = any(kw in all_errors for kw in [
            "eaddrinuse", "port", "busy", "in use",
        ])

        advice_lines = []
        if has_build_errors:
            advice_lines.append(
                "- **Fix build errors FIRST**: The build is failing. Fix all "
                "TypeScript/ESLint errors before trying to start the service."
            )
        if has_port_conflict:
            advice_lines.append(
                "- **Port conflict**: Use `kill_port` action to free the port, "
                "or specify a different port."
            )
        if not advice_lines:
            advice_lines.append(
                "- Fix the underlying issue causing the service to fail before retrying."
            )

        error_summary = "\n".join(f"  {i+1}. {e[:100]}" for i, e in enumerate(errors[-3:]))

        return (
            f"## 🛑 SERVICE START BLOCKED — {count} consecutive failures on port {port}\n\n"
            f"services_mgt has failed {count} times on port {port}. "
            f"Further attempts are **blocked** until the underlying issue is fixed.\n\n"
            f"### Recent errors:\n{error_summary}\n\n"
            f"### What to do:\n"
            + "\n".join(advice_lines) + "\n"
            f"- After fixing, call `services_mgt` with `action=start_service` "
            f"(NOT restart_service) to start fresh.\n"
        )
