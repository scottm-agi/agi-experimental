"""
Route Remediation — Inject failing routes as requirements.

RCA 214 P1: When route reachability fails, the gate blocks broadly with
"routes not reachable" — the orchestrator doesn't know WHICH routes to fix
or HOW to delegate targeted fixes.

Fix: Inject each failing route as a named requirement in the requirements_ledger
so the orchestrator can delegate route-specific fix tasks.
"""
from __future__ import annotations

import logging
from python.helpers.requirements_ledger import _ensure_ledger

logger = logging.getLogger("agix.route_remediation")


def inject_route_remediation(agent_data: dict, failing_routes: list[str]) -> None:
    """Inject each failing route as a remediation requirement.

    Idempotent: won't create duplicate requirements for the same route.

    Args:
        agent_data: The agent's data dict (contains requirements_ledger).
        failing_routes: List of route paths that failed reachability.
    """
    ledger = _ensure_ledger(agent_data)
    reqs = ledger.get("requirements", [])
    existing_ids = {r.get("id", "") for r in reqs}

    next_id = len(reqs) + 1
    for route in failing_routes:
        req_id = f"ROUTE-FIX-{route.strip('/').replace('/', '-').upper()}"
        if req_id in existing_ids:
            logger.info(f"[ROUTE REMEDIATION] {req_id} already in ledger — skipping")
            continue

        reqs.append({
            "id": req_id,
            "text": f"Fix failing route: {route} — ensure page.tsx exists and renders content",
            "category": "route_fix",
            "status": "pending",
            "assigned_to": [],
            "source": "route_remediation",
        })
        existing_ids.add(req_id)
        next_id += 1
        logger.info(f"[ROUTE REMEDIATION] Injected {req_id} for route {route}")



def build_route_remediation_message(failing_routes: list[str]) -> str:
    """Build a structured remediation message listing each failing route.

    Args:
        failing_routes: List of route paths that failed.

    Returns:
        Formatted remediation message with per-route fix instructions.
    """
    route_items = "\n".join(
        f"  - `{route}` → Create/fix `src/app{route}/page.tsx`"
        for route in failing_routes[:8]
    )
    return (
        f"⛔ ROUTE REACHABILITY FAILED — {len(failing_routes)} route(s) not reachable\n\n"
        f"### Failing Routes\n{route_items}\n\n"
        f"### FIX\n"
        f"Delegate TARGETED fixes — one subordinate per route group:\n"
        f"1. Each delegation should specify the exact route(s) to fix\n"
        f"2. Include `requirement_ids` with the ROUTE-FIX-* IDs above\n"
        f"3. The subordinate must create the page.tsx AND verify it renders\n"
    )
