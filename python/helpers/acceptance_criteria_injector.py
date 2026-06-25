"""
Acceptance Criteria Injector — Enrich delegation messages with requirement details.

RCA-232 Fix 5 (MISS 4 & 7): Requirement IDs in batch tasks are decorative
metadata. Sub-agents only see the `message` field. The requirement text
("use Resend SDK", "CAN-SPAM unsubscribe") never makes it into the
actionable task context.

This module resolves REQ IDs against the in-memory requirements ledger
and injects human-readable acceptance criteria into the delegation message.

Usage:
    from python.helpers.acceptance_criteria_injector import inject_acceptance_criteria
    enriched = inject_acceptance_criteria(message, req_ids, agent_data)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("agix.acceptance_criteria_injector")


def inject_acceptance_criteria(
    message: str,
    requirement_ids: Optional[List[str]],
    agent_data: Dict,
) -> str:
    """Inject acceptance criteria from requirement IDs into a delegation message.

    Looks up each REQ ID in the in-memory requirements ledger, extracts the
    requirement text and success criteria, and appends a formatted
    ACCEPTANCE CRITERIA block to the message.

    Args:
        message: The original delegation message.
        requirement_ids: List of requirement IDs (e.g., ["REQ-001", "REQ-002"]).
        agent_data: The agent's data dict containing _requirements_ledger.

    Returns:
        The enriched message with acceptance criteria appended, or the
        original message if no criteria could be resolved.
    """
    if not requirement_ids:
        return message

    # Get the ledger from agent data
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return message

    requirements = ledger.get("requirements", [])
    if not requirements:
        return message

    # Build a normalized lookup map (handles REQ-1 → REQ-001 mismatches)
    from python.helpers.req_id_normalizer import build_normalized_req_map
    req_map = build_normalized_req_map(requirements)

    # Resolve criteria for each ID
    criteria_blocks: List[str] = []
    for req_id in requirement_ids:
        req = req_map.get(req_id)
        if not req:
            continue

        req_text = req.get("text", "")
        category = req.get("category", "feature")
        success_criteria = req.get("success_criteria", [])

        block_lines = [
            f"### {req_id} [{category}]",
            f"**Requirement**: {req_text}",
        ]

        if success_criteria:
            block_lines.append("**Success Criteria**:")
            for criterion in success_criteria:
                block_lines.append(f"  - {criterion}")

        criteria_blocks.append("\n".join(block_lines))

    if not criteria_blocks:
        return message

    # Compose the enriched message
    criteria_section = (
        "\n\n---\n"
        "## 📋 ACCEPTANCE CRITERIA (from Requirements Ledger)\n\n"
        "The following requirements MUST be fully implemented. "
        "Do NOT stub, skip, or defer any of these.\n\n"
        + "\n\n".join(criteria_blocks)
        + "\n---\n"
    )

    return message + criteria_section
