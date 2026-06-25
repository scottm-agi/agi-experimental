from __future__ import annotations
from python.agent import AgentContext
from python.helpers.api import ApiHandler, Request, Response
from python.helpers.persist_chat import save_tmp_chat

class MessageDelete(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        message_id = input.get("message_id")
        ctxid = input.get("context")

        if not message_id or not ctxid:
            return Response("Missing message_id or context", 400)

        # Obtain agent context
        context = await self.use_context(ctxid)
        if not context:
            return Response("Context not found", 404)

        # Remove from log
        log_removed = context.log.remove_item(message_id)
        
        # Remove from python.history
        history_removed = context.agent0.history.remove_message(message_id)

        # Save context to persist changes
        save_tmp_chat(context)

        return {
            "success": True,
            "log_removed": log_removed,
            "history_removed": history_removed,
            "message_id": message_id
        }
