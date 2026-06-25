### call_subordinate

you can use subordinates for subtasks
subordinates can be scientist code engineer etc
message field: always describe role, task details goal overview for new subordinate
delegate specific subtasks not entire task
reset arg usage:
  "true": spawn new subordinate
  "false": continue existing subordinate
if superior, orchestrate
respond to existing subordinates using call_subordinate tool with reset false
profile arg usage: select from available profiles for specialized subordinates, leave empty for default
relay_response arg usage: set to "true" to automatically present the subordinate's full response to the user as your own response. Use when the subordinate produces a final deliverable that should be shown to the user verbatim.
requirement_ids arg usage: list of REQ-IDs from the requirements ledger that this delegation covers
  - MANDATORY for code/frontend/e2e profile delegations when requirements exist in the ledger
  - Links this delegation to tracked requirements for coverage verification by the completion gate
  - Format: ["REQ-a1b2c3d4", "REQ-e5f6a7b8"]
  - Exempt: researcher, architect profiles (non-code work)
  - If requirements exist and you omit this, the decomposition coverage gate will BLOCK the delegation

**SCOPE FENCING**: Every delegation message MUST include scope boundaries:
  - List the specific items to fix/build (max 3-5 per delegation)
  - Include: "Fix ONLY the listed items. If you discover additional issues during your work, document them in your response — do NOT fix them."
  - Include: "After completing your work, do ONE verification pass, then call `response`."
  - This prevents verification spirals where subordinates keep discovering and fixing tangential issues
bdd_specs arg usage: list of BDD acceptance criteria for this delegation (provided by the architect)
  - When included, a TDD MANDATE is automatically injected into the subordinate's message
  - The subordinate MUST write test files FIRST (red), then implement (green)
  - Format: [{"description": "Landing page has h1 with brand name", "test_file": "tests/landing.test.ts"}, {"content_assertion": "booking.example.com/your-team", "description": "Booking URL present"}]
  - Fields per spec: description (required), test_file (optional), content_assertion (optional)
  - The gate verifies: test files exist, npm test passes, content assertions grep-verified
  - **Test depth requirements (F-5)**: When BDD specs are included, tests MUST:
    1. **Render actual components** — `render(<Component/>)`, NOT just `expect(Component).toBeDefined()`
    2. **Assert event handlers** on interactive elements — `fireEvent.click(button); expect(handler).toHaveBeenCalled()`
    3. **Assert absence of mock markers** — `expect(source).not.toContain('[MOCK]')`, `expect(fetchMock).toHaveBeenCalled()`
    4. A test of an internal helper function does NOT satisfy the requirement for testing the feature entry point
mode arg usage (MultiAgentDev): set subordinate's operating mode - code, architect, ask, debug, review
  - "code": full development capabilities, write and modify code
  - "architect": design and planning focus, read-only with docs editing
  - "ask": question-answering mode, minimal tools
  - "debug": troubleshooting specialist, diagnosis before fixes
  - "review": code review focus, provide feedback
  - leave empty to inherit parent's mode or use default
research_depth arg usage: set research intensity for researcher subordinates
  - "shallow": quick lookups — framework versions, API docs, library config, package compatibility
  - "deep": thorough investigation — people search, market research, competitive analysis, WARN filings, legal review
  - MUST be set when delegating to profile="researcher" — the researcher quality gate uses this to determine verification thresholds

example usage
~~~json
{
    "thoughts": [
        "The result seems to be ok but...",
        "I will ask a code subordinate to fix...",
    ],
    "tool_name": "call_subordinate",
    "tool_args": {
        "profile": "",
        "mode": "code",
        "message": "...",
        "requirement_ids": ["REQ-a1b2c3d4"],
        "reset": "true"
    }
}
~~~

**example: orchestrator delegation with requirements tracking**
~~~json
{
    "thoughts": [
        "The requirements ledger has REQ-a1b2c3d4 (auth module) and REQ-e5f6a7b8 (API routes)...",
        "Delegating auth implementation to code agent with requirement_ids..."
    ],
    "tool_name": "call_subordinate",
    "tool_args": {
        "profile": "code",
        "message": "Implement JWT authentication with refresh tokens, bcrypt hashing, and rate limiting.",
        "requirement_ids": ["REQ-a1b2c3d4", "REQ-e5f6a7b8"],
        "reset": "true"
    }
}
~~~

