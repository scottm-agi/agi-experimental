from __future__ import annotations
import json
import time
import asyncio
from typing import Any, Optional, Dict, List
from python.helpers.tool import Tool, Response
from python.helpers.settings import get_settings, set_settings_delta
from python.helpers.mcp_handler import MCPConfig
from python.helpers import dirty_json

class McpOnboard(Tool):
    """
    Onboards or updates an MCP (Model Context Protocol) server configuration.
    Allows agents to self-service by adding new local or remote MCP servers to the system.
    """

    async def execute(
        self,
        name: str,
        type: str = "stdio",
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        disabled: bool = False,
        description: Optional[str] = None,
        **kwargs
    ) -> Response:
        """
        Executes the MCP onboarding.

        Args:
            name: Unique name for the MCP server.
            type: "stdio" for local servers, "sse" for remote servers.
            command: (stdio) The executable command (e.g., "node", "python").
            args: (stdio) Arguments for the command.
            env: (stdio) Environment variables for the server.
            url: (sse) The SSE endpoint URL.
            headers: (sse) Optional HTTP headers.
            disabled: Whether the server is initially disabled.
            description: Optional description of the server's purpose.
        """
        if not name:
            return Response(message="Error: 'name' is required for MCP onboarding.", break_loop=False)

        try:
            # 1. Load existing MCP configuration
            settings = get_settings()
            mcp_servers_str = settings.get("mcp_servers", "[]")
            
            try:
                mcp_servers = dirty_json.try_parse(mcp_servers_str)
                if not isinstance(mcp_servers, (list, dict)):
                    mcp_servers = []
            except Exception:
                mcp_servers = []

            # Normalize to dict if it's a list (AGIX handles both, but dict is easier to update)
            # Actually, AGIX seems to prefer a list or a dict where keys are server names.
            # Looking at normalize_config in mcp_handler.py, it handles both.
            # Let's use a dict format for easier lookup/update if we are adding a server by name.
            
            # If it's a list, we might want to convert it to a dict first to avoid duplicates by name
            if isinstance(mcp_servers, list):
                mcp_dict = {}
                for item in mcp_servers:
                    if isinstance(item, dict) and "name" in item:
                        name_key = item.pop("name")
                        mcp_dict[name_key] = item
                mcp_servers = mcp_dict

            # 2. Build the new server config
            server_config = {
                "type": type.lower(),
                "disabled": disabled
            }
            if description:
                server_config["description"] = description
            
            if type.lower() == "stdio":
                if not command:
                    return Response(message="Error: 'command' is required for 'stdio' type servers.", break_loop=False)
                server_config["command"] = command
                if args is not None:
                    server_config["args"] = args
                if env is not None:
                    server_config["env"] = env
            elif type.lower() in ["sse", "streamable-http"]:
                if not url:
                    return Response(message="Error: 'url' is required for remote servers.", break_loop=False)
                server_config["url"] = url
                if headers is not None:
                    server_config["headers"] = headers
            else:
                return Response(message=f"Error: Unsupported MCP server type '{type}'.", break_loop=False)

            # 3. Merge and Save
            if isinstance(mcp_servers, dict) and "mcpServers" in mcp_servers:
                mcp_servers["mcpServers"][name] = server_config
            else:
                mcp_servers[name] = server_config
            
            # Convert back to JSON string for settings
            # We'll save it as a dictionary of servers, which normalize_config handles correctly
            new_mcp_servers_str = json.dumps(mcp_servers, indent=2)
            
            # Use set_settings_delta to persist and apply
            # This triggers initialization in the background
            set_settings_delta({"mcp_servers": new_mcp_servers_str})

            # 4. Wait a moment for initialization and return status
            await asyncio.sleep(2) # Give it 2 seconds to start
            
            status = MCPConfig.get_instance().get_servers_status()
            target_status = next((s for s in status if s["name"] == name), None)

            if target_status:
                if target_status.get("connected"):
                    return Response(
                        message=f"## MCP Server '{name}' Onboarded successfully ✓\n\n"
                                f"The server is now connected with {target_status.get('tool_count', 0)} tools available.",
                        break_loop=False
                    )
                else:
                    return Response(
                        message=f"## MCP Server '{name}' Onboarded with errors ⚠\n\n"
                                f"The server was added but failed to connect: {target_status.get('error', 'Unknown error')}",
                        break_loop=False
                    )
            else:
                return Response(
                    message=f"## MCP Server '{name}' added to settings ✓\n\n"
                            f"Configuration was saved, but the server status could not be verified immediately.",
                    break_loop=False
                )

        except Exception as e:
            return Response(message=f"## Onboarding Error ✗\n\nFailed to onboard MCP server '{name}': {e}", break_loop=False)

if __name__ == "__main__":
    pass
