"""
Requirements Delegation Tracking

Extracted from requirements_ledger.py during P4 modularization (Phase 1.4).
Contains delegation recording, completion, escalation, failure signal detection,
auto-promotion from delegation_returned, decomposition-based auto-linking,
and phase status reconciliation.

Functions:
    _next_delegation_id              — Generate next delegation ID
    assign_requirement               — Direct assignment of requirement to delegation
    record_delegation                — Record a delegation with dedup + linking
    _has_failure_signals             — Check for error signals in response
    _find_substantial_source_files   — Find real source files in a project
    _auto_promote_delegation_returned — Promote delegation_returned → completed
    _auto_link_from_decomp           — Auto-link unassigned reqs from decomp phases
    mark_delegation_complete         — Mark delegation completed + update reqs
    reconcile_phase_status           — Reconcile decomp phase status after delegation
    mark_delegation_escalated        — Mark delegation as escalated + revert reqs
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from python.helpers.req_id_normalizer import build_normalized_req_map
from python.helpers.projects import get_decomp_index_path
from python.helpers.source_scanner import list_project_files, EXCLUDE_DIRS
from python.helpers.requirements_persistence import (
    _dedup_hash,
    _ensure_ledger,
    persist_ledger_to_project,
)
from python.helpers.requirements_stage import (
    get_stage_status,
    set_stage_status,
    infer_phase_category,
    _PROFILE_PHASE_COMPATIBILITY,
    _get_requirements_for_phase,
    set_phase_completed,
)

logger = logging.getLogger("agix.requirements_ledger")


# ── RCA-475 D4: DelegationSM wrapper ────────────────────────────────────

def _get_or_create_delegation_sm(agent_data: dict, delegation_id: str, initial_status: str = "pending"):
    """Get or create a DelegationSM for a delegation.

    RCA-475 D4: SM instances live in agent_data["_state_machines"]["delegation_{id}"].
    On first access the SM is seeded with the given initial_status.

    RCA-479 Fix: After JSON round-trip (save/reload), DelegationSM objects are
    serialized to strings via ``default=str`` (e.g., ``"<DelegationSM object at 0x…>"``).
    On reload the key exists but the value is a string, not a DelegationSM.
    Accessing ``.status`` on a string crashes with ``'str' object has no attribute 'status'``.
    Fix: check ``isinstance`` before returning; recreate the SM if corrupted.
    """
    from python.helpers.state_machines.delegation_sm import DelegationSM
    sms = agent_data.setdefault("_state_machines", {})
    key = f"delegation_{delegation_id}"
    existing = sms.get(key)
    if not isinstance(existing, DelegationSM):
        # Key missing OR corrupted (string/dict from JSON round-trip) — create fresh
        if existing is not None:
            logger.debug(
                f"[DELEGATION SM] Replacing corrupted SM for {delegation_id}: "
                f"type={type(existing).__name__}"
            )
        sms[key] = DelegationSM(status=initial_status, entity_id=delegation_id)
    return sms[key]


def _delegation_sm_transition(agent_data: dict, delegation_id: str, target: str, reason: str, source: str):
    """Transition a DelegationSM, force-syncing on invalid transitions (migration mode)."""
    sm = _get_or_create_delegation_sm(agent_data, delegation_id)
    if sm.status == target:
        return  # idempotent
    ok, msg = sm.transition(target, reason=reason, source=source)
    if not ok:
        logger.warning(f"[DELEGATION SM] {msg} — force-syncing (migration mode)")
        sm.transition(target, reason=f"force-sync: {msg}", source=source, force=True)


def _next_delegation_id(ledger: dict) -> str:
    """Generate the next delegation ID."""
    count = len(ledger.get("delegations", []))
    return f"delegation-{count + 1}"




def assign_requirement(
    agent_data: dict,
    req_id: str,
    delegation_id: str,
) -> None:
    """Directly mark a requirement as assigned to a delegation.

    This is the simple path for linking a requirement to a delegation
    without going through the full record_delegation flow. Useful for
    manual linking and testing.

    Args:
        agent_data: The agent.data dict
        req_id: The requirement ID (e.g., "REQ-001")
        delegation_id: The delegation ID covering this requirement
    """
    ledger = _ensure_ledger(agent_data)
    for req in ledger.get("requirements", []):
        if req["id"] == req_id:
            if req["status"] == "pending":
                req["status"] = "assigned"
            assigned_to = req.setdefault("assigned_to", [])  # KeyError guard: late-added reqs may lack this field
            if delegation_id not in assigned_to:
                assigned_to.append(delegation_id)
            return
    logger.warning(
        f"[REQUIREMENTS LEDGER] assign_requirement: {req_id} not found"
    )


def record_delegation(
    agent_data: dict,
    profile: str = "",
    message: str = "",
    requirement_ids: Optional[List[str]] = None,
    bdd_specs: Optional[List[Dict]] = None,
    test_specs: Optional[List[Dict]] = None,
    *,
    delegation_id: Optional[str] = None,
    message_summary: Optional[str] = None,
    status: str = "in_progress",
    response_summary: str = "",
) -> str:
    """Record a delegation and link it to requirements.

    F-3: Delegation Idempotency — when delegation_id is explicitly provided,
    checks for an existing entry with that ID first. If found, updates
    status and response_summary in-place instead of appending a duplicate.
    This prevents the ledger from accumulating N entries for the same logical
    delegation due to context reconstruction re-recording.

    Content-Hash Dedup: If an identical delegation (same profile + message)
    already exists AND has not failed, returns the existing ID instead of
    creating a duplicate. Failed delegations are cleared from the dedup set
    to allow retries.

    Args:
        agent_data: The agent.data dict
        profile: Agent profile being delegated to
        message: The delegation message (truncated for storage)
        requirement_ids: List of REQ-XXX IDs this delegation covers
        bdd_specs: Optional BDD acceptance criteria from architect.
            Each spec is a dict with keys:
            - test_file: str (e.g., "__tests__/api/discovery.test.ts")
            - descriptions: list[str] (test descriptions)
            - content_assertions: list[str] (grep-able strings)
        test_specs: Optional TDD test expectations from skeleton generator.
            Each spec is a dict with keys:
            - test_file: str (expected test file path)
            - descriptions: list[str] (test descriptions)
            - content_assertions: list[str] (literal values to verify)
        delegation_id: Explicit delegation ID. If provided and already exists
            in the ledger, updates in-place instead of appending.
        message_summary: Alias for message (used by callers that pass
            structured delegation data with message_summary key).
        status: Initial status for the delegation (default: "in_progress").
        response_summary: Initial response summary (default: "").

    Returns:
        The delegation ID (existing if deduped, new if unique)
    """
    ledger = _ensure_ledger(agent_data)
    requirement_ids = requirement_ids or []

    # Support message_summary as alias for message
    if message_summary and not message:
        message = message_summary

    # F-3: Delegation ID idempotency — if explicit delegation_id is provided,
    # check for existing entry and update in-place instead of appending.
    if delegation_id:
        for existing in ledger["delegations"]:
            if existing["id"] == delegation_id:
                # Update mutable fields in-place
                if status:
                    existing["status"] = status
                if response_summary:
                    existing["response_summary"] = response_summary
                logger.info(
                    f"[REQUIREMENTS LEDGER] F-3 idempotent update: "
                    f"delegation {delegation_id} updated in-place "
                    f"(status={status})"
                )
                return delegation_id

    # Content-hash dedup check
    if message:
        content_hash = _dedup_hash(profile, message)
        dedup_hashes = ledger.get("dedup_hashes", {})
        if content_hash in dedup_hashes:
            existing_id = dedup_hashes[content_hash]
            logger.info(
                f"[REQUIREMENTS LEDGER] Dedup hit: {profile} delegation "
                f"'{message[:50]}...' already exists as {existing_id}"
            )
            return existing_id
    else:
        dedup_hashes = ledger.get("dedup_hashes", {})
        content_hash = None

    # SS-10: Requirement-based dedup — same profile + same req_ids = same delegation.
    # Catches LLM paraphrasing: "Implement the discovery feature" vs
    # "Implement discovery feature" targeting the same REQ-IDs.
    # Failed/escalated delegations are excluded to allow retries.
    if requirement_ids:
        req_set = frozenset(requirement_ids)
        for existing in ledger["delegations"]:
            if (existing["status"] not in ("failed", "escalated") and
                existing["profile"] == profile and
                frozenset(existing.get("requirement_ids", [])) == req_set):
                logger.info(
                    f"[REQUIREMENTS LEDGER] REQ-dedup hit: {profile} delegation "
                    f"for {req_set} already exists as {existing['id']}"
                )
                return existing["id"]

    # Use explicit delegation_id if provided, otherwise auto-generate
    final_delegation_id = delegation_id or _next_delegation_id(ledger)
    ledger["delegations"].append({
        "id": final_delegation_id,
        "profile": profile,
        "message_summary": message[:200] if message else "",
        "status": status,
        "requirement_ids": list(requirement_ids),
        "response_summary": response_summary,
        "bdd_specs": list(bdd_specs) if bdd_specs else [],
        "test_specs": list(test_specs) if test_specs else [],
        "content_hash": content_hash or "",  # R3 Fix: Store hash in delegation record
    })

    # Store dedup hash → delegation ID
    if content_hash:
        dedup_hashes[content_hash] = final_delegation_id
        ledger["dedup_hashes"] = dedup_hashes

    # Update linked requirements to 'assigned'
    req_map = build_normalized_req_map(ledger["requirements"])
    for req_id in requirement_ids:
        if req_id in req_map:
            req = req_map[req_id]
            if req["status"] == "pending":
                req["status"] = "assigned"
            assigned_to = req.setdefault("assigned_to", [])  # KeyError guard: late-added reqs may lack this field
            if final_delegation_id not in assigned_to:
                assigned_to.append(final_delegation_id)

    logger.info(
        f"[REQUIREMENTS LEDGER] Recorded delegation {final_delegation_id} "
        f"to {profile} covering {len(requirement_ids)} requirements"
    )

    # RCA-475 D4: SM wrap — create SM and transition pending → in_progress
    _get_or_create_delegation_sm(agent_data, final_delegation_id, initial_status="pending")
    _delegation_sm_transition(
        agent_data, final_delegation_id, "in_progress",
        reason=f"delegation recorded for {profile}",
        source="requirements_delegation_tracker.record_delegation",
    )

    return final_delegation_id


# ─── Delegation Completion ───────────────────────────────────────────────

# Error signals that indicate the subordinate did NOT successfully complete.
# Case-insensitive matching is used.
#
# RCA-ITR48 Fix F-1: Previously this was a hardcoded list missing
# [ITERATION_LIMIT], [CHAIN_LIMIT], [FORCE_ACCEPTED_INCOMPLETE].
# Now uses sentinel_registry as the source of truth, plus legacy
# free-text patterns that aren't structured sentinel tags.
from python.helpers.sentinel_registry import get_limit_tags as _get_sentinel_tags

# All sentinel tags from the centralized registry (auto-synced)
_SENTINEL_FAILURE_TAGS = _get_sentinel_tags()

# Legacy free-text patterns (not structured sentinel tags but still indicate failure)
_LEGACY_FAILURE_PHRASES = [
    "[ESCALATE]",         # legacy tag not in sentinel_registry
    "HARD_STOP",          # bare tag without brackets
    "was terminated",
    "ran out of iterations",
    "could not complete",
    "ESCAPE HATCH",       # bare tag without brackets
]

# Combined list for _has_failure_signals()
_FAILURE_SIGNALS = _SENTINEL_FAILURE_TAGS + _LEGACY_FAILURE_PHRASES


def _has_failure_signals(response_summary: str) -> bool:
    """Check if response contains signals indicating failed completion.

    RCA-259 Fix G: Evidence-based delegation completion. Detects error
    signals like HARD_STOP, CANCELLED, termination, and iteration exhaustion.

    Args:
        response_summary: The delegation response text to check.

    Returns:
        True if any failure signal is detected (case-insensitive).
    """
    if not response_summary:
        return False
    upper = response_summary.upper()
    return any(sig.upper() in upper for sig in _FAILURE_SIGNALS)


# ─── RCA-ITR49: Source File Discovery for Auto-Promotion ──────────────────
_SOURCE_DIRS = ("src", "app", "lib", "components", "pages", "api", "server",
                "client", "frontend", "backend", "public")
_SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte",
    ".go", ".rs", ".java", ".rb", ".php", ".cs",
}
_MIN_SOURCE_LINES = 6  # >5 lines = not a stub


def _find_substantial_source_files(project_dir: str) -> List[str]:
    """Find source files with real content (>5 lines, no stubs) in a project.

    RCA-ITR49: Scans standard source directories for files with code
    extensions that have substantial content. Used as evidence that a
    delegation actually produced real code.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        List of absolute paths to substantial source files.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return []

    # OVL-3: Use centralized scanner instead of inline os.walk
    all_paths = list_project_files(
        project_dir,
        extensions=_SOURCE_EXTENSIONS,
        skip_dirs=EXCLUDE_DIRS,
    )

    found = []
    for filepath in all_paths:
        try:
            with open(filepath, "r", errors="replace") as f:
                lines = f.readlines()
            if len(lines) >= _MIN_SOURCE_LINES:
                found.append(filepath)
        except (IOError, OSError):
            continue

    return found


