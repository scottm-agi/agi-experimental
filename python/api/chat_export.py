from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response

from python.helpers import persist_chat

class ExportChat(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("ctxid", "")
        if not ctxid:
            raise Exception("No context id provided")

        # Support 'format' parameter, default to 'md'
        export_format = input.get("format", "md").lower()
        
        context = await self.use_context(ctxid)
        
        if export_format == "json":
            content = persist_chat.export_json_chat(context)
            extension = "json"
        else:
            content = persist_chat.export_markdown_chat(context)
            extension = "md"
            
        return {
            "message": f"Chats exported as {export_format.upper()}.",
            "ctxid": context.id,
            "content": content,
            "format": export_format,
            "extension": extension
        }