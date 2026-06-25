from __future__ import annotations
from typing import Any, Optional
from python.helpers.tool import Tool, Response
import python.helpers.mcp_server as mcp_helpers

class McpServer(Tool):
    """Proxy for integrated MCP server tools."""
    
    async def execute(self, **kwargs: Any) -> Response:
        method = self.method
        if not method:
            return Response(message="Error: No method specified for mcp_server tool.", break_loop=False)
            
        if method == "send_message":
            res = await mcp_helpers.send_message(**kwargs)
            return self._handle_response(res)
        elif method == "finish_chat":
            res = await mcp_helpers.finish_chat(**kwargs)
            return self._handle_response(res)
        else:
            return Response(message=f"Error: Unknown method '{method}' for mcp_server tool.", break_loop=False)

    def _handle_response(self, res: Any) -> Response:
        if hasattr(res, "status") and res.status == "success":
            msg = res.response
            if hasattr(res, "chat_id") and res.chat_id:
                 msg += f"\n\n[Chat ID: {res.chat_id}]"
            return Response(message=msg, break_loop=False)
        elif hasattr(res, "status") and res.status == "error":
            return Response(message=f"Error: {res.error}", break_loop=False)
        else:
            return Response(message=str(res), break_loop=False)
