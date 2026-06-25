"""
Escalation Protocol — Structured Subordinate Failure Routing

When a subordinate agent exhausts its fix strategies, it emits a structured
[ESCALATE] report. This module:

  1. Parses the structured escalation report from result text
  2. Stores escalation records in agent.data["_escalation_reports"]
  3. Provides query functions for gate checks

The orchestrator uses this to route failures to the `debug` agent for 5-Why
RCA instead of endlessly retrying the same approach.

Architecture:
    - parse_escalation_report() → extracts structured fields from [ESCALATE] block
    - record_escalation() → stores in agent.data
    - get_pending_escalations() → returns unresolved escalations
    - has_unresolved_escalations() → bool for gate checks
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.escalation_protocol")


# ── Parsing ──────────────────────────────────────────────────────────────

# Field patterns in the structured escalation report
_FIELD_PATTERNS = {
    "task": re.compile(r"\*\*Task\*\*:\s*(.+)", re.IGNORECASE),
    "attempts": re.compile(r"\*\*Attempts\*\*:\s*(.+)", re.IGNORECASE),
    "root_blocker": re.compile(r"\*\*Root Blocker\*\*:\s*(.+)", re.IGNORECASE),
    "what_worked": re.compile(r"\*\*What Worked\*\*:\s*(.+)", re.IGNORECASE),
    "what_failed": re.compile(r"\*\*What Failed\*\*:\s*(.+)", re.IGNORECASE),
    "suggested_next_step": re.compile(r"\*\*Suggested Next Step\*\*:\s*(.+)", re.IGNORECASE),
}

_ESCALATION_MARKER = "[ESCALATE]"


def has_escalation_marker(text: str) -> bool:
    """Check if text contains the [ESCALATE] marker."""
    if not text:
        return False
    return _ESCALATION_MARKER in text


def parse_escalation_report(text: str) -> Optional[Dict[str, str]]:
    """Parse a structured [ESCALATE] report from result text.

    Looks for the [ESCALATE] marker, then extracts **Field**: Value lines.

    Args:
        text: The full result text from a subordinate delegation.

    Returns:
        Dict of parsed fields if structured format found.
        Empty dict {} if [ESCALATE] marker present but no structured fields.
        None if no [ESCALATE] marker at all.
    """
    if not text or _ESCALATION_MARKER not in text:
        return None

    # Extract text after [ESCALATE] marker
    marker_idx = text.index(_ESCALATION_MARKER)
    escalation_text = text[marker_idx:]

    # Try to extract structured fields
    fields = {}
    for field_name, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(escalation_text)
        if match:
            fields[field_name] = match.group(1).strip()

    if not fields:
        return {}

    return fields


# ── State Management ─────────────────────────────────────────────────────

def _ensure_escalation_store(agent_data: dict) -> list:
    """Ensure _escalation_reports exists in agent_data and return it."""
    if "_escalation_reports" not in agent_data:
        agent_data["_escalation_reports"] = []
    return agent_data["_escalation_reports"]


def record_escalation(
    agent_data: dict,
    profile: str,
    report: Dict[str, str],
    delegation_id: str = "",
) -> None:
    """Record an escalation report in agent_data.

    Args:
        agent_data: The agent.data dict
        profile: Agent profile that escalated (e.g., "code")
        report: Parsed escalation report dict
        delegation_id: Optional delegation ID for cross-referencing
    """
    store = _ensure_escalation_store(agent_data)

    store.append({
        "profile": profile,
        "report": report,
        "delegation_id": delegation_id,
        "resolved": False,
        "resolution": "",
    })

    logger.warning(
        f"[ESCALATION] Recorded escalation from {profile}: "
        f"{report.get('task', 'unknown task')} — "
        f"blocker: {report.get('root_blocker', 'unknown')}"
    )


def resolve_escalation(
    agent_data: dict,
    index: int,
    resolution: str,
) -> bool:
    """Mark an escalation as resolved.

    Args:
        agent_data: The agent.data dict
        index: Index of the escalation to resolve
        resolution: Description of how it was resolved

    Returns:
        True if resolved, False if index out of range
    """
    store = _ensure_escalation_store(agent_data)
    if 0 <= index < len(store):
        store[index]["resolved"] = True
        store[index]["resolution"] = resolution
        return True
    return False


def get_pending_escalations(agent_data: dict) -> List[Dict]:
    """Get all unresolved escalation reports.

    Returns:
        List of escalation dicts where resolved == False
    """
    store = _ensure_escalation_store(agent_data)
    return [e for e in store if not e.get("resolved", False)]


def has_unresolved_escalations(agent_data: dict) -> bool:
    """Check if there are any unresolved escalations.

    Returns:
        True if at least one escalation is unresolved
    """
    return len(get_pending_escalations(agent_data)) > 0


def build_debug_routing_guidance(escalation: Dict) -> str:
    """Build routing guidance for the orchestrator to send to debug agent.

    When an escalation is detected, the orchestrator should delegate to the
    `debug` agent with this guidance for 5-Why RCA.

    Args:
        escalation: An escalation record dict

    Returns:
        Formatted routing guidance string
    """
    report = escalation.get("report", {})
    profile = escalation.get("profile", "unknown")

    return (
        f"## 🔍 ESCALATION RCA — Debug Agent Required\n\n"
        f"A `{profile}` agent has ESCALATED after exhausting fix strategies.\n\n"
        f"**Original Task**: {report.get('task', 'Unknown')}\n"
        f"**Attempts Made**: {report.get('attempts', 'Unknown')}\n"
        f"**Root Blocker**: {report.get('root_blocker', 'Unknown')}\n"
        f"**What Worked**: {report.get('what_worked', 'Unknown')}\n"
        f"**What Failed**: {report.get('what_failed', 'Unknown')}\n"
        f"**Agent's Suggestion**: {report.get('suggested_next_step', 'None')}\n\n"
        f"### Your Mission:\n"
        f"1. Perform a 5-Why root cause analysis on the **Root Blocker**\n"
        f"2. Investigate the system (read files, check configs, examine logs)\n"
        f"3. Propose a DIFFERENT approach that avoids the blocker entirely\n"
        f"4. Report your findings so the orchestrator can re-delegate with a new strategy\n\n"
        f"**DO NOT** retry the same approach the original agent tried.\n"
        f"**DO** find the root cause and propose a fundamentally different solution.\n"
    )
