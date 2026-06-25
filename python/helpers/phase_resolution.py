"""Phase Resolution — Universal phase-to-decomposition matching.

Maps delegation outcomes to decomposition-index.json phases using a
multi-strategy resolution pipeline:

    Priority 1: requirement_ids overlap (definitive, structured data)
    Priority 2: exact phase seq match (when phase number is precise)
    Priority 3: parent-to-child resolution (3.0 → 3.1, 3.2, ..., 3.6)
    Priority 4: category-based matching (title-inferred category)

This module is SKILL-AGNOSTIC. It works for:
    - Fullstack projects (numbered phases from fullstack-dev SKILL)
    - Backend-only / FE-only projects (sparse or sequential numbers)
    - Small tasks (may only have 1-2 phases, possibly unnumbered)

The resolution pipeline always works because:
    - req_guids are attached to phases by the requirements system regardless
      of which skill generated the decomposition
    - Title-based category inference (infer_phase_category) uses keyword
      matching on titles, not numbers
    - Parent-child resolution extracts the integer part and finds children
      with the same prefix

Consumers:
    - call_subordinate_execute.py (post-delegation auto-completion)
    - requirements_actions.py (complete_phase tool action)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("agent.phase_resolution")


# ── Phase-done statuses (imported lazily to avoid circular deps) ──
_DONE_STATUSES: Optional[frozenset] = None


def _get_done_statuses() -> frozenset:
    global _DONE_STATUSES
    if _DONE_STATUSES is None:
        try:
            from python.helpers.status_constants import PHASE_DONE_STATUSES
            _DONE_STATUSES = frozenset(PHASE_DONE_STATUSES)
        except ImportError:
            _DONE_STATUSES = frozenset({
                "completed", "done", "complete", "verified",
                "skipped", "partially_completed", "blocked", "deferred",
            })
    return _DONE_STATUSES


def _is_done(status: str) -> bool:
    """Check if a phase status indicates it's already done."""
    return status.lower().strip() in _get_done_statuses()


def _extract_major(seq: Any) -> Optional[int]:
    """Extract the integer (major) part from a phase sequence.

    "3.4" → 3, "3" → 3, "3.0" → 3, 3.0 → 3, None → None
    """
    if seq is None:
        return None
    if isinstance(seq, (int, float)):
        return int(seq)
    s = str(seq).strip()
    if not s:
        return None
    m = re.match(r"^(\d+)", s)
    return int(m.group(1)) if m else None


def _normalize_seq(seq: Any) -> str:
    """Normalize phase seq for comparison: strip trailing .0.

    "3.0" → "3", "3.1.0" → "3.1", "3" → "3", "3.4" → "3.4"
    """
    s = str(seq).strip()
    while s.endswith(".0"):
        s = s[:-2]
    return s


# ─── Profile → compatible categories ───────────────────────────────────
# Implementation phases can only be completed by code profiles.
# Design phases by architect/frontend. Research by researcher.
# This prevents false completions (e.g., architect delegation
# shouldn't mark implementation phases done).
_PROFILE_CATEGORY_COMPAT = {
    "code":         {"planning", "implementation", "verification", "unknown"},
    "coder":        {"planning", "implementation", "verification", "unknown"},
    "developer":    {"planning", "implementation", "verification", "unknown"},
    "frontend":     {"design", "implementation"},
    "architect":    {"design", "research"},
    "researcher":   {"research", "planning"},
    "e2e":          {"verification"},
    "browser":      {"verification"},
}


def _is_profile_compatible(profile: str, category: str) -> bool:
    """Check if a delegation profile can complete phases of this category."""
    if not profile:
        return True  # No profile info → allow (backward compat)
    allowed = _PROFILE_CATEGORY_COMPAT.get(profile.lower().strip())
    if allowed is None:
        return True  # Unknown profile → allow (fail-open)
    return category.lower().strip() in allowed


def _get_phase_category(title: str) -> str:
    """Get category from phase title, lazy-importing to avoid cycles."""
    if not title:
        return "unknown"
    try:
        from python.helpers.requirements_stage import infer_phase_category
        return infer_phase_category(title)
    except ImportError:
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════


