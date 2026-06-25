"""
Requirements Coverage Queries

Extracted from requirements_ledger.py during P4 modularization (Phase 1.4).
Contains coverage statistics, publishing readiness checks, BDD coverage,
assignment coverage verification, and remediation task queries.

Note: This is SEPARATE from requirements_coverage.py which contains the
unified get_full_coverage() function used by gates. This module contains
the original ledger query functions for backward compatibility.

Functions:
    can_publish                  — Check if publishing is allowed (no regressions)
    get_incomplete_requirements  — Get all non-completed/verified requirements
    get_bdd_coverage             — Get BDD spec coverage across delegations
    get_coverage                 — Get overall coverage statistics
    check_assignment_coverage    — Deterministic assignment coverage check
    get_unassigned_requirements  — Get requirements needing delegation
    get_pending_remediation_tasks — Get pending remediation tasks
"""

import logging
from typing import Any, Dict, List

from python.helpers.requirements_persistence import _ensure_ledger

logger = logging.getLogger("agix.requirements_ledger")


def can_publish(agent_data: dict) -> bool:
    """Check if publishing is allowed (no regressed requirements).

    Fix 8: Publishing is blocked when any requirement has status='regressed'.
    An empty ledger (no requirements) is considered publishable.

    Args:
        agent_data: The agent.data dict

    Returns:
        True if no requirements are regressed, False otherwise
    """
    from python.helpers.requirements_proof import get_regressed_requirements
    return len(get_regressed_requirements(agent_data)) == 0


def get_incomplete_requirements(agent_data: dict) -> list:
    """Get all requirements that are NOT completed or verified.

    Returns requirements with status in (pending, assigned, escalated,
    unverified, regressed). Fix 8: Added 'regressed' — a regressed req
    is NOT done, it needs rework.
    Used by the completion gate to block premature delivery and by the
    boomerang to warn when requirements are still outstanding.

    Args:
        agent_data: The agent.data dict

    Returns:
        List of requirement dicts (id, text, status, category)
    """
    ledger = _ensure_ledger(agent_data)
    reqs = ledger.get("requirements", [])
    return [
        r for r in reqs
        if r.get("status") not in ("completed", "verified")
    ]


# ─── BDD Coverage ────────────────────────────────────────────────────────


def get_bdd_coverage(agent_data: dict) -> Dict[str, Any]:
    """Get BDD spec coverage across delegations.

    Returns how many delegations have BDD specs attached vs total.
    Used by the gate to assess whether the architect provided
    acceptance criteria for this build.

    Returns:
        Dict with keys: total_delegations, with_bdd_specs, coverage_pct
    """
    ledger = _ensure_ledger(agent_data)
    delegations = ledger.get("delegations", [])
    total = len(delegations)
    with_bdd = sum(
        1 for d in delegations
        if d.get("bdd_specs") and len(d["bdd_specs"]) > 0
    )
    pct = int(with_bdd / max(total, 1) * 100)
    return {
        "total_delegations": total,
        "with_bdd_specs": with_bdd,
        "coverage_pct": pct,
    }


# ─── Coverage Queries ────────────────────────────────────────────────────


def get_coverage(agent_data: dict) -> Dict[str, Any]:
    """Get coverage statistics for requirements.

    Returns:
        Dict with keys: total_requirements, assigned, completed, unassigned (list of IDs)
    """
    ledger = _ensure_ledger(agent_data)
    reqs = ledger.get("requirements", [])

    total = len(reqs)
    assigned = sum(1 for r in reqs if r["status"] in ("assigned", "completed", "verified", "delegation_returned"))
    completed = sum(1 for r in reqs if r["status"] in ("completed", "verified"))
    unassigned_ids = [r["id"] for r in reqs if r["status"] == "pending"]

    return {
        "total_requirements": total,
        "assigned": assigned,
        "completed": completed,
        "unassigned": unassigned_ids,
    }


def check_assignment_coverage(agent_data: dict) -> Dict[str, Any]:
    """Deterministic check that every requirement has a decomposition assignment.

    RCA-362 L1 Fix: The MSR_Ph3 audit revealed that the core product feature
    (review capture) was extracted as a requirement but never assigned to any
    decomposition phase. This function provides a structured check that the
    orchestrator can call at Phase 2.5 to verify complete coverage BEFORE
    implementation begins.

    A requirement is considered "assigned" if:
    - Its status is anything other than "pending" (assigned, completed, verified), OR
    - Its assigned_to list is non-empty

    Returns:
        Dict with keys:
        - complete: bool — True if all requirements have assignments
        - total_requirements: int
        - assigned_count: int
        - unassigned_count: int
        - coverage_pct: int (0-100)
        - unassigned_requirements: list of {id, text, category} for each unassigned req
    """
    ledger = _ensure_ledger(agent_data)
    reqs = ledger.get("requirements", [])

    if not reqs:
        return {
            "complete": True,
            "total_requirements": 0,
            "assigned_count": 0,
            "unassigned_count": 0,
            "coverage_pct": 100,
            "unassigned_requirements": [],
        }

    assigned = []
    unassigned = []

    for req in reqs:
        status = req.get("status", "pending")
        assigned_to = req.get("assigned_to", [])

        if status != "pending" or assigned_to:
            assigned.append(req)
        else:
            unassigned.append({
                "id": req.get("id", ""),
                "text": req.get("text", ""),
                "category": req.get("category", ""),
            })

    total = len(reqs)
    assigned_count = len(assigned)
    unassigned_count = len(unassigned)
    coverage_pct = int(assigned_count / max(total, 1) * 100)

    if unassigned_count > 0:
        logger.warning(
            f"[REQUIREMENTS LEDGER] Assignment coverage gap: "
            f"{unassigned_count}/{total} requirements have no decomposition "
            f"phase assignment. IDs: {[u['id'] for u in unassigned]}"
        )

    return {
        "complete": unassigned_count == 0,
        "total_requirements": total,
        "assigned_count": assigned_count,
        "unassigned_count": unassigned_count,
        "coverage_pct": coverage_pct,
        "unassigned_requirements": unassigned,
    }



def get_unassigned_requirements(agent_data: dict) -> List[Dict]:
    """Get requirements that need delegation.

    Includes both 'pending' (never assigned) AND 'unverified'
    (assigned but failed post-execution verification).

    Returns:
        List of requirement dicts with status in ('pending', 'unverified')
    """
    ledger = _ensure_ledger(agent_data)
    return [
        r for r in ledger.get("requirements", [])
        if r["status"] in ("pending", "unverified")
    ]




def get_pending_remediation_tasks(agent_data: dict) -> List[Dict]:
    """Get remediation tasks that haven't been executed yet.

    The completion gate uses this to block when there are pending
    remediation tasks that the orchestrator hasn't delegated.

    Returns:
        List of remediation task dicts with status == 'pending'
    """
    ledger = _ensure_ledger(agent_data)
    return [
        t for t in ledger.get("remediation_tasks", [])
        if t.get("status") == "pending"
    ]
