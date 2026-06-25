# Full-Stack Web Development — Swarm Supplement

These rules apply to ALL subordinate agents when working on a fullstack-dev skill task.
They are injected alongside `_swarm_instructions.md` when the fullstack-dev skill is active.

---

### 🔴 TAILWIND CSS v3 ONLY — v4 IS BANNED (Overrides ALL Research)

**EVEN IF your research says "Tailwind v4 is the 2026 standard" — DO NOT USE IT.**
Tailwind v4 uses `@import "tailwindcss"` and `@theme {}` syntax that is INCOMPATIBLE with v3 utility classes and ShadCN components. Every time we use v4, the entire app renders as unstyled HTML.

**Mandatory post-scaffold steps** (do this IMMEDIATELY after `create-next-app`):
```bash
# 1. Check what version was installed
grep tailwindcss package.json
# 2. If it shows "^4" — DOWNGRADE:
npm uninstall tailwindcss
npm install -D tailwindcss@3 postcss autoprefixer
npx tailwindcss init -p
# 3. Fix globals.css — REPLACE @import "tailwindcss" with:
#    @tailwind base;
#    @tailwind components;
#    @tailwind utilities;
# 4. Delete any @theme {} blocks from globals.css
# 5. Verify: npm run dev → check browser → Tailwind classes must render
```
**If the page looks like raw unstyled HTML → the Tailwind version is wrong. Fix it before writing ANY components.**

---

### 🔴 CONFIG DEDUP CHECK (MANDATORY after scaffold or config changes)

After ANY scaffold command or config file modification, check for conflicting dual-extension configs:

```bash
# Check for JS/TS config conflicts — JS takes precedence and may have empty/wrong content
for cfg in tailwind postcss next vitest jest; do
  js_file=$(find . -maxdepth 1 -name "${cfg}.config.js" 2>/dev/null)
  ts_file=$(find . -maxdepth 1 -name "${cfg}.config.ts" -o -name "${cfg}.config.mjs" 2>/dev/null)
  if [ -n "$js_file" ] && [ -n "$ts_file" ]; then
    echo "⚠️ CONFLICT: $js_file AND $ts_file both exist — delete $js_file"
    rm "$js_file"
  fi
done
```

**Rule**: When both `.js` and `.ts` (or `.mjs`) versions exist, **always keep TypeScript/ESM and delete JS**. JavaScript configs take precedence but scaffolders often create empty `.js` stubs that shadow the real `.ts` config.

**WHY**: When both `.js` and `.ts` config versions exist, the `.js` version takes precedence. If the `.js` stub has empty values (e.g., `content: []`), it silently overrides the real `.ts` config, causing all framework features to break in production.

---

### Node.js Project Protocol (🔴 CRITICAL — MANDATORY for ALL Framework Projects)

- **USE `code_execution_tool`**: For ALL Node.js project scaffolding, use `code_execution_tool` to run the scaffold command directly (e.g., `npx create-next-app@15.1.0 . --typescript --tailwind --eslint --app --use-npm`). Always pin exact versions.
- **NEVER use @latest**: Always specify an exact version number (e.g., `@15.1.0`, NOT `@latest`).
- **EXECUTE IN ORDER**: Run scaffold commands in order, verifying each step completes before proceeding.
- **VALIDATE FIRST**: If researcher provided stable version findings, verify versions match before scaffolding.
- **INSTALL SAFELY**: Use `code_execution_tool` to run `npm install --legacy-peer-deps` to handle peer compatibility.
- **POST-SCAFFOLD CLEANUP**: After scaffold commands complete, always:
  1. Remove default boilerplate content
  2. **CHANGE** `package.json` `name` and `description` to match the project
  3. Run `npm run build` to confirm clean scaffold
- **DIAGNOSE ON FAILURE**: Check build output errors and consult `docs/framework-research.md` before retrying.
- **WHY**: Ad-hoc `npx create-next-app@latest` pulls bleeding-edge versions causing React 19 / Tailwind 4 conflicts.

---

### 🔴 DEPENDENCY-FIRST WORKFLOW (CRITICAL — Prevents Repetition Cascades)

Your **FIRST action before writing ANY component/page file** must be:
1. **List ALL packages** your implementation needs (icons, UI libs, date utils, etc.)
2. **Run `npm install <pkg1> <pkg2> ...`** in a SINGLE command
3. **Verify**: `cat package.json | grep <pkg>` for each
4. **ONLY THEN** begin writing source files with import statements

- **WHY**: Writing imports before installing causes module resolution errors → the model retries the same write → repetition loop → context saturation → agent death. Installing first eliminates the cascade at source.
- **Pattern**: Plan imports → `npm install` → write files. NOT: write files → discover missing → install → rewrite.

---

### 🔴 MODULAR CODE ARCHITECTURE (CRITICAL — Prevents Truncation & Improves Maintainability)

**Every code file MUST be under 500 lines.** No exceptions. If a file approaches 500 lines, split it into separate modules BEFORE writing.

