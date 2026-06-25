from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request
from python.helpers.task_scheduler import TaskScheduler, TaskState
from python.helpers.localization import Localization
from python.agent import AgentContext
from python.helpers import persist_chat
from python.helpers.persistence_manager import PersistenceManager


class SchedulerTasksDeleteBulk(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        Delete multiple tasks from the scheduler by IDs
        """
        # Get timezone from input (do not set if not provided, we then rely on poll() to set it)
        if timezone := input.get("timezone", None):
            Localization.get().set_timezone(timezone)

        scheduler = TaskScheduler.get()
        await scheduler.reload()

        # Get task IDs from input
        task_ids: list[str] = input.get("task_ids", [])

        if not task_ids:
            return {"error": "Missing required field: task_ids"}

        deleted_count = 0
        errors = []

        for task_id in task_ids:
            try:
                # Check if the task exists
                task = scheduler.get_task_by_uuid(task_id)
                if not task:
                    errors.append(f"Task with ID {task_id} not found")
                    continue

                context = None
                if task.context_id:
                    context = await self.use_context(task.context_id)

                # If the task is running, update its state to IDLE first
                if task.state == TaskState.RUNNING:
                    if context:
                        context.reset()
                    # Update the state to IDLE so any ongoing processes know to terminate
                    await scheduler.update_task(task_id, state=TaskState.IDLE)
                    # Force a save to ensure the state change is persisted
                    await scheduler.save()

                # This is a dedicated context for the task, so we remove it
                if context and context.id == task.uuid:
                    AgentContext.remove(context.id)
                    persist_chat.remove_chat(context.id)
                    await PersistenceManager.get_instance().delete_context_sql(context.id)

                # Remove the task
                await scheduler.remove_task_by_uuid(task_id)
                deleted_count += 1
            except Exception as e:
                errors.append(f"Error deleting task {task_id}: {str(e)}")

        return {
            "success": len(errors) == 0,
            "message": f"Deleted {deleted_count} tasks" + (f" with {len(errors)} errors" if errors else ""),
            "errors": errors if errors else None
        }

