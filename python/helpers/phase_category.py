"""Phase Category Registry — Maps phase sequence numbers to lifecycle categories.

Provides a PhaseCategory enum and a get_phase_category() mapper that
classifies any phase sequence (str, int, float) into one of 6 lifecycle
categories: PLANNING, DESIGN, IMPLEMENTATION, INTEGRATION, VERIFICATION,
DELIVERY.

Mapping (by integer part of the phase sequence):
    0 → PLANNING      (0, 0.1, 0.5, 0.5b)
    1 → DESIGN        (1, 1.x — Setup & Scaffold)
    2 → DESIGN        (2, 2.3, 2.5, 2.7, 2.8)
    3 → IMPLEMENTATION (3, 3.1, 3.5, 3.8, 3.9)
    4 → INTEGRATION   (4, 4.5, 4.7, 4.9)
    5 → VERIFICATION  (5, 5.1, 5.2)
    6 → DELIVERY      (6 — iteration/re-delivery)
    7 → DELIVERY      (7 — summary)

This module is DISTINCT from requirements_ledger.infer_phase_category(),
which classifies by phase TITLE (string matching). This module classifies
by phase NUMBER (integer part extraction).

Consumers:
    - orchestrator_gate_common.py (gate scoping)
    - gate_quality.py (gate suppression by category)
    - phase_parser.py (potential future use)
"""
from __future__ import annotations

import enum
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("agent.phase_category")


class PhaseCategory(enum.Enum):
    """Lifecycle category for a development phase."""

    PLANNING = "planning"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    INTEGRATION = "integration"
    VERIFICATION = "verification"
    DELIVERY = "delivery"


# ── Integer-part → Category mapping ──
# The integer part of the phase sequence determines the category.
# This dict is exported for external consumers that need the raw mapping.
PHASE_CATEGORIES: Dict[int, PhaseCategory] = {
    0: PhaseCategory.PLANNING,
    1: PhaseCategory.DESIGN,
    2: PhaseCategory.DESIGN,
    3: PhaseCategory.IMPLEMENTATION,
    4: PhaseCategory.INTEGRATION,
    5: PhaseCategory.VERIFICATION,
    6: PhaseCategory.DELIVERY,
    7: PhaseCategory.DELIVERY,
}

# Pre-compiled pattern: extract leading integer from sequences like "3", "2.5", "0.5b"
_LEADING_INT_RE = re.compile(r"^(\d+)")


def _extract_integer_part(phase_seq: Any) -> Optional[int]:
    """Extract the integer (major) part from a phase sequence value.

    Handles str ("3", "2.5", "0.5b"), int (3), float (2.5), None, empty.

    Args:
        phase_seq: Phase sequence value — string, int, float, or None.

    Returns:
        The integer part, or None if extraction fails.
    """
    if phase_seq is None:
        return None

    # Handle numeric types directly
    if isinstance(phase_seq, (int, float)):
        return int(phase_seq)

    # String path
    s = str(phase_seq).strip()
    if not s:
        return None

    m = _LEADING_INT_RE.match(s)
    if m:
        return int(m.group(1))

    return None


def get_phase_category(phase_seq: Any) -> Optional[PhaseCategory]:
    """Map a phase sequence to its lifecycle category.

    Extracts the integer part of the phase sequence and looks it up
    in PHASE_CATEGORIES. Returns None for unrecognised sequences.

    Args:
        phase_seq: Phase sequence — "3", "2.5", "0.5b", 3, 2.5, etc.

    Returns:
        PhaseCategory enum member, or None if the phase is unrecognised.
    """
    major = _extract_integer_part(phase_seq)
    if major is None:
        return None

    category = PHASE_CATEGORIES.get(major)
    if category is None:
        logger.debug(
            "[PHASE CATEGORY] Unrecognised phase major=%d (from %r)",
            major,
            phase_seq,
        )
    return category

def is_implementation_phase(phase_seq: Any) -> bool:
    """Convenience: check if a phase sequence is in the IMPLEMENTATION category.

    Phases 3, 3.1, 3.5, 3.8, 3.9 → True.

    Args:
        phase_seq: Phase sequence value.

    Returns:
        True if the phase is an implementation phase, False otherwise.
    """
    return get_phase_category(phase_seq) is PhaseCategory.IMPLEMENTATION


def is_planning_phase(phase_seq: Any) -> bool:
    """Check if phase is PLANNING (Phase 0, 0.1, 0.5, 0.5b).

    Use instead of: ``phase == 0`` or ``phase <= 0``
    """
    return get_phase_category(phase_seq) is PhaseCategory.PLANNING


def is_scaffold_phase(phase_seq: Any) -> bool:
    """Check if phase is DESIGN with major=1 (Phase 1 — Setup & Scaffold).

    Phase 1 is categorized as DESIGN in the enum, but it's specifically
    the scaffold/setup phase. This helper checks for major=1 specifically.

    Use instead of: ``phase == 1`` or ``phase <= 1``
    """
    return _extract_integer_part(phase_seq) == 1


def is_design_phase(phase_seq: Any) -> bool:
    """Check if phase is DESIGN (Phases 1, 1.x, 2, 2.3, 2.5, 2.7, 2.8).

    Includes both scaffold (Phase 1) and pre-implementation design (Phase 2).

    Use instead of: ``phase <= 2`` or ``phase < 3``
    """
    return get_phase_category(phase_seq) is PhaseCategory.DESIGN


def is_integration_phase(phase_seq: Any) -> bool:
    """Check if phase is INTEGRATION (Phase 4, 4.5, 4.7, 4.9).

    Use instead of: ``phase == 4`` or ``4 <= phase < 5``
    """
    return get_phase_category(phase_seq) is PhaseCategory.INTEGRATION


def is_verification_phase(phase_seq: Any) -> bool:
    """Check if phase is VERIFICATION (Phase 5, 5.1, 5.2).

    Use instead of: ``phase == 5`` or ``5 <= phase < 6``
    """
    return get_phase_category(phase_seq) is PhaseCategory.VERIFICATION


def is_delivery_phase(phase_seq: Any) -> bool:
    """Check if phase is DELIVERY (Phases 6, 7).

    Use instead of: ``phase >= 6``
    """
    return get_phase_category(phase_seq) is PhaseCategory.DELIVERY


def is_post_tdd_generation_phase(phase_seq: Any) -> bool:
    """Check if phase is AFTER TDD test generation (Phase >= 3).

    Phase 2.8 generates TDD test files. Phases 3+ (IMPLEMENTATION,
    INTEGRATION, VERIFICATION, DELIVERY) are all "post-TDD-generation"
    — meaning test files already exist on disk.

    Use instead of: ``phase >= 3``

    RCA-ITR55: This is the key check for deciding whether to tell the
    code agent "Make Existing Tests PASS" vs "Write Tests FIRST".
    """
    major = _extract_integer_part(phase_seq)
    if major is None:
        return False
    return major >= 3


def is_verification_or_later(phase_seq: Any) -> bool:
    """Check if phase is VERIFICATION or DELIVERY (Phase >= 5).

    Use instead of: ``phase >= 5``

    Used for restricting context injection, surgical fix mode,
    and cache invalidation during verify/fix stages.
    """
    major = _extract_integer_part(phase_seq)
    if major is None:
        return False
    return major >= 5