**How to structure modular code:**
- `lib/` — Business logic modules (e.g., `lib/discovery.ts`, `lib/outreach.ts`, `lib/email-service.ts`)
- `components/` — UI components, one per file (e.g., `components/Dashboard.tsx`, `components/ReviewForm.tsx`)
- `utils/` — Shared utilities (e.g., `utils/api-helpers.ts`, `utils/validation.ts`)
- `types/` — TypeScript type definitions (e.g., `types/index.ts`, `types/api.ts`)
- `app/api/` — API route handlers, one per endpoint

**WHY**: Monolithic 1000+ line files:
1. Hit the LLM output token limit → truncation → lost code → agent wastes iterations recovering
2. Make future edits harder — the code agent must re-read the entire file to modify one function
3. Break separation of concerns — bugs cascade across unrelated features

**This rule applies ONLY to code files** (*.ts, *.tsx, *.js, *.jsx, *.py, *.css). Documentation files (*.md, docs/*, *.json specs) can be any length — 5000+ lines is fine for docs.

---

### Web-Specific CSS Pipeline Setup

**Before writing ANY page code** (MANDATORY — do these IN ORDER):
1. **Check the CSS framework docs** — use `docs_lookup` with the CSS framework name to get current documentation
2. **Set up the build pipeline** — PostCSS, bundler config, CSS framework config
3. **Install UI components before importing** — if using a component library, add/install first
4. **Verify build tool config consistency** — PostCSS, bundler, CSS framework must be compatible
5. **Run the dev server** — Use the `services_mgt` tool (`action: start_service`, `command: "npm run dev"`) to start the dev server. It must produce zero build errors BEFORE writing page code. If unstyled → CSS pipeline is broken. 🔴 NEVER run `npm run dev` directly — always use `services_mgt` for port allocation and lifecycle management.

### Verification Sitemap Generation (MANDATORY for UI Projects)

After implementing ALL frontend pages, generate a `verification_sitemap.json` in the project root listing every frontend route and API endpoint with expected HTTP status codes.

### Coverage Metric Definition

- "90% test coverage" means **90%+ line coverage** as reported by the test runner's `--coverage` flag
- NOT a file-count ratio
- Ensure tests generate `coverage/coverage-summary.json`

### Dev Server Binding (Docker)

- ALL dev servers MUST bind to `0.0.0.0`, NOT `localhost` or `127.0.0.1`
- Use `--host 0.0.0.0` for Vite, `HOST=0.0.0.0` for Next.js, etc.

### 🔴 Pre-Response Integration Check (React/Next.js — MANDATORY)
Before calling `response`, delegate to `code` agent:
"Run `grep -r 'fetch(' src/app --include='*.tsx' -l` and `grep -r \"'use client'\" src/app --include='*.tsx' -l`. If EITHER returns zero results, frontend-backend integration is incomplete — Phase 4 must be re-executed."
- **Frontend pages MUST contain `fetch()` calls** to backend API routes (NOT hardcoded mock data)
- **Interactive frontend pages MUST contain `'use client'`** directive (React Server Components cannot have onClick handlers)

---

### 🔴 E2E Agent Role — Mandatory Verification Delegation (CRITICAL)

The `e2e` agent is the **independent verifier** for all full-stack projects. It MUST be delegated to before the orchestrator calls `response`. The completion gate (Layer 3) will BLOCK the response if the `e2e` profile has not been delegated to.

**E2E agent responsibilities:**
1. **Build verification**: Run `npm run build` and verify zero errors
2. **Dev server start**: Start the dev server via `services_mgt` tool (never raw `npm run dev`)
3. **Test suite execution**: Run ALL test suites (`npm test`, `pytest`, BDD/Cucumber, Playwright)
4. **Browser smoke test**: Visit all routes via `browser_agent`, take screenshots, check for console errors
5. **API verification**: `curl` all API endpoints and verify responses return real data (not mock)
6. **Aggregate reporting**: Report pass/fail per suite with specific failing tests listed

**When to delegate to e2e:**
- After ALL Phase 3 (implementation) and Phase 4 (integration) work is complete
- After the build-freeze gate (Phase 4.9) passes
- BEFORE calling `response` to deliver the project

**This is NOT optional.** Every full-stack project MUST have at least one `call_subordinate` with `profile="e2e"`. The completion gate, supervisor, and skill checklist all enforce this requirement across 5 independent layers.

---

### 🔴 PROHIBITED COMMANDS (CRITICAL — NEVER Execute These)

These commands have caused production incidents. Each has a safe alternative:

| ❌ NEVER DO THIS | ✅ DO THIS INSTEAD | Why |
|---|---|---|
| `npm run dev` / `next dev` / `vite` | `services_mgt` tool (`action: start_service`) | Port allocation, health checks, lifecycle management |
| `chmod -R 644 <dir>` | `find <dir> -type f -exec chmod 644 {} +` + `find <dir> -type d -exec chmod 755 {} +` | Preserves directory execute bits |
| `cat -n <file>` / `cat --number <file>` | `read_file` tool | Prevents line-number corruption |
| `npx create-next-app@latest` / `npx create-vite@latest` | `code_execution_tool` with pinned version (e.g., `npx create-next-app@15.1.0 .`) | Version pinning, safe non-empty directory handling |
