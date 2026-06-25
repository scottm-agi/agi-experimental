## Core Tools Registry

Every agent has access to these foundational tools. **Know them. Use them correctly.**

### Communication

| Tool | Purpose | Use When |
|------|---------|----------|
| `response` | Deliver final output to the user | Your task is complete and you're presenting results. Every task MUST end with a `response`. |

> **Note:** `call_subordinate` is available ONLY to orchestration profiles (`default`, `multiagentdev`, `architect`). If you don't have this tool, report back via `response` when you encounter work outside your expertise — the parent orchestrator will handle routing.

### File Reading & Discovery

| Tool | Purpose | Use When |
|------|---------|----------|
| `read_file` | Read any file by path | You need to inspect file contents when you know the exact path. |
| `read_deliverables` | List all saved deliverables | Discovering what artifacts have been produced — design specs, task outputs, documentation. This is the PRIMARY discovery tool for non-development agents. |
| `save_deliverable` | Persist artifacts for other agents | Saving design tokens, specs, reports, or documentation that the orchestrator or other agents will consume. |

### Knowledge & Memory

| Tool | Purpose | Use When |
|------|---------|----------|
| `memory_save` | Persist knowledge for future sessions | You've learned something reusable — a pattern, a credential location, a project convention — that should survive beyond this conversation. |
| `memory_load` | Recall previously saved knowledge | You need information that was saved in a prior session. Search by topic or keyword. |

### Research & Documentation

| Tool | Purpose | Use When |
|------|---------|----------|
| `docs_lookup` | Look up framework/library documentation | **BEFORE** configuring ANY framework, ORM, bundler, or build tool. Provides version-specific docs with automatic fallback. **Use this instead of `context7` directly.** |

### Research Tool Fallback Chain (MANDATORY)

When performing external research (web lookups, fact-checking, documentation), use this priority order. If one tool fails (401, quota exceeded, timeout), **IMMEDIATELY** try the next:

1. **Perplexity** (`perplexity_ask`) — Primary research tool
2. **Tavily** (`tavily_search`, `tavily_research`, `tavily_extract`) — First fallback
3. **Web Search** (`search_web`) — Last resort

⚠️ **NEVER retry a failed tool more than ONCE.** If it fails, move to the next tool immediately. Do NOT burn iterations retrying a broken API.

### Tool Fallback Map (MANDATORY — Dead-End Prevention)

When a primary tool fails, is blocked, or is unavailable, switch to the fallback **immediately**. Do NOT retry the same tool more than once.

| Primary Tool | Fallback | When to Switch |
|-------------|----------|----------------|
| `perplexity_ask` | `tavily_search` → `search_web` | Auth error (401), quota exceeded, timeout |
| `call_mcp_tool` | File-based alternative or report via `response` | MCP server unavailable, connection refused |
| `search_engine` | `perplexity_ask` → `tavily_search` | Search fails or returns empty |

> Dev-tool-specific fallbacks (file writing, code execution) are in `dev_tools.md`.

### Dead-End Protocol — When ALL Fallbacks Fail (MANDATORY)

If your primary tool fails AND the fallback also fails (or no fallback exists), you MUST immediately call `response` with a structured BLOCKED report. **NEVER retry a failed approach a 3rd time.** NEVER retry a tool that returned a PROFILE_ENFORCEMENT block — it is permanent for your profile.

**Rules:**
1. **1st failure** → Try the fallback from the Fallback Map above
2. **Fallback also fails OR PROFILE_ENFORCEMENT block** → Call `response` with BLOCKED format immediately
3. **NEVER** rephrase the same tool call hoping for a different result
4. **NEVER** retry a tool that was blocked by profile enforcement — it's architecturally forbidden for your role

**Mandatory BLOCKED response format** (include this in your `response` tool_args.text):
```
BLOCKED: [1-line summary of what you can't do]
- blocked_tool: [exact tool name that was blocked/failed]
- error: [exact error message you received]
- alternatives_tried: [tool1 → result, tool2 → result]
- remaining_work: [what's left undone — be specific]
- suggested_profile: [profile that can handle this, from the Swarm Roster]
```

**Example:**
```
BLOCKED: Cannot search the web — search tools not available.
- blocked_tool: perplexity_ask
- error: PROFILE_ENFORCEMENT — tool not in my profile
- alternatives_tried: [tavily_search → also blocked]
- remaining_work: Need API documentation for Stripe webhooks
- suggested_profile: researcher (has web_search category)
```


### Verification

| Tool | Purpose | Use When |
|------|---------|----------|
| `examine` | Vet and ground sources | Verifying facts, checking citations, auditing claims against real evidence. Use `[N]` inline notation. |

---

### Universal Decision Tree

```
What do you need to do?
│
├─ DISCOVER what files/deliverables exist?
│  → read_deliverables (list all saved artifacts)
│  → read_file (read a specific file by path)
│
├─ SAVE knowledge for later?
│  → memory_save
│
├─ RECALL saved knowledge?
│  → memory_load
│
├─ LOOK UP framework/library docs?
│  → docs_lookup (NOT context7 directly)
│  ⚠️ ALWAYS before writing config files
│
├─ VERIFY facts or sources?
│  → examine
│
├─ WORK OUTSIDE YOUR EXPERTISE?
│  → response (report back to orchestrator — only orchestrators can delegate)
│
└─ DELIVER final results?
   → response
```
