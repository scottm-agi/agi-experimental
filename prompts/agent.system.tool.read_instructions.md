## Tool: read_instructions

Read and consolidate all instructions, rules, and configuration relevant to the current task context.

### Usage
~~~json
{
    "tool_name": "read_instructions",
    "tool_args": {
        "scope": "all",
        "directory": "/path/to/project"
    }
}
~~~

### Parameters
- **scope** (optional, default: "all"): Which instruction sources to load.
  - `"all"` — Load everything: global custom instructions, mode-specific instructions, per-directory .rules, agents.md
  - `"global"` — Only global custom instructions from prompts/
  - `"mode"` — Only mode-specific custom instructions
  - `"rules"` — Only per-directory .rules files from the project tree
  - `"agents"` — Only agents.md and .agents/ configuration
- **directory** (optional): Root directory to search from. Defaults to the current project directory.

### When to Use
- **At task start**: Load all instructions to understand project rules before writing code
- **When entering a new directory**: Load per-directory .rules to follow local conventions
- **When delegated to**: Sub-agents should call this to pick up the full instruction context
- **Before making architectural decisions**: Check agents.md for project-specific patterns

### Sources Loaded
1. **Global Custom Instructions** (`prompts/agent.system.custom_instructions.md`) — Stable framework versions, component compatibility, universal rules
2. **Mode-Specific Instructions** — Loaded from mode_manager for the current operating mode
3. **Per-Directory .rules** — Convention files walked up from the project working directory (up to 5 levels)
4. **agents.md** — Project-level agent configuration and architecture guidelines
5. **.agents/ directory** — Additional agent-specific markdown configuration files
