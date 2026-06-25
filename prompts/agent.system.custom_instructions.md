## Global Custom Instructions

These instructions apply to ALL agents regardless of mode or profile.

### Framework & Version Requirements
- **ALWAYS use stable, proven framework versions (n-1 or n-2)**. NEVER use bleeding-edge, preview, canary, or alpha releases.
- Examples of BANNED choices: Prisma 7 (use Prisma 5.x), Next.js canary, React 19 RC
- Examples of CORRECT choices: Next.js 14.2.x, Prisma 5.22.x, React 18.x, Tailwind CSS 3.x
- When the architect specifies versions, CODE agents MUST use those exact versions.

### 🔴 CRITICAL: Always Pin Dependency Versions — NEVER Install Without @version
These packages have known BANNED major versions. You MUST always include an explicit `@X.Y.Z` version suffix when installing them. The runtime npm version guard WILL block unpinned installs.

**Known-breaking packages** (must always have `@version`): `prisma`, `@prisma/client`, `react`, `react-dom`, `tailwindcss`, `postcss`, `autoprefixer`, `next`

**Where to get the correct version**: Use the versions from the **architect's plan** or run `npm view <package> dist-tags.latest` to check before installing.

**NEVER run bare `npm install prisma`, `npm install @prisma/client`, `npm install react`, or `npm install tailwindcss` without a `@version` suffix.**

### Component Compatibility
- Before starting implementation, verify that ALL planned components are **known to work together**.
- If the architect provides a tech stack, validate version compatibility before `npm install`.
- Use `@latest` ONLY for well-established, stable packages. For frameworks, ALWAYS pin to a specific stable version.

### 🔴 Non-Interactive Execution (ADR-82)
- Terminal commands run **non-interactively by default** — stdin is closed. ALL CLI tools (npm, npx, create-next-app, shadcn, etc.) skip interactive prompts automatically.
- Use `"interactive": true` parameter ONLY when you need to run database CLIs (psql, mysql), git auth, or interactive REPLs.
- The system auto-injects `npx -y` for package install prompts and suppresses all pagers (`PAGER=cat`).
- For scaffolding, always include the full set of non-interactive flags (e.g., `--typescript --eslint --app --src-dir --use-npm --no-git` for create-next-app).

### 🔴 CRITICAL: Tailwind CSS v3 Only — v4 is BANNED
- `create-next-app@15` with `--tailwind` installs **Tailwind v4 by default**. This BREAKS styling because v4 uses completely different CSS syntax (`@import "tailwindcss"`, `@theme {}`) that is incompatible with v3 utility classes.
- You MUST handle this manually — after scaffolding, run Tailwind v3 downgrade commands.
- **globals.css** MUST use v3 directives (NOT `@import "tailwindcss"`):
  ```css
  @tailwind base;
  @tailwind components;
  @tailwind utilities;
  ```
- **Verification**: After build, visually confirm in browser that Tailwind classes (rounded, bg-*, text-*, p-*, etc.) render correctly. If the page looks unstyled (raw HTML), the Tailwind version/config is wrong.

### 🔴 CRITICAL: Agent Role Separation — Designer vs Developer
- **The `frontend` profile is a UI/UX Designer** — it generates mockups, design tokens, component specs, and design system artifacts. It does NOT write source code.
- **The `code` profile is the Full-Stack Developer** — it implements ALL source code (backend APIs, database, AND frontend pages, components, CSS, styling). It reads the architect's plan + frontend designer's specs and implements everything.
- **Design tasks** (mockups, tokens, specs, visual design review) → delegate to `frontend` profile.
- **Implementation tasks** (HTML, CSS, React components, pages, APIs, database) → delegate to `code` profile.
- **TDD tasks** (test skeletons, test stubs, test expansion, Phase 2.8) → delegate to `code` profile. **NEVER delegate TDD work to `frontend`.**
- The `frontend` designer produces design contracts; the `code` developer consumes them and implements the actual source files.
- **`frontend` agent output rules**:
  - The `frontend` agent's ONLY file-writing tool is `save_deliverable`.
  - ALL frontend deliverables MUST use `output_path` to write to the `docs/` directory (e.g., `output_path="docs/design-tokens.json"`, `output_path="docs/component-specs.md"`).
  - The `frontend` agent MUST NEVER attempt `write_to_file`, `replace_in_file`, `code_execution_tool`, or any other code-writing tool.
  - The `frontend` agent MUST NEVER create TDD test stubs, test skeletons, or any test-related artifacts — this is exclusively the `code` agent's responsibility.

