### tool name: memory_save

Persist knowledge, findings, or patterns for future recall across sessions. Saved memories survive conversation boundaries — use this to build long-term agent knowledge.

**When to use**: You've discovered something reusable — a project convention, a credential location, a debugging pattern, a user preference, or a key finding that should be available in future sessions.

**Arguments**:
- `key`: A descriptive, searchable key for this memory (e.g., "project_x_deploy_pattern", "user_prefers_typescript")
- `value`: The knowledge to persist. Can be multi-line. Be specific and actionable — future-you needs to understand this without context.

**Rules**:
- Use descriptive, searchable keys — not generic names like "info" or "data"
- Include enough context in the value that the memory is self-contained
- Don't save ephemeral data (timestamps, one-off results) — save patterns and knowledge
- Check with `memory_load` first to avoid duplicating existing memories
- Organize keys by topic: `project_<name>_*`, `user_pref_*`, `pattern_*`

**Example**:
```json
{
    "tool_name": "memory_save",
    "tool_args": {
        "key": "project_acme_tech_stack",
        "value": "The project uses Next.js 14 with App Router, Tailwind CSS, Prisma ORM with PostgreSQL. Deploy target: Vercel. Testing: Jest + React Testing Library."
    }
}
```
