"""Check SM Wiring — wire gate check results into GateProgressSM.

RCA-475 Category C: When a gate check passes, transition the related SM
to 'passed'. When it fails, the SM stays at 'pending' (not yet passed).

This is WRAP-only — SM errors never block checks (warn-only migration mode).
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger("agix.check_sm_wiring")


def transition_check_sm(
    agent_data: dict,
    check_id: str,
    passed: bool,
) -> None:
    """Wire a gate check pass/fail into a per-check GateProgressSM.

    RCA-475 C1/C3/C4/C5/C6: Each check gets its own SM under
    ``agent_data["_state_machines"]["check_<check_id>"]``.

    When *passed* is True, transitions SM pending → passed.
    When *passed* is False, SM stays pending (no transition needed).

    All SM errors are caught and logged — never blocks the check.

    Args:
        agent_data: The mutable agent.data dict.
        check_id:   Unique identifier for this check (e.g. ``"github_push_0.095"``).
        passed:     Whether the check passed (True) or failed (False).
    """
    try:
        from python.helpers.state_machines.gate_sm import GateProgressSM

        sms = agent_data.setdefault("_state_machines", {})  # type: Dict[str, GateProgressSM]
        key = f"check_{check_id}"

        if key not in sms or not isinstance(sms.get(key), GateProgressSM):
            sms[key] = GateProgressSM(entity_id=check_id)

        sm = sms[key]

        if passed and sm.status != "passed":
            ok, msg = sm.transition(
                "passed",
                reason=f"check {check_id} passed",
                source="check_sm_wiring",
            )
            if not ok:
                logger.warning(
                    "[CHECK SM] Transition failed for %s: %s — continuing anyway",
                    check_id, msg,
                )
        # When not passed, SM stays at current state (pending).
        # No transition needed — "pending" IS the not-yet-passed state.

    except Exception as exc:
        # Defensive — SM errors NEVER break check execution
        logger.debug("[CHECK SM] Error wiring %s: %s", check_id, exc)
