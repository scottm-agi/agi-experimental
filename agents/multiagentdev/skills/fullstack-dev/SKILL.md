---
name: fullstack-dev
description: >
  Full-stack web application development orchestration. Covers project scaffolding,
  framework research, architect design, frontend/backend implementation phases,
  integration wiring, build verification, and E2E testing. Activates for any request
  involving web apps, websites, dashboards, landing pages, or SaaS platforms.
triggers:
  - web app
  - full-stack
  - website
  - landing page
  - frontend
  - next.js
  - react
  - vue
  - dashboard
  - SaaS
  - web application
  - UI
  - user interface
trigger_patterns:
  - 'build.*web.*app'
  - 'create.*website'
  - '(next\.?js|react|vue|svelte|nuxt).*project'
  - 'landing.*page'
  - 'saas.*platform'
  - 'web.*dashboard'
anti_triggers:
  - CLI tool
  - command line tool
  - script only
  - data pipeline
  - API only
  - library
  - python script
  - shell script
skill_type: task_specific
---

# Full-Stack Web Development Orchestration Skill

This skill extends the core orchestrator with web-specific phases, verification gates, and
subordinate delegation patterns for building complete web applications.

**When this skill activates**: The orchestrator loads these instructions AFTER completing
generic Phase 0 planning, injecting Phases 0.5–5 into the pipeline.

### 🔴 MANDATORY PHASE ORDERING — EXACT IDs REQUIRED

Phase IDs in `decomposition_index.json`, `update_task_list`, and all delegation messages **MUST match the skill's phase numbering exactly**. You MUST NOT re-number, rename, or reorder phases. The canonical execution order is:

| Order | Phase ID | Name | Category | Profile | Gate |
|-------|----------|------|----------|---------|------|
| 1 | **0.1** | Existing Project Audit | `PLANNING` | `code` | Brownfield check |
| 2 | **0** | Planning & Manifest Extraction | `PLANNING` | orchestrator | `content_manifest.json` + `requirements_ledger.json` |
| 3 | **0.5** | Research & Docs Pre-fetch | `PLANNING` | `researcher` | `docs/framework-research.md` exists |
| 4 | **0.5b** | Feature Classification & Wave Prioritization | `PLANNING` | orchestrator | Tier 1/2/3 classification |
| 5 | **1** | Setup & Scaffold | `PLANNING` | `code` | scaffold via `code_execution_tool` verified |
| 6 | **2** | Architect Design | `DESIGN` | `architect` | Design doc + BDD + Prisma schema |
| 7 | **2.3** | UI/UX Design (Mockups + Tokens) | `DESIGN` | `frontend` (designer) | `design-tokens.json` + `component-spec.md` + mockups |
| 8 | **2.4** | Wire Design Tokens into Config | `DESIGN` | `code` | `tailwind.config.ts` non-empty extend |
| 9 | **2.5** | Validate Decomposition + Schema Lock | `DESIGN` | orchestrator | `decomposition_index.json` final + `check_type_coherence()` |
| 10 | **2.6** | Architect ↔ Researcher Cross-Check | `DESIGN` | `researcher` + `architect` | Versions confirmed + design coherence |
| 11 | **2.7** | BDD Enrichment + Quality Gate | `DESIGN` | orchestrator + `architect` | BDD ≥90% + `check_bdd_quality_blocking()` PASS |
| 12 | **2.8** | TDD Skeleton Expansion | `DESIGN` | orchestrator | All test specs in `docs/tdd/` + test runner dir |
| 13 | **3** | Implementation (TDD + BDD) | `IMPLEMENTATION` | `code` | Per-feature proofs + `RetryBudgetManager` caps |
| 14 | **3.5** | Boomerang Assessment + Gate Resolution | `IMPLEMENTATION` | orchestrator | Re-dispatch gaps + `resolve_gate_failure()` |
| 15 | **3.8** | Scaffold Cleanup | `IMPLEMENTATION` | `code` | No scaffold boilerplate |
| 16 | **3.8.1** | Post-Scaffold File Inventory | `IMPLEMENTATION` | orchestrator | All placeholders assigned |
| 17 | **3.9** | Build Verification + Cache Invalidation | `IMPLEMENTATION` | `code` | `npm run build` exits 0 + `invalidate_project_context_cache()` |
| 18 | **4** | Frontend-Backend Integration | `VERIFICATION` | `code` | API call patterns exist |
| 19 | **4.5** | Navigation & API Route Maps | `VERIFICATION` | orchestrator | Route maps built |
| 20 | **4.7** | Full Wiring Verification | `VERIFICATION` | `code` | Layer 1 + Layer 2 pass |
| 21 | **4.8** | CSS/Config Verification Gate | `VERIFICATION` | `code` | Tailwind/PostCSS/CSS verified |
| 22 | **4.9** | Build-Freeze Gate | `VERIFICATION` | `code` | `npm run build` exits 0 + snapshot |
| 23 | **4.95** | Pre-E2E Smoke Check | `VERIFICATION` | orchestrator | HTTP reachability + content checks |
| 24 | **5** | Verification (E2E + Visual) | `VERIFICATION` | `e2e` / `browser` | All proofs PASS |
| 25 | **5.0.0** | Dev Server Clean Restart | `VERIFICATION` | `code` | Server running with CSS active |
| 26 | **5.0.1** | Live Integration Smoke Test | `VERIFICATION` | `code` | API keys + endpoints verified |
| 27 | **5.1** | E2E Test Aggregation | `VERIFICATION` | `e2e` | All suites PASS |
| 28 | **5.2** | Verification Matrix Re-Dispatch | `VERIFICATION` | orchestrator | WEAK/UNTESTED reqs fixed |
| 29 | **5.3** | E2E Delegation Self-Check | `VERIFICATION` | orchestrator | 5-point checklist all YES |
| 30 | **5.0.5** | Design Review Gate (Blocking) | `VERIFICATION` | `frontend` (designer) | Visual fidelity confirmed or escape hatch |
| 31 | **5.5** | Version Control Publication | `DEPLOYMENT` | `code` | Repo pushed (if requested) |
| 32 | **6** | Iteration (if verification fails) | `VERIFICATION` | varies | Max 3 cycles |
| 33 | **7** | Summary | `DEPLOYMENT` | orchestrator | Final report |

> [!NOTE]
> **Category** maps to `PhaseCategory` enum in `python/helpers/gate_quality.py`. Gate checks with a `phase_category` are suppressed when the run scope excludes that category (e.g., PDV only runs during `IMPLEMENTATION`+). To disable ALL gates for testing, set `AGIX_GATES_ENABLED=false`.


**Violations**: If your `decomposition_index.json` contains phase IDs like "Phase 1: Backend" or "Milestone 2: Frontend" instead of the canonical IDs above, you are violating this rule. **Phase 0.5 (Research) and Phase 2 (Architect) MUST execute BEFORE Phase 2.3 (Design) BEFORE Phase 3 (Implementation).**

**Agent Boundary Rule**: Phase 3 (Implementation) delegates ONLY to the `code` agent. The `frontend` (designer) agent NEVER receives implementation tasks — it only participates in Phase 2.3 (design) and Phase 5.0.5 (review).

### 🔴 AGENT PROFILE BOUNDARIES — WHO DOES WHAT (MANDATORY)

This table defines what each agent profile CAN and CANNOT do. **Mis-delegating a task to the wrong profile wastes tokens and causes tool blocks.**

| Profile | Role | CAN Do | 🚫 CANNOT Do (NEVER delegate these) | Output Tool | Output Location |
|---------|------|--------|--------------------------------------|-------------|-----------------|
| `code` | Full-Stack Developer | Write/edit ALL source code, run commands, install packages, TDD tests, scaffold, build, deploy | — | `write_to_file`, `replace_in_file`, `code_execution_tool` | Project source tree |
| `frontend` | UI/UX Designer | Design tokens, mockups, component specs, visual review | Source code, TDD, tests, implementation, `write_to_file`, `code_execution_tool` | `save_deliverable` with `output_path` | `docs/` directory only |
| `architect` | System Architect | Architecture docs, BDD scenarios, Prisma schema specs, decomposition | Source code, tests, `write_to_file`, `code_execution_tool` | `save_deliverable` with `output_path` | `docs/` directory only |
| `researcher` | Research Analyst | Web research, API docs, version checking, market analysis | Source code, tests, `write_to_file`, `code_execution_tool` | `save_deliverable` with `output_path` | `docs/` directory only |
| `e2e` | QA / E2E Tester | Run tests, browser verification, dev server management | File creation/editing (`write_to_file`, `replace_in_file`) | `response` | Chat response only |
| `debug` | Debugger | Diagnose bugs, read logs, run commands, trace errors | File creation/editing (`write_to_file`, `replace_in_file`) | `response` | Chat response only |
| `review` | Code Reviewer | Read code, audit quality, suggest improvements | Source code, tests, `write_to_file`, `code_execution_tool` | `response` | Chat response only |

**Key rules:**
- **TDD / test skeleton / test expansion → always `code`**. Never `frontend`, `architect`, or `researcher`.
- **Phase 2.3 (Design) → `frontend`**. Output: `save_deliverable(output_path="docs/design-tokens.json")` and `save_deliverable(output_path="docs/component-specs.md")`.
- **Phase 2.8 (TDD Skeleton) → orchestrator generates** from `test-skeleton.json`. If delegation needed → `code` only.
- **Phase 3+ (Implementation) → `code` only**. The `frontend` agent is NEVER involved after Phase 2.3 until Phase 5.0.5 (visual review).

### 🔴 Prohibited Tools in This Skill Flow
- **Do NOT use `borg_compare`** in ANY phase of the fullstack-dev flow. `borg_compare` is a forensic tool for comparing two separate repositories and cloning features from one to the other. It is NOT relevant to building new applications. Using it wastes iterations and triggers delegation loops.
- If you need to compare files, use standard `read_file` and diff tools.

### 🔴 Standalone Delegation Rule (F-16 — MANDATORY)
When delegating via `call_subordinate` or `call_subordinate_batch`, you MUST submit it as the **ONLY** tool in the batch. **NEVER** combine `call_subordinate` with `requirements`, `read_file`, `sequential_thinking`, `code_execution_tool`, or any other planning/execution tool in the same tool-call batch.

**Why**: The BATCH FENCE reorders mixed batches to defer execution tools, causing `call_subordinate` to be blocked. This wastes iterations as the orchestrator resubmits the same delegation repeatedly. Submitting delegation as a standalone call avoids the fence entirely and saves ~42% of orchestrator iterations.

**Correct**:
```
Tool call 1: [requirements(action="suggest")]
Tool call 2: [call_subordinate(task="Phase 3 implementation...")]  ← standalone
```

**Wrong**:
```
Tool call 1: [requirements(action="suggest"), call_subordinate(task="...")]  ← BATCH FENCE blocks this
```

### 🔴 Deferred Tool Recovery (GAP-4 — #14 AUTOMATED)
If the BATCH FENCE defers a delegation, the deferred tool call is **automatically persisted** to `agent.data['_deferred_tool_calls']` via `persist_deferred_tool()` (`python.helpers.deferred_tool_persistence`). On the next iteration, `recover_deferred_tools()` retrieves any persisted calls for re-submission. This prevents silently lost delegations — the orchestrator no longer needs to manually detect and retry deferred `call_subordinate` calls.

## Phase 0.1: Existing Project Audit (MANDATORY for Brownfield Work)

**Before decomposing into tasks, determine if this is GREENFIELD (new project) or BROWNFIELD (improving existing code).**

1. **Check if the project already exists**: `ls /agix/usr/projects/` — If a matching project directory exists with source code, this is BROWNFIELD.
2. **If BROWNFIELD — audit the current state BEFORE planning**:
   - Delegate to `code` agent: "Read and summarize the current project state:
     - `ls -R src/` (or `app/`) — list all existing files
     - Use `read_file` on `src/app/globals.css` (or equivalent) — document ALL defined CSS classes and design tokens
     - Use `read_file` on `src/app/layout.tsx` — document the current layout structure
     - Use `read_file` on `package.json` — document installed dependencies and scripts
     - List all existing page routes
     - Save a deliverable called `docs/current-state-audit.md`"
3. **ALL subsequent delegation messages MUST include**: "This is an EXISTING project. Read `docs/current-state-audit.md` FIRST. You must work within the existing design system and architecture. Do NOT rewrite files from scratch — make targeted improvements. Only use CSS classes that are defined in the existing `globals.css` or standard Tailwind utilities."
4. **Task decomposition for BROWNFIELD work MUST be incremental**: Each task should be a targeted improvement, NOT a full rewrite.
5. **NEVER re-scaffold an existing project** — If `package.json`, `next.config.*`, and `src/` already exist, the project is scaffolded. Creating a new scaffold will DESTROY existing work.
6. **🔴 BROWNFIELD DOES NOT CHANGE THE ROUTING TABLE — Re-read it NOW:**
   - The Agent Routing Table (in the core prompt) STILL applies in brownfield mode.
   - Tasks involving **design mockups, design tokens, component specs, visual design review** → delegate to **`frontend`** profile (designer only — NO code)
   - Tasks involving **ALL source code — backend (API routes, database, auth) AND frontend (pages, components, CSS, responsive design, animations)** → delegate to **`code`** profile

---

## 🔴 Content Manifest Extraction & Fidelity Gate (MANDATORY — ALL delegations)

The user prompt serves dual roles — it's both a **requirements doc** and a **content specification**. The orchestrator must treat it as both.

### Step 1: Create `content_manifest.json` (BEFORE any delegation)

During Phase 0 planning, use `sequential_thinking` to scan the user prompt for ANY literal values that must appear verbatim in the output. These may include:
- Names, identities, company names, email addresses
- URLs (booking links, payment links, domains, API endpoints)
- Pricing, billing terms, plan names
- API service names and their intended purposes
- Email templates, signatures, CTA text
- Specific scenarios, workflows, or business rules described in detail

**Let the prompt's content determine the schema** — do NOT force a fixed template. Save as `content_manifest.json` in the project root.

**Canonical Structure (ISS-2, RCA-ITR2):** Your manifest **MUST** follow this canonical structure when the prompt includes these common fields. **🔴 CRITICAL: Do NOT flatten nested objects into simple strings.** For example, `"founder": "Jon"` is WRONG — it MUST be `"founder": {"name": "Jon", "email": "..."}`. This ensures downstream validation and test gates can consume the manifest deterministically:

