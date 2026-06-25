"""
MCP Server models — abstract base and concrete implementations for local/remote servers.
Extracted from mcp_handler.py for modularization (P2.2).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING
from pydantic import BaseModel, Field, PrivateAttr
import logging

from python.helpers.print_style import PrintStyle
from python.helpers.mcp_state import (
    get_server_lock,
    get_server_client,
    set_server_client,
)

if TYPE_CHECKING:
    pass

class MCPServer(BaseModel):
    name: str
    description: Optional[str] = None
    disabled: bool = False
    
    @abstractmethod
    async def initialize(self, config: dict):
        pass

    @abstractmethod
    def get_log(self) -> str:
        pass

    @abstractmethod
    def get_error(self) -> str:
        pass

    @abstractmethod
    def get_tools(self) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    def has_tool(self, tool_name: str) -> bool:
        pass

    @abstractmethod
    async def call_tool(self, tool_name: str, input_data: Dict[str, Any]) -> Any:
        pass

    @abstractmethod
    async def update_tools(self):
        pass


class MCPServerRemote(MCPServer):
    url: Optional[str] = None
    serverUrl: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

    def __init__(self, **data):
        super().__init__(**data)
        if not get_server_client(self.name):
            from python.helpers.mcp_client import MCPClientRemote
            set_server_client(self.name, MCPClientRemote(self))

    async def initialize(self, config: dict):
        # Tools will be loaded lazily or via update_tools
        # Use force=True to bypass cooldown on initial connection
        await self.update_tools(force=True)

    def get_error(self) -> str:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.error if client else "Client not initialized"

    def get_log(self) -> str:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.get_log() if client else ""

    def get_tools(self) -> List[dict[str, Any]]:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.tools if client else []

    def has_tool(self, tool_name: str) -> bool:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.has_tool(tool_name) if client else False

    async def call_tool(self, tool_name: str, input_data: Dict[str, Any]) -> Any:
        # Check circuit breaker before calling
        from python.helpers.mcp_config import MCPConfig
        if MCPConfig.is_server_circuit_open(self.name):
            error_msg = f"Circuit breaker for MCP server '{self.name}' is OPEN. Server is likely down or unstable."
            PrintStyle.error(error_msg)
            return {"isError": True, "content": [{"type": "text", "text": error_msg}]}
            
        client = get_server_client(self.name)
        if not client:
            return {"isError": True, "content": [{"type": "text", "text": "Client not initialized"}]}
            
        return await client.call_tool(tool_name, input_data)

    async def update_tools(self, force: bool = False):
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            if client:
                await client.update_tools(force=force)


class MCPServerLocal(MCPServer):
    command: str
    args: List[str] = []
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None

    def __init__(self, **data):
        super().__init__(**data)
        if not get_server_client(self.name):
            from python.helpers.mcp_client import MCPClientLocal
            set_server_client(self.name, MCPClientLocal(self))

    async def initialize(self, config: dict):
        # We start by ensuring tools are loaded
        # Note: MCP server is only launched when a tool is called (lazy)
        # but we want tool information immediately.
        # Use force=True to bypass cooldown on initial connection
        await self.update_tools(force=True)

    def get_error(self) -> str:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.error if client else "Client not initialized"

    def get_log(self) -> str:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.get_log() if client else ""

    def get_tools(self) -> List[dict[str, Any]]:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.tools if client else []

    def has_tool(self, tool_name: str) -> bool:
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            return client.has_tool(tool_name) if client else False

    async def call_tool(self, tool_name: str, input_data: Dict[str, Any]) -> Any:
        # Check circuit breaker before calling
        from python.helpers.mcp_config import MCPConfig
        if MCPConfig.is_server_circuit_open(self.name):
            error_msg = f"Circuit breaker for MCP server '{self.name}' is OPEN. Server is likely down or unstable."
            PrintStyle.error(error_msg)
            return {"isError": True, "content": [{"type": "text", "text": error_msg}]}

        client = get_server_client(self.name)
        if not client:
            return {"isError": True, "content": [{"type": "text", "text": "Client not initialized"}]}

        return await client.call_tool(tool_name, input_data)

    async def update_tools(self, force: bool = False):
        lock = get_server_lock(self.name)
        client = get_server_client(self.name)
        with lock:
            if client:
                await client.update_tools(force=force)

