"""
Unified Requirements Coverage
==============================

Single source of truth for requirement coverage calculations.
All gates (decomposition, verification, completion) call this
module instead of implementing their own coverage logic.

Consolidates 4 redundant coverage implementations into one function.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from python.helpers.status_constants import STAGE_DONE_STATUSES, VALID_STAGES

logger = logging.getLogger("agix.requirements_coverage")


def get_full_coverage(
    agent_data: dict,
    include_unverified: bool = True,
) -> Dict[str, Any]:
    """Single source of truth for requirement coverage.

    Returns a comprehensive coverage report that ALL gates can use.

    Args:
        agent_data: The agent.data dict
        include_unverified: Whether to count unverified as incomplete

    Returns:
        Dict with keys:
        - total: total number of requirements
        - pending: list of REQ-IDs with status 'pending'
        - assigned: list of REQ-IDs with status 'assigned'
        - completed: list of REQ-IDs with status 'completed'
        - verified: list of REQ-IDs with status 'verified'
        - unverified: list of REQ-IDs with status 'unverified'
        - failed: list of REQ-IDs with status 'failed'
        - coverage_pct: float 0.0-1.0 of completed/verified
        - is_complete: True if all requirements are completed or verified
        - pending_remediation: count of pending remediation tasks
    """
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return {
            "total": 0,
            "pending": [],
            "assigned": [],
            "completed": [],
            "verified": [],
            "unverified": [],
            "failed": [],
            "coverage_pct": 1.0,
            "is_complete": True,
            "pending_remediation": 0,
            "stage_coverage": {
                stage: {"completed": 0, "total": 0, "pct": 1.0}
                for stage in sorted(VALID_STAGES)
            },
        }

    reqs = ledger.get("requirements", [])
    total = len(reqs)

    # Categorize by status
    pending = [r["id"] for r in reqs if r.get("status") == "pending"]
    assigned = [r["id"] for r in reqs if r.get("status") == "assigned"]
    completed = [r["id"] for r in reqs if r.get("status") == "completed"]
    verified = [r["id"] for r in reqs if r.get("status") == "verified"]
    unverified = [r["id"] for r in reqs if r.get("status") == "unverified"]
    failed = [r["id"] for r in reqs if r.get("status") == "failed"]

    # Coverage: completed + verified count as "done"
    done_count = len(completed) + len(verified)
    coverage_pct = done_count / total if total > 0 else 1.0

    # Completeness: all reqs must be completed or verified
    is_complete = (done_count == total) if total > 0 else True

    # Pending remediation tasks
    remediation_tasks = ledger.get("remediation_tasks", [])
    pending_remediation = sum(
        1 for t in remediation_tasks if t.get("status") == "pending"
    )

    # If there are pending remediation tasks, not complete
    if pending_remediation > 0:
        is_complete = False

    # ADR-086: Stage-keyed coverage breakdown
    # Computes per-stage (bdd, tdd, code) completion counts using
    # categorical stage detection — no hardcoded phase numbers.
    from python.helpers.requirements_ledger import ensure_stage_status
    stage_coverage = {}
    for stage in sorted(VALID_STAGES):
        done = 0
        for r in reqs:
            ss = r.get("stage_status")
            if not ss:
                ss = ensure_stage_status(r)
            stage_val = ss.get(stage, "pending")
            if stage_val in STAGE_DONE_STATUSES:
                done += 1
        stage_coverage[stage] = {
            "completed": done,
            "total": total,
            "pct": done / total if total > 0 else 1.0,
        }

    return {
        "total": total,
        "pending": pending,
        "assigned": assigned,
        "completed": completed,
        "verified": verified,
        "unverified": unverified,
        "failed": failed,
        "coverage_pct": coverage_pct,
        "is_complete": is_complete,
        "pending_remediation": pending_remediation,
        "stage_coverage": stage_coverage,
    }
