from __future__ import annotations
import os
import sys
import json
import threading
from typing import Any, Dict, List, Optional, Union
from python.helpers.files import get_abs_path, write_file_atomic, read_file, make_dirs
from python.helpers.task_scheduler import TaskScheduler

import logging

logger = logging.getLogger(__name__)

SCHEDULER_FOLDER = "data/scheduler"

class TaskStateManager:
    """Manages persistent state for scheduled tasks."""
    
    _lock = threading.RLock()
    _instances: Dict[str, TaskStateManager] = {}

    @classmethod
    def get_for_context(cls, context_id: str) -> Optional[TaskStateManager]:
        """Get or create a TaskStateManager for the given context ID."""
        with cls._lock:
            # First, find the task associated with this context
            scheduler = TaskScheduler.get()
            tasks = scheduler.get_tasks_by_context_id(context_id)
            if not tasks:
                logger.debug("No tasks found for context ID: %s", context_id)
                return None
            
            task_uuid = tasks[0].uuid
            logger.debug("Found task %s for context %s", task_uuid, context_id)
            if task_uuid not in cls._instances:
                cls._instances[task_uuid] = cls(task_uuid)
            return cls._instances[task_uuid]

    def __init__(self, task_uuid: str):
        self.task_uuid = task_uuid
        # State is stored at the task level, not context level, to survive rotations
        self.state_path = get_abs_path(SCHEDULER_FOLDER, task_uuid, "task_state.json")
        self._cache: Dict[str, Any] = {}
        self._load()

    def _load(self):
        """Load state from disk."""
        if os.path.exists(self.state_path):
            try:
                content = read_file(self.state_path)
                if content:
                    self._cache = json.loads(content)
            except Exception:
                self._cache = {}
        else:
            self._cache = {}

    def save(self):
        """Save state to disk."""
        logger.debug("Saving state to: %s", self.state_path)
        make_dirs(os.path.dirname(self.state_path))
        write_file_atomic(self.state_path, json.dumps(self._cache, indent=2))
        logger.debug("Saved %d keys to %s", len(self._cache), self.state_path)

    def get_value(self, key: str, default: Any = None) -> Any:
        """Get a value from the state."""
        with self._lock:
            return self._cache.get(key, default)

    def set_value(self, key: str, value: Any):
        """Set a value in the state."""
        with self._lock:
            logger.debug("Setting %s=%s", key, value)
            self._cache[key] = value
            self.save()

    def track_id(self, key: str, item_id: str):
        """Add an ID to a list/set of tracked IDs."""
        with self._lock:
            logger.debug("Tracking ID %s for key %s", item_id, key)
            if key not in self._cache:
                self._cache[key] = []
            if item_id not in self._cache[key]:
                self._cache[key].append(item_id)
                # Cap the size of the set to prevent bloat (e.g., last 5000 IDs)
                if len(self._cache[key]) > 5000:
                    self._cache[key] = self._cache[key][-5000:]
                self.save()

    def is_tracked(self, key: str, item_id: str) -> bool:
        """Check if an ID is in the tracked set."""
        with self._lock:
            if key not in self._cache:
                return False
            return item_id in self._cache[key]

    def clear_key(self, key: str):
        """Clear a specific key from state."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self.save()
