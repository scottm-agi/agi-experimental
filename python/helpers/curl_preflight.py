"""
E2E Curl Pre-flight Checker — ITR-29e

Ensures the E2E agent curls all endpoints BEFORE launching browser_agent.
No point browsing dead services.

Flow:
1. E2E agent starts testing
2. Gate blocks browser_agent until curl pre-flight passes
3. E2E agent uses code_execution_tool to curl the base URL
4. tool_execute_after extension detects curl result, updates checker
5. If curl returns 2xx/3xx → pre-flight passed → browser unblocked
6. If curl fails/5xx → E2E should STOP and report back to orchestrator

The checker is stored on agent.data["_curl_preflight"] so it persists
across iterations within a delegation.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("agix.curl_preflight")

# ── Browser tools that require pre-flight ────────────────────────────
BROWSER_TOOLS = frozenset({"browser_agent", "browser_subagent"})

# ── HTTP response patterns ───────────────────────────────────────────
# Match "HTTP/1.1 200 OK" or "HTTP/2 200"
_HTTP_STATUS_LINE = re.compile(r"HTTP/[\d.]+\s+(\d{3})\b")

# Match standalone 3-digit HTTP code (from curl -w '%{http_code}')
_BARE_HTTP_CODE = re.compile(r"^(\d{3})$", re.MULTILINE)

# Match curl connection errors
_CURL_ERROR = re.compile(
    r"curl:\s*\(\d+\)\s*(Failed to connect|Connection refused|Couldn't connect|"
    r"Could not resolve host|Operation timed out|Empty reply)",
    re.IGNORECASE,
)


def detect_curl_result(output: str) -> Optional[Dict[str, Any]]:
    """Detect curl result from tool output text.

    Parses common curl output formats to extract HTTP status code
    and success/failure state.

    Args:
        output: Raw text output from code_execution_tool.

    Returns:
        Dict with {http_code, success} if curl result detected, else None.
    """
    if not output or len(output.strip()) == 0:
        return None

    text = output.strip()

    # Check for curl errors first
    error_match = _CURL_ERROR.search(text)
    if error_match:
        return {"http_code": 0, "success": False, "error": error_match.group(0)}

    # Check for HTTP status line (HTTP/1.1 200 OK)
    status_match = _HTTP_STATUS_LINE.search(text)
    if status_match:
        code = int(status_match.group(1))
        return {
            "http_code": code,
            "success": 200 <= code < 400,
        }

    # Check for bare HTTP code (from curl -w '%{http_code}')
    bare_match = _BARE_HTTP_CODE.search(text)
    if bare_match:
        code = int(bare_match.group(1))
        if code == 0:
            return {"http_code": 0, "success": False}
        return {
            "http_code": code,
            "success": 200 <= code < 400,
        }

    # No curl result detected
    return None


def should_block_browser(
    tool_name: str,
    has_passed: bool,
    is_e2e: bool,
) -> bool:
    """Determine if browser_agent should be blocked.

    Args:
        tool_name: The tool being called.
        has_passed: Whether curl pre-flight has passed.
        is_e2e: Whether the current agent is the E2E tester.

    Returns:
        True if the tool should be blocked.
    """
    # Only gate browser tools
    if tool_name not in BROWSER_TOOLS:
        return False

    # Only gate E2E agents
    if not is_e2e:
        return False

    # Block if pre-flight hasn't passed
    return not has_passed


class CurlPreflightChecker:
    """Tracks whether the E2E agent has successfully curled endpoints.

    Stored on agent.data["_curl_preflight"] for persistence across
    iterations within a single delegation.
    """

    def __init__(self):
        self._passed: bool = False
        self._attempts: list[Dict[str, Any]] = []

    def has_passed(self) -> bool:
        """Whether at least one curl returned a healthy response."""
        return self._passed

    def record_curl_result(
        self,
        url: str,
        http_code: int,
        success: bool,
    ) -> None:
        """Record a curl attempt result.

        Args:
            url: The URL that was curled.
            http_code: The HTTP response code (0 if connection failed).
            success: Whether the response indicates a live, healthy service.
        """
        self._attempts.append({
            "url": url,
            "http_code": http_code,
            "success": success,
        })

        if success:
            self._passed = True
            logger.info(
                f"[CURL PREFLIGHT] ✅ Passed — {url} returned HTTP {http_code}"
            )
        else:
            logger.warning(
                f"[CURL PREFLIGHT] ❌ Failed — {url} returned HTTP {http_code}"
            )

    def reset(self) -> None:
        """Reset for a new test run."""
        self._passed = False
        self._attempts.clear()

    def get_block_message(self) -> str:
        """Get the message to show when browser is blocked.

        Returns different messages depending on whether curl was
        attempted (and failed) or never attempted at all.
        """
        if self._attempts:
            # Curl was attempted but failed
            failed = [a for a in self._attempts if not a["success"]]
            summary = "; ".join(
                f"{a['url']} → HTTP {a['http_code']}" for a in failed[-3:]
            )
            return (
                "## 🛑 BROWSER BLOCKED — Service is NOT live\n\n"
                "You attempted to curl the endpoints but they are NOT responding:\n"
                f"- {summary}\n\n"
                "**DO NOT use browser_agent on dead endpoints.** Instead:\n"
                "1. STOP all testing immediately\n"
                "2. Write a **fix report** via `save_deliverable` reporting that "
                "the dev server is not running or returning errors\n"
                "3. The orchestrator will re-delegate to the code agent to fix the service\n"
                "4. You will be re-invoked once the service is healthy\n\n"
                "**No additional work is needed from you until the service is live.**"
            )
        else:
            # Curl was never attempted
            return (
                "## 🛑 BROWSER BLOCKED — Curl pre-flight required\n\n"
                "You MUST curl the dev server endpoints BEFORE using browser_agent.\n"
                "This verifies the service is actually running and responding.\n\n"
                "**Steps:**\n"
                "1. Use `code_execution_tool` to run: "
                "`curl -s -o /dev/null -w '%{http_code}' http://localhost:<PORT>`\n"
                "2. If HTTP 200-399 → the service is live → you may then use browser_agent\n"
                "3. If HTTP 000/5xx/error → STOP testing and write a fix report "
                "via `save_deliverable` — the service needs fixing by the code agent\n\n"
                "**Do NOT skip this step.** Browsing dead endpoints wastes time."
            )
