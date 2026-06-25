"""
MCP Client implementations — transport, session management, tool discovery.
Extracted from mcp_handler.py for modularization (P2.2).
"""
from __future__ import annotations
import asyncio
import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar, Union, TextIO
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.shared.exceptions import McpError
import httpx

from python.helpers.print_style import PrintStyle
from python.helpers.mcp_server import MCPServer, MCPServerLocal, MCPServerRemote
from python.helpers import settings, strings, secrets_helper

# Type alias for tool execution results
CallToolResult = Any
T = TypeVar('T')

class MCPClientBase(ABC):
    def __init__(self, server: Union[MCPServerLocal, MCPServerRemote]):
        self.server = server
        self.tools: List[dict[str, Any]] = []  # Tools are cached on the client instance
        self.error: str = ""
        self.log: List[str] = []
        self.log_file: Optional[TextIO] = None

    # Protected method
    def _get_log_file(self) -> TextIO:
        if not self.log_file:
            log_dir = os.path.join(settings.BASE_DIR, "logs", "mcp")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"{self.server.name}.log")
            self.log_file = open(log_path, "a", encoding="utf-8")
        return self.log_file

    def _add_log(self, message: str):
        self.log.append(message)
        if len(self.log) > 100:
            self.log.pop(0)
        try:
            f = self._get_log_file()
            f.write(f"[{strings.timestamp()}] {message}\n")
            f.flush()
        except Exception:
            pass

    @abstractmethod
    async def _create_stdio_transport(self, current_exit_stack: AsyncExitStack):
        pass

    #
    #     Manages the lifecycle of an MCP session for a single operation.
    #     Creates a temporary session, executes coro_func with it, and ensures cleanup.
    #     
    async def _execute_with_session(
        self,
        coro_func: Callable[[ClientSession], Awaitable[T]],
        read_timeout_seconds=600,
    ) -> T:
        
        # 1. Get Circuit Breaker for this server
        from python.helpers.mcp_config import MCPConfig
        breaker = MCPConfig.get_circuit_breaker(self.server.name)
        
        async def operation():
            # Apply a connection-level timeout to ensure we don't hang indefinitely 
            # if the process starts but doesn't provide streams or initialize the session.
            async def connect_and_init():
                async with AsyncExitStack() as exit_stack:
                    # _create_stdio_transport is implemented by subclasses
                    streams = await self._create_stdio_transport(exit_stack)
                    
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        self._add_log(f"Session initialized for {self.server.name}")
                        return await coro_func(session)

            return await asyncio.wait_for(
                connect_and_init(),
                timeout=30.0 # 30s limit for individual connection and tool call
            )

        try:
            # 2. Execute within the circuit breaker
            result = await breaker.execute_async(operation)
            
            self.error = ""
            return result
        except asyncio.TimeoutError:
            error_msg = f"Timeout connecting to MCP server {self.server.name} (server might be starting or hung)"
            self.error = error_msg
            self._add_log(error_msg)
            raise
        except Exception as e:
            error_msg = str(e) or type(e).__name__
            self.error = error_msg
            self._add_log(f"Error executing operation for {self.server.name}: {error_msg}")
            raise

    # Update tools list from server with automatic retry logic for resilience.
    #     
    #     Includes self-healing mechanism for npx cache corruption in brownfield environments.
    #     When cache corruption errors are detected (e.g., 'Cannot find module'), the npx cache
    #     is automatically cleared and the connection is retried.
    #     
    async def update_tools(self, max_retries: int = 2, retry_delay: float = 0.3, force: bool = False):
        
        # Check against adaptive refresh cooldown to prevent overloading failing servers
        # Skip this check if force=True (e.g., during initial connection)
        if not force:
            from python.helpers.mcp_config import MCPConfig
            if not MCPConfig.should_refresh_server(self.server.name):
                return

        async def list_tools_op(current_session: ClientSession):
            # We enforce a timeout on the list_tools call itself
            try:
                print(f"[DEBUG_LOG] MCPClientBase ({self.server.name}): list_tools starting...")
                # Note: list_tools is a standard MCP capability
                response = await asyncio.wait_for(
                    current_session.list_tools(),
                    timeout=30.0 # 30s limit for tool discovery
                )
                self.tools = [t.model_dump() for t in response.tools]
                self.error = ""
                self._add_log(f"Updated tools list: {len(self.tools)} tools found")
                print(f"[DEBUG_LOG] MCPClientBase ({self.server.name}): list_tools success, found {len(self.tools)} tools.")
            except Exception as e:
                self._add_log(f"list_tools failed: {e}")
                print(f"[DEBUG_LOG] MCPClientBase ({self.server.name}): list_tools FAILED: {e}")
                raise

        retries = 0
        while retries <= max_retries:
            try:
                await self._execute_with_session(list_tools_op)
                return
            except McpError as e:
                # Handle specific MCP protocol errors
                self.error = f"MCP Protocol Error: {e}"
                self._add_log(self.error)
                break 
            except Exception as e:
                error_str = str(e)
                
                # SELF-HEALING: Detect npx / module resolution issues common in brownfield environments
                # e.g., "Error: Cannot find module '.../mcp-server-forgejo/dist/index.js'"
                if "Cannot find module" in error_str and ("npx" in error_str or "node" in error_str):
                    PrintStyle.error(f"MCP Self-Healing: Detected potential npx cache corruption for '{self.server.name}'. Clearing npx cache and retrying...")
                    try:
                        # Clear npx cache
                        import subprocess
                        subprocess.run(["npm", "cache", "clean", "--force"], check=False, capture_output=True)
                        # Also attempt to remove specific _npx folder if we can find it
                        npx_path = os.path.expanduser("~/.npm/_npx")
                        if os.path.exists(npx_path):
                            import shutil
                            shutil.rmtree(npx_path, ignore_errors=True)
                    except Exception as e_sh:
                        PrintStyle.debug(f"Self-healing cache clear failed: {e_sh}")
                    
                    # Force a slightly longer delay before retry to let FS settle
                    await asyncio.sleep(1.0)
                
                retries += 1
                if retries <= max_retries:
                    await asyncio.sleep(retry_delay * retries)
                else:
                    self.error = f"Failed after {max_retries} retries: {error_str}"
                    self._add_log(self.error)

    # Check if a tool is available (uses cached tools)
    def has_tool(self, tool_name: str) -> bool:
        return any(t["name"] == tool_name for t in self.tools)

    # Get all tools from the server (uses cached tools)
    def get_tools(self) -> List[dict[str, Any]]:
        return self.tools

    async def call_tool(
        self, tool_name: str, input_data: Dict[str, Any], max_retries: int = 2
    ) -> CallToolResult:
        if not self.has_tool(tool_name):
            PrintStyle.debug(
                f"MCPClientBase ({self.server.name}): Tool '{tool_name}' not in cache for 'call_tool', refreshing tools..."
            )
            
            await self.update_tools()
            
            if not self.has_tool(tool_name):
                raise ValueError(f"Tool {tool_name} not found on server {self.server.name}")

        async def call_tool_op(current_session: ClientSession):
            # Standard MCP tool call
            print(f"[DEBUG_LOG] MCPClientBase ({self.server.name}): Calling tool '{tool_name}' with args: {input_data}")
            response: CallToolResult = await current_session.call_tool(
                tool_name, arguments=input_data
            )
            print(f"[DEBUG_LOG] MCPClientBase ({self.server.name}): Tool '{tool_name}' returned: {response}")
            return response

        retries = 0
        last_error = None
        while retries <= max_retries:
            try:
                if retries > 0:
                    # Exponential backoff with jitter for retries
                    delay = (0.5 * (2 ** retries)) + (0.1 * retries)
                    await asyncio.sleep(delay)
                    PrintStyle.debug(f"[MCP_RETRY] {self.server.name}.{tool_name}: Attempt {retries + 1}/{max_retries + 1}...")

                return await self._execute_with_session(call_tool_op)
            except Exception as e:
                last_error = e
                error_str = str(e)
                
                # If circuit is open, don't retry, just fail fast
                from python.helpers.mcp_config import MCPConfig
                if MCPConfig.is_server_circuit_open(self.server.name):
                    break

                # Determine if the error is "retryable"
                # (Timeout, Connection errors, etc. - in buggy APIs we assume many are transient)
                is_retryable = any(msg in error_str.lower() for msg in [
                    "timeout", "connection", "rate limit", "busy", "hung", "stream"
                ])
                
                if not is_retryable:
                    break
                    
                retries += 1

        # If we reach here, all retries failed or we hit a non-retryable error
        error_msg = f"MCPClientBase ({self.server.name}): 'call_tool' operation for '{tool_name}' failed after {retries} retries: {type(last_error).__name__}: {last_error}"
        PrintStyle.error(error_msg)
        # We wrap the error in an MCP result so the agent can see it instead of crashing the loop
        return {"isError": True, "content": [{"type": "text", "text": error_msg}]}

    def get_log(self) -> str:
        return "\n".join(self.log)


