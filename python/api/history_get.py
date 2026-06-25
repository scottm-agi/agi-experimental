from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response


class GetHistory(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        ctxid = input.get("context", [])
        context = await self.use_context(ctxid)
        agent = context.streaming_agent or context.agent0
        fmt = input.get("format", "text")

        if fmt == "json":
            history = agent.history.serialize()
        elif fmt == "markdown":
            history = agent.history.output_markdown()
        else:
            history = agent.history.output_text()

        size = agent.history.get_tokens()

        return {
            "history": history,
            "tokens": size
        }