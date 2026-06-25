"""
Task models for the scheduler system.

This module contains:
- TaskSchedule: Cron-based schedule definition
- TaskPlan: Planned task execution times
- BaseTask: Base class for all tasks
- AdHocTask: One-time tasks triggered by token
- ScheduledTask: Cron-scheduled recurring tasks
- PlannedTask: Tasks with specific planned execution times
"""

from __future__ import annotations
import random
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Literal, Optional, Union

import pytz
from crontab import CronTab
from pydantic import BaseModel, Field

from .base import TaskState, TaskType, SCHEDULER_FOLDER
from python.helpers.localization import Localization

# Helper for resilient CronTab instantiation across different library versions
def _get_crontab(schedule_str: str) -> CronTab:
    """Helper to instantiate CronTab resiliently for both 'crontab' and 'python-crontab' libraries."""
    try:
        # Try 'crontab' package style (parse-crontab) which supports 'crontab' keyword
        return CronTab(crontab=schedule_str)
    except TypeError:
        try:
            # Try 'python-crontab' package style which uses 'tab' keyword
            return CronTab(tab=schedule_str)
        except TypeError:
            # Fallback to positional argument which is generally the most compatible
            return CronTab(schedule_str)


class TaskSchedule(BaseModel):
    """Cron-based schedule definition for scheduled tasks."""
    minute: str
    hour: str
    day: str
    month: str
    weekday: str
    timezone: str = Field(default_factory=lambda: Localization.get().get_timezone())

    def to_crontab(self) -> str:
        """Convert to crontab string."""
        return f"{self.minute} {self.hour} {self.day} {self.month} {self.weekday}"


class TaskPlan(BaseModel):
    """Plan for planned task execution with todo, in_progress, and done lists."""
    todo: list[datetime] = Field(default_factory=list)
    in_progress: Optional[datetime] = None
    done: list[datetime] = Field(default_factory=list)

    @classmethod
    def create(cls, todo: list[datetime] = list(), in_progress: Optional[datetime] = None, done: list[datetime] = list()):
        if todo:
            for idx, dt in enumerate(todo):
                if dt.tzinfo is None:
                    todo[idx] = pytz.timezone("UTC").localize(dt)
        if in_progress:
            if in_progress.tzinfo is None:
                in_progress = pytz.timezone("UTC").localize(in_progress)
        if done:
            for idx, dt in enumerate(done):
                if dt.tzinfo is None:
                    done[idx] = pytz.timezone("UTC").localize(dt)
        return cls(todo=todo, in_progress=in_progress, done=done)

    def add_todo(self, launch_time: datetime):
        if launch_time.tzinfo is None:
            launch_time = pytz.timezone("UTC").localize(launch_time)
        self.todo.append(launch_time)
        self.todo = sorted(self.todo)

    def set_in_progress(self, launch_time: datetime):
        if launch_time.tzinfo is None:
            launch_time = pytz.timezone("UTC").localize(launch_time)
        if launch_time not in self.todo:
            raise ValueError(f"Launch time {launch_time} not in todo list")
        self.todo.remove(launch_time)
        self.todo = sorted(self.todo)
        self.in_progress = launch_time

    def set_done(self, launch_time: datetime):
        if launch_time.tzinfo is None:
            launch_time = pytz.timezone("UTC").localize(launch_time)
        if launch_time != self.in_progress:
            raise ValueError(f"Launch time {launch_time} is not the same as in progress time {self.in_progress}")
        if launch_time in self.done:
            raise ValueError(f"Launch time {launch_time} already in done list")
        self.in_progress = None
        self.done.append(launch_time)
        self.done = sorted(self.done)

    def get_next_launch_time(self) -> Optional[datetime]:
        return self.todo[0] if self.todo else None

    def should_launch(self) -> Optional[datetime]:
        next_launch_time = self.get_next_launch_time()
        if next_launch_time is None:
            return None
        # return next launch time if current datetime utc is later than next launch time
        if datetime.now(timezone.utc) > next_launch_time:
            return next_launch_time
        return None


