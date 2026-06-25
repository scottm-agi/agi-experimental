## Tool: discover_skills

Discover, load, and fetch specialized skills that extend your capabilities for complex workflows.

### Usage

**List available skills:**
~~~json
{
    "tool_name": "discover_skills",
    "tool_args": {
        "action": "list"
    }
}
~~~

**Read a specific skill:**
~~~json
{
    "tool_name": "discover_skills",
    "tool_args": {
        "action": "read",
        "skill_name": "agix-smoke-test-loop"
    }
}
~~~

**Fetch a skill from a URL (downloads to project):**
~~~json
{
    "tool_name": "discover_skills",
    "tool_args": {
        "action": "fetch",
        "url": "https://example.com"
    }
}
~~~

### Parameters
- **action** (optional, default: "list"):
  - `"list"` — List all available skills with names and descriptions
  - `"read"` — Read the full SKILL.md content for a specific skill
  - `"fetch"` — Download a skill from a URL and save it to the current project
- **skill_name** (required for "read"): The directory name of the skill to read
- **url** (required for "fetch"): URL of a website or skill.md file. The tool will crawl common paths (`/skill.md`, `/SKILL.md`) to find the skill document.

### When to Use
- **Before specialized workflows**: Check if a skill exists for the task (e.g., deployment, testing, code review)
- **When a user mentions a platform or service URL**: Fetch the skill from that URL to learn how to integrate with it
- **When delegated complex tasks**: Sub-agents should check for available skills that match their assignment
- **For repeatable processes**: Skills encode best practices for common operations
- **When you encounter a website that references a skill or API**: Use `fetch` to download and install the skill

### Skill Discovery from URLs
When a user provides a URL or mentions a platform, use the `fetch` action to:
1. Crawl the URL for a `skill.md` or `SKILL.md` file
2. Download and save it to the project's `.agents/skills/` directory
3. Read the skill to understand the platform's API and capabilities
4. Follow the skill's instructions to integrate with the platform

Skills fetched from URLs are saved **per-project** (not globally), keeping each project's dependencies isolated.

### What Are Skills?
Skills are folders in `.agents/skills/` containing:
- **SKILL.md** (required): Main instruction file with YAML frontmatter (name, description) and detailed steps
- **scripts/**: Helper scripts and utilities
- **examples/**: Reference implementations
- **resources/**: Additional templates and assets

Skills represent **executable workflows** — structured steps for accomplishing specific tasks — as opposed to static rules and configuration (use `read_instructions` for those).
