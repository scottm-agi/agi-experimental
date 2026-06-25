"""Phase sequence parser — universal handler for all phase formats.

RCA-345 FIX-1: Replaces broken int(float()) normalization that caused
semver phases ("1.1.0") to default to 0, breaking phase-transition
detection and making error-state bypass permanently sticky.

Also provides FIX-2 (mark_decomp_phase_completed) and FIX-3
(validate_decomp_guids) helper functions.

This module is a SHARED utility imported by:
- _22_multiagentdev_completion_gate.py (phase comparison)
- call_subordinate.py (phase detection on delegation)
- orchestrator_gate_common.py (bypass reset logic)
- requirements.py (reconciliation)
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("agent.phase_parser")


def parse_phase_seq(val: Any) -> Tuple[int, int, int]:
    """Parse any phase sequence format into a comparable (major, minor, patch) tuple.

    Handles ALL real-world formats:
        "3"     → (3, 0, 0)   — canonical integer
        "3.1"   → (3, 1, 0)   — canonical dot
        "0.5"   → (0, 5, 0)   — canonical half-phase
        "2.3"   → (2, 3, 0)   — canonical design phase
        "1.1.0" → (1, 1, 0)   — semver (the format that broke R5)
        "3.2.1" → (3, 2, 1)   — semver with patch
        3       → (3, 0, 0)   — raw int
        3.1     → (3, 1, 0)   — raw float
        None    → (0, 0, 0)   — graceful fallback
        ""      → (0, 0, 0)   — graceful fallback
        "abc"   → (0, 0, 0)   — graceful fallback

    Tuple comparison provides natural ordering:
        (3, 1, 0) > (1, 1, 0)  — True  (phase 3.1 > phase 1.1)
        (1, 1, 0) > (1, 1, 0)  — False (same phase, no transition)
        (2, 3, 0) > (0, 5, 0)  — True  (phase 2.3 > phase 0.5)

    Args:
        val: Phase sequence value — string, int, float, or None.

    Returns:
        Tuple of (major, minor, patch) integers for comparison.
    """
    if val is None:
        return (0, 0, 0)

    s = str(val).strip()
    if not s:
        return (0, 0, 0)

    parts = s.split(".")
    result = [0, 0, 0]

    for i, part in enumerate(parts[:3]):
        try:
            result[i] = int(part)
        except (ValueError, TypeError):
            # Non-numeric segment — return zero tuple
            return (0, 0, 0)

    return tuple(result)


def _normalize_seq_for_match(seq: str) -> str:
    """Normalize a seq string for matching — strip trailing .0 segments.

    "2.1.0" → "2.1"
    "1.0.0" → "1"
    "3" → "3"
    "2.3" → "2.3"
    """
    s = str(seq).strip()
    while s.endswith(".0"):
        s = s[:-2]
    return s


def _extract_profile_from_evidence(evidence: str) -> str:
    """Extract delegation profile from evidence string.

    Evidence strings from call_subordinate follow the format:
        'delegation returned (status=completed, profile=code)'

    Returns:
        Profile string (e.g., 'code', 'architect'), or '' if not found.
    """
    if not evidence:
        return ""
    match = re.search(r'profile=([a-zA-Z_]+)', evidence)
    return match.group(1) if match else ""


# Profiles that advance the 'code' stage (implementation work)
_CODE_PROFILES = frozenset({"code", "coder", "developer"})


def _get_or_create_phase_sm(phase: dict) -> 'PhaseStateMachine':
    """Get or create a PhaseStateMachine for a phase dict.

    RCA-475: WRAP pattern — stores SM on the phase dict itself
    since mark_decomp_phase_completed operates on phase lists,
    not agent_data.

    RCA-479 Fix: Handles corrupted SM entries from JSON round-trip.
    """
    from python.helpers.state_machines.phase_sm import PhaseStateMachine
    existing = phase.get("_sm")
    if not isinstance(existing, PhaseStateMachine):
        phase["_sm"] = PhaseStateMachine(
            status=phase.get("status", "pending"),
            entity_id=f"phase-{phase.get('seq', '?')}",
        )
    return phase["_sm"]


def mark_decomp_phase_completed(
    phases: List[Dict],
    phase_seq: str,
    evidence: str = "",
    force_accepted: bool = False,
    project_dir: str = None,
) -> Dict:
    """Deterministically mark a decomposition phase as completed.

    RCA-345 FIX-2: Called by call_subordinate when a delegation returns.
    The delegation tool KNOWS which phase it delegated — no guessing.

    ADR-086 §12 + ITR-55: Implementation and unknown-category phases
    require a CODE profile delegation to be marked completed. Non-code
    delegations (architect, frontend, researcher) advance the design
    stage but do NOT complete the phase.

    ADR-089 (ISSUE-1 FIX): The 'completed' status MUST be set via
    try_phase_completed() from requirements_ledger, not by direct
    assignment. This ensures requirements-based validation runs.
    When project_dir is None or the ledger is missing, try_phase_completed
    fails open (empty requirements → allowed).

    Matching logic: normalizes both the target seq and each phase's seq
    (strips trailing .0) before comparing. This handles "2.1.0" matching
    "2.1" and vice versa.

    Args:
        phases: List of phase dicts (mutated in-place).
        phase_seq: The seq of the phase to mark completed.
        evidence: Description of why phase is completed.
        force_accepted: If True, mark as "partially_completed" instead.
        project_dir: Absolute path to the project directory. Used to load
            the requirements ledger for ADR-089 validation. If None,
            falls back to direct assignment (backward compat / no-project context).

    Returns:
        {"found": bool, "phase_seq": str, "new_status": str}
    """
    target_normalized = _normalize_seq_for_match(phase_seq)
    target_tuple = parse_phase_seq(phase_seq)

    for phase in phases:
        phase_normalized = _normalize_seq_for_match(phase.get("seq", ""))
        phase_tuple = parse_phase_seq(phase.get("seq", ""))

        if phase_normalized == target_normalized or phase_tuple == target_tuple:
            current_status = phase.get("status", "pending")

            # F-8: Use canonical status set (was missing partially_completed + skipped)
            from python.helpers.status_constants import PHASE_DONE_STATUSES
            if current_status in PHASE_DONE_STATUSES:
                return {
                    "found": True,
                    "phase_seq": str(phase.get("seq", "")),
                    "new_status": current_status,
                }

            # ADR-086 §12 + ITR-55: Category-aware completion guard.
            # Implementation and unknown phases require code profile.
            from python.helpers.requirements_ledger import infer_phase_category
            phase_title = phase.get("title", "")
            category = infer_phase_category(phase_title)
            delegation_profile = _extract_profile_from_evidence(evidence)

            if category in ("implementation", "unknown") and not force_accepted:
                if delegation_profile and delegation_profile not in _CODE_PROFILES:
                    # Non-code delegation for implementation phase:
                    # advance to in_progress (design done) but NOT completed
                    if current_status == "pending":
                        phase["status"] = "in_progress"
                        phase["note"] = (
                            f"Design delegation returned (profile={delegation_profile}). "
                            f"Awaiting code delegation for completion."
                        )
                        # RCA-475: WRAP — SM tracks in_progress transition
                        sm = _get_or_create_phase_sm(phase)
                        ok, msg = sm.transition("in_progress", reason=f"Design delegation returned (profile={delegation_profile})", source="phase_parser.py")
                        if not ok:
                            logger.warning(f"[PHASE SM] {msg} — status set anyway (migration)")
                            sm.transition("in_progress", reason="force-sync", force=True)
                    logger.info(
                        f"[PHASE PARSER] Phase {phase.get('seq', '?')} ({category}) "
                        f"NOT completed — delegation profile='{delegation_profile}' "
                        f"is not a code profile. Status: {phase['status']}"
                    )
                    return {
                        "found": True,
                        "phase_seq": str(phase.get("seq", "")),
                        "new_status": phase["status"],
                    }

            new_status = "partially_completed" if force_accepted else "completed"

            if force_accepted:
                # force_accepted → partially_completed, skip ADR-089 validation
                phase["status"] = new_status
                # RCA-475: WRAP — SM tracks force_accepted transition
                sm = _get_or_create_phase_sm(phase)
                ok, msg = sm.transition(new_status, reason=evidence[:200] if evidence else "force_accepted", source="phase_parser.py")
                if not ok:
                    logger.warning(f"[PHASE SM] {msg} — status set anyway (migration)")
                    sm.transition(new_status, reason="force-sync", force=True)
            elif project_dir:
                # ADR-089: route through try_phase_completed for requirements validation
                from python.helpers.requirements_ledger import try_phase_completed
                note = (
                    f"auto-completed (RCA-345: deterministic completion on delegation return). "
                    f"Evidence: {evidence[:200]}"
                )
                allowed, reason = try_phase_completed(phase, project_dir, note=note)
                if not allowed:
                    logger.warning(
                        f"[PHASE PARSER] ADR-089 blocked completion of phase "
                        f"{phase.get('seq', '?')}: {reason}"
                    )
                    new_status = phase.get("status", "pending")  # unchanged
                    return {
                        "found": True,
                        "phase_seq": str(phase.get("seq", "")),
                        "new_status": new_status,
                    }
                # try_phase_completed already set phase["status"] = "completed"
                # RCA-475: WRAP — SM tracks try_phase_completed result
                sm = _get_or_create_phase_sm(phase)
                note_reason = f"auto-completed (RCA-345). Evidence: {evidence[:200]}" if evidence else "try_phase_completed"
                ok, msg = sm.transition(phase["status"], reason=note_reason, source="requirements_ledger.try_phase_completed")
                if not ok:
                    logger.warning(f"[PHASE SM] {msg} — status set anyway (migration)")
                    sm.transition(phase["status"], reason="force-sync", force=True)
            else:
                # No project_dir — fall back to direct assignment (backward compat)
                phase["status"] = new_status
                phase["note"] = (
                    f"auto-completed (RCA-345: deterministic completion on delegation return). "
                    f"Evidence: {evidence[:200]}"
                )
                # RCA-475: WRAP — SM tracks direct assignment
                sm = _get_or_create_phase_sm(phase)
                ok, msg = sm.transition(new_status, reason=evidence[:200] if evidence else "direct assignment", source="phase_parser.py")
                if not ok:
                    logger.warning(f"[PHASE SM] {msg} — status set anyway (migration)")
                    sm.transition(new_status, reason="force-sync", force=True)

            logger.info(
                f"[PHASE PARSER] Marked phase {phase.get('seq', '?')} as "
                f"{new_status} (was {current_status})"
            )

            return {
                "found": True,
                "phase_seq": str(phase.get("seq", "")),
                "new_status": new_status,
            }

    return {"found": False, "phase_seq": phase_seq, "new_status": ""}


def validate_decomp_guids(
    phases: List[Dict],
    ledger_req_ids: Set[str],
) -> Dict:
    """Validate that decomposition phase req_guids exist in the requirements ledger.

    RCA-345 FIX-3: Detects orphan GUIDs at save_manifest time.
    8 of 14 phases in the regression run used fabricated GUIDs that
    didn't exist in the ledger, causing silent reconciliation failure.

    Args:
        phases: List of phase dicts with "req_guids" lists.
        ledger_req_ids: Set of valid requirement IDs from the ledger.

    Returns:
        {
            "valid": bool — True if all GUIDs exist in ledger,
            "orphan_guids": list — GUIDs not found in ledger,
            "orphan_phases": list — Phase seqs with orphan GUIDs,
            "total_guids": int — Total GUIDs across all phases,
            "orphan_count": int — Number of orphan GUIDs,
        }
    """
    orphan_guids = []
    orphan_phases = []
    total_guids = 0

    for phase in phases:
        guids = phase.get("req_guids", [])
        if not guids:
            continue

        total_guids += len(guids)
        phase_orphans = [g for g in guids if g not in ledger_req_ids]

        if phase_orphans:
            orphan_guids.extend(phase_orphans)
            orphan_phases.append(str(phase.get("seq", "?")))

    return {
        "valid": len(orphan_guids) == 0,
        "orphan_guids": orphan_guids,
        "orphan_phases": orphan_phases,
        "total_guids": total_guids,
        "orphan_count": len(orphan_guids),
    }
