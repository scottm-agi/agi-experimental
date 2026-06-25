"""
Gate Router — routes checks to 3 stage-boundary gates.

Each check declares its gate via @register_check(..., gate="bdd").
This router selects which checks to run at each pipeline stage.

The router reads the gate= TAG, not priority numbers. Numbers are
assigned in skill files and change across profiles/project types.
New project types with emergent checks automatically route via their tag.

Gates:
    bdd  — fires after Phase 2 (spec generation), BEFORE code delegation
    tdd  — fires after Phase 3 (implementation), BEFORE verification
    done — fires after Phase 4/5 (verification), BEFORE response

Escape mechanism:
    Per-requirement partial status. After 3 failed attempts on the same
    requirement, it's accepted as partial. No more silent force-allow.
"""

import logging
from typing import Optional

logger = logging.getLogger("agix.gate_router")

VALID_GATES = frozenset({"bdd", "tdd", "done"})

# Maximum attempts per requirement before accepting as partial
MAX_PARTIAL_ATTEMPTS = 3


def _get_or_create_gate_sm(agent_data: dict, gate: str) -> 'GateProgressSM':
    """Get or create a GateProgressSM for the given gate.

    RCA-475 C3: Wraps existing boolean gate flags with a validated SM
    that provides transition validation and audit trail.

    RCA-479 Fix: Handles corrupted SM entries from JSON round-trip
    (DelegationSM/GateProgressSM are not JSON serializable — ``default=str``
    turns them into ``"<GateProgressSM object at 0x…>"``).
    """
    from python.helpers.state_machines.gate_sm import GateProgressSM
    sms = agent_data.setdefault("_state_machines", {})
    key = f"gate_{gate}"
    existing = sms.get(key)
    if not isinstance(existing, GateProgressSM):
        sms[key] = GateProgressSM(entity_id=gate)
    return sms[key]


def get_checks_for_gate(gate: str) -> list:
    """Return all checks tagged with the given gate name.

    Checks with gate="" run in ALL gates (backward compat for checks
    that haven't been tagged yet during migration).

    Args:
        gate: "bdd", "tdd", or "done"

    Returns:
        Sorted list of IntegrationCheck objects for this gate.
    """
    from python.helpers.orchestrator_gate_integration_checks import CHECK_REGISTRY

    if gate not in VALID_GATES:
        logger.error(f"[GATE_ROUTER] Invalid gate '{gate}' — must be one of {VALID_GATES}")
        return []

    return [
        check for check in CHECK_REGISTRY
        if check.gate == gate or check.gate == ""  # "" = runs in all gates
    ]


def _should_auto_pass_done_gate(agent_data: dict) -> bool:
    """Check if the done gate should auto-pass based on phase_cap scope.

    The done gate verifies INTEGRATION/VERIFICATION/DELIVERY outcomes.
    If the agent's phase_cap restricts execution to IMPLEMENTATION (Phase 3)
    or earlier, those outcomes will never exist — auto-pass the done gate.

    Uses PhaseCategory enum (not hardcoded phase numbers) because the
    phase numbering is defined in skills/profiles and can change.

    Returns:
        True if done gate should auto-pass (scope doesn't include done-gate phases).
    """
    from python.helpers.phase_category import get_phase_category, PhaseCategory

    phase_cap = agent_data.get("_phase_cap")
    if phase_cap is None:
        return False  # No cap = full run, done gate must fire

    cap_category = get_phase_category(phase_cap)
    if cap_category is None:
        return False  # Unknown category, let gate fire normally

    # Done gate checks require INTEGRATION or later phases.
    # If cap is PLANNING, DESIGN, or IMPLEMENTATION, auto-pass.
    DONE_GATE_NOT_NEEDED = {
        PhaseCategory.PLANNING,
        PhaseCategory.DESIGN,
        PhaseCategory.IMPLEMENTATION,
    }
    return cap_category in DONE_GATE_NOT_NEEDED


def get_current_gate(agent_data: dict) -> Optional[str]:
    """Determine which gate should fire based on current pipeline stage.

    Reads gate-passed flags from agent_data to determine the next gate.
    Gates fire in order: bdd → tdd → done. Once all 3 pass, returns None.

    Scope awareness (RCA-475): When _phase_cap limits execution to
    IMPLEMENTATION or earlier, the done gate auto-passes because its
    checks verify Phase 4/5 activities that will never execute.

    Returns:
        "bdd", "tdd", "done", or None (all gates passed).
    """
    if not agent_data.get("_bdd_gate_passed", False):
        return "bdd"
    if not agent_data.get("_tdd_gate_passed", False):
        return "tdd"
    if not agent_data.get("_done_gate_passed", False):
        # P0 (RCA-475): Scope-aware done gate skip.
        # The done gate checks verify Phase 4/5 (INTEGRATION/VERIFICATION/DELIVERY)
        # activities. When _phase_cap restricts execution to IMPLEMENTATION or
        # earlier, those activities will never be performed — so the done gate
        # should auto-pass. Uses PhaseCategory (not hardcoded numbers) because
        # phase numbers are defined in skills and can change.
        if _should_auto_pass_done_gate(agent_data):
            agent_data["_done_gate_passed"] = True
            # RCA-475 C3: SM wrap for auto-pass
            sm = _get_or_create_gate_sm(agent_data, "done")
            if sm.status != "passed":
                ok, msg = sm.transition("passed", reason="auto-pass: phase_cap below done-gate scope", source="gate_router.get_current_gate")
                if not ok:
                    logger.warning("[GATE_ROUTER] SM transition rejected for auto-pass done gate: %s", msg)
            logger.info(
                "[GATE_ROUTER] Done gate AUTO-PASSED — phase_cap=%s is at or below "
                "IMPLEMENTATION category (no verification/delivery phases will execute)",
                agent_data.get("_phase_cap"),
            )
            return None  # All gates passed
        return "done"
    return None  # All gates passed


def mark_gate_passed(agent_data: dict, gate: str) -> None:
    """Mark a gate as passed so subsequent response attempts
    skip to the next gate."""
    agent_data[f"_{gate}_gate_passed"] = True
    logger.info(f"[GATE_ROUTER] Gate '{gate}' PASSED — will not re-fire")

    # RCA-475 C3: SM wrap — create/transition GateProgressSM alongside boolean
    sm = _get_or_create_gate_sm(agent_data, gate)
    if sm.status != "passed":  # idempotent: skip if already passed
        ok, msg = sm.transition("passed", reason="gate passed", source="gate_router.mark_gate_passed")
        if not ok:
            logger.warning("[GATE_ROUTER] SM transition rejected for gate '%s': %s", gate, msg)


def reset_gates(agent_data: dict) -> None:
    """Reset all gate-passed flags (e.g., on new project or phase restart)."""
    for gate in VALID_GATES:
        agent_data.pop(f"_{gate}_gate_passed", None)

    # RCA-475 C3: Remove gate SMs so fresh ones are created on next pass
    sms = agent_data.get("_state_machines", {})
    for gate in VALID_GATES:
        sms.pop(f"gate_{gate}", None)

    logger.info("[GATE_ROUTER] All gates reset")
