"""
Scheduler package - Task scheduling system.

This package provides:
- Task models (BaseTask, AdHocTask, ScheduledTask, PlannedTask)
- Task schedule and plan models (TaskSchedule, TaskPlan)
- SchedulerTaskList for task management
- Serialization helpers

Usage:
    from python.helpers.scheduler import (
        TaskState, TaskType, SCHEDULER_FOLDER,
        TaskSchedule, TaskPlan,
        BaseTask, AdHocTask, ScheduledTask, PlannedTask,
        SchedulerTaskList,
        serialize_task, deserialize_task,
    )
"""

# Base types
from .base import (
    SCHEDULER_FOLDER,
    TaskState,
    TaskType,
)

# Task models
from .models import (
    TaskSchedule,
    TaskPlan,
    BaseTask,
    AdHocTask,
    ScheduledTask,
    PlannedTask,
    AnyTask,
)

# Task list management
from .task_list import (
    SchedulerTaskList,
)

# Serialization helpers
from .serialization import (
    serialize_datetime,
    parse_datetime,
    serialize_task_schedule,
    parse_task_schedule,
    serialize_task_plan,
    parse_task_plan,
    serialize_task,
    serialize_tasks,
    deserialize_task,
)


__all__ = [
    # Base types
    "SCHEDULER_FOLDER",
    "TaskState",
    "TaskType",
    # Task models
    "TaskSchedule",
    "TaskPlan",
    "BaseTask",
    "AdHocTask",
    "ScheduledTask",
    "PlannedTask",
    "AnyTask",
    # Task list management
    "SchedulerTaskList",
    # Serialization helpers
    "serialize_datetime",
    "parse_datetime",
    "serialize_task_schedule",
    "parse_task_schedule",
    "serialize_task_plan",
    "parse_task_plan",
    "serialize_task",
    "serialize_tasks",
    "deserialize_task",
]