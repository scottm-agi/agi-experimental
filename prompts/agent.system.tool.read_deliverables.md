### read_deliverables
Read specialist agent deliverables from the project's `deliverables/` directory.
Use this tool to load all outputs from specialist agents (researcher, account-leader, marketing-lead, sales-enabler) before synthesizing them into a unified document.

**You MUST call this tool with mode `read_all` at the START of your task to load all specialist outputs.**

**Modes:**
- `list`: Show all available deliverables with metadata (agent, title, size)
- `read_all`: Load and concatenate ALL deliverables — use this for full synthesis
- `read`: Load deliverables for a specific agent role only
- `search`: Search across deliverables for specific content (with grep fallback)

**Arguments:**
- `mode` (required): One of `list`, `read_all`, `read`, `search`
- `agent_role` (optional): Filter by agent role (for `read` mode)
- `query` (optional): Search term (for `search` mode)

**Recommended workflow:**
1. First call `read_deliverables` with `mode: "read_all"` to load all specialist outputs
2. Analyze and cross-reference the specialist findings
3. Synthesize into a single cohesive document using SCQA + Minto Pyramid

~~~json
{
    "thoughts": ["I need to load all specialist outputs before I can synthesize them."],
    "tool_name": "read_deliverables",
    "tool_args": {
        "mode": "read_all"
    }
}
~~~

**Fallback:** If `read_all` returns no deliverables, use `search` mode to look for specialist content across the project directory.