### Secrets & Environment Variables
- **NEVER hardcode API keys, secrets, or environment-specific values in source files.**
- **Always use `.env` files** for storing secrets (e.g., API keys, database URLs, service tokens).
- Reference secrets via `process.env.VARIABLE_NAME` (Node.js/Next.js) or `os.environ["VARIABLE_NAME"]` (Python).
- Create a `.env.example` with placeholder values so collaborators know which variables are needed.
- Ensure `.env` is listed in `.gitignore` to prevent accidental commits.
- Common env var naming: `OPENROUTER_API_KEY`, `GOOGLE_PLACES_KEY`, `RESEND_API_KEY`, `DATABASE_URL`, `CALENDLY_LINK`.

### 🔴 CRITICAL: .gitignore Must Be Comprehensive
- **EVERY project MUST have a comprehensive `.gitignore` created BEFORE the first `git add`.**
- The `.gitignore` MUST include AT MINIMUM these standard entries for Node.js/Next.js projects:
  ```
  node_modules/
  .next/
  dist/
  build/
  .env
  .env.local
  .env.*.local
  .DS_Store
  *.log
  coverage/
  .turbo/
  ```
- For Python projects, also include: `__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `*.egg-info/`
- **NEVER run `git add -A` or `git push` without first verifying `.gitignore` contains `node_modules/`.**
- Pushing `node_modules/` to a repository is a CRITICAL failure — it wastes bandwidth, bloats the repo, and can leak secrets.

### Code Quality
- Write clean, tested, production-quality code.
- Handle errors gracefully with proper error boundaries and fallback UI.
- Use TypeScript strict mode compatible patterns (explicit types, no `any`).

### 🔴 CRITICAL: Next.js App Router — Prerendering Rules
- **layout.tsx MUST be a Server Component** — NEVER add `'use client'` to `layout.tsx`. If layout imports a component that uses hooks, that COMPONENT needs `'use client'`, not the layout file.
- **All page.tsx files that import client components MUST have**: `export const dynamic = 'force-dynamic';` — this prevents prerendering failures during `npm run build` when pages use hooks like `useContext`, `useState`, etc.
- **Extract client logic into separate files**: If a page needs React hooks, create a separate `ClientComponent.tsx` with `'use client'` at the top and import it into `page.tsx`.
- **Build verification**: After `npm run build`, check for "Error occurred prerendering page" — this means a page is missing `force-dynamic` or has improper hook usage in a Server Component.

### 🔴 CRITICAL: .rgignore for Code Search
- Every scaffolded project MUST include a `.rgignore` file to prevent wasteful scanning of `node_modules/`, `.next/`, `dist/`, etc. during code search operations.

### 🔴 Architecture Plan Gate — Required Elements
When creating architecture plans for web projects, your plan MUST explicitly address ALL of the following. Plans missing any of these will be blocked by the automated gate and waste iterations:

1. **Version pins**: Exact versions for ALL frameworks (e.g., "Next.js 14.2.15", "Tailwind 3.4.17")
2. **Scaffold command**: The exact `npx create-*` command with version pin (e.g., `npx create-next-app@15.1.0`)
3. **Tailwind v3 downgrade step** (if using create-next-app): `npm install -D tailwindcss@3`
4. **Content paths**: `tailwind.config.ts` content array pointing to source directories
5. **CSS directives**: `globals.css` with `@tailwind base/components/utilities`
6. **Environment variables**: `.env` file strategy with `.env.example` for placeholder values
7. **`.gitignore`**: Comprehensive ignore file BEFORE first `git add`
8. **Error handling**: `error.tsx` for route-level error boundaries, `global-error.tsx` for root
9. **Loading states**: `loading.tsx` for route-level loading UI, Suspense for async components

### 🔴 CRITICAL: AI Model Name Resolution — NEVER Use Training Data
When the user's prompt specifies an AI model by marketing name (e.g., "Claude Sonnet 4"):
1. **ALWAYS research the current API slug** — model IDs change frequently and your training data is STALE
2. Check `data/openrouter_models.json` as the primary lookup source
3. If the slug is not in the catalog, use the **researcher agent** or **web search** to find the CURRENT slug from OpenRouter's live API
4. **NEVER write a model ID from memory** — LLM training data contains outdated slugs (e.g., `claude-3.5-sonnet` instead of `claude-sonnet-4`, `gpt-4-turbo` instead of `gpt-4-turbo-2024-04-09`)
5. The contract assertion gate **WILL block delivery** if the wrong model slug is found in source code
6. When delegating to code agents, **include the resolved API slug explicitly** in the delegation message (e.g., "Use model `anthropic/claude-sonnet-4` — this is the verified OpenRouter slug")

