---
name: fullstack-conventions
description: >
  Fullstack development conventions and best practices for web application
  projects. Covers dependency management, npm peer resolution, Prisma ORM,
  scaffolding tools, git workflows, and web project quality standards.
  Load this skill when building or modifying web applications.
---

# Fullstack Development Conventions

This skill provides development conventions for fullstack web application projects.
It is loaded on-demand to reduce base context overhead for non-web tasks.

## Dependency Management

When installing or configuring frameworks, libraries, ORMs, build tools, or runtime dependencies:

1. **Stable Version Policy**: Always prefer the LTS (Long-Term Support) or second-latest stable release — NOT `@latest`. Bleeding-edge major versions often have breaking changes, incomplete migration documentation, and sparse community support. This applies universally to web frameworks, ORMs, compilers, bundlers, and runtime environments.
2. **NEVER use `@latest`**: Always specify an exact version number. Example: `npm install prisma@6.19.2` NOT `npm install prisma@latest`. Use `context7` MCP (`resolve-library-id` → `query-docs`) to find the current stable version BEFORE installing.
3. **Pin Exact Versions**: Always pin exact dependency versions in lock files (package-lock.json, poetry.lock, Gemfile.lock, etc.) for reproducible builds. Never rely on floating ranges for core dependencies.
4. **Error Recovery — Downgrade Rule**: If a dependency configuration or initialization error persists after 3 fix attempts with the same version, try **downgrading to the previous stable major/minor version** before continuing to debug. Framework-specific configuration syntax often changes between major versions — using the version your training data covers is more reliable than debugging undocumented new APIs.
5. **Documentation-First**: Before writing any configuration for a framework, use `context7` MCP to fetch its current docs. NEVER configure from memory — version-specific config changes cause 80% of setup failures.
6. **Version Compatibility Check (CRITICAL)**: Before installing ANY framework pairing (e.g., Prisma + Next.js, TypeORM + NestJS, SQLAlchemy + FastAPI), you MUST check version compatibility FIRST. Use `context7` MCP (`resolve-library-id` → `query-docs`) to look up both packages, OR use `perplexity` / `search_engine` to query "compatible versions of [framework A] with [framework B]". Example: "What version of Prisma is compatible with Next.js 16.x?" — install failures from mismatched major versions cause 40%+ of scaffolding problems.

### Fallback Default Versions (when no architect plan or dynamically resolved versions are available)

If the architect plan did not provide specific versions, use these known-stable defaults as a fallback. **Always prefer dynamically resolved versions over these defaults.**

```bash
# Prisma — Prisma 7 is BANNED (incompatible config model)
npm install prisma@5.22.0 @prisma/client@5.22.0

# React — React 19 is BANNED
npm install react@18.3.1 react-dom@18.3.1

# Tailwind CSS — Tailwind v4 is BANNED (different CSS syntax)
npm install -D tailwindcss@3.4.17 postcss@8.4.49 autoprefixer@10.4.20

# Next.js
npx create-next-app@14.2.15
```

> **Maintainability note**: Update these defaults whenever a new stable version is validated. These are safe fallbacks, not mandates — the researcher should resolve current versions for new projects.
## npm Peer Dependency Resolution (MANDATORY)

When `npm install` fails with `ERESOLVE` or any peer dependency conflict:

1. **Immediate retry**: Re-run with `npm install --legacy-peer-deps`. This resolves 90%+ of peer dep conflicts.
2. **React 19 RC downgrade**: If the project was scaffolded with React 19 RC (common with `create-next-app@15`), downgrade BEFORE installing additional packages:
   ```bash
   npm install react@18 react-dom@18 @types/react@18 @types/react-dom@18 --save --legacy-peer-deps
   ```
3. **All subsequent installs**: After ANY peer dep failure, ALWAYS append `--legacy-peer-deps` to all future `npm install` commands in that project.
4. **NEVER abandon a project due to peer dep errors** — `--legacy-peer-deps` almost always resolves them. These are version negotiation issues, not real incompatibilities.

## Prisma ORM Setup (MANDATORY)

When using Prisma as an ORM, you MUST complete BOTH steps — `prisma generate` alone is NOT enough:

1. **`npx prisma generate`** — Creates the TypeScript/JS client from your schema. This does NOT create the database.
2. **`npx prisma db push`** — Creates/syncs the actual database tables from your schema. Without this, ALL Prisma queries will fail at runtime.

**Common mistake**: Running only `prisma generate` and forgetting `prisma db push`. This causes "table does not exist" or "Environment variable not found: DATABASE_URL" errors at runtime. ALWAYS run both:
```bash
npx prisma generate && npx prisma db push
```

For SQLite in dev environments, ensure your `.env` contains `DATABASE_URL="file:./dev.db"` and your `schema.prisma` has `provider = "sqlite"`.

## Scaffolding Tools (Non-Interactive Mode)

When using scaffolding tools like `create-next-app`, `create-vite`, `create-react-app`, etc.:

