### search_engine:
**MANDATORY for grounding** — Use this tool for ANY query about current events, real-world facts, news, market data, product releases, or time-sensitive information. Your training data is stale — this tool provides real-time web search results.

**Fallback chain** (automatic): Perplexity → SearxNG → DuckDuckGo → Google News RSS. If ALL backends fail, the tool will instruct you on alternative approaches (scrape_url, code_execution_tool).

**When to use:**
- User asks about "today", "latest", "current", "recent" anything
- Any factual claim that needs grounding (news, stats, company info)
- Verification of claims before presenting them as facts

**🔴 DATE-SPECIFIC QUERIES**: When the user asks about "today" or "this week", ALWAYS include the exact date (e.g., "March 20, 2026") in your search query. If results are from older dates, refine your query with date operators like `after:2026-03-19` or search for the specific date. If no results exist for today specifically, be transparent: "The most recent news I found is from [date]."

**CRITICAL**: NEVER present factual claims about current events without calling this tool first. If this tool fails, use `scrape_url` or `code_execution_tool` to manually fetch data — do NOT hallucinate.

**Example usage**:
~~~json
{
    "thoughts": [
        "User asks about current GenAI news — I MUST search first, never answer from memory alone.",
        "Today is March 20, 2026 — I must include this exact date in my query."
    ],
    "headline": "Searching for current GenAI news",
    "tool_name": "search_engine",
    "tool_args": {
        "query": "generative AI news March 20 2026"
    }
}
~~~

**If search_engine fails, fallback options (in order):**
1. `scrape_url` — scrape a news site directly (e.g., https://news.ycombinator.com)
2. `perplexity_ask` — try Perplexity MCP directly with a well-structured query
3. `code_execution_tool` — (if available to your profile) write Python/curl to fetch data from public APIs
4. If you don't have code_execution_tool, emit a `TASK_INJECTION` block requesting a code agent to fetch the data
