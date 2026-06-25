"""
Requirements Management Tool

Gives the orchestrator LLM direct access to the requirements ledger:
  - init: Bootstrap the ledger from extracted prompt requirements (Phase 0)
  - list: Show all tracked requirements with their status
  - coverage: Show coverage statistics (total/assigned/completed/unassigned)
  - suggest: Return unassigned requirement IDs ready for delegation
  - update: Add new requirements dynamically
  - mark_complete: Mark a requirement as completed
  - save_manifest: Persist content_manifest.json, decomposition_index.json, or requirements_ledger.json

This replaces all write_to_file usage for planning artifacts.
Multiagentdev should NEVER use write_to_file — all writes go through this tool.

Architecture: Thin tool wrapper around python.helpers.requirements_ledger.
"""
from __future__ import annotations

import glob
import json
import os
import logging
from typing import Any

from python.helpers.tool import Tool, Response
from python.helpers.planning_paths import get_path as _planning_path
from python.helpers.requirements_ledger import (
    get_coverage,
    get_unassigned_requirements,
    add_requirement,
    mark_requirement_complete,
    check_assignment_coverage,
    init_requirements,
    supplement_from_prompt,
    _ensure_ledger,
)

logger = logging.getLogger("agix.requirements_tool")

# Module-level import for project resolution — used by _ensure_active_project_dir
# and all action handlers. Imported here so it can be patched cleanly in tests.
from python.helpers import projects
from python.helpers.projects import get_decomp_index_path


from python.tools.requirements_config import _MANDATORY_PHASES, _PHASE_ARTIFACT_MAP, _reconciler_warnings, _ensure_active_project_dir
from python.tools.requirements_manifest import _normalize_manifest_schema, _validate_manifest_model_name, _enrich_tech_stack_from_research, _validate_models_for_llm_integrations
from python.tools.requirements_sync import _validate_mandatory_phases, _sync_decomp_assignments_to_ledger, _sync_decomposition_plan_to_agent_data, _reconcile_mandatory_phase_status, detect_artifact_gaps, _filter_deferred_decomposition_phases, _seed_integration_from_dep_graph, _detect_dropped_phases, _reinject_dropped_phases

def _handle_list(agent_data: dict) -> str:
    """Return all requirements with their current status."""
    ledger = _ensure_ledger(agent_data)
    reqs = ledger.get("requirements", [])

    if not reqs:
        return (
            "📋 No requirements in ledger (0 total). "
            "Requirements are populated automatically from the user's prompt "
            "via the goal tracking extension."
        )

    lines = [f"📋 **Requirements Ledger** — {len(reqs)} total\n"]
    # Group by status
    status_order = ["pending", "assigned", "completed", "verified", "unverified", "escalated"]
    status_emoji = {
        "pending": "⏳",
        "assigned": "🔄",
        "completed": "✅",
        "verified": "✅✅",
        "unverified": "⚠️",
        "escalated": "🚨",
    }

    for status in status_order:
        group = [r for r in reqs if r.get("status") == status]
        if group:
            emoji = status_emoji.get(status, "•")
            lines.append(f"\n### {emoji} {status.upper()} ({len(group)})")
            for r in group:
                text_preview = r.get("text", "")[:80]
                lines.append(f"- `{r['id']}`: {text_preview}")

    return "\n".join(lines)

def _handle_coverage(agent_data: dict) -> str:
    """Return coverage statistics."""
    stats = get_coverage(agent_data)

    total = stats["total_requirements"]
    if total == 0:
        return "📊 No requirements tracked yet. Coverage: N/A"

    assigned = stats["assigned"]
    completed = stats["completed"]
    unassigned = stats["unassigned"]
    pct = round((assigned / total) * 100)

    lines = [
        f"📊 **Requirements Coverage**: {assigned}/{total} assigned ({pct}%)",
        f"  - Completed: {completed}/{total}",
        f"  - Assigned (in progress): {assigned - completed}",
        f"  - Unassigned: {len(unassigned)}",
    ]

    if unassigned:
        lines.append(f"\n**Unassigned IDs** (include these in your next delegation):")
        for uid in unassigned:
            lines.append(f"  - `{uid}`")

    return "\n".join(lines)

def _handle_check_coverage(agent_data: dict, args: dict = None, agent_context=None) -> str:
    """Check that every requirement has a decomposition phase assignment.

    RCA-362 L2 Fix: Deterministic gate for Phase 2.5 — verifies that
    no requirements were extracted but left unassigned. Returns structured
    pass/fail with the specific unassigned requirements for the orchestrator
    to create missing decomposition phases.

    RCA-ITR355 RC-E: Before checking, auto-sync from decomposition_index.json
    on disk. The decomposition may have req_guids mapped but the in-memory
    ledger still shows 'pending' because the ISS-R4 sync only runs during
    save_manifest. This pre-check sync prevents false 0% coverage FAIL.
    """
    # GAP-1 FIX: Persist _active_project_dir to agent_data for downstream consumers
    _ensure_active_project_dir(agent_data, agent_context)

    # RC-E: Auto-sync from decomposition on disk before checking
    try:
        from python.helpers import projects
        project_name = projects.get_context_project_name(
            agent_context
        ) if agent_context else None
        if project_name:
            project_dir = projects.get_project_folder(project_name)
            decomp_path = get_decomp_index_path(project_dir)
            if os.path.exists(decomp_path):
                with open(decomp_path, "r", encoding="utf-8") as f:
                    phases = json.load(f)
                if isinstance(phases, list):
                    _sync_decomp_assignments_to_ledger(agent_data, phases)
    except Exception as sync_err:
        logger.debug(
            f"[REQUIREMENTS TOOL] RC-E: Pre-check decomp sync failed (non-fatal): "
            f"{sync_err}"
        )

    result = check_assignment_coverage(agent_data)

    if result["total_requirements"] == 0:
        return "📊 No requirements in ledger — nothing to check."

    if result["complete"]:
        msg = (
            f"✅ **Assignment Coverage: PASS** — "
            f"{result['total_requirements']}/{result['total_requirements']} "
            f"requirements have decomposition assignments (100%)"
        )
        # ITR-22: Surface any reconciler warnings in the return message
        # so _12_tool_failure_tracker can detect and trigger retry/escalation.
        if _reconciler_warnings:
            msg += "\n\n" + "\n".join(_reconciler_warnings)
            _reconciler_warnings.clear()
        return msg

    # Build actionable failure message
    lines = [
        f"❌ **Assignment Coverage: FAIL** — "
        f"{result['unassigned_count']}/{result['total_requirements']} "
        f"requirements have NO decomposition phase assignment "
        f"({result['coverage_pct']}% covered)",
        "",
        "**Unassigned requirements** (MUST be added to decomposition_index.json):",
    ]
    for req in result["unassigned_requirements"]:
        lines.append(f"  - `{req['id']}` [{req.get('category', '?')}]: {req['text'][:80]}")

    lines.append("")
    lines.append(
        "🔴 **ACTION REQUIRED**: Create decomposition phases for these "
        "requirements BEFORE proceeding to Phase 3 implementation."
    )

    # ITR-22: Surface reconciler warnings so _12_tool_failure_tracker sees them.
    if _reconciler_warnings:
        lines.append("")
        lines.extend(_reconciler_warnings)
        _reconciler_warnings.clear()

    return "\n".join(lines)

def _handle_suggest(agent_data: dict) -> str:
    """Return unassigned requirements ready for delegation."""
    unassigned = get_unassigned_requirements(agent_data)

    if not unassigned:
        return (
            "✅ All requirements are assigned or completed. "
            "No unassigned requirements remaining."
        )

    lines = [
        f"🎯 **{len(unassigned)} requirements need delegation**\n",
        "Include these `requirement_ids` in your next `call_subordinate` call:\n",
    ]

    # Group by category if available
    for r in unassigned:
        text_preview = r.get("text", "")[:80]
        category = r.get("category", "uncategorized")
        lines.append(f"- `{r['id']}` [{category}]: {text_preview}")

    # Provide copy-pasteable format
    ids_list = [r["id"] for r in unassigned]
    lines.append(f"\n**Copy-paste**: `\"requirement_ids\": {json.dumps(ids_list)}`")

    return "\n".join(lines)

