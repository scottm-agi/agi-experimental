from __future__ import annotations
"""
Search Grounding Gate — tool_execute_after extension

Tracks search tool usage during the agent's session. When the 'response'
tool fires, checks if ANY search/grounding tool was called. If NOT, and
the query appears to require real-world facts, blocks the response and
injects instructions to:
1. Try code_execution_tool (write Python/curl to search)
2. Or delegate to a code-capable subordinate agent
3. Or explicitly tell the user: "I cannot verify this — all search tools
   are unavailable"

This prevents the agent from fabricating "Verified" facts when search
tools are missing.
"""

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.search_grounding_gate")

# Tools that count as "search grounding"
# FIX-028 (G-5): Removed call_subordinate/batch — delegation to ANY profile
# was counted as "grounded research", trivially bypassing the gate.
# Only actual search/scrape tools count as grounding.
SEARCH_TOOLS = {
    "search_engine",
    "scrape_url",
    "perplexity_ask",        # MCP tool name
    "perplexity-ask",        # MCP server name
    "code_execution_tool",   # Can be used to write search scripts
}

# Keywords that indicate the query needs real-world facts
FACTUAL_KEYWORDS = [
    "news", "today", "latest", "current", "recent", "released",
    "announced", "launched", "update", "market", "stock", "price",
    "weather", "score", "result", "event", "happened",
]


class SearchGroundingGate(Extension):
    # Context-aware: researcher profile, response + search tools
    PROFILES = {"researcher"}

    """Block response if factual query was answered without any search."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return



        tool_lower = tool_name.lower().replace("-", "_")

        # Track search tool usage
        if tool_lower in SEARCH_TOOLS or any(s in tool_lower for s in SEARCH_TOOLS):
            self.agent.data.setdefault("_search_tools_used", set())
            self.agent.data["_search_tools_used"].add(tool_lower)
            logger.info(
                f"[SEARCH GROUNDING GATE] Search tool used: {tool_name}"
            )
            return

        # Only intercept the 'response' tool
        if tool_lower != "response":
            return

        # Check if any search tool was used this session
        search_used = self.agent.data.get("_search_tools_used", set())
        if search_used:
            logger.info(
                f"[SEARCH GROUNDING GATE] Response OK — search tools used: "
                f"{search_used}"
            )
            # Reset for next turn
            self.agent.data["_search_tools_used"] = set()
            return

        # No search tool was used — check if the query needed facts
        needs_facts = self._query_needs_facts()
        if not needs_facts:
            logger.info(
                "[SEARCH GROUNDING GATE] Response OK — query doesn't need "
                "factual grounding"
            )
            return

        # Check if this is a retry (we already warned once)
        if self.agent.data.get("_grounding_gate_warned", False):
            logger.warning(
                "[SEARCH GROUNDING GATE] SECOND ATTEMPT — still no search. "
                "Injecting disclaimer into response."
            )
            # On second attempt, let it through but add disclaimer
            if isinstance(response, Response) and response.message:
                disclaimer = (
                    "\n\n---\n⚠️ **Disclaimer**: This response could not be "
                    "verified against real-time sources. All search tools were "
                    "unavailable during this session. The information above may "
                    "be based on stale training data and should be independently "
                    "verified.\n"
                )
                response.message += disclaimer
            self.agent.data["_grounding_gate_warned"] = False
            self.agent.data["_search_tools_used"] = set()
            return

        # FIRST ATTEMPT — block and force search/code fallback
        logger.warning(
            "[SEARCH GROUNDING GATE] BLOCKED — factual response without any "
            "search tool call. Forcing code_execution fallback."
        )

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "search_grounding"):
            return  # Allow through

        if isinstance(response, Response):
            response.break_loop = False

        # Update the response in-place for seamless UX (Issue #862)
        # Instead of removing and recreating, clear content and show retry heading
        try:
            loop_data = self.agent.loop_data
            if loop_data and "log_item_response" in loop_data.params_temporary:
                log_item = loop_data.params_temporary["log_item_response"]
                log_item.update(
                    content="",
                    heading=f"icon://refresh {self.agent.agent_name}: Retrying (search grounding)...",
                )
                logger.info(
                    "[SEARCH GROUNDING GATE] Updated response log item in-place "
                    "for retry"
                )
        except Exception as e:
            logger.warning(
                f"[SEARCH GROUNDING GATE] Could not update log item: {e}"
            )

        warning = (
            "⚠️ SEARCH GROUNDING VIOLATION: You attempted to respond to a "
            "factual query WITHOUT calling any search tool first.\n\n"
            "Your response has been BLOCKED because it may contain fabricated "
            "information. You MUST do ONE of the following:\n\n"
            "1. **Use code_execution_tool** — write a Python script to search "
            "the web (e.g., using `requests` + Google News RSS, or `curl` to "
            "a public API like HackerNews, Reddit, or Brave Search).\n"
            "   Example:\n"
            "   ```python\n"
            "   import urllib.request, json\n"
            "   url = 'https://news.google.com/rss/search?q=generative+AI+today&hl=en'\n"
            "   data = urllib.request.urlopen(url).read().decode()\n"
            "   print(data[:3000])\n"
            "   ```\n\n"
            "2. **Delegate to a code-capable subordinate** — use "
            "`call_subordinate` with the `multiagentdev` profile to write a "
            "search script for you.\n\n"
            "3. **Tell the user honestly** — if you truly cannot search, "
            "respond: 'I'm unable to verify current news — all my search "
            "tools are unavailable. Would you like me to try writing a custom "
            "search script?'\n\n"
            "🔴 NEVER fabricate facts. NEVER say 'Verified' without a tool "
            "call. NEVER present training data as current news."
        )

        await self.agent.hist_add_warning(message=warning)
        # Counter already incremented by gate_check above
        self.agent.data["_grounding_gate_warned"] = True

    def _query_needs_facts(self) -> bool:
        """Check if the original user query needs real-world fact grounding."""
        # Look at the agent's history for the user's message
        try:
            history = self.agent.history
            for msg in history:
                role = getattr(msg, "role", "") or ""
                content = str(getattr(msg, "content", "") or "").lower()
                if role == "user" and any(kw in content for kw in FACTUAL_KEYWORDS):
                    return True
            # Also check if this is a researcher agent (always needs facts)
            agent_name = getattr(self.agent, "agent_name", "").lower()
            if "research" in agent_name:
                return True
        except Exception:
            pass
        return False
