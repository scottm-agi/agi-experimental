from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response

from typing import Any

from python.helpers.mcp_handler import MCPConfig


class McpServersStatuss(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        
        # try:
            status = MCPConfig.get_instance().get_servers_status()
            return {"success": True, "status": status}
        # except Exception as e:
        #     return {"success": False, "error": str(e)}
