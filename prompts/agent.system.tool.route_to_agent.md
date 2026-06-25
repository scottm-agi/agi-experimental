## route_to_agent

Route a user request to the appropriate specialist agent.

### When to use
- When the user asks for development work → route to `multiagentdev`
- When the user asks for sales/marketing → route to `alex`
- When the user asks for security work → route to `security_auditor`
- When the user asks for research → route to `researcher`
- For simple greetings or questions → handle directly (do NOT use this tool)

### Parameters
- **message** (required): The user's full request to route. Pass the COMPLETE user message.
- **profile** (optional): Target agent profile to route to. If omitted, intent is auto-detected from the message.

### Routing Rules
| User Intent | Route To |
|---|---|
| Build, code, develop, debug, test | `multiagentdev` |
| Sales, marketing, outreach, campaigns | `alex` |
| Security audit, vulnerability scan | `security_auditor` |
| MCP server, model context protocol | `mcp_builder` |
| Research, analysis, investigation | `researcher` |
| Simple question, greeting, clarification | Handle directly (don't route) |

### Important
- Do NOT route simple questions. If the user says "hello" or "what can you do?", respond directly.
- You CANNOT route directly to `code`, `debug`, `frontend`, or `architect` — these must go through `multiagentdev`.
- You CANNOT route directly to `content-writer`, `marketing-lead`, or `sales-enabler` — these must go through `alex`.
- When in doubt, let the auto-detection decide (omit the `profile` parameter).
