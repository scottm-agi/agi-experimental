from __future__ import annotations
"""
update_task_list — Agent tool for managing structured task lists.

Ported from Roo-Code's UpdateTodoListTool. Each agent (orchestrator or worker)
can maintain a structured task list in agent.data["_task_list"].

The agent calls this tool to:
1. Create its task list at the START of work (from plan decomposition)
2. Update status as it progresses through tasks
3. The orchestrator completion gate validates all tasks are complete before
   allowing the `response` tool.

Input format: Markdown checklist
    [ ] Pending task
    [-] In-progress task
    [x] Completed task
"""

from python.helpers.tool import Tool, Response
from python.helpers.task_list import parse_markdown_checklist, format_task_list_status
import logging

logger = logging.getLogger("agix.update_task_list")


class UpdateTaskList(Tool):

    async def execute(self, **kwargs):
        todos_raw = self.args.get("content", self.args.get("todos", ""))

        # NEW: Accept structured metadata for GUID linkage
        metadata = self.args.get("metadata", {})
        if isinstance(metadata, str):
            try:
                import json
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        if not todos_raw:
            # If no content, return current task list status
            task_list = self.agent.data.get("_task_list", [])
            if task_list:
                return Response(
                    message=format_task_list_status(task_list),
                    break_loop=False,
                )
            return Response(
                message="No task list registered. Provide a markdown checklist to create one.",
                break_loop=False,
            )

        # Parse markdown checklist into structured TodoItems
        todos = parse_markdown_checklist(todos_raw)

        if not todos:
            return Response(
                message="Could not parse any tasks from the provided content. "
                        "Use markdown checklist format:\n"
                        "[ ] Pending task\n"
                        "[-] In-progress task\n"
                        "[x] Completed task",
                break_loop=False,
            )

        # ── Inject GUID linkage from metadata or agent.data fallback ──
        task_guid = metadata.get("task_guid", "")
        parent_hash = metadata.get("parent_hash", "")
        # Fallback: read from agent.data if not provided explicitly
        if not parent_hash:
            parent_hash = self.agent.data.get("_parent_task_hash", "")
        if not task_guid:
            task_guid = self.agent.data.get("_parent_task_guid", "")

        if task_guid or parent_hash:
            for todo in todos:
                if not todo.get("guid"):
                    todo["guid"] = task_guid
                if not todo.get("parent_hash"):
                    todo["parent_hash"] = parent_hash

        # Store/update task list in agent data
        existing = self.agent.data.get("_task_list", [])

        if existing:
            # Merge: update existing tasks by content match, add new ones
            existing_contents = {t["content"]: t for t in existing}
            for new_task in todos:
                if new_task["content"] in existing_contents:
                    # Update status of existing task
                    existing_contents[new_task["content"]]["status"] = new_task["status"]
                    # Update linkage if newly provided
                    if new_task.get("guid"):
                        existing_contents[new_task["content"]]["guid"] = new_task["guid"]
                    if new_task.get("parent_hash"):
                        existing_contents[new_task["content"]]["parent_hash"] = new_task["parent_hash"]
                else:
                    # Add new task
                    existing.append(new_task)
            self.agent.data["_task_list"] = existing
        else:
            # First time — set full list
            self.agent.data["_task_list"] = todos

        task_list = self.agent.data["_task_list"]
        completed = sum(1 for t in task_list if t["status"] == "completed")
        total = len(task_list)

        logger.info(
            f"[TASK LIST] {self.agent.agent_name}: "
            f"{completed}/{total} tasks complete"
        )

        return Response(
            message=f"Task list updated: {completed}/{total} complete.\n\n"
                    + format_task_list_status(task_list),
            break_loop=False,
        )

    async def before_execution(self, **kwargs):
        pass

    async def after_execution(self, response, **kwargs):
        pass
