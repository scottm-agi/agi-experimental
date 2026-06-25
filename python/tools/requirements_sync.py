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
from python.tools.requirements_manifest import _normalize_seq, _seq_less_than

def _validate_mandatory_phases(phases: list) -> list:
    """Ensure mandatory pre-code phases exist in decomposition_index.

    ITR-18 FIX (ISS-3): The LLM drops Phase 0.5 and 2.3 from the
    decomposition index. This L1 deterministic validator auto-injects
    missing mandatory phases and sorts by seq number.

    ITR-30 P0 FIX (SS-2/SS-6): Normalizes seq values before comparison
    so semver format (2.3.0) matches canonical format (2.3).

    Args:
        phases: List of phase dicts from decomposition_index.json

    Returns:
        Updated list with mandatory phases injected if missing, sorted by seq.
    """
    # ITR-30: Normalize existing seqs to canonical format for comparison
    existing_seqs = {_normalize_seq(p.get("seq", "")) for p in phases}

    injected = []
    for seq, template in _MANDATORY_PHASES.items():
        # RCA-470: Normalize BOTH sides — existing seqs are normalized above,
        # so template keys must also be normalized for comparison.
        if _normalize_seq(seq) not in existing_seqs:
            phase = {
                "seq": seq,
                "guid": f"REQ-AUTO-{seq.replace('.', '')}",
                **template,
                "note": f"ITR-18: Auto-injected mandatory phase {seq} (was missing from LLM decomposition)",
            }
            injected.append(phase)
            logger.info(
                f"[REQUIREMENTS TOOL] ITR-18: Auto-injected mandatory Phase {seq} "
                f"({template['title']}) into decomposition_index"
            )

    if injected:
        phases = phases + injected

    # Sort by seq number for correct ordering
    # ITR-30: Use _normalize_seq for sort key to handle semver
    def seq_sort_key(p):
        try:
            return float(_normalize_seq(p.get("seq", "99")))
        except (ValueError, TypeError):
            return 99.0

    phases.sort(key=seq_sort_key)

    return phases


def _detect_dropped_phases(old_phases: list, new_phases: list) -> list:
    """Detect phases present in old_phases but missing from new_phases.

    RCA-470 Fix 4: When the LLM rewrites decomposition_index.json, it may
    silently drop phases added by the Architect or auto-injected by the
    framework. This function compares old vs new and returns the dropped ones.

    Args:
        old_phases: Existing phases from disk (before rewrite).
        new_phases: New phases from the LLM (about to be written).

    Returns:
        List of phase dicts that exist in old but not in new. Each dict
        preserves its original status/title/req_guids.
    """
    old_seqs = {}
    for p in old_phases:
        seq = _normalize_seq(p.get("seq", p.get("phase_seq", "")))
        if seq:
            old_seqs[seq] = p

    new_seqs = set()
    for p in new_phases:
        seq = _normalize_seq(p.get("seq", p.get("phase_seq", "")))
        if seq:
            new_seqs.add(seq)

    dropped = []
    for seq, phase in old_seqs.items():
        if seq not in new_seqs:
            dropped.append(dict(phase))  # copy to avoid mutation
            logger.warning(
                f"[REQUIREMENTS TOOL] RCA-470 Fix 4: Phase {seq} "
                f"({phase.get('title', '?')}, status={phase.get('status', '?')}) "
                f"was DROPPED by LLM decomposition rewrite"
            )

    return dropped


def _reinject_dropped_phases(new_phases: list, dropped: list) -> list:
    """Re-inject dropped phases back into the new decomposition.

    RCA-470 Fix 4: Merges dropped phases back, adds a recovery note,
    and returns the sorted combined list.

    Args:
        new_phases: The new phases from the LLM.
        dropped: Phases that were dropped (from _detect_dropped_phases).

    Returns:
        Merged list with dropped phases re-injected, sorted by seq.
    """
    for phase in dropped:
        phase["note"] = (
            f"RCA-470 Fix 4: Auto re-injected — this phase was dropped by "
            f"LLM decomposition rewrite but preserved by framework merge protection"
        )
        logger.info(
            f"[REQUIREMENTS TOOL] RCA-470 Fix 4: Re-injecting dropped Phase "
            f"{phase.get('seq', '?')} ({phase.get('title', '?')})"
        )

    merged = list(new_phases) + dropped

    # Sort by seq number
    def seq_sort_key(p):
        try:
            return float(_normalize_seq(p.get("seq", p.get("phase_seq", "99"))))
        except (ValueError, TypeError):
            return 99.0

    merged.sort(key=seq_sort_key)
    return merged


# ── Wire 1: Integration requirement seeding hook ─────────────────────────
# Seeds REQ-INT-xxx requirements from the architect's dependency-graph.json
# after Phase 2 completes. Called by _reconcile_mandatory_phase_status
# callers (requirements_actions.py) after reconciliation.

def _seed_integration_from_dep_graph(
    project_dir: str,
    agent_data: dict,
    phases: list,
) -> int:
    """Seed integration requirements from dependency-graph.json if Phase 2 is done.

    This is the wiring hook that connects the budget_cost_model's
    generate_integration_requirements() and requirements_seeding's
    seed_integration_requirements() to the actual pipeline.

    Args:
        project_dir: Absolute path to the project directory.
        agent_data: Agent data dict (requirements ledger lives here).
        phases: List of phase dicts from decomposition_index.json.

    Returns:
        Number of new integration requirements seeded (0 if skipped).
    """
    # Only seed after Phase 2 is completed
    phase2_done = any(
        str(p.get("seq", "")) == "2" and p.get("status") == "completed"
        for p in phases
    )
    if not phase2_done:
        return 0

    # Check if dependency-graph.json exists
    graph_path = os.path.join(project_dir, "docs", "dependency-graph.json")
    if not os.path.isfile(graph_path):
        return 0

    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            dep_graph = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.debug(f"[INTEGRATION SEEDING] Failed to read dependency graph: {e}")
        return 0

    try:
        from python.helpers.requirements_seeding import seed_integration_requirements
        count = seed_integration_requirements(agent_data, dep_graph)
        if count > 0:
            logger.info(
                f"[INTEGRATION SEEDING] Seeded {count} REQ-INT-xxx requirements "
                f"from dependency-graph.json"
            )
        return count
    except Exception as e:
        logger.debug(f"[INTEGRATION SEEDING] Seeding failed (non-fatal): {e}")
        return 0