def resolve_phases_by_requirement_ids(
    phases: List[Dict],
    requirement_ids: List[str],
) -> List[str]:
    """Find phases whose req_guids overlap with the given requirement_ids.

    Returns list of phase seq strings for phases that:
    1. Have at least one req_guid matching a requirement_id
    2. Are NOT already in a done status

    Args:
        phases: List of phase dicts from decomposition-index.json
        requirement_ids: List of requirement IDs from the delegation kwargs

    Returns:
        List of matching phase seq strings.
    """
    if not requirement_ids:
        return []

    req_set = set(requirement_ids)
    matched = []
    for phase in phases:
        phase_reqs = phase.get("req_guids") or phase.get("req_ids") or []
        if not phase_reqs:
            continue
        if _is_done(phase.get("status", "pending")):
            continue  # Already done — don't double-mark
        if req_set.intersection(phase_reqs):
            seq = str(phase.get("seq", phase.get("phase_seq", "")))
            if seq:
                matched.append(seq)

    return matched


def resolve_parent_to_children(
    phases: List[Dict],
    parent_seq: str,
) -> List[str]:
    """Find child phases of a parent sequence.

    When the orchestrator says "Phase 3.0" but the decomposition index
    has 3.1, 3.2, ..., 3.6, this function finds those children.

    If the exact parent_seq exists in the index, returns just that phase.

    Args:
        phases: List of phase dicts from decomposition-index.json
        parent_seq: The parent phase sequence (e.g., "3.0", "3", "4")

    Returns:
        List of child phase seq strings.
    """
    parent_normalized = _normalize_seq(parent_seq)
    parent_major = _extract_major(parent_seq)
    if parent_major is None:
        return []

    # Check if exact match exists first
    for phase in phases:
        phase_normalized = _normalize_seq(phase.get("seq", ""))
        if phase_normalized == parent_normalized:
            return [str(phase.get("seq", ""))]

    # No exact match — find children with same major number
    children = []
    for phase in phases:
        seq = str(phase.get("seq", phase.get("phase_seq", "")))
        phase_major = _extract_major(seq)
        if phase_major == parent_major:
            children.append(seq)

    return children


def all_children_completed(
    phases: List[Dict],
    parent_seq: str,
) -> bool:
    """Check if ALL sub-phases of a parent are in a done status.

    Returns False if no children exist (nothing to verify).

    Args:
        phases: List of phase dicts from decomposition-index.json
        parent_seq: The parent phase sequence (e.g., "3.0")

    Returns:
        True if all children are done, False otherwise.
    """
    parent_normalized = _normalize_seq(parent_seq)
    parent_major = _extract_major(parent_seq)
    if parent_major is None:
        return False

    children = []
    for phase in phases:
        seq = str(phase.get("seq", phase.get("phase_seq", "")))
        phase_normalized = _normalize_seq(seq)
        phase_major = _extract_major(seq)

        # Exact match (e.g., Phase "3.0" exists)
        if phase_normalized == parent_normalized:
            children.append(phase)
        # Child match (e.g., 3.1, 3.2, ..., 3.6)
        elif phase_major == parent_major:
            children.append(phase)

    if not children:
        return False

    return all(_is_done(p.get("status", "pending")) for p in children)


def resolve_pending_children(
    phases: List[Dict],
    parent_seq: str,
) -> List[str]:
    """Get only the PENDING (not-done) sub-phases of a parent.

    Useful for targeted re-delegation: "Phase 3.4-3.6 still need work."

    Args:
        phases: List of phase dicts from decomposition-index.json
        parent_seq: The parent phase sequence (e.g., "3.0")

    Returns:
        List of pending child phase seq strings.
    """
    parent_major = _extract_major(parent_seq)
    if parent_major is None:
        return []

    pending = []
    for phase in phases:
        seq = str(phase.get("seq", phase.get("phase_seq", "")))
        phase_major = _extract_major(seq)
        if phase_major == parent_major and not _is_done(phase.get("status", "pending")):
            pending.append(seq)

    return pending


