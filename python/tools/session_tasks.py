from __future__ import annotations
"""
Session Tasks Tool

Provides agents with the ability to manage per-session task/todo lists.
This tool enables both the Orchestrator and Alex to track mission objectives
within a single chat session.
"""

import json
from typing import Any, Dict, List, Optional

from python.helpers.tool import Tool, Response
from python.helpers.session_tasks import (
    SessionTaskList,
    SessionTask,
    TaskStatus,
    TaskPriority,
    get_session_tasks,
    get_or_create_session_tasks,
    save_session_tasks,
)


class SessionTasksTool(Tool):
    """Tool for managing session task lists."""

    async def execute(self, **kwargs) -> Response:
        """Execute the session tasks tool method."""
        method = self.method
        
        # Fallback 1: Extract method from tool name if format is "session_tasks:add_task"
        if not method and ":" in self.name:
            parts = self.name.split(":", 1)
            method = parts[1] if len(parts) > 1 else None
        
        # Fallback 2: Check args/kwargs for method or action parameter
        if not method:
            method = (
                self.args.get("method") or 
                self.args.get("action") or 
                kwargs.get("method") or 
                kwargs.get("action")
            )
        
        if method == "list_tasks":
            return await self.list_tasks(**kwargs)
        elif method == "add_task":
            return await self.add_task(**kwargs)
        elif method == "update_task":
            return await self.update_task(**kwargs)
        elif method == "start_task":
            return await self.start_task(**kwargs)
        elif method == "complete_task":
            return await self.complete_task(**kwargs)
        elif method == "fail_task":
            return await self.fail_task(**kwargs)
        elif method == "remove_task":
            return await self.remove_task(**kwargs)
        elif method == "get_progress":
            return await self.get_progress(**kwargs)
        elif method == "get_next_task":
            return await self.get_next_task(**kwargs)
        elif method == "set_mission":
            return await self.set_mission(**kwargs)
        else:
            return Response(
                message=f"Unknown method '{self.name}:{method}'",
                break_loop=False
            )

    def _get_context_id(self) -> str:
        """Get the current context ID."""
        return self.agent.context.id

    def _get_task_list(self) -> SessionTaskList:
        """Get or create the session task list for the current context."""
        return get_or_create_session_tasks(
            context_id=self._get_context_id(),
            owner=self.agent.agent_name or "alex",
        )

    async def list_tasks(self, **kwargs) -> Response:
        """
        List all tasks in the session.
        
        Optional filters:
        - status: Filter by status (pending, in_progress, completed, blocked, failed, skipped)
        - assigned_to: Filter by assignee
        - format: Output format (json, markdown, summary)
        """
        status_filter: Optional[str] = kwargs.get("status")
        assigned_to_filter: Optional[str] = kwargs.get("assigned_to")
        output_format: str = kwargs.get("format", "markdown")
        
        task_list = self._get_task_list()
        
        # Apply filters
        tasks = task_list.tasks
        if status_filter:
            try:
                status = TaskStatus(status_filter)
                tasks = [t for t in tasks if t.status == status]
            except ValueError:
                return Response(
                    message=f"Invalid status filter: {status_filter}. Valid values: {[s.value for s in TaskStatus]}",
                    break_loop=False
                )
        
        if assigned_to_filter:
            tasks = [t for t in tasks if t.assigned_to == assigned_to_filter]
        
        # Format output
        if output_format == "json":
            task_dicts = [
                {
                    "id": t.id,
                    "description": t.description,
                    "status": t.status.value,
                    "priority": t.priority,
                    "assigned_to": t.assigned_to,
                    "created_by": t.created_by,
                    "dependencies": t.dependencies,
                    "result": t.result,
                    "error": t.error,
                }
                for t in tasks
            ]
            message = json.dumps(task_dicts, indent=2)
        elif output_format == "summary":
            message = task_list.to_summary()
        else:  # markdown
            if tasks:
                lines = [f"## Session Tasks ({len(tasks)} tasks)"]
                for task in tasks:
                    lines.append(task.to_markdown())
                message = "\n".join(lines)
            else:
                message = "No tasks found."
        
        return Response(message=message, break_loop=False)

    async def add_task(self, **kwargs) -> Response:
        """
        Add a new task to the session.
        
        Required:
        - description: Task description
        
        Optional:
        - priority: 1 (critical) to 5 (optional), default 3 (medium)
        - assigned_to: Agent/mode to assign the task to
        - dependencies: List of task IDs that must complete first
        - parent_id: Parent task ID for subtasks
        """
        description: str = kwargs.get("description", "")
        if not description:
            return Response(
                message="Task description is required",
                break_loop=False
            )
        
        priority: int = kwargs.get("priority", TaskPriority.MEDIUM)
        assigned_to: Optional[str] = kwargs.get("assigned_to")
        dependencies: List[str] = kwargs.get("dependencies", [])
        parent_id: Optional[str] = kwargs.get("parent_id")
        
        task_list = self._get_task_list()
        
        task = task_list.add_task(
            description=description,
            created_by=self.agent.agent_name or "alex",
            assigned_to=assigned_to,
            priority=priority,
            parent_id=parent_id,
            dependencies=dependencies,
        )
        
        await task_list.save()
        
        return Response(
            message=f"Added task '{task.id}': {description}",
            break_loop=False
        )

    async def update_task(self, **kwargs) -> Response:
        """
        Update an existing task.
        
        Required:
        - task_id: ID of the task to update
        
        Optional (at least one required):
        - description: New description
        - priority: New priority (1-5)
        - assigned_to: New assignee
        - status: New status
        """
        task_id: str = kwargs.get("task_id", "")
        if not task_id:
            return Response(
                message="Task ID is required",
                break_loop=False
            )
        
        task_list = self._get_task_list()
        task = task_list.get_task(task_id)
        
        if not task:
            return Response(
                message=f"Task not found: {task_id}",
                break_loop=False
            )
        
        # Build update dict
        updates: Dict[str, Any] = {}
        if "description" in kwargs:
            updates["description"] = kwargs["description"]
        if "priority" in kwargs:
            updates["priority"] = kwargs["priority"]
        if "assigned_to" in kwargs:
            updates["assigned_to"] = kwargs["assigned_to"]
        if "status" in kwargs:
            try:
                updates["status"] = TaskStatus(kwargs["status"])
            except ValueError:
                return Response(
                    message=f"Invalid status: {kwargs['status']}",
                    break_loop=False
                )
        
        if not updates:
            return Response(
                message="No updates provided",
                break_loop=False
            )
        
        task_list.update_task(task_id, **updates)
        await task_list.save()
        
        return Response(
            message=f"Updated task '{task_id}': {updates}",
            break_loop=False
        )

    async def start_task(self, **kwargs) -> Response:
        """
        Mark a task as in progress.
        
        Required:
        - task_id: ID of the task to start
        
        Optional:
        - assigned_to: Agent/mode taking ownership
        """
        task_id: str = kwargs.get("task_id", "")
        if not task_id:
            return Response(
                message="Task ID is required",
                break_loop=False
            )
        
        assigned_to: Optional[str] = kwargs.get("assigned_to", self.agent.agent_name)
        
        task_list = self._get_task_list()
        task = task_list.start_task(task_id, assigned_to)
        
        if not task:
            return Response(
                message=f"Task not found or cannot be started: {task_id}",
                break_loop=False
            )
        
        await task_list.save()
        
        return Response(
            message=f"Started task '{task_id}': {task.description}",
            break_loop=False
        )

    async def complete_task(self, **kwargs) -> Response:
        """
        Mark a task as completed.
        
        Required:
        - task_id: ID of the task to complete
        
        Optional:
        - result: Summary of what was accomplished
        """
        task_id: str = kwargs.get("task_id", "")
        if not task_id:
            return Response(
                message="Task ID is required",
                break_loop=False
            )
        
        result: Optional[str] = kwargs.get("result")
        
        task_list = self._get_task_list()
        task = task_list.complete_task(task_id, result)
        
        if not task:
            return Response(
                message=f"Task not found: {task_id}",
                break_loop=False
            )
        
        await task_list.save()
        
        # Check if this unblocked any tasks
        progress = task_list.get_progress()
        
        return Response(
            message=f"Completed task '{task_id}'. Progress: {progress['completed']}/{progress['total']} ({progress['percent_complete']}%)",
            break_loop=False
        )

    async def fail_task(self, **kwargs) -> Response:
        """
        Mark a task as failed.
        
        Required:
        - task_id: ID of the task that failed
        
        Optional:
        - error: Error message or reason for failure
        """
        task_id: str = kwargs.get("task_id", "")
        if not task_id:
            return Response(
                message="Task ID is required",
                break_loop=False
            )
        
        error: Optional[str] = kwargs.get("error")
        
        task_list = self._get_task_list()
        task = task_list.fail_task(task_id, error)
        
        if not task:
            return Response(
                message=f"Task not found: {task_id}",
                break_loop=False
            )
        
        await task_list.save()
        
        return Response(
            message=f"Failed task '{task_id}': {error or 'No error message'}",
            break_loop=False
        )

    async def remove_task(self, **kwargs) -> Response:
        """
        Remove a task from the list.
        
        Required:
        - task_id: ID of the task to remove
        """
        task_id: str = kwargs.get("task_id", "")
        if not task_id:
            return Response(
                message="Task ID is required",
                break_loop=False
            )
        
        task_list = self._get_task_list()
        removed = task_list.remove_task(task_id)
        
        if not removed:
            return Response(
                message=f"Task not found: {task_id}",
                break_loop=False
            )
        
        await task_list.save()
        
        return Response(
            message=f"Removed task '{task_id}'",
            break_loop=False
        )

    async def get_progress(self, **kwargs) -> Response:
        """
        Get progress statistics for the session tasks.
        
        Optional:
        - format: Output format (json, text)
        """
        output_format: str = kwargs.get("format", "text")
        
        task_list = self._get_task_list()
        progress = task_list.get_progress()
        
        if output_format == "json":
            message = json.dumps(progress, indent=2)
        else:
            message = (
                f"📊 Task Progress:\n"
                f"  Total: {progress['total']}\n"
                f"  ✅ Completed: {progress['completed']}\n"
                f"  🔄 In Progress: {progress['in_progress']}\n"
                f"  ⏳ Pending: {progress['pending']}\n"
                f"  🚫 Blocked: {progress['blocked']}\n"
                f"  ❌ Failed: {progress['failed']}\n"
                f"  ⏭️ Skipped: {progress['skipped']}\n"
                f"  Progress: {progress['percent_complete']}%"
            )
        
        return Response(message=message, break_loop=False)

    async def get_next_task(self, **kwargs) -> Response:
        """
        Get the next actionable task by priority.
        
        Optional:
        - assigned_to: Filter by assignee
        """
        assigned_to_filter: Optional[str] = kwargs.get("assigned_to")
        
        task_list = self._get_task_list()
        
        if assigned_to_filter:
            # Get actionable tasks for specific assignee
            actionable = [
                t for t in task_list.get_actionable_tasks()
                if t.assigned_to == assigned_to_filter or t.assigned_to is None
            ]
            if actionable:
                task = sorted(actionable, key=lambda t: t.priority)[0]
            else:
                task = None
        else:
            task = task_list.get_next_task()
        
        if not task:
            return Response(
                message="No actionable tasks available",
                break_loop=False
            )
        
        return Response(
            message=f"Next task: [{task.id}] {task.description} (priority: {task.priority}, assigned: {task.assigned_to or 'unassigned'})",
            break_loop=False
        )

    async def set_mission(self, **kwargs) -> Response:
        """
        Set or update the mission statement for the session.
        
        Required:
        - mission: The mission/goal description
        """
        mission: str = kwargs.get("mission", "")
        if not mission:
            return Response(
                message="Mission description is required",
                break_loop=False
            )
        
        task_list = self._get_task_list()
        task_list.mission = mission
        await task_list.save()
        
        return Response(
            message=f"Mission set: {mission}",
            break_loop=False
        )