def _reconcile_mandatory_phase_status(phases: list, project_dir: str, *, agent_data: dict = None) -> list:
    """Reconcile auto-injected phase statuses based on artifact existence.

    F-2 FIX: Auto-injected phases (0.5, 1, 2.3) stay 'pending' in
    decomposition_index.json even after their work completes. This function
    checks for known completion artifacts and auto-marks phases as completed.

    F-6 FIX: Monotonicity guard — before auto-completing Phase X, verify
    that ALL predecessor phases with artifact-map entries are completed.
    This prevents non-monotonic completion (e.g., 2.5 marked completed
    while 2.4 is still pending).

    F-9 FIX: Execution tracking — when agent_data is provided and contains
    _phases_dispatched, only auto-complete phases that were actually dispatched.
    This prevents false completion of phases whose artifacts are side-effects
    of other phases' work (e.g., Phase 2.5 artifact created by Phase 2.3's
    skeleton generator). Falls back to artifact-only behavior when dispatch
    tracking is unavailable (backward compatibility).

    Rules:
    - Only checks phases with status == 'pending' (won't revert completed)
    - Files must be >50 bytes (avoids counting stubs/empty files)
    - Directories must be non-empty
    - Sets 'completion_evidence' on the phase dict for auditability
    - Won't auto-complete if a predecessor phase (with artifact map) is pending
    - Won't auto-complete if dispatch tracking is active and phase wasn't dispatched

    Args:
        phases: List of phase dicts from decomposition_index.json
        project_dir: Absolute path to the project directory
        agent_data: Optional agent data dict containing _phases_dispatched.
                    When None or missing _phases_dispatched key, falls back
                    to artifact-only behavior (backward compatibility).

    Returns:
        The phases list (mutated in-place for convenience).
    """
    # ── SS-1/5: CHECKPOINT FAST PATH ──────────────────────────────────
    # Try to resolve phase statuses from checkpoint.json first (O(1)).
    # If checkpoint exists and all artifacts validate, skip the full scan.
    try:
        from python.helpers.phase_checkpoint import load_checkpoint, validate_checkpoint
        _ckpt = load_checkpoint(project_dir)
        if _ckpt is not None:
            _validated = validate_checkpoint(project_dir, _ckpt)
            _ckpt_phases = _validated.get("phases", {})
            _pending_seqs = [
                str(p.get("seq", "")) for p in phases
                if p.get("status") == "pending"
            ]
            _fast_resolved = 0
            for _p in phases:
                _seq = str(_p.get("seq", ""))
                if _p.get("status") != "pending":
                    continue
                _ckpt_entry = _ckpt_phases.get(_seq)
                if _ckpt_entry and _ckpt_entry.get("status") == "completed":
                    _p["status"] = "completed"
                    _p["completion_evidence"] = f"checkpoint_fast_path"
                    _fast_resolved += 1
            if _fast_resolved > 0:
                logger.info(
                    f"[RECONCILER] SS-1/5: Checkpoint fast-path resolved "
                    f"{_fast_resolved}/{len(_pending_seqs)} pending phases"
                )
            # If ALL pending phases were resolved by checkpoint, skip full scan
            remaining_pending = [
                p for p in phases if p.get("status") == "pending"
                and _PHASE_ARTIFACT_MAP.get(_normalize_seq(str(p.get("seq", ""))))
            ]
            if not remaining_pending and _fast_resolved > 0:
                logger.info(
                    "[RECONCILER] SS-1/5: All artifact-mapped phases resolved "
                    "from checkpoint — skipping full artifact scan"
                )
                return phases
    except Exception as _ckpt_err:
        logger.debug(
            f"[RECONCILER] SS-1/5: Checkpoint fast-path unavailable "
            f"(falling back to full scan): {_ckpt_err}"
        )
    # ── END CHECKPOINT FAST PATH ────────────────────────────────────────

    # F-9: Determine if execution tracking is active
    _dispatch_tracking_active = False
    _dispatched_phases = set()
    if agent_data is not None and '_phases_dispatched' in agent_data:
        _dispatch_tracking_active = True
        raw = agent_data['_phases_dispatched']
        _dispatched_phases = set(str(s) for s in (raw if isinstance(raw, list) else [raw]))
    for phase in phases:
        seq = str(phase.get("seq", ""))
        if phase.get("status") != "pending":
            continue
        artifacts = _PHASE_ARTIFACT_MAP.get(_normalize_seq(seq), [])
        if not artifacts:
            continue

        # F-6: MONOTONICITY CHECK — Don't auto-complete if any predecessor
        # with an artifact-map entry is still pending.
        has_pending_predecessor = False
        for pred_phase in phases:
            pred_seq = str(pred_phase.get("seq", ""))
            if pred_seq == seq:
                continue
            try:
                if _seq_less_than(pred_seq, seq) and pred_phase.get("status") == "pending":
                    # Only block if predecessor HAS artifacts to check
                    # (if it has no artifacts, it's execution-tracked and we can't know)
                    if _PHASE_ARTIFACT_MAP.get(pred_seq):
                        has_pending_predecessor = True
                        logger.info(
                            f"[RECONCILER] Monotonicity guard: NOT auto-completing phase {seq} "
                            f"because predecessor phase {pred_seq} is still pending"
                        )
                        break
            except (ValueError, TypeError):
                continue

        if has_pending_predecessor:
            continue  # Skip auto-completion

        # F-9: EXECUTION TRACKING CHECK — Don't auto-complete if dispatch
        # tracking is active and this phase was never dispatched.
        if _dispatch_tracking_active and seq not in _dispatched_phases:
            logger.info(
                f"[RECONCILER] F-9: NOT auto-completing phase {seq} — "
                f"artifact exists but phase was never dispatched "
                f"(potential side-effect of another phase's work)"
            )
            continue

        for artifact in artifacts:
            full_path = os.path.join(project_dir, artifact)
            if os.path.exists(full_path):
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 50:
                    from python.helpers.requirements_ledger import try_phase_completed
                    allowed, reason = try_phase_completed(
                        phase, project_dir,
                        note=f"auto-reconciled (F-2: artifact {artifact} exists, "
                             f"{os.path.getsize(full_path)} bytes)",
                    )
                    if allowed:
                        phase["completion_evidence"] = artifact
                        logger.info(
                            f"[REQUIREMENTS TOOL] F-2: Auto-completed phase {seq} "
                            f"(artifact {artifact} exists, "
                            f"{os.path.getsize(full_path)} bytes)"
                        )
                        # SS-1/5: Persist checkpoint for restart resilience
                        try:
                            from python.helpers.phase_checkpoint import persist_phase_checkpoint
                            persist_phase_checkpoint(
                                project_dir, phase_id=seq,
                                status="completed", artifacts=[artifact],
                            )
                        except Exception:
                            pass  # Non-fatal
                    else:
                        logger.info(
                            f"[REQUIREMENTS TOOL] F-2: Phase {seq} completion BLOCKED "
                            f"by ADR-089: {reason}"
                        )
                    break
                elif os.path.isdir(full_path) and os.listdir(full_path):
                    from python.helpers.requirements_ledger import try_phase_completed
                    allowed, reason = try_phase_completed(
                        phase, project_dir,
                        note=f"auto-reconciled (F-2: directory {artifact} is non-empty)",
                    )
                    if allowed:
                        phase["completion_evidence"] = artifact
                        logger.info(
                            f"[REQUIREMENTS TOOL] F-2: Auto-completed phase {seq} "
                            f"(directory {artifact} is non-empty)"
                        )
                        # SS-1/5: Persist checkpoint for restart resilience
                        try:
                            from python.helpers.phase_checkpoint import persist_phase_checkpoint
                            persist_phase_checkpoint(
                                project_dir, phase_id=seq,
                                status="completed", artifacts=[artifact],
                            )
                        except Exception:
                            pass  # Non-fatal
                    else:
                        logger.info(
                            f"[REQUIREMENTS TOOL] F-2: Phase {seq} completion BLOCKED "
                            f"by ADR-089: {reason}"
                        )
                    break
    return phases

def detect_artifact_gaps(phases: list, project_dir: str) -> list:
    """Detect completed phases with missing output artifacts.

    F-3 FIX: When a phase is marked "completed" or "delegation_returned"
    but its critical output artifacts (from _PHASE_ARTIFACT_MAP) are missing,
    this function detects the gap and returns a structured report. The report
    can be consumed by the orchestrator to nudge re-delegation.

    A gap is reported when a completed phase has an artifact-map entry but
    NONE of its expected artifacts exist on disk. If at least one artifact
    exists, no gap is reported (partial completion is acceptable).

    Args:
        phases: List of phase dicts from decomposition_index.json
        project_dir: Absolute path to the project directory

    Returns:
        List of gap dicts, each containing:
        - phase_seq: Phase sequence number
        - phase_title: Phase title
        - missing_artifacts: List of missing artifact paths
        - present_artifacts: List of present artifact paths
        - recommendation: Actionable text suggesting re-delegation
    """
    # Statuses that indicate a phase "should have" produced artifacts
    completed_statuses = {"completed", "delegation_returned"}

    gaps = []
    for phase in phases:
        seq = str(phase.get("seq", ""))
        status = phase.get("status", "pending")

        # Only check phases that are marked as done
        if status not in completed_statuses:
            continue

        # Only check phases that have expected artifacts
        expected_artifacts = _PHASE_ARTIFACT_MAP.get(_normalize_seq(seq), [])
        if not expected_artifacts:
            continue

        missing = []
        present = []
        for artifact in expected_artifacts:
            full_path = os.path.join(project_dir, artifact)
            if os.path.exists(full_path):
                # Check file size or directory content
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 50:
                    present.append(artifact)
                elif os.path.isdir(full_path) and os.listdir(full_path):
                    present.append(artifact)
                else:
                    missing.append(artifact)  # Exists but too small/empty
            else:
                missing.append(artifact)

        # Only report a gap if ALL artifacts are missing (none present)
        if missing and not present:
            title = phase.get("title", f"Phase {seq}")
            gaps.append({
                "phase_seq": seq,
                "phase_title": title,
                "missing_artifacts": missing,
                "present_artifacts": present,
                "recommendation": (
                    f"Re-delegate Phase {seq} ({title}) to produce "
                    f"{', '.join(missing)}"
                ),
            })
            logger.info(
                f"[RECONCILER] F-3: Artifact gap detected for completed phase {seq} "
                f"({title}): missing {missing}"
            )

    return gaps

