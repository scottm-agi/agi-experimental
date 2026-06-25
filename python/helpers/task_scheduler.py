"""
Task Scheduler - Thin facade for backwards compatibility.

This module serves as the main entry point for the task scheduling system.
It re-exports all types from the scheduler package and provides the main
TaskScheduler class for task execution.

Module Structure:
    - scheduler/base.py: TaskState, TaskType, SCHEDULER_FOLDER
    - scheduler/models.py: TaskSchedule, TaskPlan, BaseTask, AdHocTask, ScheduledTask, PlannedTask
    - scheduler/task_list.py: SchedulerTaskList
    - scheduler/serialization.py: serialize/parse helpers
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
import os
import shutil
import threading
from urllib.parse import urlparse
import json
import uuid as uuid_module
from typing import Any, Callable, Dict, Optional, Union, ClassVar

import nest_asyncio
nest_asyncio.apply()

import pytz

# Re-export everything from the scheduler package for backwards compatibility
from python.helpers.scheduler import (
    # Base types
    SCHEDULER_FOLDER,
    TaskState,
    TaskType,
    # Task models
    TaskSchedule,
    TaskPlan,
    BaseTask,
    AdHocTask,
    ScheduledTask,
    PlannedTask,
    AnyTask,
    # Task list management
    SchedulerTaskList,
    # Serialization helpers
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

# RCA-20260612 Issue 12: Wire project GC for build worktree cleanup
from python.helpers.project_gc import identify_stale_projects, cleanup_stale_projects

# Additional imports for TaskScheduler class
from python.agent import Agent, AgentContext, AgentContextType, UserMessage
from python.initialize import initialize_agent
from python.helpers.persist_chat import save_tmp_chat, load_tmp_chat
from python.helpers.print_style import PrintStyle
from python.helpers.defer import DeferredTask
from python.helpers.files import get_abs_path, make_dirs, read_file, write_file, write_file_atomic
from python.helpers.strings import truncate_text_by_ratio
from python.helpers.localization import Localization
from python.helpers.settings import get_settings
from python.helpers import projects


class TaskScheduler:
    """
    Main task scheduler class for executing scheduled tasks.
    
    This is a singleton class that manages the task execution loop,
    context management, and task lifecycle.
    """

    _instance = None
    _global_version: ClassVar[int] = 0
    _initialization_task: Optional[asyncio.Task] = None

    @classmethod
    def _increment_version(cls):
        """Increment the global tasks version."""
        cls._global_version += 1

    @classmethod
    def get(cls) -> "TaskScheduler":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._tasks = SchedulerTaskList.get()
            self._printer = PrintStyle(italic=True, font_color="green", padding=False)
            try:
                loop = asyncio.get_running_loop()
                TaskScheduler._initialization_task = loop.create_task(self._initialize())
            except RuntimeError:
                pass
            self._initialized = True

    async def _initialize(self):
        """Run startup initialization: reset stale tasks + seed defaults."""
        await self.reset_stale_tasks()
        # Issue #833: Seed default scheduled tasks on first startup
        from python.helpers.scheduler.default_tasks import seed_default_tasks
        try:
            await seed_default_tasks(self._tasks)
        except Exception as e:
            self._printer.print(f"Warning: Failed to seed default tasks: {e}")

    async def reload(self):
        await self._tasks.reload()
        self._increment_version()

    async def reset_stale_tasks(self):
        """Reset any tasks in RUNNING state to ERROR on startup."""
        await self.reload()
        running_tasks = [t for t in self.get_tasks() if t.state == TaskState.RUNNING]
        if running_tasks:
            self._printer.print(f"Cleanup: Found {len(running_tasks)} stale running tasks. Marking as INTERRUPTED.")
            for task in running_tasks:
                await self.update_task(
                    task.uuid, 
                    state=TaskState.ERROR,
                    last_result="ERROR: System was interrupted while task was running. Manual recovery or restart required."
                )
            await self.save()

    def get_tasks(self) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        return self._tasks.get_tasks()

    def get_tasks_by_context_id(self, context_id: str, only_running: bool = False) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        return self._tasks.get_tasks_by_context_id(context_id, only_running)

    async def add_task(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> "TaskScheduler":
        await self._tasks.add_task(task)
        self._increment_version()
        ctx = await self._get_chat_context(task)
        return self

    async def remove_task_by_uuid(self, task_uuid: str) -> "TaskScheduler":
        await self._tasks.remove_task_by_uuid(task_uuid)
        self._increment_version()
        return self

    async def remove_task_by_name(self, name: str) -> "TaskScheduler":
        await self._tasks.remove_task_by_name(name)
        self._increment_version()
        return self

    def get_task_by_uuid(self, task_uuid: str) -> Union[ScheduledTask, AdHocTask, PlannedTask] | None:
        return self._tasks.get_task_by_uuid(task_uuid)

    def get_task_by_name(self, name: str) -> Union[ScheduledTask, AdHocTask, PlannedTask] | None:
        return self._tasks.get_task_by_name(name)

    def find_task_by_name(self, name: str) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        return self._tasks.find_task_by_name(name)

    async def tick(self):
        if not get_settings()["tasks_enabled"]:
            return
        await self._recover_stuck_running_tasks()
        tasks_to_run = await self._tasks.get_due_tasks()
        if tasks_to_run:
            self._printer.print(f"Propagating {len(tasks_to_run)} tasks for execution...")
            # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
            SCHEDULER_TICK_TIMEOUT = 600.0  # 10 minutes
            tick_tasks = [asyncio.ensure_future(self._run_task(task)) for task in tasks_to_run]
            done, pending = await asyncio.wait(
                tick_tasks,
                timeout=SCHEDULER_TICK_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                self._printer.print(
                    f"[SCHEDULER] {len(pending)} tick tasks timed out after "
                    f"{SCHEDULER_TICK_TIMEOUT}s — cancelling"
                )
                for p in pending:
                    p.cancel()
                await asyncio.wait(pending, timeout=5.0)

    async def _recover_stuck_running_tasks(self):
        """Automatic recovery for tasks stuck in RUNNING state beyond their timeout."""
        GRACE_PERIOD_SECONDS = 600
        await self.reload()
        now = datetime.now(timezone.utc)
        
        for task in self.get_tasks():
            if task.state != TaskState.RUNNING or not task.last_run:
                continue
            last_run = task.last_run
            if last_run.tzinfo is None:
                last_run = pytz.timezone("UTC").localize(last_run)
            running_duration = (now - last_run).total_seconds()
            max_allowed_duration = task.timeout_seconds + GRACE_PERIOD_SECONDS
            
            if running_duration > max_allowed_duration:
                self._printer.print(f"[STALE RECOVERY] Task '{task.name}' running for {int(running_duration)}s. Auto-resetting.")
                await self.update_task(task.uuid, state=TaskState.ERROR,
                    last_result=f"SYSTEM: Task forcibly reset due to timeout (running for {int(running_duration)}s). Next run will start fresh.")
                await self._update_lineage(task, summary=f"Stale recovery after {int(running_duration)}s",
                    result="Task forcibly reset", state="error")

    async def run_task_by_uuid(self, task_uuid: str, task_context: Optional[str] = None, wait_if_running: bool = False):
        await self._tasks.reload()
        task = self.get_task_by_uuid(task_uuid)
        if not task:
            raise ValueError(f"Task with UUID '{task_uuid}' not found")

        if task.state == TaskState.RUNNING:
            if wait_if_running:
                if task.pending_run_queued:
                    return f"Task '{task.name}' already has a queued run pending."
                updated = await self.update_task_checked(task_uuid,
                    verify_func=lambda t: t.state == TaskState.RUNNING and not t.pending_run_queued,
                    pending_run_queued=True)
                if not updated:
                    await self._tasks.reload()
                    task = self.get_task_by_uuid(task_uuid)
                    if task and task.state != TaskState.RUNNING:
                        pass  # Task finished, run now
                    else:
                        return f"Task '{task.name}' already has a queued run."
                else:
                    max_wait = task.timeout_seconds + 60
                    poll_interval = 5
                    waited = 0
                    while waited < max_wait:
                        await asyncio.sleep(poll_interval)
                        waited += poll_interval
                        await self._tasks.reload()
                        task = self.get_task_by_uuid(task_uuid)
                        if task and task.state != TaskState.RUNNING:
                            await self.update_task(task_uuid, pending_run_queued=False)
                            break
                    else:
                        await self.update_task(task_uuid, pending_run_queued=False)
                        return f"Task '{task.name}' still running after {max_wait}s wait."
            else:
                elapsed_info = ""
                if task.last_run:
                    elapsed = (datetime.now(timezone.utc) - task.last_run).total_seconds()
                    remaining = max(0, task.timeout_seconds - elapsed)
                    elapsed_info = f" (running for {int(elapsed)}s)"
                return f"Task '{task.name}' is already running{elapsed_info}."

        if task.state == TaskState.DISABLED:
            raise ValueError(f"Task '{task.name}' is disabled")
        if task.state == TaskState.ERROR:
            task = await self.update_task(task_uuid, state=TaskState.IDLE)
            if not task:
                raise ValueError(f"Task with UUID '{task_uuid}' not found after state reset")

        await self._run_task(task, task_context)

    async def run_task_by_name(self, name: str, task_context: Optional[str] = None):
        task = self._tasks.get_task_by_name(name)
        if task is None:
            raise ValueError(f"Task with name {name} not found")
        await self._run_task(task, task_context)

    async def save(self):
        await self._tasks.save()
        self._increment_version()

    async def fork_task(self, task_uuid: str, reason: str, summary: str = ""):
        await self.update_task(task_uuid, pending_fork=True, pending_fork_summary=summary)
        self._printer.print(f"Task {task_uuid} marked for forking: {reason}")

    async def update_task_checked(self, task_uuid: str,
        verify_func: Callable[[Union[ScheduledTask, AdHocTask, PlannedTask]], bool] = lambda task: True,
        **update_params) -> Optional[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        def _update_task(task):
            task.update(**update_params)
        result = await self._tasks.update_task_by_uuid(task_uuid, _update_task, verify_func)
        if result:
            self._increment_version()
        return result

    async def update_task(self, task_uuid: str, **update_params) -> Optional[Union[ScheduledTask, AdHocTask, PlannedTask]]:
        return await self.update_task_checked(task_uuid, lambda task: True, **update_params)

    async def __new_context(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> AgentContext:
        if not task.context_id:
            raise ValueError(f"Task {task.name} has no context ID")

        if not task.project_name:
            raise ValueError(f"Task {task.name} ({task.uuid}) is missing project association.")

        # CRITICAL: Create the AgentContext FIRST before activating the project.
        # activate_project requires the context to exist for lookup.
        # If the task specifies a profile override (e.g., "code" for build tasks),
        # pass it as override_settings so initialize_agent uses it instead of
        # the user's global default profile. This prevents build tasks from
        # running under orchestrator profiles like "multiagentdev" which would
        # trigger full decomposition/delegation for simple file changes.
        override = {"agent_profile": task.profile} if task.profile else None
        config = initialize_agent(override_settings=override, context_id=task.context_id)
        # #1110: Explicitly set type=TASK so the orchestrator gate bypass
        # fires on the very first run (default is USER which doesn't bypass).
        context: AgentContext = AgentContext(config, id=task.context_id, name=task.name, type=AgentContextType.TASK)

        # Now activate the project on the newly created context
        from python.helpers import projects
        try:
            await projects.activate_project(task.context_id, task.project_name)
        except Exception as e:
            # Graceful fallback: if the task's project was deleted, fall back to 'default'
            project_err = str(e)
            if "does not exist" in project_err or "No such project" in project_err:
                self._printer.print(
                    f"[SCHEDULER] Task '{task.name}': project '{task.project_name}' "
                    f"not found, falling back to 'default' project."
                )
                projects.ensure_default_project_exists()
                await projects.activate_project(task.context_id, "default")
                # Update the task's persisted project_name to avoid repeating the fallback
                await self.update_task(task.uuid, project_name="default")
            else:
                raise

        # Build effective system prompt with scope injection (#1010)
        effective_system_prompt = task.system_prompt or ""
        if task.scope:
            effective_system_prompt += (
                "\n\n## TASK SCOPE BOUNDARIES (MANDATORY)\n"
                f"{task.scope}\n\n"
                "You MUST operate strictly within these boundaries. "
                "If you encounter work outside this scope, report it as a finding "
                "but DO NOT take action. Stay focused on the defined task."
            )

        prompt_content = f"## Task:\n{task.prompt}"
        if effective_system_prompt:
            prompt_content = f"## System Prompt:\n{effective_system_prompt}\n\n{prompt_content}"
        context.log.log(type="user", heading="Task Initialized", content=prompt_content, protected=True)
        save_tmp_chat(context)
        return context

    async def _get_chat_context(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> AgentContext:
        from python.helpers.persist_chat import load_chat
        context = await load_chat(task.context_id) if task.context_id else None
        if context:
            assert isinstance(context, AgentContext)
            # #1110: Always enforce TASK type — even if persisted as USER.
            # This ensures the orchestrator gate bypass fires on every run.
            context.type = AgentContextType.TASK
            save_tmp_chat(context)
            return context
        return await self.__new_context(task)

    async def _persist_chat(self, task: Union[ScheduledTask, AdHocTask, PlannedTask], context: AgentContext):
        TASK_LOG_KEEP_LAST = 100
        await context.log.prune_logs(keep_last=TASK_LOG_KEEP_LAST, context_id=context.id)
        save_tmp_chat(context)

    async def _update_lineage(self, task: Union[ScheduledTask, AdHocTask, PlannedTask], summary: str, result: str, state: str):
        task_dir_name = task.context_id or task.uuid
        lineage_path = get_abs_path(SCHEDULER_FOLDER, task_dir_name, "lineage.json")
        lineage = {"context_id": task.context_id, "task_uuid": task.uuid, "runs": []}
        if os.path.exists(lineage_path):
            try:
                lineage_content = read_file(lineage_path)
                if lineage_content:
                    lineage = json.loads(lineage_content)
            except Exception:
                pass
        run_entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "summary": summary, "result": result, "state": state}
        if "runs" not in lineage:
            lineage["runs"] = []
        lineage["runs"].append(run_entry)
        lineage["runs"] = lineage["runs"][-task.lineage_keep_last:]
        write_file_atomic(lineage_path, json.dumps(lineage, indent=2))

    def _get_error_lineage(self, task_dir: str, limit: int = 3) -> str:
        """Read lineage.json and return a formatted summary of recent error runs.
        
        Returns empty string if no errors found or file doesn't exist.
        Used to inject error history into the next task prompt for 5-Why awareness.
        """
        lineage_path = os.path.join(task_dir, "lineage.json")
        if not os.path.exists(lineage_path):
            return ""
        
        try:
            content = read_file(lineage_path)
            if not content:
                return ""
            lineage = json.loads(content)
        except Exception:
            return ""
        
        runs = lineage.get("runs", [])
        error_runs = [r for r in runs if r.get("state") == "error"]
        if not error_runs:
            return ""
        
        # Take only the last N error runs
        recent_errors = error_runs[-limit:]
        
        lines = [
            "The following recent runs FAILED. Apply 5-Why root-cause analysis before proceeding:",
            "1. WHY did the error occur?",
            "2. WHY does that root cause exist?",
            "3. WHAT must change in your approach? Fix the ROOT CAUSE, not the symptom.",
            "",
        ]
        for i, run in enumerate(recent_errors, 1):
            ts = run.get("timestamp", "unknown")
            summary = run.get("summary", "No summary")
            result = truncate_text_by_ratio(run.get("result", "No details"), 300)
            lines.append(f"### Failed Run {i} ({ts})")
            lines.append(f"**Summary:** {summary}")
            lines.append(f"**Error:** {result}")
            lines.append("")
        
        return "\n".join(lines)

    async def _post_run_cleanup(self, task: Union[ScheduledTask, AdHocTask, PlannedTask], context: AgentContext):
        task.run_count += 1
        agent = context.streaming_agent or context.agent0
        if agent and hasattr(agent, "history") and task.history_prune_turns > 0:
            agent.history.prune_to_turns(task.history_prune_turns)
            save_tmp_chat(context)
        if task.run_count >= task.auto_clone_interval and task.auto_clone_interval > 0:
            await self.rotate_task_context(task)
        else:
            await self.update_task(task.uuid, run_count=task.run_count)

    async def rotate_task_context(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]):
        old_context_id = task.context_id
        new_context_id = str(uuid_module.uuid4())
        old_context_id_for_files = old_context_id or task.uuid
        old_task_dir = get_abs_path(SCHEDULER_FOLDER, old_context_id_for_files)
        new_task_dir = get_abs_path(SCHEDULER_FOLDER, new_context_id)
        
        if os.path.exists(old_task_dir):
            from python.helpers.files import create_dir
            create_dir(new_task_dir)
            for filename in ["task_definition.md", "action_summary.md", "lineage.json"]:
                old_file = os.path.join(old_task_dir, filename)
                if os.path.exists(old_file):
                    shutil.copy2(old_file, os.path.join(new_task_dir, filename))
            from python.helpers.persist_chat import get_chat_folder_path
            old_chat_folder = get_chat_folder_path(old_context_id_for_files)
            if os.path.exists(old_chat_folder):
                new_chat_folder = get_chat_folder_path(new_context_id)
                shutil.copytree(old_chat_folder, new_chat_folder, dirs_exist_ok=True)

        task.rotated_contexts.append(old_context_id_for_files)
        if len(task.rotated_contexts) > task.rotation_keep_count:
            try:
                expired = task.rotated_contexts.pop(0)
                from python.helpers.files import delete_dir
                delete_dir(get_abs_path(SCHEDULER_FOLDER, expired))
                from python.helpers.persist_chat import remove_chat
                remove_chat(expired)
            except Exception:
                pass

        await self.update_task(task.uuid, context_id=new_context_id, run_count=0,
            last_result=f"Context rotated from {old_context_id_for_files}.",
            rotated_contexts=task.rotated_contexts)

    async def _run_task(self, task: Union[ScheduledTask, AdHocTask, PlannedTask], task_context: str | None = None):
        async def _run_task_wrapper(task_uuid: str, task_context: str | None = None):
            if TaskScheduler._initialization_task:
                try:
                    await TaskScheduler._initialization_task
                except Exception as e:
                    self._printer.print(f"Warning during TaskScheduler initialization: {e}")

            task_snapshot = self.get_task_by_uuid(task_uuid)
            if task_snapshot is None or task_snapshot.state == TaskState.RUNNING:
                return

            current_task = await self.update_task_checked(task_uuid, lambda t: t.state != TaskState.RUNNING, state=TaskState.RUNNING)
            if not current_task or current_task.state != TaskState.RUNNING:
                return

            await current_task.on_run()
            agent = None

            try:
                context = await self._get_chat_context(current_task)
                AgentContext.use(context.id)
                agent = context.streaming_agent or context.agent0

                # Agent registration with supervisor is handled by
                # the _50_supervisor_register extension (agent_init hook).
                # MasterAgentSupervisor removed per RCA-249 Phase 7.

                attachment_filenames = []
                if current_task.attachments:
                    for attachment in current_task.attachments:
                        if os.path.exists(attachment):
                            attachment_filenames.append(attachment)
                        else:
                            try:
                                url = urlparse(attachment)
                                if url.scheme in ["http", "https", "ftp", "ftps", "sftp"]:
                                    attachment_filenames.append(attachment)
                            except Exception:
                                pass

                task_dir_name = current_task.context_id or current_task.uuid
                task_dir = get_abs_path(SCHEDULER_FOLDER, task_dir_name)
                os.makedirs(task_dir, exist_ok=True)
                
                def_path = os.path.join(task_dir, "task_definition.md")
                scope_section = f"\n\n## Scope\n{current_task.scope}" if current_task.scope else ""
                def_content = f"# Task Definition\n\n## System Prompt\n{current_task.system_prompt}{scope_section}\n\n## Main Prompt\n{current_task.prompt}"
                write_file(def_path, def_content)
                
                summary_path = os.path.join(task_dir, "action_summary.md")
                summary_content = ""
                if os.path.exists(summary_path):
                    summary_content = read_file(summary_path)

                full_prompt = current_task.prompt
                handover_text = ""
                if getattr(current_task, "pending_fork_summary", ""):
                    handover_text = f"\n\n### Handover Context\n{current_task.pending_fork_summary}"
                if getattr(current_task, "pending_handover", None):
                    handover_json = json.dumps(current_task.pending_handover, indent=2)
                    handover_text += f"\n\n### Structured Handover\n```json\n{handover_json}\n```"
                if handover_text:
                    full_prompt += handover_text
                    await self.update_task(current_task.uuid, pending_fork_summary="", pending_handover=None)

                task_prompt = f"## Task:\n{full_prompt}"
                if task_context:
                    task_prompt = f"## Context:\n{task_context}\n\n{task_prompt}"
                if summary_content:
                    task_prompt = f"## Previous Action Summary:\n{summary_content}\n\n{task_prompt}"

                # Inject recent error lineage for 5-Why course-correction
                error_lineage = self._get_error_lineage(task_dir, limit=3)
                if error_lineage:
                    task_prompt = f"## ⚠️ Recent Run Errors (5-Why Required)\n{error_lineage}\n\n{task_prompt}"

                context.log.log(type="user", heading="User message", content=task_prompt,
                    kvps={"attachments": attachment_filenames}, protected=True, id=str(uuid_module.uuid4()))

                # Build effective system prompt with scope injection (#1010)
                effective_sys_prompt = current_task.system_prompt or ""
                if current_task.scope:
                    effective_sys_prompt += (
                        "\n\n## TASK SCOPE BOUNDARIES (MANDATORY)\n"
                        f"{current_task.scope}\n\n"
                        "You MUST operate strictly within these boundaries. "
                        "If you encounter work outside this scope, report it as a finding "
                        "but DO NOT take action. Stay focused on the defined task."
                    )
                hist_msg = UserMessage(message=task_prompt, system_message=[effective_sys_prompt], attachments=attachment_filenames)
                hist_msg.protected = True
                await agent.hist_add_user_message(hist_msg)

                try:
                    from python.helpers.supervisor_agent import get_llm_supervisor
                    llm_supervisor = get_llm_supervisor()
                    if llm_supervisor:
                        llm_supervisor.register_agent(agent)
                    save_tmp_chat(context)
                except Exception:
                    pass

                try:
                    result = await asyncio.wait_for(agent.monologue(), timeout=current_task.timeout_seconds)
                except asyncio.TimeoutError:
                    error_msg = f"Task timed out after {current_task.timeout_seconds}s"
                    # Write error summary so next run knows what failed
                    try:
                        error_summary = f"⚠️ PREVIOUS RUN FAILED (Timeout)\n\n{error_msg}\n\nApply 5-Why root-cause analysis before retrying. Do NOT repeat the same approach."
                        write_file(summary_path, error_summary)
                    except Exception:
                        pass
                    # Critical fix: call cleanup even on timeout so run_count
                    # increments and history is pruned (prevents corrupted state)
                    try:
                        await self._persist_chat(current_task, context)
                        await self._post_run_cleanup(current_task, context)
                    except Exception as cleanup_err:
                        self._printer.print(f"Timeout cleanup error: {cleanup_err}")
                    await current_task.on_error(error_msg)
                    await self._update_lineage(current_task, summary="Timeout", result=error_msg, state="error")
                    return

                if agent.history.current and agent.history.current.messages:
                    agent.history.current.messages[-1].protected = True
                if context.log.logs:
                    context.log.logs[-1].protected = True

                if current_task.type in [TaskType.SCHEDULED, TaskType.PLANNED]:
                    try:
                        summary_req = "Summarize actions in 3-5 bullet points for the next run."
                        summary_result = await agent.call_utility_model(system="Task monitoring assistant.",
                            message=f"Last Response: {result}\n\n{summary_req}")
                        write_file(summary_path, summary_result)
                        await self._update_lineage(current_task, summary=summary_result, result=result, state="success")
                    except Exception as ex:
                        await self._update_lineage(current_task, summary=f"Error: {ex}", result=result, state="success")

                await self._persist_chat(current_task, context)
                await self._post_run_cleanup(current_task, context)
                await current_task.on_success(result)

                await self._tasks.reload()
                updated_task = self.get_task_by_uuid(task_uuid)
                if updated_task and updated_task.state != TaskState.IDLE:
                    await self.update_task(task_uuid, state=TaskState.IDLE)

            except Exception as e:
                self._printer.print(f"Scheduler Task '{current_task.name}' failed: {e}")
                # Write error summary so next run knows what failed
                try:
                    error_summary = f"⚠️ PREVIOUS RUN FAILED (Exception)\n\n{truncate_text_by_ratio(str(e), 500)}\n\nApply 5-Why root-cause analysis before retrying. Do NOT repeat the same approach."
                    write_file(summary_path, error_summary)
                except Exception:
                    pass
                await current_task.on_error(str(e))
                await self._update_lineage(current_task, summary="Error occurred", result=str(e), state="error")
                await self._tasks.reload()
                updated_task = self.get_task_by_uuid(task_uuid)
                if updated_task and updated_task.state != TaskState.ERROR:
                    await self.update_task(task_uuid, state=TaskState.ERROR)
                if agent:
                    await agent.handle_critical_exception(e)
            finally:
                try:
                    await self._tasks.reload()
                    task_check = self.get_task_by_uuid(task_uuid)
                    if task_check and task_check.state != TaskState.RUNNING:
                        # RCA-249 Phase 7: Only LLM supervisor remains
                        from python.helpers.supervisor_agent import get_llm_supervisor
                        llm_supervisor = get_llm_supervisor()
                        if llm_supervisor and agent:
                            context_id = getattr(agent.context, "id", "no_context")
                            llm_supervisor.unregister_agent(f"{agent.agent_name}@{context_id}")
                except Exception:
                    pass

                await current_task.on_finish()
                await self._tasks.save()
                
                await self._tasks.reload()
                final_task = self.get_task_by_uuid(task_uuid)
                if final_task and getattr(final_task, "pending_fork", False):
                    await self.update_task(task_uuid, pending_fork=False)
                    async def _delayed_fork():
                        await asyncio.sleep(2)
                        await self.run_task_by_uuid(task_uuid)
                    asyncio.create_task(_delayed_fork())

        deferred_task = DeferredTask(thread_name=self.__class__.__name__)
        deferred_task.start_task(_run_task_wrapper, task.uuid, task_context)
        await asyncio.sleep(0.1)

    async def cleanup_task_data(self, task_uuid: str):
        """Cleanup all data associated with a task."""
        task = self.get_task_by_uuid(task_uuid)
        if not task:
            return
        contexts_to_delete = set()
        if task.context_id:
            contexts_to_delete.add(task.context_id)
        contexts_to_delete.add(task.uuid)
        if hasattr(task, "rotated_contexts") and task.rotated_contexts:
            for ctx_id in task.rotated_contexts:
                contexts_to_delete.add(ctx_id)

        from python.helpers.persist_chat import remove_chat
        from python.helpers.persistence_manager import PersistenceManager

        pm = PersistenceManager.get_instance()
        for ctxid in contexts_to_delete:
            try:
                AgentContext.remove(ctxid)
                remove_chat(ctxid)
                await pm.delete_context_sql(ctxid)
            except Exception:
                pass

    def serialize_all_tasks(self) -> list[Dict[str, Any]]:
        """Serialize all tasks to a list of dictionaries."""
        return serialize_tasks(self.get_tasks())

    def serialize_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Serialize a specific task by UUID."""
        task = self.get_task_by_uuid(task_id)
        if task:
            return serialize_task(task)
        return None