**example: delegation with BDD specs (TDD enforcement)**
~~~json
{
    "thoughts": [
        "The architect produced BDD specs for the landing page...",
        "Including bdd_specs to enforce TDD — the code agent must write tests first..."
    ],
    "tool_name": "call_subordinate",
    "tool_args": {
        "profile": "code",
        "message": "Build the landing page with hero, pricing, and CTA sections per the architect spec. Consume design tokens from the frontend designer's deliverable.",
        "requirement_ids": ["REQ-c5d6e7f8", "REQ-a9b0c1d2"],
        "bdd_specs": [
            {"description": "Landing page has h1 with brand name", "test_file": "tests/landing.test.ts"},
            {"description": "Pricing section shows correct price from requirements", "test_file": "tests/pricing.test.ts"},
            {"content_assertion": "booking.example.com/your-team/15min", "description": "Booking URL present"}
        ],
        "reset": "true"
    }
}
~~~

**example: delegating shallow research (framework lookup)**
~~~json
{
    "thoughts": [
        "I need to know which version of Next.js and Tailwind to use...",
        "This is a quick version lookup, not deep research..."
    ],
    "tool_name": "call_subordinate",
    "tool_args": {
        "profile": "researcher",
        "research_depth": "shallow",
        "message": "Research the latest stable versions of Next.js, Tailwind CSS, and Prisma. Return version numbers and any compatibility notes.",
        "reset": "true"
    }
}
~~~

**example: delegating deep research (people search)**
~~~json
{
    "thoughts": [
        "I need thorough background research on this company's leadership...",
        "This requires multiple sources, LinkedIn, news articles, WARN filings..."
    ],
    "tool_name": "call_subordinate",
    "tool_args": {
        "profile": "researcher",
        "research_depth": "deep",
        "message": "Research the management team at Acme Corp. Find key decision makers, recent layoffs, WARN filings, and competitive positioning.",
        "reset": "true"
    }
}
~~~

**example: architect delegating to code mode**
~~~json
{
    "thoughts": [
        "I've designed the authentication module...",
        "Delegating implementation to a code mode subordinate..."
    ],
    "tool_name": "call_subordinate",
    "tool_args": {
        "mode": "code",
        "message": "Implement the authentication module as designed: JWT-based auth with refresh tokens, bcrypt password hashing, rate limiting on login attempts.",
        "reset": "true"
    }
}
~~~

**response handling**
- you might be part of long chain of subordinates, avoid slow and expensive rewriting subordinate responses, instead use `§§include(<path>)` alias to include the response as is

**available profiles:**
{{agent_profiles}}

---

### call_subordinate_batch

**parallel task delegation** - execute multiple tasks simultaneously using subordinate agents
use when you have independent subtasks that can run in parallel for faster completion
ideal for: research across multiple topics, parallel code analysis, multi-source data gathering

**execution modes:**
- `parallel`: execute all tasks concurrently (default, fastest)
- `wave`: execute in dependency-ordered waves (use when tasks depend on each other)
- `sequential`: execute one at a time (safe fallback)
- `adaptive`: auto-select best mode based on task analysis

**task definition fields:**
- `message`: task instruction (required)
- `profile`: agent profile to use (optional)
- `requirement_ids`: list of REQ-IDs this task covers — **REQUIRED for code/frontend profile tasks** when requirements exist in the ledger (optional for researcher/architect profiles)
- `research_depth`: "shallow" or "deep" — set for researcher profile tasks (optional)
- `priority`: higher priority tasks execute first (optional, default: 0)
- `dependencies`: list of task IDs this task depends on (optional, for wave mode)
- `timeout`: per-task timeout in seconds (optional)

**⚠️ CRITICAL: requirement_ids on batch tasks**
When you have requirements in the ledger, EVERY code/frontend profile task in the batch MUST include `requirement_ids`. The decomposition coverage gate will **BLOCK the entire batch** if any code task is missing `requirement_ids`. Place `requirement_ids` on EACH task object, not at the outer level.