def resolve_phases_by_category(
    phases: List[Dict],
    category: str,
    status_filter: str = "pending",
) -> List[str]:
    """Find phases by their title-inferred category.

    This is the SKILL-AGNOSTIC path. For projects without numbered phases
    (or with only sparse numbers), the phase title is the primary identifier.
    infer_phase_category() uses keyword matching on titles to determine
    if a phase is planning, implementation, verification, etc.

    Args:
        phases: List of phase dicts from decomposition-index.json
        category: The lifecycle category to match (e.g., "implementation")
        status_filter: Only return phases with this status. Use "*" for all.

    Returns:
        List of matching phase seq strings.
    """
    matched = []
    for phase in phases:
        title = phase.get("title", "")
        phase_cat = _get_phase_category(title)
        if phase_cat.lower() == category.lower():
            if status_filter == "*" or phase.get("status", "pending") == status_filter:
                seq = str(phase.get("seq", phase.get("phase_seq", "")))
                if seq:
                    matched.append(seq)

    return matched


def resolve_completed_phases(
    phases: List[Dict],
    detected_phase: Any = None,
    requirement_ids: List[str] = None,
    delegation_profile: str = "",
    delegation_status: str = "success",
) -> List[str]:
    """Universal phase resolution — the MAIN entry point.

    Tries all strategies in priority order to find which decomposition
    phases should be marked as completed after a delegation returns.

    Priority 1: requirement_ids overlap (definitive — structured data)
    Priority 2: exact phase seq match
    Priority 3: parent-to-child resolution (3.0 → 3.1, 3.2, ...)
    Priority 4: category-based matching (title → category → profile compat)

    Profile filtering: implementation phases can only be completed by
    code profiles, design phases by architect/frontend, etc.

    Args:
        phases: List of phase dicts from decomposition-index.json
        detected_phase: Phase number from detect_delegation_phase() — may be
            int, float, str, or None
        requirement_ids: Requirement IDs from delegation kwargs
        delegation_profile: Profile of the subordinate agent (code, architect, etc.)
        delegation_status: Delegation result status (success, partial, failed)

    Returns:
        List of phase seq strings that should be marked completed.
    """
    requirement_ids = requirement_ids or []
    resolved = []

    # ── Priority 1: Requirement-ID resolution (definitive) ──
    if requirement_ids:
        resolved = resolve_phases_by_requirement_ids(phases, requirement_ids)
        if resolved:
            logger.info(
                f"[PHASE RESOLUTION] P1 req_ids matched {len(resolved)} phases: "
                f"{resolved} (from {len(requirement_ids)} req_ids)"
            )

    # ── Priority 2: Exact phase seq match ──
    if not resolved and detected_phase is not None:
        target_normalized = _normalize_seq(detected_phase)
        for phase in phases:
            phase_normalized = _normalize_seq(phase.get("seq", ""))
            if phase_normalized == target_normalized:
                if not _is_done(phase.get("status", "pending")):
                    resolved = [str(phase.get("seq", ""))]
                    logger.info(
                        f"[PHASE RESOLUTION] P2 exact match: {resolved[0]}"
                    )
                break

    # ── Priority 3: Parent-to-child resolution ──
    if not resolved and detected_phase is not None:
        children = resolve_parent_to_children(phases, str(detected_phase))
        if children:
            # Filter to pending-only
            pending_children = [
                seq for seq in children
                if not _is_done(
                    next(
                        (p.get("status", "pending") for p in phases
                         if str(p.get("seq", "")) == seq),
                        "pending"
                    )
                )
            ]
            if pending_children:
                resolved = pending_children
                logger.info(
                    f"[PHASE RESOLUTION] P3 parent-child: detected={detected_phase} "
                    f"→ children={resolved}"
                )

    # ── Profile-category filtering ──
    # Implementation phases can only be completed by code profiles, etc.
    if resolved and delegation_profile:
        filtered = []
        for seq in resolved:
            phase = next(
                (p for p in phases if str(p.get("seq", "")) == seq),
                None
            )
            if phase is None:
                continue
            title = phase.get("title", "")
            category = _get_phase_category(title)
            if _is_profile_compatible(delegation_profile, category):
                filtered.append(seq)
            else:
                logger.info(
                    f"[PHASE RESOLUTION] Filtered out phase {seq} — "
                    f"profile '{delegation_profile}' can't complete "
                    f"category '{category}' (title: '{title}')"
                )
        resolved = filtered

    return resolved