class MCPClientLocal(MCPClientBase):
    def __del__(self):
        # Ensure log file closed
        if hasattr(self, 'log_file') and self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass

    async def _create_stdio_transport(self, current_exit_stack: AsyncExitStack):
        server = self.server
        if not isinstance(server, MCPServerLocal):
            raise ValueError("MCPClientLocal requires MCPServerLocal")
        
        self._add_log(f"Connecting to local MCP server: {server.command} {' '.join(server.args)}")
        
        # Prepare environment
        env = os.environ.copy()
        if server.env:
            # Resolve §§secret(KEY) placeholders in env values before spawning.
            # This ensures tokens like GITHUB_PERSONAL_ACCESS_TOKEN get actual values
            # instead of literal "§§secret(GITHUB_TOKEN)" strings.
            # IMPORTANT: We use get_secret() which checks BOTH the secrets DB AND
            # os.environ, unlike replace_placeholders() which only checks the DB.
            import re
            sm = secrets_helper.get_default_secrets_manager()
            resolved_env = {}
            for k, v in server.env.items():
                if isinstance(v, str) and "§§secret" in v:
                    match = re.fullmatch(secrets_helper.ALIAS_PATTERN, v.strip())
                    if match:
                        secret_key = match.group(1)
                        resolved = sm.get_secret(secret_key)
                        if resolved:
                            resolved_env[k] = resolved
                            self._add_log(f"Resolved §§secret({secret_key}) for env var '{k}'")
                        else:
                            self._add_log(f"Warning: Could not resolve §§secret({secret_key}) for env var '{k}' — key not found in secrets DB or environment")
                            resolved_env[k] = v  # Keep original if not found
                    else:
                        # Partial match or embedded placeholder — try replace_placeholders
                        try:
                            resolved_env[k] = sm.replace_placeholders(v)
                        except Exception:
                            resolved_env[k] = v
                else:
                    resolved_env[k] = v
            env.update(resolved_env)

        params = StdioServerParameters(
            command=server.command,
            args=server.args,
            env=env,
        )
        
        # Connect using the MCP stdio_client
        # stdio_client returns a context manager that provides (read, write) streams
        return await current_exit_stack.enter_async_context(stdio_client(params))


