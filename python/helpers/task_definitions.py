"""
Task Definitions Module
Defines supervision levels for different task types.
Part of Supervisor Reliability Enhancement - Gap 4.
"""

from enum import Enum
from typing import Dict


class SupervisionLevel(Enum):
    """Supervision levels for tasks."""
    NONE = "none"           # No supervision (truly ephemeral)
    MINIMAL = "minimal"     # Log only, no intervention
    STANDARD = "standard"   # Normal supervision (current behavior for non-TASK)
    ENHANCED = "enhanced"   # Stricter timeouts, mandatory heartbeats


# Default supervision levels for known task types
TASK_SUPERVISION_DEFAULTS: Dict[str, SupervisionLevel] = {
    # Scheduled automation tasks - need enhanced supervision
    "github_triage": SupervisionLevel.ENHANCED,
    "forgejo_triage": SupervisionLevel.ENHANCED,
    "comment_sweeper": SupervisionLevel.ENHANCED,
    "sweep_for_responses": SupervisionLevel.ENHANCED,
    "analysis_runner": SupervisionLevel.ENHANCED,
    "build_monitor": SupervisionLevel.ENHANCED,
    "repository_automation": SupervisionLevel.ENHANCED,
    
    # Short utility tasks - minimal supervision
    "quick_summary": SupervisionLevel.MINIMAL,
    "format_check": SupervisionLevel.MINIMAL,
    "syntax_check": SupervisionLevel.MINIMAL,
    
    # Subordinate agent tasks - standard supervision
    "call_subordinate": SupervisionLevel.STANDARD,
    "delegate_task": SupervisionLevel.STANDARD,
    
    # Default for unknown tasks
    "default": SupervisionLevel.STANDARD
}


# Timeout overrides for enhanced tasks (in seconds)
ENHANCED_TASK_TIMEOUTS: Dict[str, int] = {
    "github_triage": 60,     # Stricter timeout
    "forgejo_triage": 60,
    "comment_sweeper": 45,
    "analysis_runner": 90,
    "build_monitor": 60,
}


# Heartbeat interval for enhanced tasks (in seconds)
HEARTBEAT_INTERVAL = 60


def get_task_supervision_level(task_name: str) -> SupervisionLevel:
    """
    Get the supervision level for a task by name.
    
    Args:
        task_name: The name or identifier of the task
        
    Returns:
        SupervisionLevel for the task
    """
    if not task_name:
        return TASK_SUPERVISION_DEFAULTS["default"]
    
    task_name_lower = task_name.lower()
    
    # Check for exact match
    if task_name_lower in TASK_SUPERVISION_DEFAULTS:
        return TASK_SUPERVISION_DEFAULTS[task_name_lower]
    
    # Check for partial match
    for key, level in TASK_SUPERVISION_DEFAULTS.items():
        if key != "default" and key in task_name_lower:
            return level
    
    return TASK_SUPERVISION_DEFAULTS["default"]


def get_task_timeout(task_name: str, default_timeout: int = 90) -> int:
    """
    Get the timeout for a task.
    
    Args:
        task_name: The name or identifier of the task
        default_timeout: Default timeout if no override exists
        
    Returns:
        Timeout in seconds
    """
    if not task_name:
        return default_timeout
    
    task_name_lower = task_name.lower()
    
    # Check for exact match
    if task_name_lower in ENHANCED_TASK_TIMEOUTS:
        return ENHANCED_TASK_TIMEOUTS[task_name_lower]
    
    # Check for partial match
    for key, timeout in ENHANCED_TASK_TIMEOUTS.items():
        if key in task_name_lower:
            return timeout
    
    return default_timeout


def is_enhanced_supervision(task_name: str) -> bool:
    """Check if a task requires enhanced supervision."""
    return get_task_supervision_level(task_name) == SupervisionLevel.ENHANCED


def should_skip_supervision(task_name: str) -> bool:
    """Check if a task should skip supervision entirely."""
    return get_task_supervision_level(task_name) == SupervisionLevel.NONE


def should_log_only(task_name: str) -> bool:
    """Check if a task should only log signals (no intervention)."""
    return get_task_supervision_level(task_name) == SupervisionLevel.MINIMAL