def _handle_init(agent_data: dict, args: dict = None, agent_context=None, original_prompt: str = "") -> str:
    """Bootstrap the requirements ledger from extracted prompt requirements.

    This is the Phase 0 entry point — called ONCE at the start of orchestration
    to populate the ledger. After LLM init, runs the regex extractor to auto-
    supplement any missing requirements (RCA-ITR4-001 fix).

    Args:
        agent_data: The agent.data dict
        args: Dict with 'requirements' key — list of {text, category} dicts.
        agent_context: The agent context (used to retrieve original prompt)
        original_prompt: The original user prompt text (extracted by caller)
    """
    # GAP-1 FIX: Persist _active_project_dir to agent_data for downstream consumers
    _ensure_active_project_dir(agent_data, agent_context)

    args = args or {}
    requirements = args.get("requirements", [])

    if not requirements:
        return (
            "⚠️ No requirements provided. Pass 'requirements': "
            '[{"text": "...", "category": "..."}]'
        )

    # Idempotent: check if ledger already has requirements
    ledger = _ensure_ledger(agent_data)
    existing = ledger.get("requirements", [])
    if len(existing) >= 1:
        return (
            f"✅ Ledger already initialized with {len(existing)} requirements. "
            f"Skipping re-init (idempotent). Use 'update' action to add more."
        )

    init_requirements(agent_data, requirements)

    # RCA-ITR4-001: Auto-supplement from original prompt using regex extractor.
    # The LLM's manual extraction often misses implicit requirements (drip
    # automation, physical address, GitHub push, review capture, etc.).
    # Run the deterministic extractor to catch anything missed.
    supplemented = 0
    try:
        if original_prompt and len(original_prompt) > 50:
            supplemented = supplement_from_prompt(agent_data, original_prompt)
            if supplemented > 0:
                logger.info(
                    f"[REQUIREMENTS TOOL] Auto-supplemented {supplemented} "
                    f"requirements from prompt regex extractor"
                )
    except Exception as e:
        logger.warning(f"[REQUIREMENTS TOOL] Auto-supplement failed (non-fatal): {e}")

    ledger = _ensure_ledger(agent_data)
    count = len(ledger.get("requirements", []))
    ids = [r["id"] for r in ledger.get("requirements", [])]

    supplement_msg = ""
    if supplemented > 0:
        supplement_msg = f" (+{supplemented} auto-supplemented from prompt analysis)"

    # ISS-R1 FIX: Auto-persist ledger to disk.
    # Root cause: _handle_init only populated agent.data (memory) but never
    # wrote requirements_ledger.json to disk. The LLM was never instructed
    # to call save_manifest for the ledger, so it never happened.
    # RCA-312 F-2: MUST use persist_ledger_to_project() (which filters
    # _-prefixed internal keys) instead of _handle_save_manifest() (which doesn't).
    persist_msg = ""
    try:
        from python.helpers import projects
        project_name = projects.get_context_project_name(
            agent_context
        ) if agent_context else None
        project_dir = None
        if project_name:
            project_dir = projects.get_project_folder(project_name)

        if project_dir:
            from python.helpers.requirements_ledger import persist_ledger_to_project
            persist_ledger_to_project(agent_data, project_dir)
            persist_msg = " (auto-persisted to disk)"
            logger.info(f"[REQUIREMENTS TOOL] Auto-persisted ledger via persist_ledger_to_project")

            # F-0 (ITR-11): Auto-generate test skeleton from the ledger.
            # Root cause: skeleton_generator.py existed but was NEVER called.
            # The LLM was generating the skeleton manually (7/81 REQs).
            # Now deterministic: one entry per REQ-ID, every time.
            #
            # 5-Layer Defense:
            #   L0: SKILL.md instructs "generate test_skeleton" (advisory)
            #   L1: This deterministic call (generate_test_skeleton)
            #   L2: TDD tests verify 1:1 REQ-to-skeleton mapping
            #   L3: Phase 2.7 gate verifies docs/test-skeleton.json exists
            try:
                from python.helpers.skeleton_generator import (
                    generate_test_skeleton,
                    generate_bdd_skeleton,
                    generate_tdd_tests,  # noqa: F401 — imported for check_coverage action
                )
                # RCA-461 R-1: Pass original_prompt for scoped literal matching.
                # At Phase 0 the manifest doesn't exist yet, so this is the
                # only source of expected_literals until Phase 2.7 re-generation.
                skeleton = generate_test_skeleton(project_dir, original_prompt=original_prompt)
                skeleton_count = len(skeleton.get("requirements", []))
                persist_msg += f" + test-skeleton ({skeleton_count} entries)"
                logger.info(
                    f"[REQUIREMENTS TOOL] F-0: Auto-generated test skeleton "
                    f"with {skeleton_count} entries for {count} requirements"
                )

                # Also generate BDD skeleton for web projects
                bdd_result = generate_bdd_skeleton(project_dir)
                if bdd_result:
                    persist_msg += " + bdd-scenarios-skeleton"
                    logger.info(
                        "[REQUIREMENTS TOOL] F-0: Auto-generated BDD skeleton "
                        "for web project"
                    )

                # ITR-19 FIX: TDD stubs are NO LONGER generated in Phase 0.
                # Root cause: generate_tdd_tests calls detect_project_language
                # which checks package.json — but package.json doesn't exist
                # until Phase 1 (scaffold). This caused Python stubs for TS
                # projects. TDD stubs are now generated in check_coverage
                # action (Phase 2.7) when the scaffold already exists.
            except Exception as skel_err:
                logger.warning(
                    f"[REQUIREMENTS TOOL] F-0: Skeleton generation failed "
                    f"(non-fatal): {skel_err}"
                )

            # F-2 FIX: Reconcile decomposition_index phase statuses after init.
            # If decomposition_index.json already exists (from a previous run or
            # re-init), reconcile auto-injected phase statuses against artifacts.
            try:
                decomp_path = get_decomp_index_path(project_dir)
                if os.path.isfile(decomp_path):
                    with open(decomp_path, "r", encoding="utf-8") as df:
                        decomp_phases = json.load(df)
                    if isinstance(decomp_phases, list):
                        decomp_phases = _reconcile_mandatory_phase_status(decomp_phases, project_dir, agent_data=agent_data)
                        with open(decomp_path, "w", encoding="utf-8") as df:
                            json.dump(decomp_phases, df, indent=2, ensure_ascii=False)
                        logger.info(
                            "[REQUIREMENTS TOOL] F-2: Reconciled phase statuses "
                            "in decomposition_index.json during init"
                        )
                        # Wire 1: Seed integration requirements from dependency graph
                        try:
                            _seed_integration_from_dep_graph(
                                project_dir, agent_data,
                                decomp_phases,
                            )
                        except Exception as w1_err:
                            logger.debug(f"[INTEGRATION SEEDING] Init hook failed: {w1_err}")
            except Exception as f2_err:
                logger.debug(
                    f"[REQUIREMENTS TOOL] F-2 init reconciliation failed (non-fatal): "
                    f"{f2_err}"
                )

            # ARCH-RCSIG: Chat-scoped decomposition reconciliation.
            # When a new chat session starts (e.g., after container restart),
            # reset implementation/integration/verification/delivery phase
            # statuses to prevent phantom completions from persisting.
            try:
                from python.helpers.decomp_chat_scope import reconcile_chat_session
                chat_id = ""
                if agent_context:
                    chat_id = getattr(agent_context, 'id', '') or ''
                    if not chat_id:
                        chat_id = getattr(getattr(agent_context, 'context', None), 'id', '') or ''
                if chat_id and project_dir:
                    scope_result = reconcile_chat_session(project_dir, chat_id)
                    if scope_result.get("phases_reset", 0) > 0:
                        logger.warning(
                            f"[ARCH-RCSIG] New chat session — reset "
                            f"{scope_result['phases_reset']} runtime phase(s)"
                        )
            except Exception as cs_err:
                logger.debug(
                    f"[ARCH-RCSIG] Chat scope reconciliation failed (non-fatal): {cs_err}"
                )

            # ITR-39 SYSTEM 1a: Seed TraceabilityIndex from the ledger.
            # Root cause: TraceabilityIndex (243 lines) had ZERO production callers.
            # This deterministic call seeds the index with all REQ-IDs so downstream
            # consumers (gate_quality, TDD stubs) can do forward/reverse lookups.
            try:
                from python.helpers.traceability_index import seed_traceability_from_ledger
                seed_traceability_from_ledger(agent_data, project_dir)
                persist_msg += " + traceability-index"
                logger.info(
                    "[REQUIREMENTS TOOL] ITR-39: Seeded TraceabilityIndex "
                    f"from ledger ({count} requirements)"
                )
            except Exception as trace_err:
                logger.warning(
                    f"[REQUIREMENTS TOOL] ITR-39: Traceability seeding failed "
                    f"(non-fatal): {trace_err}"
                )
    except Exception as e:
        logger.warning(f"[REQUIREMENTS TOOL] Auto-persist failed (non-fatal): {e}")

    return (
        f"✅ Requirements ledger initialized with {count} requirements{supplement_msg}: "
        f"{', '.join(ids)}{persist_msg}"
    )

