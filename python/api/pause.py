from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response


class Pause(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
            # input data
            paused = input.get("paused", False)
            ctxid = input.get("context", "")

            # context instance - get or create
            context = await self.use_context(ctxid)

            context.paused = paused

            return {
                "message": "Agent paused." if paused else "Agent unpaused.",
                "pause": paused,
            }
