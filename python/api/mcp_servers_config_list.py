"""
API endpoint: mcp_servers_config_list (Issue #797)

Returns a structured list of all MCP server configurations with
editable properties (name, type, disabled, timeout, env, args).
Enables the settings UI to show a dynamic config table.
"""
from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from typing import Any
import json
import os

from python.helpers.mcp_handler import MCPConfig


class McpServersConfigList(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        configs = []
        instance = MCPConfig.get_instance()

        # Connected servers
        for server in instance.servers:
            config_entry = {
                "name": server.name,
                "type": "remote" if hasattr(server, "url") else "local",
                "disabled": False,
                "connected": True,
                "tool_count": len(server.get_tools()),
                "error": server.get_error() or None,
                "is_system": getattr(server, "_is_system", False),
            }
            # Expose local server specifics
            if hasattr(server, "command"):
                config_entry["command"] = server.command
                config_entry["args"] = getattr(server, "args", [])
            # Expose remote server specifics
            if hasattr(server, "url"):
                config_entry["url"] = server.url
            if hasattr(server, "timeout"):
                config_entry["timeout"] = server.timeout

            configs.append(config_entry)

        # Disconnected / disabled servers
        for disc in instance.disconnected_servers:
            disc_config = disc.get("config", {})
            configs.append({
                "name": disc.get("name", "unknown"),
                "type": "remote" if disc_config.get("url") or disc_config.get("serverUrl") else "local",
                "disabled": disc_config.get("disabled", False),
                "connected": False,
                "tool_count": 0,
                "error": disc.get("error"),
                "is_system": disc_config.get("_is_system", False),
                "command": disc_config.get("command"),
                "args": disc_config.get("args", []),
                "url": disc_config.get("url") or disc_config.get("serverUrl"),
            })

        return {"success": True, "configs": configs}