def _handle_save_bdd_scenarios(
    agent_data: dict, args: dict = None, agent_context=None
) -> str:
    """Save structured BDD scenarios with enforced REQ-ID traceability.

    Args:
        agent_data: The agent.data dict
        args: Dict with:
            - 'scenarios': List of dicts, each with:
                req_ids: List[str] — REQ-IDs this scenario covers (REQUIRED)
                feature: str — Feature name
                scenario: str — Scenario name
                given: str — Given clause
                when: str — When clause
                then: List[str] — Then clauses
        agent_context: The agent.context for project resolution
    """
    args = args or {}
    scenarios = args.get("scenarios", [])

    if not scenarios or not isinstance(scenarios, list):
        return (
            "⚠️ Missing 'scenarios' list. Provide structured BDD scenarios:\n"
            '```json\n'
            '{"action": "save_bdd_scenarios", "scenarios": [\n'
            '  {"req_ids": ["REQ-001"], "feature": "Payments",\n'
            '   "scenario": "Stripe checkout",\n'
            '   "given": "user on /pricing",\n'
            '   "when": "clicks Pay",\n'
            '   "then": ["redirected to Stripe checkout", "sees $200/mo"]}\n'
            "]}\n"
            "```"
        )

    # GAP-1 FIX: Persist _active_project_dir to agent_data for downstream consumers
    _ensure_active_project_dir(agent_data, agent_context)

    # Resolve project directory
    try:
        from python.helpers import projects, files as file_utils
        project_name = projects.get_context_project_name(
            agent_context
        ) if agent_context else None

        if project_name:
            project_dir = projects.get_project_folder(project_name)
        else:
            project_dir = file_utils.get_abs_path("tmp")
    except Exception as e:
        return f"⚠️ Cannot save BDD: failed to resolve project directory ({e})."

    # Load test skeleton for validation
    skeleton_path = os.path.join(project_dir, "docs", "test-skeleton.json")
    skeleton_reqs = []
    if os.path.isfile(skeleton_path):
        try:
            with open(skeleton_path) as f:
                skeleton_data = json.load(f)
            skeleton_reqs = skeleton_data.get("requirements", [])
        except (json.JSONDecodeError, IOError):
            pass

    # Validate + check coverage
    from python.helpers.skeleton_generator import (
        validate_bdd_scenario_input,
        assemble_bdd_from_structured,
    )

    validation = validate_bdd_scenario_input(scenarios, skeleton_reqs)

    # Assemble BDD markdown
    bdd_content = assemble_bdd_from_structured(scenarios)

    # RCA-470: Auto-inject delivery standard scenarios missed by the LLM.
    # REQ-SCAFFOLD-001, REQ-DELIVERY-*, REQ-INFRA-* are in test-skeleton
    # with bdd_needed=True but the LLM reliably misses them. At 100%
    # threshold, the gate would block. Inject deterministically, then re-check.
    if skeleton_reqs and not validation.get("pass", True):
        from python.helpers.bdd_generator_creation import inject_missing_delivery_bdd
        bdd_content = inject_missing_delivery_bdd(bdd_content, skeleton_reqs)
        # Re-validate using text-based coverage (injected scenarios are in text)
        from python.helpers.bdd_validators import check_bdd_coverage
        recheck = check_bdd_coverage(skeleton_reqs, bdd_content)
        if recheck["pass"]:
            validation = recheck

    # F-9 (ITR-16) + RCA-474: BDD scenario count regression prevention.
    # BEFORE RCA-474: guard rejected writes with fewer scenarios, telling LLM
    # to "try again with ALL" — but the LLM never read the existing file,
    # creating an infinite retry loop (25→8→9→12→8→9...).
    # AFTER RCA-474: Auto-MERGE new scenarios into existing, dedup by
    # (req_ids_tuple + scenario_name). No rejection, no retry loop.
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    bdd_path = os.path.join(docs_dir, "bdd-scenarios.md")

    existing_scenario_count = 0
    existing_scenarios_json = []
    bdd_json_path = os.path.join(docs_dir, "bdd-scenarios.json")

    if os.path.isfile(bdd_path):
        try:
            with open(bdd_path, "r", encoding="utf-8") as ef:
                existing_content = ef.read()
            existing_scenario_count = sum(
                1 for line in existing_content.splitlines()
                if line.strip().startswith("Scenario:")
            )
        except (IOError, UnicodeDecodeError):
            pass

    # Load existing structured scenarios for merge
    if os.path.isfile(bdd_json_path):
        try:
            with open(bdd_json_path, "r", encoding="utf-8") as jf:
                existing_json = json.load(jf)
            if isinstance(existing_json, dict):
                existing_scenarios_json = existing_json.get("scenarios", [])
            elif isinstance(existing_json, list):
                existing_scenarios_json = existing_json
        except (json.JSONDecodeError, IOError):
            pass

    new_scenario_count = len(scenarios)

    if existing_scenario_count > 0 and new_scenario_count < existing_scenario_count and existing_scenarios_json:
        # RCA-474: Auto-merge instead of reject.
        # Dedup key: (sorted req_ids tuple, scenario name)
        def _dedup_key(s):
            rids = tuple(sorted(s.get("req_ids", [])))
            name = s.get("scenario", s.get("feature", ""))
            return (rids, name)

        seen = set()
        merged = []
        # Existing scenarios first (preserve order)
        for s in existing_scenarios_json:
            key = _dedup_key(s)
            if key not in seen:
                seen.add(key)
                merged.append(s)
        # Then new scenarios (add only unique ones)
        new_added = 0
        for s in scenarios:
            key = _dedup_key(s)
            if key not in seen:
                seen.add(key)
                merged.append(s)
                new_added += 1

        logger.info(
            f"[BDD TOOL] RCA-474: Auto-merged {new_scenario_count} new into "
            f"{existing_scenario_count} existing → {len(merged)} total "
            f"({new_added} unique new added)"
        )

        # Use merged set for downstream processing
        scenarios = merged
        bdd_content = assemble_bdd_from_structured(scenarios)
        # Re-validate with merged set
        validation = validate_bdd_scenario_input(scenarios, skeleton_reqs)

    with open(bdd_path, "w") as f:
        f.write(bdd_content)

    # F-0b: Persist raw structured scenarios as JSON for downstream consumers
    # (e.g. TDD generator) so they don't have to parse Gherkin markdown.
    # Wrapped in {"scenarios": [...]} for extensibility (future: add metadata).
    bdd_json_path = os.path.join(docs_dir, "bdd-scenarios.json")
    with open(bdd_json_path, "w", encoding="utf-8") as jf:
        json.dump({"scenarios": scenarios}, jf, indent=2, ensure_ascii=False)

    # Build response
    lines = [f"✅ BDD scenarios saved to {bdd_path}"]
    lines.append(
        f"Coverage: {validation['covered']}/{validation['total_bdd_needed']} "
        f"= {validation['coverage']:.0%}"
    )

    if validation.get("invalid_req_ids"):
        lines.append(
            f"⚠️ Invalid REQ-IDs (not in skeleton): {validation['invalid_req_ids']}"
        )

    if not validation["pass"] and validation.get("missing_req_ids"):
        lines.append(
            f"\n🔴 COVERAGE BELOW 90% — Missing {len(validation['missing_req_ids'])} REQ-IDs:\n"
            f"{', '.join(validation['missing_req_ids'][:20])}"
        )
        lines.append(
            "\nYou MUST call save_bdd_scenarios again with scenarios covering "
            "the missing REQ-IDs to pass the BDD coverage gate."
        )
    else:
        lines.append("✅ BDD coverage gate PASSED.")

    # F-6 (ITR-15): Surface granularity advisory hints to the LLM.
    # These are L1 advisory — the LLM decides whether to act.
    granularity_hints = validation.get("granularity_hints", [])
    if granularity_hints:
        avg_ratio = validation.get("avg_reqs_per_scenario", 0)
        lines.append(
            f"\n📊 **Granularity Advisory** (avg {avg_ratio:.1f} reqs/scenario):"
        )
        for hint in granularity_hints:
            lines.append(
                f"  ⚡ \"{hint['scenario']}\" covers {hint['req_count']} REQ-IDs — "
                f"{hint['suggestion']}"
            )

    # F-1 (ITR-16): Surface BDD template quality advisory to the LLM.
    # L1 advisory — tool provides pattern detection context, LLM decides.
    # Design: deterministic tools give weight & context, LLM is final arbiter.
    template_quality = validation.get("template_quality", {})
    if template_quality:
        tq_pass = template_quality.get("quality_pass", True)
        tq_count = template_quality.get("templated_count", 0)
        tq_total = template_quality.get("total_scenarios", 0)
        tq_ratio = template_quality.get("templated_ratio", 0)
        tq_hints = template_quality.get("quality_hints", [])

        if not tq_pass:
            lines.append(
                f"\n🔴 **BDD TEMPLATE QUALITY FAILED**: {tq_count}/{tq_total} "
                f"scenarios ({tq_ratio:.0%}) use generic template language.\n"
                f"You MUST enrich each scenario with domain-specific Given/When/Then "
                f"using content manifest values (names, prices, URLs, API endpoints).\n"
                f"Example: Instead of 'the feature implementation handles the core use case correctly',\n"
                f"write 'it queries Perplexity API for businesses in the given zip code'."
            )
            for hint in tq_hints[:5]:
                lines.append(
                    f"  ⚡ \"{hint['scenario']}\" ({', '.join(hint.get('req_ids', []))}) — "
                    f"{hint['suggestion']}"
                )
        elif tq_hints:
            lines.append(
                f"\n📊 **Template Quality Advisory**: {tq_count}/{tq_total} "
                f"scenarios ({tq_ratio:.0%}) use generic templates (below "
                f"{template_quality.get('threshold', 0.8):.0%} threshold). Consider enriching."
            )

    # Store validation result for Phase 2 gate
    agent_data["_bdd_validation"] = validation

    # ISS-7 FIX: Persist validation to disk so _reconcile_decomp_statuses
    # can read it without needing agent_data. The reconciler runs during
    # save_manifest (decomposition_index.json) which doesn't have agent_data.
    # Layer: L2 (Testable System) → L3 (Gate) bridge via disk persistence.
    try:
        validation_path = os.path.join(docs_dir, ".bdd_validation.json")
        with open(validation_path, "w", encoding="utf-8") as vf:
            json.dump(validation, vf, indent=2, ensure_ascii=False)
        logger.info(
            f"[BDD VALIDATION] Persisted to {validation_path} "
            f"(coverage={validation.get('coverage', 0):.0%})"
        )
    except Exception as persist_err:
        logger.warning(f"[BDD VALIDATION] Persist to disk failed — BDD scenarios may not be saved: {persist_err}")

    # FIX-5: Auto-generate requirement_test_mapping.json from BDD scenarios
    try:
        mapping = {}
        for scenario in scenarios:
            req_ids = scenario.get("req_ids", [])
            feature = scenario.get("feature", "unknown").lower().replace(" ", "_")
            for rid in req_ids:
                if rid not in mapping:  # Don't overwrite existing entries
                    mapping[rid] = {
                        "test_file": f"test_{feature}.test.ts",
                        "implementation_file": f"src/{feature}.tsx",
                        "bdd_feature": scenario.get("feature", ""),
                        "bdd_scenario": scenario.get("scenario", ""),
                    }
        if mapping:
            proj_meta_dir = os.path.join(project_dir, ".agix.proj")
            os.makedirs(proj_meta_dir, exist_ok=True)
            mapping_path = os.path.join(proj_meta_dir, "requirement_test_mapping.json")
            with open(mapping_path, "w", encoding="utf-8") as mf:
                json.dump(mapping, mf, indent=2, ensure_ascii=False)
            logger.info(
                f"[BDD] FIX-5: Auto-generated requirement_test_mapping.json "
                f"with {len(mapping)} REQ-ID mappings"
            )
    except Exception as mapping_err:
        logger.debug(f"[BDD] FIX-5: Mapping generation skipped: {mapping_err}")

    # ADR-086 Phase 3 Step 3-1: Set BDD stage status for matched REQ-IDs.
    # After BDD scenarios are saved, mark the bdd stage as completed for
    # each requirement referenced in the scenarios. This feeds the stage-
    # keyed status model so that overall status correctly reflects whether
    # BDD, TDD, and code stages are independently completed.
    try:
        from python.helpers.requirements_ledger import set_stage_status, _ensure_ledger
        import re

        # Collect all REQ-IDs from scenarios
        bdd_req_ids = set()
        for scenario in scenarios:
            for rid in scenario.get("req_ids", []):
                if re.match(r"REQ-[a-f0-9]+", rid, re.IGNORECASE):
                    bdd_req_ids.add(rid)

        if bdd_req_ids:
            ledger = _ensure_ledger(agent_data)
            bdd_stage_count = 0
            for req in ledger.get("requirements", []):
                if req.get("id") in bdd_req_ids:
                    set_stage_status(req, "bdd", "completed")
                    bdd_stage_count += 1
            if bdd_stage_count > 0:
                logger.info(
                    f"[BDD] ADR-086: Set bdd stage to 'completed' for "
                    f"{bdd_stage_count} requirements"
                )
    except Exception as stage_err:
        logger.debug(f"[BDD] ADR-086: BDD stage update skipped: {stage_err}")

    return "\n".join(lines)

