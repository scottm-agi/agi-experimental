from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response
from python.agent import AgentContext, AgentContextType


class ChatList(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: Input, request: Request) -> Output:
        chats = []
        
        # Issue #1070 Fix: Use _context_metadata registry instead of _contexts.
        # _contexts is an LRU OrderedDict capped at MAX_CONTEXTS_IN_MEMORY (default 25),
        # so it only contains the most recently accessed chats.
        # _context_metadata contains ALL registered chats (never evicted),
        # ensuring the History Settings tab shows the full chat list.
        all_metadata = dict(AgentContext._context_metadata)
        
        for ctx_id, meta in all_metadata.items():
            # Skip BACKGROUND contexts as they should be invisible to users
            ctx_type_str = meta.get("type", "user")
            if ctx_type_str == AgentContextType.BACKGROUND.value:
                continue
            
            # If context is in memory, use full output() for richer data
            in_memory_ctx = AgentContext._contexts.get(ctx_id)
            if in_memory_ctx:
                data = in_memory_ctx.output()
            else:
                # Use lightweight metadata output for evicted contexts
                data = AgentContext.output_light_from_metadata(meta)
            
            # Map fields for frontend compatibility in the History tab
            # history-settings-store.js expects 'ctxid' and 'updated_at'
            data["ctxid"] = data["id"]
            data["updated_at"] = data.get("last_message") or data.get("created_at")
            
            chats.append(data)
            
        return {
            "success": True,
            "chats": chats
        }