```json
{
  "founder": {
    "name": "Founder Name from prompt",
    "email": "founder@example.com"
  },
  "pricing": {
    "monthly": "$X/mo",
    "stripe_monthly_url": "https://buy.stripe.com/...",
    "stripe_prepaid_url": "https://buy.stripe.com/..."
  },
  "urls": {
    "calendly": "https://calendly.com/...",
    "domain": "example.com"
  },
  "scenarios": [
    "Scenario 1 description",
    "Scenario 2 description"
  ],
  "tech_stack": {
    "framework": "Next.js",
    "database": "PostgreSQL"
  },
  "domain": "example.com",
  "competitors": [
    {
      "name": "Competitor Name",
      "price": "$X/mo",
      "target": "Market segment",
      "weakness": "Why we win"
    }
  ],
  "email_sequences": [
    {
      "trigger": "pipeline_add",
      "steps": [
        {"day": 0, "type": "intro", "description": "Initial outreach"},
        {"day": 3, "type": "follow_up", "description": "Day 3 follow-up"},
        {"day": 7, "type": "final", "description": "Day 7 final touch"}
      ]
    }
  ],
  "compliance": {
    "email": ["CAN-SPAM: unsubscribe mechanism", "Physical address in signature"],
    "payment": ["PCI-DSS: Stripe Elements only"]
  },
  "models": [
    {
      "marketing_name": "Claude Sonnet 4",
      "provider": "openrouter",
      "verified_slug": "",
      "context": "Primary AI model for the application"
    }
  ],
  "integrations": [
    {
      "name": "Resend",
      "type": "email",
      "env_var": "RESEND_API_KEY",
      "sdk_package": "resend",
      "api_base_url": "https://api.resend.com",
      "auth_pattern": "bearer_token"
    }
  ]
}
```

**Key rules for the canonical structure:**
- Use `founder.name` and `founder.email` — NOT `identity.founder` or `identity.email`
- Use `pricing.stripe_monthly_url` — NOT `links.stripe_monthly`
- Use `urls.calendly` — NOT `links.calendly`
- Use `scenarios` as a top-level array — NOT nested under another key
- Omit keys that aren't present in the prompt — the schema is additive, not mandatory
- Use `competitors` as a top-level array when the prompt contains a competitive landscape or comparison table
- Use `email_sequences` when the prompt describes multi-step email flows with timing
- Use `compliance` when the prompt mentions legal requirements (CAN-SPAM, TCPA, PCI-DSS)
- Use `models` when the prompt specifies AI/LLM models — capture the marketing name, provider, and leave `verified_slug` empty for the researcher to fill in during Phase 0.5 Step 3
- Use `integrations` when the prompt mentions external APIs/services (Resend, Stripe, Google Places, OpenRouter, etc.) — for EACH integration, extract:
  - `name`: Service display name (e.g., "Resend")
  - `type`: Category (e.g., "email", "payments", "search", "llm")
  - `env_var`: Environment variable name, derive as `{NAME_UPPER}_API_KEY` (e.g., "RESEND_API_KEY")
  - `sdk_package`: The npm/pip package name (e.g., "resend", "@stripe/stripe-js") — use your knowledge or leave empty for researcher to verify
  - `api_base_url`: The API base URL (e.g., "https://api.resend.com") — leave empty if unknown
  - `auth_pattern`: How to authenticate: `bearer_token`, `api_key_header`, `basic_auth`, `oauth2` — leave empty if unknown


### Step 1b: Extract Requirements with Stable GUIDs (BEFORE any delegation)

IMMEDIATELY after creating `content_manifest.json`, use `sequential_thinking` to decompose the user prompt into a **structured list of requirements**. Each requirement gets a **stable GUID** computed from its text:

- **GUID**: `REQ-{first 8 chars of MD5 hash of the requirement text}`
- **text**: The specific requirement
- **category**: One of: `url`, `integration`, `feature`, `branding`, `config`, `content`, `design`

Use the `generate_guid` tool. Save this array to `requirements_ledger.json` in the project root.

**Requirements Extrapolation Policy** (RCA-ITR5 ISSUE-8):
- ✅ **DO extrapolate** requirements that directly support explicitly stated prompt requirements (e.g., if the prompt says "outreach pipeline with AI emails", a `/scenarios` page showing email scenarios is a valuable supporting feature)
- ✅ **Tag extrapolated requirements** with `"source": "extrapolated"` so they're distinguishable from explicit prompt requirements
- ✅ **Follow KISS**: Extrapolated features should be simple, focused, and genuinely needed — not speculative or aspirational
- ❌ **DO NOT extrapolate** features that don't directly support any explicit prompt requirement
- ❌ **DO NOT extrapolate** administrative pages, analytics dashboards, or settings pages unless the prompt explicitly mentions them

### 🔴 Feature Atomization Rule (MANDATORY)
When extracting requirements, **split compound features** into individual requirements. A compound feature is any prompt statement that describes multiple distinct implementation items:

**Example (WRONG)**:
- `REQ-abc`: "3-email sequence with intro, day 3 follow-up, and day 7 follow-up" ← ONE requirement for THREE distinct features

**Example (CORRECT)**:
- `REQ-abc1`: "Email sequence: intro email with free audit link sent immediately after pipeline add"
- `REQ-abc2`: "Email sequence: day 3 follow-up email generated lazily when due"
- `REQ-abc3`: "Email sequence: day 7 final touch email generated lazily when due"

**How to detect compound features**: If a requirement mentions multiple time triggers (day 3, day 7), multiple distinct endpoints, multiple UI pages, or uses enumeration words ("intro + follow-up + final"), it MUST be split. Each sub-feature that needs its own code path, API endpoint, database field, or scheduled job MUST be a separate requirement.

### Step 1c: Create Milestone-Level Decomposition Index (BEFORE architect delegation)

After `requirements_ledger.json` is ready, group requirements into **high-level milestones** (3-6 major work packages). Write the initial `decomposition_index.json` with top-level sequence IDs only.

**Brownfield adjustment**: If Phase 0.1 determined this is a brownfield project, omit the scaffold milestone. Start with "Audit Existing Codebase" as `1.0.0` instead.

### Step 1d: Generate Test Skeleton & BDD Skeleton (BEFORE architect delegation)

After `requirements_ledger.json` exists, generate testability skeletons:
1. `test-skeleton.json` — maps each REQ-ID to a suggested test type (unit, integration, e2e, literal, config) and injects delivery standard REQs (e.g., REQ-DELIVERY-001 for project README)
2. `bdd-scenarios.md` — (web projects only) generates REQ-tagged Gherkin scenarios with concrete THEN clauses for architect enrichment

These skeletons are generated by the infrastructure pipeline automatically. They provide the architect with a test expectation contract and ensure every requirement has a traceable test type BEFORE any implementation begins.

**ENFORCEMENT**: Both skeletons must exist in `docs/` before proceeding to Phase 2 (Architect).

### Step 2: Attach manifest AND requirement GUIDs to EVERY subordinate delegation

EVERY `call_subordinate` task message MUST include the relevant subset of the manifest with requirement GUIDs linking the delegation to tracked requirements.

### Content Fidelity Rules
- If the prompt specifies a price → use that exact price
- If the prompt specifies a URL → use that exact URL
- If the prompt specifies an integration → use that service
- If the prompt specifies a name/identity → use it verbatim
- If the prompt specifies a model or API slug → use the **exact model slug** from the prompt (e.g., `gpt-4o-mini`, `claude-sonnet-4-20250514`). NEVER substitute with a different slug from training data. Use the resolved slug from the user's requirements, not a hallucinated alternative.
- **NEVER fabricate statistics, testimonials, or user counts** that the prompt doesn't provide

---

## Phase 0.5: Research & Documentation Pre-fetch (MANDATORY — BEFORE ANY IMPLEMENTATION)

**WHY**: Each subordinate agent spawns its own MCP server connections. If 5 agents all call `resolve-library-id` simultaneously, it exhausts thread pools. Do ALL research ONCE upfront.

**Before delegating**: Use `read_file` on `.mise.toml` in the project root to get runtime versions. Include these as context in the delegation message.

**Delegate to `researcher` agent** with this pattern:
```
TASK: Phase 0.5 — Framework Documentation Pre-fetch & Compatibility Research

Before ANY implementation begins, research all frameworks in the tech stack.

## RUNTIME CONSTRAINTS (provided by orchestrator)
{{RUNTIME_CONSTRAINTS}}

ALL framework version recommendations MUST be compatible with these runtime versions.

## Step 1: Individual Framework Research
For EACH framework/library:
1. Call `resolve-library-id` with the library name
2. Call `query-docs` with the resolved ID to fetch current stable version + docs
3. Record the EXACT version number and key configuration patterns

## Step 2: Version Compatibility Matrix (CRITICAL)
Research which versions work TOGETHER:
1. Use `perplexity_ask` (or `tavily_search` as fallback) to search for compatibility
2. Build a compatibility matrix: ✅ confirmed, ⚠️ known issues, ❌ incompatible
3. Prefer N-1 major versions with better ecosystem compatibility

## Step 3: Literal & API Identifier Verification (RCA-15 RC-2 — CRITICAL)
Check the content_manifest.json for any model names, API identifiers, or external service slugs. For EACH one:
1. Extract the marketing name from the manifest (e.g., "Claude Sonnet 4 via OpenRouter", "GPT-4o")
2. Search the provider's official API docs to verify the EXACT model slug / API identifier
   - For OpenRouter models: Search `https://openrouter.ai/models` for the correct slug (e.g., `anthropic/claude-sonnet-4`)
   - For direct provider APIs: Check the provider's model listing page
   - For payment providers: Verify environment variable naming conventions
3. Write verified identifiers into `docs/framework-research.md` using this format:
   ```
   ## LLM Model Verification
   Model ID: `anthropic/claude-sonnet-4`
   Provider: OpenRouter
   Marketing Name: Claude Sonnet 4
   Verified At: [date]
   ```

**Why this exists (RCA-15 RC-2)**: Without Step 3, the researcher only verified framework versions. Model slugs had no verification step, no manifest field, and no researcher scope. The code agent's `resolve_literals` tool found no researcher data and fell back to stale training data (`claude-3.5-sonnet` instead of `claude-sonnet-4`).

### Step 3b: Integration SDK Verification (ADR-ITR48 F-3 — CRITICAL)

