from __future__ import annotations
"""
Session Tasks API Endpoints for AGIX

This module provides REST API endpoints for managing per-session task/todo lists.
These endpoints enable the WebUI to display and manage tasks for each chat session.

Endpoints:
- GET  /api/session_tasks/{context_id} - Get all tasks for a session
- POST /api/session_tasks/{context_id} - Add a new task
- GET  /api/session_tasks/{context_id}/progress - Get progress statistics
- GET  /api/session_tasks/{context_id}/next - Get next actionable task
- PATCH /api/session_tasks/{context_id}/{task_id} - Update a task
- DELETE /api/session_tasks/{context_id}/{task_id} - Remove a task
- POST /api/session_tasks/{context_id}/{task_id}/start - Start a task
- POST /api/session_tasks/{context_id}/{task_id}/complete - Complete a task
- POST /api/session_tasks/{context_id}/{task_id}/fail - Fail a task
- POST /api/session_tasks/{context_id}/mission - Set mission statement
"""

import logging
from typing import Any, Dict, List, Optional

from python.helpers.session_tasks import (
    SessionTaskList,
    SessionTask,
    TaskStatus,
    TaskPriority,
    get_session_tasks,
    get_or_create_session_tasks,
    save_session_tasks,
    clear_session_tasks_cache,
)

logger = logging.getLogger(__name__)


# =============================================================================
# API Handler Functions
# =============================================================================

async def get_tasks(context_id: str, status: Optional[str] = None, assigned_to: Optional[str] = None) -> Dict[str, Any]:
    """
    Get all tasks for a session.
    
    Args:
        context_id: The context/chat ID
        status: Optional status filter
        assigned_to: Optional assignee filter
        
    Returns:
        Dict with success flag and tasks
    """
    task_list = get_or_create_session_tasks(context_id)
    
    tasks = task_list.tasks
    
    # Apply filters
    if status:
        try:
            status_enum = TaskStatus(status)
            tasks = [t for t in tasks if t.status == status_enum]
        except ValueError:
            return {
                "success": False,
                "message": f"Invalid status: {status}. Valid values: {[s.value for s in TaskStatus]}",
            }
    
    if assigned_to:
        tasks = [t for t in tasks if t.assigned_to == assigned_to]
    
    return {
        "success": True,
        "context_id": context_id,
        "mission": task_list.mission,
        "owner": task_list.owner,
        "tasks": [
            {
                "id": t.id,
                "description": t.description,
                "status": t.status.value,
                "priority": t.priority,
                "assigned_to": t.assigned_to,
                "created_by": t.created_by,
                "parent_id": t.parent_id,
                "dependencies": t.dependencies,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "result": t.result,
                "error": t.error,
                "metadata": t.metadata,
            }
            for t in tasks
        ],
        "count": len(tasks),
    }


async def add_task(
    context_id: str,
    description: str,
    priority: int = 3,
    assigned_to: Optional[str] = None,
    dependencies: Optional[List[str]] = None,
    parent_id: Optional[str] = None,
    created_by: str = "user",
) -> Dict[str, Any]:
    """
    Add a new task to a session.
    
    Args:
        context_id: The context/chat ID
        description: Task description
        priority: Priority level (1-5)
        assigned_to: Agent/mode to assign
        dependencies: List of task IDs that must complete first
        parent_id: Parent task ID for subtasks
        created_by: Who created the task
        
    Returns:
        Dict with success flag and task info
    """
    if not description:
        return {
            "success": False,
            "message": "Task description is required",
        }
    
    # Validate priority is an integer in range 1-5
    if not isinstance(priority, int):
        return {
            "success": False,
            "message": f"Priority must be an integer, got: {type(priority).__name__}",
        }
    if priority < 1 or priority > 5:
        return {
            "success": False,
            "message": f"Priority must be between 1 and 5, got: {priority}",
        }
    
    task_list = get_or_create_session_tasks(context_id)
    
    # Validate dependencies exist
    if dependencies:
        existing_ids = {t.id for t in task_list.tasks}
        missing = [d for d in dependencies if d not in existing_ids]
        if missing:
            return {
                "success": False,
                "message": f"Dependencies not found: {missing}",
            }
    
    task = task_list.add_task(
        description=description,
        created_by=created_by,
        assigned_to=assigned_to,
        priority=priority,
        parent_id=parent_id,
        dependencies=dependencies or [],
    )
    
    await task_list.save()
    
    return {
        "success": True,
        "message": f"Task added: {task.id}",
        "task": {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority,
            "assigned_to": task.assigned_to,
            "created_by": task.created_by,
            "dependencies": task.dependencies,
        },
    }


