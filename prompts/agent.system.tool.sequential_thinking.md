# Tool: sequential_thinking

Use the `sequential_thinking` tool for complex tasks that require structured, step-by-step reasoning.

## When to Use
- Multi-file changes or architectural planning
- Debugging complex issues with multiple variables
- Breaking down ambiguous requirements into actionable steps
- Any task requiring more than 3 logical steps

## Usage Guidelines
- Start with an initial estimate of needed thoughts (`totalThoughts`)
- Each thought should build on, question, or revise previous insights
- Adjust `totalThoughts` as understanding deepens
- Use `isRevision: true` to reconsider earlier conclusions
- Use `branchFromThought` to explore alternative approaches

## Best Practices
- Dedicate at least one thought to First Principles deconstruction
- Generate a hypothesis, then verify it before proceeding
- Express uncertainty when present — don't guess
- Only set `nextThoughtNeeded: false` when truly satisfied with the answer
