"""
Scheduler task list management.

This module contains:
- SchedulerTaskList: Singleton class for managing the task list
"""

from __future__ import annotations
import random
import threading
from os.path import exists
from typing import Annotated, Callable, ClassVar, Optional, Union

from pydantic import BaseModel, Field, PrivateAttr

from .base import SCHEDULER_FOLDER, TaskState
from .models import ScheduledTask, AdHocTask, PlannedTask, AnyTask
from python.helpers.files import get_abs_path, make_dirs, read_file, write_file_atomic
from python.helpers.print_style import PrintStyle


class SchedulerTaskList(BaseModel):
    """
    Singleton class managing the scheduler task list.
    
    Provides thread-safe operations for task storage, retrieval,
    and persistence to disk (tasks.json).
    """
    tasks: list[Annotated[Union[ScheduledTask, AdHocTask, PlannedTask], Field(discriminator="type")]] = Field(default_factory=list)
    
    _last_mtime: float = PrivateAttr(default=0.0)
    _last_reload: float = PrivateAttr(default=0.0)
    
    # Singleton instance
    __instance: ClassVar[Optional["SchedulerTaskList"]] = PrivateAttr(default=None)

    @classmethod
    def get(cls) -> "SchedulerTaskList":
        """Get or create the singleton instance."""
        path = get_abs_path(SCHEDULER_FOLDER, "tasks.json")
        if cls.__instance is None:
            if not exists(path):
                make_dirs(path)
                # Use synchronous save for initialization
                cls.__instance = cls(tasks=[])
                cls.__instance.save_sync()
            else:
                cls.__instance = cls.model_validate_json(read_file(path))
        else:
            # Optimization: Avoid blocking asyncio.run() on every get() call.
            # The scheduler loop calls reload() explicitly when needed.
            # For API calls, we rely on the last loaded state or call reload() explicitly.
            pass
        return cls.__instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls.__instance = None

    def save_sync(self):
        """Synchronous version of save() for initialization."""
        path = get_abs_path(SCHEDULER_FOLDER, "tasks.json")
        if not exists(path):
            make_dirs(path)
        json_data = self.model_dump_json()
        write_file_atomic(path, json_data)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    async def reload(self) -> "SchedulerTaskList":
        """Reload tasks from disk with mtime checking."""
        import os
        import time
        path = get_abs_path(SCHEDULER_FOLDER, "tasks.json")
        
        if exists(path):
            try:
                mtime = os.path.getmtime(path)
                current_time = time.time()
                
                # If mtime matches, always skip (major performance bottleneck fix)
                if mtime == self._last_mtime:
                    return self
                
                # If mtime changed, but we reloaded VERY recently (e.g. within 0.5s), 
                # we skip to avoid thrashing during heavy write bursts.
                if current_time - self._last_reload < 0.5:
                    return self

                # Read file outside the lock
                content = read_file(path)
                data = self.__class__.model_validate_json(content)
                with self._lock:
                    self.tasks.clear()
                    self.tasks.extend(data.tasks)
                    self._last_mtime = mtime
                    self._last_reload = current_time
            except Exception as e:
                PrintStyle.debug(f"Error reloading scheduler tasks: {e}")
        return self


    async def add_task(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> "SchedulerTaskList":
        """Add a task to the list and save."""
        with self._lock:
            self.tasks.append(task)
        await self.save()
        return self

    async def save(self) -> "SchedulerTaskList":
        """Save tasks to disk with validation."""
        # Prepare data outside the lock if possible, but model_dump_json needs the tasks
        with self._lock:
            # Debug: check for AdHocTasks with null tokens before saving
            for task in self.tasks:
                if isinstance(task, AdHocTask):
                    if task.token is None or task.token == "":
                        PrintStyle(italic=True, font_color="red", padding=False).print(
                            f"WARNING: AdHocTask {task.name} ({task.uuid}) has a null or empty token before saving: '{task.token}'"
                        )
                        # Generate a new token to prevent errors
                        task.token = str(random.randint(1000000000000000000, 9999999999999999999))
                        PrintStyle(italic=True, font_color="red", padding=False).print(
                            f"Fixed: Generated new token '{task.token}' for task {task.name}"
                        )

            # Get the JSON string before writing
            json_data = self.model_dump_json()

        # Write file outside the lock
        path = get_abs_path(SCHEDULER_FOLDER, "tasks.json")
        if not exists(path):
            make_dirs(path)

        # Debug: check if 'null' appears as token value in JSON
        if '"type": "adhoc"' in json_data and '"token": null' in json_data:
            PrintStyle(italic=True, font_color="red", padding=False).print(
                "ERROR: Found null token in JSON output for an adhoc task"
            )

        write_file_atomic(path, json_data)

        # Debug: Verify after saving
        if exists(path):
            loaded_json = read_file(path)
            if '"type": "adhoc"' in loaded_json and '"token": null' in loaded_json:
                PrintStyle(italic=True, font_color="red", padding=False).print(
                    "ERROR: Null token persisted in JSON file for an adhoc task"
                )

        return self

    async def update_task_by_uuid(
        self,
        task_uuid: str,
        updater_func: Callable[[Union[ScheduledTask, AdHocTask, PlannedTask]], None],
        verify_func: Callable[[Union[ScheduledTask, AdHocTask, PlannedTask]], bool] = lambda task: True
    ) -> Optional[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        """
        Atomically update a task by UUID using the provided updater function.

        The updater_func should take the task as an argument and perform any necessary updates.
        This method ensures that the task is updated and saved atomically, preventing race conditions.

        Returns the updated task or None if not found.
        """
        # Reload to ensure we have the latest state (reload is now lock-safe)
        await self.reload()

        with self._lock:
            # Find the task
            task = next((task for task in self.tasks if task.uuid == task_uuid and verify_func(task)), None)
            if task is None:
                return None

            # Apply the updates via the provided function
            updater_func(task)

        # Save the changes (save is now lock-safe)
        await self.save()

        return task

    def get_tasks(self) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        """Get all tasks (thread-safe)."""
        with self._lock:
            return self.tasks

    def get_tasks_by_context_id(self, context_id: str, only_running: bool = False) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        """Get tasks by context ID."""
        with self._lock:
            return [
                task for task in self.tasks
                if task.context_id == context_id
                and (not only_running or task.state == TaskState.RUNNING)
            ]

    async def get_due_tasks(self) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        """Get tasks that are due to run."""
        # Reload outside the lock
        await self.reload()
        with self._lock:
            return [
                task for task in self.tasks
                if task.check_schedule() and task.state in [TaskState.IDLE, TaskState.ERROR]
            ]

    def get_task_by_uuid(self, task_uuid: str) -> Union[ScheduledTask, AdHocTask, PlannedTask] | None:
        """Get a task by UUID."""
        with self._lock:
            return next((task for task in self.tasks if task.uuid == task_uuid), None)

    def get_task_by_name(self, name: str) -> Union[ScheduledTask, AdHocTask, PlannedTask] | None:
        """Get a task by exact name match."""
        with self._lock:
            return next((task for task in self.tasks if task.name == name), None)

    def find_task_by_name(self, name: str) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        """Find tasks by name substring or exact UUID match."""
        with self._lock:
            # Match on name (substring) or UUID (exact)
            return [task for task in self.tasks if name.lower() in task.name.lower() or name.lower() == task.uuid.lower()]

    async def remove_task_by_uuid(self, task_uuid: str) -> "SchedulerTaskList":
        """Remove a task by UUID."""
        with self._lock:
            self.tasks = [task for task in self.tasks if task.uuid != task_uuid]
            await self.save()
        return self

    async def remove_task_by_name(self, name: str) -> "SchedulerTaskList":
        """Remove a task by name."""
        with self._lock:
            self.tasks = [task for task in self.tasks if task.name != name]
            await self.save()
        return self


__all__ = [
    "SchedulerTaskList",
]