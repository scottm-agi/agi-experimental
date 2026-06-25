from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response
from python.helpers.lifecycle_service import LifecycleService


class RemoveChat(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("context", "")
        import logging
        logger = logging.getLogger("agix.api.chat_remove")
        logger.info(f"Removing context {ctxid}")

        # Route through centralized LifecycleService — single authoritative
        # deletion path for memory + disk + SQL + tasks.
        result = await LifecycleService.delete_chat(ctxid)

        return {
            "success": True,
            "message": "Context removed.",
            "details": result,
        }