class BaseTask(BaseModel):
    """Base class for all task types."""
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    context_id: Optional[str] = Field(default=None)
    state: TaskState = Field(default=TaskState.IDLE)
    name: str = Field()
    system_prompt: str
    prompt: str
    attachments: list[str] = Field(default_factory=list)
    project_name: Optional[str] = Field(default=None)
    project_color: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run: Optional[datetime] = None
    last_result: Optional[str] = None
    run_count: int = Field(default=0)
    history_prune_turns: int = Field(default=20)
    lineage_keep_last: int = Field(default=5000)  # Increased default for high-frequency tasks
    auto_clone_interval: int = Field(default=1000)
    rotation_keep_count: int = Field(default=10)
    rotated_contexts: list[str] = Field(default_factory=list)
    pending_fork: bool = Field(default=False)
    pending_fork_summary: str = Field(default="")
    pending_run_queued: bool = Field(default=False)  # Single-waiter queue
    timeout_seconds: int = Field(default=1800)  # 30 minute default
    scope: Optional[str] = Field(default=None)  # Descriptive scope boundary (#1010)
    profile: Optional[str] = Field(default=None)  # Override agent profile for this task (e.g., "code" for build tasks)
    completion_promise: Optional[str] = Field(default=None)  # Ralph Loop
    checkpoint_promises: list[str] = Field(default_factory=list)  # Sequential promises
    checkpoint_index: int = Field(default=0)  # Current checkpoint
    max_loop_iterations: int = Field(default=5)  # Ralph Loop max iterations
    pending_handover: Optional[dict] = Field(default=None)  # Structured handover context
    consecutive_errors: int = Field(default=0)  # Circuit breaker: auto-disable after MAX_CONSECUTIVE_ERRORS

    MAX_CONSECUTIVE_ERRORS: int = 3  # Class constant — not a Pydantic field

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.context_id:
            self.context_id = self.uuid
        self._lock = threading.RLock()

    def update(self,
               name: Optional[str] = None,
               state: Optional[TaskState] = None,
               system_prompt: Optional[str] = None,
               prompt: Optional[str] = None,
               attachments: Optional[list[str]] = None,
               last_run: Optional[datetime] = None,
               last_result: Optional[str] = None,
               context_id: Optional[str] = None,
               **kwargs):
        with self._lock:
            if name is not None:
                self.name = name
                self.updated_at = datetime.now(timezone.utc)
            if state is not None:
                self.state = state
                self.updated_at = datetime.now(timezone.utc)
            if system_prompt is not None:
                self.system_prompt = system_prompt
                self.updated_at = datetime.now(timezone.utc)
            if prompt is not None:
                self.prompt = prompt
                self.updated_at = datetime.now(timezone.utc)
            if attachments is not None:
                self.attachments = attachments
                self.updated_at = datetime.now(timezone.utc)
            if last_run is not None:
                self.last_run = last_run
                self.updated_at = datetime.now(timezone.utc)
            if last_result is not None:
                self.last_result = last_result
                self.updated_at = datetime.now(timezone.utc)
            if context_id is not None:
                self.context_id = context_id
                self.updated_at = datetime.now(timezone.utc)
            for key, value in kwargs.items():
                if value is not None:
                    setattr(self, key, value)
                    self.updated_at = datetime.now(timezone.utc)
            
    def fork(self, reason: str, summary: str = ""):
        """Signal that this task should be forked (re-run immediately)."""
        with self._lock:
            self.pending_fork = True
            self.pending_fork_summary = summary
            self.last_result = f"Forking requested: {reason}. Summary: {summary[:100]}..."

    def check_schedule(self, frequency_seconds: float = 60.0) -> bool:
        return False

    def get_next_run(self) -> Optional[datetime]:
        return None

    def is_dedicated(self) -> bool:
        return self.context_id == self.uuid

    def get_next_run_minutes(self) -> Optional[int]:
        next_run = self.get_next_run()
        if next_run is None:
            return None
        return int((next_run - datetime.now(timezone.utc)).total_seconds() / 60)

    async def on_run(self):
        """Called when task starts running. Import here to avoid circular imports."""
        from python.helpers.task_scheduler import TaskScheduler
        await TaskScheduler.get().update_task(
            self.uuid,
            state=TaskState.RUNNING,
            last_run=datetime.now(timezone.utc),
            last_result="Running..."
        )

    async def on_finish(self):
        """Called when task finishes (success or error)."""
        from python.helpers.task_scheduler import TaskScheduler
        await TaskScheduler.get().update_task(
            self.uuid,
            updated_at=datetime.now(timezone.utc)
        )

    async def on_error(self, error: str):
        """Called when task encounters an error.
        
        Increments consecutive_errors. If >= MAX_CONSECUTIVE_ERRORS,
        auto-disables the task to prevent infinite retry loops.
        """
        from python.helpers.task_scheduler import TaskScheduler
        from python.helpers.print_style import PrintStyle
        
        self.consecutive_errors += 1
        scheduler = TaskScheduler.get()
        
        # Circuit breaker: auto-disable after MAX_CONSECUTIVE_ERRORS
        if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
            new_state = TaskState.DISABLED
            error_msg = (
                f"AUTO-DISABLED: {self.consecutive_errors} consecutive errors. "
                f"Last error: {error}. Re-enable manually after investigating."
            )
            PrintStyle(italic=True, font_color="red", padding=False).print(
                f"Task '{self.name}' auto-disabled after {self.consecutive_errors} consecutive errors"
            )
        else:
            new_state = TaskState.ERROR
            error_msg = f"ERROR ({self.consecutive_errors}/{self.MAX_CONSECUTIVE_ERRORS}): {error}"
        
        updated_task = await scheduler.update_task(
            self.uuid,
            state=new_state,
            last_run=datetime.now(timezone.utc),
            last_result=error_msg,
            consecutive_errors=self.consecutive_errors
        )
        if not updated_task:
            PrintStyle(italic=True, font_color="red", padding=False).print(
                f"Failed to update task {self.uuid} state to {new_state} after error: {error}"
            )
        await scheduler.save()

    async def on_success(self, result: str):
        """Called when task completes successfully."""
        from python.helpers.task_scheduler import TaskScheduler
        from python.helpers.print_style import PrintStyle
        
        scheduler = TaskScheduler.get()

        # Ralph Loop: Check for completion promise and checkpoints
        should_fork = False
        fork_reason = ""
        new_checkpoint_index = self.checkpoint_index

        # 1. Check sequential checkpoints
        if self.checkpoint_promises:
            for i in range(self.checkpoint_index, len(self.checkpoint_promises)):
                if self.checkpoint_promises[i] in result:
                    new_checkpoint_index = i + 1
                    PrintStyle(italic=True, font_color="green", padding=False).print(
                        f"Checkpoint {i+1}/{len(self.checkpoint_promises)} achieved: {self.checkpoint_promises[i]}"
                    )
            
            if new_checkpoint_index < len(self.checkpoint_promises):
                should_fork = True
                fork_reason = f"Checkpoint {new_checkpoint_index+1}/{len(self.checkpoint_promises)} ('{self.checkpoint_promises[new_checkpoint_index]}') not yet achieved."

        # 2. Check final completion promise
        if not should_fork and self.completion_promise and self.completion_promise not in result:
            should_fork = True
            fork_reason = f"Final completion promise '{self.completion_promise}' not found in output."

        # 3. Handle iteration limits
        if should_fork:
            if self.run_count >= self.max_loop_iterations:
                should_fork = False
                result = f"MAX_ITERATIONS_REACHED: {result}"
            else:
                fork_reason += f" Iteration {self.run_count+1}/{self.max_loop_iterations}."

        updated_task = await scheduler.update_task(
            self.uuid,
            state=TaskState.IDLE,
            last_run=datetime.now(timezone.utc),
            last_result=result,
            pending_fork=should_fork,
            pending_fork_summary=fork_reason if should_fork else "",
            checkpoint_index=new_checkpoint_index,
            consecutive_errors=0  # Reset circuit breaker on success
        )
        if not updated_task:
            PrintStyle(italic=True, font_color="red", padding=False).print(
                f"Failed to update task {self.uuid} state to IDLE after success"
            )
        await scheduler.save()