async def update_task(
    context_id: str,
    task_id: str,
    description: Optional[str] = None,
    priority: Optional[int] = None,
    assigned_to: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update a task.
    
    Args:
        context_id: The context/chat ID
        task_id: Task ID to update
        description: New description
        priority: New priority
        assigned_to: New assignee
        status: New status
        
    Returns:
        Dict with success flag and updated task
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": False,
            "message": f"No task list found for context: {context_id}",
        }
    
    task = task_list.get_task(task_id)
    if not task:
        return {
            "success": False,
            "message": f"Task not found: {task_id}",
        }
    
    # Build updates
    updates: Dict[str, Any] = {}
    if description is not None:
        updates["description"] = description
    if priority is not None:
        updates["priority"] = priority
    if assigned_to is not None:
        updates["assigned_to"] = assigned_to
    if status is not None:
        try:
            updates["status"] = TaskStatus(status)
        except ValueError:
            return {
                "success": False,
                "message": f"Invalid status: {status}",
            }
    
    if not updates:
        return {
            "success": False,
            "message": "No updates provided",
        }
    
    task_list.update_task(task_id, **updates)
    await task_list.save()
    
    # Refresh task
    task = task_list.get_task(task_id)
    
    return {
        "success": True,
        "message": f"Task updated: {task_id}",
        "task": {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority,
            "assigned_to": task.assigned_to,
        } if task else None,
    }


async def remove_task(context_id: str, task_id: str) -> Dict[str, Any]:
    """
    Remove a task from a session.
    
    Args:
        context_id: The context/chat ID
        task_id: Task ID to remove
        
    Returns:
        Dict with success flag
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": False,
            "message": f"No task list found for context: {context_id}",
        }
    
    removed = task_list.remove_task(task_id)
    
    if not removed:
        return {
            "success": False,
            "message": f"Task not found: {task_id}",
        }
    
    await task_list.save()
    
    return {
        "success": True,
        "message": f"Task removed: {task_id}",
    }


async def bulk_remove_tasks(context_id: str, task_ids: List[str]) -> Dict[str, Any]:
    """
    Remove multiple tasks from a session.
    
    Args:
        context_id: The context/chat ID
        task_ids: List of task IDs to remove
        
    Returns:
        Dict with success flag and count of removed tasks
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": False,
            "message": f"No task list found for context: {context_id}",
        }
    
    if not task_ids:
        return {
            "success": True,
            "message": "No tasks to remove",
            "count": 0,
        }

    count = task_list.remove_tasks(task_ids)
    
    if count > 0:
        await task_list.save()
    
    return {
        "success": True,
        "message": f"Bulk removed {count} tasks",
        "count": count,
    }


async def start_task(context_id: str, task_id: str, assigned_to: Optional[str] = None) -> Dict[str, Any]:
    """
    Start a task (mark as in progress).
    
    Args:
        context_id: The context/chat ID
        task_id: Task ID to start
        assigned_to: Who is working on it
        
    Returns:
        Dict with success flag and task info
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": False,
            "message": f"No task list found for context: {context_id}",
        }
    
    task = task_list.start_task(task_id, assigned_to)
    
    if not task:
        return {
            "success": False,
            "message": f"Task not found or cannot be started: {task_id}",
        }
    
    await task_list.save()
    
    return {
        "success": True,
        "message": f"Task started: {task_id}",
        "task": {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "assigned_to": task.assigned_to,
        },
    }


async def complete_task(context_id: str, task_id: str, result: Optional[str] = None) -> Dict[str, Any]:
    """
    Complete a task.
    
    Args:
        context_id: The context/chat ID
        task_id: Task ID to complete
        result: Summary of what was accomplished
        
    Returns:
        Dict with success flag and progress info
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": False,
            "message": f"No task list found for context: {context_id}",
        }
    
    task = task_list.complete_task(task_id, result)
    
    if not task:
        return {
            "success": False,
            "message": f"Task not found: {task_id}",
        }
    
    await task_list.save()
    
    progress = task_list.get_progress()
    
    return {
        "success": True,
        "message": f"Task completed: {task_id}",
        "task": {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "result": task.result,
        },
        "progress": progress,
    }