class CustomHTTPClientFactory:
    def __init__(self, verify: bool = True):
        self.verify = verify

    def __call__(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        # Default headers for MCP SSE clients
        merged_headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        if headers:
            merged_headers.update(headers)
            
        return httpx.AsyncClient(
            headers=merged_headers,
            timeout=timeout or httpx.Timeout(300.0), # Generous default timeout
            verify=self.verify,
            auth=auth,
            follow_redirects=True
        )


class MCPClientRemote(MCPClientBase):
    def __init__(self, server: Union[MCPServerLocal, MCPServerRemote]):
        super().__init__(server)
        self.session_id: Optional[str] = None  # Track session ID for streaming HTTP clients

    # Connect to an MCP server, init client and save stdio/write streams
    async def _create_stdio_transport(self, current_exit_stack: AsyncExitStack):
        server = self.server
        if not isinstance(server, MCPServerRemote):
            raise ValueError("MCPClientRemote requires MCPServerRemote")
        
        url = server.url or server.serverUrl
        if not url:
            raise ValueError(f"No URL provided for remote MCP server {server.name}")
            
        self._add_log(f"Connecting to remote MCP server: {url}")
        
        # Prepare headers
        headers = server.headers or {}
        
        # We use a custom HTTP client factory to handle timeouts and SSL verification
        # Self-signed certs are common in dev environments, we allow them if specified by env
        verify_ssl = os.environ.get("MCP_VERIFY_SSL", "true").lower() == "true"
        client_factory = CustomHTTPClientFactory(verify=verify_ssl)
        
        # Connect using the MCP sse_client
        # sse_client returns a context manager that provides (read, write) streams
        # Note: sse_client handles the SSE to async streams conversion
        return await current_exit_stack.enter_async_context(
            sse_client(
                url=url,
                headers=headers,
                sse_timeout=30.0,
                client_factory=client_factory
            )
        )

    # Get the current session ID if available (for streaming HTTP clients).
    def get_session_id(self) -> Optional[str]:
        return self.session_id