class AdHocTask(BaseTask):
    """One-time task triggered by token."""
    type: Literal[TaskType.AD_HOC] = TaskType.AD_HOC
    token: str = Field(default_factory=lambda: str(random.randint(1000000000000000000, 9999999999999999999)))

    @classmethod
    def create(
        cls,
        name: str,
        system_prompt: str,
        prompt: str,
        token: str,
        attachments: list[str] = list(),
        context_id: str | None = None,
        project_name: str | None = None,
        project_color: str | None = None,
        scope: str | None = None,
        profile: str | None = None,
    ):
        return cls(name=name,
                   system_prompt=system_prompt,
                   prompt=prompt,
                   attachments=attachments,
                   token=token,
                   context_id=context_id,
                   project_name=project_name,
                   project_color=project_color,
                   scope=scope,
                   profile=profile)

    def update(self,
               name: Optional[str] = None,
               state: Optional[TaskState] = None,
               system_prompt: Optional[str] = None,
               prompt: Optional[str] = None,
               attachments: Optional[list[str]] = None,
               last_run: Optional[datetime] = None,
               last_result: Optional[str] = None,
               context_id: Optional[str] = None,
               token: Optional[str] = None,
               **kwargs):
        super().update(name=name,
                       state=state,
                       system_prompt=system_prompt,
                       prompt=prompt,
                       attachments=attachments,
                       last_run=last_run,
                       last_result=last_result,
                       context_id=context_id,
                       token=token,
                       **kwargs)