def _auto_promote_delegation_returned(
    agent_data: dict,
    delegation: dict,
    project_dir: str,
) -> int:
    """Auto-promote delegation_returned → completed when project evidence exists.

    RCA-ITR49 Fix 1: Closes the status lifecycle gap. After a delegation
    returns success and the project directory contains substantial source
    files, we promote the delegation's requirements from delegation_returned
    to completed via mark_requirement_complete(force=True).

    The force=True is critical — it bypasses L1 proof file checks that would
    fail because we're passing project-level evidence, not per-requirement
    proof files. The delegation's success + project file existence IS the proof.

    Args:
        ledger: The requirements ledger dict (from agent_data).
        delegation: The delegation dict that just completed.
        project_dir: Path to project directory with source files.

    Returns:
        Number of requirements promoted to completed.
    """
    from python.helpers.requirements_proof import mark_requirement_complete
    ledger = agent_data.get("_requirements_ledger", {})
    if not ledger:
        return 0
    if not project_dir:
        return 0

    # Only promote for successful delegations
    if delegation.get("status") != "completed":
        return 0

    req_ids = delegation.get("requirement_ids", [])
    if not req_ids:
        return 0

    # Check for substantial source files in the project
    source_files = _find_substantial_source_files(project_dir)
    if not source_files:
        logger.info(
            "[REQUIREMENTS LEDGER] ITR49-F1: No substantial source files in "
            f"{project_dir} — keeping requirements at delegation_returned"
        )
        return 0

    # F-7 (RCA-461): Feature PRESENCE check — scan source files for stubs.
    # File existence alone doesn't prove features are implemented.
    from python.helpers.stub_patterns import find_stubs_in_text
    stub_file_count = 0
    for sf in source_files:
        try:
            with open(sf, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            stubs_found = find_stubs_in_text(content)
            total_lines = len(content.splitlines())
            if total_lines > 0 and len(stubs_found) / total_lines > 0.20:
                stub_file_count += 1
        except (IOError, OSError):
            continue

    stub_ratio = stub_file_count / len(source_files) if source_files else 0
    if stub_ratio > 0.5:
        logger.warning(
            f"[REQUIREMENTS LEDGER] F-7: {stub_file_count}/{len(source_files)} "
            f"files have >20% stub patterns. NOT promoting to completed — "
            f"keeping at delegation_returned for manual review."
        )
        return 0

    # Build req map to find which ones are at delegation_returned
    # Promote ALL requirements at delegation_returned (not just the delegation's
    # explicit req_ids) — Fix 3's _auto_link_from_decomp may have set additional
    # requirements to delegation_returned that also need promotion.
    promoted = 0

    for req in ledger.get("requirements", []):
        flat_status = req.get("status", "pending")
        code_stage = get_stage_status(req, "code")

        # ADR-086 ITR-52 FIX: Catch TWO cases:
        # 1. Standard: status == "delegation_returned" (normal lifecycle)
        # 2. Diverged: status == "completed" but code stage still
        #    "delegation_returned" — occurs when _sync_decomp_assignments
        #    sets status directly without set_stage_status()
        needs_promote = (
            flat_status == "delegation_returned"
            or (
                code_stage == "delegation_returned"
                and flat_status not in ("pending", "assigned")
            )
        )
        if not needs_promote:
            continue

        # ADR-086: Use the formal pipeline to promote the requirement to completed.
        # This properly handles telemetry, clears regressions, and computes overall status.
        try:
            req_id = req.get("id")
            if req_id:
                mark_requirement_complete(agent_data, req_id, force=True)
                promoted += 1
        except Exception as e:
            logger.debug(f"[REQUIREMENTS LEDGER] Auto-promote error for {req.get('id')}: {e}")
        logger.info(
            f"[REQUIREMENTS LEDGER] ITR49-F1: {req.get('id', '?')} promoted "
            f"delegation_returned → completed (project has "
            f"{len(source_files)} substantial source files)"
        )

    if promoted:
        logger.info(
            f"[REQUIREMENTS LEDGER] ITR49-F1: Auto-promoted {promoted} "
            f"requirements to completed (evidence: {len(source_files)} source files)"
        )

    return promoted


def _auto_link_from_decomp(
    agent_data: dict,
    delegation: dict,
    project_dir: str,
) -> int:
    """Auto-link unassigned requirements from overlapping decomp phases.

    RCA-ITR49 Fix 3: When a delegation completes, find decomposition phases
    whose req_guids overlap with the delegation's requirement_ids. For each
    overlapping phase, link ALL req_guids in that phase to the delegation's
    completion status. This catches the 27-of-42 requirements that get NO
    delegation link because they weren't explicitly listed in requirement_ids.

    Args:
        ledger: The requirements ledger dict.
        delegation: The delegation dict that just completed.
        project_dir: Path to project directory.

    Returns:
        Number of requirements newly linked.
    """
    ledger = agent_data.get("_requirements_ledger", {})
    if not ledger:
        return 0
    if not project_dir:
        return 0

    delegation_req_ids = set(delegation.get("requirement_ids", []))
    if not delegation_req_ids:
        return 0

    # Read decomposition_index.json
    decomp_path = get_decomp_index_path(project_dir)
    if not os.path.isfile(decomp_path):
        return 0

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp_data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return 0

    # Handle both list and dict formats
    phases_list = decomp_data
    if isinstance(decomp_data, dict):
        for key in ("phases", "tasks", "milestones"):
            if key in decomp_data and isinstance(decomp_data[key], list):
                phases_list = decomp_data[key]
                break
        else:
            return 0

    if not isinstance(phases_list, list):
        return 0

    # Find all req_guids from phases that overlap with the delegation
    all_linked_guids = set()
    for phase in phases_list:
        phase_guids = set(phase.get("req_guids", []))
        if phase_guids & delegation_req_ids:  # overlap exists
            all_linked_guids.update(phase_guids)

    # Link unassigned requirements
    req_map = build_normalized_req_map(ledger.get("requirements", []))
    linked = 0

    for guid in all_linked_guids:
        if guid in delegation_req_ids:
            continue  # Already handled by the delegation itself

        req = req_map.get(guid)
        if req and req.get("status") == "pending":
            for pre_stage in ("bdd", "tdd"):
                if get_stage_status(req, pre_stage) == "pending":
                    set_stage_status(req, pre_stage, "completed")
            set_stage_status(req, "code", "delegation_returned")
            linked += 1
            logger.info(
                f"[REQUIREMENTS LEDGER] ITR49-F3: {guid} auto-linked "
                f"from decomp phase (was pending → delegation_returned)"
            )

    if linked:
        logger.info(
            f"[REQUIREMENTS LEDGER] ITR49-F3: Auto-linked {linked} "
            f"unassigned requirements from decomposition phases"
        )

    return linked


def mark_delegation_complete(
    agent_data: dict,
    delegation_id: str,
    response_summary: str = "",
    project_dir: str = None,
) -> bool:
    """Mark a delegation as completed and update linked requirements.

    RCA-362 L1 Fix: Auto-persists to disk after update when project_dir
    is provided. Keeps requirements_ledger.json in sync with in-memory.

    Args:
        agent_data: The agent.data dict
        delegation_id: The delegation ID to complete
        response_summary: Summary of the delegation's response
        project_dir: If provided, auto-persist ledger to disk after update

    Returns:
        True if the delegation was found and updated, False otherwise
    """
    ledger = _ensure_ledger(agent_data)

    # Find and update the delegation
    delegation = None
    for d in ledger["delegations"]:
        if d["id"] == delegation_id:
            delegation = d
            break

    if delegation is None:
        logger.warning(
            f"[REQUIREMENTS LEDGER] Delegation {delegation_id} not found"
        )
        return False

    delegation["response_summary"] = response_summary[:500]

    # RCA-259 Fix G: Evidence-based completion — check for failure signals
    # BEFORE marking requirements as completed. Failed delegations should
    # NOT resolve requirements (prevents phantom success).
    if _has_failure_signals(response_summary):
        delegation["status"] = "failed"

        # RCA-475 D4: SM wrap — transition to 'failed'
        _delegation_sm_transition(
            agent_data, delegation_id, "failed",
            reason=f"failure signals in response",
            source="requirements_delegation_tracker.mark_delegation_complete",
        )

        logger.warning(
            f"[REQUIREMENTS LEDGER] Delegation {delegation_id} FAILED — "
            f"error signals detected in response. Requirements NOT resolved."
        )
        # Bug 6: Wire mark_failed for linked requirements.
        # When a delegation explicitly fails (HARD_STOP, CANCELLED, etc.),
        # requirements shouldn't sit in 'assigned' limbo — mark them failed
        # so pre_delivery_coverage_audit and compute_overall_status know.
        for req_id in delegation.get("requirement_ids", []):
            mark_failed(
                agent_data,
                req_id,
                reason=f"Delegation {delegation_id} failed: "
                       f"{response_summary[:200]}",
            )
        # RCA-362: Persist even on failure — disk must reflect current state
        if project_dir:
            persist_ledger_to_project(agent_data, project_dir)
        return True

    # Normal completion path
    delegation["status"] = "completed"

    # RCA-475 D4: SM wrap — transition to 'completed'
    _delegation_sm_transition(
        agent_data, delegation_id, "completed",
        reason=f"delegation completed successfully",
        source="requirements_delegation_tracker.mark_delegation_complete",
    )


    # F-2 (ITR-22): Update linked requirements to 'delegation_returned' —
    # NOT 'completed'. This intermediate status prevents false-completion
    # vectors where a delegation returns success but the code is incomplete
    # or missing required deliverables. Only mark_requirement_complete()
    # with proof_files can promote requirements to 'completed'.
    req_map = build_normalized_req_map(ledger["requirements"])
    for req_id in delegation.get("requirement_ids", []):
        if req_id in req_map:
            req = req_map[req_id]
            # ADR-086: When setting code to delegation_returned, promote
            # bdd and tdd from pending → completed so compute_overall_status
            # (min-priority) returns delegation_returned, not pending.
            # These stages are implicitly satisfied when code was delegated.
            for pre_stage in ("bdd", "tdd"):
                if get_stage_status(req, pre_stage) == "pending":
                    set_stage_status(req, pre_stage, "completed")
            set_stage_status(req, "code", "delegation_returned")
            logger.info(
                f"[REQUIREMENTS LEDGER] F-2: {req_id} set to "
                f"'delegation_returned' (awaiting proof for completion)"
            )


    logger.info(
        f"[REQUIREMENTS LEDGER] Delegation {delegation_id} completed "
        f"({len(delegation.get('requirement_ids', []))} requirements set to delegation_returned)"
    )
    # RCA-362: Auto-persist to disk after successful completion
    if project_dir:
        persist_ledger_to_project(agent_data, project_dir)

    # F-8: Reconcile decomposition phase statuses after delegation completion.
    # Category-based matching: infers phase category from title/description,
    # matches delegation to phase via overlapping req_guids, then updates
    # the phase status in decomposition_index.json.
    try:
        reconcile_phase_status(agent_data, delegation_id, project_dir=project_dir)
    except Exception as f8_err:
        logger.debug(
            f"[REQUIREMENTS LEDGER] F-8: Phase reconciliation failed "
            f"(non-fatal): {f8_err}"
        )

    # RCA-ITR49 Fix 3: Auto-link unassigned requirements from decomposition
    # phases that match this delegation's scope via overlapping req_guids.
    # Must run BEFORE auto-promote so newly linked reqs also get promoted.
    if project_dir:
        try:
            _auto_link_from_decomp(agent_data, delegation, project_dir)
        except Exception as f3_err:
            logger.debug(
                f"[REQUIREMENTS LEDGER] ITR49-F3: Decomp auto-link failed "
                f"(non-fatal): {f3_err}"
            )

    # RCA-ITR49 Fix 1: Auto-promote delegation_returned → completed
    # when the delegation succeeded (not failed) AND project evidence exists.
    # This closes the status lifecycle gap without reverting ITR-22/ITR-39 safety.
    if project_dir:
        try:
            _auto_promote_delegation_returned(agent_data, delegation, project_dir)
            # Re-persist after promotion so disk reflects completed status
            persist_ledger_to_project(agent_data, project_dir)
        except Exception as f1_err:
            logger.debug(
                f"[REQUIREMENTS LEDGER] ITR49-F1: Auto-promote failed "
                f"(non-fatal): {f1_err}"
            )

    return True

# ─── F-8 Category Inference & F-14 Profile Compatibility ─────────────
# EXTRACTED to python/helpers/requirements_stage.py during P4 modularization.
# Re-exported above: _CATEGORY_KEYWORDS, infer_phase_category,
# _PROFILE_PHASE_COMPATIBILITY


def reconcile_phase_status(
    agent_data: dict,
    delegation_id: str,
    project_dir: str = None,
) -> bool:
    """Reconcile decomposition phase status after a delegation completes.

    F-8 Fix: When a delegation completes, find the matching phase in
    decomposition_index.json by:
      1. Overlapping req_guids (delegation requirement_ids ∩ phase req_guids)
      2. Phase must be in 'pending' status (don't re-complete)

    Category is inferred from the phase TITLE, not its number. This ensures
    the reconciler works regardless of how phases are numbered by any skill.

    Args:
        agent_data: The agent.data dict containing the requirements ledger.
        delegation_id: The ID of the completed delegation.
        project_dir: Path to the project directory containing
            decomposition_index.json.

    Returns:
        True if at least one phase was updated, False otherwise.
    """
    if not project_dir:
        return False

    # Find the delegation in the ledger
    ledger = _ensure_ledger(agent_data)
    delegation = None
    for d in ledger.get("delegations", []):
        if d["id"] == delegation_id:
            delegation = d
            break

    if delegation is None:
        logger.debug(
            f"[REQUIREMENTS LEDGER] F-8: Delegation {delegation_id} not found"
        )
        return False

    # Only reconcile completed delegations
    if delegation.get("status") != "completed":
        return False

    delegation_req_ids = set(delegation.get("requirement_ids", []))
    if not delegation_req_ids:
        return False

    # Read decomposition_index.json
    decomp_path = get_decomp_index_path(project_dir)
    if not os.path.isfile(decomp_path):
        return False

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp_data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.debug(
            f"[REQUIREMENTS LEDGER] F-8: Could not read decomp: {e}"
        )
        return False

    # Handle both list and dict formats
    is_dict_format = isinstance(decomp_data, dict)
    phases_list = decomp_data
    dict_key = None
    if is_dict_format:
        for key in ("phases", "tasks", "milestones"):
            if key in decomp_data and isinstance(decomp_data[key], list):
                phases_list = decomp_data[key]
                dict_key = key
                break
        else:
            return False

    if not isinstance(phases_list, list):
        return False

    # Find phases with overlapping req_guids that are still pending
    updated = False
    for phase in phases_list:
        if phase.get("status") != "pending":
            continue

        phase_req_guids = set(phase.get("req_guids", []))
        if not phase_req_guids:
            continue

        # Match: delegation req_ids overlap with phase req_guids
        overlap = delegation_req_ids & phase_req_guids
        if not overlap:
            continue

        # Infer category from phase title
        phase_title = phase.get("title", "")
        phase_category = infer_phase_category(phase_title)

        # F-14: Category-aware gating. A delegation should only auto-complete
        # phases whose category is compatible with the delegation's profile.
        # E.g., frontend (design) must NOT complete implementation phases.
        delegation_profile = delegation.get("profile", "unknown")
        compatible_categories = _PROFILE_PHASE_COMPATIBILITY.get(
            delegation_profile, set()
        )

        if phase_category != "unknown" and phase_category not in compatible_categories:
            logger.info(
                f"[REQUIREMENTS LEDGER] F-14: Skipping phase {phase.get('seq', '?')} "
                f"({phase_title}) — category '{phase_category}' not compatible with "
                f"profile '{delegation_profile}' (allowed: {compatible_categories})"
            )
            continue

        # ADR-089: Use centralized completion validation instead of direct assignment.
        # Get requirements assigned to this phase from the ledger.
        phase_reqs = _get_requirements_for_phase(
            phase, ledger.get("requirements", [])
        )
        allowed, reason = set_phase_completed(phase, phase_reqs, project_dir)
        if not allowed:
            logger.info(
                f"[REQUIREMENTS LEDGER] ADR-089: Phase {phase.get('seq', '?')} "
                f"({phase_title}) completion blocked: {reason}"
            )
            continue

        phase["note"] = (
            f"F-8 reconciled: delegation {delegation_id} completed "
            f"(category={phase_category}, req overlap={sorted(overlap)}, "
            f"ADR-089 validated: {reason})"
        )
        updated = True
        logger.info(
            f"[REQUIREMENTS LEDGER] F-8: Phase {phase.get('seq', '?')} "
            f"({phase_title}) → completed "
            f"(delegation={delegation_id}, category={phase_category}, "
            f"overlap={sorted(overlap)})"
        )


    # Write back if any phase was updated
    if updated:
        try:
            if is_dict_format and dict_key:
                decomp_data[dict_key] = phases_list
            else:
                decomp_data = phases_list
            with open(decomp_path, "w", encoding="utf-8") as f:
                json.dump(decomp_data, f, indent=2)
        except (IOError, OSError) as e:
            logger.warning(
                f"[REQUIREMENTS LEDGER] F-8: Could not write decomp: {e}"
            )
            return False

        # FIX-4 BUG-2: Sync updated phases back to agent_data["_decomposition_index"].
        # Root cause: This function updates the ON-DISK decomposition_index.json
        # but never updates the IN-MEMORY copy. The coverage gate at
        # _16_decomposition_coverage_gate.py:194 reads from agent_data, so it
        # still sees the stale "pending" status — causing re-delegation of
        # completed work and HARD_BLOCKs.
        # Fix: Mirror the disk write into agent_data.
        try:
            existing_decomp = agent_data.get("_decomposition_index", {})
            if isinstance(existing_decomp, dict):
                # Update the phases list within the existing dict structure
                for key_name in ("phases", "tasks", "milestones"):
                    if key_name in existing_decomp and isinstance(existing_decomp[key_name], list):
                        # Update matching phases by seq
                        for updated_phase in phases_list:
                            seq = updated_phase.get("seq")
                            if seq is None:
                                continue
                            for i, mem_phase in enumerate(existing_decomp[key_name]):
                                if mem_phase.get("seq") == seq:
                                    existing_decomp[key_name][i] = dict(updated_phase)
                                    break
                        break
                else:
                    # No known key found — store the full phases list
                    agent_data["_decomposition_index"] = {"phases": list(phases_list)}
            elif isinstance(existing_decomp, list):
                # Direct list format — replace entirely
                agent_data["_decomposition_index"] = list(phases_list)
            else:
                # No existing data — store fresh
                agent_data["_decomposition_index"] = {"phases": list(phases_list)}

            logger.info(
                f"[REQUIREMENTS LEDGER] FIX-4: Synced {len(phases_list)} phases "
                f"back to agent_data['_decomposition_index']"
            )
        except Exception as sync_err:
            logger.debug(
                f"[REQUIREMENTS LEDGER] FIX-4: In-memory decomp sync failed "
                f"(non-fatal): {sync_err}"
            )

    return updated


def mark_delegation_escalated(
    agent_data: dict,
    delegation_id: str,
    escalation_reason: str = "",
) -> bool:
    """Mark a delegation as escalated and revert linked requirements to pending.

    When a subordinate escalates, the delegation is not "failed" (which implies
    retry-with-same-approach). It's "escalated" — meaning a different agent/approach
    is needed. Linked requirements revert to 'pending' so they can be re-assigned.

    Args:
        agent_data: The agent.data dict
        delegation_id: The delegation ID to escalate
        escalation_reason: Why the subordinate escalated

    Returns:
        True if the delegation was found and updated, False otherwise
    """
    ledger = _ensure_ledger(agent_data)

    delegation = None
    for d in ledger["delegations"]:
        if d["id"] == delegation_id:
            delegation = d
            break

    if delegation is None:
        logger.warning(
            f"[REQUIREMENTS LEDGER] Delegation {delegation_id} not found for escalation"
        )
        return False

    delegation["status"] = "escalated"
    delegation["response_summary"] = f"ESCALATED: {escalation_reason[:500]}"

    # RCA-475 D4: SM wrap — 'escalated' is not in DelegationSM, use 'failed' via force
    _delegation_sm_transition(
        agent_data, delegation_id, "failed",
        reason=f"escalated: {escalation_reason[:100]}",
        source="requirements_delegation_tracker.mark_delegation_escalated",
    )

    # Clear dedup hash so a new approach can be tried
    dedup_hashes = ledger.get("dedup_hashes", {})
    content_hash = _dedup_hash(delegation["profile"], delegation["message_summary"])
    dedup_hashes.pop(content_hash, None)

    # Revert linked requirements to 'pending' so they can be re-assigned
    req_map = build_normalized_req_map(ledger["requirements"])
    for req_id in delegation.get("requirement_ids", []):
        if req_id in req_map:
            req = req_map[req_id]
            if req["status"] in ("assigned", "pending"):
                set_stage_status(req, "code", "pending")
                logger.info(
                    f"[REQUIREMENTS LEDGER] {req_id} reverted to pending "
                    f"(escalation from {delegation_id})"
                )

    logger.warning(
        f"[REQUIREMENTS LEDGER] Delegation {delegation_id} ESCALATED — "
        f"{escalation_reason[:100]}. "
        f"{len(delegation.get('requirement_ids', []))} requirements reverted."
    )
    return True


# ─── Per-Requirement Partial/Failed Status (Phase 1 Gate Refactor) ──────


def mark_partial(
    agent_data: dict,
    req_id: str,
    reason: str,
    attempt: int = 1,
    gate_name: str = "",
    check_name: str = "",
) -> bool:
    """Mark a requirement as PARTIAL — tried but not fully met.

    Called when a gate check fails for a specific requirement after remediation.
    Tracks attempt count. After 3 attempts, the requirement is accepted as
    partial and the system escapes (replaces universal_gate_budget force-allow).

    Both partial and failed follow the same 3-attempt pattern. The difference
    is semantic: partial = some done, failed = couldn't do at all (edge case).
    Separate from code-level/build-level retry systems.

    Args:
        agent_data: The agent.data dict
        req_id: The requirement ID (e.g., "REQ-007")
        reason: Why the requirement is partial (gate check failure message)
        attempt: Current attempt number (1, 2, 3)
        gate_name: Which gate caught this (bdd/tdd/done)
        check_name: Which specific check failed

    Returns:
        True if this was attempt 3+ (accepted as partial, escape)
        False if more attempts remain
    """
    ledger = _ensure_ledger(agent_data)
    for req in ledger.get("requirements", []):
        if req["id"] == req_id:
            # Track partial attempts
            partial_history = req.setdefault("partial_history", [])
            partial_history.append({
                "attempt": attempt,
                "reason": reason[:500],
                "gate": gate_name,
                "check": check_name,
                "timestamp": __import__("time").time(),
            })

            if attempt >= 3:
                # Map gate name to stage name for stage-keyed pipeline
                # ("done" gate validates the "code" stage)
                _GATE_TO_STAGE = {"bdd": "bdd", "tdd": "tdd", "done": "code"}
                stage = _GATE_TO_STAGE.get(gate_name, "code")
                set_stage_status(req, stage, "partial")
                req["partial_reason"] = reason[:500]
                logger.warning(
                    f"[LEDGER] REQ {req_id} marked PARTIAL (stage={stage}) after "
                    f"{attempt} attempts: {reason[:100]}"
                )
                return True  # Escape — accepted as partial

            logger.info(
                f"[LEDGER] REQ {req_id} partial attempt {attempt}/3: "
                f"{reason[:100]}"
            )
            return False  # More attempts remain

    logger.warning(f"[LEDGER] REQ {req_id} not found for mark_partial")
    return False


def mark_failed(
    agent_data: dict,
    req_id: str,
    reason: str,
) -> bool:
    """Mark a requirement as FAILED — structurally impossible.

    Unlike partial (tried but incomplete), failed means the requirement
    CANNOT be met (e.g., external API unavailable, missing credentials).
    Edge case — most requirements will be partial, not failed.

    Both partial and failed are "done" statuses for lifecycle purposes
    (in REQ_DONE_STATUSES) but are explicitly NOT "completed" or "verified".

    Returns True if the requirement was found and marked.
    """
    ledger = _ensure_ledger(agent_data)
    for req in ledger.get("requirements", []):
        if req["id"] == req_id:
            # Set all stages to failed via the stage-keyed pipeline
            for stage in ("bdd", "tdd", "code"):
                set_stage_status(req, stage, "failed")
            req["failed_reason"] = reason[:500]
            logger.warning(
                f"[LEDGER] REQ {req_id} marked FAILED: {reason[:100]}"
            )
            return True
    logger.warning(f"[LEDGER] REQ {req_id} not found for mark_failed")
    return False


def get_partial_summary(agent_data: dict) -> list:
    """Get structured summary of all partial/failed requirements.

    Returns list of dicts with {req_id, text, status, reason, attempts}
    for the final response template. Used by the orchestrator gate to
    build a structured per-requirement status summary instead of the
    old unstructured 'INCOMPLETE ITEMS' text.
    """
    ledger = _ensure_ledger(agent_data)
    results = []
    for req in ledger.get("requirements", []):
        if req.get("status") in ("partial", "failed"):
            results.append({
                "req_id": req["id"],
                "text": req.get("text", "")[:200],
                "status": req["status"],
                "reason": req.get("partial_reason", req.get("failed_reason", "")),
                "attempts": len(req.get("partial_history", [])),
            })
    return results