def _handle_save_manifest(agent_data: dict, args: dict = None, agent_context=None) -> str:
    """Persist a planning artifact (content_manifest.json, decomposition_index.json).

    This replaces write_to_file for orchestrator planning artifacts.
    Files are written to the project directory.

    Args:
        agent_data: The agent.data dict
        args: Dict with:
            - 'filename': e.g. 'content_manifest.json' or 'decomposition_index.json'
            - 'content': JSON-serializable content (dict or list)
        agent_context: The agent.context for project resolution
    """
    args = args or {}
    filename = args.get("filename", "").strip()
    content = args.get("content")

    # RCA-457: Accept BOTH hyphen-case (canonical disk convention from
    # planning_paths.py) and underscore (legacy/internal convention).
    # The LLM may send either convention.
    ALLOWED_FILES = {
        "content_manifest.json", "content-manifest.json",
        "decomposition_index.json", "decomposition-index.json",
        "requirements_ledger.json", "requirements-ledger.json",
    }

    if not filename:
        return (
            f"⚠️ Missing 'filename'. Allowed files: {', '.join(sorted(ALLOWED_FILES))}"
        )

    if filename not in ALLOWED_FILES:
        return (
            f"⚠️ '{filename}' not in allowed files. "
            f"Allowed: {', '.join(sorted(ALLOWED_FILES))}"
        )

    # RCA-457: Normalize to underscore convention for all downstream
    # `filename == "decomposition_index.json"` checks. The file is
    # SAVED to the canonical hyphen path via planning_paths, but
    # internal processing uses underscores.
    filename = filename.replace("-", "_")

    if content is None:
        return "⚠️ Missing 'content'. Provide JSON content to save."

    # GAP-1 FIX: Persist _active_project_dir to agent_data for downstream consumers
    _ensure_active_project_dir(agent_data, agent_context)

    # Resolve project directory — use agent.context like all other tools
    try:
        from python.helpers import projects, files as file_utils
        project_name = projects.get_context_project_name(
            agent_context
        ) if agent_context else None

        if project_name:
            project_dir = projects.get_project_folder(project_name)
        else:
            project_dir = file_utils.get_abs_path("tmp")
            logger.warning(
                f"[REQUIREMENTS TOOL] No project context available for save_manifest. "
                f"Falling back to {project_dir}. This may cause stale data issues."
            )
    except Exception as e:
        return (
            f"⚠️ Cannot save '{filename}': failed to resolve project directory ({e}). "
            f"No active project context available."
        )

    # Map agent-supplied filenames to planning_paths keys.
    # After RCA-457 normalization above, filename is always underscore here.
    _FILENAME_TO_KEY = {
        "content_manifest.json": "content_manifest",
        "decomposition_index.json": "decomposition_index",
        "requirements_ledger.json": "requirements_ledger",
    }
    planning_key = _FILENAME_TO_KEY.get(filename)
    if planning_key:
        filepath = _planning_path(project_dir, planning_key)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
    else:
        filepath = os.path.join(project_dir, filename)

    # ISS-R2-v2: Normalize content_manifest.json to canonical schema
    if filename == "content_manifest.json" and isinstance(content, dict):
        content = _normalize_manifest_schema(content)
        # FIX-8: Enrich tech_stack with pinned versions from researcher output.
        # The researcher writes docs/framework-research.md with exact version
        # pins (e.g., "Next.js 15.0.0"). This enricher reads those pins and
        # updates the manifest's tech_stack so downstream agents use them.
        try:
            content = _enrich_tech_stack_from_research(content, project_dir)
        except Exception as enrich_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] FIX-8 tech_stack enrichment failed (non-fatal): "
                f"{enrich_err}"
            )
        # ITR-20 F-11: Sanitize secrets in manifest before writing to disk.
        # Replaces raw API keys with env-var references ({{STRIPE_SECRET_KEY}}, etc.)
        try:
            from python.helpers.requirements_sanitizer import sanitize_manifest_secrets
            content = sanitize_manifest_secrets(content)
        except Exception as san_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] ITR-20 F-11 manifest sanitization failed (non-fatal): "
                f"{san_err}"
            )
        # ITR-30 SS-7: Validate ai_model against the original user prompt
        # to catch training-data contamination (e.g., Claude 3.5 vs Sonnet 4)
        try:
            original_prompt = agent_data.get("original_prompt", "") if agent_data else ""
            if original_prompt and isinstance(content, dict):
                model_warnings = _validate_manifest_model_name(content, original_prompt)
                for warn in model_warnings:
                    logger.warning(f"[REQUIREMENTS TOOL] {warn}")
        except Exception as mv_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] ITR-30 SS-7 model validation failed (non-fatal): "
                f"{mv_err}"
            )
        # RCA-470 F-3: Validate that LLM integrations have a models section
        # with verified_slug. Without this, the code agent substitutes stale
        # model names from training data.
        try:
            if isinstance(content, dict):
                model_slug_warnings = _validate_models_for_llm_integrations(content)
                for warn in model_slug_warnings:
                    logger.warning(f"[REQUIREMENTS TOOL] {warn}")
        except Exception as ms_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] RCA-470 F-3 model slug validation failed (non-fatal): "
                f"{ms_err}"
            )

    # Idempotent: compare content hash — only skip if content is identical
    import hashlib
    new_content_str = json.dumps(content, indent=2, ensure_ascii=False, sort_keys=True)
    new_hash = hashlib.sha256(new_content_str.encode()).hexdigest()[:12]

    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing_str = f.read()
            existing_hash = hashlib.sha256(
                json.dumps(json.loads(existing_str), indent=2, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:12]
            if existing_hash == new_hash:
                return (
                    f"✅ {filename} already exists with identical content (hash={new_hash}). "
                    f"Skipping write (idempotent)."
                )
            else:
                logger.info(
                    f"[REQUIREMENTS TOOL] {filename} exists but content differs "
                    f"(old_hash={existing_hash}, new_hash={new_hash}). Overwriting."
                )
        except (json.JSONDecodeError, IOError):
            pass  # File exists but is invalid — overwrite it

    # Write the file
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    # RCA-310 FIX-3: Preserve completed/assigned statuses when re-saving ledger.
    # Root cause: When the orchestrator runs multiple waves, a later wave may
    # re-initialize the ledger (via agent.data) with all-pending statuses,
    # then save_manifest overwrites the disk file — silently reverting progress.
    # Fix: Before writing, merge any completed/assigned statuses from the
    # existing disk file into the new content.
    if filename == "requirements_ledger.json" and isinstance(content, dict):
        try:
            _preserve_ledger_progress(content, filepath)
        except Exception as merge_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] RCA-310 ledger merge FAILED: "
                f"{merge_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ Ledger progress merge FAILED — requirement statuses may be reverted: {merge_err}"
            )

    # F-4 (RCA-358): Reconcile parent-child entries before saving.
    # When the orchestrator creates a stub (e.g., 2.3) and the architect creates
    # sub-tasks (e.g., 2.3.0), both exist as 'pending'. Auto-complete the parent
    # to prevent double-delegation.
    if filename == "decomposition_index.json" and isinstance(content, list):
        # RCA-470 Fix 4: Detect and re-inject phases dropped by LLM rewrite.
        # Must run BEFORE _validate_mandatory_phases so we catch Architect-added
        # phases that aren't in _MANDATORY_PHASES.
        try:
            if os.path.isfile(filepath):
                with open(filepath, "r", encoding="utf-8") as existing_f:
                    existing_decomp = json.load(existing_f)
                # Handle both list and dict-with-phases formats
                if isinstance(existing_decomp, dict):
                    for key in ("tasks", "milestones", "phases"):
                        if key in existing_decomp and isinstance(existing_decomp[key], list):
                            existing_decomp = existing_decomp[key]
                            break
                if isinstance(existing_decomp, list):
                    dropped = _detect_dropped_phases(existing_decomp, content)
                    if dropped:
                        logger.warning(
                            f"[REQUIREMENTS TOOL] RCA-470 Fix 4: {len(dropped)} phases "
                            f"dropped by LLM rewrite — re-injecting"
                        )
                        content = _reinject_dropped_phases(content, dropped)
        except Exception as fix4_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] RCA-470 Fix 4 dropped-phase detection FAILED: "
                f"{fix4_err}"
            )
        # ITR-18 FIX: Ensure mandatory pre-code phases are present.
        # Root cause: LLM drops Phase 0.5 and 2.3 from decomposition.
        # L1 deterministic fix: auto-inject missing mandatory phases.
        try:
            content = _validate_mandatory_phases(content)
        except Exception as phase_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] ITR-18 mandatory phase validation FAILED: "
                f"{phase_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ Mandatory phase validation FAILED — required phases may be missing: {phase_err}"
            )
        # F-2 FIX: Reconcile auto-injected phase statuses against artifact existence.
        # Root cause: _validate_mandatory_phases() injects with status='pending' but
        # never checks if the work is already done. This reconciler upgrades pending
        # phases to 'completed' when their completion artifacts exist on disk.
        try:
            content = _reconcile_mandatory_phase_status(content, project_dir, agent_data=agent_data)
        except Exception as f2_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] F-2 phase status reconciliation FAILED: "
                f"{f2_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ Phase status reconciliation FAILED: {f2_err}"
            )
        # Wire 1: Seed integration requirements from dependency graph
        try:
            _seed_integration_from_dep_graph(
                project_dir, agent_data,
                content if isinstance(content, list) else [],
            )
        except Exception as w1_err:
            logger.debug(f"[INTEGRATION SEEDING] Save hook failed: {w1_err}")
        try:
            from python.helpers.validators.decomposition_dispatch import (
                reconcile_parent_child_entries,
            )
            content = reconcile_parent_child_entries(content)
        except Exception as reconcile_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] F-4 reconciliation failed (non-fatal): "
                f"{reconcile_err}"
            )

        # ISS-R3 FIX: Auto-reconcile phase statuses based on deliverable existence.
        # Root cause: The orchestrator LLM doesn't always update phase statuses
        # after delegations complete. Phases stay "pending" even when their
        # deliverables exist on disk. Fix: check for known deliverable files
        # and auto-mark phases as completed.
        try:
            _reconcile_decomp_statuses(content, project_dir, agent_data=agent_data)
        except Exception as recon_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] ISS-R3 status reconciliation failed (non-fatal): "
                f"{recon_err}"
            )

        # F-3 (ITR-51): Sweep for requirements stuck in 'assigned' after their
        # linked phases completed/failed (e.g., after Docker restart). Must run
        # AFTER _reconcile_decomp_statuses so phase statuses are current.
        try:
            stale_count = _sweep_stale_assignments(content, project_dir)
            if stale_count > 0:
                logger.info(
                    f"[REQUIREMENTS TOOL] F-3: Reset {stale_count} stale "
                    f"assignment(s) to 'pending'"
                )
        except Exception as sweep_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] F-3 stale assignment sweep failed "
                f"(non-fatal): {sweep_err}"
            )

        # RCA-ITR54: _guard_implementation_phase_inflation() DELETED.
        # This heuristic guard caused ITR-53 by accepting scaffold files as
        # 'real code' (>1KB check). Implementation phase verification is now
        # handled by ADR-089 stage_status validation (TDD tests as proof).
        # See: agix-devdocs/docs/rca/rca_itr54_tdd_as_proof_audit.md

        # SS-7 FIX (ITR-344): Auto-promote call DELETED.
        # _auto_promote_implementation_phases() used a global file count to
        # falsely promote ALL Phase 3.x+ phases. Implementation phase promotion
        # is now handled exclusively by orchestrator LLM + RCA-361 req_guid
        # cross-reference. See mainstreet-344-audit.md SS-7.

        # FIX-8 (ITR-32): Filter deferred decomposition phases.
        # Root cause: Architect prompt tells LLM to tag features with
        # timeline: immediate/near-term/future, but the requirements tool
        # never enforced those tags. Timeline was pure LLM honor-system.
        # Fix: Mark near-term/future/deferred phases as status='deferred'
        # so the gate ignores them and the orchestrator doesn't delegate them.
        try:
            content = _filter_deferred_decomposition_phases(content)
        except Exception as temporal_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] FIX-8 temporal filter FAILED: "
                f"{temporal_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ Temporal phase filter FAILED — deferred features may be delegated: {temporal_err}"
            )

    # RCA-ITR5 ISSUE-4: Sanitize ledger entries before writing to disk.
    # Strip API keys, §§secret() tokens, and §§REDACTED_PATTERN from text fields.
    # Layer 2 defense: even if LLM extraction includes raw keys, they're stripped.
    if filename == "requirements_ledger.json":
        try:
            from python.helpers.requirements_sanitizer import sanitize_ledger
            if isinstance(content, list):
                content = sanitize_ledger(content)
            elif isinstance(content, dict) and "requirements" in content:
                content["requirements"] = sanitize_ledger(content["requirements"])
        except Exception as san_err:
            logger.debug(
                f"[REQUIREMENTS TOOL] RCA-ITR5 ledger sanitization failed (non-fatal): "
                f"{san_err}"
            )

    # Use the canonical formatting (not sort_keys for human readability)
    content_str = json.dumps(content, indent=2, ensure_ascii=False)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content_str)

    logger.info(f"[REQUIREMENTS TOOL] Saved {filename} ({len(content_str)} bytes, hash={new_hash}) to {filepath}")

    # ISS-R4 FIX: Sync decomposition assignments back to requirement records.
    # Root cause (ITR-15 loop): check_assignment_coverage() reads req.status
    # and req.assigned_to from agent_data, but save_manifest only writes the
    # decomposition_index.json file — never updating the in-memory requirement
    # records. This causes check_coverage to report 25+ requirements as
    # "unassigned" even though they ARE mapped in decomp phases, creating an
    # infinite loop where the orchestrator keeps re-saving the decomp index.
    # Fix: After saving decomposition_index.json, extract all req_guids/req_ids
    # from phases and mark those requirements as status="assigned" in agent_data.
    if filename == "decomposition_index.json" and isinstance(content, list):
        try:
            _sync_decomp_assignments_to_ledger(agent_data, content)
        except Exception as sync_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] ISS-R4 assignment sync FAILED: "
                f"{sync_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ Assignment sync FAILED — check_coverage may report false unassigned: {sync_err}"
            )

    # ── Pipeline Gap Fix: Wire _decomposition_task_count & _decomposition_plan ──
    # Root cause: Agent.get_max_turns() reads _decomposition_task_count for dynamic
    # budget scaling (R-4 / RCA-362), and _45_intelligent_supervisor.py reads
    # _decomposition_plan for decomposition progress suppression (RCA-237 RC-7).
    # manifest_packages.py reads _decomposition_plan.integrations for SDK validation.
    # NONE of these keys were ever written, making all three features dead code.
    # Fix: Compute both summaries from the decomposition content after save.
    if filename == "decomposition_index.json" and isinstance(content, list) and agent_data is not None:
        try:
            _sync_decomposition_plan_to_agent_data(agent_data, content)
        except Exception as plan_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] Decomposition plan sync FAILED: "
                f"{plan_err}"
            )

    result_msg = f"✅ Saved {filename} to {filepath} ({len(content_str)} bytes)"

    # F-3 (ITR-18): Delegation nudge for auto-injected mandatory phases.
    # Root cause: Auto-injected phases are passive data — the LLM sees them
    # as informational, not actionable. This nudge tells the orchestrator
    # to DELEGATE the injected phases in its next batch.
    if filename == "decomposition_index.json" and isinstance(content, list):
        try:
            injected_phases = [
                p for p in content
                if p.get("note", "").startswith("ITR-18: Auto-injected")
                and p.get("status", "pending") not in (
                    "completed", "verified", "done", "skipped"
                )
            ]
            if injected_phases:
                nudge_parts = []
                for phase in injected_phases:
                    seq = phase.get("seq", "?")
                    title = phase.get("title", "Unknown")
                    agent = phase.get("agent", "unknown")
                    nudge_parts.append(
                        f"  - Phase {seq} ({title}) → delegate to {agent} agent"
                    )
                nudge = (
                    "\n\n🔴 **MANDATORY DELEGATION REQUIRED**\n"
                    "The following phases were auto-injected and MUST be delegated:\n"
                    + "\n".join(nudge_parts)
                    + "\n\nYou MUST include these in your next delegation batch."
                )
                result_msg += nudge
                logger.info(
                    f"[REQUIREMENTS TOOL] F-3: Delegation nudge appended for "
                    f"{len(injected_phases)} auto-injected phases"
                )
        except Exception as nudge_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] F-3 delegation nudge FAILED: "
                f"{nudge_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ Delegation nudge FAILED — auto-injected phases may not be delegated: {nudge_err}"
            )

    # RCA-345 FIX-3: GUID validation at save_manifest time.
    # Root cause: 8 of 14 phases in the regression run used fabricated GUIDs
    # that didn't exist in the ledger, causing silent reconciliation failure.
    # Fix: After saving decomposition_index.json, cross-reference all req_guids
    # against the requirements ledger. Advisory only — warns but doesn't block.
    if filename == "decomposition_index.json" and isinstance(content, list):
        try:
            ledger_path = _planning_path(project_dir, "requirements_ledger")
            if os.path.isfile(ledger_path):
                with open(ledger_path, "r", encoding="utf-8") as lf:
                    ledger_data = json.load(lf)
                # Extract all requirement IDs from the ledger
                ledger_reqs = []
                if isinstance(ledger_data, dict):
                    ledger_reqs = ledger_data.get("requirements", [])
                elif isinstance(ledger_data, list):
                    ledger_reqs = ledger_data
                ledger_req_ids = {
                    r.get("id", "") for r in ledger_reqs if isinstance(r, dict) and r.get("id")
                }

                # Auto-sync: if a GUID exists in agent.data runtime ledger but
                # not in the file-based ledger, sync it before declaring orphan.
                # Root cause (MainStreet Phase 3 2026-06-19): agent adds a late
                # requirement to agent.data, saves decomposition index, but
                # hasn't called save_manifest for the ledger yet. The GUID
                # check against the stale file produces a false orphan warning
                # that traps the agent in a supervisor redirect loop.
                if agent_context:
                    try:
                        _ad = getattr(agent_context, 'data', None) or {}
                        runtime_ledger = _ad.get('_requirements_ledger', {})
                        runtime_reqs = runtime_ledger.get('requirements', [])
                        runtime_ids = {
                            r.get('id', '') for r in runtime_reqs
                            if isinstance(r, dict) and r.get('id')
                        }
                        missing_from_file = runtime_ids - ledger_req_ids
                        if missing_from_file:
                            # Auto-sync: add missing runtime reqs to file
                            runtime_map = {r['id']: r for r in runtime_reqs if r.get('id')}
                            for mid in missing_from_file:
                                if mid in runtime_map:
                                    ledger_reqs.append(runtime_map[mid])
                                    ledger_req_ids.add(mid)
                            # Persist the updated ledger to file
                            if isinstance(ledger_data, dict):
                                ledger_data['requirements'] = ledger_reqs
                            with open(ledger_path, 'w', encoding='utf-8') as lf:
                                json.dump(ledger_data, lf, indent=2)
                            logger.info(
                                f"[REQUIREMENTS TOOL] Auto-synced {len(missing_from_file)} "
                                f"runtime requirements to file ledger: {missing_from_file}"
                            )
                    except Exception as sync_err:
                        logger.debug(
                            f"[REQUIREMENTS TOOL] Runtime ledger auto-sync "
                            f"skipped: {sync_err}"
                        )

                from python.helpers.phase_parser import validate_decomp_guids
                guid_result = validate_decomp_guids(content, ledger_req_ids)

                if not guid_result["valid"]:
                    orphan_list = ", ".join(guid_result["orphan_guids"][:20])
                    phase_list = ", ".join(guid_result["orphan_phases"][:20])
                    result_msg += (
                        f"\n\n⚠️ **RCA-345 GUID VALIDATION WARNING**: "
                        f"{guid_result['orphan_count']}/{guid_result['total_guids']} "
                        f"req_guids in decomposition phases do NOT exist in the "
                        f"requirements ledger.\n"
                        f"  Orphan GUIDs: {orphan_list}\n"
                        f"  Affected phases: {phase_list}\n"
                        f"  These phases will fail reconciliation. Re-map them to "
                        f"valid requirement IDs from the ledger."
                    )
                    logger.warning(
                        f"[REQUIREMENTS TOOL] RCA-345 FIX-3: {guid_result['orphan_count']} "
                        f"orphan GUIDs detected in decomposition_index.json "
                        f"(phases: {phase_list})"
                    )
                else:
                    logger.info(
                        f"[REQUIREMENTS TOOL] RCA-345 FIX-3: All "
                        f"{guid_result['total_guids']} req_guids validated against ledger"
                    )
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.debug(
                f"[REQUIREMENTS TOOL] RCA-345 FIX-3: Could not validate GUIDs "
                f"(non-fatal): {e}"
            )
        except Exception as guid_err:
            logger.warning(
                f"[REQUIREMENTS TOOL] RCA-345 FIX-3 GUID validation FAILED: "
                f"{guid_err}"
            )
            _reconciler_warnings.append(
                f"⚠️ GUID validation FAILED — orphan GUIDs may cause reconciliation failures: {guid_err}"
            )

    # ITR-22: Surface reconciler warnings so _12_tool_failure_tracker sees them.
    if _reconciler_warnings:
        result_msg += "\n\n" + "\n".join(_reconciler_warnings)
        _reconciler_warnings.clear()

    return result_msg