class ScheduledTask(BaseTask):
    """Cron-scheduled recurring task."""
    type: Literal[TaskType.SCHEDULED] = TaskType.SCHEDULED
    schedule: TaskSchedule

    @classmethod
    def create(
        cls,
        name: str,
        system_prompt: str,
        prompt: str,
        schedule: TaskSchedule,
        attachments: list[str] = list(),
        context_id: Optional[str] = None,
        timezone: Optional[str] = None,
        project_name: str | None = None,
        project_color: str | None = None,
        scope: str | None = None,
        profile: str | None = None,  # RCA-20260612 Issue 13: parity with AdHocTask
    ):
        # Set timezone in schedule if provided
        if timezone is not None:
            schedule.timezone = timezone
        else:
            schedule.timezone = Localization.get().get_timezone()

        return cls(name=name,
                   system_prompt=system_prompt,
                   prompt=prompt,
                   attachments=attachments,
                   schedule=schedule,
                   context_id=context_id,
                   project_name=project_name,
                   project_color=project_color,
                   scope=scope,
                   profile=profile)

    def update(self,
               name: Optional[str] = None,
               state: Optional[TaskState] = None,
               system_prompt: Optional[str] = None,
               prompt: Optional[str] = None,
               attachments: Optional[list[str]] = None,
               last_run: Optional[datetime] = None,
               last_result: Optional[str] = None,
               context_id: Optional[str] = None,
               schedule: Optional[TaskSchedule] = None,
               **kwargs):
        super().update(name=name,
                       state=state,
                       system_prompt=system_prompt,
                       prompt=prompt,
                       attachments=attachments,
                       last_run=last_run,
                       last_result=last_result,
                       context_id=context_id,
                       schedule=schedule,
                       **kwargs)

    def check_schedule(self, frequency_seconds: float = 60.0) -> bool:
        with self._lock:
            crontab = _get_crontab(self.schedule.to_crontab())  # type: ignore

            # Get the timezone from the schedule or use UTC as fallback
            task_timezone = pytz.timezone(self.schedule.timezone or Localization.get().get_timezone())
            now = datetime.now(timezone.utc)

            # 0. Gate: If last_run is in the future, skip (Issue #86 Edge Case)
            if self.last_run:
                last_run = self.last_run
                if last_run.tzinfo is None:
                    last_run = pytz.timezone("UTC").localize(last_run)
                
                if last_run > now + timedelta(seconds=5):  # 5s buffer for small skews
                    return False

            # 1. Regular check: is there a run within this frequency window?
            reference_time = now - timedelta(seconds=frequency_seconds)
            reference_time_tz = reference_time.astimezone(task_timezone)

            # Get next run time
            next_run_dt = crontab.next(now=reference_time_tz, return_datetime=True)  # type: ignore
            if next_run_dt:
                is_in_window = (next_run_dt.astimezone(timezone.utc) <= now)
                is_after_last = True
                if self.last_run:
                    is_after_last = (next_run_dt.astimezone(timezone.utc) > last_run + timedelta(seconds=1))
                
                if is_in_window and is_after_last:
                    return True

            # 2. Catch-up check: was there a run scheduled between last_run and now? (Issue #86)
            if self.last_run:
                scheduled_run = crontab.previous(now=now.astimezone(task_timezone), return_datetime=True)  # type: ignore
                if scheduled_run and scheduled_run.astimezone(timezone.utc) > last_run + timedelta(seconds=1):
                    return True

            return False

    def get_next_run(self) -> datetime | None:
        with self._lock:
            crontab = _get_crontab(self.schedule.to_crontab())  # type: ignore
            return crontab.next(now=datetime.now(timezone.utc), return_datetime=True)  # type: ignore


