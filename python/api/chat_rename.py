from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response


class RenameChat(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("context_id", "")
        new_name = input.get("name", "")

        if not ctxid or not new_name:
            return {"ok": False, "message": "context_id and name are required."}

        # Load context, set name, persist
        from python.helpers import persist_chat
        context = await persist_chat.load_chat(ctxid)
        if not context:
            return {"ok": False, "message": f"Context {ctxid} not found."}

        context.name = new_name
        persist_chat.save_tmp_chat(context)

        # Also persist to SQL
        try:
            from python.helpers.persistence_manager import PersistenceManager
            from python.helpers.persist_chat import _serialize_context
            pm = PersistenceManager.get_instance()
            await pm.save_context_sql(_serialize_context(context))
        except Exception:
            pass  # SQL persistence is best-effort

        return {"ok": True, "message": f"Chat renamed to '{new_name}'."}

