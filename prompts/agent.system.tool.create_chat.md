# create_chat

Create a new standalone chat session. This allows you to branch out your work into a new, clean conversation while maintaining a logical link to the current session.

## When to use
- **Work Branching**: When a task becomes large or complex enough to warrant a dedicated clean slate.
- **Pivoting**: When you need to investigate a side quest without cluttering the main mission.
- **Parallel Work**: When you want to start a new stream of work that can be viewed or managed independently.
- **Context Management**: When the current chat context is getting too full or noisy.

## Arguments
- `name` (string, optional): A descriptive title for the new chat session.
- `project_name` (string, optional): The name of the project to associate the new chat with. Highly recommended for workspace organization.
- `reason` (string, mandatory): A clear explanation of why this chat is being branched now.
- `mission` (string, mandatory): A concise "North Star" mission statement for the new agent session.
- `initial_message` (string, mandatory): The "Ideal Prompt" used to jumpstart the new session.

> [!IMPORTANT]
> **Safety Limit**: You are allowed a maximum of **10** branched chats per parent context. Attempting to exceed this limit will result in a tool error.

## The Ideal Prompt (Mandatory Structure)
When branching, your `initial_message` MUST be a high-quality "Handover Document" that includes:
1. **Context Summary**: A bulleted summary of all relevant findings, code state, and decisions from the parent chat.
2. **Project Scope**: Briefly mention the relevant project/workspace boundaries.
3. **Branching Rationale**: Why is this work being moved to a new chat?
4. **Mission Goal**: A single, unambiguous objective for the new chat to achieve.

### Example
```json
{
  "tool_name": "create_chat",
  "tool_args": {
    "name": "Refactoring: Database Migrations",
    "project_name": "backend-core",
    "reason": "Separating structural database changes from the business logic discussion for clarity.",
    "mission": "Design and implement the migration scripts for the new UserSchema while maintaining backward compatibility.",
    "initial_message": "# Mission: Database Schema Migration\n\n## Context\nWe have identified a bottleneck in the current User model. The parent chat (ID: [ctxid]) has finalized the new field requirements: [list of fields].\n\n## Rationale\nMoving this to a clean session to prevent LLM context saturation during complex SQL generation.\n\n## Task\n1. Review the existing schema in `db/schema.py`.\n2. Generate migration scripts using Alembic.\n3. Verify compatibility with the current API."
  }
}
```

## Best Practices
- **Act as an Architect**: When you create a child chat, you are the architect. Provide perfect instructions so the next agent doesn't have to ask questions.
- **Explicit Constraints**: Include any critical constraints (e.g., "Don't touch the auth layer", "Use Python 3.12 syntax") in the `initial_message`.
- **Reference Parent**: Mention the parent context ID if the agent needs to refer back to shared project files.