For EACH integration entry in `content_manifest.json`:
1. Verify the `sdk_package` name is correct (search npm/PyPI for the exact package)
2. Verify the `api_base_url` is current (check the provider's API docs)
3. Confirm the `auth_pattern` (bearer, api_key_header, etc.)
4. If any field was left empty during manifest creation, fill it in now using research
5. Write verified SDK details into `docs/framework-research.md`:
   ```
   ## Integration SDK Verification
   Service: Resend
   npm Package: `resend` ✅ (verified on npmjs.com)
   API Base URL: `https://api.resend.com` ✅
   Auth: Bearer token via `Authorization: Bearer $RESEND_API_KEY`
   Verified At: [date]
   ```

**Why this exists (ADR-ITR48 F-3)**: Without SDK verification, code agents received only `{name, type, env_var}` — no package name, no endpoint. They defaulted to mocking integrations because they couldn't discover the real SDK. This step populates `sdk_package`, `api_base_url`, and `auth_pattern` so the delegation brief gives code agents concrete implementation details.

## Step 4: Database Provider Compatibility Research (if project uses a database)

If the project requirements include database operations (Prisma, Drizzle, Supabase, etc.):
1. Research the database provider's feature support for the target deployment
2. Specifically check: enum type support, JSON column support, array types, default values
3. Document any limitations (e.g., "SQLite does not support enums — use string constraints instead")
4. Include the provider-specific Prisma/Drizzle adapter name and version

**Why this exists (SS-13)**: In the MainStreet smoke test, the code agent used Prisma enums with a SQLite database, which doesn't support native enums. This caused build failures that consumed 3 delegation cycles to diagnose. Provider research at Phase 0.5 prevents the class of error entirely.

Save deliverable with output_path="docs/framework-research.md" — this places the file at the canonical project path AND in deliverables/ for backward compatibility. Include pinned versions, compatibility matrix, verified model slugs, and recommended stack versions.

CRITICAL: NEVER recommend @latest. Always pin to exact versions from the docs.
```

**ENFORCEMENT**: Do NOT proceed to Phase 1 until `docs/framework-research.md` exists AND includes a version compatibility matrix.

### Phase 0.5b: Feature Classification & Wave Prioritization (ADR-007)

After Phase 0 planning, classify EVERY feature:
1. **Tier 1 (MUST)**: Core functionality the app cannot ship without
2. **Tier 2 (SHOULD)**: Important but not blocking
3. **Tier 3 (NICE)**: Polish and extras

Delegation ordering MUST follow tier priority. If agent budget exhaustion occurs, re-dispatch ONLY Tier 1 and Tier 2 features.

#### 🔴 Priority-Scope Inclusion Rule (U-1, RCA-2 through RCA-6)

**ALL requirements in the user prompt are IN-SCOPE unless explicitly marked "out of scope" or "do not build".** Timeline labels ("Near-Term", "Phase 2", "Growth", "Weeks 3-6", "What needs to happen") indicate **IMPLEMENTATION ORDER**, not scope exclusion. Every extracted requirement MUST have a work package in the decomposition.

- If a requirement is labeled "Near-Term" → it is IN-SCOPE, build it in a later wave
- If a requirement is labeled "Growth" → it is IN-SCOPE, build it after core features
- If a requirement appears under "What needs to happen" → it is IN-SCOPE, treat as action items
- **ONLY exclude requirements that contain explicit exclusion language**: "out of scope", "do not build", "future consideration only", "not for this build"

The `LineItem.priority` field tags each requirement with its tier (`immediate`, `near_term`, `growth`, `phased`, `action_needed`). ALL tiers MUST appear in the decomposition. If any priority tier has zero work packages, the decomposition is INCOMPLETE.

---

## Phase 1: Setup & Context

1. **Clone/access the repo** → Delegate to `code` agent
2. **Read the issue** → Delegate to `ask` or `researcher` agent
3. **Analyze the codebase** → Delegate to `architect` agent

### 🔴 SCAFFOLDING: MANDATORY version-pinned scaffold via `code_execution_tool`

**CRITICAL**: Project scaffolding MUST use `code_execution_tool` to run the scaffold command with a pinned version (e.g., `npx create-next-app@15.1.0 . --typescript --tailwind --eslint --app --use-npm`). NEVER use `@latest`. The scaffold setup MUST:
- Handle non-empty directories (project dirs always have `.mise.toml`, `.agix.proj`, `memory-bank/`)
- Pin exact versions (no `^` or `~` ranges) — strip `^` and `~` from `package.json` after scaffolding
- Create `.npmrc` with `legacy-peer-deps=true`
- Configure ESLint build isolation

Delegate scaffold to `code` agent with:
```
TASK: Phase 1 — Project Scaffold via code_execution_tool

🔴 USE `code_execution_tool` to run the scaffold command with a pinned version.
Example: npx create-next-app@15.1.0 . --typescript --tailwind --eslint --app --use-npm --skip-install
DO NOT use @latest. Always pin the exact version from docs/framework-research.md.

After scaffolding, create .npmrc with legacy-peer-deps=true, then run npm install --legacy-peer-deps.
Install additional project-specific packages (e.g. npm install lucide-react clsx @prisma/client --legacy-peer-deps).

After init, execute the commands IN ORDER using code_execution_tool.

🔴 POST-SCAFFOLD VERIFICATION (MANDATORY before returning success):
After ALL scaffold commands complete, run this EXACT verification:

  ls package.json next.config.* tailwind.config.* src/app/page.tsx node_modules/.package-lock.json

ALL of these files MUST exist. If ANY are missing:
1. The scaffold FAILED — do NOT report success
2. Diagnose why (check terminal output for errors)
3. Retry with a different approach
4. Only report success when ALL files are verified on disk

DO NOT trust your memory of what was created. VERIFY with ls.

🔴 PHASE 1 COMPLETION CRITERIA (STOP when ALL met):
Phase 1 is DONE when these infrastructure checks pass:
1. All scaffold files exist on disk (verified by ls)
2. `npm install` completed without errors
3. `npx next build` exits with code 0 (no TypeScript errors, no missing modules)
4. `package.json` contains all packages imported in src/ files
5. Project name is NOT a scaffold default (my-app, scaffold-temp)
6. README.md does NOT contain scaffold boilerplate (Create Next App, bootstrapped with)

🔴 PHASE 1 TDD — TEST INFRASTRUCTURE, NOT CONTENT:
TDD applies during Phase 1 — but test INFRASTRUCTURE, not scaffold content.

✅ CORRECT Phase 1 tests (infrastructure verification):
- Test that `npm run build` exits 0
- Test that all import aliases resolve
- Test that .env has entries for every process.env.X in src/
- Test that project name is not scaffold default
- Test that README has no scaffold boilerplate

❌ WRONG Phase 1 tests (feature content — DO NOT write these):
- home.test.tsx testing scaffold page markup
- Component render tests for placeholder pages  
- Snapshot tests of boilerplate content

🔴 PHASE 1 BOUNDARIES:
- ❌ DO NOT write feature/content tests (.test.tsx for page markup)
- ❌ DO NOT attempt to "perfect" the scaffold — get infra tests green and STOP
- ❌ DO NOT spend more than 25 iterations on this task

When the 6 infrastructure checks pass, call `response` IMMEDIATELY.
Do NOT keep iterating to "verify one more thing."
```

### 🔴 POST-SCAFFOLD Cleanup (MANDATORY after scaffold)

After scaffold completes, delegate a cleanup task to `code` agent:
```
TASK: Post-Scaffold Cleanup — Replace ALL boilerplate content

1. Root page/entry file — Replace with project's actual landing page content
2. Layout/metadata files — Update page title, meta description, favicon
3. Global styles — Remove scaffold-specific styles
4. Public assets — Remove default scaffold images/logos
5. Package manifest — Verify `name` field matches project name
6. Dev script stability — Apply documented workarounds

VERIFICATION: After cleanup:
  - Search for boilerplate strings. Count MUST be 0.
  - Verify package.json `name` is not a scaffold default.
  - Start dev server and confirm it runs without panics.
```

**ENFORCEMENT**: Do NOT proceed to Phase 2 until scaffold boilerplate is confirmed removed.

#### Scaffold Marker Convention (F-6, ITR-11)
Every scaffold file created in Phase 1 MUST include a marker comment at the top:
- TypeScript/JavaScript: `// SCAFFOLD: Replace in Phase 3 — this is placeholder content`
- CSS: `/* SCAFFOLD: Replace in Phase 3 — this is placeholder content */`
- Markdown: `<!-- SCAFFOLD: Replace in Phase 3 — this is placeholder content -->`

This ensures:
1. The code agent in Phase 3 knows to REPLACE the file, not build on top
2. The stub_detection gate catches any scaffold markers that survive Phase 3

### 🔴 POST-SCAFFOLD `.env.example` Generation (MANDATORY)

After scaffold + cleanup, delegate `.env.example` creation to `code` agent:
```
TASK: Create .env.example from integration requirements

1. Read the prompt/requirements for ALL integration mentions
   (Stripe, Resend, OpenRouter, Prisma, Google Places, Perplexity, etc.)
2. Create a .env.example file in the project root with ALL required env vars
3. Use descriptive placeholder values (e.g., STRIPE_SECRET_KEY=sk_test_xxx)
4. Include comments grouping vars by service
5. If a .env file already exists, ensure .env.example is a superset

VERIFICATION:
  - .env.example exists and is non-empty
  - Every integration mentioned in the prompt has corresponding env var(s)
  - .env.example is listed in .gitignore exceptions (tracked, not ignored)
```

**ENFORCEMENT**: The `.env.example verification` gate (order 0.07) will block
if required env vars are missing. Create this file BEFORE Phase 2.

### 🔴 NO GIT PUSH UNTIL PHASE 5.5 (MANDATORY)

Do **NOT** run `git push`, `git commit`, or any version control publication
during Phases 1-5. All quality gates, verification, and content checks MUST
pass before any code is pushed to a remote repository.

**Why**: Pushing before quality gates creates divergence between local and
remote code. The remote may have broken code while local has fixes.

---

## 🔴 CRITICAL: Service Lifecycle & Safety Rules (MANDATORY — ALL Phases)

These rules apply to ALL phases. Violations cause cascading infrastructure failures.

### Dev Server Management — USE `services_mgt` EXCLUSIVELY

**NEVER run `npm run dev`, `npx next dev`, `npx vite`, or `node server.js` directly.**
These commands block terminals, create orphan processes, miss port allocation,
and break downstream verification gates.

**ALWAYS use the `services_mgt` tool:**
```json
{
    "tool_name": "services_mgt",
    "tool_args": {
        "action": "start_service",
        "name": "<project_name>",
        "command": "npm run dev",
        "project_dir": "<project_path>"
    }
}
```

**Why (from production incidents):**
- `npm run dev &` creates an orphan process with no lifecycle management
- The `_dev_server_started` flag never gets set → Browser UAT gate fails with false 404s
- Port conflicts occur when multiple agents start servers on random ports
- `services_mgt` handles: port allocation (5100+), health checks, process tracking, and clean shutdown

### Permission Management — NEVER use `chmod -R` with non-executable modes

**NEVER run `chmod -R 644 <dir>` or any recursive chmod that removes execute bits.**
Directories REQUIRE the execute bit (`x`) to be traversable. `644` = `rw-r--r--` = no execute = directories become inaccessible.

**If you need to fix file permissions, use:**
```bash
# Files get 644 (rw-r--r--), directories get 755 (rwxr-xr-x)
find <dir> -type f -exec chmod 644 {} +
find <dir> -type d -exec chmod 755 {} +
```

**Why (from production incidents):**
- `chmod -R 644 .` made ALL directories inaccessible — `cd`, `ls`, `npm install` all failed
- The entire project tree became unrecoverable without corrective `chmod 755` on directories

### File Reading — NEVER use `cat -n` or `cat --number`

**ALWAYS use the `read_file` tool to read files.** NEVER use `cat -n`, `cat --number`,
or similar in `code_execution_tool`. These prepend line numbers that corrupt files when written back.

**Why (from production incidents):**
- Agent read file with `cat -n` → output had `1: import ...`, `2: export ...`
- Agent then used that output to "modify" the file → wrote line numbers into source code
- The file compiled with syntax errors, breaking the entire build

---

## Phase 2: Architect Design (Exhaustive)

Delegate to `architect` agent with ALL requirements from Phase 0.
**🔴 SCOPE**: This delegation covers ONLY Phase 2 (architecture). Phases 2.3, 2.5, 2.6, 2.7 are SEPARATE delegations to DIFFERENT agents — tell the architect: "Your scope is Phase 2 ONLY. Do NOT claim completion of Phases 2.3-2.7."
- **🔴 MANDATORY**: Include `docs/framework-research.md`, `content_manifest.json`, `decomposition_index.json`, and `requirements_ledger.json`
- Architect MUST specify framework and dependencies — scaffolding is executed via `code_execution_tool` with pinned versions (NEVER `@latest`)
- Shared type contracts — single types file for ALL interfaces
- **🔴 Integration Module Mandate**: Each external integration gets a SEPARATE module
- **🔴 SDK Coherence Mandate**: For every integration SDK mentioned in the prompt (Stripe, Resend, Prisma, etc.), verify: (a) it is listed as a dependency in `package.json`, (b) it is actually imported in source code, (c) the import path is correct for the installed version. The `verify_sdk_completeness` gate enforces this — failures here mean the integration was declared but never wired.
- **🔴 Product Description = Build Requirements**: Present-tense product descriptions in the user prompt ("captures reviews", "routes leads", "scrapes listings") are **functional capabilities that MUST be implemented**. They are NOT marketing copy. Extract every action verb and treat it as a build requirement with its own REQ-ID in `requirements_ledger.json`.
- **🔴 BDD Mandate**: Architect produces `bdd-scenarios.md` with Gherkin acceptance criteria for ALL features
- Architect specifies page structure and component hierarchy — but does NOT generate visual mockups (that is Phase 2.3)
- **🔴 Prisma Schema Creation Mandate (RCA-ITR12 SS-1/SS-2)**: When the architecture includes a database, the architect MUST create the actual `prisma/schema.prisma` file with all model definitions — not just describe them in `docs/architecture.md`. Use `save_deliverable` with `output_path="prisma/schema.prisma"` to write the complete schema. **WHY**: The `codebase_state_injector` scans `prisma/schema.prisma` before every Phase 3 delegation and injects model names into the code agent's context with "You MUST use these EXACT names." If the schema doesn't exist until Phase 3 Wave 1 creates it, early Phase 3 delegations get `models=0` — causing code agents to invent wrong model names (e.g., `prisma.business` instead of `prisma.prospect`), which creates runtime crashes. By creating the schema in Phase 2, ALL Phase 3 delegations receive correct model names via automatic injection.

### 🔴 Decomposition Granularity Mandate (ITR-39 FIX-4 — CRITICAL)

Each decomposition phase MUST have **at most 5 REQ-IDs**. Phases with more than 5 requirements overload the code agent's scope, producing incomplete implementations.

Rules:
1. **1 page = 1 phase**: Each frontend page/route gets its own decomposition phase
2. **1 API group = 1 phase**: Related API routes (e.g., all /api/reviews/* endpoints) are ONE phase
3. **Shared UI = 1 phase**: Navigation, layout, and shared components are ONE phase
4. **If a phase has >5 REQ-IDs**: Split it into sub-phases by feature group
5. **The orchestrator will reject decomposition_index.json** if any phase has >5 REQ-IDs and re-delegate to the architect for splitting

The root cause (ITR-39): When 8+ REQs are in one phase, the code agent receives all of them simultaneously. It attempts to implement all 8, runs out of context/budget, and delivers 3/8 complete. The other 5 are stubs or missing. Splitting to ≤5 per phase gives the code agent a tractable scope.

**Anti-pattern (WRONG):**
```json
{
  "phase": "3.1",
  "name": "Dashboard + Reviews + Pipeline + Analytics",
  "req_guids": ["REQ-001", "REQ-002", "REQ-003", "REQ-004", "REQ-005", "REQ-006", "REQ-007", "REQ-008"]
}
```

**Correct (split by page/feature group):**
```json
[
  {"phase": "3.1", "name": "Dashboard Page", "req_guids": ["REQ-001", "REQ-002"]},
  {"phase": "3.2", "name": "Reviews Page + API", "req_guids": ["REQ-003", "REQ-004"]},
  {"phase": "3.3", "name": "Pipeline Page + API", "req_guids": ["REQ-005", "REQ-006"]},
  {"phase": "3.4", "name": "Analytics Page", "req_guids": ["REQ-007", "REQ-008"]}
]
```



### 🔴 Expanded BDD Scenario Extraction (ALL Requirement Categories — MANDATORY)

The architect MUST extract BDD scenarios for ALL requirement categories — not just feature behavior. The existing `bdd_scenarios.py` validator verifies these mechanically. Categories:


**BRANDING** (every project):
```gherkin
Feature: Branding
  Scenario: Project identity in metadata
    Given the layout file
    Then page contains "{project_name from manifest}"
    And page does NOT contain "Create Next App"
    And page does NOT contain "generated by"
```

**COMPLIANCE** (when email/payment/user-data is involved):
```gherkin
Feature: CAN-SPAM Compliance
  Scenario: Email has unsubscribe mechanism [COMPLIANCE: BLOCKING]
    Given email template is generated
    Then page contains "unsubscribe"
    And page contains physical mailing address

Feature: PCI-DSS Compliance
  Scenario: Payment uses Stripe Elements [COMPLIANCE: BLOCKING]
    Given payment page exists
    Then code imports "@stripe/stripe-js"
    And page does NOT contain "<input type='text' name='card'"
```

**UI/UX** (when prompt specifies colors/mode):
```gherkin
Feature: Design System
  Scenario: Color scheme matches prompt
    Given the global stylesheet
    Then CSS contains "--background" with value "{hex from prompt}"
    And CSS contains "color-scheme" with value "dark"
```

**WIRING** (for each API-to-frontend connection):
```gherkin
Feature: API Integration
  Scenario: Dashboard calls reviews API
    Given the dashboard page
    Then page contains "fetch"
    And page contains "/api/reviews"
```

**PAYMENT/SUBSCRIPTION** (when Stripe or payment integration is present — RCA-ITR3 F-3):
```gherkin
Feature: Subscription Flow
  Scenario: New user subscribes via Stripe checkout [PAYMENT: BLOCKING]
    Given a visitor on the pricing page
    When they click the subscription CTA
    Then they are redirected to the Stripe checkout URL from content_manifest
    And after payment, they receive dashboard access

  Scenario: Stripe webhook processes subscription
    Given a successful Stripe checkout event
    When the webhook fires
    Then the user's subscription status is updated
    And they can access protected dashboard routes
```

Compliance scenarios MUST be tagged `[COMPLIANCE: BLOCKING]` in `bdd-scenarios.md`. Payment scenarios MUST be tagged `[PAYMENT: BLOCKING]`. If any BLOCKING scenario fails validation, the build cannot proceed to delivery.

### 🔴 POST-PHASE-2 GATE — DO NOT CALL RESPONSE (RCA-311)

**After the architect returns from Phase 2, you are NOT done.** The architect completes ONLY Phase 2. The following phases MUST be delegated SEPARATELY before you may call `response`:

1. **Phase 2.3** → delegate to `frontend` agent (mockups, design tokens, component specs)
2. **Phase 2.5** → orchestrator validates decomposition index (self-task)
3. **Phase 2.6** → delegate to `researcher` (cross-check architect ↔ research versions + design coherence)
4. **Phase 2.7** → delegate to `code` agent (skeleton validation + BDD enrichment)

**DO NOT call `response` until ALL of the above phases are completed.** If the architect claims "I completed Phases 2.0-2.7", IGNORE that claim — the architect can only complete Phase 2. Phases 2.3-2.7 require different agent profiles.

**Verification**: Before calling `response`, check that these files exist:
- `design-tokens.json` (Phase 2.3)
- `component-spec.md` (Phase 2.3)
- `docs/ux-flows.md` (Phase 2.3)
- `docs/design-mockups/*.png` (Phase 2.3 — at least 3 mockups)
- `docs/design-cross-check.md` or version cross-check (Phase 2.6)
- `prisma/schema.prisma` (Phase 2 — if architecture includes a database)

If ANY are missing, the corresponding phase has NOT been executed. Delegate it before proceeding.

---

## 🔴 Phase 2.3: UI/UX Design — Mockups + Tokens + Specs (MANDATORY)

**Profile**: `frontend` (designer) — this agent produces design artifacts ONLY, never code.

### 🔴 MANDATORY Skill Reference: `ui-design-first`
The `frontend` agent MUST follow the **`ui-design-first` skill** (`skills/ui-design-first/SKILL.md`) — its 7-step pipeline is the canonical workflow for this phase:
1. Gather Design Context (Step 1)
2. Generate Design System Reference Card (Step 2)
3. Generate Per-Page Mockups (Step 3)
4. **Cross-Page Consistency Audit** (Step 4 — CRITICAL, must not be skipped)
5. Extract Asset Requirements (Step 5)
6. Generate Required Assets (Step 6)
7. Code with Mockup Alignment (Step 7 — handed to code agent in Phase 3)

The delegation message below MUST reference this skill explicitly so the designer follows all 7 steps.

Delegate to `frontend` agent with architect's design doc:
```
TASK: Phase 2.3 — UI/UX Design System & Mockups

🔴 MANDATORY: Follow the `ui-design-first` skill pipeline (skills/ui-design-first/SKILL.md).
Execute ALL 7 steps IN ORDER. Do NOT skip the Cross-Page Consistency Audit (Step 4).

You are the UI/UX Designer. Create the visual design system for this project.

## Context
- Read the architect's design document for page structure and component hierarchy
- Read `content_manifest.json` for all literal values (names, prices, URLs, copy text)
- Read `requirements_ledger.json` for feature requirements

## Deliverables (ALL MANDATORY)

### 🔴 TOOL USAGE — YOU DO NOT HAVE `write_to_file`
Your available tools for creating deliverables are:
- **`generate_image`** — creates and saves PNG mockups (the tool writes the file for you)
- **`save_deliverable`** — saves JSON and Markdown artifacts to the project
- **DO NOT attempt `write_to_file` or `apply_diff`** — they are not in your toolset

### 1. Design System Card
Use `generate_image` to create `00-design-system.png` showing:
- Complete color palette with hex values
- Typography scale (h1-h4, body, caption)
- Button styles, card components, input fields
- Spacing system visualization

### 2. Per-Page Mockups
For EVERY page in the architect's design doc, use `generate_image`:
- Generate a photorealistic mockup at 1440px width
- **🔴 NO DEVICE FRAMES**: Generate ONLY the page interface itself — NO browser chrome, NO laptop/phone/tablet frames, NO address bars, NO window decorations (dots, minimize/maximize buttons). Just the pure UI content on a clean background.
- Include REAL content from content_manifest.json (not lorem ipsum)
- Name them `01-homepage.png`, `02-dashboard.png`, etc.
- Follow the **Image Context Chain**: first mockup uses the design system card as `reference_image`, subsequent mockups use the first mockup as `reference_image`
- Run a cross-page consistency audit — regenerate inconsistent pages

### 3. Design Tokens (design-tokens.json)
Use `save_deliverable` with `output_path="design-tokens.json"` to save a machine-readable JSON file at the project root.
The code agent will consume this file to implement the CSS/theme.
Include: colors, typography, spacing, borderRadius, shadows, gradients, breakpoints.
**Note**: The `.json` extension ensures YAML frontmatter is automatically skipped — the file will be pure JSON.

**🔴 Design Token Independence Rule**: `design-tokens.json` MUST be a **standalone file** with zero dependencies on other design artifacts. The delegation brief system's `_build_how_section()` in `delegation_brief.py` reads this file programmatically and injects it into Phase 3 delegations. If design-tokens.json references external files (e.g., "see component-spec.md for details"), the injection will produce incomplete context. ALL token values (hex codes, rem values, font names) must be self-contained in this file.

### 🔴 Prompt Color Values → Token Constraints (MANDATORY)

When the user prompt specifies explicit color values (hex codes, color names,
"dark mode", "light mode"):

1. Call `resolve_literals` with category "design" to capture these as constraints
2. Pass the resolved colors as HARD INPUTS to design token generation
3. `design-tokens.json` MUST use prompt-specified colors as primary palette
4. The architect MUST include BDD scenarios that verify these colors appear
   in the CSS (e.g., `Then CSS contains "--background" with value "#0a0a0f"`)

The token generator MUST NOT generate a generic palette when the prompt
specifies colors. Prompt colors override defaults.

### 4. Component Specification (component-spec.md)
Use `save_deliverable` with `output_path="component-spec.md"` to save this Markdown file at the project root. For EVERY component visible in mockups, document:
- Name, purpose, which page(s) it appears on
- Props (name, type, required, description)
- Visual spec referencing token paths (e.g., `tokens.colors.primary.500`)
- Responsive behavior at each breakpoint
- Interactive states (hover, active, disabled, loading)
- Component hierarchy (parent/child relationships)

### 5. UX Flow Documentation (docs/ux-flows.md)
Use `save_deliverable` with `output_path: "docs/ux-flows.md"` to save this Markdown file.
Document the **5 core user journeys** for this project:
- **Signup/Onboarding**: New user → Signup/Checkout → Dashboard
- **Discovery**: Discovery → Filter → Qualify → Pipeline
- **Outreach**: Pipeline → Compose → Queue → Monitor
- **Review Response**: Review alert → AI response → Approve/Send
- **Self-service Audit**: Visitor → Self-service audit → CTA

For EACH journey, document:
- Entry point (how the user arrives)
- Step-by-step flow with page transitions
- Key decision points and branching logic
- Success state and error/fallback states
- Which components and API routes are involved at each step

Adapt the 5 canonical journeys to match the specific project's domain.
If a journey doesn't apply (e.g., no outreach feature), document the closest equivalent flow.

## 🔴 CRITICAL RULES
- You are a DESIGNER — do NOT write source code (.tsx, .jsx, .css, .ts, .js)
- **Tools**: Use `generate_image` for PNGs, `save_deliverable` for JSON/MD. Do NOT use `write_to_file`.
- **Tool priority**: Use `generate_image` for ALL mockup generation. `a2ui_generate` is available but NOT needed during multiagentdev orchestration — it adds unnecessary overhead. Stick to `generate_image` with the Image Context Chain pipeline.
- Use framework-agnostic CSS values (rem, px, hex) — NOT Tailwind utilities
- ALL design-tokens.json values MUST match what's visible in your mockups
- Your artifacts are the CONTRACT that the code agent implements from
```

**ENFORCEMENT**: Do NOT proceed to Phase 3 until ALL of these exist:
1. Design system card PNG (via `generate_image`)
2. At least 1 per-page mockup PNG (via `generate_image`)
3. `design-tokens.json` (via `save_deliverable` with `output_path="design-tokens.json"`)
4. `component-spec.md` (via `save_deliverable` with `output_path="component-spec.md"`)
5. `docs/ux-flows.md` — UX flow documentation (via `save_deliverable`)

### Phase 2.4: Wire Design Tokens into Framework Config (F-6 ITR-16 — MANDATORY)

After Phase 2.3 produces `design-tokens.json`, delegate to `code` agent to wire tokens into the framework:
```
TASK: Phase 2.4 — Wire Design Tokens into tailwind.config.ts

Read design-tokens.json and update tailwind.config.ts to consume the design tokens.

1. Read design-tokens.json — extract colors, typography, spacing, borderRadius, shadows
2. Update tailwind.config.ts `theme.extend` with the token values:
   - Map color tokens to Tailwind color names (e.g., primary.50-900)
   - Map typography tokens to fontFamily, fontSize
   - Map spacing tokens to spacing scale
   - Map shadow tokens to boxShadow
3. Update globals.css :root with CSS custom properties matching the tokens
4. Verify the dark/light mode matches the design tokens (if tokens specify dark theme,
   ensure globals.css uses dark background colors)

VERIFICATION:
- tailwind.config.ts extend block is non-empty
- At least 3 token categories (colors, typography, spacing) are present
- CSS custom properties in :root reference token values
```

**ENFORCEMENT**: Do NOT proceed to Phase 2.5 until `tailwind.config.ts` has a non-empty `extend` block with design token values.

---

### Phase 2.5: Validate & Own Decomposition Index + Schema Lock (MANDATORY — after architect returns)

Read back, validate, and take final ownership of `decomposition_index.json`. Verify structure, check for circular dependencies, ensure requirement coverage. Your version supersedes everything.

#### 🔴 Step 1: Requirement Assignment Coverage Check (RCA-362 — BLOCKING)

**BEFORE validating structure**, call the `requirements` tool to verify every extracted requirement has a decomposition phase assignment:

```json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "check_coverage"
    }
}
```

**If the result says `FAIL`**: You MUST create decomposition phases for every unassigned requirement listed in the output. Add them to `decomposition_index.json` via `save_manifest`. Re-run `check_coverage` until it returns `PASS`.

**If the result says `PASS`**: Proceed to Step 1.5.

**Why this exists (RCA-362)**: In the MSR_Ph3 smoke test, 85 requirements were extracted but the core product feature (review capture flow) was never assigned to any decomposition phase. The orchestrator proceeded to Phase 3 without noticing. The E2E agent eventually caught it — but by then, 41% of delegations had been wasted on retries. This deterministic check catches the gap at planning time (L1) before any code is written.

#### 🔴 Step 1.5: Shared UI Component Check (RCA-15 RC-1 — BLOCKING)

**AFTER requirement coverage passes**, check if the decomposition includes shared UI components when the app has multiple pages:

1. Count the number of DISTINCT page routes in `decomposition_index.json` (phases with page/route-related names or requirements)
2. **If ≥2 pages exist**: Verify at least one phase includes a task for shared navigation/layout/navbar/sidebar
3. **If NO shared navigation task exists**: You MUST add one. Create a decomposition phase for "Shared UI Shell" that includes:
   - Root layout component with shared navigation
   - Navigation links to ALL routes in the decomposition
   - Consistent header/footer across all pages

**Check these keywords in phase descriptions**: `navigation`, `navbar`, `sidebar`, `layout`, `ui shell`, `shared component`, `header`, `menu`

**Why this exists (RCA-15 RC-1)**: In the MainStreet smoke test, the architect specified navigation (mandate at L240 in architect prompt), but the decomposition collapsed 10 tasks into 1 Phase 3 with 28 requirements. No individual task was responsible for the shared navigation component. The code agent built all pages independently — resulting in an app where users could not navigate between pages. This check catches missing shared UI components at planning time.

#### 🔴 Step 1.7: Schema Lock Validation (GAP-1 — SS-4 — BLOCKING)

**AFTER shared UI check**, the `_build_schema_lock_section()` function in `python.helpers.delegation_brief` automatically scans for existing TypeScript types/interfaces via `check_type_coherence()`. If types exist, subsequent Phase 3 delegations receive a **SCHEMA LOCK** section listing exact type names that code agents MUST reuse.

**This is automatic** — the orchestrator does NOT need to call it manually. The `call_subordinate` injection pipeline handles it. However, the orchestrator should verify that the architect's `prisma/schema.prisma` and `types.ts` are consistent before proceeding:

1. If both `prisma/schema.prisma` and a shared types file exist, verify model names match interface names
2. If conflicts exist (e.g., Prisma uses `Prospect` but types.ts uses `Lead`), re-delegate to the architect to resolve BEFORE Phase 3

**Why this exists (SS-4)**: Multi-wave code delegations independently introduced conflicting type names (`Prospect`, `Lead`, `Business` for the same entity) → 84 TypeScript build errors. The Schema Lock prevents this entire class of error by mandating type name reuse.

#### Step 2: Structural Validation

Verify `decomposition_index.json` structure:
- All phase IDs match the canonical table (0.1, 0, 0.5, 0.5b, 1, 2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3, 3.5, 3.8, 3.8.1, 3.9, 4, ...)
- No circular dependencies between phases
- Every phase has at least one `req_guid` linking it to a requirement
- Implementation phases (3.x) have `req_guids` arrays that reference actual REQ-IDs from the ledger

#### Step 3: Take Ownership

Save the validated decomposition via `save_manifest`. Your version supersedes the architect's.

### Phase 2.6: Architect ↔ Researcher Cross-Verification Loop (MANDATORY)

**🔴 PREREQUISITE**: Phase 2.6 MUST NOT execute until Phase 2.3 has completed and its deliverables exist on disk:
- `design-tokens.json` must exist in project root
- `component-spec.md` must exist in project root
- `docs/design-mockups/` must contain ≥2 PNG files

If these artifacts are missing, delegate Phase 2.3 FIRST, then return to Phase 2.6.

Three verification steps must pass before Phase 2.7:

#### Step 1: Version Compatibility
Verify architect's version selections against runtime constraints from Phase 0.5. If mismatches found, loop researcher → architect until all versions are confirmed compatible (max 2 cycles).

#### Step 2: Design Coherence Validation (RCA-310 FIX-1)
Compare architect's design direction (`docs/architecture.md` — color mode, typography, layout) with frontend's design tokens (`design-tokens.json`). Check:
- **Color mode**: If architecture says "light mode", design tokens MUST use light backgrounds (`#ffffff`, `#f8fafc`, etc.) — NOT dark (`#0f172a`, `#1e293b`)
- **Typography**: Font family in tokens must match architecture spec
- **Primary colors**: Accent/primary colors must match

If mismatches found, re-delegate to frontend agent with explicit correction instructions (max 1 cycle).

#### Step 3: Version Pin Verification (RCA-310 FIX-4)
Compare installed `package.json` dependency versions with versions recommended in `docs/framework-research.md`. If any dependency differs by a MAJOR version from the researched recommendation, the code agent must pin to the researched version. Log warnings for minor version differences but don't block.

---

## Phase 2.7: Skeleton → Architect BDD Enrichment Handoff (MANDATORY — after architect returns)

> **F-0 (ITR-11)**: Test skeleton is now AUTO-GENERATED deterministically during Phase 0 (`_handle_init`).
> Every REQ-ID in the ledger gets a skeleton entry automatically — the LLM does NOT generate this manually.
> BDD skeleton is also auto-generated for web projects.

After the architect completes Phase 2, verify that the architect's `bdd-scenarios.md` covers all REQ-IDs from the test skeleton:

1. Read `docs/test-skeleton.json` — get all REQ-IDs where `bdd_needed: true`
2. Read `docs/bdd-scenarios.md` — scan for REQ-ID references
3. **Gap check (L1 — `check_bdd_coverage()`)**: Coverage MUST be ≥ **90%** of `bdd_needed: true` REQs. If below 90%, re-delegate the architect to add the missing BDD scenarios. Use `from python.helpers.skeleton_generator import check_bdd_coverage` for deterministic validation.
4. **🔴 Template Quality Check (F-1 ITR-16)**: After `save_bdd_scenarios`, check the response for `BDD TEMPLATE QUALITY FAILED`. If >80% of scenarios use generic template language (e.g., "the feature implementation handles the core use case correctly"), the architect MUST re-enrich them with domain-specific content.
5. **🔴 BDD Quality Blocking Gate (GAP-2 — SS-10/SS-11)**: After enrichment, two additional L1 checks run automatically via the completion gate:
   - `check_bdd_quality_blocking()` (`python.helpers.orchestrator_gate_common`) — blocks completion if BDD quality score is below threshold (no longer advisory-only)
   - `check_bdd_literal_mismatches()` (`python.helpers.orchestrator_gate_common`) — detects manifest-to-BDD price/URL/name mismatches and produces write-to-consumer blocking verdicts
   These checks were previously write-only (detected but never consumed). They now produce blocking gate failures that prevent Phase 3 from starting with low-quality BDD scenarios.

### 🔴 BDD Enrichment Requirements (F-1 ITR-16 — MANDATORY)

The architect MUST replace generic template language with **domain-specific Given/When/Then** using values from `content_manifest.json`. The `save_bdd_scenarios` tool detects generic templates and will reject them.

**❌ WRONG (generic template):**
```gherkin
Scenario: Verify feature requirement [REQ-001]
  Given the feature requirement [REQ-001] is implemented
  When the source file is inspected
  Then the feature implementation handles the core use case correctly
```

**✅ CORRECT (domain-specific enrichment):**
```gherkin
Scenario: Discovery engine finds HVAC businesses by zip code [REQ-001]
  Given a zip code "01844" and vertical "HVAC"
  When the discovery scan runs via POST /api/discovery
  Then it queries Perplexity API for businesses in that area
  And it cross-references with Google Places API
  And it returns prospects with name, rating, review count, contact info
  And prospects with 3.0-4.3★ ratings and 5-50 reviews are scored highest
```

**Enrichment checklist** (apply to EACH scenario):
- Replace "the feature requirement is implemented" with the SPECIFIC behavior being tested
- Replace "the source file is inspected" with the ACTUAL trigger (API call, user action, cron job)
- Replace "handles the core use case correctly" with MEASURABLE outcomes using manifest values
- Include specific prices, URLs, names, API endpoints from `content_manifest.json`
- Include specific error cases relevant to THIS feature (not generic "empty input, invalid data")

**Maximum 1 re-delegation cycle.** If the architect still doesn't cover all REQs, log a warning and proceed — the verification matrix will catch it as WEAK/UNTESTED.

#### Skeleton Route Coverage (RCA-310 FIX-5)
After skeleton validation, verify that EVERY API route defined in `docs/architecture.md` has a corresponding `route.ts` skeleton file in `src/app/api/`. If the architecture spec defines N API routes, the skeleton must have N `route.ts` files (even if they are stubs with `// TODO: Phase 3 Implementation`). Empty directories without `route.ts` files are NOT acceptable — they cause missing-module errors during Phase 3 implementation.

---

### Phase 2.8: TDD Skeleton Expansion (MANDATORY — after BDD enrichment)

**After BDD quality gates pass**, the infrastructure automatically generates concrete, FAILING test file stubs for ALL layers. This is invoked by `generate_tdd_stubs()` (`python.helpers.tdd_generator`) + `generate_wiring_test_stubs()` (same module).

#### What Gets Generated

| Layer | Test File | Framework | Source |
|-------|-----------|-----------|--------|
| **Unit** | `test_unit_requirements.test.ts` | vitest | REQ-IDs with test_type=unit |
| **Integration** | `test_integration_requirements.test.ts` | vitest | REQ-IDs with test_type=integration |
| **E2E** | `test_e2e_requirements.test.ts` | vitest | REQ-IDs with test_type=e2e |
| **Literal** | `test_literal_requirements.test.ts` | vitest | REQ-IDs with test_type=literal + manifest values |
| **Design Tokens** | `test_design_tokens.test.ts` | vitest | CSS custom properties from `design-tokens.json` |
| **Wiring** | `test_wiring_api_completeness.test.ts` | vitest | Routes from `navigation-map.md` |

#### How It Works

1. `generate_test_skeleton()` maps each REQ-ID to a test type (unit/integration/e2e/literal/config)
2. `generate_tdd_tests()` expands the skeleton into language-aware test file specs with:
   - `describe()` / `it()` blocks per requirement
   - Failing assertions (`throw new Error('TODO: Implement test for REQ-XXX')`) or executable literal assertions (`expect(content).toContain('$200/mo')`) 
3. `generate_wiring_test_stubs()` generates API route completeness tests from `navigation-map.md`:
   - Every API endpoint has a describe block verifying the route handler exists
   - Every frontend page has a test verifying the page component exists
4. All test specs are written to `docs/tdd/` AND copied to `src/__tests__/` for test runner auto-discovery

#### Phase 3 Contract

The code agent's job in Phase 3 is to **make these tests PASS** — not to write new tests from scratch. The test stubs define the contract; the code agent writes the implementation that satisfies it.

#### Deliverable Check

The Phase 2.8 deliverable gate verifies that the `docs/tdd/` directory exists with >0 files. If no test specs were generated, the phase has not completed.

#### Why This Exists (GAP-B)

Previously, TDD was aspirational — the delegation message said "write tests first" but no pre-existing test framework constrained the agent. With actual failing test stubs, the code agent has a concrete target: "make these N tests pass." This changes TDD from honor-system to mechanically enforced.

---

## 🔴🔴 Additive-Only Mandate (ALL Post-Build Phases — CRITICAL) 🔴🔴

**Once the project passes its first successful build (`npm run build` exits 0), ALL subsequent
changes to the codebase MUST be ADDITIVE. No rewrites, no refactors, no "start over."**

This mandate applies to ALL phases after the first successful build — including integration
(Phase 4), verification fixes (Phase 5 rework), and iteration (Phase 6).

### Rules:
1. **No file rewrites**: You may NOT replace the contents of a working file with new code.
   Use `replace_in_file` to make surgical edits. `write_to_file` with `overwrite: true` on
   a file that already has working code is a **CRITICAL VIOLATION**.
2. **No component conversions**: Do NOT convert client components to server components (or
   vice versa), do NOT change data-fetching patterns (e.g., switching from `fetch` to direct
   Prisma calls), do NOT reorganize import structures.
3. **No architectural changes**: The architecture was set in Phase 2. Post-build phases
   execute within the architecture — they do NOT redesign it.
4. **Additions are always safe**: Adding new files, adding new functions to existing files,
   adding new CSS classes, adding error handling — all are permitted and encouraged.
5. **Modifications must be surgical**: When fixing a bug or wiring an integration, change
   ONLY the specific lines that need to change. Context lines above and below MUST remain
   identical.
6. **Working code is FROZEN**: If a page/route/component was rendering correctly before
   your current task, it MUST render correctly after. Breaking working code to fix a
   different issue is a **CRITICAL VIOLATION**.

### How the Orchestrator Enforces This:
- Every post-build delegation MUST include: `"🔴 ADDITIVE-ONLY: The build is passing. Your changes
  must be additive — do NOT rewrite working files. Fix ONLY what is described in the failure below."`
- The orchestrator MUST verify the build still passes after each fix delegation returns.
- If a fix delegation breaks the build, the orchestrator MUST revert and re-delegate with
  a smaller scope.

### Scoped Brief Requirement (MANDATORY for ALL fix/rework delegations):
When delegating fixes (post-build), the orchestrator MUST provide a **clear, scoped brief** that includes:
1. **The exact error/failure**: Quote the error message verbatim — do NOT paraphrase.
2. **The specific file(s) to fix**: List at most 3 existing files by path.
3. **Explicit instruction that working files are FROZEN**: "All files NOT listed above are
   FROZEN — do NOT read, modify, or reference them."
4. **The expected outcome**: What does "fixed" look like? (e.g., "build passes", "page renders",
   "API returns 200").

Delegating a fix without all 4 items is a **CRITICAL VIOLATION** — it gives the code agent
ambiguous scope, which historically leads to destructive rewrites.

---

## 🔴 Proof-Based TDD/BDD Enforcement Chain (ALL Implementation Phases)

The orchestrator (multiagentdev) is a **read-only proof checker**. It never writes code — it delegates work and reads proof objects to determine if delegations succeeded. Every subordinate MUST produce verifiable evidence.

### The 5-Check Proof Pipeline

After each `call_subordinate` return, the verification pipeline automatically runs 5 deterministic checks per linked requirement:

| Check | What | How |
|-------|------|-----|
| `no_stubs` | No TODO/FIXME/`return []`/placeholder | Regex scan of evidence files |
| `has_logic` | Real implementation (>3 non-comment lines) | AST/line-count analysis |
| `contract_match` | Literal values from prompt present in code | Content manifest grep |
| `build_passes` | `npm run build` exits 0 | Last build status |
| `test_passed` | ALL test suites pass | Aggregate of npm test + pytest + BDD |

**Proof status**: All 5 pass = `PASS`. Any hard fail = `FAIL`. Tests not yet run = `PARTIAL`.

### Delegation TDD/BDD Template (USE IN ALL Phase 3/4 DELEGATIONS)

Every `call_subordinate` message MUST include:

```
## TDD Mandate
1. Write failing tests FIRST (pytest for Python, vitest/jest for JS/TS)
2. Implement code to make tests pass
3. Run ALL tests before responding: `npm test && pytest` (as applicable)
4. When MODIFYING existing test files, use `replace_in_file` to change specific
   test cases — do NOT rewrite the entire test file with `write_to_file`.
   Read the file first with `read_file` to find the exact sections to modify.
5. Include test results in your response:
   ✅ Test Results: X/Y pass (npm test: A/B, pytest: C/D)
   ✅ Stub Check: 0 TODO/FIXME found

## BDD Acceptance Criteria
**DO NOT use generic examples.** Read `docs/bdd-scenarios.md` and include the ACTUAL
GIVEN/WHEN/THEN scenarios relevant to THIS work package's REQ-IDs.

For each REQ-ID assigned to this work package:
1. Find the matching `## Feature: ... [REQ-XXX]` section in `docs/bdd-scenarios.md`
2. Copy the exact GIVEN/WHEN/THEN clauses into this delegation
3. If no BDD scenario exists for a REQ-ID, flag it as a gap

Encode these as test cases — not just documentation.
The code agent MUST write failing tests for each THEN clause BEFORE implementing.

## Self-Check Before Responding
Before calling response tool:
1. Run: `grep -rn "TODO\|FIXME\|return \[\]" src/` — must return 0 results
2. Run: `npm test` (or `pytest`) — all must pass
3. If failures exist → fix them FIRST, don't deliver broken code

## 🔴 Environment & Secrets Context (RCA-362 F-10 — MANDATORY)
Read the project's `.env.example` file and include ALL relevant env var names
in your delegation message. The code agent MUST know which API keys are available
so it can write correct integration code (not placeholder/mock values).

For each integration in this work package's scope, include:
- The env var NAME (e.g., `STRIPE_SECRET_KEY`, `RESEND_API_KEY`)
- Its PURPOSE (e.g., "Stripe checkout session creation")
- Where it's used (e.g., "src/app/api/stripe/route.ts")

Do NOT include actual secret VALUES — only names and purposes.
The code agent reads the `.env` file on disk to get the actual values.
```

### E2E Agent as Independent Verifier

The `e2e` agent runs AFTER implementation is complete (all `code` agent work packages). It is the **independent prover**:

```
TASK: E2E Verification — Full Test Suite Aggregation

Run ALL test suites and report aggregate results:
1. `npm test` — unit + integration tests (JS/TS)
2. `pytest` — Python backend tests (if applicable)
3. BDD/Cucumber: `npx cucumber-js` (if .feature files exist)
4. Playwright: `npx playwright test` (if playwright config exists)
5. Browser smoke test — visit all routes, check console errors

Report format:
  ✅/❌ npm test: X/Y pass
  ✅/❌ pytest: X/Y pass  
  ✅/❌ BDD: X/Y scenarios pass
  ✅/❌ Browser: X/Y routes render without console errors

If ANY suite fails, list the specific failing tests with file:line.
```

E2E results feed back as the definitive `test_passed` boolean in proof objects. The completion gate blocks delivery until ALL proofs show `PASS` (or circuit breaker fires after 3 blocks).

### Post-Implementation Route Verification (MANDATORY)

After ALL Phase 3 work packages complete and BEFORE dispatching the E2E agent:

1. **Route completeness check**: Run `find src/app -name 'page.tsx' -o -name 'route.ts'` and verify every route in the architect's decomposition has a file
2. **Import resolution check**: Run `npx tsc --noEmit 2>&1 | head -20` — must exit 0 or only show non-critical warnings
3. **URL wiring check**: For each URL in `content_manifest.json`, verify at least one source file references it
4. **Design token consumption**: If `design-tokens.json` exists, verify `globals.css` contains at least one CSS custom property from the token file
5. **Scaffold cleanup**: Verify `package.json` name is NOT 'scaffold-temp', 'my-app', or framework defaults

If ANY check fails, create a targeted `call_subordinate` with profile='code' to fix the specific issue before proceeding to E2E.

---

## Phase 3: Implementation (TDD + BDD) — 🔴 FEATURE-LEVEL DECOMPOSITION

**CRITICAL: Decompose by USER-FACING FEATURE, not by tech layer.**

**🔴 ALL implementation delegates to `code` agent ONLY.** The `frontend` (designer) agent does NOT receive Phase 3 tasks — it already delivered its artifacts in Phase 2.3. The code agent implements BOTH backend and frontend pages.

> **F-5 (ITR-11)**: Do NOT create a separate researcher delegation for each sub-task.
> One researcher delegation per milestone is sufficient. Sub-tasks share the research output.

### 🔴 Delegation Spiral Circuit Breaker (GAP-3 — SS-7/SS-8)

The structural guard system (`_10_structural_guards.py` DETECTOR 10: `cross_delegation_spiral`) automatically detects delegation loops and the intelligent supervisor system manages escalation. After repeated failures to the same profile, the infrastructure triggers a **force-complete protocol** that:
1. Sets `_quality_bypass_signal` to prevent the completion gate from blocking
2. Returns an escalation message to the orchestrator explaining why force-complete was triggered
3. Prevents the orchestrator from entering a 13+ delegation spiral (the #1 Phase 3 failure pattern)

The orchestrator does NOT need to manually check for spirals — the infrastructure detects and breaks them automatically via the structural guards and supervisor escalation chain.

### 🔴 RetryBudgetManager Integration (GAP-7 — P0-1)

The `RetryBudgetManager` (`python.helpers.retry_budget`) enforces hard retry caps per operation type using a forward-only state machine: `ATTEMPT → RETRY(1..N) → ESCALATE → FORCE_COMPLETE`. When an agent exhausts its retry budget:
- It escalates to FORCE_COMPLETE instead of looping
- The budget is profile-aware (code agents have different limits than orchestrators)
- Cumulative failures across operation types are tracked — if total failures exceed a threshold, all operations escalate

This replaces the previous 15 separate loop control keys with a single unified retry state machine.

Agent iteration budgets:
- `code` subordinate: **200 iterations** (~40 tool calls, 1-2 features with TDD)
- `browser` subordinate: **50 iterations** (~10 browser_agent calls)

**Sizing rule**: Each task should be completable in **50-100 iterations**. If a task requires 3+ API routes AND tests, split it.

**🔴 Frontend Page Scope Cap — 1 PAGE PER DELEGATION (MANDATORY)**:

Each `code` delegation for frontend work covers **MAX 1 page + its API route + its components**. NEVER bundle multiple pages into a single delegation. The orchestrator MUST decompose frontend work into per-page tasks:

**Correct decomposition** (3-page app):
- Delegation 3a: "Implement Landing Page (`/`) + `src/app/page.tsx` + hero/nav components"
- Delegation 3b: "Implement Dashboard (`/dashboard`) + `src/app/dashboard/page.tsx` + `GET /api/dashboard`"
- Delegation 3c: "Implement Audit Page (`/audit/[id]`) + `src/app/audit/[id]/page.tsx` + `GET /api/audit/[id]`"

**Wrong decomposition** (causes cancellation):
- ❌ "Implement all frontend pages (Landing + Dashboard + Audit) + API routes + build fix"

**🔴 MANDATORY SCOPE ESTIMATION (BEFORE every `call_subordinate`)**:
Before delegating, count the expected files to create/modify. Use `sequential_thinking` to enumerate:
1. Pages to create (count)
2. API routes to create (count)
3. Components to create (count)
4. Config files to modify (count)
5. **Total file count** = sum of above

**Decision rule**: If total > 5 files → SPLIT the task. Each sub-task should touch 3-5 files max.
If a subordinate was previously cancelled (`[ITERATION_LIMIT]` or `[CANCELLED]`), the re-dispatch MUST cover FEWER files than the original — never the same or more.

### 🔴 Code is a Contractor with Full Context (UPSTREAM TESTABILITY DOCTRINE)

The code agent is a **contractor** — it does NOT think about system architecture, design decisions, or cross-cutting concerns. It receives a **fully-specified job** and executes: BDD → TDD → Code → Test → Fix loops until perfect.

**BUT**: A contractor can only deliver excellent work if given the right plans and context per job. The following context is **automatically injected** via programmatic `inject_*` functions in `call_subordinate.py`. You do NOT need to manually include these — they are auto-injected:

| Auto-Injected Context | Function | What It Provides |
|----------------------|----------|-----------------|
| Codebase state | `inject_codebase_state` | Types, Prisma models, existing files, API routes — prevents duplicate creation |
| BDD scenarios | `inject_bdd_fallback` | GIVEN/WHEN/THEN acceptance criteria per REQ-ID |
| TDD mandate | `inject_tdd_mandate` | Test-first enforcement + delivery standards |
| Researcher output | `inject_researcher_api_docs` | Verified SDK versions, API docs, env var names |
| Prompt passthrough | `inject_prompt_passthrough` | Content manifest values (URLs, names, pricing) |
| Design contract | `_build_how_section` (delegation_brief.py) | Design tokens (colors, typography, spacing) from `design-tokens.json` |
| Component spec | `inject_component_spec` | Full `component-spec.md` content — component hierarchy, props, behavior |
| Mockup references | `inject_mockup_refs` | List of `docs/design-mockups/*.png` files + READ instruction |
| Route map | `inject_route_map` | Full app route table from architect plan + decomposition |
| Model slugs | Layer 3 `resolve_literals` | Verified API identifiers via `model_resolver` |

**You MUST manually include** in each delegation message:
- Exact feature scope (which REQ-IDs this job covers)
- **🔴 TDD/BDD template** (from Proof-Based Enforcement Chain section above)
- BDD acceptance criteria — READ `docs/bdd-scenarios.md` for the ACTUAL GIVEN/WHEN/THEN
- **🔴 Mockup analysis mandate**: "Before writing ANY page component, READ the mockup PNG from `docs/design-mockups/` using `read_file`. Extract colors, layout, hierarchy. Your CSS MUST match."
- **"Run ALL tests (`npx vitest run src/`) and type checks (`npx tsc --noEmit`) before responding. Include output summary in response."**
- **"🔴 STRICT RULE: Do NOT run `npm run build` during implementation. ALL tests MUST be perfectly green before wasting time on a production build. Rely solely on `vitest` and `tsc --noEmit` for iterative checks."**
- "If you run low on iterations, PRIORITIZE completing the current feature."
- **🔴 Environment context** (from the Delegation TDD/BDD Template `.env` section above)
- **🔴 Content Manifest Binding Contract**: For each UI component that displays dynamic data, specify WHICH `content_manifest.json` field(s) it must consume. Example:
  - `HeroSection` → `manifest.hero.title`, `manifest.hero.subtitle`, `manifest.hero.cta_text`
  - `PricingCard` → `manifest.pricing[].name`, `manifest.pricing[].price`, `manifest.pricing[].features`
  - `Footer` → `manifest.contact.phone`, `manifest.contact.email`, `manifest.social_links`
  The code agent MUST `grep` for each manifest field in the completed source to verify binding.

**🔴 Code Quality Anti-Patterns (RCA-362 F-11 — INCLUDE IN EVERY DELEGATION)**:

Include these explicit prohibitions in EVERY `call_subordinate` message:

```
## 🔴 ANTI-PATTERNS (VIOLATIONS CAUSE BUILD FAILURES)

1. **NO unsafe type coercion**: NEVER use `parseFloat()`, `parseInt()`, or `Number()`
   on values that could be strings like "$200/mo" or "4.9 stars". Parse explicitly:
   `const price = rawPrice.replace(/[^0-9.]/g, '');`

2. **NO placeholder secrets**: NEVER hardcode `"sk_test_xxx"` or `"re_xxx"` in source code.
   ALWAYS use `process.env.STRIPE_SECRET_KEY` etc. Read `.env.example` for available vars.

3. **NO dual export conflicts**: Each file exports EITHER `export default` OR
   named exports (`export function/const`), NEVER both in the same file.
   Next.js API routes use named exports: `export async function GET/POST`.
   Next.js pages use default exports: `export default function Page`.

4. **NO missing TypeScript types**: Every function parameter and return type MUST have
   explicit types. Use `unknown` instead of `any`. Verify with `npx tsc --noEmit`.

5. **MANDATORY NULL-SAFETY on dynamic data (RCA-ITR29 U-2)**: ALL pages that render
   data from API/DB MUST use defensive access patterns:
   - Optional chaining on ALL property accesses: `data?.rating?.toFixed(1)` NOT `data.rating.toFixed(1)`
   - Default values for ALL numeric renders: `(rating ?? 0).toFixed(1)` NOT `rating.toFixed(1)`
   - Default values for ALL string renders: `{name || 'Unknown'}` NOT `{name}`
   - Loading/error/empty states for EVERY dynamic page — not just the happy path
   - NEVER call `.toFixed()`, `.toString()`, `.map()`, `.filter()` without null guard
   **WHY (ITR-29 S-2)**: In MSR_Smoke_1780675145, `audit.rating.toFixed(1)` crashed
   when rating was undefined. This class of error wastes E2E iteration budget.

6. **NO fabricated/mock data in production code (RCA-ITR29 U-6)**: NEVER use hardcoded
   fake data arrays (e.g., `[{id: 'r1', author: 'Alice Smith'}, ...]`). ALL dynamic
   data MUST come from API calls or database queries. If seed data is needed for
   development, put it in `prisma/seed.ts` — NOT in page components.
   - NO `// For smoke test, we'll use mock reviews`
   - NO hardcoded review/user/business objects in page components
   - NO `setItems([{id: '1', ...}, {id: '2', ...}])` with literal data
   **WHY (ITR-29 S-6)**: Dashboard delivered with hardcoded fake reviews visible to
   real users. Fabrication detector fired 4 times but data was still delivered.
```

**🔴 Inter-Wave File Context (MANDATORY)**: After each wave, run `find src/ -type f | sort` and include in subsequent delegations.

### Phase 3.8 — Scaffold Cleanup (MANDATORY)

Before build verification, the code agent MUST clean up leftover scaffold boilerplate:

1. **Check for leftover scaffold boilerplate** (Create Next App, Vite defaults, CRA defaults, etc.)
   - Use `scaffold_cleanup_checker.detect_scaffold_boilerplate(project_dir)` to scan
   - Key files checked: `app/page.tsx`, `src/App.tsx`, `README.md`, `index.html`
2. **Replace ALL default text with project-specific content** from the user prompt
   - `"Create Next App"` → actual project name from `content_manifest.json`
   - `"Get started by editing"` → real landing page content
   - Default README → project-specific documentation
3. **Update README.md** to describe the actual project, not the scaffold framework

**Why this exists (F-6)**: Scaffold boilerplate (e.g., Next.js "Create Next App" default pages, README with framework defaults) persists into the final build because no cleanup phase existed. This causes content fidelity failures at E2E verification — the user's requested content is never written because the agent never replaces the scaffold defaults.

**Enforcement**: If `detect_scaffold_boilerplate()` returns `has_boilerplate: true` after Phase 3 implementation, the orchestrator MUST dispatch a targeted `call_subordinate` with `profile='code'` to replace the boilerplate before proceeding to Phase 4.

### Phase 3.8.1: Post-Scaffold File Inventory (MANDATORY)

After scaffold cleanup, the orchestrator MUST inventory scaffold-generated files that need project-specific content:

```bash
# Find all files with scaffold boilerplate markers
grep -rn 'SCAFFOLD\|TODO.*scaffold\|placeholder\|lorem ipsum\|example\.com' src/ --include='*.tsx' --include='*.ts' --include='*.css' | head -30
```

For EACH file found, create a Phase 3 delegation entry specifying:
1. The file path that needs real content
2. Which `content_manifest.json` fields replace the boilerplate
3. The REQ-IDs this file satisfies

This prevents scaffold boilerplate from surviving into the final build — every placeholder is explicitly assigned to a delegation.

### Phase 3.9 — Build Verification + Cache Invalidation (MANDATORY)

Before reporting Phase 3 completion, the code agent MUST:
1. Run the project's build command (`npm run build` / `cargo build` / etc.)
2. Fix ALL build errors (TypeScript, module resolution, syntax)
3. Only report success after a clean build

**Why this exists**: Build errors (e.g., `error TS2345` in `perplexity.ts`) that slip past Phase 3 are not caught until E2E verification, wasting iteration budget and delegation cycles. A deterministic L1 build check catches them in seconds.

**Enforcement**: The `build_verifier.py` L1 helper (`python.helpers.build_verifier`) provides:
- `detect_build_command(project_dir)` — auto-detects the correct build command
- `parse_build_output(output)` — parses build output for error patterns
- `build_verification_prompt(project_dir)` — generates the agent instruction

**🔴 Cache Invalidation (GAP-5 — #9)**: After ANY build failure, `invalidate_project_context_cache()` (`python.helpers.delegation_brief`) is automatically called to ensure subsequent delegations receive fresh project state (updated types, new files, corrected imports). Without this, the LRU-cached project context from before the build failure would persist, causing code agents to receive stale type information.

Skipping this step causes E2E failures and wasted iteration budget.

### Phase 3.5: Boomerang Assessment + Gate Resolution (MANDATORY after EVERY Phase 3 delegation)

1. Read subordinate's return message — check for `[ITERATION_LIMIT]` or `[CHAIN_LIMIT]`
2. Compare against Phase 0 checklist: ✅ completed, ⚠️ incomplete, ❌ missing
3. Re-dispatch remaining work with fresh agents
4. **🔴 ANTI-RE-SCAFFOLD**: EVERY re-dispatch MUST include: "CRITICAL: This is an EXISTING, SCAFFOLDED project. DO NOT run npx create-*, npm init, or any scaffold command."
5. **🔴 Inter-Wave Symbol Dedup (ADR-008)**: Check for duplicate exported symbols across files
6. **🔴 Task Injection Processing**: Scan for `TASK_INJECTION` blocks and dispatch new tasks
7. **🔴 TODO/Placeholder Scan (RCA-ITR12 SS-4)**: After each Phase 3 delegation returns, scan source files for TODO/FIXME/HACK comments, hardcoded coordinates (lat/lng), and placeholder values. Use `check_source_for_todos_and_placeholders(project_dir)` from `python.helpers.manifest_fidelity_validator`. Any findings must be re-dispatched to the code agent for replacement with real implementations. **WHY**: ITR-12 exposed that a `// TODO: geocode zip code` with hardcoded Boston coordinates (42.3601, -71.0589) passed all gates because no check looked for TODO comments in source code.
8. **🔴 Gate Failure Resolution (GAP-6 — SS-1)**: When a re-dispatch delegation **succeeds** (code agent returns without `[ITERATION_LIMIT]` or errors), call `resolve_gate_failure()` (`python.helpers.requirements_ledger`) to clear gate failures from the original failed delegation. This prevents stale gate failures from accumulating — previously, 27 gate failures pinned to delegation A were never cleared even after 13 remediation delegations succeeded, driving the completion gate to keep blocking. The resolution function marks matching failures as `resolved: true` so `get_active_gate_failures()` excludes them.
9. **🔴 Disk-Level Implementation Verification (ARCH-RCSIG)**: After each Phase 3 delegation returns, the framework automatically validates that actual source files were created via `validate_phase_completion()` in `python.helpers.phase_completion_guard`. If the delegation claims success but 0 new source files exist on disk, the phase is marked `partially_completed` and must be re-dispatched. **DO NOT trust delegation status alone** — always verify file output on disk. The pre-delegation file snapshot is taken automatically by `call_subordinate.py`.

**Maximum 3 boomerang cycles per feature.**

---

## Phase 4: Integration (🔴 MANDATORY — NEVER SKIP — MANDATORY STANDALONE)

**Phase 4 is a mandatory standalone task.** It:
1. MUST execute AFTER all Phase 3 parallel implementation tasks complete
2. CANNOT be collapsed into a Wave/batch with implementation tasks
3. MUST be dispatched as its own `call_subordinate` delegation
4. MUST NOT be assumed to happen "within" a frontend or backend task

When batching parallel tasks that contain BOTH frontend and backend work,
ALWAYS schedule Phase 4 as a sequential follow-up AFTER the batch completes.

This MUST be a separate `call_subordinate` task. Delegate to `code` agent:
```
TASK: Phase 4 — Frontend-Backend Integration

🔴 SURGICAL MODIFICATION ONLY — Modify existing frontend pages to call backend APIs.
DO NOT create new pages. DO NOT rewrite existing pages from scratch.
DO NOT refactor working components (e.g., client→server, mock→Prisma rewrites).

For EVERY page that displays data or has forms:
1. READ the existing page FIRST with `read_file` — understand what already works
2. ONLY add API call wiring where data is currently hardcoded/static
3. Keep ALL existing imports, components, and JSX structure intact
4. Add loading/error states as ADDITIONS, not replacements
5. If the page already works with mock data, wire the API call AND keep
   the mock data as fallback: `const data = apiData ?? mockData`

🔴 BEFORE modifying any file, run: `npx tsc --noEmit` and `npx vitest run src/` to confirm current state passes tests.
🔴 AFTER modifying each file, run: `npx tsc --noEmit` and `npx vitest run src/` to catch import/type and logic errors immediately.
🔴 🚫 DO NOT RUN `npm run build` iteratively. It takes 30 seconds and wastes budget. Use `vitest` and `tsc --noEmit` ONLY until all tests are perfectly green!
🔴 If tests or type checks fail after your changes, REVERT and try a smaller change.

After wiring, verify API call patterns exist in frontend files.
```

**ENFORCEMENT**: Search project for API call patterns after completion. If ZERO results, re-delegate.

### Phase 4.5: Build Navigation & API Route Maps (MANDATORY)

- Generate navigation map using `build_navigation_map` tool
- **🔴 API Route Gap Detection**: Cross-reference frontend call URLs with backend handlers. Create missing endpoints.

### 🔴 Phase 4.7: Full Wiring Verification (MANDATORY)

**Layer 1 (Quick Sanity)**: Search project for obvious wiring gaps:
- Files containing `// Mock`, `// TODO`, `// Placeholder`, `// Hardcoded`
- Frontend files with hardcoded array declarations used as data sources
- API routes with no callers

**Layer 2 (Intelligent Check)**: Delegate to a `code` agent with this specific task:
```
TASK: Phase 4.7 — Wiring Verification Audit

Examine every frontend page and every API route. For each page that displays
dynamic data, verify it calls a real API endpoint. For each API endpoint,
verify at least one frontend component calls it. Report any:
- Pages using hardcoded/mock data instead of API calls
- API routes that no frontend page calls
- Frontend fetch() calls targeting routes that don't exist

Fix any gaps found. Do NOT report success if gaps remain.
```

If Layer 2 finds gaps → the code agent fixes them inline.
If Layer 2 finds zero gaps → proceed to Phase 4.9 (Build-Freeze).

This step examines REAL FILES with intelligent understanding.
Agent self-reports ("I wired everything") are NOT sufficient evidence.

### 🔴 Phase 4.8: CSS/Config Verification Gate (MANDATORY before Build)

Before running `npm run build` or `npm run dev`, the orchestrator MUST verify ALL styling prerequisites exist and are correctly configured. Delegate this verification to a `code` agent:

```
TASK: Phase 4.8 — CSS/Config Verification

Verify that ALL of these files exist and are correctly configured:
1. `src/app/globals.css` — MUST contain `@tailwind base`, `@tailwind components`, `@tailwind utilities`
2. `tailwind.config.ts` (or .js) — MUST have `content: ["./src/**/*.{ts,tsx,js,jsx}"]`
3. `postcss.config.mjs` (or .js) — MUST have tailwindcss + autoprefixer plugins
4. `package.json` — MUST have `tailwindcss` and `postcss` in devDependencies

Verification script:
```bash
echo "=== CSS/Config Verification ==="
for f in src/app/globals.css tailwind.config.ts postcss.config.mjs; do
  [ -f "$f" ] && echo "✅ $f exists ($(wc -l < $f) lines)" || echo "❌ MISSING: $f"
done
grep -q "tailwindcss" package.json && echo "✅ tailwindcss in deps" || echo "❌ MISSING tailwindcss dep"
grep -q "@tailwind base" src/app/globals.css 2>/dev/null && echo "✅ @tailwind directives present" || echo "❌ MISSING @tailwind directives"
```

Also verify THEME CONSISTENCY:
- Read `globals.css` body/root background color
- Grep ALL `page.tsx` files for `bg-white` or `bg-black` or `bg-[#...]`
- If ANY page uses a background that contradicts `globals.css` → FIX IT
- All pages MUST use the same theme (dark or light) as defined in globals.css

If ANY file is missing or any theme contradiction found → FIX before proceeding.
Do NOT proceed to Phase 4.9 until this verification passes.
```

### 🔴 Phase 4.9: BUILD-FREEZE GATE (TERMINAL CODE OPERATION)

This is the **last step that may modify source code**. After this step succeeds, the source tree is FROZEN.

1. Backup build cache: `cp -r .next/ .next.bak/ 2>/dev/null || true`
2. Delete build cache: `rm -rf .next/ node_modules/.cache/`
3. Run production build: `npm run build`
4. If build FAILS:
   - Restore backup: `mv .next.bak/ .next/`
   - Fix the specific build error (do NOT rewrite files — surgical fix only)
   - Re-attempt build (max 3 attempts)
5. If build SUCCEEDS: `rm -rf .next.bak/`
6. Record build output
7. **🔴 Create pre-verification snapshot**: Immediately after build succeeds, call
   `pre_phase5_snapshot.create_snapshot(project_dir)` to capture the known-good state.
   This snapshot is the rollback target if any Phase 5/6 fix breaks the build.

**FREEZE CONTRACT**: After build succeeds → NO `call_subordinate` may modify source files. If Phase 5 discovers issues → return to Phase 3/4, fix, re-build.
**SNAPSHOT CONTRACT**: The pre-Phase-5 snapshot is sacrosanct. If a Phase 6 fix delegation
breaks the build, the orchestrator MUST restore from this snapshot before re-attempting.

### Phase 4.95: Pre-E2E Smoke Check (MANDATORY — before E2E delegation)

Before delegating to the E2E agent, run a cheap deterministic check to avoid wasting E2E budget on obviously broken builds:

1. **HTTP reachability**: `curl -s -o /dev/null -w '%{http_code}' http://0.0.0.0:{PORT}` → must return 200
2. **Boilerplate detection**: `curl -s http://0.0.0.0:{PORT} | grep -ic 'create next app\|welcome to next\|vite app\|hello world'` → must return 0
3. **CSS loaded**: `curl -s http://0.0.0.0:{PORT} | grep -c 'tailwind\|--tw-\|stylesheet'` → must be > 0
4. **Real content present**: `curl -s http://0.0.0.0:{PORT} | grep -ic '<project_name_from_manifest>'` → must be > 0

If ANY check fails, the orchestrator MUST:
- NOT delegate to E2E (wastes iteration budget)
- Instead, delegate to `code` agent to fix the specific issue
- Re-run Phase 4.95 after the fix

**Why this exists**: E2E agents cost 50-200 iterations. Sending them to verify a site that returns scaffold boilerplate or 502 is pure waste. A 4-line curl check catches 80% of issues in <1 second.

---

## Phase 5: Verification (MANDATORY — NEVER SKIP)

> **⚠️ BUILD-FREEZE IN EFFECT**: Source tree is FROZEN. Do NOT modify source files during verification.

### 🔴 Phase 5.0.0: Dev Server Clean Restart (MANDATORY — FIRST STEP)

Before ANY verification, the dev server MUST be restarted from a clean state to ensure the Tailwind JIT compiler processes ALL CSS classes written during Phase 3. A stale dev server that was started before `globals.css` or `tailwind.config.ts` were finalized will serve empty CSS — causing the entire UI to appear unstyled.

Delegate to `code` agent:
```
TASK: Phase 5.0.0 — Dev Server Clean Restart

1. Stop any running dev server: Use `services_mgt` with `action: "stop_service"` for any active services
2. Clear ALL caches:
   - `rm -rf .next/`
   - `rm -rf node_modules/.cache/`
   - `rm -rf .vite/`
3. Verify node_modules exists: `ls node_modules/.package-lock.json` → if missing, run `npm install`
4. Start dev server via services_mgt: Use `services_mgt` with `action: "start_service"`, `command: "npm run dev"`, `project_dir: "<project_path>"`
5. Wait for "ready" or "Local:" message in output (confirms server compiled successfully)
6. Verify CSS is being served: Use `services_mgt list_services` to get the assigned port, then `curl -s http://0.0.0.0:{PORT} | grep -c 'tailwind\|--tw-'`
   - If 0 matches → CSS is NOT being processed. Check postcss config, rebuild node_modules

Do NOT proceed to verification until the dev server is confirmed running with CSS active.
```

**WHY THIS EXISTS**: In smoke test MSR_Smoke_1778413874, the dev server was started during Phase 3 by an early subordinate, BEFORE `globals.css` and `tailwind.config.ts` were written by a later subordinate. The JIT compiler cached the empty state. All Tailwind utility classes rendered as no-ops. This step ensures a clean restart AFTER all source files are finalized.

### Phase 5.0.1: Live Integration Smoke Test (LIT — MANDATORY)

For EACH integration in `content_manifest.json`:
- API Key Validation (minimal API call)
- Endpoint Reachability (curl all routes)
- Integration imports/env var verification

### Phase 5.1: 🔴 E2E Test Aggregation (Proof Finalization — MANDATORY)

Delegate to `e2e` agent with the full test aggregation template (from Proof-Based Enforcement section).
The E2E agent runs ALL test suites independently and reports aggregate pass/fail.

**Step 0.5 — Verification Matrix Check (MANDATORY before E2E testing)**:
Before running general E2E tests, the E2E agent MUST:
1. Read `.agix.proj/verification_matrix.json`
2. Identify any requirements scored WEAK or UNTESTED
3. Report these gaps to the orchestrator for re-dispatch
4. Only proceed with E2E testing after the orchestrator acknowledges the gap report

**This is the definitive proof**: E2E results update `test_passed` in ALL proof objects.
The completion gate reads these proofs — if ANY show `FAIL`, delivery is blocked.

```
E2E aggregate → test_passed=True/False → proof objects updated →
_22 completion gate reads proofs → PASS: deliver | FAIL: re-delegate
```

### Phase 5.2: Verification Matrix Re-Dispatch Loop (MANDATORY)

After E2E verification, run the verification matrix one final time:
1. Call `build_verification_matrix(project_dir)` to aggregate all layers (TDD + Literals + BDD + PDV)
2. For each requirement scored WEAK or UNTESTED:
   - Generate a targeted re-dispatch task to the `code` agent
   - Include the specific test expectation from `test-skeleton.json`
   - Maximum 3 re-dispatch cycles total
3. After all re-dispatch cycles, produce a final matrix summary
4. If any REQ remains UNTESTED after 3 cycles, log it as a known gap in the delivery summary

**Circuit breaker**: If the same REQ remains UNTESTED after 3 re-dispatch cycles, stop re-dispatching and include it in the Phase 7 summary as a known gap.

### Phase 5.3: E2E Delegation Self-Check (MANDATORY)

Before proceeding to visual/functional verification or delivery, the orchestrator MUST verify that E2E verification was properly delegated. This is the **Layer 1 self-check** — the orchestrator checks its own work before the completion gate (Layer 3) does.

**5-Point E2E Delegation Checklist** (ALL must be YES):

1. ✅ **E2E agent delegated**: Was `call_subordinate` invoked with `profile="e2e"`? If `_delegation_profiles` does not contain `"e2e"`, the orchestrator MUST delegate to the e2e agent NOW before proceeding.
2. ✅ **Dev server started via services_mgt**: Was the dev server started using the `services_mgt` tool (not raw `npm run dev`)? The `_dev_server_started` flag must be true.
3. ✅ **All test suites run**: Did the e2e agent run `npm test`, `pytest`, and any BDD/Playwright suites? Check the e2e agent's return message for test result summaries.
4. ✅ **Browser smoke test done**: Did the e2e agent (or browser_agent) visit at least the root route and confirm it renders without console errors?
5. ✅ **QA/UAT grade reported**: Did the e2e agent report an aggregate pass/fail grade? If any suite failed, the orchestrator must re-dispatch fixes before delivery.

**If ANY item is NO**: Delegate to the `e2e` agent with the full test aggregation template (from Phase 5.1) before proceeding. Do NOT skip this step — the completion gate (Layer 3) will block your response if e2e verification is missing.

### Visual & Functional Verification

8. **Run all tests** → Full test suite must pass (via E2E agent above)
9. **Visual verification** → Browser agent screenshots (max 4-5 routes per call)
10. **UI Consistency Audit** → Navbar/footer on ALL pages, consistent colors, customized tab title
11. **API Integration Verification** → curl every route, verify real data (not mock)
12. **Systematic Page Health Curl** → Compare navigation map vs actual HTTP responses
12.5. **E2E Click-Through Testing** → E2E agent tests all pages/links/forms

**🔴 E2E FAIL → Code Fix Routing**: Extract issues → delegate to `code` → wait for fix → clear cache → rebuild → ONLY THEN re-run E2E. Maximum 3 fix cycles.

13. **Completeness check** → All requirements implemented, no stubs, >10 source files
14. **🔴 Anti-Empty File Gate** → `find src/ -name '*.tsx' -empty`
15. **🔴 Content Manifest Verification** → Grep for every manifest value in source
16. **🔴 Enhanced Mock Data Detection** → Search for hardcoded mock arrays
17. **🔴 Proof Gate Final Check** → All proof objects must show `PASS` (5/5 checks). If any show `FAIL` or `PARTIAL`, re-delegate the failing requirement before delivering.

---

## 🔴 Phase 5.0.5: Design Review Gate (BLOCKING — with Escape Hatch)

**Profile**: `frontend` (designer) — reviews visual fidelity ONLY, does NOT modify code.

After E2E verification, delegate to the `frontend` (designer) agent for visual QA:
```
TASK: Phase 5.0.5 — Design Review (Visual Fidelity Audit)

You are the UI/UX Designer. Review the implemented pages against your original mockups.

## Your Inputs
- Browser screenshots from Phase 5 E2E verification
- Your original mockups in `docs/design-mockups/`
- Your `design-tokens.json` and `component-spec.md`

## Review Checklist
For EACH page, compare the screenshot against the mockup and check:
1. **Color Fidelity**: Do the rendered colors match design-tokens.json values?
2. **Typography**: Are font sizes, weights, and families correct?
3. **Spacing & Layout**: Is padding, margin, and grid layout correct?
4. **Component Hierarchy**: Are all specified components present in the right order?
5. **Responsive Behavior**: Does the layout respond correctly at breakpoints?
6. **Interactive States**: Do hover/active states match the spec?
7. **Content Accuracy**: Does the rendered content match the content manifest?

## Output Format
For each page, report:
- **Page**: [page name]
- **Verdict**: PASS | MINOR_DEVIATION | MAJOR_DEVIATION
- **Deviations**: [list specific differences with screenshots]

## 🔴 CRITICAL: You are a REVIEWER, not a fixer
- Do NOT modify any source code
- Do NOT run any build commands
- If you find deviations, DESCRIBE them — the code agent will fix them
- Emit TASK_INJECTION blocks for any fixes needed
```

### Escape Hatch (Auto-Pass Conditions)

The design review gate is **blocking by default**, but auto-passes with a WARNING (not blocking) if ALL of these conditions are met:

1. **ALL BDD test scenarios pass** (from Phase 5 E2E verification)
2. **No MAJOR_DEVIATION** verdicts from the designer (only PASS or MINOR_DEVIATION)
3. **Minor deviations are within tolerance**:
   - Color values within ±5% luminance of token values
   - Spacing within ±4px of specified values
   - Font sizes within ±1px

If the escape hatch fires, the orchestrator logs: `"DESIGN_REVIEW: Auto-passed with minor deviations (BDD passed, no major issues). Deviations logged for future iteration."`

If any MAJOR_DEVIATION is found, the gate BLOCKS and the orchestrator must:
1. Parse the designer's deviation report
2. Delegate fix tasks to the `code` agent (NOT the designer)
3. Re-run the design review (max 2 cycles)

---

## Phase 5.5: Version Control Publication (AFTER all quality gates pass)

🔴 **PREREQUISITE**: Phase 5.5 MUST NOT run until ALL of the following are true:
- Phase 5 verification gates have PASSED (build, routes, content, BDD)
- Design review has PASSED or auto-passed with minor deviations
- No CRITICAL gate blocks remain

### Steps:
1. Initialize git if not already initialized, create `.gitignore`
2. Stage all files: `git add -A`
3. Commit with descriptive message: `git commit -m "feat: <project-name> — full implementation"`
4. Push using temp_clone pattern (see `_swarm_instructions.md`)
5. **Post-push sync**: After successful push, copy any files from the
   temp_clone back to the project directory to ensure local matches remote:
   ```bash
   rsync -a --exclude='.git' tmp/push_staging/ /agix/usr/projects/<name>/
   ```

### Why post-push sync?
The `temp_clone` pattern copies project files to a staging dir for push.
Any git-generated files (like `.git/` metadata) don't come back. The
rsync step ensures the project directory stays the canonical source.

Use MCP GitHub tools (pre-authenticated) — fallback to git CLI via temp_clone.

---

## Phase 6: Iteration (IF VERIFICATION FAILS)

🔴 **SURGICAL FIX MANDATE**: Phase 6 fixes MUST be the MINIMUM change to resolve
the specific verification failure. Phase 6 is NOT a rewrite phase.

### 🔴 Phase 6 Diagnosis Scoping (RCA-ITR36 — TRUE ROOT CAUSE)
When delegating Phase 6 diagnosis (to `debug` or `code` agent), the diagnosis MUST be
**scoped to the specific failure** — NOT a full codebase audit:

1. **ONLY investigate the failing route/page**: If `/dashboard` returns 500, diagnose
   `/dashboard` — do NOT audit `/review/[id]`, `/discovery`, or other working pages.
2. **Mock data in WORKING pages is NOT a Phase 6 issue**: If a page renders correctly
   with mock/hardcoded data, that is Phase 4 Integration work — NOT a Phase 6 verification
   failure. The diagnosis agent MUST NOT flag working mock data as a "violation."
3. **Do NOT apply Phase 4.7 wiring rules during Phase 6**: Phase 4.7 checks for "pages
   using hardcoded/mock data instead of API calls." This check is for Phase 4 ONLY.
   In Phase 6, the only question is: "Does the page CRASH or return an error?"
4. **Include ONLY the specific failure in the fix delegation**: The orchestrator MUST NOT
   include diagnosis findings about working pages in the Phase 6 fix delegation.

**WHY (RCA-ITR36)**: In the MainStreet smoke test, the diagnosis agent investigated a
`/dashboard` 500 error but also audited `/review/[id]` and flagged its mock data as a
"Rule 9 violation." The orchestrator included "Replace mock data with Prisma fetch" in
the fix delegation → code agent rewrote the working page → broke imports → site crashed.
The page was working fine with mock data. The diagnosis scope creep caused the destruction.

### Rules:
1. **IDENTIFY the specific failure**: What EXACTLY failed in Phase 5? Quote the error message.
2. **SCOPE the fix to that failure**: If `/dashboard` returns 500 from empty DB,
   the fix is "seed the database" — NOT "rewrite dashboard to use Prisma queries."
3. **DO NOT rewrite working pages**: If a page rendered correctly before Phase 5,
   it MUST render correctly after Phase 6. Changes to working pages are PROHIBITED
   unless the verification failure is IN that specific page's rendering logic.
4. **DO NOT refactor**: No client→server component conversions, no mock→real data
   rewrites, no import reorganization, no architectural changes. Fix the SPECIFIC error.
5. **File budget**: Each Phase 6 delegation may modify MAX 3 existing files.
   Creating new files (e.g., seed scripts, config files) is unlimited.
6. **Pre/post verification**: Run `npx tsc --noEmit` and `npx vitest run src/` iteratively during editing. Run `npm run build` EXACTLY ONCE at the very end to confirm the final fix didn't break the build.

### Delegation Template for Phase 6:
```
TASK: Phase 6 — Fix Specific Verification Failure

🔴 SURGICAL FIX ONLY — Do NOT rewrite working code.
🔴 ADDITIVE-ONLY MODE — The build is currently passing. Your changes must PRESERVE the build.

## The Specific Failure
[Quote the EXACT error from Phase 5 verification — verbatim, not paraphrased]

## Allowed Changes
- [List the SPECIFIC files/changes needed — max 3 existing files]
- [For each file, describe the EXACT change: "In file X, add Y at line Z" or "In file X, change A to B"]

## What NOT To Do (🔴 READ THIS FIRST — VIOLATIONS CAUSE ROLLBACK)
- ❌ Do NOT rewrite any page/component that was working before this failure
- ❌ Do NOT refactor: no client→server conversions, no mock→real data rewrites,
     no state management changes, no routing architecture changes
- ❌ Do NOT reorganize imports, barrel exports, or module structure
- ❌ Do NOT change data fetching patterns (fetch→Prisma, SWR→fetch, etc.)
- ❌ Do NOT touch files NOT listed in "Allowed Changes" above
- ❌ Do NOT install new dependencies unless the error message explicitly names
     a missing module
- ❌ Do NOT interpret reference context (manifests, BDD specs, requirements)
     as instructions to rebuild — they are for REFERENCE ONLY
- ❌ Do NOT "improve" or "clean up" code while fixing the bug
- ❌ Do NOT create new pages, routes, or components unless the fix specifically
     requires a missing file

## Frozen Files (DO NOT TOUCH)
- ALL files NOT listed in "Allowed Changes" are FROZEN
- If you are tempted to modify a frozen file, STOP and report back instead

## Verification
- Run `npx tsc --noEmit` and `npx vitest run src/` BEFORE making changes (baseline — MUST pass)
- Run `npx tsc --noEmit` and `npx vitest run src/` AFTER each file change
- 🔴 DO NOT RUN `npm run build` iteratively after each file change. It wastes budget.
- Run `npm run build` EXACTLY ONCE at the end of your task to prove the fix preserves the build.
- If the build or tests break after your change, REVERT immediately and try a smaller fix
- Your response MUST include: test/build status before, changes made, test/build status after
```

### Operational Limits:
- Maximum 3 re-delegation cycles
- **Researcher rate-limit (F-5, ITR-11)**: Do NOT pair a `researcher` delegation with every `code` delegation in the same wave. Instead:
  - Phase 0.5: ONE researcher delegation for framework research (already defined)
  - Phase 2.6: ONE researcher delegation for cross-check (already defined)
  - Phase 3+: Pair researcher with code ONLY for the FIRST task in each milestone group. Subsequent tasks in the same milestone reuse the research output.
  - Maximum: 2 researcher delegations per planning phase, 1 per implementation milestone
- Dedup guard: skip 80%+ similar re-delegations
- NEVER repeat verification-only tasks more than once

### 🔴 Anti-Redundant-Recovery Rule (MANDATORY — ITR-48 RCA)

**Problem**: The orchestrator sees errors from a PRIOR phase's response (e.g., "lucide-react not found"
from Phase 3.2) and injects a "Recovery" delegation that re-installs deps, re-reads manifests,
and re-runs builds — all of which the CURRENT phase's code agent already handled. This wastes
30+ minutes per occurrence and is the #1 cause of repeated `npm install` calls.

**Rules**:

1. **NEVER inject a "Recovery" or "Dependency Fix" delegation if an implementation phase just returned successfully.**
   - A subordinate that calls `response` has ALREADY verified its own work (tests pass, build passes).
   - Errors mentioned in EARLIER phase responses (3.1, 3.2) do NOT apply after a later phase (3.3) succeeds.
   - If the latest subordinate returned with passing tests and build, those errors are STALE. Move to the NEXT phase.

2. **Before creating ANY "Recovery" or "Fix" delegation, verify the error still exists on disk:**
   ```
   # Check if the supposedly-missing dep is actually missing:
   call_subordinate(agent="code", message="Run: npm list lucide-react 2>&1 && echo EXISTS || echo MISSING")
   ```
   Only create a Recovery delegation if the error is CONFIRMED on disk, not just mentioned in context.

3. **NEVER re-delegate a phase that already produced source files.**
   - Before re-delegating Phase 3.X, check: `ls src/lib/services/ src/__tests__/ src/app/api/`
   - If files from that phase already exist (e.g., `outreach.ts`, `outreach.test.ts`), the phase is DONE.
   - Do NOT re-delegate it — mark it complete and move to the next phase.

4. **State carries forward between delegations.**
   - Dependencies installed by Phase 3.2's code agent persist on disk for Phase 3.3.
   - Each new delegation should NOT start with `npm install` for deps that are already in `node_modules/`.
   - Include in delegation messages: "Dependencies are already installed. Do NOT run `npm install` unless you add NEW packages."

5. **One-phase-at-a-time, no interleaving.**
   - NEVER start a second delegation while the first is still running.
   - `call_subordinate` is synchronous — wait for it to return before calling it again.
   - If you feel compelled to inject a Recovery task, WAIT for the current task to finish first.

## Phase 7: Summary

Produce final summary: features built, tests passing, files created, how to run, known limitations.

---

## 🔄 Container Restart Recovery (MANDATORY)

When the orchestrator detects it is running inside a RESTARTED container
(evidence: `decomposition_index.json` exists with phases marked completed),
it MUST follow this recovery protocol instead of improvising mega-delegations.

**Why this exists**: Without a restart template, the orchestrator LLM improvises
recovery delegations that span 5+ phases with 12+ REQ-IDs in a single
`call_subordinate`. This causes scope overload, budget exhaustion, and 50% of
all delegations being restart recovery instead of productive work.

### Detection

1. Check if `decomposition_index.json` exists in the project directory
2. If it has phases with status `completed` → this is a **RESTART**
3. Read the decomposition index to determine the **last completed phase**
4. Read `requirements_ledger.json` to determine which REQ-IDs are already satisfied

### Recovery Rules

1. **Skip all completed phases** — they already produced their artifacts. Do NOT
   re-delegate work that has already been done.
2. **Resume from the FIRST non-completed phase** — check its dependencies exist
   on disk before starting.
3. **Scope each delegation to ONE PHASE** — do NOT span multiple phases in a single
   `call_subordinate`. Each recovery delegation covers exactly one phase from the
   canonical phase table.
4. **DO NOT delegate restart recovery itself** — decompose recovery into normal
   per-phase delegations using the standard delegation templates above.
5. **DO NOT include 'fix jest.setup.js' or 'fix test config'** — these directives
   cause verification spirals where the agent spends its entire budget fixing test
   infrastructure instead of resuming actual work.
6. **NEVER paste the original user request verbatim** — it may contain API keys,
   secrets, and credentials. Reference it by saying "the original user request"
   or "see `content_manifest.json`" without repeating sensitive content.
7. **Invalidate caches** — call `invalidate_project_context_cache()` to get fresh
   state. Stale caches from the previous container lifecycle cause false-positive
   gate passes and phantom file references.
8. **Cap REQ-IDs at 5 per delegation** — above 5 causes scope overload and
   incomplete implementations. If a phase has >5 REQ-IDs, split it into
   sub-delegations following the Decomposition Granularity Mandate.

### Recovery Delegation Template

```
TASK: Phase {PHASE_ID} — {PHASE_NAME} (Restart Recovery)

This is a RESTART RECOVERY delegation. The container restarted mid-execution.

## Completed Phases (DO NOT REDO)
{list of completed phase IDs and their status}

## Current Phase
Phase {PHASE_ID}: {PHASE_NAME}
REQ-IDs: {max 5 REQ-IDs for this phase}

## Prerequisites Verified
- {list artifacts that exist on disk from completed phases}

## Instructions
{standard delegation instructions for this phase from the templates above}

🔴 SCOPE: This delegation covers ONLY Phase {PHASE_ID}. Do NOT attempt
work from any other phase.
```

### Anti-Patterns (DO NOT)

- ❌ Create mega-delegations spanning 3+ phases (e.g., "do Phases 3, 4, and 5 in one shot")
- ❌ Include 'fix test config' or 'fix jest.setup.js' directives in recovery delegations
- ❌ Paste API keys, secrets, or credentials from the original user request
- ❌ Assign 12+ REQ-IDs to a single code agent delegation
- ❌ Re-do completed phases "just to be safe" — trust the artifacts on disk
- ❌ Improvise a custom recovery flow — use the standard per-phase delegation templates
- ❌ Skip cache invalidation — stale state from the previous container lifecycle causes cascading failures
