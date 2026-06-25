"""
Serialization helpers for task scheduler.

This module contains:
- serialize_datetime / parse_datetime
- serialize_task_schedule / parse_task_schedule
- serialize_task_plan / parse_task_plan
- serialize_task / serialize_tasks / deserialize_task
"""

from __future__ import annotations
import random
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Type, TypeVar, Union, cast

from .base import TaskState, TaskType
from .models import (
    TaskSchedule,
    TaskPlan,
    BaseTask,
    AdHocTask,
    ScheduledTask,
    PlannedTask,
    AnyTask,
)
from python.helpers.localization import Localization
from python.helpers.print_style import PrintStyle


# Type variable for task deserialization
T = TypeVar('T', bound=Union[ScheduledTask, AdHocTask, PlannedTask])


def serialize_datetime(dt: Optional[datetime]) -> Optional[str]:
    """
    Serialize a datetime object to ISO format string in the user's timezone.

    This uses the Localization singleton to convert the datetime to the user's timezone
    before serializing it to an ISO format string for frontend display.

    Returns None if the input is None.
    """
    # Use the Localization singleton for timezone conversion and serialization
    return Localization.get().serialize_datetime(dt)


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """
    Parse ISO format datetime string with timezone awareness.

    This converts from the localized ISO format returned by serialize_datetime
    back to a datetime object with proper timezone handling.

    Returns None if dt_str is None.
    """
    if not dt_str:
        return None

    try:
        # Use the Localization singleton for consistent timezone handling
        return Localization.get().localtime_str_to_utc_dt(dt_str)
    except ValueError as e:
        raise ValueError(f"Invalid datetime format: {dt_str}. Expected ISO format. Error: {e}")


def serialize_task_schedule(schedule: TaskSchedule) -> Dict[str, str]:
    """Convert TaskSchedule to a standardized dictionary format."""
    return {
        'minute': schedule.minute,
        'hour': schedule.hour,
        'day': schedule.day,
        'month': schedule.month,
        'weekday': schedule.weekday,
        'timezone': schedule.timezone
    }


def parse_task_schedule(schedule_data: Dict[str, str]) -> TaskSchedule:
    """Parse dictionary into TaskSchedule with validation."""
    try:
        return TaskSchedule(
            minute=schedule_data.get('minute', '*'),
            hour=schedule_data.get('hour', '*'),
            day=schedule_data.get('day', '*'),
            month=schedule_data.get('month', '*'),
            weekday=schedule_data.get('weekday', '*'),
            timezone=schedule_data.get('timezone', Localization.get().get_timezone())
        )
    except Exception as e:
        raise ValueError(f"Invalid schedule format: {e}") from e


def serialize_task_plan(plan: TaskPlan) -> Dict[str, Any]:
    """Convert TaskPlan to a standardized dictionary format."""
    return {
        'todo': [serialize_datetime(dt) for dt in plan.todo],
        'in_progress': serialize_datetime(plan.in_progress) if plan.in_progress else None,
        'done': [serialize_datetime(dt) for dt in plan.done]
    }


def parse_task_plan(plan_data: Dict[str, Any]) -> TaskPlan:
    """Parse dictionary into TaskPlan with validation."""
    try:
        # Handle case where plan_data might be None or empty
        if not plan_data:
            return TaskPlan(todo=[], in_progress=None, done=[])

        # Parse todo items with careful validation
        todo_dates = []
        for dt_str in plan_data.get('todo', []):
            if dt_str:
                parsed_dt = parse_datetime(dt_str)
                if parsed_dt:
                    # Ensure datetime is timezone-aware (use UTC if not specified)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    todo_dates.append(parsed_dt)

        # Parse in_progress with validation
        in_progress = None
        if plan_data.get('in_progress'):
            in_progress = parse_datetime(plan_data.get('in_progress'))
            # Ensure datetime is timezone-aware
            if in_progress and in_progress.tzinfo is None:
                in_progress = in_progress.replace(tzinfo=timezone.utc)

        # Parse done items with validation
        done_dates = []
        for dt_str in plan_data.get('done', []):
            if dt_str:
                parsed_dt = parse_datetime(dt_str)
                if parsed_dt:
                    # Ensure datetime is timezone-aware
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    done_dates.append(parsed_dt)

        # Sort dates for better usability
        todo_dates.sort()
        done_dates.sort(reverse=True)  # Most recent first for done items

        # Cast to ensure type safety
        todo_dates_cast: list[datetime] = cast(list[datetime], todo_dates)
        done_dates_cast: list[datetime] = cast(list[datetime], done_dates)

        return TaskPlan.create(
            todo=todo_dates_cast,
            in_progress=in_progress,
            done=done_dates_cast
        )
    except Exception as e:
        PrintStyle(italic=True, font_color="red", padding=False).print(
            f"Error parsing task plan: {e}"
        )
        # Return empty plan instead of failing
        return TaskPlan(todo=[], in_progress=None, done=[])


