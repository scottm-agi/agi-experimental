# Development Swarm Instructions
# These instructions are automatically injected into all subordinates of multiagentdev.
# They establish baseline quality standards across the entire development agent "swarm".

## Mandatory Standards

### 🚨 CRITICAL: Escalation Protocol — [ESCALATE]
When you have attempted a task **2 or more times** and it continues to fail, you MUST NOT keep retrying the same approach. Instead, emit a structured escalation report so the orchestrator can route the problem to a `debug` agent for 5-Why root cause analysis.

**Format** — include this EXACTLY at the END of your response:
```
[ESCALATE]
## Escalation Report
**Task**: [What you were trying to do]
**Attempts**: [Number of attempts made]
**Root Blocker**: [The specific error/condition preventing success]
**What Worked**: [Any partial progress achieved]
**What Failed**: [Approaches tried that did not resolve the issue]
**Suggested Next Step**: [Your recommendation for a different approach]
```

**Rules**:
- After 2 failed attempts at the SAME error, you MUST escalate — do NOT retry a 3rd time.
- The orchestrator will delegate to a `debug` agent for RCA, then re-issue the work with a new approach.
- This is NOT a failure on your part — it is a signal that the problem needs deeper investigation.
- NEVER silently give up or report success when work is incomplete. Escalate instead.

### 1. Test-Driven Development (TDD)
- Write the failing test FIRST, before any implementation code
- Run the test and confirm it fails (red)
- Write the minimum code to make it pass (green)
- Run all tests — confirm no regressions
- Refactor if needed, re-run tests
- NEVER skip this order. If you find yourself writing code without a test, STOP.

### 1a. 🔴 MANDATORY: Frontend Designer Agent for UI/UX Design
- **The `frontend` profile is a UI/UX Designer — it generates mockups, design tokens, and component specs. It does NOT write source code.**
- **ALL source code — backend AND frontend (pages, components, CSS, styling) — is implemented by the `code` profile.**
- If a task is "build a web app", the design phase (mockups, tokens, specs) goes to `frontend`, and ALL implementation (backend + frontend pages/components/CSS) goes to `code`.
- **NEVER delegate source code writing to `frontend` — ALWAYS use `code` for implementation.**
- **If your task list contains ZERO `frontend` profile delegations for Phase 2.3 (design), you have FAILED. But if Phase 3 (implementation) delegates coding to `frontend`, that is ALSO a failure.**

### 2. Sequential Thinking
- Use the `sequential_thinking` tool for any multi-step implementation
- Break down the problem before writing code
- Adjust your plan as understanding deepens

### 3. Build System Verification (CRITICAL for Frontend)
After scaffolding ANY project or modifying build config:
- **Read `package.json`** — check exact versions of CSS framework, meta-framework, and component libraries. Major version changes (v3 → v4, v5 → v6) almost always require different config formats, directives, and file conventions.
- **Fetch latest docs BEFORE configuring anything** — ALWAYS use `docs_lookup` to fetch current documentation for EVERY framework, ORM, bundler, or CSS tool before writing any configuration. Also use `perplexity_ask` for current best practices. NEVER configure a framework from training-data memory alone — version-specific config changes cause 80% of setup failures.
- **Verify config matches installed version** — never assume config format from memory. Check the installed version first, then configure accordingly. If in doubt, check the framework's migration guide.
- **Install UI components before importing** — if using a component library, components must be added/installed before they can be referenced in code. Check the component directory.
- **Verify build tool config consistency** — ensure PostCSS, bundler, and CSS framework configs are compatible. Mismatched versions cause silent CSS generation failures.
- **Run the dev server** — `npm run dev` (or equivalent) must produce zero build errors BEFORE writing page code. If the UI looks unstyled (browser defaults, no colors, no rounded corners), the CSS pipeline is broken — go back to step 1.

### 3a-3b: Framework-Specific Build Rules
> **Moved to skill supplements.** When the `fullstack-dev` skill is active, web-specific build rules (Tailwind v3 enforcement, config dedup) are injected from `skills/fullstack-dev/swarm_supplement.md`. See your active skill supplement for details.

