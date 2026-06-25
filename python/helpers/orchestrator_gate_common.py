"""Orchestrator Utilities — shared functions for orchestration logic.

Formerly orchestrator_gate_common.py (~1,274 lines of gate code).
Reduced to utility functions that are still used by non-gate consumers.
"""

import logging
from typing import Optional

logger = logging.getLogger("agix.orchestrator_utils")


# ── Constants ───────────────────────────────────────────────────────

# Used by supervisor and integration checks
MAX_INTEGRATION_BLOCKS = 5


# ── Utility Functions ───────────────────────────────────────────────

def format_gate_block(
    check_name: str = "",
    block_count: int = 0,
    max_blocks: int = 3,
    details: str = "",
    **kwargs,
) -> str:
    """Format an advisory check message.

    Used by checks/ directory to format quality check messages.
    """
    reason = kwargs.get("reason", check_name)
    action = kwargs.get("action", "")
    return (
        f"[ADVISORY: {reason or check_name}] "
        f"(block {block_count}/{max_blocks}) "
        f"{details} {action}".strip()
    )


def resolve_project_dir_from_context(agent_data: dict) -> Optional[str]:
    """Get the active project directory from agent data."""
    return agent_data.get("_active_project_dir") or agent_data.get("project_dir")


def build_verification_warning(project_dir: str = "") -> str:
    """Advisory: verification recommended."""
    return f"[ADVISORY] Verification recommended for {project_dir}"


def build_browser_uat_warning(project_dir: str = "") -> str:
    """Advisory: browser UAT recommended."""
    return f"[ADVISORY] Browser UAT recommended for {project_dir}"


def detect_force_accepted_result(result_text: str) -> bool:
    """Check if a delegation result was force-accepted."""
    if not result_text:
        return False
    return "[FORCE_ACCEPTED]" in result_text or "[ESCAPE_HATCH]" in result_text


def get_total_delegation_count(agent_data: dict) -> int:
    """Get total delegation count from agent data."""
    return agent_data.get("_delegation_count", 0)
