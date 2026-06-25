### tool name: memory_load

Recall previously saved knowledge from agent memory. Use this to retrieve patterns, conventions, preferences, and findings saved in prior sessions.

**When to use**: You need information that was saved with `memory_save` in a previous session — project conventions, user preferences, debugging patterns, or previously discovered facts.

**Arguments**:
- `query`: A search query describing what you're looking for. Can be a key name, topic, or natural language description.
- `scope` (optional): Limit search to a specific scope (e.g., project name, category)

**Rules**:
- Search BEFORE you research — check if you already know the answer from a prior session
- Use specific queries — "my_project deploy" is better than "deploy"
- If no results found, the knowledge hasn't been saved yet — discover it and save with `memory_save`
- Memories are shared across the agent system — you can recall what any agent saved

**Example**:
```json
{
    "tool_name": "memory_load",
    "tool_args": {
        "query": "current project tech stack"
    }
}
```
