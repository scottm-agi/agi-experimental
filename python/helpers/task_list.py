"""
Task List helper — TodoItem data model, markdown parsing, and status validation.

Ported from Roo-Code's UpdateTodoListTool/shared/todo patterns.

Each agent (orchestrator or worker) can maintain a structured task list in
agent.data["_task_list"]. The list is populated via the `update_task_list` tool
and validated by the orchestrator completion gate before allowing `response`.

TodoItem structure:
    {
        "id": "md5-hash",
        "content": "Build Discovery Engine API routes",
        "status": "pending",  # pending | in_progress | completed
        "guid": "",            # Optional: REQ-xxxx from orchestrator delegation
        "parent_hash": ""      # Optional: 12-char canonical hash of parent task
    }

Status flow (strict, no backwards transitions):
    pending → in_progress → completed
"""

import re
from typing import Any, Dict, List, Optional


# Valid statuses (Roo-Code compatible)
VALID_STATUSES = {"pending", "in_progress", "completed"}

# Status transition rules (current → allowed next states)
ALLOWED_TRANSITIONS = {
    "pending": {"pending", "in_progress", "completed"},
    "in_progress": {"in_progress", "completed"},
    "completed": {"completed"},
}


def parse_markdown_checklist(md: Any) -> List[Dict[str, str]]:
    """Parse markdown checklist into TodoItem list.

    Supports all standard markdown checkbox formats:
        [ ] pending
        [-] or [~] in_progress
        [x] or [X] completed
        - [ ] with dash prefix

    Ported from Roo-Code's parseMarkdownChecklist().

    Args:
        md: Markdown string with checklist items.

    Returns:
        List of TodoItem dicts with {id, content, status}.
    """
    if not md or not isinstance(md, str):
        return []

    lines = [line.strip() for line in md.split("\n") if line.strip()]
    todos: List[Dict[str, str]] = []

    # Pattern: optional "- " prefix, then [marker], then content
    pattern = re.compile(r'^(?:-\s*)?\[\s*([ xX\-~])\s*\]\s+(.+)$')

    for line in lines:
        match = pattern.match(line)
        if not match:
            continue

        marker = match.group(1)
        content = match.group(2).strip()

        if marker in ("x", "X"):
            status = "completed"
        elif marker in ("-", "~"):
            status = "in_progress"
        else:
            status = "pending"

        # Generate deterministic ID from content (Roo-Code uses md5)
        from python.helpers.hashing import content_hash
        item_id = content_hash(content + status)

        todos.append({
            "id": item_id,
            "content": content,
            "status": status,
            "guid": "",
            "parent_hash": "",
        })

    return todos


def validate_status_transition(current: str, next_status: str, *, todo_item: Optional[Dict[str, str]] = None) -> bool:
    """Check if a status transition is valid (no backwards movement).

    Args:
        current: Current status.
        next_status: Proposed new status.
        todo_item: Optional todo dict. When provided, a TodoItemSM is
            created/reused in todo_item["_todo_sm"] for audit trail.
            RCA-475 E4: wrap, not replace — SM validation is warn-only.

    Returns:
        True if the transition is allowed.
    """
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    is_valid = next_status in allowed

    # ── RCA-475 E4: SM validation (wrap — warn-only during migration) ──
    if todo_item is not None:
        _validate_with_todo_sm(todo_item, current, next_status, is_valid)

    return is_valid


def _get_or_create_todo_sm(todo_item: Dict[str, str], current_status: str) -> "TodoItemSM":
    """Get or create a TodoItemSM for a todo item dict.

    RCA-475 E4: SM instances live in todo_item["_todo_sm"].
    On first access, the SM is seeded with the current status.

    RCA-479 Fix: Handles corrupted SM entries from JSON round-trip.
    """
    from python.helpers.state_machines.todo_item_sm import TodoItemSM

    sm = todo_item.get("_todo_sm")
    if not isinstance(sm, TodoItemSM):
        sm = TodoItemSM(
            status=current_status,
            entity_id=todo_item.get("id", "?"),
        )
        todo_item["_todo_sm"] = sm
    return sm


