"""
Base types and constants for the task scheduler.

This module contains:
- TaskState and TaskType enums
- SCHEDULER_FOLDER constant
- Common imports
"""

from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING

# Constants
SCHEDULER_FOLDER = "data/scheduler"


class TaskState(str, Enum):
    """State of a scheduled task."""
    IDLE = "idle"
    RUNNING = "running"
    DISABLED = "disabled"
    ERROR = "error"


class TaskType(str, Enum):
    """Type of a scheduled task."""
    AD_HOC = "adhoc"
    SCHEDULED = "scheduled"
    PLANNED = "planned"


# Re-export for convenience
__all__ = [
    "SCHEDULER_FOLDER",
    "TaskState",
    "TaskType",
]