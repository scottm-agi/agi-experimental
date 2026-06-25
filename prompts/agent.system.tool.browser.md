### browser_agent:

Use this tool to navigate to websites, perform research, extract data, and interact with web applications. This is the preferred tool for all **complex, interactive browser-based tasks**. 
For **high-fidelity scraping of static or complex JS sites**, prefer `scrape_url` (Crawl4AI) as it is faster and more deterministic for bulk content extraction.
DO NOT use code_execution_tool to write custom Playwright or Selenium scripts. 
Always use browser_agent for robust, screenshot-enabled browsing.

subordinate agent controls playwright browser
message argument talks to agent give clear instructions credentials task based

## 🔴 MANDATORY: Curl-First Pre-Flight (before ANY UAT/verification browsing)
Before calling `browser_agent` for UAT or visual verification, you MUST first use `code_execution_tool` to curl every route:
```bash
for route in / /discovery /dashboard /audit; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:<PORT>$route)
  BODY=$(curl -s http://localhost:<PORT>$route | head -c 200)
  echo "Route: $route → HTTP $CODE"
  if echo "$BODY" | grep -qi "404\|not found\|page could not"; then echo "  ⚠️ SOFT-404 DETECTED"; fi
done
```
- If >30% of routes return 4xx/5xx or soft-404 → report `QUALITY: FAIL` immediately. Do NOT launch browser_agent.
- Only call `browser_agent` after confirming routes return real content via curl.
- This prevents the common failure mode of spending 40+ browser steps clicking through 404 pages.
**url argument MUST be provided** when you know the target URL — this pre-navigates the browser directly to the page before the agent starts. Without it, the browser starts on about:blank and may never reach the target.
reset argument spawns new agent
do not reset if iterating
be precise descriptive like: open google login and end task, log in using ... and end task
when following up start: considering open pages
dont use phrase wait for instructions use end task
downloads default in /agix/tmp/downloads
pass secrets and variables in message when needed

usage:
```json
{
  "thoughts": ["I need to verify the local dev server at http://0.0.0.0:5100/"],
  "headline": "Browsing local dev server for visual verification",
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Navigate to http://0.0.0.0:5100/ and verify the landing page renders correctly with proper styling, navigation, and content. Take a screenshot.",
    "url": "http://0.0.0.0:5100/",
    "reset": "true"
  }
}
```

```json
{
  "thoughts": ["I need to log in to..."],
  "headline": "Opening new browser session for login",
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Open and log me into...",
    "url": "https://example.com/login",
    "reset": "true"
  }
}
```

```json
{
  "thoughts": ["I need to continue on the current page..."],
  "headline": "Continuing with existing browser session",
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Considering open pages, click...",
    "reset": "false"
  }
}
```