def _handle_update(agent_data: dict, args: dict = None, agent_context=None) -> str:
    """Add new requirements to the ledger dynamically.

    RCA-357: Now reports duplicate skips so the LLM understands that
    'update' means 'add new' and existing requirements are preserved.

    RCA-362 F-7: Warns when adding requirements after decomposition has
    started (assigned/completed requirements exist). Late additions risk
    coverage gaps because they won't have decomposition phases.

    Args:
        agent_data: The agent.data dict
        args: Dict with 'requirements' key — list of {text, category} dicts.
              Or 'text' + 'category' for a single requirement.
    """
    args = args or {}

    # F-7: Requirement freeze guard — warn (don't block) when adding
    # requirements after decomposition has started. Late additions
    # won't have decomposition phases and risk coverage gaps.
    freeze_warning = ""
    ledger = _ensure_ledger(agent_data)
    existing_reqs = ledger.get("requirements", [])
    if existing_reqs:
        assigned_count = sum(
            1 for r in existing_reqs
            if r.get("status") in ("assigned", "completed", "verified")
        )
        if assigned_count > 0:
            freeze_warning = (
                f"\n\n⚠️ **Late requirement addition**: {assigned_count} requirements "
                f"are already assigned/completed. New requirements added now will "
                f"NOT have decomposition phases. Run `check_coverage` after adding "
                f"to verify all requirements have assignments."
            )

    # Support both single requirement and batch
    requirements = args.get("requirements", [])
    if not requirements and args.get("text"):
        requirements = [{"text": args["text"], "category": args.get("category", "feature")}]

    if not requirements:
        return "⚠️ No requirements provided. Pass 'requirements': [{\"text\": \"...\", \"category\": \"...\"}]"

    added_ids = []
    skipped_ids = []
    ledger = _ensure_ledger(agent_data)
    existing_texts = {
        r.get("text", "").strip().lower()
        for r in ledger.get("requirements", [])
    }

    for req in requirements:
        text = req.get("text", "")
        category = req.get("category", "feature")
        if text:
            normalized = text.strip().lower()
            was_existing = normalized in existing_texts
            req_id = add_requirement(agent_data, text=text, category=category)
            if was_existing:
                skipped_ids.append(req_id)
            else:
                added_ids.append(req_id)
                existing_texts.add(normalized)

    parts = []
    if added_ids:
        parts.append(f"✅ Added {len(added_ids)} requirements: {', '.join(added_ids)}")
    if skipped_ids:
        parts.append(
            f"⏭️ Skipped {len(skipped_ids)} (already exist): {', '.join(skipped_ids)}"
        )

    if not parts:
        return "⚠️ No valid requirements to add (empty text)."

    if not added_ids and skipped_ids:
        return (
            f"✅ All {len(skipped_ids)} requirements already exist in ledger. "
            f"Existing IDs: {', '.join(skipped_ids)}. No changes made."
        )

    return " | ".join(parts) + freeze_warning