def serialize_task(task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> Dict[str, Any]:
    """
    Standardized serialization for task objects with proper handling of all complex types.
    """
    # Start with a basic dictionary
    task_dict = {
        "uuid": task.uuid,
        "name": task.name,
        "state": task.state,
        "system_prompt": task.system_prompt,
        "prompt": task.prompt,
        "attachments": task.attachments,
        "project_name": task.project_name,
        "project_color": task.project_color,
        "created_at": serialize_datetime(task.created_at),
        "updated_at": serialize_datetime(task.updated_at),
        "last_run": serialize_datetime(task.last_run),
        "next_run": serialize_datetime(task.get_next_run()),
        "last_result": task.last_result,
        "context_id": task.context_id,
        "dedicated_context": task.is_dedicated(),
        "scope": task.scope,
        "consecutive_errors": task.consecutive_errors,
        "project": {
            "name": task.project_name,
            "color": task.project_color,
        },
    }

    # Add type-specific fields
    if isinstance(task, ScheduledTask):
        task_dict['type'] = 'scheduled'
        task_dict['schedule'] = serialize_task_schedule(task.schedule)  # type: ignore
    elif isinstance(task, AdHocTask):
        task_dict['type'] = 'adhoc'
        adhoc_task = cast(AdHocTask, task)
        task_dict['token'] = adhoc_task.token
    else:
        task_dict['type'] = 'planned'
        planned_task = cast(PlannedTask, task)
        task_dict['plan'] = serialize_task_plan(planned_task.plan)  # type: ignore

    return task_dict


def serialize_tasks(tasks: list[Union[ScheduledTask, AdHocTask, PlannedTask]]) -> list[Dict[str, Any]]:
    """
    Serialize a list of tasks to a list of dictionaries.
    """
    return [serialize_task(task) for task in tasks]


def deserialize_task(task_data: Dict[str, Any], task_class: Optional[Type[T]] = None) -> T:
    """
    Deserialize dictionary into appropriate task object with validation.
    If task_class is provided, uses that type. Otherwise determines type from data.
    """
    task_type_str = task_data.get('type', '')
    determined_class = None

    if not task_class:
        # Determine task class from data
        if task_type_str == 'scheduled':
            determined_class = cast(Type[T], ScheduledTask)
        elif task_type_str == 'adhoc':
            determined_class = cast(Type[T], AdHocTask)
            # Ensure token is a valid non-empty string
            if not task_data.get('token'):
                task_data['token'] = str(random.randint(1000000000000000000, 9999999999999999999))
        elif task_type_str == 'planned':
            determined_class = cast(Type[T], PlannedTask)
        else:
            raise ValueError(f"Unknown task type: {task_type_str}")
    else:
        determined_class = task_class
        # If this is an AdHocTask, ensure token is valid
        if determined_class == AdHocTask and not task_data.get('token'):  # type: ignore
            task_data['token'] = str(random.randint(1000000000000000000, 9999999999999999999))

    common_args = {
        "uuid": task_data.get("uuid"),
        "name": task_data.get("name"),
        "state": TaskState(task_data.get("state", TaskState.IDLE)),
        "system_prompt": task_data.get("system_prompt", ""),
        "prompt": task_data.get("prompt", ""),
        "attachments": task_data.get("attachments", []),
        "project_name": task_data.get("project_name"),
        "project_color": task_data.get("project_color"),
        "created_at": parse_datetime(task_data.get("created_at")),
        "updated_at": parse_datetime(task_data.get("updated_at")),
        "last_run": parse_datetime(task_data.get("last_run")),
        "last_result": task_data.get("last_result"),
        "context_id": task_data.get("context_id"),
        "scope": task_data.get("scope"),
        "consecutive_errors": task_data.get("consecutive_errors", 0),
    }

    # Add type-specific fields
    if determined_class == ScheduledTask:  # type: ignore
        schedule_data = task_data.get("schedule", {})
        common_args["schedule"] = parse_task_schedule(schedule_data)
        return ScheduledTask(**common_args)  # type: ignore
    elif determined_class == AdHocTask:  # type: ignore
        common_args["token"] = task_data.get("token", "")
        return AdHocTask(**common_args)  # type: ignore
    else:
        plan_data = task_data.get("plan", {})
        common_args["plan"] = parse_task_plan(plan_data)
        return PlannedTask(**common_args)  # type: ignore


__all__ = [
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