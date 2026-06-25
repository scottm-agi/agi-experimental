### save_deliverable
Save your final deliverable output to the project. By default, files are saved as Markdown to the project's `deliverables/` directory so the **content-writer** agent can later read and synthesize all specialist outputs.

**You MUST call this tool before calling `response` to ensure your output is persisted.**

**Exception**: Do NOT call `save_deliverable` for short verification tasks, smoke tests, health checks, or status queries. Only persist deliverables for substantive research, analysis, or content creation tasks that produce documents the user or content-writer needs.

**Arguments:**
- `title` (required): A descriptive title for this deliverable (e.g., "Deep Research Report", "Account Strategy", "Campaign Plan")
- `content` (required unless `file_path` is provided): The full markdown content of your deliverable — include ALL findings, tables, analysis, and recommendations
- `file_path` (optional): Path to an existing file whose content should be saved as the deliverable. Use this when the content already exists on disk (e.g., a navigation-map.md you already wrote). If both `content` and `file_path` are provided, `content` takes priority.
- `output_path` (optional): Relative path within the project where the file should also be saved (e.g., `docs/framework-research.md`, `design-tokens.json`). When provided, the file is written to this canonical location AND a copy is saved to `deliverables/` for backward compatibility. **Path must be relative** — absolute paths and path traversal (`../`) are rejected. For `.json` files, YAML frontmatter is automatically skipped.
- `agent_role` (optional): Your agent role (auto-detected from your profile if omitted)

**Usage — default (deliverables/ only):**
~~~json
{
    "thoughts": ["I have completed my research. I need to save it as a deliverable before responding."],
    "tool_name": "save_deliverable",
    "tool_args": {
        "title": "Salesforce Deep Research Report",
        "content": "# Research Findings\n\n## Financial Overview\n- FY26 Revenue: $41.5B...\n\n## Competitive Landscape\n..."
    }
}
~~~

**Usage — canonical path (dual-write):**
~~~json
{
    "thoughts": ["The orchestrator requested this at docs/framework-research.md. I'll use output_path to place it there."],
    "tool_name": "save_deliverable",
    "tool_args": {
        "title": "Framework Research",
        "content": "# Framework Research\n\n## Version Compatibility Matrix\n...",
        "output_path": "docs/framework-research.md"
    }
}
~~~

**Usage — JSON file (no frontmatter):**
~~~json
{
    "thoughts": ["Design tokens should be machine-readable JSON, not Markdown."],
    "tool_name": "save_deliverable",
    "tool_args": {
        "title": "Design Tokens",
        "content": "{\"colors\": {\"primary\": \"#3B82F6\"}, \"spacing\": {\"sm\": \"0.5rem\"}}",
        "output_path": "design-tokens.json"
    }
}
~~~

**🔴 CRITICAL**: Always save your COMPLETE output — never truncate or summarize. The content-writer needs the full detail to produce a board-ready deliverable.

**🔴 WHEN `output_path` IS SPECIFIED IN YOUR DELEGATION**: If the orchestrator tells you to save a deliverable with a specific `output_path` (e.g., "Save deliverable with output_path='docs/framework-research.md'"), you MUST include that `output_path` parameter in your `save_deliverable` call. This ensures downstream gates can find the file at the expected location.
