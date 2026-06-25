# create_skill

Create a new structured, reusable "Skill" to encapsulate repeatable processes, methodologies, or specialized toolsets. This tool generates a modular skill directory following the project's 3-level progressive disclosure architecture.

## When to use
- **Repetitive Workflows**: When you identify a specific sequence of steps (e.g., a custom deployment flow, a specific security audit pattern) that is likely to be reused in future tasks.
- **Specialized Knowledge**: When you've gathered deep domain knowledge or refined a complex prompt that should be persisted as a "Standard Operating Procedure."
- **Team Standards**: When building new protocols (like TDD cycles or code review standards) that all agents in the swarm should follow.

## Arguments
- `skill_name` (string, mandatory): A unique, kebab-case identifier for the skill (e.g., `custom-security-audit`).
- `description` (string, mandatory): A concise (1-2 sentence) description of what the skill does. Used in Level 1 Metadata for fast discovery.
- `instructions` (string, mandatory): The detailed core logic of the skill. This will be formatted into the `SKILL.md` body (Level 2). Be specific about steps, and include examples if possible.
- `modes` (array of strings, optional): A list of agent modes this skill should be associated with (e.g., `["code", "architect"]`).
- `is_global` (boolean, optional): If `true`, the skill is saved to the global skills directory (~/.config/agix/skills). If `false` (default), it is saved to the current project's `.roo/skills/`.

## Best Practices
- **Atomic Design**: Keep skills focused on a single responsibility.
- **3-Level Disclosure**: The tool automatically handles Level 1 (Metadata) and Level 2 (Instructions). For Level 3 (Resources), you can manually add files to the skill's `resources/` directory if needed.
- **Unambiguous Steps**: Use a numbered list for the main flow in your `instructions`.

## Example
```json
{
  "tool_name": "create_skill",
  "tool_args": {
    "skill_name": "fast-api-endpoint",
    "description": "Guides the agent through creating a standard FastAPI endpoint with Pydantic models and unit tests.",
    "instructions": "1. Define the Pydantic request/response models.\n2. Create the endpoint in `api/routes.py`.\n3. Implement the business logic in the service layer.\n4. Write a unit test in `tests/` verifying the endpoint status code.",
    "modes": ["code"]
  }
}
```
