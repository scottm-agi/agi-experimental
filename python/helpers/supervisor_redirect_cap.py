"""
Supervisor Redirect Cap

Prevents infinite supervisor redirect loops when the quality gate, duplicate
detector, and supervisor intervention system compete without coordination.

Root Cause (5-Why, Iteration 10b):
After the quality gate escape hatch fires and allows a response through,
the supervisor detects "stuckness" and injects redirect messages. Each
redirect causes the agent to produce near-identical content, which the
duplicate detector kills, causing the supervisor to redirect again — loop.

Fix: Cap consecutive supervisor redirects at SUPERVISOR_REDIRECT_CAP.
After that, suppress further redirects to let the response through.
"""

import logging

logger = logging.getLogger("agix.supervisor_redirect_cap")

from python.helpers.thresholds_registry import Thresholds

# P0-4 / Systems Audit C-4: Lowered from 3 to 2.
# After this many consecutive supervisor redirects without a successful
# user-facing response, suppress further redirects to break the loop.
SUPERVISOR_REDIRECT_CAP = Thresholds.SUPERVISOR_REDIRECT_CAP


def increment_redirect_counter(agent_data: dict) -> int:
    """Increment the consecutive supervisor redirect counter.

    Returns:
        The new counter value.
    """
    count = agent_data.get("_consecutive_supervisor_redirects", 0) + 1
    agent_data["_consecutive_supervisor_redirects"] = count
    if count >= SUPERVISOR_REDIRECT_CAP:
        logger.warning(
            f"[REDIRECT_CAP] Supervisor redirect #{count} "
            f"(cap={SUPERVISOR_REDIRECT_CAP}) — will suppress further redirects"
        )
    return count


def should_suppress_redirect(agent_data: dict) -> bool:
    """Check if a supervisor redirect should be suppressed.

    Returns:
        True if the redirect should be suppressed (counter >= cap)
        False if the redirect should proceed normally
    """
    count = agent_data.get("_consecutive_supervisor_redirects", 0)
    return count >= SUPERVISOR_REDIRECT_CAP


def reset_redirect_counter(agent_data: dict) -> None:
    """Reset the consecutive supervisor redirect counter.

    Call this when:
    - A new user message arrives (new chat turn)
    - A successful response is sent (break_loop=True)
    """
    agent_data["_consecutive_supervisor_redirects"] = 0

from typing import Optional


def get_enforcement_context(agent_data: dict) -> Optional[str]:
    """Build a human-readable summary of active tool enforcement state.

    RCA-301 Issue 4: When the supervisor redirects an agent, the redirect
    message must include awareness of which tools are blocked or restricted.
    Without this, the supervisor might tell the agent to "try writing the file
    differently" while the surgical edit enforcer is about to block write_to_file.

    Args:
        agent_data: The agent's data dict containing enforcement state keys.

    Returns:
        A string describing active constraints, or None if no constraints active.
    """
    lines = []

    # Check surgical edit strikes
    strikes = agent_data.get("_surgical_edit_strikes", {})
    if strikes:
        active_strikes = {k: v for k, v in strikes.items() if v > 0}
        if active_strikes:
            files_str = ", ".join(
                f"`{k.split('/')[-1]}` ({v} strikes)"
                for k, v in active_strikes.items()
            )
            lines.append(
                f"SURGICAL EDIT ENFORCER: The following files have write_to_file "
                f"strikes: {files_str}. Use `replace_in_file` for surgical edits "
                f"instead of full-file rewrites."
            )

    # Check blocked tools
    blocked = agent_data.get("_tracker_blocked_tools", set())
    if blocked:
        blocked_str = ", ".join(f"`{t}`" for t in sorted(blocked))
        lines.append(
            f"BLOCKED TOOLS: {blocked_str} are temporarily blocked due to "
            f"repeated failures. Use alternative tools or approaches."
        )

    if not lines:
        return None

    return "\n".join(lines)