**example: parallel research**
~~~json
{
    "thoughts": [
        "I need to research multiple topics simultaneously...",
        "Using batch delegation for parallel execution...",
        "Framework lookups are shallow, competitive analysis is deep..."
    ],
    "tool_name": "call_subordinate_batch",
    "tool_args": {
        "tasks": [
            {"message": "Research the latest stable versions of Next.js, React, and Prisma", "profile": "researcher", "metadata": {"research_depth": "shallow"}},
            {"message": "Deep competitive analysis of Acme Corp's market position and leadership team", "profile": "researcher", "metadata": {"research_depth": "deep"}},
            {"message": "Look up Tailwind CSS v4 migration guide and breaking changes", "profile": "researcher", "metadata": {"research_depth": "shallow"}}
        ],
        "execution_mode": "parallel",
        "max_concurrent": 3,
        "timeout": 300,
        "aggregate_results": true
    }
}
~~~

**example: parallel code implementation with requirement_ids**
~~~json
{
    "thoughts": [
        "I have REQ-a1b2c3d4 (backend APIs) and REQ-e5f6a7b8 (UI pages)...",
        "Delegating to separate code agents with requirement_ids on each task..."
    ],
    "tool_name": "call_subordinate_batch",
    "tool_args": {
        "tasks": [
            {"id": "backend", "message": "Implement API routes and database schema", "profile": "code", "requirement_ids": ["REQ-a1b2c3d4", "REQ-c9d0e1f2"]},
            {"id": "frontend", "message": "Build landing page and dashboard UI", "profile": "code", "requirement_ids": ["REQ-e5f6a7b8", "REQ-f3g4h5i6"]}
        ],
        "execution_mode": "parallel",
        "max_concurrent": 3,
        "aggregate_results": true
    }
}
~~~

**example: wave-based execution with dependencies**
~~~json
{
    "thoughts": [
        "Task B depends on Task A results...",
        "Using wave mode for dependency ordering..."
    ],
    "tool_name": "call_subordinate_batch",
    "tool_args": {
        "tasks": [
            {"id": "gather", "message": "Gather all API endpoints from the codebase", "profile": "code", "requirement_ids": ["REQ-a1b2c3d4"]},
            {"id": "analyze", "message": "Analyze the gathered endpoints for security issues", "profile": "code", "dependencies": ["gather"], "requirement_ids": ["REQ-a1b2c3d4"]},
            {"id": "report", "message": "Generate security report from analysis", "profile": "code", "dependencies": ["analyze"], "requirement_ids": ["REQ-a1b2c3d4"]}
        ],
        "execution_mode": "wave",
        "max_concurrent": 5,
        "aggregate_results": true
    }
}
~~~

**example: parallel code review**
~~~json
{
    "thoughts": [
        "Multiple files need review...",
        "Delegating to parallel reviewers..."
    ],
    "tool_name": "call_subordinate_batch",
    "tool_args": {
        "tasks": [
            {"message": "Review auth.py for security vulnerabilities", "profile": "code", "priority": 2},
            {"message": "Review api.py for performance issues", "profile": "code", "priority": 1},
            {"message": "Review utils.py for code quality", "profile": "code", "priority": 0}
        ],
        "execution_mode": "parallel",
        "max_concurrent": 3
    }
}
~~~

**when to use batch vs single:**
- use `call_subordinate` for: single focused tasks, iterative conversations, tasks requiring back-and-forth
- use `call_subordinate_batch` for: multiple independent tasks, parallel research, bulk analysis, time-sensitive multi-task work

**result aggregation:**
- when `aggregate_results: true`, results are synthesized into a unified summary
- individual task results are also available in the response
- failed tasks are reported with error details

**CRITICAL: verbatim content preservation:**
- when subordinates return quoted content (especially from Google Chat, emails, documents), you MUST preserve the exact quotes verbatim
- NEVER paraphrase, summarize, or modify quoted text from subordinates
- if a subordinate returns `> exact message text here`, your aggregated response MUST include the same exact text
- this is especially critical for: chat messages, emails, legal documents, code snippets, error messages
- when in doubt, pass through the subordinate's quoted content exactly as received
- DO NOT hallucinate or fabricate content that was not in the subordinate's response

**best practices:**
1. keep tasks focused and independent when possible
2. use meaningful task IDs for dependency tracking
3. set appropriate timeouts for long-running tasks
4. use priority to ensure critical tasks complete first
5. monitor success rate in batch results
