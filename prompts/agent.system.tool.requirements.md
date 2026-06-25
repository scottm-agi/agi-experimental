### requirements

Manage the requirements ledger — the central source of truth for project requirements tracking.

**Actions:**

- `init` — Bootstrap the ledger from extracted prompt requirements (Phase 0). Pass `requirements: [{"text": "...", "category": "..."}]`. Idempotent — skips if ledger already populated.
- `list` — Show all tracked requirements and their current status (pending, assigned, completed, verified, escalated)
- `coverage` — Show coverage statistics: how many requirements are assigned vs unassigned
- `check_coverage` — **Phase 2.5 gate**: Verify EVERY requirement has a decomposition phase assignment. Returns PASS or FAIL with specific unassigned requirements. MUST return PASS before Phase 3.
- `suggest` — Return unassigned requirement IDs ready for your next delegation
- `update` — Add new requirements dynamically. Pass `requirements: [{"text": "...", "category": "..."}]` or single `text` + `category`.
- `mark_complete` — Mark requirements as completed. Pass `requirement_ids: ["REQ-001", "REQ-003"]` (batch — preferred) or `requirement_id: "REQ-XXX"` (single). Auto-persists to disk.
- `save_manifest` — Save planning artifacts (content_manifest.json, decomposition_index.json). Pass `filename` and `content`. Idempotent — skips if file already exists with valid data.
- `save_bdd_scenarios` — **Phase 2 (architect)**: Save BDD acceptance scenarios with mandatory REQ-ID traceability. Pass `scenarios: [{"req_ids": ["REQ-001"], "feature": "...", "scenario": "...", "given": "...", "when": "...", "then": ["..."]}]`. Tool validates REQ-IDs exist, checks ≥90% coverage, formats Gherkin markdown, and persists to `docs/bdd-scenarios.md`. Returns missing REQ-IDs if coverage is below threshold — you MUST call again with the missing ones.

**When to use:**
- **Phase 0**: Use `init` to bootstrap requirements, `save_manifest` to create manifests and decomposition indexes
- Before each delegation wave: `suggest` to check which requirements still need assignment
- After delegations complete: `coverage` to verify progress
- **Phase 2 (architect enrichment)**: Use `save_bdd_scenarios` to save BDD acceptance scenarios — do NOT use `save_deliverable` or `write_to_file` for BDD. The tool enforces REQ-ID traceability and coverage checks.
- **Phase 2.5**: `check_coverage` to verify ALL requirements have decomposition assignments (BLOCKING before Phase 3)
- When the completion gate blocks on unassigned requirements: `list` to see details
- After architect returns a plan: `update` to add discovered sub-requirements
- When subordinate completes work: `mark_complete` to update requirement status

**🔴 CRITICAL:** This tool replaces ALL `write_to_file` usage for planning artifacts. Do NOT use `write_to_file` — it is blocked for the orchestrator profile.

~~~json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "init",
        "requirements": [
            {"text": "Booking link: https://example.com/book", "category": "url"},
            {"text": "Payment gateway integration", "category": "integration"}
        ]
    }
}
~~~

~~~json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "save_manifest",
        "filename": "content_manifest.json",
        "content": {"urls": ["https://example.com"], "integrations": ["stripe"]}
    }
}
~~~

~~~json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "suggest"
    }
}
~~~

~~~json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "mark_complete",
        "requirement_ids": ["REQ-001", "REQ-003", "REQ-005"]
    }
}
~~~

~~~json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "check_coverage"
    }
}
~~~

**🔴 BDD Scenarios (Phase 2 — MANDATORY for web/fullstack projects):**
~~~json
{
    "tool_name": "requirements",
    "tool_args": {
        "action": "save_bdd_scenarios",
        "scenarios": [
            {
                "req_ids": ["REQ-001", "REQ-005"],
                "feature": "Payment Integration",
                "scenario": "Stripe Self-Service Signup",
                "given": "a new customer visits /pricing",
                "when": "they click the 'Get Started' CTA",
                "then": [
                    "they MUST be redirected to the Stripe checkout at https://buy.stripe.com/xxx",
                    "the checkout MUST show $200/month pricing"
                ]
            },
            {
                "req_ids": ["REQ-002"],
                "feature": "Landing Page",
                "scenario": "Hero section displays brand",
                "given": "the page loads at /",
                "when": "the hero section renders",
                "then": [
                    "an h1 MUST contain the brand name",
                    "a primary CTA button MUST be visible"
                ]
            }
        ]
    }
}
~~~

**Why `save_bdd_scenarios` instead of `save_deliverable`:**
- `save_deliverable` = free-form markdown → REQ-IDs get dropped by the LLM
- `save_bdd_scenarios` = structured input → tool GUARANTEES REQ-IDs are embedded in the output
- Tool returns missing REQ-IDs so you know exactly what to add
- Coverage gate (≥90%) blocks Phase 2 completion until satisfied