def _validate_with_todo_sm(
    todo_item: Dict[str, str],
    current: str,
    next_status: str,
    is_valid: bool,
) -> None:
    """Wire TodoItemSM alongside validate_status_transition.

    RCA-475 E4: On valid transitions, SM records the transition.
    On invalid transitions, SM is still created (for tracking) but
    the transition is not applied. Warns but never blocks.
    """
    import logging
    _logger = logging.getLogger("agix.task_list")

    sm = _get_or_create_todo_sm(todo_item, current)

    if not is_valid:
        _logger.warning(
            "[TASK_LIST SM] Transition %s→%s rejected by ALLOWED_TRANSITIONS "
            "(SM stays at %s)", current, next_status, sm.status
        )
        return

    # Skip if SM already at target (idempotent self-transitions)
    if sm.status == next_status:
        return

    ok, msg = sm.transition(
        next_status,
        reason=f"validate_status_transition({current}, {next_status})",
        source="task_list.py",
    )
    if not ok:
        _logger.warning("[TASK_LIST SM] %s — status set anyway (migration mode)", msg)
        sm.transition(
            next_status,
            reason=f"force-sync: {msg}",
            source="task_list.py",
            force=True,
        )



def get_incomplete_tasks(task_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Get all tasks that are NOT completed.

    Args:
        task_list: List of TodoItem dicts.

    Returns:
        List of incomplete TodoItems.
    """
    return [t for t in task_list if t.get("status") != "completed"]


def format_task_list_status(task_list: List[Dict[str, str]]) -> str:
    """Format task list as readable status string.

    Args:
        task_list: List of TodoItem dicts.

    Returns:
        Markdown-formatted status string.
    """
    if not task_list:
        return "No tasks registered."

    completed = sum(1 for t in task_list if t.get("status") == "completed")
    total = len(task_list)
    lines = [f"**Task Progress: {completed}/{total} complete**\n"]

    for t in task_list:
        status = t.get("status", "pending")
        if status == "completed":
            marker = "✅"
        elif status == "in_progress":
            marker = "🔄"
        else:
            marker = "⬜"
        line = f"  {marker} {t.get('content', 'Unknown task')}"
        # Append GUID/hash linkage when present
        guid = t.get("guid", "")
        parent_hash = t.get("parent_hash", "")
        if guid or parent_hash:
            tags = []
            if guid:
                tags.append(f"GUID:{guid}")
            if parent_hash:
                tags.append(f"hash:{parent_hash}")
            line += f" ({', '.join(tags)})"
        lines.append(line)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Verification Task Templates — Auto-generated L1 tasks for dev workflows
# ═══════════════════════════════════════════════════════════════════════

# These templates are injected by the task decomposer when multiagentdev
# handles a fullstack/dev task. Each becomes a task in the agent's _task_list,
# so L1 (open todos) naturally blocks completion until they're done.
# Framework-agnostic: they describe INTENT, not specific tools/files.
VERIFICATION_TASK_TEMPLATES = [
    # Phase: TDD & Architecture (during implementation)
    "Create route/URI/API sitemap as part of architect or TDD phase (verification_sitemap.json)",
    "Write tests for key pages and API routes (target ≥90% coverage)",
    "Run production build and fix all compilation errors",
    "Remove all boilerplate/scaffold content from entry files",
    # Phase: Curl Validation (before browser testing)
    "Curl ALL page URIs from sitemap — verify 200 status for every route (gaps become new todos)",
    "Curl ALL API endpoints from sitemap — verify correct responses (gaps become new todos)",
    # Phase: LIT (Logical Integration Testing — between curl and browser UAT)
    "Create LIT plan (lit_plan.json) with test cases for 4 patterns: API routes, integration smoke, data flow, error paths",
    "Execute LIT plan — curl all API routes with test data, verify responses match expected shapes",
    # Phase: Browser UAT (final validation)
    "E2E browser agent: navigate all pages, click all buttons/links, verify full site functionality as UAT",
    # Phase: Finalization
    "Push code to version control (if specified in requirements)",
]

# Keywords used to detect whether a task in the list is a "verification" task
VERIFICATION_KEYWORDS = [
    "build", "verification", "test", "e2e", "boilerplate",
    "integration", "scaffold", "coverage", "sitemap", "version control",
    "push", "verify", "audit", "validate", "curl", "browser",
    "uat", "route", "uri", "api", "endpoint", "navigate",
    "lit", "lit_plan", "data flow", "error paths", "integration smoke",
]


def has_verification_tasks(task_list: Optional[List[Dict[str, str]]],
                           min_count: int = 3) -> bool:
    """Check if the task list contains at least min_count verification-type tasks.

    Args:
        task_list: The agent's _task_list.
        min_count: Minimum number of verification tasks required.

    Returns:
        True if sufficient verification tasks are present.
    """
    if not task_list:
        return False

    count = 0
    for task in task_list:
        content = task.get("content", "").lower()
        if any(kw in content for kw in VERIFICATION_KEYWORDS):
            count += 1
            if count >= min_count:
                return True
    return False


def format_verification_guidance() -> str:
    """Build mandatory verification tasks guidance for the system prompt.

    This is injected by the task decomposer when multiagentdev handles a
    dev/fullstack task. It tells the agent to include these verification
    tasks in its update_task_list call.

    Returns:
        Markdown guidance string.
    """
    lines = [
        "",
        "📋 **MANDATORY VERIFICATION TASKS** — Your task list MUST include "
        "these items. They are non-negotiable quality gates that you must "
        "complete BEFORE calling `response`:",
        "",
        "### Phase A: TDD & Architecture (during implementation)",
        "- [ ] Create route/URI/API sitemap (verification_sitemap.json) "
        "during architect or TDD phase",
        "- [ ] Write tests for key pages and API routes (≥90% coverage)",
        "- [ ] Run production build and fix all compilation errors",
        "- [ ] Remove all boilerplate/scaffold content from entry files",
        "",
        "### Phase B: Curl Validation (before browser testing)",
        "- [ ] Curl ALL page URIs from sitemap — verify 200 status (gaps "
        "become new todos)",
        "- [ ] Curl ALL API endpoints from sitemap — verify correct "
        "responses (gaps become new todos)",
        "",
        "### Phase C: Browser UAT (final validation)",
        "- [ ] E2E browser agent: navigate all pages, click all buttons/"
        "links, verify full site functionality as a real user would in UAT",
        "",
        "### Phase D: Finalization",
        "- [ ] Push code to version control (if specified in requirements)",
        "",
        "**ORDER MATTERS**: Phase B (curl) MUST come before Phase C "
        "(browser). Any gaps found during curl validation become new "
        "todos that must be fixed before browser UAT.",
        "",
        "Add these to your `update_task_list` call alongside your "
        "implementation tasks. The completion gate will block you from "
        "responding until ALL tasks (including these) are marked completed.",
        "",
    ]
    return "\n".join(lines)


def format_lit_guidance() -> str:
    """Build LIT (Logical Integration Testing) guidance for the system prompt.

    Injected by the task decomposer when multiagentdev handles a dev/fullstack
    task. Tells the agent to create and execute a LIT plan covering 4 patterns.

    Returns:
        Markdown guidance string with pattern examples and curl templates.
    """
    lines = [
        "",
        "🧪 **MANDATORY LIT (Logical Integration Testing)** — Your task list "
        "MUST include LIT tasks. These prove your APIs ACTUALLY WORK, not "
        "just that files exist:",
        "",
        "### Phase B.5: LIT Execution (between curl validation and browser UAT)",
        "- [ ] Create LIT plan (`lit_plan.json`) with test cases for ALL 4 patterns",
        "- [ ] Execute LIT plan — run all test cases and report results",
        "",
        "### 4 Required LIT Test Patterns:",
        "",
        "**Pattern 1: API Route Testing** — curl each API route with realistic data:",
        "```bash",
        '# Example: POST to an API endpoint with test data',
        'curl -X POST http://0.0.0.0:5100/api/search \\',
        '  -H "Content-Type: application/json" \\',
        "  -d '{\"query\":\"test\",\"category\":\"default\"}'",
        '# Expected: 200 with structured JSON response matching api_contracts',
        "```",
        "",
        "**Pattern 2: Integration Smoke** — verify external service wiring:",
        "```bash",
        '# Check that required env vars exist',
        'echo $API_KEY | head -c 5  # Should output non-empty',
        '# Or verify API returns graceful error when key is missing',
        "```",
        "",
        "**Pattern 3: Data Flow** — verify end-to-end request→response pipeline:",
        "```bash",
        '# Submit form data → verify it flows through to actual response',
        'curl -X POST http://0.0.0.0:5100/api/items \\',
        "  -d '{\"id\":\"test123\",\"action\":\"create\"}'",
        '# Verify response contains real processed data, not stubs',
        "```",
        "",
        "**Pattern 4: Error Paths** — verify proper error handling:",
        "```bash",
        '# Empty body should return 400, not 500',
        "curl -X POST http://0.0.0.0:5100/api/items -d '{}'",
        '# Expected: 400 with {"error": "Required fields missing"}',
        '# Invalid route should return 404, not crash',
        "```",
        "",
        "**The completion gate will auto-generate `lit_plan.json` from your "
        "`verification_sitemap.json` if you don't create one. But you MUST "
        "execute the tests and include results in your delegation response.**",
        "",
    ]
    return "\n".join(lines)


def format_prerequisites_guidance() -> str:
    """Build MEP (Mise-en-place) prerequisites guidance for the system prompt.

    Forces the architect pass to identify data needs, env vars, external
    APIs, and sample data BEFORE implementation starts. Delegates data
    gathering to the `researcher` agent.

    Inspired by: SWE-AF PlanResult schema, MEP Manifest pattern, SOUL
    confidence scoring.
    """
    lines = [
        "",
        "🍳 **MISE-EN-PLACE: Prerequisites Discovery** — Before writing ANY "
        "implementation code, you MUST identify what this system needs to "
        "boot up and be fully testable:",
        "",
        "### Phase 0: Prerequisites Checklist",
        "Create a `prerequisites.json` in the project root listing:",
        "",
        "```json",
        "{",
        '  "seed_data": [',
        '    {"source": "Google Places API", "purpose": "populate /api/discovery", '
        '"delegate_to": "researcher"}',
        "  ],",
        '  "env_vars": ["GOOGLE_PLACES_API_KEY", "DATABASE_URL"],',
        '  "external_apis": [',
        '    {"name": "Google Places", "docs_url": "https://...", '
        '"sample_response_needed": true}',
        "  ],",
        '  "fixtures": [',
        '    {"file": "fixtures/sample_businesses.json", '
        '"description": "10 sample business records for testing"}',
        "  ]",
        "}",
        "```",
        "",
        "**Rules:**",
        "- Only bootstrap data that is **strictly required** for the TDD "
        "'Red' phase — don't crawl for nice-to-have data",
        "- If any prerequisite requires external data, delegate to "
        "`researcher` via `call_subordinate` in **parallel** with "
        "your architecture work",
        "- For larger data needs (API docs crawling, sample dataset "
        "generation), create an **adhoc scheduled task** with "
        "`scheduler:create_adhoc_task` targeting the `researcher` profile "
        "— this runs in its own context without blocking your main flow",
        "- API schema fetching: use `code_execution_tool` with curl to "
        "grab real sample responses from documented endpoints",
        "- For missing env vars: create a `.env.example` listing all "
        "required variables with descriptions",
        "",
    ]
    return "\n".join(lines)


def format_testability_audit() -> str:
    """Build testability audit questions for injection during TDD phase.

    Forces the agent to think about what the system needs to be fully
    testable BEFORE writing any test files. These 4 questions surface
    data dependencies that might otherwise cause test failures.
    """
    lines = [
        "",
        "🔍 **TESTABILITY AUDIT** — Before writing tests, answer these "
        "4 questions:",
        "",
        "1. **Sample Data**: What test data do API routes need to return "
        "meaningful responses? (Create fixture files with realistic data)",
        "2. **Environment Variables**: Which env vars must exist for "
        "integrations to work? (Create `.env.example` if missing)",
        "3. **Mock/Stub Services**: What external services need mock or "
        "stub implementations for offline testing? (Don't hit real APIs "
        "in unit tests)",
        "4. **Database Seed State**: What database state (if any) must "
        "be seeded for tests to pass? (Create seed scripts or fixtures)",
        "",
        "**Add answers to your task plan.** If any answer reveals a "
        "missing prerequisite, resolve it BEFORE writing tests.",
        "",
    ]
    return "\n".join(lines)


def format_structured_plan_guidance() -> str:
    """Build structured plan format guidance for the architect pass.

    Requires the architect to output a machine-parsable ENGINEERING BLUEPRINT
    that includes goals, assumptions, prerequisites, execution phases, AND
    complete technical specs: API contracts, data models, component bindings,
    and service library definitions.

    This upstream completeness eliminates the root cause of stub/mock code:
    developer agents receive exact response shapes, entity definitions, and
    wiring instructions instead of guessing from route lists.

    NOTE: Architect specifies task CATEGORIES, not agent assignments.
    Agent routing is multiagentdev's responsibility.

    The plan MUST be saved as architect_plan.json so:
    1. The Blueprint Verifier can validate completeness before development starts
    2. The codebase_state_injector propagates contracts to all developer agents
    3. The E2E gate can verify implementation coverage against the architect's intent
    """
    lines = [
        "",
        "📋 **ENGINEERING BLUEPRINT FORMAT** — Your architecture plan MUST "
        "be a complete engineering specification, not just a route list. "
        "Save it as `architect_plan.json` in the project root.",
        "",
        "```json",
        "{",
        '  "goal": "What the system must accomplish",',
'  "assumptions": [',
'    "User has the required runtime installed",',
'    "API keys are available for external services"',
'  ],',
'  "prerequisites": {',
'    "data": ["Sample records for testing"],',
'    "env_vars": ["API_KEY", "DATABASE_URL"],',
'    "external_services": ["Payment API", "Email service"]',
'  },',
"",
'  "planned_routes": [',
'    {"path": "/", "component": "HomePage", "file": "src/app/page.tsx"},',
'    {"path": "/dashboard", "component": "DashboardPage", "file": "src/app/dashboard/page.tsx"},',
'    {"path": "/dashboard/items", "component": "ItemsPage", "file": "src/app/dashboard/items/page.tsx"},',
'    {"path": "/about", "component": "AboutPage", "file": "src/app/about/page.tsx"}',
'  ],',
'  "planned_api_routes": ["/api/items", "/api/search"],',
"",
'  "api_contracts": {',
'    "/api/items": {',
'      "file": "src/app/api/items/route.ts",',
'      "GET": {',
'        "response_shape": {',
'          "items": [{"id": "string", "title": "string", "status": "string", "created_at": "string"}]',
'        }',
'      },',
'      "POST": {',
'        "request_body": {"title": "string", "description": "string", "category": "string"},',
'        "response_shape": {"success": "boolean", "id": "string", "created_at": "string"}',
'      }',
'    },',
'    "/api/search": {',
'      "file": "src/app/api/search/route.ts",',
'      "GET": {',
'        "response_shape": {',
'          "results": [{"id": "string", "name": "string", "description": "string", "score": "number"}]',
'        }',
'      }',
'    }',
'  },',
"",
'  "data_models": {',
'    "Item": {"id": "string", "title": "string", "description": "string", "status": "string", "created_at": "string"},',
'    "SearchResult": {"id": "string", "name": "string", "description": "string", "score": "number"}',
'  },',
"",
'  "component_bindings": {',
'    "/dashboard": {',
'      "file": "src/app/dashboard/page.tsx",',
'      "data_source": "GET /api/items",',
'      "state_pattern": "useState + useEffect + fetch",',
'      "renders": "Summary stats from Item[]"',
'    },',
'    "/dashboard/items": {',
'      "file": "src/app/dashboard/items/page.tsx",',
'      "data_source": "GET /api/items",',
'      "state_pattern": "useState + useEffect + fetch",',
'      "renders": "ItemCard list from Item[]"',
'    }',
'  },',
"",
'  "service_libs": {',
'    "src/lib/external-api.ts": {',
'      "exports": ["searchItems", "getItemDetails"],',
'      "used_by": ["/api/search"]',
'    }',
'  },',
"",
'  "phases": [',
'    {"id": 1, "name": "Project Scaffolding + Framework Config", ',
'"depends_on": [], "category": "frontend"},',
'    {"id": 2, "name": "Data Models + Service Libs", ',
'"depends_on": [1], "category": "backend"},',
'    {"id": 3, "name": "API Routes (implement contracts)", ',
'"depends_on": [2], "category": "backend"},',
'    {"id": 4, "name": "UI Pages + Components (follow bindings)", ',
'"depends_on": [1], "category": "frontend"},',
'    {"id": 5, "name": "Integration Wiring + E2E Testing", ',
'"depends_on": [3, 4], "category": "integration"}',
'  ],',
'  "risk_assessment": {',
'    "high_risk": ["API integration with rate-limited service"],',
'    "needs_researcher": ["Fetch sample API responses before Phase 2"]',
'  }',
"}",
        "```",
        "",
        "### 🔴 MANDATORY SECTIONS (Blueprint Verifier will block without these):",
        "",
        "**`file` fields are REQUIRED**: Every entry in `planned_routes`, "
        "`api_contracts`, and `component_bindings` MUST include a `file` "
        "field specifying the exact relative file path where that route, "
        "endpoint, or component will be implemented. The Blueprint Verifier "
        "uses these paths to confirm code exists at the specified locations.",
        "",
        "**`api_contracts`**: For EVERY endpoint in `planned_api_routes`, define "
        "the HTTP methods, request bodies, and **exact response shapes**. Include "
        "field names and types. Developer agents use these as the spec — if you "
        "don't define the shape, they'll write `res.json({ message: 'ok' })`.",
        "",
        "**`data_models`**: Define EVERY entity type referenced in your contracts. "
        "These become the TypeScript interfaces and database schemas. Field names "
        "MUST match what's in `api_contracts`.",
        "",
        "**`component_bindings`**: For EVERY dynamic page (dashboards, data views), "
        "specify which API to call (`data_source`), how to manage state "
        "(`state_pattern`), and what to render (`renders`). Static pages like "
        "`/`, `/about` don't need bindings.",
        "",
        "**`service_libs`**: For EVERY external service integration, define "
        "where the lib lives, what it exports, and which routes use it. "
        "This prevents orphan libs that get written but never imported.",
        "",
        "### ⚠️ Other Rules:",
        "",
        "**Save as `architect_plan.json`** in the project root. The Blueprint "
        "Verifier validates completeness before any developer agent starts.",
        "",
        "**Do NOT specify which agent or mode to use.** "
        "Use `category` (frontend, backend, infrastructure, integration, testing) ",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# initialTodos Propagation — Roo-Code NewTaskTool port
# ═══════════════════════════════════════════════════════════════════════


def get_subtasks_for_delegation(
    task_list: Optional[List[Dict[str, str]]],
    delegation_message: str,
) -> List[Dict[str, str]]:
    """Extract relevant pending/in-progress subtasks for a delegation target.

    Uses simple keyword overlap scoring to identify which parent tasks
    are relevant to the subordinate's work described in delegation_message.

    Ported from Roo-Code's NewTaskTool — parent passes initialTodos to child.

    Args:
        task_list: Parent agent's full _task_list.
        delegation_message: The message/prompt being sent to the subordinate.

    Returns:
        List of TodoItem dicts relevant to the delegation (pending/in_progress only).
    """
    if not task_list or not delegation_message:
        return []

    delegation_words = set(delegation_message.lower().split())
    # Remove very common stop words to avoid false matches
    stop_words = {
        "the", "a", "an", "is", "are", "to", "for", "and", "or", "of",
        "in", "on", "at", "by", "with", "from", "this", "that", "it",
        "be", "as", "do", "not", "all", "use", "you", "your", "will",
        "should", "must", "can", "has", "have", "was", "been", "would",
    }
    delegation_words -= stop_words

    matches: List[Dict[str, str]] = []

    for task in task_list:
        if task.get("status") == "completed":
            continue  # Only propagate incomplete tasks

        content = task.get("content", "")
        content_words = set(content.lower().split()) - stop_words

        # Calculate word overlap score
        overlap = delegation_words & content_words
        if len(overlap) >= 2 or (
            len(content_words) <= 3 and len(overlap) >= 1
        ):
            matches.append(task)

    return matches


def format_initial_todos(todos: List[Dict[str, str]]) -> str:
    """Format task items as markdown for injection into subordinate's system prompt.

    Args:
        todos: List of TodoItem dicts to format.

    Returns:
        Markdown string with checklist and instructions for the subordinate.
    """
    if not todos:
        return ""

    lines = [
        "## Inherited Tasks from Parent Agent",
        "",
        "The following tasks have been assigned to you by the parent "
        "orchestrator. Track your progress using `update_task_list`:",
        "",
    ]

    for t in todos:
        status = t.get("status", "pending")
        content = t.get("content", "Unknown")
        if status == "completed":
            marker = "[x]"
        elif status == "in_progress":
            marker = "[-]"
        else:
            marker = "[ ]"
        lines.append(f"- {marker} {content}")

    lines.extend([
        "",
        "Use `update_task_list` to mark these as in_progress when you "
        "start working on them and completed when done.",
    ])

    return "\n".join(lines)
