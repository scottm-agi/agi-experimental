"""
E2E Browser Pre-flight Gate — ITR-29e (tool_execute_before)

Blocks browser_agent / browser_subagent for E2E agents until they've
curled the dev server and confirmed it's live. No point browsing dead
endpoints.

If curl failed → instructs agent to STOP and write fix report back
to orchestrator for code agent remediation.
"""
from __future__ import annotations

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.curl_preflight import (
    CurlPreflightChecker,
    should_block_browser,
    BROWSER_TOOLS,
)

logger = logging.getLogger("agix.browser_preflight_gate")


class BrowserPreflightGate(Extension):
    # Context-aware: only fire for e2e agents, on browser tools
    PROFILES = {"e2e"}

    """Blocks browser tools until curl pre-flight passes (E2E only)."""

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        # Only gate browser tools
        if tool_name not in BROWSER_TOOLS:
            return None

        # Only gate E2E agents
        agent = self.agent
        if not agent:
            return None

        agent_name = getattr(agent, "agent_name", "").lower()
        # Check if this is an E2E agent (by name or profile)
        is_e2e = "e2e" in agent_name

        if not is_e2e:
            return None

        # Get or create the pre-flight checker
        checker = agent.data.get("_curl_preflight")
        if checker is None:
            checker = CurlPreflightChecker()
            agent.data["_curl_preflight"] = checker

        if checker.has_passed():
            return None  # Pre-flight passed — allow browser

        # FIX-022 (G-10): Circuit breaker — if dev server never starts,
        # don't block the E2E agent forever. After MAX blocks, allow
        # through with a warning so the agent can report the failure.
        MAX_PREFLIGHT_BLOCKS = 5
        block_count = agent.data.get("_browser_preflight_blocks", 0)
        block_count += 1
        agent.data["_browser_preflight_blocks"] = block_count

        if block_count >= MAX_PREFLIGHT_BLOCKS:
            logger.warning(
                f"[BROWSER PREFLIGHT] Circuit breaker fired after "
                f"{block_count} blocks — allowing {tool_name} through. "
                f"Dev server may not be running."
            )
            # Allow through but flag quality degradation
            agent.data["_quality_degraded"] = True
            return None  # Allow through

        # Block browser with actionable message
        block_msg = checker.get_block_message()
        logger.warning(
            f"[BROWSER PREFLIGHT] BLOCKED {tool_name} for E2E agent "
            f"'{agent_name}' — curl pre-flight not passed "
            f"({block_count}/{MAX_PREFLIGHT_BLOCKS})"
        )

        return Response(
            message=block_msg,
            break_loop=False,
        )