1. **ALWAYS use non-interactive mode**. These tools prompt for user input which will hang in automated environments.
2. **For `create-next-app`**: Use `npx --yes create-next-app@VERSION . --typescript --tailwind --eslint --app --src-dir --import-alias '@/*' --use-npm --skip-install` — specify ALL flags explicitly so no prompts appear.
3. **Never rely on interactive prompts** — if a tool asks questions, it will hang forever in an agent container.
4. **If a tool hangs**: Kill it and retry with explicit flags or pipe `printf` to provide answers automatically.
5. **IMMEDIATELY after scaffolding**, create a `.npmrc` file in the project root to prevent peer dependency conflicts and ensure deterministic builds:
   ```bash
   printf "legacy-peer-deps=true\nsave-exact=true\n" > .npmrc
   ```
   This MUST be done BEFORE running `npm install`. The `legacy-peer-deps` flag prevents React 18/19 peer dep conflicts. The `save-exact` flag ensures deterministic resolution.
   > ⚠️ DO NOT add `install-strategy=nested` — it breaks framework dependency hoisting (e.g., @swc/helpers for Next.js). See Iteration 158 RCA.

## Git / Version Control Workflow (MANDATORY)

When working with git in project directories, you MUST follow this exact order:

1. **ALWAYS `git init` FIRST** before running ANY other git commands (`checkout`, `branch`, `add`, `commit`, `push`). Without `git init`, the project directory has no `.git` isolation, and security guards will HARD BLOCK destructive commands like `checkout -b`.
2. **For repository creation**: Use the `github.create_repository` MCP tool (NOT `gh` CLI). The `gh` CLI is not installed in the container. Example: call the MCP tool `github.create_repository` with `name`, `private`, and `org` arguments.
3. **MCP GitHub Fallback (CRITICAL)**: If the `github.create_repository` MCP tool fails (connection error, auth error, "name already exists", etc.), **DO NOT retry it**. Instead, use these fallback methods in order:
   - **Fallback A — GitHub REST API via code_execution_tool**: Create the repo via `curl` to `https://api.github.com/orgs/{org}/repos`:
     ```bash
     curl -s -X POST https://api.github.com/orgs/AGIXSpace/repos \
       -H "Authorization: Bearer $GITHUB_TOKEN" \
       -H "Accept: application/vnd.github+json" \
       -d '{"name":"repo-name","private":false}'
     ```
   - **Fallback B — git push to new repo**: If the repo already exists (e.g., "name already exists" error), skip creation entirely. Just `git init` → `git remote add origin` → `git push -u origin main`.
   - **NEVER retry a failed MCP tool more than once** — use the fallback instead.
4. **For pushing code**: After `git init`, use `code_execution_tool` with a Python script:
   ```python
   import subprocess
   # Set remote with token
   subprocess.run(["git", "remote", "add", "origin", "https://x-access-token:<GITHUB_TOKEN>@github.com/org/repo.git"], cwd="/path/to/project")
   subprocess.run(["git", "push", "-u", "origin", "main"], cwd="/path/to/project")
   ```
5. **Correct command sequence**: `git init` → `git branch -m main` → `git add -A` → `git commit -m "..."` → set remote → `git push`
6. **Never use `gh` CLI** commands (`gh repo create`, `gh pr create`, etc.) — they are not available. Use GitHub MCP tools instead, with the fallback methods above if they fail.

### Git Push Lifecycle Rule (CRITICAL)

**Git push MUST be the LAST step** in the development lifecycle — only after:
1. ✅ All source code is written and quality-checked
2. ✅ Dev server is running and routes are reachable
3. ✅ All quality gates pass (contract assertions, boilerplate, env vars, form routes)
4. ✅ Browser UAT confirms visual fidelity

**NEVER push before quality validation.** The git command sequence above is the _mechanical order_ of git commands. The _lifecycle order_ is:

**Code → Validate → Test → Gate → Push → Complete**

If you push before gates pass and then discover issues, the remote repository will contain broken code. Always validate first.

## Web Project Quality (Proactive Guidance)

When building web applications, proactively address these quality areas:

1. **Visual Assets**: Use `gen_image` to create custom hero images, product screenshots, and branded graphics. Never ship a project with only default scaffold images (vercel.svg, next.svg, etc.).
2. **Scaffold Cleanup**: After scaffolding with `create-next-app`, `create-vite`, etc., delete default files: `vercel.svg`, `next.svg`, `globe.svg`, `window.svg`, `file.svg`. Replace with project-specific assets.
3. **README Documentation**: Generate a comprehensive `README.md` (200+ chars) covering: project overview, setup instructions, tech stack, folder structure, and deployment steps.
4. **Content Depth**: Landing pages should have real, substantive content — not thin stubs. Include multiple sections, real copy (not lorem ipsum), and interactive elements.
5. **Placeholder Removal**: Replace all placeholder text ("Lorem ipsum", "Your title here", "Description goes here") with real project-specific content before delivery.

## Zero-Delay UX (WEB/API AGENTS — MANDATORY)
When building user-facing interfaces:
1. **NEVER block the user** with a loading spinner while a backend process runs (provisioning, external API sync, data migration).
2. **Use async patterns** — queue the background work, immediately reflect pending state in the UI (e.g., "Processing...", "Pending"), and update via polling or webhooks when complete.
3. **Optimistic UI** — show the expected result immediately and reconcile if the backend disagrees.

**Rule**: If a user must stare at a spinner for >2 seconds during a flow they could otherwise continue, the UX is broken. Move long-running work to background queues.
