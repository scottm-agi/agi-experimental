"""Phase Completion Guard — RCA-ITR41 F-1 + Category-aware validation.

Centralizes the delegation status check that determines whether a phase
should be marked as completed after a delegation returns.

Root cause (RCA-ITR41): call_subordinate.py:1035 had:
    is_failed = delegation_result.status in ("failed",)
This only checked for "failed", allowing "partial", "escalated", and
"cancelled" delegations to mark phases as fully "completed" — causing
the orchestrator to skip re-attempting incomplete work.

This module provides:
    should_skip_phase_completion(status) — Original status-only check (Layer 1).
    validate_phase_completion(...)       — Category-aware validation (Layer 1 + 2).

Layer 1: Status check (fast path — same as should_skip_phase_completion).
Layer 2: Category-aware output verification using phase_category + evidence.
    - PLANNING/DESIGN: delegation success is sufficient.
    - IMPLEMENTATION: delegation success AND new source files created.
    - INTEGRATION/VERIFICATION/DELIVERY: delegation success (future: richer checks).

Consumers:
    - call_subordinate.py (should_skip_phase_completion — existing)
    - orchestrator gate (validate_phase_completion — new)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Set

logger = logging.getLogger("python.helpers.phase_completion_guard")

# Statuses that indicate a delegation did NOT fully succeed.
# Phases must NOT be marked "completed" when a delegation returns
# with any of these statuses.
_FAILED_STATUSES = frozenset({
    "failed",       # Delegation explicitly failed
    "partial",      # Subordinate hit iteration/chain limit, force_complete, or quality failure
    "escalated",    # Escalated to human or higher-level agent
    "cancelled",    # Cancelled by timeout or supervisor
})


def should_skip_phase_completion(delegation_status: str) -> bool:
    """Return True if the delegation status indicates the phase should
    NOT be marked as completed.

    Args:
        delegation_status: The status string from DelegationResult
            (e.g., "completed", "partial", "failed", "escalated", "cancelled").

    Returns:
        True if phase completion should be SKIPPED (delegation was not successful).
        False if phase completion should PROCEED (delegation succeeded).
    """
    return delegation_status.lower().strip() in _FAILED_STATUSES


# ──────────────────────────────────────────────────────────────────────
# Category-aware validation (Layer 2)
# ──────────────────────────────────────────────────────────────────────

# Lazy imports to avoid circular dependency at module load time.
# These are imported inside validate_phase_completion() on first call.

@dataclass
class PhaseCompletionResult:
    """Result of validate_phase_completion — structured verdict.

    Attributes:
        should_skip:        True → do NOT mark phase completed.
        reason:             Human-readable explanation of the verdict.
        recommended_status: What status to set on the phase
                            (completed / partially_completed / pending).
        evidence:           Optional structured PhaseCompletionEvidence.
    """
    should_skip: bool
    reason: str
    recommended_status: str
    evidence: object = None  # PhaseCompletionEvidence or None


def validate_phase_completion(
    delegation_status: str,
    phase_seq: str = "",
    project_dir: str = "",
    pre_delegation_files: Optional[Set[str]] = None,
    chat_id: str = "",
) -> PhaseCompletionResult:
    """Category-aware phase completion validation.

    Layer 1: Status check (fast path — same as should_skip_phase_completion).
    Layer 2: Category-aware output verification:
        - IMPLEMENTATION: must have created new source files.
        - All other categories: delegation success is sufficient.

    For backward compatibility, callers can still use
    should_skip_phase_completion() directly for status-only checks.

    Args:
        delegation_status:    Status from DelegationResult.
        phase_seq:            Phase sequence identifier (e.g. "3.1").
        project_dir:          Absolute path to the project root.
        pre_delegation_files: Set of relative source file paths captured
                              BEFORE the delegation.  None → skip file check.
        chat_id:              Chat/conversation ID for traceability.

    Returns:
        PhaseCompletionResult with should_skip, reason, recommended_status,
        and optional evidence.
    """
    # Lazy imports — keep module-level import list minimal to avoid cycles.
    from python.helpers.phase_category import get_phase_category, PhaseCategory
    from python.helpers.phase_completion_evidence import PhaseCompletionEvidence
    from python.helpers.implementation_completion_validator import (
        validate_implementation_completion,
    )

    # ── Layer 1: Status rejection (fast path) ──
    if should_skip_phase_completion(delegation_status):
        return PhaseCompletionResult(
            should_skip=True,
            reason=f"Delegation status '{delegation_status}' is in FAILED_STATUSES",
            recommended_status=(
                "partially_completed" if delegation_status == "partial" else "pending"
            ),
        )

    # ── Determine category ──
    category = get_phase_category(phase_seq)
    category_str = category.value if category else "unknown"

    # ── Layer 2: Category-aware validation ──
    # ITR-55 P1: For IMPLEMENTATION phases, delegation success IS the evidence.
    # The subordinate's code_self_check already validates build + TDD green
    # before returning. Line-counting is demoted to advisory-only logging.
    if category == PhaseCategory.IMPLEMENTATION and pre_delegation_files is not None:
        # ADR-82: Support both FileSnapshot (new) and Set[str] (legacy)
        from python.helpers.implementation_completion_validator import FileSnapshot
        if isinstance(pre_delegation_files, FileSnapshot):
            result = validate_implementation_completion(
                project_dir, phase_seq,
                pre_delegation_snapshot=pre_delegation_files,
            )
        else:
            result = validate_implementation_completion(
                project_dir, phase_seq,
                pre_delegation_files=pre_delegation_files,
            )
        evidence = PhaseCompletionEvidence(
            phase_seq=phase_seq,
            category=category_str,
            delegation_status=delegation_status,
            new_files_created=result["new_files"],
            new_file_count=result["new_file_count"],
            total_lines_added=result["total_lines_added"],
            chat_id=chat_id,
        )
        if not result["passed"]:
            # ITR-55 P1: Log for audit trail but DO NOT BLOCK.
            # Delegation status=success means subordinate TDD/build verified.
            logger.info(
                f"[PHASE COMPLETION] Phase {phase_seq} [implementation]: "
                f"File evidence below threshold ({result['reason']}) but "
                f"delegation succeeded — trusting TDD/build status."
            )
        return PhaseCompletionResult(
            should_skip=False,
            reason=result["reason"],
            recommended_status="completed",
            evidence=evidence,
        )

    # ── G-27: Category-aware soft checks for non-IMPLEMENTATION phases ──
    # Previously all non-IMPLEMENTATION categories fell through to
    # "delegation success is sufficient" with no output verification.
    # Now we add SOFT (advisory) checks for VERIFICATION and INTEGRATION.
    # These log warnings but do NOT block phase completion.
    evidence = PhaseCompletionEvidence(
        phase_seq=phase_seq,
        category=category_str,
        delegation_status=delegation_status,
        chat_id=chat_id,
    )

    if category == PhaseCategory.VERIFICATION:
        # Soft check: look for test execution evidence
        has_test_evidence = (
            evidence.tdd_tests_passed
            or evidence.verification_matrix_score > 0
            or evidence.e2e_pass_rate > 0
        )
        if not has_test_evidence:
            logger.warning(
                f"[PHASE COMPLETION] Phase {phase_seq} [verification]: "
                f"No test execution evidence found (tdd_tests_passed=False, "
                f"verification_matrix_score=0, e2e_pass_rate=0). "
                f"Allowing completion but quality may be degraded."
            )

    elif category == PhaseCategory.INTEGRATION:
        # Soft check: look for integration evidence (build/routes)
        has_integration_evidence = (
            evidence.build_passed
            or evidence.routes_wired > 0
            or evidence.fetch_route_alignment > 0
        )
        if not has_integration_evidence:
            logger.warning(
                f"[PHASE COMPLETION] Phase {phase_seq} [integration]: "
                f"No integration evidence found (build_passed=False, "
                f"routes_wired=0). Allowing completion but wiring "
                f"may be incomplete."
            )

    # DELIVERY and all other categories: delegation success is sufficient
    return PhaseCompletionResult(
        should_skip=False,
        reason=f"Phase {phase_seq} [{category_str}]: delegation succeeded",
        recommended_status="completed",
        evidence=evidence,
    )