def _handle_mark_complete(agent_data: dict, args: dict = None, agent_context=None) -> str:
    """Mark one or more requirements as completed.

    F-2C: Now requires proof_files argument — a list of file paths that
    prove the requirement was implemented. Optional proof_grep keyword
    searches within proof files for a specific function/class name.

    RCA-362 L1 Fix: Now accepts agent_context to resolve project_dir and
    auto-persist ledger to disk after marking complete.

    Supports both:
      - requirement_id: "REQ-001" (single — backward compatible)
      - requirement_ids: ["REQ-001", "REQ-003", "REQ-005"] (batch — preferred)

    When both are provided, requirement_ids (array) takes precedence.

    Args:
        agent_data: The agent.data dict
        args: Dict with 'requirement_id' (str) or 'requirement_ids' (list),
              'proof_files' (list of file paths), 'proof_grep' (optional keyword)
        agent_context: The agent.context for project_dir resolution
    """
    args = args or {}

    # GAP-1 FIX: Persist _active_project_dir to agent_data for downstream consumers
    _ensure_active_project_dir(agent_data, agent_context)

    # RCA-362: Resolve project_dir for disk persistence
    project_dir = None
    try:
        project_name = projects.get_context_project_name(
            agent_context
        ) if agent_context else None
        if project_name:
            project_dir = projects.get_project_folder(project_name)
    except Exception:
        pass  # Non-fatal — mark_complete still works in memory

    # F-2C: Delegate to lightweight proof-gated handler
    from python.helpers.requirements_proof_gate import handle_mark_complete_with_proof
    return handle_mark_complete_with_proof(
        agent_data=agent_data,
        args=args,
        mark_fn=mark_requirement_complete,
        project_dir=project_dir,
    )