def _sync_decomp_assignments_to_ledger(agent_data: dict, phases: list) -> None:
    """Sync decomposition phase assignments back to requirement records.

    ISS-R4 FIX: When decomposition_index.json is saved, the req_guids/req_ids
    in each phase represent the assignment mapping. But the individual requirement
    records in agent_data._requirements_ledger still have status="pending" and
    empty assigned_to. This causes check_assignment_coverage() to report them
    as unassigned, creating an infinite orchestrator loop.

    Fix: Extract all req IDs from decomp phases, then set status="assigned"
    and assigned_to=[phase_seq] for matching requirement records.
    """
    # Collect all REQ-IDs → list of phases they appear in
    req_to_phases: dict = {}
    for phase in phases:
        seq = phase.get("seq", "?")
        for key in ("req_guids", "req_ids"):
            for rid in phase.get(key, []):
                if rid not in req_to_phases:
                    req_to_phases[rid] = []
                if seq not in req_to_phases[rid]:
                    req_to_phases[rid].append(seq)

    if not req_to_phases:
        return

    # Update requirement records in agent_data
    ledger = agent_data.get("_requirements_ledger", {})
    reqs = ledger.get("requirements", [])
    synced = 0

    for req in reqs:
        rid = req.get("id", "")
        if rid in req_to_phases:
            current_status = req.get("status", "pending")
            # Only upgrade pending → assigned (don't downgrade completed/verified)
            if current_status == "pending":
                req["status"] = "assigned"
                synced += 1
            # Always update assigned_to with phase info
            req["assigned_to"] = req_to_phases[rid]

    if synced > 0:
        logger.info(
            f"[REQUIREMENTS TOOL] ISS-R4: Synced {synced} requirements from "
            f"pending → assigned based on decomposition_index.json "
            f"({len(req_to_phases)} total REQ-IDs in phases)"
        )

    # F-8: Use canonical status set (was missing partially_completed + verified)
    from python.helpers.status_constants import PHASE_DONE_STATUSES as _DONE_STATUSES
    phase_status_map = {}
    for phase in phases:
        seq = phase.get("seq", "?")
        phase_status_map[str(seq)] = str(phase.get("status", "pending")).lower().strip()

    completed_count = 0
    for req in reqs:
        rid = req.get("id", "")
        if rid in req_to_phases and req.get("status") == "assigned":
            linked_seqs = req_to_phases[rid]
            all_done = all(
                phase_status_map.get(str(s), "pending") in _DONE_STATUSES
                for s in linked_seqs
            )
            if all_done:
                # ADR-086 ITR-52 FIX: Use set_stage_status() to keep
                # stage_status.code in sync with the flat status field.
                # Previously this was req["status"] = "completed" which
                # left stage_status.code at "delegation_returned", causing
                # the ADR-086 gate to block implementation phases.
                from python.helpers.requirements_ledger import set_stage_status
                set_stage_status(req, "code", "delegation_returned")
                completed_count += 1

    if completed_count > 0:
        logger.info(
            f"[REQUIREMENTS TOOL] SS-11: Promoted {completed_count} requirements from "
            f"assigned → delegation_returned (all linked phases are done, awaiting test proof)"
        )

def _sync_decomposition_plan_to_agent_data(agent_data: dict, phases: list) -> None:
    """Wire _decomposition_task_count and _decomposition_plan from decomp phases.

    Pipeline Gap Fix: Two agent.data keys are read by downstream consumers but
    were never written, making their features dead code:

    1. _decomposition_task_count (int) — read by Agent.get_max_turns() for
       dynamic budget scaling (R-4 / RCA-362). Without this, the budget never
       scales with decomposition complexity.

    2. _decomposition_plan (dict) — read by:
       - _45_intelligent_supervisor.py:687 for decomposition progress suppression
       - manifest_packages.py:90 for SDK integration package validation (RCA-237 RC-4)

    This function is called from _handle_save_manifest after writing
    decomposition_index.json. It computes both keys from the phase list.

    Args:
        agent_data: The agent's data dict.
        phases: The decomposition_index.json content (list of phase dicts).
    """
    # ── _decomposition_task_count ──
    task_count = len(phases) if isinstance(phases, list) else 0
    agent_data["_decomposition_task_count"] = task_count

    # ── _decomposition_plan ──
    completed = 0
    in_progress = 0
    pending = 0
    _DONE = {"completed", "done", "complete", "skipped"}
    _WIP = {"in_progress", "in-progress", "active", "started"}
    _DEFERRED = {"deferred", "deferred_to_next_phase"}

    # Collect integrations from all phases
    all_integrations: list = []
    seen_integration_names: set = set()

    for phase in (phases if isinstance(phases, list) else []):
        status = str(phase.get("status", "pending")).lower().strip()
        if status in _DONE:
            completed += 1
        elif status in _WIP:
            in_progress += 1
        elif status not in _DEFERRED:
            pending += 1
        # else: deferred — tracked in total but not in pending

        # Extract integrations from phases (RCA-237 RC-4)
        phase_integrations = phase.get("integrations", [])
        for integration in phase_integrations:
            name = integration.get("name", "")
            if name and name not in seen_integration_names:
                seen_integration_names.add(name)
                all_integrations.append(integration)

    plan = {
        "total_tasks": task_count,
        "completed_tasks": completed,
        "in_progress_tasks": in_progress,
        "pending_tasks": pending,
    }

    if all_integrations:
        plan["integrations"] = all_integrations

    agent_data["_decomposition_plan"] = plan

    # ── FIX-4 BUG-1: Wire _decomposition_index ──
    # Root cause: _16_decomposition_coverage_gate.py reads
    # agent_data["_decomposition_index"] for dependency checking (U-295-5),
    # but this key was NEVER written — declared in agent_data_keys.py as
    # persisted but no code path populated it. Result: the dependency gate
    # always saw {} and never actually blocked out-of-order delegations.
    # Fix: Store the full phase list so in-memory consumers see current statuses.
    agent_data["_decomposition_index"] = {
        "phases": list(phases) if isinstance(phases, list) else [],
    }

    logger.info(
        f"[REQUIREMENTS TOOL] Synced decomposition plan to agent.data: "
        f"{task_count} tasks ({completed} done, {in_progress} WIP, "
        f"{pending} pending), {len(all_integrations)} integrations"
    )

