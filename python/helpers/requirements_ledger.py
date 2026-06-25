"""
Requirements Traceability Ledger

Tracks prompt requirements → delegations → completion status.
Closes the gap where the orchestrator delegates work but nobody validates
whether all user prompt requirements are actually covered by delegations.

All state lives in agent.data["_requirements_ledger"] — survives context
condensation with no extra persistence layer needed.

Data structure:
    _requirements_ledger = {
        "requirements": [
            {
                "id": "REQ-001",
                "text": "Cal.com booking link: https://cal.com/...",
                "category": "url",
                "status": "pending",        # pending|assigned|completed|verified|partial|failed
                "assigned_to": [],          # delegation IDs covering this requirement
            },
            ...
        ],
        "delegations": [
            {
                "id": "delegation-1",
                "profile": "frontend",
                "message_summary": "Build the landing page...",
                "status": "in_progress",    # in_progress|completed|failed
                "requirement_ids": ["REQ-001", "REQ-004"],
                "response_summary": "",
            },
            ...
        ],
    }

P4 MODULARIZATION NOTE
======================
This file was 2,321 lines. During P4 modularization it was split into 7
focused modules. This file now contains ONLY re-exports for backward
compatibility (406+ test imports reference python.helpers.requirements_ledger).

Modules:
    requirements_stage.py               — Stage/lifecycle (ADR-086, ADR-089)
    requirements_persistence.py         — Persistence, init, migration, dedup
    requirements_crud.py                — CRUD, anti-pattern filter, category inference
    requirements_delegation_tracker.py  — Delegation recording, completion, escalation
    requirements_proof.py               — Proof evidence, verification, regression
    requirements_coverage_queries.py    — Coverage stats, publishing checks
    requirements_seeding.py             — Seeding, gate failures, pre-delivery audit
"""

import logging

from python.helpers.planning_paths import get_path as _planning_path  # noqa: F401

logger = logging.getLogger("agix.requirements_ledger")

# ─── P4 Modularization: re-exports from requirements_stage.py ────────
# All functions below were extracted to requirements_stage.py but are
# re-exported here for backward compatibility (406+ test imports).
from python.helpers.requirements_stage import (  # noqa: F401
    compute_overall_status,
    get_stage_status,
    set_stage_status,
    is_stage_complete,
    CATEGORY_REQUIRED_STAGES,
    _get_requirements_for_phase,
    set_phase_completed,
    try_phase_completed,
    ensure_stage_status,
    _CATEGORY_KEYWORDS,
    infer_phase_category,
    _PROFILE_PHASE_COMPATIBILITY,
)

# ─── P4 Modularization: re-exports from requirements_persistence.py ──
# Persistence, initialization, migration, and dedup functions extracted to
# requirements_persistence.py — re-exported for backward compatibility.
from python.helpers.requirements_persistence import (  # noqa: F401
    _dedup_hash,
    _ensure_ledger,
    _rebuild_dedup_hashes,
    _sanitize_all_strings,
    persist_ledger_to_project,
    load_ledger_from_project,
    rehydrate_requirements_ledger,
    migrate_legacy_ledger,
    get_delegation_ledger_for_gate,
)

# ─── P4 Modularization: re-exports from requirements_crud.py ─────────
# CRUD, anti-pattern filter, and category inference extracted to
# requirements_crud.py — re-exported for backward compatibility.
from python.helpers.requirements_crud import (  # noqa: F401
    _ANTIPATTERN_PREFIX_RE,
    _DOUBLE_NEGATION_RE,
    is_antipattern_requirement,
    _generate_req_id,
    init_requirements,
    verify_seeding,
    add_requirement,
    _infer_category,
)

# ─── P4 Modularization: re-exports from requirements_delegation_tracker.py ─
# Delegation tracking, completion, escalation, failure detection, auto-promotion,
# and phase reconciliation extracted — re-exported for backward compatibility.
from python.helpers.requirements_delegation_tracker import (  # noqa: F401
    _next_delegation_id,
    assign_requirement,
    record_delegation,
    _SENTINEL_FAILURE_TAGS,
    _LEGACY_FAILURE_PHRASES,
    _FAILURE_SIGNALS,
    _has_failure_signals,
    _SOURCE_DIRS,
    _SOURCE_EXTENSIONS,
    _MIN_SOURCE_LINES,
    _find_substantial_source_files,
    _auto_promote_delegation_returned,
    _auto_link_from_decomp,
    mark_delegation_complete,
    reconcile_phase_status,
    mark_delegation_escalated,
    mark_partial,              # Gate-level escape: tried 3× but not fully met
    mark_failed,               # Structurally impossible requirement
    get_partial_summary,       # Structured summary for final response
)

# ─── P4 Modularization: re-exports from requirements_proof.py ────────
# Proof evidence checks, requirement completion marking, gate-based
# verification, E2E verification failure, and regression status extracted
# to requirements_proof.py — re-exported for backward compatibility.
from python.helpers.requirements_proof import (  # noqa: F401
    _STUB_INDICATORS,
    _COMMENTED_API_PATTERNS,
    _MIN_SUBSTANTIAL_LINES,
    _run_proof_evidence_checks,
    mark_requirement_complete,
    promote_test_results_to_ledger,
    mark_verified_from_gate_results,
    mark_requirements_verification_failed,
    get_verification_failed_requirements,
    mark_requirements_regressed,
    get_regressed_requirements,
)

# ─── P4 Modularization: re-exports from requirements_coverage_queries.py ─
# Coverage statistics, publishing checks, BDD coverage, assignment coverage,
# and remediation task queries extracted — re-exported for backward compat.
from python.helpers.requirements_coverage_queries import (  # noqa: F401
    can_publish,
    get_incomplete_requirements,
    get_bdd_coverage,
    get_coverage,
    check_assignment_coverage,
    get_unassigned_requirements,
    get_pending_remediation_tasks,
)

# GAP-5 FIX: build_tdd_mandate is now canonical in delegation_message.py.
# Re-export for backward compatibility — callers that import from this module
# will still work, but the single source of truth is delegation_message.
from python.helpers.delegation_message import build_tdd_mandate  # noqa: F401

# ─── P4 Modularization: re-exports from requirements_seeding.py ──────
# Seeding, line item merging, prompt supplement, PDV features, requirement
# ID validation, gate failure recording/resolution, and pre-delivery audit
# extracted to requirements_seeding.py — re-exported for backward compat.
from python.helpers.requirements_seeding import (  # noqa: F401
    seed_from_goal_state,
    merge_line_items_into_ledger,
    supplement_from_prompt,
    seed_features_into_ledger,
    validate_requirement_ids,
    record_gate_failure,
    get_gate_failures,
    resolve_gate_failure,
    clear_delegation_failures,
    get_active_gate_failures,
    get_remediation_tasks,
    pre_delivery_coverage_audit,
)
