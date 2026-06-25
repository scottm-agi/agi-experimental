# build_kb — Build & Deployment Knowledge Base

Query the curated build knowledge base for Prisma ORM, build workflows, deployment, and integration patterns.

Use this tool when you encounter build failures, ORM errors, deployment issues, or need pre-build checklists. It provides canonical solutions distilled from:
- Prisma ORM documentation (v5-v7)
- Next.js 15 production guides
- Railway/Vercel/Docker deployment patterns
- AGIX smoke test RCA history

## Parameters

- **category** (optional): Specific KB category to query. One of:
  - `prisma_patterns` — Singleton setup, Prisma 7 migration, connection pooling, Docker config
  - `build_workflow` — Pre-build checklist, tsc --noEmit, next typegen, memory management
  - `deployment_patterns` — Railway/Vercel/Docker deployment, env vars, health checks
  - `integration_patterns` — Route Handlers, Server Actions, auth, database connections
- **query** (optional): Free-text search to find matching sections within KB files

## When to Use

- When a build error mentions Prisma, database connection, or ORM issues
- When planning a build pipeline or pre-build checks
- When deploying to Railway, Vercel, or Docker
- When implementing API routes, Server Actions, or auth flows
- When encountering Prisma 7 breaking changes (adapter-required, output-required)
- **Before starting a build** — check `build_workflow` for the pre-build checklist

## When to Use build_kb vs frontend_kb

| Issue Type | Use |
|-----------|-----|
| Prisma/ORM errors | `build_kb(category="prisma_patterns")` |
| Build failures, tsc errors | `build_kb(category="build_workflow")` |
| Deployment issues | `build_kb(category="deployment_patterns")` |
| API routes, Server Actions | `build_kb(category="integration_patterns")` |
| React hooks, component typing | `frontend_kb(category="react_typescript")` |
| Hydration, use client errors | `frontend_kb(category="nextjs_app_router")` |
| CSS, Tailwind, design systems | `frontend_kb(category="css_design_systems")` |

## Examples

```
# Get Prisma singleton and v7 migration patterns
build_kb(category="prisma_patterns")

# Search for connection pooling solutions
build_kb(query="connection pool serverless")

# Get the pre-build checklist
build_kb(category="build_workflow")

# Search for Docker deployment patterns
build_kb(query="standalone dockerfile next.js")
```
