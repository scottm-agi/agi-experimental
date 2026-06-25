from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response
from python.agent import AgentContext
from python.helpers import persist_chat
from python.helpers.task_scheduler import TaskScheduler
from python.helpers.persistence_manager import PersistenceManager

# Import at top-level to avoid fragility from lazy imports inside loops.
# Fallback guards against sys.modules pollution from test stubs (#RCA-230).
try:
    from python.helpers.projects import CONTEXT_DATA_KEY_PROJECT
except (ImportError, AttributeError):
    CONTEXT_DATA_KEY_PROJECT = "project"

class RemoveChatBulk(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        contexts = input.get("contexts", [])
        if not contexts:
            return Response("No contexts provided", 400)

        removed = []
        errors = []
        
        scheduler = TaskScheduler.get()
        # No need to reload here if we are just removing by UUID
        # but let's be safe as RemoveChat does it
        await scheduler.reload()

        for ctxid in contexts:
            try:
                # 1. Obtain project name BEFORE reset, then reset context
                project_name = None
                context = AgentContext.use(ctxid)
                if context:
                    project_name = context.get_data(CONTEXT_DATA_KEY_PROJECT)
                    context.reset()
                
                # 2. Remove from AgentContext (memory/heartbeat)
                AgentContext.remove(ctxid)
                
                # 3. Remove from disk (history files)
                persist_chat.remove_chat(ctxid)
                
                # 3b. Remove from SQL database
                try:
                    await PersistenceManager.get_instance().delete_context_sql(ctxid)
                except Exception:
                    pass  # Best-effort — don't skip task cleanup below
                
                # 4. Remove associated tasks from scheduler
                tasks = scheduler.get_tasks_by_context_id(ctxid)
                for task in tasks:
                    await scheduler.remove_task_by_uuid(task.uuid)

                removed.append(ctxid)
            except Exception as e:
                errors.append({"ctxid": ctxid, "error": str(e)})

        return {
            "success": len(errors) == 0,
            "removed": removed,
            "errors": errors
        }