def _reconcile_decomp_statuses(phases: list, project_dir: str, agent_data: dict | None = None) -> None:
    """Auto-mark decomp phases as completed when their deliverable files exist.

    ISS-R3 FIX: The orchestrator LLM doesn't always update phase statuses
    after delegations complete. This function checks for known deliverable
    files on disk and auto-marks phases that produced them.

    Mutates the phases list in-place.
    """
    # FIX-7: Auto-sync decomp assignments to ledger at reconciliation time.
    # Root cause: sync only ran during save_manifest/check_coverage, not during
    # phase completion reconciliation. This caused false unassigned requirements.
    try:
        _sync_decomp_assignments_to_ledger(agent_data, phases)
    except Exception as sync_err:
        logger.debug(f"[RECONCILE] FIX-7: Decomp-ledger sync skipped: {sync_err}")

    # Map phase seq IDs to their expected deliverable files
    PHASE_DELIVERABLES = {
        "0.5": [
            os.path.join("docs", "framework-research.md"),
        ],
        "1": [
            "package.json",
            ".env.example",
        ],
        "2": [
            os.path.join("docs", "architecture-spec.md"),
            # ISS-5 FIX: BDD scenarios are a Phase 2 deliverable.
            # Architect MUST produce bdd-scenarios.md via save_bdd_scenarios.
            os.path.join("docs", "bdd-scenarios.md"),
        ],
        "2.3": [
            os.path.join("docs", "design-tokens.json"),
            os.path.join("docs", "component-spec.md"),
            os.path.join("docs", "ux-flows.md"),
        ],
        "2.5": [
            # Phase 2.5 is a validation step — no specific deliverable.
            # Auto-completed by downstream inference (see below).
        ],
        "2.6": [
            os.path.join("docs", "planning-cross-check.md"),
            os.path.join("docs", "design-cross-check.md"),
            # RCA-ITR5 ISSUE-1: LLMs may name this file differently across runs
            os.path.join("docs", "cross-check-report.md"),
        ],
        "2.7": [
            os.path.join("docs", "test-skeleton.json"),
        ],
        "2.8": [
            # Phase 2.8: TDD Skeleton Expansion — auto-generated by
            # generate_tdd_stubs() + generate_wiring_test_stubs() after
            # BDD quality gate passes. The .tdd_hash sentinel file
            # is only written after successful stub generation (SS-4).
            os.path.join("docs", "tdd", ".tdd_hash"),
        ],
    }

    # ISS-5 FIX: Alternative glob patterns for deliverable discovery.
    # Root cause: The researcher agent uses save_deliverable() which writes to
    # deliverables/researcher_<timestamp>.md instead of docs/framework-research.md.
    # The existing deliverable_rescue.py only rescues inline content (when the
    # researcher returns content in its delegation response). This map handles
    # the case where the researcher DID save a file — just to the wrong location.
    # Universal pattern: any phase can add alt_globs for known alternative paths.
    PHASE_ALT_GLOBS = {
        "0.5": [
            os.path.join("deliverables", "researcher_*.md"),
        ],
    }

    for phase in phases:
        seq = str(phase.get("seq", ""))
        status = phase.get("status", "pending")

        # Only auto-complete phases that are still "pending"
        if status != "pending":
            continue

        expected_files = PHASE_DELIVERABLES.get(seq)
        if not expected_files:
            # Even without fixed paths, check alt_globs
            alt_globs = PHASE_ALT_GLOBS.get(seq, [])
            if alt_globs:
                for pattern in alt_globs:
                    matches = glob.glob(os.path.join(project_dir, pattern))
                    if matches:
                        matched_file = os.path.relpath(matches[0], project_dir)
                        from python.helpers.requirements_ledger import try_phase_completed
                        allowed, reason = try_phase_completed(
                            phase, project_dir,
                            note=f"auto-reconciled (ISS-5: alt-glob {matched_file} exists)",
                        )
                        if allowed:
                            logger.info(
                                f"[REQUIREMENTS TOOL] ISS-5: Auto-completed phase {seq} "
                                f"(alt-glob deliverable {matched_file} found)"
                            )
                        else:
                            logger.info(
                                f"[REQUIREMENTS TOOL] ISS-5: Phase {seq} completion BLOCKED "
                                f"by ADR-089: {reason}"
                            )
                        break
            continue

        # RCA-ITR3 F-2: Phase 2.3 requires ALL deliverables (not just ANY)
        # because design-tokens alone doesn't mean the design phase is complete.
        # Other phases use generous ANY logic (1 file = phase done).
        REQUIRE_ALL_PHASES = {"2.3"}

        if seq in REQUIRE_ALL_PHASES:
            # Check if ALL expected files exist
            all_exist = all(
                os.path.isfile(os.path.join(project_dir, expected))
                for expected in expected_files
            )
            if all_exist:
                from python.helpers.requirements_ledger import try_phase_completed
                allowed, reason = try_phase_completed(
                    phase, project_dir,
                    note=f"auto-reconciled (ISS-R3: all {len(expected_files)} deliverables found)",
                )
                if allowed:
                    logger.info(
                        f"[REQUIREMENTS TOOL] ISS-R3: Auto-completed phase {seq} "
                        f"(all {len(expected_files)} deliverables found)"
                    )
                else:
                    logger.info(
                        f"[REQUIREMENTS TOOL] ISS-R3: Phase {seq} completion BLOCKED "
                        f"by ADR-089: {reason}"
                    )
        else:
            # Check if ANY expected file exists (generous — 1 file = phase done)
            found = False
            for expected in expected_files:
                full_path = os.path.join(project_dir, expected)
                if os.path.isfile(full_path):
                    # RCA-475 Fix 1: Phase 2.7 must check BDD validation before
                    # auto-completing. Without this, ISS-R3 auto-completes on
                    # test-skeleton.json existence even when BDD coverage is 39%.
                    if seq == "2.7":
                        bdd_val_path = os.path.join(project_dir, "docs", ".bdd_validation.json")
                        if os.path.isfile(bdd_val_path):
                            try:
                                with open(bdd_val_path) as bvf:
                                    bdd_val = json.load(bvf)
                                if not bdd_val.get("pass", True):
                                    logger.warning(
                                        f"[REQUIREMENTS TOOL] ISS-R3: NOT auto-completing phase 2.7 "
                                        f"— BDD validation failed "
                                        f"({bdd_val.get('coverage', 0):.0%} coverage)"
                                    )
                                    break  # Skip auto-completion
                            except (json.JSONDecodeError, OSError) as e:
                                logger.debug(
                                    f"[REQUIREMENTS TOOL] ISS-R3: Could not read "
                                    f".bdd_validation.json: {e} — treating as no-file"
                                )
                                # Corrupt file → treat as no-file, allow auto-completion

                    from python.helpers.requirements_ledger import try_phase_completed
                    allowed, reason = try_phase_completed(
                        phase, project_dir,
                        note=f"auto-reconciled (ISS-R3: {expected} exists)",
                    )
                    if allowed:
                        logger.info(
                            f"[REQUIREMENTS TOOL] ISS-R3: Auto-completed phase {seq} "
                            f"(deliverable {expected} found)"
                        )
                    else:
                        logger.info(
                            f"[REQUIREMENTS TOOL] ISS-R3: Phase {seq} completion BLOCKED "
                            f"by ADR-089: {reason}"
                        )
                    found = True
                    break

            # ISS-5: If no fixed-path file found, check alt_globs as fallback
            if not found:
                alt_globs = PHASE_ALT_GLOBS.get(seq, [])
                for pattern in alt_globs:
                    matches = glob.glob(os.path.join(project_dir, pattern))
                    if matches:
                        matched_file = os.path.relpath(matches[0], project_dir)
                        from python.helpers.requirements_ledger import try_phase_completed
                        allowed, reason = try_phase_completed(
                            phase, project_dir,
                            note=f"auto-reconciled (ISS-5: alt-glob {matched_file} exists)",
                        )
                        if allowed:
                            logger.info(
                                f"[REQUIREMENTS TOOL] ISS-5: Auto-completed phase {seq} "
                                f"(alt-glob deliverable {matched_file} found)"
                            )
                        else:
                            logger.info(
                                f"[REQUIREMENTS TOOL] ISS-5: Phase {seq} completion BLOCKED "
                                f"by ADR-089: {reason}"
                            )
                        break

    # ISS-R3-v2 + ADR-086 Step 3b-3: Downstream inference for phases without
    # specific deliverables. Phase 2.5 (validate) doesn't produce its own
    # file, but if ANY later phase with category 'design' or 'implementation'
    # is completed, then 2.5 must have passed.
    # ADR-086: Uses category-based successor check instead of hardcoded
    # seq list [2.6, 2.7, 3]. Phase numbers are a SKILL construct.
    from python.helpers.requirements_ledger import infer_phase_category as _ipc_25

    seq_to_phase = {str(p.get("seq", "")): p for p in phases}
    phase_25 = seq_to_phase.get("2.5")
    if phase_25 and phase_25.get("status") == "pending":
        phase_25_seq = 2.5
        for p in phases:
            p_seq_str = str(p.get("seq", "0"))
            try:
                p_seq = float(p_seq_str)
            except (ValueError, TypeError):
                try:
                    p_seq = float(p_seq_str.split(".")[0])
                except (ValueError, TypeError, IndexError):
                    continue
            if p_seq <= phase_25_seq:
                continue
            p_category = _ipc_25(p.get("title", ""))
            if p_category in ("design", "implementation") and p.get("status") == "completed":
                from python.helpers.requirements_ledger import try_phase_completed
                allowed, reason = try_phase_completed(
                    phase_25, project_dir,
                    note=(
                        f"auto-reconciled (ISS-R3-v2 + ADR-086: downstream "
                        f"{p_category} phase {p_seq_str} completed)"
                    ),
                )
                if allowed:
                    logger.info(
                        f"[REQUIREMENTS TOOL] ISS-R3-v2: Auto-completed phase 2.5 "
                        f"(downstream {p_category} phase {p_seq_str} is completed)"
                    )
                else:
                    logger.info(
                        f"[REQUIREMENTS TOOL] ISS-R3-v2: Phase 2.5 completion "
                        f"BLOCKED by ADR-089: {reason}"
                    )
                break

    # SS-4 (Phase 2.5): Type Coherence Check — Schema Lock.
    #
    # Cross-references entity names in decomposition_index.json against
    # Prisma schema and architecture-spec to detect type drift BEFORE code
    # is written. Advisory only (logs warnings, doesn't hard-block).
    # Upgrade to blocking after stability proven.
    try:
        from python.helpers.type_coherence import check_type_coherence
        tc_result = check_type_coherence(project_dir)
        if tc_result.get("warnings"):
            for w in tc_result["warnings"]:
                logger.warning(f"[REQUIREMENTS TOOL] SS-4 TYPE COHERENCE: {w}")
            # Store on phase 2.5 for downstream inspection
            if phase_25:
                phase_25["_type_coherence"] = {
                    "pass": tc_result.get("pass", True),
                    "warning_count": len(tc_result["warnings"]),
                    "canonical_types": tc_result.get("canonical_types", []),
                    "conflicts": tc_result.get("conflicts", []),
                }
        elif tc_result.get("canonical_types"):
            logger.info(
                f"[REQUIREMENTS TOOL] SS-4 TYPE COHERENCE: PASS. "
                f"Canonical types: {tc_result['canonical_types']}"
            )
    except Exception as tc_err:
        logger.debug(f"[REQUIREMENTS TOOL] SS-4: Type coherence check skipped: {tc_err}")

    # ITR-14 ISS-2 ROOT CAUSE FIX: BDD Phase 2 gate.
    #
    # PRIMARY: Architect uses save_bdd_scenarios tool → structured input
    #   → tool validates REQ-IDs, checks coverage, stores _bdd_validation
    #   → gate reads _bdd_validation here
    #
    # FALLBACK: If architect used save_deliverable (old path) or write_to_file,
    #   enforce_bdd_req_traceability() runs as heuristic safety net
    phase_2 = seq_to_phase.get("2")
    if phase_2 and phase_2.get("status") == "completed":
        try:
            from python.helpers.skeleton_generator import (
                enforce_bdd_req_traceability,
                validate_bdd_literals,
                validate_bdd_behavioral_consistency,
                validate_bdd_conditional_completeness,
            )
            # L1: BDD literal cross-check (catches $199 vs $200)
            bdd_mismatches = validate_bdd_literals(project_dir)
            if bdd_mismatches:
                logger.warning(
                    f"[BDD QUALITY] {len(bdd_mismatches)} literal mismatches "
                    f"in BDD: {[m.get('bdd_value', '?') for m in bdd_mismatches]}"
                )
                phase_2["_bdd_literal_mismatches"] = bdd_mismatches

            # F-3 (ITR-22): BDD behavioral consistency — manifest routing
            bdd_routing_mismatches = validate_bdd_behavioral_consistency(project_dir)
            if bdd_routing_mismatches:
                logger.warning(
                    f"[BDD BEHAVIORAL] {len(bdd_routing_mismatches)} routing "
                    f"inversions: {[m['scenario'] for m in bdd_routing_mismatches]}"
                )
                phase_2["_bdd_routing_mismatches"] = bdd_routing_mismatches

            # ITR-32 F-5: BDD conditional completeness — all branches covered
            bdd_completeness_gaps = validate_bdd_conditional_completeness(project_dir)
            if bdd_completeness_gaps:
                logger.warning(
                    f"[BDD COMPLETENESS] {len(bdd_completeness_gaps)} conditional "
                    f"branches not covered by BDD: "
                    f"{[g['missing_branch'] for g in bdd_completeness_gaps]}"
                )
                phase_2["_bdd_completeness_gaps"] = bdd_completeness_gaps

            # FIX-1b (RCA-ITR49 SS-1b): Manifest↔BDD literal consistency check
            # Root cause: check_bdd_literal_consistency in bdd_literal_checker.py
            # was built and tested but had ZERO production callers — same bug
            # class as inject_contract_assertions (SS-1).
            try:
                from python.helpers.bdd_literal_checker import check_bdd_literal_consistency
                # RCA-461: Use canonical path. Was hardcoded to project root,
                # but manifest lives at docs/content-manifest.json.
                manifest_path = _planning_path(project_dir, "content_manifest")
                bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
                literal_result = check_bdd_literal_consistency(manifest_path, bdd_path)
                if not literal_result.get("consistent", True):
                    mismatches = literal_result.get("mismatches", [])
                    logger.warning(
                        f"[BDD LITERAL] {len(mismatches)} manifest↔BDD literal "
                        f"mismatches: {[m.get('field', '?') for m in mismatches]}"
                    )
                    phase_2["_bdd_literal_consistency_mismatches"] = mismatches

                    # F-9 (RCA-470): Auto-correct BDD price literals using
                    # manifest as source of truth. bdd_literals.py was built
                    # and tested but had ZERO production callers.
                    try:
                        from python.helpers.bdd_literals import auto_correct_bdd_literals
                        corrections = auto_correct_bdd_literals(project_dir)
                        if corrections:
                            logger.info(
                                f"[BDD AUTOCORRECT] Fixed {len(corrections)} price "
                                f"mismatches: {[(c['old_price'], c['new_price']) for c in corrections]}"
                            )
                            phase_2["_bdd_literal_autocorrections"] = corrections
                    except Exception as ac_err:
                        logger.debug(f"[F-9] BDD auto-correction skipped: {ac_err}")
            except Exception as e:
                logger.debug(f"[FIX-1b] BDD literal consistency check skipped: {e}")


            # Fix 3.2: Content fidelity cross-check — catch content gaps BEFORE implementation
            try:
                from python.helpers.bdd_generator_validation import check_bdd_content_coverage
                bdd_text = ""
                bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
                if os.path.isfile(bdd_path):
                    with open(bdd_path, "r") as f:
                        bdd_text = f.read()
                if bdd_text:
                    # Get requirements from ledger — filter for content categories
                    ledger = (agent_data or {}).get("_requirements_ledger", {})
                    ledger_reqs = ledger.get("requirements", [])
                    content_reqs = [
                        # Transform ledger format (id) → check_bdd_content_coverage format (req_id)
                        {"req_id": r.get("id", ""), "text": r.get("text", "")}
                        for r in ledger_reqs
                        if r.get("category", "").lower() in {"content", "copy", "branding", "pricing"}
                    ]
                    if content_reqs:
                        coverage_result = check_bdd_content_coverage(content_reqs, bdd_text)
                        gaps = coverage_result.get("gaps", [])
                        if gaps:
                            logger.warning(
                                f"[BDD CONTENT FIDELITY] {len(gaps)} content requirement(s) "
                                f"not covered in BDD scenarios: {gaps[:5]}"
                            )
                            phase_2["_bdd_content_fidelity_gaps"] = gaps
            except Exception as e:
                logger.debug(f"[Fix 3.2] Content fidelity cross-check skipped: {e}")

            # L1: BDD coverage gate
            # ISS-7 FIX: Try reading persisted validation from disk FIRST.
            # save_bdd_scenarios now writes .bdd_validation.json alongside
            # bdd-scenarios.md. This solves the agent_data inaccessibility
            # problem (reconciler runs during save_manifest, no agent_data).
            bdd_validation = None
            validation_json_path = os.path.join(
                project_dir, "docs", ".bdd_validation.json"
            )
            if os.path.isfile(validation_json_path):
                try:
                    with open(validation_json_path, "r", encoding="utf-8") as vf:
                        bdd_validation = json.load(vf)
                    logger.info(
                        f"[BDD GATE] Read persisted validation from "
                        f"{validation_json_path}: "
                        f"coverage={bdd_validation.get('coverage', 0):.1%}"
                    )
                except (json.JSONDecodeError, IOError, OSError) as read_err:
                    logger.debug(
                        f"[BDD GATE] Could not read .bdd_validation.json: {read_err}"
                    )
                    bdd_validation = None

            # Fallback: If no persisted validation, detect and re-validate
            if bdd_validation is None:
                bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
                if os.path.isfile(bdd_path):
                    with open(bdd_path) as bf:
                        bdd_text = bf.read()
                    # Structured tool outputs "Feature:" on first line (no markdown #)
                    used_structured_tool = bdd_text.strip().startswith("Feature:")
                else:
                    used_structured_tool = False

                if not used_structured_tool:
                    # FALLBACK: Architect used old path — run heuristic enforcement
                    enforcement = enforce_bdd_req_traceability(project_dir)
                    if enforcement.get("enforced"):
                        cov = enforcement.get("coverage", {})
                        if enforcement.get("injected_count", 0) > 0:
                            logger.info(
                                f"[BDD ENFORCE] Fallback: auto-injected "
                                f"{enforcement['injected_count']} REQ-IDs"
                            )
                        bdd_validation = cov
                else:
                    # Structured tool was used — read BDD and check coverage
                    skeleton_path = os.path.join(project_dir, "docs", "test-skeleton.json")
                    if os.path.isfile(skeleton_path):
                        from python.helpers.skeleton_generator import check_bdd_coverage
                        with open(skeleton_path) as sf:
                            skeleton_data = json.load(sf)
                        skeleton_reqs = skeleton_data.get("requirements", [])
                        bdd_validation = check_bdd_coverage(skeleton_reqs, bdd_text)

            if bdd_validation:
                if not bdd_validation.get("pass", True):
                    phase_2["status"] = "pending"
                    from python.helpers.gate_config import BDD_COVERAGE_THRESHOLD
                    phase_2["note"] = (
                        f"BDD COVERAGE GATE BLOCKED: "
                        f"{bdd_validation.get('covered', 0)}/{bdd_validation.get('total_bdd_needed', 0)} "
                        f"= {bdd_validation.get('coverage', 0):.1%} < {BDD_COVERAGE_THRESHOLD:.0%}. "
                        f"Use requirements(action='save_bdd_scenarios') with all REQ-IDs."
                    )
                    logger.warning(
                        f"[BDD GATE] Phase 2 BLOCKED — coverage "
                        f"{bdd_validation.get('coverage', 0):.1%} < {BDD_COVERAGE_THRESHOLD:.0%}"
                    )
                else:
                    logger.info(
                        f"[BDD GATE] Phase 2 PASSED — coverage "
                        f"{bdd_validation.get('covered', 0)}/{bdd_validation.get('total_bdd_needed', 0)} "
                        f"= {bdd_validation.get('coverage', 0):.1%}"
                    )
                    # ITR-19: Generate TDD stubs here (post-scaffold, language
                    # detection will work correctly now)
                    try:
                        from python.helpers.skeleton_generator import (
                            generate_tdd_tests,
                            generate_test_skeleton,
                        )
                        from python.helpers.tdd_generator_creation import MissingBDDException

                        # RCA-461 R-1: Regenerate test skeleton BEFORE TDD generation.
                        # At Phase 0 (init), the manifest didn't exist yet so
                        # expected_literals were empty. Now at Phase 2.7 the
                        # manifest exists on disk — re-generation populates
                        # expected_literals from category-based manifest mapping.
                        try:
                            generate_test_skeleton(project_dir)
                            logger.info(
                                "[BDD GATE] R-1: Regenerated test skeleton with "
                                "manifest values before TDD generation"
                            )
                        except Exception as regen_err:
                            logger.warning(
                                f"[BDD GATE] R-1: Skeleton re-gen failed (non-fatal): "
                                f"{regen_err}"
                            )

                        try:
                            tdd_results = generate_tdd_tests(project_dir)
                        except MissingBDDException as e:
                            # RCA-366: Block Phase 2 and force a retry if BDD is completely missing
                            phase_2["status"] = "pending"
                            phase_2["note"] = f"TDD GENERATOR BLOCKED: {str(e)}. You must save valid BDD scenarios for this requirement."
                            logger.warning(f"[BDD GATE] Phase 2 BLOCKED by Missing BDD: {str(e)}")
                            return phases
                            
                        if tdd_results:
                            logger.info(
                                f"[BDD GATE] ITR-19: Auto-generated {len(tdd_results)} "
                                f"TDD test modules (post-scaffold, correct language)"
                            )
                            # ITR-33 FIX-B: Capture RED baseline immediately after
                            # TDD stubs are written. This records which tests FAIL
                            # before implementation starts, enabling the gate to
                            # verify the RED→GREEN transition at completion time.
                            try:
                                from python.helpers.tdd_red_green_validator import (
                                    capture_red_baseline,
                                    validate_red_baseline_quality,
                                )
                                baseline = capture_red_baseline(project_dir, timeout=30)
                                if baseline:
                                    valid, msg = validate_red_baseline_quality(baseline)
                                    if valid:
                                        logger.info(
                                            f"[BDD GATE] ITR-33: RED baseline captured — "
                                            f"{baseline.get('failed', 0)}/{baseline.get('total', 0)} tests fail "
                                            f"(red_ratio={baseline.get('red_ratio', 0):.1%})"
                                        )
                                    else:
                                        logger.warning(
                                            f"[BDD GATE] ITR-33: RED baseline INVALID — {msg}. "
                                            f"Tests may be garbage stubs (auto-pass)."
                                        )
                                else:
                                    logger.info(
                                        f"[BDD GATE] ITR-33: RED baseline skipped — "
                                        f"no test runner found or no tests to run"
                                    )
                            except Exception as rb_err:
                                logger.warning(
                                    f"[BDD GATE] ITR-33: RED baseline capture failed (non-fatal): {rb_err}"
                                )
                    except Exception as tdd_err:
                        # ITR-22 FIX: Surface error in return message so existing
                        # _12_tool_failure_tracker sees it and triggers retry/escalation.
                        # Previously: logger.debug("non-fatal") — invisible to agent.
                        logger.warning(f"[BDD GATE] TDD stub generation FAILED: {tdd_err}")
                        _reconciler_warnings.append(
                            f"⚠️ TDD stub generation FAILED: {tdd_err}. "
                            f"Retry by calling requirements(action='check_coverage')."
                        )
        except Exception as bdd_err:
            # ITR-22 FIX: Surface error in return message so existing
            # _12_tool_failure_tracker sees it and triggers retry/escalation.
            logger.warning(f"[BDD QUALITY] Validation FAILED: {bdd_err}")
            _reconciler_warnings.append(
                f"⚠️ BDD validation FAILED: {bdd_err}. "
                f"Fix BDD scenarios and re-save."
            )


    # RCA-361: Implementation phase reconciliation via req_guid cross-reference.
    # Implementation phases (3.x) don't have predictable deliverable files.
    # Instead, check if ALL req_guids linked to the phase are resolved in the
    # requirements_ledger.json. If yes, mark the phase as completed.
    ledger_path = _planning_path(project_dir, "requirements_ledger")
    if os.path.isfile(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger_data = json.load(f)
            # Build a lookup of requirement statuses
            req_status_map = {}
            for req in ledger_data.get("requirements", []):
                req_status_map[req.get("id", "")] = req.get("status", "pending")

            RESOLVED_STATUSES = {"completed", "verified", "done", "complete"}

            # RCA-400 F-4: Detect planning-only mode ONCE before loop.
            # In planning-only runs, requirements get marked 'completed'
            # during BDD/manifest creation — NOT because code was written.
            # Phase 3+ must NOT be auto-completed based on req_guid
            # resolution alone when in planning-only mode.
            is_planning_only = False
            project_json_path = os.path.join(project_dir, "project.json")
            if os.path.isfile(project_json_path):
                try:
                    with open(project_json_path, "r", encoding="utf-8") as pf:
                        pdata = json.load(pf)
                    is_planning_only = bool(pdata.get("planning_only", False))
                except (json.JSONDecodeError, IOError, OSError):
                    pass  # Default to not planning-only

            for phase in phases:
                if phase.get("status") != "pending":
                    continue
                req_guids = phase.get("req_guids", [])
                if not req_guids:
                    continue

                # ADR-086 Step 3b-1 + Phase 4: Replace RCA-ITR51 band-aid
                # (hardcoded seq_num >= 3.0) with categorical detection.
                # For implementation phases, check the code STAGE specifically
                # instead of overall status — prevents false auto-completion
                # when BDD/TDD marked reqs as 'completed' before any code.
                from python.helpers.requirements_ledger import (
                    infer_phase_category,
                    get_stage_status as _get_stage_status,
                )

                phase_category = infer_phase_category(phase.get("title", ""))

                # Implementation phases require delegation evidence for auto-
                # completion — req_guid resolution alone is not sufficient
                # because BDD/TDD writers mark reqs 'completed' before code.
                # The delegation-based reconciler (RCA-345 FIX-4, below)
                # handles implementation phase auto-close via actual evidence.
                # ITR-54 Fix-2b: Also treat 'unknown' as implementation
                # (conservative default). Planning/research/design/verification/
                # deployment all have well-defined keywords; unknown = likely
                # implementation and must require delegation evidence.
                if phase_category in ("implementation", "unknown"):
                    # ADR-086 Phase 4: Check code stage specifically
                    # Build stage-aware status map from ledger
                    all_resolved = all(
                        _get_stage_status(
                            next((r for r in ledger_data.get("requirements", [])
                                  if r.get("id") == guid), {}),
                            "code"
                        ) in RESOLVED_STATUSES
                        for guid in req_guids
                    )
                    if not all_resolved:
                        logger.info(
                            f"[REQUIREMENTS TOOL] ADR-086: Skipping phase "
                            f"{phase.get('seq', '?')} ({phase_category}) — "
                            f"code stage not resolved for all {len(req_guids)} req_guids"
                        )
                        continue
                else:
                    # Non-implementation phases: use overall status as before
                    # RCA-400 F-4: Infrastructure phases skip in planning-only
                    if is_planning_only and phase_category in ("planning", "research"):
                        seq_str = str(phase.get("seq", "0"))
                        try:
                            seq_num = float(seq_str)
                        except (ValueError, TypeError):
                            try:
                                seq_num = float(seq_str.split(".")[0])
                            except (ValueError, TypeError, IndexError):
                                seq_num = 0.0
                        if seq_num >= 1.0 and seq_num < 2.0:
                            logger.info(
                                f"[REQUIREMENTS TOOL] RCA-400 F-4 + ITR-30: Skipping phase {seq_str} "
                                f"in planning-only mode (infrastructure phase, {len(req_guids)} req_guids)"
                            )
                            continue

                    all_resolved = all(
                        req_status_map.get(guid, "pending") in RESOLVED_STATUSES
                        for guid in req_guids
                    )

                if all_resolved:
                    from python.helpers.requirements_ledger import try_phase_completed
                    allowed, reason = try_phase_completed(
                        phase, project_dir,
                        note=f"auto-reconciled (RCA-361: all {len(req_guids)} req_guids resolved in ledger)",
                    )
                    if allowed:
                        logger.info(
                            f"[REQUIREMENTS TOOL] RCA-361: Auto-completed phase {phase.get('seq', '?')} "
                            f"(all {len(req_guids)} req_guids resolved in ledger)"
                        )
                    else:
                        logger.info(
                            f"[REQUIREMENTS TOOL] RCA-361: Phase {phase.get('seq', '?')} completion "
                            f"BLOCKED by ADR-089: {reason}"
                        )
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.debug(f"[REQUIREMENTS TOOL] RCA-361: Could not read ledger for reconciliation: {e}")

    # ── RCA-345 FIX-4: Delegation-Based Phase Auto-Close (Defense-in-Depth) ──
    # After all existing reconciliation checks, if any phases are STILL pending,
    # check the cumulative delegation_result_ledger. If a completed delegation
    # exists for a phase's seq, auto-close it.
    #
    # This is a DEFENSE-IN-DEPTH fallback. FIX-2 (deterministic phase completion
    # in call_subordinate) should handle the primary case. This reconciler
    # catches edge cases where FIX-2 missed (e.g., phase_seq mismatch, race
    # conditions, or Docker restarts that lose agent_data but preserved the
    # ledger on disk).
    try:
        from python.helpers.phase_parser import parse_phase_seq, _normalize_seq_for_match

        # Source 1: agent_data (in-memory, preferred)
        result_ledger = None
        if agent_data and isinstance(agent_data, dict):
            result_ledger = agent_data.get("_delegation_result_ledger")

        # Source 2: disk fallback (survives Docker restarts / context condensation)
        if not result_ledger:
            disk_ledger_path = os.path.join(
                project_dir, ".agix.proj", "delegation_result_ledger.json"
            )
            if os.path.isfile(disk_ledger_path):
                try:
                    with open(disk_ledger_path, "r", encoding="utf-8") as dlf:
                        disk_data = json.load(dlf)
                    if isinstance(disk_data, list):
                        result_ledger = disk_data
                    elif isinstance(disk_data, dict):
                        result_ledger = disk_data.get("entries", [])
                except (json.JSONDecodeError, IOError, OSError) as disk_err:
                    logger.debug(
                        f"[REQUIREMENTS TOOL] RCA-345 FIX-4: Could not read disk ledger: {disk_err}"
                    )

        if result_ledger and isinstance(result_ledger, list):
            # Build a map of the LATEST delegation entry per phase_seq.
            # F-8: Use canonical delegation status set for "done" detection.
            # F-11 (RCA-EVAL-1): Also track FAILED delegations so phases
            # get marked "failed" instead of staying "pending" forever.
            # The LATEST entry wins — if a phase failed then succeeded,
            # the success takes precedence (and vice versa).
            from python.helpers.status_constants import DELEGATION_DONE_STATUSES as COMPLETED_STATUSES
            FAILED_STATUSES = frozenset({"failed", "error", "cancelled", "blocked"})

            # Map: normalized_seq -> latest ledger entry (keeps last occurrence)
            latest_delegation_by_seq: dict[str, dict] = {}

            for entry in result_ledger:
                if not isinstance(entry, dict):
                    continue
                entry_status = entry.get("status", "")
                entry_phase_seq = entry.get("phase_seq", "")
                if not entry_phase_seq:
                    continue
                # Only track entries with known done or failed statuses
                if entry_status not in COMPLETED_STATUSES and entry_status not in FAILED_STATUSES:
                    continue
                norm = _normalize_seq_for_match(entry_phase_seq)
                # Keep the LATEST entry (list order = chronological)
                latest_delegation_by_seq[norm] = entry

            if latest_delegation_by_seq:
                auto_reconciled_count = 0
                auto_failed_count = 0
                for phase in phases:
                    if phase.get("status") != "pending":
                        continue
                    phase_seq_raw = str(phase.get("seq", ""))
                    if not phase_seq_raw:
                        continue

                    phase_norm = _normalize_seq_for_match(phase_seq_raw)
                    phase_tuple = parse_phase_seq(phase_seq_raw)

                    # Match 1: normalized string match
                    matched_entry = latest_delegation_by_seq.get(phase_norm)

                    # Match 2: tuple comparison fallback (handles semver variants)
                    if not matched_entry:
                        for norm_key, entry in latest_delegation_by_seq.items():
                            entry_tuple = parse_phase_seq(entry.get("phase_seq", ""))
                            if entry_tuple == phase_tuple and entry_tuple != (0, 0, 0):
                                matched_entry = entry
                                break

                    if matched_entry:
                        matched_status = matched_entry.get("status", "")
                        if matched_status in COMPLETED_STATUSES:
                            # ITR-55 FIX: Implementation phases must only be
                            # auto-completed by CODE profile delegations.
                            # Design/research/architect delegations share the
                            # same phase seq (e.g. 3.1.0) but are planning
                            # work, not implementation. Without this guard,
                            # an architect delegation for "Phase 3.1.0 Design"
                            # falsely closes "Phase 3.1.0 Implementation".
                            delegation_profile = matched_entry.get("profile", "")
                            phase_title = phase.get("title", "")
                            from python.helpers.requirements_ledger import infer_phase_category
                            p_category = infer_phase_category(phase_title)
                            if p_category == "implementation":
                                CODE_PROFILES = {"code", "coder", "developer"}
                                if delegation_profile not in CODE_PROFILES:
                                    logger.info(
                                        f"[REQUIREMENTS TOOL] ITR-55 FIX: Skipping auto-complete "
                                        f"of implementation phase {phase_seq_raw} — delegation "
                                        f"profile='{delegation_profile}' is not a code profile "
                                        f"(title='{phase_title[:60]}')"
                                    )
                                    continue

                            from python.helpers.requirements_ledger import try_phase_completed
                            allowed, reason = try_phase_completed(
                                phase, project_dir,
                                note=(
                                    f"auto-reconciled (delegation-based): "
                                    f"delegation status={matched_status}, "
                                    f"profile={matched_entry.get('profile', '?')}"
                                ),
                            )
                            if allowed:
                                auto_reconciled_count += 1
                                logger.info(
                                    f"[REQUIREMENTS TOOL] RCA-345 FIX-4: Auto-completed phase "
                                    f"{phase_seq_raw} via delegation ledger "
                                    f"(delegation status={matched_status}, "
                                    f"profile={matched_entry.get('profile', '?')})"
                                )
                            else:
                                logger.info(
                                    f"[REQUIREMENTS TOOL] RCA-345 FIX-4: Phase {phase_seq_raw} "
                                    f"completion BLOCKED by ADR-089: {reason}"
                                )
                        elif matched_status in FAILED_STATUSES:
                            # F-11: Mark phase as "failed" instead of leaving
                            # it "pending" forever. This gives accurate status
                            # reporting and prevents infinite retry loops.
                            phase["status"] = "failed"
                            phase["note"] = phase.get("note", "") or (
                                f"auto-reconciled (delegation-failed): "
                                f"delegation status={matched_status}, "
                                f"profile={matched_entry.get('profile', '?')}"
                            )
                            auto_failed_count += 1
                            logger.warning(
                                f"[REQUIREMENTS TOOL] F-11: Phase {phase_seq_raw} marked "
                                f"'failed' via delegation ledger "
                                f"(delegation status={matched_status}, "
                                f"profile={matched_entry.get('profile', '?')})"
                            )

                if auto_reconciled_count > 0:
                    logger.info(
                        f"[REQUIREMENTS TOOL] RCA-345 FIX-4: Delegation-based reconciler "
                        f"auto-completed {auto_reconciled_count} phase(s)"
                    )
                if auto_failed_count > 0:
                    logger.warning(
                        f"[REQUIREMENTS TOOL] F-11: Delegation-based reconciler "
                        f"marked {auto_failed_count} phase(s) as 'failed'"
                    )
    except Exception as fix4_err:
        logger.debug(
            f"[REQUIREMENTS TOOL] RCA-345 FIX-4: Delegation-based reconciliation "
            f"failed (non-fatal): {fix4_err}"
        )

def _preserve_ledger_progress(new_ledger: dict, filepath: str) -> None:
    """Merge completed/assigned statuses from existing disk file into new ledger.

    RCA-310 FIX-3: When the orchestrator runs multiple waves, the in-memory
    ledger may be re-initialized with all-pending statuses. Before overwriting
    the disk file, check if any requirements were previously marked as
    completed/assigned and preserve those statuses.

    Mutates new_ledger in-place.
    """
    if not os.path.exists(filepath):
        return

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    existing_reqs = existing.get("requirements", [])
    if not existing_reqs:
        return

    # Build lookup: req_id -> status from disk
    disk_statuses = {}
    for r in existing_reqs:
        req_id = r.get("id", "")
        status = r.get("status", "pending")
        if status in ("completed", "assigned", "verified"):
            disk_statuses[req_id] = status

    if not disk_statuses:
        return

    # Merge: if new ledger has a requirement as "pending" but disk has it
    # as "completed"/"assigned", preserve the disk status.
    merged_count = 0
    new_reqs = new_ledger.get("requirements", [])
    for r in new_reqs:
        req_id = r.get("id", "")
        if req_id in disk_statuses and r.get("status") == "pending":
            r["status"] = disk_statuses[req_id]
            merged_count += 1

    if merged_count > 0:
        logger.info(
            f"[REQUIREMENTS TOOL] RCA-310 FIX-3: Preserved {merged_count} "
            f"completed/assigned statuses from disk (would have been overwritten)"
        )

def _filter_deferred_decomposition_phases(phases: list) -> list:
    """Mark non-immediate timeline phases as 'deferred' to prevent delegation.

    FIX-8 (ITR-32): The architect prompt tells the LLM to tag features with
    timeline: immediate/near-term/future, but the requirements tool that saves
    decomposition_index.json NEVER reads or enforces those tags. Timeline
    classification was pure LLM honor-system.

    This function enforces temporal tags deterministically:
    - Phases with timeline in {'near-term', 'future', 'deferred'} → status='deferred'
    - Phases with timeline='immediate' or no timeline → unchanged
    - Already completed/in_progress/assigned phases → never overwritten

    Mutates phases in-place. Returns the same list reference.
    """
    DEFERRED_TIMELINES = {"near-term", "future", "deferred"}
    # Statuses that should never be overwritten
    PROTECTED_STATUSES = {"completed", "in_progress", "assigned", "skipped"}

    deferred_count = 0
    for phase in phases:
        timeline = phase.get("timeline", "")
        if not timeline or not isinstance(timeline, str):
            continue

        timeline_lower = timeline.strip().lower()
        status = phase.get("status", "pending")

        if timeline_lower in DEFERRED_TIMELINES and status not in PROTECTED_STATUSES:
            phase["status"] = "deferred"
            phase["deferred_reason"] = (
                f"ITR-32 FIX-8: Timeline '{timeline}' is not immediate — "
                f"deferred to future iteration"
            )
            deferred_count += 1
            logger.info(
                f"[REQUIREMENTS TOOL] FIX-8: Phase {phase.get('seq', '?')} "
                f"({phase.get('title', '?')}) marked deferred (timeline={timeline})"
            )

    if deferred_count > 0:
        logger.info(
            f"[REQUIREMENTS TOOL] FIX-8: Deferred {deferred_count} phase(s) "
            f"with non-immediate timeline tags"
        )

    return phases