async def fail_task(context_id: str, task_id: str, error: Optional[str] = None) -> Dict[str, Any]:
    """
    Fail a task.
    
    Args:
        context_id: The context/chat ID
        task_id: Task ID that failed
        error: Error message
        
    Returns:
        Dict with success flag
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": False,
            "message": f"No task list found for context: {context_id}",
        }
    
    task = task_list.fail_task(task_id, error)
    
    if not task:
        return {
            "success": False,
            "message": f"Task not found: {task_id}",
        }
    
    await task_list.save()
    
    return {
        "success": True,
        "message": f"Task failed: {task_id}",
        "task": {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "error": task.error,
        },
    }


async def get_progress(context_id: str) -> Dict[str, Any]:
    """
    Get progress statistics for a session.
    
    Args:
        context_id: The context/chat ID
        
    Returns:
        Dict with progress statistics
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": True,
            "context_id": context_id,
            "progress": {
                "total": 0,
                "completed": 0,
                "in_progress": 0,
                "pending": 0,
                "blocked": 0,
                "failed": 0,
                "skipped": 0,
                "percent_complete": 0.0,
            },
        }
    
    return {
        "success": True,
        "context_id": context_id,
        "mission": task_list.mission,
        "progress": task_list.get_progress(),
    }


async def get_next_task(context_id: str, assigned_to: Optional[str] = None) -> Dict[str, Any]:
    """
    Get the next actionable task.
    
    Args:
        context_id: The context/chat ID
        assigned_to: Optional filter by assignee
        
    Returns:
        Dict with next task info
    """
    task_list = get_session_tasks(context_id)
    
    if not task_list:
        return {
            "success": True,
            "context_id": context_id,
            "next_task": None,
            "message": "No task list found",
        }
    
    if assigned_to:
        actionable = [
            t for t in task_list.get_actionable_tasks()
            if t.assigned_to == assigned_to or t.assigned_to is None
        ]
        task = sorted(actionable, key=lambda t: t.priority)[0] if actionable else None
    else:
        task = task_list.get_next_task()
    
    if not task:
        return {
            "success": True,
            "context_id": context_id,
            "next_task": None,
            "message": "No actionable tasks",
        }
    
    return {
        "success": True,
        "context_id": context_id,
        "next_task": {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority,
            "assigned_to": task.assigned_to,
            "dependencies": task.dependencies,
        },
    }


async def set_mission(context_id: str, mission: str) -> Dict[str, Any]:
    """
    Set the mission statement for a session.
    
    Args:
        context_id: The context/chat ID
        mission: Mission description
        
    Returns:
        Dict with success flag
    """
    if not mission:
        return {
            "success": False,
            "message": "Mission description is required",
        }
    
    task_list = get_or_create_session_tasks(context_id)
    task_list.mission = mission
    await task_list.save()
    
    return {
        "success": True,
        "message": "Mission set",
        "mission": mission,
    }


async def delete_task_list(context_id: str) -> Dict[str, Any]:
    """
    Delete the entire task list for a session.
    
    Args:
        context_id: The context/chat ID
        
    Returns:
        Dict with success flag
    """
    deleted = SessionTaskList.delete(context_id)
    clear_session_tasks_cache(context_id)
    
    return {
        "success": deleted,
        "message": "Task list deleted" if deleted else "Task list not found",
    }


# =============================================================================
# Route Registration
# =============================================================================

