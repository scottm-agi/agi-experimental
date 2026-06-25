# Code Self-Check Tool

Run structural quality checks on the current project **before completing your task**.

## When to Use
- Before sending your final `response` tool call
- After completing all code changes to verify structural integrity
- When the orchestrator reports integration check failures

## Arguments
- `path` (optional): Project directory path. Defaults to `project_dir` from agent data.

## What It Checks
Runs the **same raw validators** used by the orchestrator completion gate:
- **fetch_route**: All `fetch('/api/...')` calls have matching route handlers
- **boilerplate**: Entry files don't contain scaffold boilerplate
- **nav_link**: All navigation `href` links have corresponding page files
- **schema_route**: All Prisma model references exist in `schema.prisma`

## Output
Returns `✅ Self-check PASSED` if all checks pass, or `⛔ Self-check: N failure(s)` with details.
