"""
API endpoint: browser_agent_status (Issue #723)

Returns the current browser agent status for the active context,
including whether browser is active, paused, and latest screenshot.
"""
from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from typing import Any
import os
import glob


class BrowserAgentStatus(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        ctxid = input.get("context", "")
        context = await self.use_context(ctxid)

        # Check if browser agent is active
        state = context.agent.get_data("_browser_agent_state") if context.agent else None
        active = False
        paused = getattr(context, "paused", False)
        screenshot_url = None
        progress = ""

        if state and hasattr(state, "task") and state.task:
            active = state.task.is_alive() if hasattr(state.task, "is_alive") else False

        # Find latest screenshot
        if ctxid:
            from python.helpers import files, persist_chat
            chat_folder = persist_chat.get_chat_folder_path(ctxid)
            screenshot_dir = files.get_abs_path(chat_folder, "browser", "screenshots")
            if os.path.isdir(screenshot_dir):
                pngs = sorted(glob.glob(os.path.join(screenshot_dir, "*.png")), key=os.path.getmtime, reverse=True)
                if pngs:
                    screenshot_url = f"img://{pngs[0]}"

        return {
            "success": True,
            "active": active,
            "paused": paused,
            "screenshot_url": screenshot_url,
            "context": ctxid,
        }