def register_session_tasks_routes(app):
    """
    Register session tasks routes with a Flask app.
    
    Args:
        app: Flask application instance
    """
    from flask import request, jsonify
    
    # GET /api/session_tasks/{context_id} - Get all tasks
    @app.route("/api/session_tasks/<context_id>", methods=["GET"])
    async def api_get_tasks(context_id: str):
        status = request.args.get("status")
        assigned_to = request.args.get("assigned_to")
        result = await get_tasks(context_id, status=status, assigned_to=assigned_to)
        return jsonify(result)
    
    # POST /api/session_tasks/{context_id} - Add a task
    @app.route("/api/session_tasks/<context_id>", methods=["POST"])
    async def api_add_task(context_id: str):
        data = request.get_json() or {}
        result = await add_task(
            context_id=context_id,
            description=data.get("description", ""),
            priority=data.get("priority", 3),
            assigned_to=data.get("assigned_to"),
            dependencies=data.get("dependencies"),
            parent_id=data.get("parent_id"),
            created_by=data.get("created_by", "user"),
        )
        return jsonify(result)
    
    # GET /api/session_tasks/{context_id}/progress - Get progress
    @app.route("/api/session_tasks/<context_id>/progress", methods=["GET"])
    async def api_get_progress(context_id: str):
        result = await get_progress(context_id)
        return jsonify(result)
    
    # GET /api/session_tasks/{context_id}/next - Get next task
    @app.route("/api/session_tasks/<context_id>/next", methods=["GET"])
    async def api_get_next_task(context_id: str):
        assigned_to = request.args.get("assigned_to")
        result = await get_next_task(context_id, assigned_to=assigned_to)
        return jsonify(result)
    
    # PATCH /api/session_tasks/{context_id}/{task_id} - Update task
    @app.route("/api/session_tasks/<context_id>/<task_id>", methods=["PATCH"])
    async def api_update_task(context_id: str, task_id: str):
        data = request.get_json() or {}
        result = await update_task(
            context_id=context_id,
            task_id=task_id,
            description=data.get("description"),
            priority=data.get("priority"),
            assigned_to=data.get("assigned_to"),
            status=data.get("status"),
        )
        return jsonify(result)
    
    # DELETE /api/session_tasks/{context_id}/{task_id} - Remove task
    @app.route("/api/session_tasks/<context_id>/<task_id>", methods=["DELETE"])
    async def api_remove_task(context_id: str, task_id: str):
        result = await remove_task(context_id, task_id)
        return jsonify(result)
    
    # POST /api/session_tasks/<context_id>/bulk_delete - Bulk remove tasks
    @app.route("/api/session_tasks/<context_id>/bulk_delete", methods=["POST"])
    async def api_bulk_remove_tasks(context_id: str):
        data = request.get_json() or {}
        task_ids = data.get("task_ids", [])
        result = await bulk_remove_tasks(context_id, task_ids)
        return jsonify(result)
    
    # POST /api/session_tasks/<context_id>/<task_id>/start - Start task
    @app.route("/api/session_tasks/<context_id>/<task_id>/start", methods=["POST"])
    async def api_start_task(context_id: str, task_id: str):
        data = request.get_json() or {}
        result = await start_task(
            context_id=context_id,
            task_id=task_id,
            assigned_to=data.get("assigned_to"),
        )
        return jsonify(result)
    
    # POST /api/session_tasks/<context_id>/<task_id>/complete - Complete task
    @app.route("/api/session_tasks/<context_id>/<task_id>/complete", methods=["POST"])
    async def api_complete_task(context_id: str, task_id: str):
        data = request.get_json() or {}
        result = await complete_task(
            context_id=context_id,
            task_id=task_id,
            result=data.get("result"),
        )
        return jsonify(result)
    
    # POST /api/session_tasks/<context_id>/<task_id>/fail - Fail task
    @app.route("/api/session_tasks/<context_id>/<task_id>/fail", methods=["POST"])
    async def api_fail_task(context_id: str, task_id: str):
        data = request.get_json() or {}
        result = await fail_task(
            context_id=context_id,
            task_id=task_id,
            error=data.get("error"),
        )
        return jsonify(result)
    
    # POST /api/session_tasks/<context_id>/mission - Set mission
    @app.route("/api/session_tasks/<context_id>/mission", methods=["POST"])
    async def api_set_mission(context_id: str):
        data = request.get_json() or {}
        result = await set_mission(
            context_id=context_id,
            mission=data.get("mission", ""),
        )
        return jsonify(result)
    
    # DELETE /api/session_tasks/<context_id> - Delete task list
    @app.route("/api/session_tasks/<context_id>", methods=["DELETE"])
    async def api_delete_task_list(context_id: str):
        result = await delete_task_list(context_id)
        return jsonify(result)
    
    logger.info("Session Tasks API routes registered")