def _handle_set_iteration_budget(agent_data: dict, args: dict = None, agent_context=None) -> str:
    """Set the orchestrator's iteration budget based on scope.

    RCA-362 FIX-2 Layer 1: The orchestrator calls this AFTER decomposition,
    when it knows the full scope (number of delegations, phases, requirements).
    The budget is stored in agent.data['_llm_iteration_budget'] and read by
    Agent.get_max_turns().

    Args:
        agent_data: The agent.data dict
        args: Dict with:
            - 'estimated_delegations': How many delegate calls expected (required)
            - 'total_phases': Number of decomposition phases (optional)
            - 'total_requirements': Number of tracked requirements (optional)
        agent_context: The agent.context (unused for this action)

    Returns:
        Confirmation message with the calculated budget.
    """
    from python.helpers.budget_reserve import calculate_llm_budget

    args = args or {}
    estimated_delegations = int(args.get("estimated_delegations", 0))
    total_phases = int(args.get("total_phases", 0))
    total_requirements = int(args.get("total_requirements", 0))

    budget = calculate_llm_budget(
        estimated_delegations=estimated_delegations,
        total_phases=total_phases,
        total_requirements=total_requirements,
    )

    # Store in agent.data for Agent.get_max_turns() to read
    agent_data["_llm_iteration_budget"] = budget

    logger.info(
        f"[REQUIREMENTS TOOL] Smart Budget: set _llm_iteration_budget={budget} "
        f"(delegations={estimated_delegations}, phases={total_phases}, "
        f"reqs={total_requirements})"
    )

    return (
        f"✅ Iteration budget set to **{budget}** turns.\n\n"
        f"**Budget breakdown**:\n"
        f"- {estimated_delegations} delegations × 7 turns/delegation = "
        f"{estimated_delegations * 7} turns\n"
        f"- Verification overhead: 20 turns\n"
        f"- **Total: {budget} turns**\n\n"
        f"This budget is capped by the hard service limit (profile max turns). "
        f"Agent.get_max_turns() will use min({budget}, profile_limit)."
    )

