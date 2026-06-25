from __future__ import annotations
"""
Session Task List System

Provides per-session task/todo list management for AGIX.
This enables the Orchestrator (Boomerang) and Alex to collaboratively
track mission objectives within a single chat session.

Unlike the scheduler (cron-like recurring tasks), this system focuses on
mission-driven task decomposition and tracking.
"""

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from os.path import exists
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from pydantic import BaseModel, Field, PrivateAttr

from python.helpers.files import get_abs_path, make_dirs, read_file, write_file

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.session_tasks")

SESSION_TASKS_FOLDER = "tmp/session_tasks"


class TaskStatus(str, Enum):
    """Status of a session task."""
    PENDING = "pending"           # Not yet started
    IN_PROGRESS = "in_progress"   # Currently being worked on
    COMPLETED = "completed"       # Successfully finished
    BLOCKED = "blocked"           # Waiting on dependencies
    FAILED = "failed"             # Failed to complete
    SKIPPED = "skipped"           # Intentionally skipped


class TaskPriority(int, Enum):
    """Priority levels for tasks."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    OPTIONAL = 5


class SessionTask(BaseModel):
    """Individual task within a session."""
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: int = Field(default=TaskPriority.MEDIUM)
    
    # Attribution
    created_by: str = Field(default="alex")  # "orchestrator" | "alex" | agent_name
    assigned_to: Optional[str] = Field(default=None)  # Agent mode/name assigned
    
    # Hierarchy
    parent_id: Optional[str] = Field(default=None)  # For subtask hierarchy
    dependencies: List[str] = Field(default_factory=list)  # Task IDs that must complete first
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    
    # Results
    result: Optional[str] = Field(default=None)  # Outcome/summary when completed
    error: Optional[str] = Field(default=None)   # Error message if failed
    
    # Flexible metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def update(self, **kwargs) -> "SessionTask":
        """Update task fields."""
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)
        self.updated_at = datetime.now(timezone.utc)
        return self

    def model_dump(self, **kwargs) -> dict:
        """Override to exclude non-serializable SM objects from metadata.

        RCA-475 BUG-1: _wire_sm() stores SessionTaskSM in metadata.
        Pydantic's model_dump_json() cannot serialize it → crash.
        We WRAP the parent method and strip the SM key.
        """
        d = super().model_dump(**kwargs)
        meta = d.get("metadata")
        if meta and "_session_task_sm" in meta:
            d["metadata"] = {k: v for k, v in meta.items() if k != "_session_task_sm"}
        return d

    def model_dump_json(self, **kwargs) -> str:
        """Override: Pydantic v2 model_dump_json() bypasses model_dump().

        Route through our filtered model_dump() to ensure SM objects
        are stripped before JSON serialization.
        """
        import json as _json
        indent = kwargs.pop("indent", None)
        d = self.model_dump(**kwargs)
        return _json.dumps(d, default=str, indent=indent)

    def _wire_sm(self, target_status: str, source_method: str) -> None:
        """RCA-475 E6: Create/transition SessionTaskSM alongside status assignment.

        SM instances live in self.metadata["_session_task_sm"].
        Warn-only during migration — never blocks the original assignment.
        """
        from python.helpers.state_machines.session_task_sm import SessionTaskSM

        sm = self.metadata.get("_session_task_sm")
        if sm is None:
            # Seed SM with status BEFORE this transition (previous status)
            # Since status was already assigned, we infer the old status.
            # For first call, SM starts at INITIAL_STATUS (pending).
            sm = SessionTaskSM(entity_id=self.id)
            self.metadata["_session_task_sm"] = sm

        if sm.status == target_status:
            return  # idempotent

        ok, msg = sm.transition(
            target_status,
            reason=source_method,
            source="session_tasks.py",
        )
        if not ok:
            logger.warning("[SESSION_TASK SM] %s — status set anyway (migration mode)", msg)
            sm.transition(
                target_status,
                reason=f"force-sync: {msg}",
                source="session_tasks.py",
                force=True,
            )

    def start(self, assigned_to: Optional[str] = None) -> "SessionTask":
        """Mark task as in progress."""
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now(timezone.utc)
        if assigned_to:
            self.assigned_to = assigned_to
        self.updated_at = datetime.now(timezone.utc)
        self._wire_sm("in_progress", "SessionTask.start")
        return self
    
    def complete(self, result: Optional[str] = None) -> "SessionTask":
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        if result:
            self.result = result
        self.updated_at = datetime.now(timezone.utc)
        self._wire_sm("completed", "SessionTask.complete")
        return self
    
    def fail(self, error: Optional[str] = None) -> "SessionTask":
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        if error:
            self.error = error
        self.updated_at = datetime.now(timezone.utc)
        self._wire_sm("failed", "SessionTask.fail")
        return self
    
    def block(self, reason: Optional[str] = None) -> "SessionTask":
        """Mark task as blocked."""
        self.status = TaskStatus.BLOCKED
        if reason:
            self.metadata["block_reason"] = reason
        self.updated_at = datetime.now(timezone.utc)
        self._wire_sm("blocked", "SessionTask.block")
        return self
    
    def skip(self, reason: Optional[str] = None) -> "SessionTask":
        """Mark task as skipped."""
        self.status = TaskStatus.SKIPPED
        self.completed_at = datetime.now(timezone.utc)
        if reason:
            self.metadata["skip_reason"] = reason
        self.updated_at = datetime.now(timezone.utc)
        self._wire_sm("skipped", "SessionTask.skip")
        return self
    
    def is_actionable(self) -> bool:
        """Check if task can be started (not blocked by dependencies)."""
        return self.status == TaskStatus.PENDING
    
    def to_markdown(self) -> str:
        """Convert task to markdown checkbox format."""
        status_icons = {
            TaskStatus.PENDING: "⏳",
            TaskStatus.IN_PROGRESS: "🔄",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.BLOCKED: "🚫",
            TaskStatus.FAILED: "❌",
            TaskStatus.SKIPPED: "⏭️",
        }
        icon = status_icons.get(self.status, "❓")
        checkbox = "[x]" if self.status == TaskStatus.COMPLETED else "[ ]"
        
        line = f"- {checkbox} {icon} {self.description}"
        if self.assigned_to:
            line += f" (@{self.assigned_to})"
        if self.status == TaskStatus.BLOCKED and self.dependencies:
            line += f" (blocked by: {', '.join(self.dependencies)})"
        return line


class SessionTaskList(BaseModel):
    """Task list bound to a chat session."""
    
    context_id: str
    mission: str = Field(default="")  # Original mission/prompt
    tasks: List[SessionTask] = Field(default_factory=list)
    
    # Ownership for conflict resolution
    owner: str = Field(default="alex")  # "orchestrator" | "alex"
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Thread safety
    _lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)
    
    def __init__(self, **data):
        super().__init__(**data)
        self._lock = threading.RLock()
    
    # ==================== Task Management ====================
    
    def add_task(
        self,
        description: str,
        created_by: str = "alex",
        assigned_to: Optional[str] = None,
        priority: int = TaskPriority.MEDIUM,
        parent_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionTask:
        """Add a new task to the list."""
        with self._lock:
            task = SessionTask(
                description=description,
                created_by=created_by,
                assigned_to=assigned_to,
                priority=priority,
                parent_id=parent_id,
                dependencies=dependencies or [],
                metadata=metadata or {},
            )
            self.tasks.append(task)
            self.updated_at = datetime.now(timezone.utc)
            self._update_blocked_status()
            logger.info(f"Added task '{task.id}': {description}")
            return task
    
    def get_task(self, task_id: str) -> Optional[SessionTask]:
        """Get a task by ID."""
        with self._lock:
            return next((t for t in self.tasks if t.id == task_id), None)
    
    def update_task(self, task_id: str, **kwargs) -> Optional[SessionTask]:
        """Update a task by ID."""
        with self._lock:
            task = self.get_task(task_id)
            if task:
                task.update(**kwargs)
                self.updated_at = datetime.now(timezone.utc)
                self._update_blocked_status()
                logger.info(f"Updated task '{task_id}': {kwargs}")
            return task
    
    def remove_task(self, task_id: str) -> bool:
        """Remove a task by ID and clean up orphan dependencies."""
        with self._lock:
            removed = False
            for i, task in enumerate(self.tasks):
                if task.id == task_id:
                    self.tasks.pop(i)
                    removed = True
                    break
            
            if removed:
                # Clean up orphan dependencies - remove deleted task from other tasks' dependencies
                for task in self.tasks:
                    if task_id in task.dependencies:
                        task.dependencies.remove(task_id)
                        task.updated_at = datetime.now(timezone.utc)
                        logger.info(f"Removed orphan dependency '{task_id}' from task '{task.id}'")
                self.updated_at = datetime.now(timezone.utc)
                self._update_blocked_status()
                logger.info(f"Removed task '{task_id}'")
            return removed

    def remove_tasks(self, task_ids: List[str]) -> int:
        """Remove multiple tasks by ID and clean up orphan dependencies."""
        count = 0
        with self._lock:
            # We iterate and call the internal logic for each task_id
            # To be efficient, we can batch the dependency cleanup if needed,
            # but for now, calling remove_task-like logic in a loop within the lock is safe.
            for task_id in task_ids:
                removed = False
                for i, task in enumerate(self.tasks):
                    if task.id == task_id:
                        self.tasks.pop(i)
                        removed = True
                        break
                
                if removed:
                    count += 1
                    # Clean up orphan dependencies
                    for task in self.tasks:
                        if task_id in task.dependencies:
                            task.dependencies.remove(task_id)
                            task.updated_at = datetime.now(timezone.utc)
                    logger.info(f"Bulk removed task '{task_id}'")
            
            if count > 0:
                self.updated_at = datetime.now(timezone.utc)
                self._update_blocked_status()
                logger.info(f"Bulk removed {count} tasks")
        return count
    
    def start_task(self, task_id: str, assigned_to: Optional[str] = None) -> Optional[SessionTask]:
        """Mark a task as in progress."""
        with self._lock:
            task = self.get_task(task_id)
            if task and task.is_actionable():
                task.start(assigned_to)
                self.updated_at = datetime.now(timezone.utc)
                logger.info(f"Started task '{task_id}'")
            return task
    
    def complete_task(self, task_id: str, result: Optional[str] = None) -> Optional[SessionTask]:
        """Mark a task as completed."""
        with self._lock:
            task = self.get_task(task_id)
            if task:
                # Prevent completing already completed/failed/skipped tasks
                if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED]:
                    logger.warning(f"Task '{task_id}' already in terminal state: {task.status}")
                    return task  # Return task but don't modify
                task.complete(result)
                self.updated_at = datetime.now(timezone.utc)
                self._update_blocked_status()
                logger.info(f"Completed task '{task_id}'")
            return task
    
    def fail_task(self, task_id: str, error: Optional[str] = None) -> Optional[SessionTask]:
        """Mark a task as failed."""
        with self._lock:
            task = self.get_task(task_id)
            if task:
                # Prevent failing already completed/failed/skipped tasks
                if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED]:
                    logger.warning(f"Task '{task_id}' already in terminal state: {task.status}")
                    return task  # Return task but don't modify
                task.fail(error)
                self.updated_at = datetime.now(timezone.utc)
                logger.info(f"Failed task '{task_id}': {error}")
            return task
    
    # ==================== Query Methods ====================
    
    def get_pending_tasks(self) -> List[SessionTask]:
        """Get all pending tasks."""
        with self._lock:
            return [t for t in self.tasks if t.status == TaskStatus.PENDING]
    
    def get_in_progress_tasks(self) -> List[SessionTask]:
        """Get all in-progress tasks."""
        with self._lock:
            return [t for t in self.tasks if t.status == TaskStatus.IN_PROGRESS]
    
    def get_completed_tasks(self) -> List[SessionTask]:
        """Get all completed tasks."""
        with self._lock:
            return [t for t in self.tasks if t.status == TaskStatus.COMPLETED]
    
    def get_blocked_tasks(self) -> List[SessionTask]:
        """Get all blocked tasks."""
        with self._lock:
            return [t for t in self.tasks if t.status == TaskStatus.BLOCKED]
    
    def get_actionable_tasks(self) -> List[SessionTask]:
        """Get tasks that can be started (pending and not blocked)."""
        with self._lock:
            self._update_blocked_status()
            return [t for t in self.tasks if t.is_actionable()]
    
    def get_tasks_by_assignee(self, assignee: str) -> List[SessionTask]:
        """Get tasks assigned to a specific agent/mode."""
        with self._lock:
            return [t for t in self.tasks if t.assigned_to == assignee]
    
    def get_subtasks(self, parent_id: str) -> List[SessionTask]:
        """Get subtasks of a parent task."""
        with self._lock:
            return [t for t in self.tasks if t.parent_id == parent_id]
    
    def get_next_task(self) -> Optional[SessionTask]:
        """Get the next actionable task by priority."""
        actionable = self.get_actionable_tasks()
        if actionable:
            # Sort by priority (lower number = higher priority)
            return sorted(actionable, key=lambda t: t.priority)[0]
        return None
    
    # ==================== Statistics ====================
    
    def get_progress(self) -> Dict[str, Any]:
        """Get task list progress statistics."""
        with self._lock:
            total = len(self.tasks)
            if total == 0:
                return {
                    "total": 0,
                    "completed": 0,
                    "in_progress": 0,
                    "pending": 0,
                    "blocked": 0,
                    "failed": 0,
                    "skipped": 0,
                    "percent_complete": 0.0,
                }
            
            completed = len([t for t in self.tasks if t.status == TaskStatus.COMPLETED])
            in_progress = len([t for t in self.tasks if t.status == TaskStatus.IN_PROGRESS])
            pending = len([t for t in self.tasks if t.status == TaskStatus.PENDING])
            blocked = len([t for t in self.tasks if t.status == TaskStatus.BLOCKED])
            failed = len([t for t in self.tasks if t.status == TaskStatus.FAILED])
            skipped = len([t for t in self.tasks if t.status == TaskStatus.SKIPPED])
            
            return {
                "total": total,
                "completed": completed,
                "in_progress": in_progress,
                "pending": pending,
                "blocked": blocked,
                "failed": failed,
                "skipped": skipped,
                "percent_complete": round((completed / total) * 100, 1),
            }
    
    # ==================== Dependency Management ====================
    
    def _update_blocked_status(self):
        """Update blocked status based on dependencies."""
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        
        for task in self.tasks:
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED, TaskStatus.IN_PROGRESS]:
                continue
            
            if task.dependencies:
                # Check if all dependencies are completed
                unmet = [dep for dep in task.dependencies if dep not in completed_ids]
                if unmet:
                    if task.status != TaskStatus.BLOCKED:
                        task.status = TaskStatus.BLOCKED
                        task._wire_sm("blocked", "_update_blocked_status")
                        task.metadata["unmet_dependencies"] = unmet
                        task.updated_at = datetime.now(timezone.utc)
                else:
                    # All dependencies completed - unblock
                    if task.status == TaskStatus.BLOCKED:
                        task.status = TaskStatus.PENDING
                        task._wire_sm("pending", "_update_blocked_status")
                        task.metadata.pop("unmet_dependencies", None)
                        task.updated_at = datetime.now(timezone.utc)
            else:
                # No dependencies - should be pending if currently blocked
                if task.status == TaskStatus.BLOCKED:
                    task.status = TaskStatus.PENDING
                    task._wire_sm("pending", "_update_blocked_status")
                    task.metadata.pop("unmet_dependencies", None)
                    task.updated_at = datetime.now(timezone.utc)
    
    def add_dependency(self, task_id: str, depends_on: str) -> bool:
        """Add a dependency to a task."""
        with self._lock:
            task = self.get_task(task_id)
            if task and depends_on not in task.dependencies:
                task.dependencies.append(depends_on)
                task.updated_at = datetime.now(timezone.utc)
                self._update_blocked_status()
                return True
            return False
    
    def remove_dependency(self, task_id: str, depends_on: str) -> bool:
        """Remove a dependency from a task."""
        with self._lock:
            task = self.get_task(task_id)
            if task and depends_on in task.dependencies:
                task.dependencies.remove(depends_on)
                task.updated_at = datetime.now(timezone.utc)
                self._update_blocked_status()
                return True
            return False
    
    # ==================== Stale Queue Task Cleanup ====================
    
    STALE_QUEUE_TTL_SECONDS: int = 3600  # 1 hour default
    
    def cleanup_stale_queue_tasks(self, stale_ttl_seconds: int = None) -> int:
        """Mark stale message_queue tasks as FAILED.
        
        Tasks from the message queue (source='message_queue') that are still
        PENDING or IN_PROGRESS after the TTL are considered stale — the
        original message was likely lost (e.g., Docker restart).
        
        Args:
            stale_ttl_seconds: TTL in seconds. Defaults to STALE_QUEUE_TTL_SECONDS.
            
        Returns:
            Number of tasks marked as failed.
        """
        ttl = stale_ttl_seconds or self.STALE_QUEUE_TTL_SECONDS
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)
        cleaned = 0
        
        with self._lock:
            for task in self.tasks:
                if task.status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                    continue
                if task.metadata.get("source") != "message_queue":
                    continue
                if task.created_at and task.created_at < cutoff:
                    task.fail("Stale queue task (exceeded TTL)")
                    cleaned += 1
                    logger.info(f"Cleaned stale queue task '{task.id}': {task.description[:60]}")
        
        return cleaned
    
    # ==================== Serialization ====================
    
    def to_markdown(self) -> str:
        """Convert task list to markdown format."""
        with self._lock:
            progress = self.get_progress()
            lines = [
                f"## Session Tasks ({progress['completed']}/{progress['total']} complete)",
                "",
            ]
            
            if self.mission:
                lines.append(f"**Mission:** {self.mission}")
                lines.append("")
            
            # Group by status
            in_progress = [t for t in self.tasks if t.status == TaskStatus.IN_PROGRESS]
            pending = [t for t in self.tasks if t.status == TaskStatus.PENDING]
            blocked = [t for t in self.tasks if t.status == TaskStatus.BLOCKED]
            completed = [t for t in self.tasks if t.status == TaskStatus.COMPLETED]
            failed = [t for t in self.tasks if t.status == TaskStatus.FAILED]
            
            if in_progress:
                lines.append("### In Progress")
                for task in in_progress:
                    lines.append(task.to_markdown())
                lines.append("")
            
            if pending:
                lines.append("### Pending")
                for task in sorted(pending, key=lambda t: t.priority):
                    lines.append(task.to_markdown())
                lines.append("")
            
            if blocked:
                lines.append("### Blocked")
                for task in blocked:
                    lines.append(task.to_markdown())
                lines.append("")
            
            if completed:
                lines.append("### Completed")
                for task in completed:
                    lines.append(task.to_markdown())
                lines.append("")
            
            if failed:
                lines.append("### Failed")
                for task in failed:
                    lines.append(task.to_markdown())
                lines.append("")
            
            return "\n".join(lines)
    
    def to_summary(self) -> str:
        """Get a brief summary of the task list."""
        progress = self.get_progress()
        return (
            f"Tasks: {progress['completed']}/{progress['total']} complete "
            f"({progress['percent_complete']}%), "
            f"{progress['in_progress']} in progress, "
            f"{progress['blocked']} blocked"
        )
    
    # ==================== Persistence ====================
    
    @classmethod
    def get_path(cls, context_id: str) -> str:
        """Get the file path for a session task list."""
        return get_abs_path(SESSION_TASKS_FOLDER, f"{context_id}.json")
    
    @classmethod
    def load(cls, context_id: str) -> Optional["SessionTaskList"]:
        """Load a session task list from disk."""
        path = cls.get_path(context_id)
        if exists(path):
            try:
                data = read_file(path)
                return cls.model_validate_json(data)
            except Exception as e:
                logger.error(f"Failed to load session tasks for {context_id}: {e}")
        return None
    
    @classmethod
    def load_or_create(cls, context_id: str, mission: str = "", owner: str = "alex") -> "SessionTaskList":
        """Load existing or create new session task list."""
        existing = cls.load(context_id)
        if existing:
            return existing
        return cls(context_id=context_id, mission=mission, owner=owner)
    
    async def save(self) -> "SessionTaskList":
        """Save the session task list to disk."""
        with self._lock:
            path = self.get_path(self.context_id)
            make_dirs(path)
            write_file(path, self.model_dump_json(indent=2))
            logger.debug(f"Saved session tasks for {self.context_id}")
        return self
    
    def save_sync(self) -> "SessionTaskList":
        """Synchronous save for non-async contexts."""
        with self._lock:
            path = self.get_path(self.context_id)
            make_dirs(path)
            write_file(path, self.model_dump_json(indent=2))
            logger.debug(f"Saved session tasks for {self.context_id}")
        return self
    
    @classmethod
    def delete(cls, context_id: str) -> bool:
        """Delete a session task list from disk."""
        import os
        path = cls.get_path(context_id)
        if exists(path):
            try:
                os.remove(path)
                logger.info(f"Deleted session tasks for {context_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete session tasks for {context_id}: {e}")
        return False
    
    # ==================== Boomerang Integration ====================
    
    @classmethod
    def from_boomerang_subtasks(
        cls,
        context_id: str,
        mission: str,
        subtasks: List[Dict[str, Any]],
        owner: str = "orchestrator",
    ) -> "SessionTaskList":
        """Create a session task list from Boomerang decomposed subtasks."""
        task_list = cls(
            context_id=context_id,
            mission=mission,
            owner=owner,
        )
        
        # Map subtask IDs to task IDs for dependency resolution
        id_map: Dict[str, str] = {}
        
        for subtask in subtasks:
            task = SessionTask(
                description=subtask.get("description", ""),
                created_by="orchestrator",
                assigned_to=subtask.get("mode"),
                metadata={
                    "boomerang_id": subtask.get("id"),
                    "mode": subtask.get("mode"),
                },
            )
            id_map[subtask.get("id", "")] = task.id
            task_list.tasks.append(task)
        
        # Resolve dependencies
        for i, subtask in enumerate(subtasks):
            deps = subtask.get("dependencies", [])
            if deps:
                resolved_deps: List[str] = []
                for dep in deps:
                    resolved = id_map.get(dep)
                    if resolved:
                        resolved_deps.append(resolved)
                    elif dep:  # Use original dep if not in map but not empty
                        resolved_deps.append(dep)
                task_list.tasks[i].dependencies = resolved_deps
        
        task_list._update_blocked_status()
        logger.info(f"Created session task list from {len(subtasks)} Boomerang subtasks")
        return task_list


# ==================== Global Access Functions ====================

_session_task_lists: Dict[str, SessionTaskList] = {}
_global_lock = threading.RLock()


def get_session_tasks(context_id: str) -> Optional[SessionTaskList]:
    """Get session task list for a context (from cache or disk)."""
    with _global_lock:
        if context_id in _session_task_lists:
            return _session_task_lists[context_id]
        
        task_list = SessionTaskList.load(context_id)
        if task_list:
            _session_task_lists[context_id] = task_list
        return task_list


def get_or_create_session_tasks(
    context_id: str,
    mission: str = "",
    owner: str = "alex",
) -> SessionTaskList:
    """Get or create session task list for a context.
    
    Also runs stale queue task cleanup on load to prevent
    phantom 'pending' tasks from accumulating in the sidebar.
    """
    with _global_lock:
        if context_id in _session_task_lists:
            return _session_task_lists[context_id]
        
        task_list = SessionTaskList.load_or_create(context_id, mission, owner)
        
        # Fix #4: Clean up stale message_queue tasks on load
        cleaned = task_list.cleanup_stale_queue_tasks()
        if cleaned > 0:
            task_list.save_sync()
            logger.info(f"Cleaned {cleaned} stale queue task(s) for context {context_id}")
        
        _session_task_lists[context_id] = task_list
        return task_list


def clear_session_tasks_cache(context_id: Optional[str] = None):
    """Clear session task list cache."""
    with _global_lock:
        if context_id:
            _session_task_lists.pop(context_id, None)
        else:
            _session_task_lists.clear()


async def save_session_tasks(context_id: str) -> bool:
    """Save session task list for a context."""
    task_list = get_session_tasks(context_id)
    if task_list:
        await task_list.save()
        return True
    return False