### 4. Tech Stack Fidelity
- If the user specified a tech stack (e.g., "Next.js on Railway"), use EXACTLY that stack
- Do NOT substitute languages without explicit user approval (e.g., don't write Python when user said TypeScript)
- Do NOT rationalize post-hoc ("Python is better for X") — follow the user's specification

### 5. No Premature Completion
- NEVER declare "DONE" or "verified" without actual verification
- For UI work: take a screenshot or use browser agent to verify visual quality
- A wireframe-looking output means the CSS framework is misconfigured — investigate the build config
- For API work: make a real HTTP request and verify the response
- For tests: actually run them and show the output

### 6. Project Scoping
- All code MUST live under a named project in `/agix/usr/projects/<name>/`
- Before creating a new project, list existing projects and check if one already matches the user's topic. Use fuzzy matching (e.g., `mainstreet-review` matches "mainstreet review")  
- Never create duplicate or near-duplicate projects — if a matching project exists, use it
- If no matching project exists, use `setup_project` tool to create one with an appropriate name

### 7. Background Commands
- Always run long-running commands (npm install, dev servers, builds) in the background
- Do NOT block on interactive prompts — use `--yes`, `--no-interactive`, or pipe `yes` as needed
- If a command stalls, kill it and retry with non-interactive flags

### 8. Memory Bank
- Read `memory-bank/` files at start of task if they exist
- Update `memory-bank/activeContext.md` and `progress.md` after significant changes
- Record novel fixes/patterns in `memories.md` (sparingly)

### 9. 🔴 Completeness Mandate (No Skeletons)
- Every file MUST contain real, functional implementation code — NOT stubs or placeholders
- NEVER leave `// TODO`, `# FIXME`, `/* placeholder */`, `pass`, or empty function bodies
- NEVER create empty component files with just an export statement
- If a feature requires logic you're unsure about, implement your best approximation — NEVER leave it blank
- A "skeleton" output (file structure with empty files) is a CRITICAL FAILURE
- **Verification**: After implementation, count source files. A full-stack app should have >10 source files minimum. If fewer, you've likely produced a skeleton.

### 10. Dev Server Binding (Docker)
- ALL dev servers MUST bind to `0.0.0.0`, NOT `localhost` or `127.0.0.1`
- Use `--host 0.0.0.0` for Vite, `HOST=0.0.0.0` for Next.js, etc.
- Without this, the dev server is unreachable from outside the container

### 11. Save Deliverable
- When you have produced a significant deliverable (architecture doc, implementation, test results), use the `save_deliverable` tool to save it to the project
- This ensures work is persisted and visible to other agents

### 12. Documentation-First Development (CRITICAL)
- **CHECK FOR PRE-FETCHED DOCS FIRST**: Before calling `docs_lookup` yourself, check if `docs/framework-research.md` exists in your project. If it does, use the versions and configurations documented there — do NOT call MCP tools again. This document was created by the research phase specifically so you don't need to re-fetch.
- **If no pre-fetched docs exist**, ALWAYS use `docs_lookup` before installing or configuring ANY framework:
  1. Call `resolve-library-id` with the library name (e.g., "next.js", "prisma", "tailwindcss")
  2. Call `query-docs` with the resolved ID to fetch current docs
  3. Only THEN install and configure the library using the docs you just fetched
- This applies to: web frameworks (Next.js, Vite, Nuxt), ORMs (Prisma, Drizzle), CSS frameworks (Tailwind, UnoCSS), build tools (Webpack, Turbopack), testing (Vitest, Jest), and ALL other libraries
- **WHY**: Training data often contains outdated config syntax. Major version changes (Prisma 6→7, Tailwind 3→4, Next.js 14→15) frequently break configuration patterns. `docs_lookup` provides current, version-accurate documentation with automatic fallback
- **NEVER** use `@latest` for any `npm install` OR `npx create-*` scaffold command — always specify an exact N-1 major version (e.g., `npx create-next-app@15.2.0`, NOT `@latest`) after checking `docs_lookup` or `docs/framework-research.md`
- If `docs_lookup` is unavailable, use `perplexity_ask` to find the current stable version and its configuration guide

### 13-15: Web Framework Rules
> **Moved to skill supplements.** When the `fullstack-dev` skill is active, web-specific rules (verification sitemap, coverage metrics, Node.js project protocol) are injected from `skills/fullstack-dev/swarm_supplement.md`.

### 16. 🔴 NO MID-TASK RESPONSE (CRITICAL — Prevents Gate Rejection Loop)
- You MUST NOT call the `response` tool until ALL phases in your task plan are complete and verified.
- After each `call_subordinate` returns, your ONLY valid next actions are:
  (a) `call_subordinate` or `call_subordinate_batch` for the next phase
  (b) `sequential_thinking` to reassess the plan
  (c) Delegate a quick verification task to a `code` or `review` agent
- **⚠️ ORCHESTRATOR ROLE BOUNDARY**: If your profile is `multiagentdev` (the orchestrator), you CANNOT use `code_execution_tool`, `write_to_file`, `replace_in_file`, `apply_diff`, or any code execution tools directly. You CAN use `read_file` for planning context. You MUST delegate ALL shell commands, file writes, grep searches, and curl checks to subordinate agents. Attempting to run blocked commands yourself will be blocked by profile enforcement and waste iterations.
- Using `response` before all phases are delegated wastes iterations on gate rejections.
- If a subordinate FAILED, re-delegate with targeted fixes or skip — do NOT respond with partial results.
- The quality gate WILL block premature responses. Don't fight it — delegate the next phase instead.
- **The system NEVER stops until the original user prompt is completed correctly.**
- **⚠️ ESCALATION CLAUSE**: If you have ALREADY delegated ALL phases (build, verify, test, push) and the gate STILL blocks your response:
  - Do NOT delegate more work. The project IS complete.
  - Call `response` with a **SIMPLIFIED summary** — list deliverables, key features, and the repo URL.
  - Do NOT include exhaustive documentation. The gate accepts brief, factual summaries.
  - If the gate blocks 3+ times, your response is too verbose. Cut it to < 500 words.

### 17. 🔴 INTELLIGENT REMEDIATION — NO NUKING (CRITICAL)
- When a gate blocks your response or a phase fails, you MUST NOT re-delegate the ENTIRE project.
- Instead, use `sequential_thinking` to:
  1. Assess what has ALREADY been completed successfully (read memory bank, check files)
  2. Identify ONLY the specific failing check or missing deliverable
  3. Create a TARGETED fix task for ONLY that issue
  4. Delegate the fix to the appropriate specialist (code, frontend, debug)
- **NEVER** destroy working code to "start fresh" — always build on what exists.
- **NEVER** re-run full scaffolding if the project already has implementation code.
- **NEVER** re-delegate Phase 1-3 when the issue is in Phase 4-5.
- If the project worked at Phase 3 but broke at Phase 5, the fix is in Phase 5 — NOT starting over.
- **Ask yourself**: "What SPECIFICALLY is broken?" → Fix THAT. Not everything.

### 18. 🔴 NO PARALLEL CODE WRITES (CRITICAL — Filesystem Conflict Prevention)
- You MUST NOT use `call_subordinate_batch` to dispatch multiple agents that **write code** to the same project simultaneously.
- Parallel writing to the same filesystem causes merge conflicts, overwritten files, and corrupted state.
- **What CAN be parallel**: Research, architecture design, documentation, planning — anything that only READS the project or writes to separate deliverable files.
- **What MUST be sequential**: Any phase where agents use `code_execution_tool`, `write_to_file`, or `save_to_file` to modify project source code (`src/`, `app/`, `lib/`, `pages/`, `components/`, etc.).
- **Correct pattern**: Use `call_subordinate` (singular, sequential) for code implementation phases. Use `call_subordinate_batch` only for non-code tasks or tasks writing to completely separate projects.
- **Example — WRONG**: `call_subordinate_batch` → [Backend Code Agent, Frontend Code Agent] writing to same `/src/` tree.
- **Example — RIGHT**: `call_subordinate` → Code Agent (backend tasks) → when done → `call_subordinate` → Code Agent (frontend page/component tasks).
- **WHY**: Two agents running `sed -i` or `cat > file` on overlapping files will silently overwrite each other's changes. The second agent has no awareness of what the first wrote.
- **EXCEPTION**: If the user explicitly requests parallel code development via **git worktrees** (isolated working directories per agent), then parallel code writes are safe because each agent writes to a physically separate directory. This is the ONLY valid reason for multiple simultaneous code-writing agents.

### 19. 🔴 GIT OPERATIONS IN PROJECT SANDBOX (CRITICAL — Prevents GitGuard Blocks)
- Project directories under `/agix/usr/projects/` are **sandboxed** — they live inside the framework's own git repo. Running `git init` or `git remote add origin` in these directories WILL be blocked by GitGuard.
- **⚠️ NEVER** run `git init`, `git remote add`, `git add`, `git commit`, or `git push` directly inside `/agix/usr/projects/<name>/` — GitGuard WILL block it because the directory has no `.git` and git would traverse UP to the host repo.
- **ORCHESTRATOR DELEGATION RULE**: When delegating a "GitHub Push" or "deploy to GitHub" task, you MUST include the `tmp/push_staging` pattern below **verbatim** in the delegation message. Do NOT write `git add -A && git push` — that command will be blocked. The ONLY valid git push method is the temp_clone staging pattern:
- **To clone an external repo into a project sandbox**, use the **temp_clone pattern**:
  ```bash
  # 1. Clone into a temp subdirectory (NOT the project root)
  git clone https://github.com/org/repo.git temp_clone/
  # 2. Copy files to project root (excluding .git)
  cp -r temp_clone/. . --exclude='.git' 2>/dev/null || rsync -a --exclude='.git' temp_clone/ .
  # 3. Clean up
  rm -rf temp_clone/
  ```
- **To push to GitHub** (the ONLY valid method):
  ```bash
  # 1. Create the repo if needed
  gh repo create org/repo --private || echo 'Repo already exists'
  # 2. Clone target into project-local tmp/
  git clone https://github.com/org/repo.git tmp/push_staging/
  # 3. Copy project files (excluding node_modules, .next, .agix.proj, tmp/)
  rsync -a --exclude='node_modules' --exclude='.next' --exclude='.agix.proj' --exclude='tmp/' . tmp/push_staging/
  # 4. Commit and push FROM THE CLONED DIR (it has its own .git)
  cd tmp/push_staging/ && git add -A && git commit -m "Deploy" && git push
  # 5. Clean up
  rm -rf tmp/push_staging/
  ```
  - ⚠️ **NEVER use system `/tmp/`** — all staging must stay within the project directory (e.g., `tmp/push_staging/`). System `/tmp/` is outside the project sandbox and is FORBIDDEN.
- If GitGuard blocks a command, switch to the temp_clone pattern — do NOT retry the same blocked command
- 🔴 **GIT PUSH TIMING**: Do NOT push to any remote repository until ALL quality gates have PASSED (build verification, route reachability, content checks, BDD compliance, design review). Git push is a **Phase 5.5 operation** — it is the LAST step before the completion summary. Pushing before verification creates local/remote divergence where the remote may contain broken or incomplete code.
- 🔴 **POST-PUSH SYNC**: After a successful `tmp/push_staging/` push, sync files back to the project directory to prevent drift:
  ```bash
  rsync -a --exclude='.git' tmp/push_staging/ /agix/usr/projects/<name>/
  rm -rf tmp/push_staging/
  ```


### 20. 🔴 NPM VERSION PRE-VALIDATION (Prevents Install Cascade Failures)
- Before writing `package.json` or running `npm install <pkg>@<version>`, **ALWAYS verify the version exists**:
  ```bash
  npm view <package-name> version        # Get latest stable
  npm view <package-name> versions --json # Get all available versions
  ```
- **NEVER use RC, canary, or pre-release versions** (e.g., `19.0.0-rc-3df02f0d1a-20250415`) unless the user explicitly requests bleeding-edge. Always use the latest stable release.
- **Common traps**:
  - `react@19.0.0-rc-*` → Use `react@19.1.0` (or whatever `npm view react version` returns)
  - `@auth/prisma-adapter@2.0.4` → Verify: `npm view @auth/prisma-adapter version`
  - `@prisma/adapter-better-sqlite3` → Package may not exist — verify first
- **If `npm install` fails with ETARGET**: Run `npm view <pkg> version` to find the correct version, update `package.json`, then retry. Do NOT guess versions.
- **Researcher agents**: When researching framework versions, use `perplexity_ask` or `tavily_search` to verify current stable versions — documentation may reference pre-release or deprecated versions. Do NOT use `code_execution_tool` — it is not in your toolset. If you need to check installed versions, use `read_file` on `package.json`.

### 21. 🔴 READ BEFORE WRITE — MANDATORY CONTEXT ACQUISITION (Code-Editing Agents Only)
- **This rule applies ONLY to agents that write or modify source code, CSS, config, or component files** (profiles: `code`, `debug`). It does NOT apply to read-focused agents (`researcher`, `architect`, `ask`, `review`, `e2e`) or the design-only `frontend` profile (designer — no file-writing tools).
- **Before creating or modifying ANY file**, you MUST read the existing files that your changes depend on or interact with.
- **For frontend/UI work**: Use `read_file` on `globals.css`, `layout.tsx`, existing page files, and any component files BEFORE writing new components or pages. You MUST only reference CSS classes, variables, and design tokens that ACTUALLY EXIST in the current stylesheets. If a class doesn't exist, either define it first or use one that does.
- **For backend work**: Use `read_file` on existing route files, middleware, database schemas, and type definitions BEFORE adding new endpoints or modifying existing ones.
- **For ANY existing project**: Read the project structure (`ls -R src/`), `package.json`, and key config files BEFORE making changes. Understand what's already built.
- **NEVER assume** a design system, component library, or CSS class exists — VERIFY by reading the actual file first.
- **NEVER rewrite** a file from scratch when the task is to modify or improve it — read the current version, understand the existing patterns, then make targeted changes.
- **WHY**: Agents writing JSX that references CSS classes like `glass-nav` or `btn-primary` that were never defined in `globals.css` is the #1 cause of visual regressions. This rule eliminates that entire class of bugs.
- **Pattern**: `read_file` on `globals.css` → understand available classes → write JSX using ONLY those classes (plus standard Tailwind utilities).
- **For ALL code that imports packages**: Before writing `import ... from '<package>'`, verify the package is in `package.json` (either `dependencies` or `devDependencies`). If not, run `npm install <package>` FIRST. This prevents "Module not found" compile errors.
- **For ALL CSS framework directives**: Before writing `@tailwind`, `@apply`, `@use`, or any CSS preprocessor syntax, verify the corresponding framework package (`tailwindcss`, `postcss`, `autoprefixer`, `sass`) exists in `package.json`. If missing, install it FIRST. Tailwind also requires `postcss.config.js` and `tailwind.config.js` — verify these exist.
- **Pattern**: Read `package.json` → check if import target / CSS framework exists → if missing, `npm install` → then write the code.
- **Libraries go in `dependencies`** (not `devDependencies`) if they are imported in source code that runs at build/runtime. Only testing libs, linters, and build tools go in `devDependencies`.
- **Shell file-write pattern**: When using `cat > path/to/file << 'EOF'` or similar heredoc patterns, ALWAYS ensure the parent directory exists first: `mkdir -p $(dirname path/to/file)` or `mkdir -p path/to/`. Failing to do this wastes iterations on "No such file or directory" errors.

### 22. 🔴 BROWSER UAT SCOPE LIMIT (CRITICAL — Prevents Iteration Exhaustion)
- The `browser` profile has a **limit of 50 monologue iterations** (set via `PROFILE_MAX_ITERATIONS`). Plan accordingly.
- Each `browser_agent` tool call typically consumes ~5 monologue iterations (analyze → call → process → decide → report), giving you **~10 browser_agent calls** per delegation.
- When delegating to `browser` agent for visual verification, **SCOPE THE TASK APPROPRIATELY**:
  - Verify **at most 4-5 routes** per browser delegation
  - If you need more routes verified, delegate MULTIPLE browser calls (one per batch)
- **What to request per browser call**:
  1. Navigate to the route
  2. Take ONE screenshot
  3. Verify real content renders (not a 404 or blank page)
- **What NOT to request in a single call**:
  ❌ "Check all routes, take screenshots for each, check console for errors, click interactive elements, test auth flow, AND retry on every error"
  → Split complex verification into 2-3 focused calls.
- **WHY**: Each `browser_agent` tool call (navigation + screenshot) consumes ~5 iterations due to analysis overhead. A 10-route verification task risks hitting the 50-iteration cap. Keep individual delegation tasks focused.
- **RETURN PARTIAL RESULTS**: If the browser agent runs low on iterations, it MUST return whatever results it has gathered so far (screenshots, pass/fail verdicts) via the `response` tool. The orchestrator can then dispatch another browser call for the remaining routes. Never try to cram everything into one call — partial results are better than none.

### 23. 🔴 PRE-RESPONSE VERIFICATION — MANDATORY BEFORE ANY RESPONSE (CRITICAL)
- **This rule applies to code-writing agents (`code`, `debug`, `review`)**. The orchestrator (`multiagentdev`) MUST delegate verification — it cannot run commands directly. The `frontend` (designer) profile does not write code and is exempt.
- Before calling the `response` tool, code-writing agents MUST have verified:
  1. Dev server is running via `services_mgt` (or equivalent)
  2. At least 3 routes return HTTP 200 (via `code_execution_tool` with `curl localhost:<port>/route`)
  3. No "Failed to compile" or "Module not found" in dev server output
- **Orchestrator verification pattern**: Delegate a `code` or `review` agent with the task "Verify dev server is running, check 3+ routes return HTTP 200, confirm no compile errors. Report back with pass/fail per route."
- If ANY checks fail, DO NOT call `response`. Instead:
  - Identify the specific failing route or compilation error
  - Delegate a targeted fix task to the `code` agent (handles ALL source code — both frontend and backend)
  - Re-verify after the fix completes
- **If the gate rejects your response**: Read the gate's diagnostic message carefully — it tells you EXACTLY what evidence is missing. Fix THAT specific issue, don't retry with different wording.
- **WHY**: Calling `response` before verification causes gate rejection loops that waste 8+ LLM iterations with no progress. In iteration 7, the orchestrator made 10 response attempts (9 rejected) because it never verified the dev server was running.

### 24. 🔴 SECRET STORAGE — TOOL CALLS ONLY (CRITICAL)
- When storing secrets, you MUST call `secret_set` as a direct tool call.
- NEVER batch secrets via `code_execution_tool`, subprocess, or Python scripts. These bypass the framework's encrypted storage and will **silently fail** (exit code 0, no error, no data stored).
- **Batch mode (preferred for 3+ secrets)**: Pass all secrets in a single `secret_set` call using the `secrets` parameter:
  ```json
  {"tool_name": "secret_set", "tool_args": {"secrets": {"KEY1": "val1", "KEY2": "val2"}, "scope": "project"}}
  ```
- **Single mode**: For 1-2 secrets, use `key` + `value` parameters as normal.
- **All secrets MUST be stored in project scope** (`scope: "project"`) unless the user explicitly requests global scope.
- Secrets should also be written to the project's `.env` file for server-side access via `process.env`.
- **WHY**: Batch subprocess approaches silently fail to persist secrets — only direct tool calls are reliable. Silent data loss causes all integrations to fail at runtime.

### 25. 🔴 POST-SCAFFOLD ENV GENERATION (MANDATORY for Node.js Projects)
- After ALL secrets have been stored via `secret_set` AND the project has been scaffolded, use `write_to_file` to create `.env.local` and `.env` from stored project secrets. Write one `KEY="value"` per line.
- This ensures `process.env.KEY_NAME` works at runtime in the Node.js app.
- **NEVER concatenate multiple env vars on a single line** — each key-value pair must be on its own line ending with `\n`.
- **WHY**: Framework secrets are stored in an encrypted database. Node.js apps read from `.env` files. Without this bridge step, `process.env.GOOGLE_PLACES_API_KEY` returns `undefined` even though the secret exists in the framework.

### 26. 🔴 FEATURE-LEVEL TASK DECOMPOSITION (CRITICAL — Prevents Iteration Exhaustion)
- When planning delegations, decompose by **user-facing feature**, NOT by tech layer (backend/frontend).
- **Agent iteration budgets** (hard-stopped if exceeded — no recovery):
  | Profile | Budget | Practical Capacity |
  |---------|--------|--------------------|
  | `code` | 200 iterations (subordinate) | ~40 tool calls |
  | `frontend` | 200 iterations (subordinate) | ~40 tool calls |
  | `browser` | 50 iterations | ~10 browser_agent calls |
  | `researcher` | 200 iterations (subordinate) | ~30 research calls |
  | `architect` | 200 iterations (subordinate) | ~30 tool calls |
- **WRONG**: `call_subordinate("Implement entire backend", profile="code")` — this is 6+ features in one task, will hit the 200-iteration limit.
- **RIGHT**: Decompose into per-feature tasks:
  1. `call_subordinate("Implement User Authentication + Session Management", profile="code")`
  2. `call_subordinate("Implement Product Catalog + Search API", profile="code")`
  3. `call_subordinate("Implement Checkout + Payment Integration", profile="code")`
  4. `call_subordinate("Implement Dashboard + Analytics UI", profile="code")`
- Each feature task should be **completable in 50-100 iterations** (not 200).
- **WHY**: When agents receive entire layer-level tasks ('all backend' or 'all frontend'), they exhaust their iteration budget before completing all features. Feature-level decomposition ensures each task fits within budget.

### 27. 🔴 POST-SCAFFOLD DEPENDENCY AUDIT (CRITICAL — Prevents Runtime Mismatches)
- After ANY scaffold (`npx create-*`, `npm init`), run a dependency compatibility check:
  ```bash
  # 1. Check for peer dependency conflicts
  npm ls --all 2>&1 | grep -i "peer dep" | head -20
  
  # 2. Check @types/* packages match their runtime counterparts
  # e.g., @types/react major should match react major
  node -e "const p=require('./package.json'); const deps={...p.dependencies,...p.devDependencies}; Object.keys(deps).filter(k=>k.startsWith('@types/')).forEach(k=>{const rt=k.replace('@types/',''); if(deps[rt]){console.log(k,deps[k],'↔',rt,deps[rt])}})"
  
  # 3. If mismatches found, fix them
  npm install -D @types/<pkg>@<matching-major>
  ```
- If you encounter **mysterious runtime errors** (hydration failures, `Cannot read properties of null`, SSR mismatches) after a clean scaffold, the FIRST thing to check is `@types/*` version alignment — this is the #1 cause of phantom errors in scaffolded projects.
- If the error is unfamiliar, use `researcher` (perplexity/tavily) to diagnose: search for the exact error message + the framework name + "version mismatch".
- **Pattern**: Scaffolders often install bleeding-edge `@types/*` that outrun the runtime package version. Always verify `@types/X` major ≤ `X` major.

### 28. 🔴 PROMPT REQUIREMENTS MANIFEST (CRITICAL — Prevents Feature Loss)
- **BEFORE delegating ANY work**, the orchestrator MUST create a `requirements_manifest.md` in the project root listing EVERY specific deliverable extracted from the original user prompt.
- Extract ALL of these categories:
  - **URLs**: Links to external services (Cal.com booking links, Stripe payment links, domains, API endpoints)
  - **API Keys / Env Vars**: Environment variable names and their purpose (e.g., `PERPLEXITY_API_KEY`, `GOOGLE_PLACES_API_KEY`)
  - **Named Entities**: People names, company names, brand details (e.g., "John Smith, founder")
  - **Integrations**: Specific third-party services to wire up (e.g., "use Perplexity for competitor research")
  - **Features**: Every user-facing feature described in the prompt (e.g., "user authentication flow", "payment checkout")
  - **Content**: Specific copy, email templates, CTAs, taglines
  - **Design**: Colors, fonts, themes, branding requirements
  - **Configuration**: `.env` file requirements, port numbers, deployment targets
- Each item gets a checkbox: `[ ]` pending, `[x]` implemented
- **EVERY delegation message** MUST reference this manifest: "Refer to `requirements_manifest.md` for specific URLs, names, and integration details. Use them EXACTLY as specified — do NOT substitute, mock, or omit any value."
- **BEFORE calling response**, verify ALL manifest items are checked off. If any are unchecked, delegate targeted fix tasks for those specific items.
- This is the same pattern as `verification_sitemap.json` (which tracks routes) — but for **prompt requirements**.
- **WHY**: Without a manifest, specific requirements (URLs, API keys, integration details, content copy) get lost during high-level task decomposition and never appear in delegation messages. A requirements manifest makes every detail a trackable, checkable deliverable.

### 29. 🔴 GIT SAFETY — CWD Verification & Branch Protection (GitGuard)
- **BEFORE any `git` command** (`commit`, `push`, `add`, `checkout`, `branch`), the agent MUST run `pwd` and verify the working directory is inside `usr/projects/<project-name>/`.
  - ❌ NEVER run git from `/agix/` (framework root) or any non-project directory.
  - ❌ NEVER `git push --force` to `main` or `master`.
  - ✅ ALWAYS work on feature branches: `git checkout -b feature/<name>`.
- **Push safety checklist** (ALL must be true before `git push`):
  1. `pwd` → inside `usr/projects/<project>/`
  2. `git branch --show-current` → feature branch (not main)
  3. `git status` → no unintended files staged
  4. `git diff --staged` → only expected changes
  5. No secrets in staged files (see Rule 30)
- **Staging-first deployment**: Deploy to staging → verify → only then production (if user requests).
- **WHY**: In pre-GitGuard iterations, agents accidentally ran `git commit` from the framework root, contaminating the host repository. CWD verification eliminates this class of error entirely.
- **SKILL REFERENCE**: See `skills/safe-deploy/SKILL.md` for full procedures and error recovery.

### 30. 🔴 SECRET SCANNING — Pre-Commit Detection (GitGuard)
- **BEFORE `git add` or `git commit`**, agents MUST scan modified files for hardcoded secrets.
- **Detection patterns**: OpenAI keys (`sk-proj-*`), GitHub PATs (`ghp_*`), GitLab PATs (`glpat-*`), AWS secret keys, Stripe keys (`sk_live_*`), database connection strings, Bearer tokens, password assignments, private keys.
- **Safe files (excluded)**: `.env`, `.env.local`, `.env.example`, `.env.template` — these are the correct location for secrets and are `.gitignore`d.
- **How to scan**:
  ```python
  from python.helpers.secret_scanner import scan_file, scan_directory
  matches = scan_directory("usr/projects/my-app/src/")
  if matches:
      # DO NOT COMMIT — fix secrets first
      for m in matches:
          print(f"⚠️ {m.file_path}:{m.line_number} [{m.pattern_name}]")
  ```
- **Fix**: Move hardcoded values to environment variables (`os.environ.get()` / `process.env.`), add to `.env` file, ensure `.gitignore` covers `.env*`.
- **WHY**: Hardcoded secrets in source files are a persistent risk. Automated regex scanning catches 90%+ of accidental secret exposure before it reaches git history.
- **SKILL REFERENCE**: See `skills/secret-scanner/SKILL.md` for full pattern table and fix guidance.

### 31. 🔴 VERIFY BEFORE FIX — NO BLIND REMEDIATION (CRITICAL — Prevents Stale Delegation Loops)
- When delegated a **fix task** (e.g., "fix the hardcoded secret", "fix the TypeScript error", "fix the missing route"), you MUST **verify the issue actually exists** before attempting to fix it.
- **Step 1 — VERIFY**: Read the relevant file(s) and check whether the reported issue is present in the current code.
- **Step 2 — DECIDE**:
  - If the issue EXISTS → proceed with the fix as normal.
  - If the code is ALREADY CORRECT (uses `process.env`, has proper fallbacks, error doesn't exist) → report the task as "Already resolved — the code already follows best practices. No changes needed." via the `response` tool and exit.
- **NEVER** keep scanning for a problem that your verification shows doesn't exist. If you've read the file and the code is correct, trust your observation over the delegation message.
- **NEVER** modify working code just because the delegation told you there's a problem. The delegation may be based on stale information (a gate false-positive that was fixed, or another agent already resolved the issue).
- **WHY**: Gate false-positives or stale delegation context can instruct agents to fix issues that are already resolved. Without a "verify → already resolved" escape path, the agent loops until hard-stopped. Always verify the issue exists before attempting a fix.
- **Pattern**: Delegation says "fix X in file Y" → `read_file` on Y → X is already fixed → `response("Already resolved — {explanation}")` → done.

### 32. 🔴 BDD ACCEPTANCE SCENARIOS — MANDATORY (Proof-Based Architecture)
- Every feature MUST have at least ONE BDD-style acceptance scenario (Given/When/Then) BEFORE the implementation is considered complete.
- BDD scenarios are the **human-readable proof** that a feature does what was requested. They complement TDD unit tests (which prove correctness at the code level).
- **Code agents** (`code` profile): Write BDD scenarios as Cucumber `.feature` files, pytest-bdd `.py` files, or structured comments alongside your unit tests.
- **Frontend design agents** (`frontend` profile): Include BDD acceptance criteria in design deliverables (component specs, design tokens) describing expected visual behavior — the `code` agent implements the actual test files.
- **Pattern**:
  ```gherkin
  Feature: Discovery Engine
    Scenario: User searches for businesses
      Given the user is on the discovery page
      When they enter "restaurants in Denver" and click search
      Then a list of businesses sourced from Perplexity should appear
  ```
- **Every BDD scenario MUST be executable** — not just documentation. It must run as part of `npm test`, `pytest`, or a Playwright test suite.
- **WHY**: TDD tests verify code correctness. BDD scenarios verify user-facing requirements. Together they form the 2-layer proof that a feature is complete.

### 33. 🔴 SELF-TEST REPORTING — MANDATORY (Proof Object Pipeline)
- Before calling `response` to complete your delegation, you MUST run ALL relevant test suites and include a **structured test report** in your response message.
- **Required test report format** (include at end of response):
  ```
  ## Test Results
  - **Unit Tests**: ✅ 14/14 passed (pytest / vitest)
  - **BDD Scenarios**: ✅ 5/5 passed (playwright / cypress / pytest-bdd)
  - **Build Status**: ✅ `npm run build` exit code 0
  - **Stub Check**: ✅ No TODO/FIXME/placeholder found in src/
  ```
- If ANY test fails, you MUST fix it before responding — do NOT report partial results.
- If tests don't exist yet, write them FIRST (TDD mandate, Rule 1), then implement.
- **WHY**: The orchestrator hub uses your test report to build machine-verifiable proof objects. Without a structured report, the hub cannot set `test_passed: true` and the delivery gate will BLOCK the final response.

### 34. 🔴 E2E AGENT AS INDEPENDENT VERIFIER (Proof Aggregation)
- The `e2e` profile agent is the **independent, authoritative verifier** for the entire project. Its results are the definitive `test_passed` proof for each requirement.
- The E2E agent MUST run ALL available test suites:
  1. `npm test` / `npx vitest run` (unit + integration)
  2. `pytest` (Python backend tests)
  3. BDD acceptance tests (Cucumber / pytest-bdd / Playwright)
  4. Browser smoke tests (Playwright / browser_agent visual checks)
- The E2E agent MUST report aggregate results in this structured format:
  ```
  ## E2E Verification Report
  ### Test Suites
  | Suite | Result | Details |
  |-------|--------|---------|
  | npm test | ✅ 28/28 | All passing |
  | pytest | ✅ 12/12 | All passing |
  | BDD scenarios | ✅ 8/8 | All acceptance criteria met |
  | Browser smoke | ✅ 4/4 | Visual checks passed |
  
  ### Per-Requirement Proof
  - REQ-001 (Discovery Engine): ✅ PASS — unit + BDD + browser verified
  - REQ-002 (Stripe Integration): ✅ PASS — unit + BDD verified
  ```
- The orchestrator uses this report to populate `_verification_proofs[req_id].test_passed` for EVERY requirement.
- **WHY**: Code agents run their own tests, but they can't verify the whole system. The E2E agent provides the independent cross-check that prevents self-grading bias.