class PlannedTask(BaseTask):
    """Task with specific planned execution times."""
    type: Literal[TaskType.PLANNED] = TaskType.PLANNED
    plan: TaskPlan

    @classmethod
    def create(
        cls,
        name: str,
        system_prompt: str,
        prompt: str,
        plan: TaskPlan,
        attachments: list[str] = list(),
        context_id: str | None = None,
        project_name: str | None = None,
        project_color: str | None = None,
        scope: str | None = None,
        profile: str | None = None,  # RCA-20260612 Issue 13: parity with AdHocTask
    ):
        return cls(name=name,
                   system_prompt=system_prompt,
                   prompt=prompt,
                   plan=plan,
                   attachments=attachments,
                   context_id=context_id,
                   project_name=project_name,
                   project_color=project_color,
                   scope=scope,
                   profile=profile)

    def update(self,
               name: Optional[str] = None,
               state: Optional[TaskState] = None,
               system_prompt: Optional[str] = None,
               prompt: Optional[str] = None,
               attachments: Optional[list[str]] = None,
               last_run: Optional[datetime] = None,
               last_result: Optional[str] = None,
               context_id: Optional[str] = None,
               plan: Optional[TaskPlan] = None,
               **kwargs):
        super().update(name=name,
                       state=state,
                       system_prompt=system_prompt,
                       prompt=prompt,
                       attachments=attachments,
                       last_run=last_run,
                       last_result=last_result,
                       context_id=context_id,
                       plan=plan,
                       **kwargs)

    def check_schedule(self, frequency_seconds: float = 60.0) -> bool:
        with self._lock:
            return self.plan.should_launch() is not None

    def get_next_run(self) -> datetime | None:
        with self._lock:
            return self.plan.get_next_launch_time()

    async def on_run(self):
        with self._lock:
            next_launch_time = self.plan.should_launch()
            if next_launch_time is not None:
                self.plan.set_in_progress(next_launch_time)
        await super().on_run()

    async def on_finish(self):
        plan_updated = False

        with self._lock:
            if self.plan.in_progress is not None:
                self.plan.set_done(self.plan.in_progress)
                plan_updated = True

        if plan_updated:
            from python.helpers.task_scheduler import TaskScheduler
            scheduler = TaskScheduler.get()
            await scheduler.update_task(self.uuid, plan=self.plan)

        await super().on_finish()

    async def on_success(self, result: str):
        await super().on_success(result)

    async def on_error(self, error: str):
        await super().on_error(error)


# Type alias for any task type
AnyTask = Union[ScheduledTask, AdHocTask, PlannedTask]


__all__ = [
    "TaskSchedule",
    "TaskPlan",
    "BaseTask",
    "AdHocTask",
    "ScheduledTask",
    "PlannedTask",
    "AnyTask",
]