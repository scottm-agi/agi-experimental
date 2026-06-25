"""
Manifest Assignment — track per-delegation manifest items.

RCA 214 P2: The orchestrator should assign specific manifest items to each
delegation and track completion per-item, not just overall percentage.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("agix.manifest_assignment")


def assign_items_to_delegation(
    agent_data: dict,
    delegation_id: str,
    items: list[str],
) -> None:
    """Assign manifest items (requirement IDs) to a specific delegation.

    Args:
        agent_data: The agent's data dict.
        delegation_id: Unique ID of the delegation.
        items: List of requirement IDs (e.g., ["REQ-001", "REQ-003"]).
    """
    assignments = agent_data.setdefault("_manifest_assignments", {})
    assignments[delegation_id] = items
    logger.info(
        f"[MANIFEST] Assigned {len(items)} items to delegation {delegation_id}: "
        f"{', '.join(items)}"
    )


def get_unassigned_items(agent_data: dict) -> list[dict]:
    """Return requirements NOT yet assigned to any delegation.

    Args:
        agent_data: The agent's data dict.

    Returns:
        List of unassigned requirement dicts from the ledger.
    """
    assignments = agent_data.get("_manifest_assignments", {})
    assigned_ids = set()
    for items in assignments.values():
        assigned_ids.update(items)

    # Support the structured ledger format (dict with 'requirements' key)
    ledger = agent_data.get("_requirements_ledger", {})
    if isinstance(ledger, dict):
        reqs = ledger.get("requirements", [])
    else:
        reqs = ledger  # Legacy flat list fallback
    return [r for r in reqs if r.get("id") not in assigned_ids]


def mark_item_complete(agent_data: dict, item_id: str) -> None:
    """Mark a manifest item as complete.

    Args:
        agent_data: The agent's data dict.
        item_id: The requirement ID to mark complete.
    """
    completed = agent_data.setdefault("_manifest_completed", set())
    completed.add(item_id)
    logger.info(f"[MANIFEST] Marked {item_id} complete")


def get_completion_status(agent_data: dict) -> dict:
    """Get overall manifest completion status.

    Returns:
        Dict with 'total', 'completed', 'assigned', 'unassigned' counts.
    """
    # Support the structured ledger format
    ledger_raw = agent_data.get("_requirements_ledger", {})
    if isinstance(ledger_raw, dict):
        ledger = ledger_raw.get("requirements", [])
    else:
        ledger = ledger_raw

    completed = agent_data.get("_manifest_completed", set())
    assignments = agent_data.get("_manifest_assignments", {})

    assigned_ids = set()
    for items in assignments.values():
        assigned_ids.update(items)

    total = len(ledger)
    completed_count = len(completed & {r["id"] for r in ledger})

    return {
        "total": total,
        "completed": completed_count,
        "assigned": len(assigned_ids),
        "unassigned": total - len(assigned_ids),
    }