def _handle_complete_phase(agent_data: dict, args: dict = None, agent_context=None) -> str:
    """Mark a decomposition phase as completed.

    RCA-345 FIX-2: Provides a structured tool call for phase completion,
    replacing the LLM honor system. The orchestrator can explicitly call:
        requirements(action="complete_phase", phase_seq="3.1.0", evidence="...")
    to deterministically mark a phase as completed.

    Args:
        agent_data: The agent.data dict
        args: Dict with:
            - 'phase_seq': The phase sequence to mark completed (e.g., "3.1.0")
            - 'evidence': Optional evidence string
            - 'force_accepted': If True, mark as partially_completed
        agent_context: The agent.context for project resolution
    """
    args = args or {}
    phase_seq = str(args.get("phase_seq", "")).strip()
    evidence = str(args.get("evidence", "manual complete_phase call"))
    force_accepted = bool(args.get("force_accepted", False))

    if not phase_seq:
        return "⚠️ Missing 'phase_seq'. Usage: requirements(action='complete_phase', phase_seq='3.1.0')"

    # GAP-1 FIX: Persist _active_project_dir to agent_data for downstream consumers
    _ensure_active_project_dir(agent_data, agent_context)

    # Resolve project directory
    try:
        project_name = projects.get_context_project_name(
            agent_context
        ) if agent_context else None
        if project_name:
            project_dir = projects.get_project_folder(project_name)
        else:
            return "⚠️ No project context — cannot resolve decomposition_index.json"
    except Exception as e:
        return f"⚠️ Cannot complete phase: {e}"

    decomp_path = get_decomp_index_path(project_dir)
    if not os.path.isfile(decomp_path):
        return f"⚠️ No decomposition_index.json at {decomp_path}"

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return f"⚠️ Cannot read decomposition_index.json: {e}"

    # Handle both list and dict formats
    phases_list = decomp_data
    if isinstance(decomp_data, dict):
        phases_list = (
            decomp_data.get("tasks")
            or decomp_data.get("milestones")
            or decomp_data.get("phases")
            or []
        )

    if not isinstance(phases_list, list):
        return "⚠️ decomposition_index.json has unexpected format"

    from python.helpers.phase_parser import mark_decomp_phase_completed
    result = mark_decomp_phase_completed(
        phases_list, phase_seq, evidence=evidence, force_accepted=force_accepted,
        project_dir=project_dir,
    )

    if not result["found"]:
        available_seqs = [str(p.get("seq", "?")) for p in phases_list]
        return (
            f"⚠️ Phase '{phase_seq}' not found in decomposition_index.json. "
            f"Available phases: {', '.join(available_seqs)}"
        )

    # Write back
    if isinstance(decomp_data, dict):
        for key in ("tasks", "milestones", "phases"):
            if key in decomp_data:
                decomp_data[key] = phases_list
                break
    else:
        decomp_data = phases_list

    with open(decomp_path, "w", encoding="utf-8") as f:
        json.dump(decomp_data, f, indent=2, ensure_ascii=False)

    status = result["new_status"]
    logger.info(
        f"[REQUIREMENTS TOOL] complete_phase: Phase {phase_seq} → {status}"
    )

    # RCA-461 P0 FIX: TDD generation fallback on manual phase completion.
    # Second safety net (first is in call_subordinate_execute.py).
    # When the orchestrator marks Phase 2.x as complete, generate TDD stubs
    # if they don't exist yet — prevents code agents from running without
    # literal-assertion tests.
    try:
        phase_major = float(phase_seq.split(".")[0])
        tdd_dir = os.path.join(project_dir, "docs", "tdd")
        if phase_major == 2 and not os.path.isdir(tdd_dir):
            from python.helpers.skeleton_generator import (
                generate_tdd_tests,
                generate_test_skeleton,
            )
            # Regenerate skeleton with manifest values
            try:
                generate_test_skeleton(project_dir)
                logger.info(
                    "[REQUIREMENTS TOOL] RCA-461: Regenerated skeleton "
                    "(complete_phase fallback)"
                )
            except Exception:
                pass  # Non-fatal — skeleton may already be current
            tdd_results = generate_tdd_tests(project_dir)
            if tdd_results:
                logger.info(
                    f"[REQUIREMENTS TOOL] RCA-461: Auto-generated "
                    f"{len(tdd_results)} TDD test modules "
                    f"(complete_phase fallback for Phase {phase_seq})"
                )
    except (ValueError, TypeError, ImportError) as tdd_err:
        logger.debug(f"[REQUIREMENTS TOOL] TDD fallback skipped: {tdd_err}")

    return f"✅ Phase {phase_seq} marked as {status}. Evidence: {evidence[:100]}"

def record_phase_dispatched(agent_data: dict, phase_seq: str) -> None:
    """Record that a delegation was dispatched for a phase.

    F-9 FIX: Called from call_subordinate.py when a delegation is dispatched.
    Tracks which phases have had actual delegations so the reconciler can
    distinguish between phases that were truly executed vs. phases whose
    artifacts are side-effects of other phases' work.

    Wiring point: In call_subordinate.py, right after _current_phase is set
    (around line 804), call:
        from python.tools.requirements import record_phase_dispatched
        record_phase_dispatched(self.agent.data, detected_phase)

    Args:
        agent_data: The agent's data dict (self.agent.data)
        phase_seq: The phase sequence number (e.g., "2.5")
    """
    dispatched = agent_data.get('_phases_dispatched', [])
    if not isinstance(dispatched, list):
        dispatched = list(dispatched)  # Handle unexpected types
    phase_str = str(phase_seq)
    if phase_str not in dispatched:
        dispatched.append(phase_str)
    agent_data['_phases_dispatched'] = dispatched

def _sweep_stale_assignments(phases: list, project_dir: str) -> int:
    """Sweep for requirements stuck in 'assigned' after phases completed/failed.

    F-3 (ITR-51): After Docker restart, requirements assigned to interrupted
    phases stay 'assigned' forever because the reconciler only syncs phase
    status → requirement status when the phase transitions. If the system
    restarts mid-phase, the transition event is lost.

    This function scans for requirements with status='assigned' whose linked
    phases have status 'completed' or 'failed', and resets them to 'pending'
    so they get re-assigned on the next delegation cycle.

    Args:
        phases: List of decomposition phase dicts (each with 'seq', 'status',
                optionally 'req_guids').
        project_dir: Path to the project directory containing
                     requirements_ledger.json.

    Returns:
        Number of requirements reset from 'assigned' to 'pending'.
    """
    ledger_path = _planning_path(project_dir, "requirements_ledger")
    if not os.path.isfile(ledger_path):
        return 0

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.warning("[RECONCILE] F-3: Failed to read requirements_ledger.json")
        return 0

    requirements = ledger.get("requirements", [])
    if not requirements:
        return 0

    # Build phase status lookup: seq → status
    TERMINAL_STATUSES = {"completed", "failed"}
    phase_status = {str(p.get("seq", "")): p.get("status", "") for p in phases}

    # Build reverse index: req_id → [phase_seqs] (from phases' req_guids)
    req_to_phases_from_guids: dict[str, list[str]] = {}
    for phase in phases:
        for req_guid in phase.get("req_guids", []):
            req_to_phases_from_guids.setdefault(req_guid, []).append(
                str(phase.get("seq", ""))
            )

    reset_count = 0
    changed = False

    for req in requirements:
        # Only consider requirements stuck in 'assigned'
        if req.get("status") != "assigned":
            continue

        # Determine which phases this requirement is linked to
        assigned_to = req.get("assigned_to", [])
        if not assigned_to:
            # Fallback: check if any phase references this req via req_guids
            req_id = req.get("id", "")
            assigned_to = req_to_phases_from_guids.get(req_id, [])

        if not assigned_to:
            # Can't determine linked phases — leave alone (conservative)
            continue

        # Check if ALL linked phases are in a terminal status
        all_terminal = all(
            phase_status.get(str(seq), "") in TERMINAL_STATUSES
            for seq in assigned_to
        )

        if all_terminal:
            req_id = req.get("id", "unknown")
            old_phases = ", ".join(str(s) for s in assigned_to)
            logger.info(
                f"[RECONCILE] F-3: Stale assignment detected — {req_id} "
                f"assigned to phase(s) [{old_phases}] which are all "
                f"completed/failed. Resetting to 'pending'."
            )
            req["status"] = "pending"
            reset_count += 1
            changed = True

    if changed:
        try:
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"[RECONCILE] F-3: Failed to write updated ledger: {e}")

    if reset_count > 0:
        logger.info(
            f"[RECONCILE] F-3: Stale assignment sweep complete — "
            f"reset {reset_count} requirement(s) from 'assigned' to 'pending'"
        )

    return reset_count
