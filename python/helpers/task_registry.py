"""
TaskRegistry — Global singleton for tracking asyncio.Task handles.

Enables the supervisor (3rd agent / IO-Breaker) to target and cancel specific
stuck subordinates within asyncio.wait/gather calls.

Design (from supervisor_io_breaker.md debate consensus):
- Global singleton (thread-safe via asyncio — single event loop)
- Composite IDs: "{agent_name}@{context_id}"
- Methods: register_task, cancel_task, get_task, get_all_active, cleanup_done, clear

Usage:
    from python.helpers.task_registry import TaskRegistry

    registry = TaskRegistry.instance()
    registry.register_task("agent_1@ctx_abc", some_asyncio_task)

    # Supervisor cancels a stuck task:
    registry.cancel_task("agent_1@ctx_abc")

    # Cleanup completed tasks:
    registry.cleanup_done()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger("agix.task_registry")

# Module-level singleton
_instance: Optional[TaskRegistry] = None


class TaskRegistry:
    """Global registry of asyncio.Task handles keyed by composite ID.

    Thread safety: asyncio is single-threaded within an event loop,
    so no locking is needed for standard dict operations.
    """

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}

    @classmethod
    def instance(cls) -> "TaskRegistry":
        """Get or create the singleton TaskRegistry."""
        global _instance
        if _instance is None:
            _instance = cls()
        return _instance

    def register_task(self, composite_id: str, task: asyncio.Task) -> None:
        """Register an asyncio.Task with a composite ID.

        Args:
            composite_id: "{agent_name}@{context_id}" format
            task: The asyncio.Task to track
        """
        self._tasks[composite_id] = task
        logger.debug(f"[TASK REGISTRY] Registered: {composite_id}")

    def cancel_task(self, composite_id: str) -> bool:
        """Cancel a task by its composite ID.

        Args:
            composite_id: The task to cancel

        Returns:
            True if the task was found and cancellation was requested,
            False if the task was not found.
        """
        task = self._tasks.get(composite_id)
        if task is None:
            logger.debug(f"[TASK REGISTRY] cancel_task: {composite_id} not found")
            return False

        if not task.done():
            task.cancel()
            logger.warning(f"[TASK REGISTRY] Cancelled: {composite_id}")
        else:
            logger.debug(
                f"[TASK REGISTRY] cancel_task: {composite_id} already done"
            )

        # Remove from registry
        self._tasks.pop(composite_id, None)
        return True

    def get_task(self, composite_id: str) -> Optional[asyncio.Task]:
        """Get a task by its composite ID.

        Returns:
            The asyncio.Task, or None if not found.
        """
        return self._tasks.get(composite_id)

    def get_all_active(self) -> Dict[str, asyncio.Task]:
        """Get all active (not done) tasks.

        Returns:
            Dict of composite_id → asyncio.Task for tasks that are still running.
        """
        return {
            cid: task
            for cid, task in self._tasks.items()
            if not task.done()
        }

    def cleanup_done(self) -> int:
        """Remove completed/cancelled tasks from the registry.

        Returns:
            Number of tasks removed.
        """
        done_ids = [
            cid for cid, task in self._tasks.items()
            if task.done()
        ]
        for cid in done_ids:
            del self._tasks[cid]

        if done_ids:
            logger.debug(
                f"[TASK REGISTRY] Cleaned up {len(done_ids)} done tasks: "
                f"{done_ids}"
            )
        return len(done_ids)

    def clear(self) -> None:
        """Cancel all tasks and clear the registry.

        Used for shutdown or test cleanup.
        """
        for cid, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                logger.debug(f"[TASK REGISTRY] clear: cancelled {cid}")
        self._tasks.clear()
