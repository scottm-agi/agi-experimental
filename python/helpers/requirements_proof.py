"""
Requirements Proof & Verification

Extracted from requirements_ledger.py during P4 modularization (Phase 1.4).
Contains Layer 1 deterministic proof evidence checks, requirement completion
marking with 2-layer verification, gate-based verification promotion,
E2E verification failure marking, and regression status tracking.

Functions:
    _run_proof_evidence_checks            — L1 deterministic checks on proof files
    mark_requirement_complete             — Mark requirement completed (with proof)
    mark_verified_from_gate_results       — Promote completed → verified
    mark_requirements_verification_failed — Mark reqs as E2E verification failed
    get_verification_failed_requirements  — Get reqs that failed E2E verification
    mark_requirements_regressed           — Mark completed reqs as regressed
    get_regressed_requirements            — Get all regressed requirements
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

from python.helpers.req_id_normalizer import build_normalized_req_map
from python.helpers.requirements_persistence import (
    _ensure_ledger,
    persist_ledger_to_project,
)
from python.helpers.requirements_stage import (
    get_stage_status,
    set_stage_status,
)

logger = logging.getLogger("agix.requirements_ledger")


# ─── L1 Deterministic Proof Evidence Checks ──────────────────────────────
#
# 2-layer completion verification system. Layer 1 runs fast deterministic
# checks on proof files. Layer 2 is the LLM (the caller) which receives
# the structured evidence report and makes the final decision.
#
# Stub indicators: patterns that suggest a file is a placeholder, not real
# implementation. Case-insensitive matching.
_STUB_INDICATORS = [
    "todo",
    "placeholder",
    "lorem",
    "hardcoded",
    "fake data",
    "stub",
    "mock data",
    "sample data",
    "not implemented",
    "fixme",
]

# Patterns for commented-out API calls (line starts with comment marker
# followed by a fetch/request pattern)
_COMMENTED_API_PATTERNS = [
    r"^\s*(?://|#|/\*)\s*(?:fetch|axios|requests?\.|http\.|api\.)",
]

# Minimum line count threshold for a file to be considered "substantial"
_MIN_SUBSTANTIAL_LINES = 25


def _run_proof_evidence_checks(
    proof_files: List[str],
) -> dict:
    """Run Layer 1 deterministic checks on proof evidence files.

    Checks:
    - File existence
    - Content depth (>= _MIN_SUBSTANTIAL_LINES)
    - Stub indicator detection
    - Commented-out API call detection

    Args:
        proof_files: List of file paths to verify.

    Returns:
        Dict with:
        - files_checked: List of per-file check results
        - stub_indicators: List of all stub indicators found across files
        - total_lines: Total line count across all files
        - has_issues: True if any check flagged a concern
    """
    import re as _re

    files_checked = []
    all_stub_indicators = []
    total_lines = 0

    for filepath in proof_files:
        file_report = {"path": filepath}

        # Check 1: File existence
        if not os.path.exists(filepath):
            file_report["missing"] = True
            file_report["lines"] = 0
            file_report["stubs"] = []
            files_checked.append(file_report)
            all_stub_indicators.append(f"FILE_MISSING:{filepath}")
            continue

        file_report["missing"] = False

        # Read file content
        try:
            with open(filepath, "r", errors="replace") as f:
                content = f.read()
        except (IOError, OSError) as e:
            file_report["missing"] = True
            file_report["lines"] = 0
            file_report["stubs"] = [f"READ_ERROR:{e}"]
            file_report["read_error"] = str(e)
            files_checked.append(file_report)
            all_stub_indicators.append(f"READ_ERROR:{filepath}")
            continue

        lines = content.splitlines()
        line_count = len(lines)
        file_report["lines"] = line_count
        total_lines += line_count

        # Check 2: Content depth — is the file substantial?
        if line_count < _MIN_SUBSTANTIAL_LINES:
            file_report["too_small"] = True

        # Check 3: Stub indicator detection
        content_lower = content.lower()
        found_stubs = []
        for indicator in _STUB_INDICATORS:
            if indicator in content_lower:
                found_stubs.append(indicator)

        # Check 4: Commented-out API calls
        for line in lines:
            for pattern in _COMMENTED_API_PATTERNS:
                if _re.search(pattern, line, _re.IGNORECASE):
                    found_stubs.append(f"commented_api:{line.strip()[:60]}")
                    break  # One match per line is enough

        file_report["stubs"] = found_stubs
        all_stub_indicators.extend(found_stubs)
        files_checked.append(file_report)

    # Determine if there are issues
    has_issues = False
    if all_stub_indicators:
        has_issues = True
    # Check if ALL files are too small (not just one)
    if all(
        f.get("too_small") or f.get("missing") for f in files_checked
    ):
        has_issues = True

    return {
        "files_checked": files_checked,
        "stub_indicators": all_stub_indicators,
        "total_lines": total_lines,
        "has_issues": has_issues,
    }


def mark_requirement_complete(
    agent_data: dict,
    req_id: str,
    project_dir: str = None,
    proof_evidence: Optional[List[str]] = None,
    force: bool = False,
) -> "bool | dict":
    """Explicitly mark a single requirement as completed, with optional proof verification.

    This is the CRUD "update" path — called by the orchestrator after a
    boomerang confirms work is done. Separate from mark_delegation_complete
    which updates requirements as a side effect of delegation completion.

    2-Layer Completion Verification:
    - When proof_evidence is provided and force=False, runs L1 deterministic
      checks (file existence, content depth, stub detection). If issues are
      found, returns a structured evidence dict for the LLM (Layer 2) to
      review instead of blindly marking complete.
    - When proof_evidence is None/empty or force=True, behaves exactly as
      before (backward compatible blind setter).

    RCA-362 L1 Fix: Auto-persists to disk after update when project_dir
    is provided, ensuring requirements_ledger.json stays in sync with
    in-memory state. Previously, only agent.data was updated — the file
    on disk stayed at "all pending" creating 3 disconnected tracking systems.

    Args:
        agent_data: The agent.data dict
        req_id: The requirement ID to mark complete (e.g., "REQ-002")
        project_dir: If provided, auto-persist ledger to disk after update
        proof_evidence: Optional list of file paths as proof of implementation.
            When provided, L1 deterministic checks are run before marking.
        force: If True, bypass all proof checks (for circuit breakers, admin
            overrides). Marks "completed" unconditionally.

    Returns:
        - True if the requirement was found and marked "completed"
        - False if the requirement was not found
        - dict with evidence report if proof checks detected issues
          (keys: evidence, recommendation, req_id)
    """
    ledger = _ensure_ledger(agent_data)

    # Find the requirement first
    target_req = None
    for req in ledger.get("requirements", []):
        if req["id"] == req_id:
            target_req = req
            break

    if target_req is None:
        logger.warning(
            f"[REQUIREMENTS LEDGER] mark_requirement_complete: {req_id} not found"
        )
        return False

    # ── L1 Proof Evidence Checks ──────────────────────────────────────
    # Only run when proof_evidence is provided AND force is not set
    if proof_evidence and not force:
        evidence = _run_proof_evidence_checks(proof_evidence)

        if evidence["has_issues"]:
            recommendation = "REJECT"
            logger.info(
                f"[REQUIREMENTS LEDGER] {req_id} proof evidence has issues: "
                f"{len(evidence['stub_indicators'])} indicators, "
                f"{evidence['total_lines']} total lines. "
                f"Returning evidence to caller for L2 judgment."
            )
            return {
                "req_id": req_id,
                "evidence": evidence,
                "recommendation": recommendation,
            }

        # L1 passed — no issues found, proceed to mark complete
        logger.info(
            f"[REQUIREMENTS LEDGER] {req_id} proof evidence L1 passed: "
            f"{evidence['total_lines']} lines, no stub indicators."
        )

    # ── Mark Complete (ADR-086: stage-keyed) ───────────────────────────
    # Fix 8: Track previous_status before overwriting
    target_req["previous_status"] = target_req.get("status", "pending")
    # Backward-compat: when called without proof_evidence (or with force),
    # mark ALL stages as "completed" so compute_overall_status() returns
    # "completed". Setting only "code" leaves bdd/tdd at "pending" for reqs
    # created by add_requirement() which have no stage_status, causing
    # overall status to stay "pending" (minimum-wins rule).
    set_stage_status(target_req, "bdd", "completed")
    set_stage_status(target_req, "tdd", "completed")
    set_stage_status(target_req, "code", "completed")
    # Fix 8: Clear regression_reason on re-complete (keep history)
    target_req.pop("regression_reason", None)
    logger.info(
        f"[REQUIREMENTS LEDGER] {req_id} manually marked completed"
    )
    # RCA-362: Auto-persist to disk to keep file in sync
    if project_dir:
        persist_ledger_to_project(agent_data, project_dir)
    return True


# ── ISSUE-2: Test-Result-as-Proof ─────────────────────────────────────────
# REQ-ID extraction pattern: matches REQ_XXX_NNN or REQ-XXX-NNN in test names.
# Test names use underscores (pytest nodeids), so REQ-FEAT-1 becomes REQ_FEAT_1.
# IMPORTANT: Case-sensitive match on [A-Z0-9] segments — stops at lowercase test
# description (e.g., REQ_FEAT_1_login_works → captures only REQ_FEAT_1).
_REQ_ID_IN_TEST = re.compile(r"(REQ(?:[_-][A-Z0-9]+)+)")


def promote_test_results_to_ledger(
    agent_data: dict,
    test_output: str,
) -> tuple:
    """Promote requirements based on test results (ISSUE-2: test IS the proof).

    Scans pytest/node test output for lines containing REQ-IDs. For each
    requirement found:
      - PASSED test → set_stage_status(req, "code", "completed")
      - FAILED test → set_stage_status(req, "code", "failed")

    Only updates requirements whose current code stage is "delegation_returned"
    (i.e., they were bulk-synced but not yet proven by tests).

    Args:
        agent_data: The agent.data dict containing the requirements ledger.
        test_output: Raw test runner stdout+stderr (pytest or node format).

    Returns:
        Tuple of (promoted_count, failed_count).
    """
    ledger = _ensure_ledger(agent_data)
    requirements = ledger.get("requirements", [])

    if not requirements or not test_output:
        return (0, 0)

    # Build lookup: normalize REQ-IDs → requirement dicts
    # Support both REQ-FEAT-1 and REQ_FEAT_1 formats
    req_map = {}
    for req in requirements:
        rid = req.get("id", "")
        if rid:
            # Store under both dash and underscore variants
            req_map[rid.upper()] = req
            req_map[rid.upper().replace("-", "_")] = req

    # Parse test output line by line
    # Format: "test_name PASSED" or "test_name FAILED" or pytest verbose
    passed_reqs = set()
    failed_reqs = set()

    for line in test_output.splitlines():
        line_upper = line.strip().upper()
        if not line_upper:
            continue

        # Extract REQ-IDs from the line
        matches = _REQ_ID_IN_TEST.findall(line)
        if not matches:
            continue

        # Determine pass/fail from the line
        is_pass = "PASSED" in line_upper or " PASS " in line_upper
        is_fail = "FAILED" in line_upper or " FAIL " in line_upper or "ERROR" in line_upper

        if not (is_pass or is_fail):
            continue

        for raw_id in matches:
            # Normalize: underscores → dashes for lookup
            normalized = raw_id.upper().replace("_", "-")
            if normalized in req_map or raw_id.upper() in req_map:
                if is_fail:
                    failed_reqs.add(normalized)
                elif is_pass:
                    passed_reqs.add(normalized)

    # Failed takes priority over passed (if a REQ appears in both)
    passed_reqs -= failed_reqs

    promoted = 0
    failed = 0

    for rid_normalized in passed_reqs:
        req = req_map.get(rid_normalized) or req_map.get(
            rid_normalized.replace("-", "_")
        )
        if req:
            # GAP-2 fix: Update tdd stage when tests pass for a requirement
            tdd_stage = get_stage_status(req, "tdd")
            if tdd_stage not in ("completed", "verified"):
                set_stage_status(req, "tdd", "completed")
                logger.info(
                    f"[REQUIREMENTS PROOF] {req['id']} tdd stage → completed "
                    f"(test passed)"
                )
            # Existing: Update code stage
            if req.get("stage_status", {}).get("code") == "delegation_returned":
                set_stage_status(req, "code", "completed")
                promoted += 1
                logger.info(
                    f"[REQUIREMENTS PROOF] {req['id']} promoted: "
                    f"delegation_returned → completed (test passed)"
                )

    for rid_normalized in failed_reqs:
        req = req_map.get(rid_normalized) or req_map.get(
            rid_normalized.replace("-", "_")
        )
        if req:
            # GAP-2 fix: Update tdd stage when tests fail for a requirement
            tdd_stage = get_stage_status(req, "tdd")
            if tdd_stage != "verified":
                set_stage_status(req, "tdd", "failed")
                logger.info(
                    f"[REQUIREMENTS PROOF] {req['id']} tdd stage → failed "
                    f"(test failed)"
                )
            # Existing: Update code stage
            if req.get("stage_status", {}).get("code") == "delegation_returned":
                set_stage_status(req, "code", "failed")
                failed += 1
                logger.info(
                    f"[REQUIREMENTS PROOF] {req['id']} marked failed: "
                    f"delegation_returned → failed (test failed)"
                )

    if promoted or failed:
        logger.info(
            f"[REQUIREMENTS PROOF] Test-result-as-proof: "
            f"{promoted} promoted, {failed} failed"
        )

    return (promoted, failed)

def mark_verified_from_gate_results(agent_data: dict) -> int:
    """Promote completed requirements to verified when gate checks pass.

    Closes the verification loop: when all gate checks pass for a
    requirement's linked delegations, the requirement transitions from
    'completed' to 'verified'. This lets the orchestrator distinguish
    "finished" from "proven correct."

    Conditions for promotion:
    1. Requirement status must be 'completed'
    2. All linked delegation IDs must have status 'completed' in the ledger
    3. No gate_failures entries must reference any of the linked delegation IDs

    Args:
        agent_data: The agent.data dict

    Returns:
        Number of requirements promoted from completed → verified
    """
    ledger = _ensure_ledger(agent_data)
    requirements = ledger.get("requirements", [])
    delegations = ledger.get("delegations", [])
    gate_failures = ledger.get("gate_failures", [])

    # Build set of delegation IDs that have gate failures
    failed_delegation_ids = set()
    for failure in gate_failures:
        for did in failure.get("affected_delegation_ids", []):
            failed_delegation_ids.add(did)

    # Build lookup of delegation statuses
    delegation_status = {d["id"]: d.get("status", "") for d in delegations}

    promoted = 0
    for req in requirements:
        if req.get("status") != "completed":
            continue

        assigned_ids = req.get("assigned_to", [])

        # Check if ANY linked delegation has a gate failure
        if any(did in failed_delegation_ids for did in assigned_ids):
            continue

        # Check if ALL linked delegations are completed
        # (requirements with no delegations assigned can still be promoted
        # since they were marked completed directly)
        all_delegations_done = all(
            delegation_status.get(did) == "completed"
            for did in assigned_ids
        )

        if all_delegations_done:
            # Promote all stages to verified so compute_overall_status returns "verified".
            set_stage_status(req, "bdd", "verified")
            set_stage_status(req, "tdd", "verified")
            set_stage_status(req, "code", "verified")
            promoted += 1
            logger.info(
                f"[REQUIREMENTS LEDGER] {req['id']} promoted: "
                f"completed → verified (gate verification passed)"
            )


    if promoted:
        logger.info(
            f"[REQUIREMENTS LEDGER] Verification loop closed: "
            f"{promoted} requirements promoted to verified"
        )

    return promoted


def mark_requirements_verification_failed(
    agent_data: dict,
    failed_req_ids: list,
    failure_reasons: dict,
) -> int:
    """Mark requirements as verification_failed when E2E reports failures.

    F-11 Fix: The INVERSE of mark_verified_from_gate_results(). When E2E
    verification detects that a 'completed' requirement actually fails in
    practice, this sets verification_status='failed' WITHOUT changing the
    primary status field. This separates "what the code agent says it did"
    from "what E2E verification confirmed."

    Idempotent: already-failed requirements are skipped (no double-fail).

    Args:
        agent_data: The agent.data dict
        failed_req_ids: List of requirement IDs that failed E2E verification
        failure_reasons: Dict mapping req_id → failure reason string.
            Missing keys get a default reason.

    Returns:
        Number of requirements newly marked as verification_failed
    """
    if not failed_req_ids:
        return 0

    ledger = _ensure_ledger(agent_data)
    requirements = ledger.get("requirements", [])

    # Build lookup for fast access
    req_map = {r["id"]: r for r in requirements}

    marked = 0
    for req_id in failed_req_ids:
        if req_id not in req_map:
            logger.warning(
                f"[REQUIREMENTS LEDGER] mark_verification_failed: "
                f"{req_id} not found in ledger — skipping"
            )
            continue

        req = req_map[req_id]

        # Idempotent: skip if already marked as verification_failed
        if req.get("verification_status") == "failed":
            logger.info(
                f"[REQUIREMENTS LEDGER] {req_id} already verification_failed "
                f"— skipping (idempotent)"
            )
            continue

        # Set verification_status (separate from primary status)
        reason = failure_reasons.get(req_id, "E2E verification failed (no details provided)")
        req["verification_status"] = "failed"
        req["verification_failure_reason"] = reason
        marked += 1

        logger.warning(
            f"[REQUIREMENTS LEDGER] {req_id} verification FAILED: {reason[:100]}"
        )

    if marked:
        logger.warning(
            f"[REQUIREMENTS LEDGER] E2E verification: "
            f"{marked} requirement(s) marked as verification_failed"
        )

    return marked


def get_verification_failed_requirements(agent_data: dict) -> list:
    """Retrieve all requirements with verification_status='failed'.

    F-11: Returns requirements that passed code completion but failed
    E2E verification. Used by the orchestrator to identify what needs
    to be re-delegated or fixed.

    Args:
        agent_data: The agent.data dict

    Returns:
        List of requirement dicts with verification_status='failed'
    """
    ledger = _ensure_ledger(agent_data)
    requirements = ledger.get("requirements", [])

    return [
        req for req in requirements
        if req.get("verification_status") == "failed"
    ]


# ─── Regression Status ──────────────────────────────────────────────────


def mark_requirements_regressed(
    agent_data: dict,
    req_ids: list,
    reason: str,
    project_dir: str = None,
) -> dict:
    """Mark previously-completed requirements as regressed.

    Fix 8: When E2E verification discovers that a requirement which was
    previously 'completed' or 'verified' is now broken, mark it as
    'regressed'. This blocks publishing until the regression is fixed.

    Only requirements in done-like statuses ('completed', 'verified',
    'delegation_returned') can be regressed. Pending/assigned requirements
    that were never completed are skipped.

    Args:
        agent_data: The agent.data dict
        req_ids: List of requirement IDs to mark as regressed
        reason: Human-readable reason for the regression
        project_dir: If provided, auto-persist ledger to disk after update

    Returns:
        Dict with 'regressed' (list of IDs that were regressed) and
        'skipped' (list of dicts with 'id' and 'reason' for skipped IDs)
    """
    from datetime import datetime, timezone

    ledger = _ensure_ledger(agent_data)
    req_map = build_normalized_req_map(ledger.get("requirements", []))

    regressable_statuses = ("completed", "verified", "delegation_returned")
    regressed_ids = []
    skipped = []

    for req_id in req_ids:
        if req_id not in req_map:
            skipped.append({"id": req_id, "reason": "not found in ledger"})
            continue

        req = req_map[req_id]
        current_status = req.get("status", "")

        if current_status not in regressable_statuses:
            skipped.append({
                "id": req_id,
                "reason": f"status '{current_status}' is not regressable",
            })
            continue

        # Set regression fields (ADR-086: stage-keyed)
        req["previous_status"] = current_status
        set_stage_status(req, "code", "regressed")
        req["regression_reason"] = reason

        # Track regression history
        if "regression_history" not in req:
            req["regression_history"] = []
        req["regression_history"].append({
            "from_status": current_status,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        regressed_ids.append(req_id)

    if regressed_ids:
        logger.warning(
            f"[REQUIREMENTS LEDGER] Regressed {len(regressed_ids)} "
            f"requirement(s): {regressed_ids}. Reason: {reason[:100]}"
        )
    if skipped:
        logger.info(
            f"[REQUIREMENTS LEDGER] Skipped {len(skipped)} requirement(s) "
            f"during regression: {skipped}"
        )

    # Auto-persist if project_dir provided
    if project_dir:
        persist_ledger_to_project(agent_data, project_dir)

    return {"regressed": regressed_ids, "skipped": skipped}


def get_regressed_requirements(agent_data: dict) -> list:
    """Retrieve all requirements with status='regressed'.

    Fix 8: Returns requirements that were previously completed but
    have since regressed due to E2E verification failures or other
    breakages. Used by the orchestrator to identify what needs rework.

    Args:
        agent_data: The agent.data dict

    Returns:
        List of requirement dicts with status='regressed'
    """
    ledger = _ensure_ledger(agent_data)
    return [
        req for req in ledger.get("requirements", [])
        if req.get("status") == "regressed"
    ]
