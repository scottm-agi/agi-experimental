from __future__ import annotations
from typing import Any, List, Optional, Union
from python.helpers.tool import Tool, Response
from python.helpers.task_state import TaskStateManager

class TaskStateTool(Tool):
    """Tool for managing persistent task state across scheduled runs."""

    async def execute(self, **kwargs) -> Response:
        """Execute the task state tool method."""
        method = self.method
        
        # Determine context_id from agent
        context_id = self.agent.context.id
        manager = TaskStateManager.get_for_context(context_id)
        
        if not manager:
            return Response(
                message="Task state management is only available for scheduled tasks with a registered task UUID.",
                break_loop=False
            )

        if method == "get_value":
            return self.get_value(manager, **kwargs)
        elif method == "set_value":
            return self.set_value(manager, **kwargs)
        elif method == "track_id":
            return self.track_id(manager, **kwargs)
        elif method == "is_tracked":
            return self.is_tracked(manager, **kwargs)
        elif method == "clear_key":
            return self.clear_key(manager, **kwargs)
        else:
            return Response(
                message=f"Unknown method '{method}' for TaskStateTool",
                break_loop=False
            )

    def get_value(self, manager: TaskStateManager, **kwargs) -> Response:
        key = kwargs.get("key")
        if not key:
            return Response(message="Key is required", break_loop=False)
        value = manager.get_value(key)
        return Response(message=str(value), break_loop=False)

    def set_value(self, manager: TaskStateManager, **kwargs) -> Response:
        key = kwargs.get("key")
        value = kwargs.get("value")
        if not key:
            return Response(message="Key is required", break_loop=False)
        manager.set_value(key, value)
        return Response(message=f"Set {key} to {value}", break_loop=False)

    def track_id(self, manager: TaskStateManager, **kwargs) -> Response:
        key = kwargs.get("key")
        item_id = kwargs.get("item_id")
        if not key or not item_id:
            return Response(message="Key and item_id are required", break_loop=False)
        manager.track_id(key, item_id)
        return Response(message=f"Tracked ID {item_id} in {key}", break_loop=False)

    def is_tracked(self, manager: TaskStateManager, **kwargs) -> Response:
        key = kwargs.get("key")
        item_id = kwargs.get("item_id")
        if not key or not item_id:
            return Response(message="Key and item_id are required", break_loop=False)
        tracked = manager.is_tracked(key, item_id)
        return Response(message=str(tracked), break_loop=False)

    def clear_key(self, manager: TaskStateManager, **kwargs) -> Response:
        key = kwargs.get("key")
        if not key:
            return Response(message="Key is required", break_loop=False)
        manager.clear_key(key)
        return Response(message=f"Cleared {key}", break_loop=False)
