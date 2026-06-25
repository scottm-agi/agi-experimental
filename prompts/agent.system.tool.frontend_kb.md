# frontend_kb — Frontend Knowledge Base

Query the curated frontend knowledge base for React, TypeScript, Next.js, and CSS patterns.

Use this tool when you encounter build errors, type conflicts, or framework-specific issues. It provides canonical solutions distilled from:
- `typescript-cheatsheets/react` (47K⭐)
- `awesome-cursorrules` (39K⭐)
- AGIX smoke test history

## Parameters

- **category** (optional): Specific KB category to query. One of:
  - `react_typescript` — Props typing, hooks, refs, events, generics, context
  - `nextjs_app_router` — Server/client components, layouts, metadata, routing
  - `css_design_systems` — Design tokens, Tailwind, responsive, animations
  - `common_pitfalls` — HTML attribute conflicts, hydration, null safety, imports
- **query** (optional): Free-text search to find matching sections within KB files

## When to Use

- When a build error mentions type conflicts, missing properties, or hydration issues
- When unsure about correct typing for React hooks, events, or refs
- When working with Next.js App Router and encountering server/client component issues
- When setting up CSS design systems or Tailwind configuration

## Examples

```
# Get all React+TypeScript patterns
frontend_kb(category="react_typescript")

# Search for HTML attribute conflict solutions
frontend_kb(query="Omit HTML attribute conflict")

# Get Next.js App Router patterns
frontend_kb(category="nextjs_app_router")

# Search for hydration error fixes
frontend_kb(query="hydration mismatch")
```
