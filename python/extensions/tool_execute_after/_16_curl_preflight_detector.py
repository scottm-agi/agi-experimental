"""
E2E Curl Pre-flight Detector — ITR-29e (tool_execute_after)

Monitors tool output for service liveness results. When the E2E
agent runs curl commands, test_service, or scrape_url, this extension
detects the HTTP response code and updates the CurlPreflightChecker.

Wired with _12_browser_preflight_gate.py:
- This extension (after) detects curl/test results → updates checker
- The gate (before) blocks browser_agent until checker has_passed

ITR-29e hotfix: Also monitors services_mgt (test_service action) and
scrape_url since E2E agents use these as curl equivalents.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from python.helpers.extension import Extension
from python.helpers.curl_preflight import (
    CurlPreflightChecker,
    detect_curl_result,
)

logger = logging.getLogger("agix.curl_preflight_detector")

# Tools that can produce liveness evidence
_LIVENESS_TOOLS = frozenset({
    "code_execution_tool", "code_execution",
    "services_mgt",
    "scrape_url",
})


class CurlPreflightDetector(Extension):
    # Context-aware: e2e agents only, code execution and services
    PROFILES = {"e2e"}
    TOOLS = frozenset({"code_execution_tool", "code_execution", "services_mgt", "scrape_url"})

    """Detects liveness results in tool output for E2E agents."""

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        response: Any = None,
        **kwargs,
    ):
        # Only monitor liveness-relevant tools
        if tool_name not in _LIVENESS_TOOLS:
            return None

        agent = self.agent
        if not agent:
            return None

        # Only monitor E2E agents
        agent_name = getattr(agent, "agent_name", "").lower()
        if "e2e" not in agent_name:
            return None

        # Extract output from the response
        output = ""
        if response and hasattr(response, "message"):
            output = str(response.message)
        elif response:
            output = str(response)

        if not output:
            return None

        # ── services_mgt test_service path ────────────────────────────
        if tool_name == "services_mgt":
            action = ""
            if tool_args:
                action = str(tool_args.get("action", ""))
            if action != "test_service":
                return None
            # Parse JSON response from test_service
            return self._handle_test_service(agent, tool_args, output)

        # ── scrape_url path ───────────────────────────────────────────
        if tool_name == "scrape_url":
            return self._handle_scrape_url(agent, tool_args, output)

        # ── code_execution_tool path (original curl detection) ────────
        command = ""
        if tool_args:
            command = str(tool_args.get("code", "") or tool_args.get("command", ""))

        is_curl_command = "curl" in command.lower() if command else False

        result = detect_curl_result(output)
        if result is None:
            return None

        if not is_curl_command and result.get("http_code", 0) == 0:
            return None

        return self._record_result(agent, command, result)

    def _handle_test_service(self, agent, tool_args, output):
        """Handle services_mgt test_service result."""
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return None

        http_code = data.get("http_code", 0)
        status = data.get("status", "error")
        port = ""
        if tool_args:
            port = str(tool_args.get("port", ""))

        url = f"http://localhost:{port}" if port else "unknown"
        success = status == "success" and 200 <= http_code < 400

        checker = self._get_checker(agent)
        checker.record_curl_result(url=url, http_code=http_code, success=success)
        logger.info(
            f"[CURL PREFLIGHT] Detected test_service result: "
            f"port={port} code={http_code} success={success}"
        )
        return None

    def _handle_scrape_url(self, agent, tool_args, output):
        """Handle scrape_url result — if it returned content, service is live."""
        url = ""
        if tool_args:
            url = str(tool_args.get("url", ""))

        # If scrape_url returned substantial content, the service is live
        if output and len(output.strip()) > 50 and "error" not in output[:100].lower():
            checker = self._get_checker(agent)
            checker.record_curl_result(url=url, http_code=200, success=True)
            logger.info(
                f"[CURL PREFLIGHT] Detected scrape_url success: url={url}"
            )
        return None

    def _record_result(self, agent, command, result):
        """Record a curl result from code_execution_tool."""
        url = "unknown"
        if command:
            url_match = re.search(r"https?://\S+", command)
            if url_match:
                url = url_match.group(0).rstrip("'\"")

        checker = self._get_checker(agent)
        checker.record_curl_result(
            url=url,
            http_code=result["http_code"],
            success=result["success"],
        )
        return None

    def _get_checker(self, agent):
        """Get or create the CurlPreflightChecker."""
        checker = agent.data.get("_curl_preflight")
        if checker is None:
            checker = CurlPreflightChecker()
            agent.data["_curl_preflight"] = checker
        return checker

