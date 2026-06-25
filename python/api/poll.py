from __future__ import annotations
import threading
from typing import Dict, Any
from python.helpers.api import ApiHandler, Request, Response
from python.agent import AgentContext, AgentContextType

from python.helpers.task_scheduler import TaskScheduler
from python.helpers.localization import Localization
from python.helpers.dotenv_manager import get_dotenv_value

# Caching for context list to avoid recomputing on every poll
# We use a cache keyed by (combined_version, timezone)
_context_list_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


class Poll(ApiHandler):

    async def process(self, input: dict, request: Request) -> dict | Response:
        global _context_list_cache
        
        ctxid = input.get("context", "")
        from_no = input.get("log_from", 0)
        log_guid = input.get("log_guid", "") # Accept log_guid from frontend
        notifications_from = input.get("notifications_from", 0)
        
        # New: differential polling versions
        contexts_version = input.get("contexts_version", -1)
        tasks_version = input.get("tasks_version", -1)
        
        # Light mode: only return minimal data for navigation list
        light_mode = input.get("light_mode", False)

        # Get current global versions
        current_contexts_version = AgentContext._global_version
        current_tasks_version = TaskScheduler._global_version

        # Get timezone from input (default to dotenv default or UTC if not provided)
        timezone = input.get("timezone", get_dotenv_value("DEFAULT_USER_TIMEZONE", "UTC"))
        Localization.get().set_timezone(timezone)

        # context instance - get or create only if ctxid is provided
        if ctxid:
            try:
                context = await self.use_context(ctxid, create_if_not_exists=False)
            except Exception as e:
                context = None
        else:
            context = None

        # Get logs only if we have a context (and not in light mode)
        logs = []
        if context and not light_mode:
            # If GUID changed (e.g. after prune), return more logs to restore UI state
            # Also increase default limit from 50 to 100 to align with frontend cache
            if log_guid and log_guid != context.log.guid:
                from_no = 0 
            
            logs = context.log.output(start=from_no, limit=100 if from_no == 0 else None)

        # Get notifications from global notification manager
        notification_manager = AgentContext.get_notification_manager()
        notifications = notification_manager.output(start=notifications_from)

        # Skip contexts/tasks serialization if versions match (use cached values)
        ctxs = None
        tasks = None
        
        # Combined version for cache invalidation
        combined_version = f"{current_contexts_version}_{current_tasks_version}"
        cache_key = f"{combined_version}_{timezone}"

        if contexts_version != current_contexts_version or tasks_version != current_tasks_version:
            # 1. Check if we can use cached list
            with _cache_lock:
                cached = _context_list_cache.get(cache_key)
                if cached:
                    ctxs = cached["ctxs"]
                    tasks = cached["tasks"]

            if ctxs is None:
                # 2. Not in cache, compute it
                # Get a task scheduler instance
                scheduler = TaskScheduler.get()

                ctxs = []
                tasks = []
                processed_contexts = set()  # Track processed context IDs

                # Start with all tasks from the scheduler
                all_scheduler_tasks = scheduler.get_tasks()
                for task in all_scheduler_tasks:
                    task_data = scheduler.serialize_task(task.uuid)
                    if task_data:
                        # Add light context info if the context is loaded
                        ctx = AgentContext.get(task.context_id) if task.context_id else AgentContext.get(task.uuid)
                        if ctx:
                            light_info = ctx.output_light()
                            # Do not overwrite specialized task type with generic context type
                            if "type" in light_info:
                                del light_info["type"]
                            task_data.update(light_info)
                        
                        tasks.append(task_data)
                        processed_contexts.add(task.context_id or task.uuid)

                # Then add remaining regular chats from metadata registry
                # (includes ALL contexts, not just in-memory LRU ones)
                all_metadata = dict(AgentContext._context_metadata)
                for ctx_id, meta in all_metadata.items():
                    # Skip if already processed as a task
                    if ctx_id in processed_contexts:
                        continue

                    # Skip BACKGROUND contexts
                    ctx_type_str = meta.get("type", "user")
                    if ctx_type_str == AgentContextType.BACKGROUND.value:
                        processed_contexts.add(ctx_id)
                        continue

                    # Check if this is a task context
                    context_task = scheduler.get_task_by_uuid(ctx_id)
                    is_task_context = (
                        context_task is not None and (context_task.context_id == ctx_id or context_task.uuid == ctx_id)
                    )

                    if not is_task_context:
                        # Use full output_light() if context is in memory, else use metadata
                        in_memory_ctx = AgentContext._contexts.get(ctx_id)
                        if in_memory_ctx:
                            ctxs.append(in_memory_ctx.output_light())
                        else:
                            ctxs.append(AgentContext.output_light_from_metadata(meta))
                    else:
                        # Shouldn't be reached if all scheduler tasks are processed above
                        task_data = scheduler.serialize_task(context_task.uuid)
                        if task_data:
                            in_memory_ctx = AgentContext._contexts.get(ctx_id)
                            if in_memory_ctx:
                                light_info = in_memory_ctx.output_light()
                            else:
                                light_info = AgentContext.output_light_from_metadata(meta)
                            if "type" in light_info:
                                del light_info["type"]
                            task_data.update(light_info)
                            tasks.append(task_data)

                    processed_contexts.add(ctx_id)

                # Sort tasks and chats by their creation date, descending
                ctxs.sort(key=lambda x: x["created_at"], reverse=True)
                tasks.sort(key=lambda x: x["created_at"], reverse=True)
                
                # 3. Update cache
                with _cache_lock:
                    # Optional: Clean up cache if it gets too large (but keep recent ones)
                    if len(_context_list_cache) > 20: 
                        _context_list_cache.clear()
                    
                    _context_list_cache[cache_key] = {
                        "ctxs": ctxs,
                        "tasks": tasks
                    }

        # data from this server
        return {
            "deselect_chat": ctxid and not context,
            "context": context.id if context else "",
            "contexts": ctxs,
            "contexts_version": current_contexts_version,
            "tasks": tasks,
            "tasks_version": current_tasks_version,
            "logs": logs,
            "log_guid": context.log.guid if context else "",
            "log_version": len(context.log.updates) if context else 0,
            "log_progress": context.log.progress if context else 0,
            "log_progress_active": context.log.progress_active if context else False,
            "paused": context.paused if context else False,
            "agent_idle": (not context) or (not context.task) or (not context.task.is_alive()),
            "notifications": notifications,
            "notifications_guid": notification_manager.guid,
            "notifications_version": len(notification_manager.updates),
        }
