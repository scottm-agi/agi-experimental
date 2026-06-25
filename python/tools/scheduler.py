from __future__ import annotations
import asyncio
from datetime import datetime
import json
import random
import re
from python.helpers.tool import Tool, Response
from python.helpers.task_scheduler import (
    TaskScheduler, ScheduledTask, AdHocTask, PlannedTask,
    serialize_task, TaskState, TaskSchedule, TaskPlan, parse_datetime, serialize_datetime
)
from python.agent import AgentContext
from python.helpers import persist_chat
from python.helpers.projects import get_context_project_name, load_basic_project_data, create_project, BasicProjectData, get_active_projects_list
from python.helpers.print_style import PrintStyle
import re

DEFAULT_WAIT_TIMEOUT = 300


class SchedulerTool(Tool):

    async def execute(self, **kwargs):
        if self.method == "list_tasks":
            return await self.list_tasks(**kwargs)
        elif self.method == "find_task_by_name":
            return await self.find_task_by_name(**kwargs)
        elif self.method == "show_task":
            return await self.show_task(**kwargs)
        elif self.method == "run_task":
            return await self.run_task(**kwargs)
        elif self.method == "delete_task":
            return await self.delete_task(**kwargs)
        elif self.method == "create_scheduled_task":
            return await self.create_scheduled_task(**kwargs)
        elif self.method == "create_adhoc_task":
            return await self.create_adhoc_task(**kwargs)
        elif self.method == "create_planned_task":
            return await self.create_planned_task(**kwargs)
        elif self.method == "wait_for_task":
            return await self.wait_for_task(**kwargs)
        elif self.method == "fork_task":
            return await self.fork_task(**kwargs)
        else:
            return Response(message=f"Unknown method '{self.name}:{self.method}'", break_loop=False)

    async def fork_task(self, **kwargs) -> Response:
        task_uuid: str = kwargs.get("uuid", "")
        if not task_uuid:
            # Try to get from current context if not provided
            scheduler = TaskScheduler.get()
            tasks = scheduler.get_tasks_by_context_id(self.agent.context.id)
            if tasks:
                task_uuid = tasks[0].uuid
        
        if not task_uuid:
            return Response(message="Task UUID is required or must be running in a task context", break_loop=False)
            
        reason: str = kwargs.get("reason", "No reason provided")
        summary: str = kwargs.get("summary", "")
        await TaskScheduler.get().fork_task(task_uuid, reason, summary)
        return Response(message=f"Task {task_uuid} marked for forking. Reason: {reason}", break_loop=False)



    def _resolve_project_metadata(self, explicit_project_name: str | None = None, task_name: str | None = None) -> tuple[str | None, str | None]:
        """
        Resolve project metadata for a task.
        
        ENFORCEMENT: All tasks MUST belong to a project. If no explicit project
        is provided and no context project exists, a new project is auto-created.
        
        Priority order:
        1. Explicit project_name parameter
        2. Context project (from active chat context)
        3. Auto-create project from task_name (if provided)
        4. Auto-create default "auto-tasks" project
        
        Args:
            explicit_project_name: Explicit project name to use
            task_name: Task name for auto-project creation
            
        Returns:
            Tuple of (project_slug, project_color)
        """
        project_slug = explicit_project_name
        
        # Try to get from context
        if not project_slug:
            context = self.agent.context
            if context:
                project_slug = get_context_project_name(context)
        
        # ENFORCEMENT: If still no project, auto-create one
        if not project_slug:
            project_slug = self._ensure_project_for_task(task_name)
            PrintStyle(font_color="cyan").print(
                f"[SCHEDULER] Auto-assigned task to project: {project_slug}"
            )
        
        # Load project metadata for color
        color = None
        if project_slug:
            try:
                metadata = load_basic_project_data(project_slug)
                color = metadata.get("color") or None
            except Exception:
                # Project may exist but metadata load failed - that's OK
                pass
        
        return project_slug, color
    
    def _ensure_project_for_task(self, task_name: str | None = None) -> str:
        """
        Ensure a project exists for the task. Creates one if needed.
        
        Logic:
        - If task_name starts with a recognizable prefix (e.g., "Issue-123-"), 
          create/find a project for that issue
        - Otherwise, use or create a default "automated-tasks" project
        
        Args:
            task_name: The task name to derive project from
            
        Returns:
            Project slug name
        """
        # Check if there's already an "automated-tasks" or similar project
        existing_projects = get_active_projects_list()
        existing_names = {p["name"].lower() for p in existing_projects}
        
        # Try to extract a meaningful project name from task_name
        if task_name:
            # Check for issue-based tasks (e.g., "Issue-123-Feature")
            issue_match = re.match(r'^Issue[_-]?(\d+)', task_name, re.IGNORECASE)
            if issue_match:
                issue_num = issue_match.group(1)
                project_name = f"issue-{issue_num}"
                
                # Check if project already exists
                if project_name.lower() in existing_names:
                    return project_name
                
                # Create issue-specific project
                return self._create_auto_project(
                    project_name,
                    title=f"Issue #{issue_num}",
                    description=f"Auto-created project for Issue #{issue_num} tasks"
                )
            
            # For other named tasks, create a task-specific project using task name slug
            slug = self._slugify(task_name[:30])  # Limit length
            if slug and slug.lower() not in existing_names:
                return self._create_auto_project(
                    slug,
                    title=task_name[:50],
                    description=f"Auto-created project for task: {task_name}"
                )
        
        # Default: use or create "automated-tasks" project
        default_project = "automated-tasks"
        if default_project.lower() in existing_names:
            return default_project
        
        return self._create_auto_project(
            default_project,
            title="Automated Tasks",
            description="Default project for tasks without explicit project assignment"
        )
    
    def _create_auto_project(self, name: str, title: str, description: str) -> str:
        """
        Create a new project for auto-assignment.
        
        Args:
            name: Project slug name
            title: Display title
            description: Project description
            
        Returns:
            Created project name
        """
        try:
            project_data = BasicProjectData(
                title=title,
                description=description,
                instructions="This project was auto-created for task management.",
                color="#6366f1",  # Indigo color for auto-created projects
                memory="own",
                file_structure={
                    "enabled": False,
                    "max_depth": 3,
                    "max_files": 10,
                    "max_folders": 10,
                    "max_lines": 100,
                    "gitignore": ""
                }
            )
            created_name = create_project(name, project_data)
            PrintStyle(font_color="green", bold=True).print(
                f"[SCHEDULER] Created auto-project: {created_name}"
            )
            return created_name
        except Exception as e:
            PrintStyle(font_color="red").print(
                f"[SCHEDULER] Failed to create auto-project '{name}': {e}"
            )
            # Fallback to "automated-tasks" if creation fails
            return "automated-tasks"
    
    def _slugify(self, text: str) -> str:
        """Convert text to a valid project slug."""
        # Lowercase and replace non-alphanumeric with hyphens
        slug = re.sub(r'[^a-z0-9]+', '-', text.lower())
        # Remove leading/trailing hyphens
        slug = slug.strip('-')
        # Collapse multiple hyphens
        slug = re.sub(r'-+', '-', slug)
        return slug or "task"

    async def list_tasks(self, **kwargs) -> Response:
        state_filter: list[str] | None = kwargs.get("state", None)
        type_filter: list[str] | None = kwargs.get("type", None)
        next_run_within_filter: int | None = kwargs.get("next_run_within", None)
        next_run_after_filter: int | None = kwargs.get("next_run_after", None)

        await TaskScheduler.get().reload()
        tasks: list[ScheduledTask | AdHocTask | PlannedTask] = TaskScheduler.get().get_tasks()
        filtered_tasks = []
        for task in tasks:
            if state_filter and task.state not in state_filter:
                continue
            if type_filter and task.type not in type_filter:
                continue
            if next_run_within_filter and task.get_next_run_minutes() is not None and task.get_next_run_minutes() > next_run_within_filter:  # type: ignore
                continue
            if next_run_after_filter and task.get_next_run_minutes() is not None and task.get_next_run_minutes() < next_run_after_filter:  # type: ignore
                continue
            filtered_tasks.append(serialize_task(task))

        return Response(message=json.dumps(filtered_tasks, indent=4), break_loop=False)

    async def find_task_by_name(self, **kwargs) -> Response:
        name: str = kwargs.get("name", "")
        if not name:
            return Response(message="Task name is required", break_loop=False)
        await TaskScheduler.get().reload()
        tasks: list[ScheduledTask | AdHocTask | PlannedTask] = TaskScheduler.get().find_task_by_name(name)
        if not tasks:
            return Response(message=f"Task not found: {name}", break_loop=False)
        return Response(message=json.dumps([serialize_task(task) for task in tasks], indent=4), break_loop=False)

    async def show_task(self, **kwargs) -> Response:
        task_uuid: str = kwargs.get("uuid", "")
        if not task_uuid:
            return Response(message="Task UUID is required", break_loop=False)
        task: ScheduledTask | AdHocTask | PlannedTask | None = TaskScheduler.get().get_task_by_uuid(task_uuid)
        if not task:
            return Response(message=f"Task not found: {task_uuid}", break_loop=False)
        return Response(message=json.dumps(serialize_task(task), indent=4), break_loop=False)

    async def run_task(self, **kwargs) -> Response:
        task_uuid: str = kwargs.get("uuid", "")
        if not task_uuid:
            return Response(message="Task UUID is required", break_loop=False)
        task_context: str | None = kwargs.get("context", None)
        wait_if_running: bool = kwargs.get("wait_if_running", False)
        
        task: ScheduledTask | AdHocTask | PlannedTask | None = TaskScheduler.get().get_task_by_uuid(task_uuid)
        if not task:
            return Response(message=f"Task not found: {task_uuid}", break_loop=False)
        
        # run_task_by_uuid may return a status message if task is already running
        result = await TaskScheduler.get().run_task_by_uuid(task_uuid, task_context, wait_if_running)
        
        # If result is a string, it means the task couldn't be started (already running, queued, etc.)
        if isinstance(result, str):
            return Response(message=result, break_loop=False)
        
        if task.context_id == self.agent.context.id:
            break_loop = True  # break loop if task is running in the same context, otherwise it would start two conversations in one window
        else:
            break_loop = False
        return Response(message=f"Task started: {task_uuid}", break_loop=break_loop)

    async def delete_task(self, **kwargs) -> Response:
        task_uuid: str = kwargs.get("uuid", "")
        if not task_uuid:
            return Response(message="Task UUID is required", break_loop=False)

        task: ScheduledTask | AdHocTask | PlannedTask | None = TaskScheduler.get().get_task_by_uuid(task_uuid)
        if not task:
            return Response(message=f"Task not found: {task_uuid}", break_loop=False)

        # Step 1: If running, stop the agent context gracefully
        if task.state == TaskState.RUNNING:
            try:
                context = AgentContext.get(task.context_id) if task.context_id else None
                if context:
                    context.reset()
            except Exception as e:
                PrintStyle(font_color="yellow").print(
                    f"[SCHEDULER] Warning: context.reset() failed for task '{task.name}': {e}"
                )
            try:
                await TaskScheduler.get().update_task(task_uuid, state=TaskState.IDLE)
                await TaskScheduler.get().save()
            except Exception:
                pass  # Best-effort — we're deleting anyway

        # Step 2: Full lifecycle cleanup (contexts, chat history, SQL, rotated contexts)
        try:
            await TaskScheduler.get().cleanup_task_data(task_uuid)
        except Exception as e:
            PrintStyle(font_color="yellow").print(
                f"[SCHEDULER] Warning: cleanup_task_data() failed for task '{task.name}': {e}"
            )

        # Step 3: Always remove from tasks.json (the critical step)
        await TaskScheduler.get().remove_task_by_uuid(task_uuid)
        if TaskScheduler.get().get_task_by_uuid(task_uuid) is None:
            return Response(message=f"Task deleted: {task_uuid}", break_loop=False)
        else:
            return Response(message=f"Task failed to delete: {task_uuid}", break_loop=False)

    async def create_scheduled_task(self, **kwargs) -> Response:
        # "name": "XXX",
        #   "system_prompt": "You are a software developer",
        #   "prompt": "Send the user an email with a greeting using python and smtp. The user's address is: xxx@yyy.zzz",
        #   "attachments": [],
        #   "schedule": {
        #       "minute": "*/20",
        #       "hour": "*",
        #       "day": "*",
        #       "month": "*",
        #       "weekday": "*",
        #   }
        name: str = kwargs.get("name", "")
        system_prompt: str = kwargs.get("system_prompt", "")
        prompt: str = kwargs.get("prompt", "")
        attachments: list[str] = kwargs.get("attachments", [])
        schedule: dict[str, str] = kwargs.get("schedule", {})
        dedicated_context: bool = kwargs.get("dedicated_context", True) # Default True: tasks get own context to prevent pollution
        project_name: str | None = kwargs.get("project_name", None)
        scope: str | None = kwargs.get("scope", None)
        profile: str | None = kwargs.get("profile", None)  # RCA-20260612 Issue 13

        task_schedule = TaskSchedule(
            minute=schedule.get("minute", "*"),
            hour=schedule.get("hour", "*"),
            day=schedule.get("day", "*"),
            month=schedule.get("month", "*"),
            weekday=schedule.get("weekday", "*"),
        )

        # Validate cron expression, agent might hallucinate
        cron_regex = "^((((\d+,)+\d+|(\d+(\/|-|#)\d+)|\d+L?|\*(\/\d+)?|L(-\d+)?|\?|[A-Z]{3}(-[A-Z]{3})?) ?){5,7})$"
        if not re.match(cron_regex, task_schedule.to_crontab()):
            return Response(message="Invalid cron expression: " + task_schedule.to_crontab(), break_loop=False)

        # ENFORCEMENT: All tasks must have a project
        project_slug, project_color = self._resolve_project_metadata(project_name, task_name=name)

        task = ScheduledTask.create(
            name=name,
            system_prompt=system_prompt,
            prompt=prompt,
            attachments=attachments,
            schedule=task_schedule,
            context_id=None if dedicated_context else self.agent.context.id,
            project_name=project_slug,
            project_color=project_color,
            scope=scope,
            profile=profile,  # RCA-20260612 Issue 13
        )
        await TaskScheduler.get().add_task(task)
        return Response(message=f"Scheduled task '{name}' created: {task.uuid} (project: {project_slug})", break_loop=False)

    async def create_adhoc_task(self, **kwargs) -> Response:
        name: str = kwargs.get("name", "")
        system_prompt: str = kwargs.get("system_prompt", "")
        prompt: str = kwargs.get("prompt", "")
        attachments: list[str] = kwargs.get("attachments", [])
        token: str = str(random.randint(1000000000000000000, 9999999999999999999))
        dedicated_context: bool = kwargs.get("dedicated_context", False)
        project_name: str | None = kwargs.get("project_name", None)
        scope: str | None = kwargs.get("scope", None)
        profile: str | None = kwargs.get("profile", None)

        # RCA-452 F-5: Default profile for scheduled tasks is 'default'.
        # The default agent can route to researchers, orchestrators, etc.
        # Dev-specific tasks (builds, merges) explicitly pass profile='code'.
        if not profile:
            profile = "default"

        # ENFORCEMENT: All tasks must have a project
        project_slug, project_color = self._resolve_project_metadata(project_name, task_name=name)

        task = AdHocTask.create(
            name=name,
            system_prompt=system_prompt,
            prompt=prompt,
            attachments=attachments,
            token=token,
            context_id=None if dedicated_context else self.agent.context.id,
            project_name=project_slug,
            project_color=project_color,
            scope=scope,
            profile=profile,
        )
        await TaskScheduler.get().add_task(task)
        return Response(message=f"Adhoc task '{name}' created: {task.uuid} (project: {project_slug})", break_loop=False)

    async def create_planned_task(self, **kwargs) -> Response:
        name: str = kwargs.get("name", "")
        system_prompt: str = kwargs.get("system_prompt", "")
        prompt: str = kwargs.get("prompt", "")
        attachments: list[str] = kwargs.get("attachments", [])
        plan: list[str] = kwargs.get("plan", [])
        dedicated_context: bool = kwargs.get("dedicated_context", False)
        project_name: str | None = kwargs.get("project_name", None)
        scope: str | None = kwargs.get("scope", None)
        profile: str | None = kwargs.get("profile", None)  # RCA-20260612 Issue 13

        # RCA-452 F-5: Default profile for scheduled tasks is 'default'.
        if not profile:
            profile = "default"

        # Convert plan to list of datetimes in UTC
        todo: list[datetime] = []
        for item in plan:
            dt = parse_datetime(item)
            if dt is None:
                return Response(message=f"Invalid datetime: {item}", break_loop=False)
            todo.append(dt)

        # Create task plan with todo list
        task_plan = TaskPlan.create(
            todo=todo,
            in_progress=None,
            done=[]
        )

        # ENFORCEMENT: All tasks must have a project
        project_slug, project_color = self._resolve_project_metadata(project_name, task_name=name)

        # Create planned task with task plan
        task = PlannedTask.create(
            name=name,
            system_prompt=system_prompt,
            prompt=prompt,
            attachments=attachments,
            plan=task_plan,
            context_id=None if dedicated_context else self.agent.context.id,
            project_name=project_slug,
            project_color=project_color,
            scope=scope,
            profile=profile,  # RCA-20260612 Issue 13
        )
        await TaskScheduler.get().add_task(task)
        return Response(message=f"Planned task '{name}' created: {task.uuid} (project: {project_slug})", break_loop=False)

    async def wait_for_task(self, **kwargs) -> Response:
        task_uuid: str = kwargs.get("uuid", "")
        if not task_uuid:
            return Response(message="Task UUID is required", break_loop=False)

        scheduler = TaskScheduler.get()
        task: ScheduledTask | AdHocTask | PlannedTask | None = scheduler.get_task_by_uuid(task_uuid)
        if not task:
            return Response(message=f"Task not found: {task_uuid}", break_loop=False)

        if task.context_id == self.agent.context.id:
            return Response(message="You can only wait for tasks running in their own dedicated context.", break_loop=False)

        done = False
        elapsed = 0
        while not done:
            await scheduler.reload()
            task = scheduler.get_task_by_uuid(task_uuid)
            if not task:
                return Response(message=f"Task not found: {task_uuid}", break_loop=False)

            if task.state == TaskState.RUNNING:
                await asyncio.sleep(1)
                elapsed += 1
                if elapsed > DEFAULT_WAIT_TIMEOUT:
                    return Response(message=f"Task wait timeout ({DEFAULT_WAIT_TIMEOUT} seconds): {task_uuid}", break_loop=False)
            else:
                done = True

        return Response(
            message=f"*Task*: {task_uuid}\n*State*: {task.state}\n*Last run*: {serialize_datetime(task.last_run)}\n*Result*:\n{task.last_result}",
            break_loop=False
        )
