"""
MCP Configuration — server lifecycle, tool discovery, circuit breakers.
Extracted from mcp_handler.py for modularization (P2.2).
"""
from __future__ import annotations
import asyncio
import os
import threading
import json
import logging
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING, ClassVar
from pydantic import BaseModel, Field, PrivateAttr

from python.helpers.print_style import PrintStyle
from python.helpers.mcp_server import MCPServer, MCPServerLocal, MCPServerRemote
from python.helpers.mcp_client import MCPClientBase, MCPClientLocal, MCPClientRemote
from python.helpers.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from python.helpers.strings import replace_file_includes
from python.helpers import dirty_json
import python.helpers.mcp_state as mcp_state


class MCPConfig(BaseModel):
    servers: list[MCPServer] = Field(default_factory=list)
    disconnected_servers: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def get_circuit_breaker(cls, server_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a specific MCP server."""
        if server_name not in mcp_state._mcp_circuit_breakers:
            mcp_state._mcp_circuit_breakers[server_name] = CircuitBreaker(
                name=f"mcp_{server_name}",
                config=mcp_state._mcp_circuit_breaker_config,
            )
        return mcp_state._mcp_circuit_breakers[server_name]
    
    @classmethod 
    def is_server_circuit_open(cls, server_name: str) -> bool:
        """Check if a server's circuit breaker is open (should skip requests)."""
        if server_name not in mcp_state._mcp_circuit_breakers:
            return False  # No breaker = closed
        breaker = mcp_state._mcp_circuit_breakers[server_name]
        return breaker.is_open
    
    @classmethod
    def get_circuit_breaker_status(cls) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers for monitoring."""
        return {
            name: breaker.get_status()
            for name, breaker in mcp_state._mcp_circuit_breakers.items()
        }


    @classmethod
    def get_instance(cls) -> "MCPConfig":
        if mcp_state._mcp_config_instance is None:
            mcp_state._mcp_config_instance = cls(servers_list=[])
        return mcp_state._mcp_config_instance

    @classmethod
    def get_config_str(cls) -> str:
        return mcp_state._mcp_config_str

    @classmethod
    def _is_initiator(cls, config_str: str) -> bool:
        """Helper to determine if we should be the one leading initialization."""
        return not mcp_state._mcp_config_initialized or mcp_state._mcp_config_str != config_str

    @classmethod
    def wait_for_lock(cls):
        with mcp_state._mcp_config_lock:
            return

    @classmethod
    async def update(cls, config_str: str) -> Any:

        with open("/tmp/mcp_init_debug.log", "a") as debug_file:
            debug_file.write(f"--- MCPConfig.update called at {asyncio.get_event_loop().time()} ---\n")
            debug_file.write(f"Config length: {len(config_str) if config_str else 0}\n")

        # Atomic check-and-set for initialization
        ready_event = mcp_state.get_mcp_ready_event()
        with mcp_state._mcp_config_lock:
            # If already initialized with same config, skip
            if mcp_state._mcp_config_initialized and mcp_state._mcp_config_str == config_str:
                return cls.get_instance()
            
            # If initialization is already in progress by another task, wait for it
            if mcp_state._mcp_config_init_in_progress:
                pass # Fall through to await ready_event.wait()
            else:
                mcp_state._mcp_config_init_in_progress = True
                ready_event.clear()
        
        # If we arrived here and init is in progress, we either wait or lead the init
        if not ready_event.is_set() and not cls._is_initiator(config_str):
            PrintStyle.debug(f"[MCP_DEBUG] wait_until_ready: Another initialization is in progress. Waiting...")
            await ready_event.wait()
            # Double check if initialized correctly
            if mcp_state._mcp_config_initialized and mcp_state._mcp_config_str == config_str:
                return cls.get_instance()

        try:
            # 0. Load configurations from both system and user sources
            raw_servers_list = []

            # A. Load system-level servers (Immutable code-level config)
            system_config_path = "/agix/mcps/system.json" if os.path.exists("/agix/mcps/system.json") else ("/agix/mcps/system.json" if os.path.exists("/agix/mcps/system.json") else "mcps/system.json")
            if not os.path.exists(system_config_path) and os.path.exists(os.path.join(os.getcwd(), "mcps/system.json")):
                 system_config_path = os.path.join(os.getcwd(), "mcps/system.json")
            if os.path.exists(system_config_path):
                try:
                    with open(system_config_path, "r") as f:
                        system_parsed = json.load(f)
                        system_normalized = cls.normalize_config(system_parsed)
                        for item in system_normalized:
                            if isinstance(item, dict):
                                item["_is_system"] = True  # Internally track system servers
                                raw_servers_list.append(item)
                except Exception as e_sys:
                    PrintStyle.error(f"Error loading system MCP config from {system_config_path}: {e_sys}")

            # B. Load user-level servers (from settings.json / config_str)
            if config_str and config_str.strip():
                try:
                    parsed_value = dirty_json.try_parse(config_str)
                    user_normalized = cls.normalize_config(parsed_value)
                    if isinstance(user_normalized, list):
                        for item in user_normalized:
                            if isinstance(item, dict):
                                raw_servers_list.append(item)
                    else:
                        PrintStyle.error(f"Error: User MCP config structure is not a list/dict: {config_str}")
                except Exception as e_json:
                    PrintStyle.error(f"Error parsing user MCP config string: {e_json}")

            # C. De-duplicate and merge (System takes precedence for core names)
            # Normalize keys so 'sequential-thinking' and 'sequential_thinking' dedup (#970)
            merged_servers = {}
            for item in raw_servers_list:
                name = item.get("name")
                if not name: continue
                
                # Normalize: lowercase, strip hyphens and underscores for dedup key
                norm_key = name.lower().replace("_", "").replace("-", "")
                
                # If it's a system server, always keep it or overwrite user one with same name
                # If it's a user server and name already exists from system, ignore it
                if norm_key not in merged_servers or item.get("_is_system"):
                    merged_servers[norm_key] = item

            servers_data = list(merged_servers.values())

            # 1. First Pass: Create server objects (sync)
            new_servers = []
            new_disconnected = []

            for server_item in servers_data:
                server_name = server_item.get("name", "unnamed_server")
                if server_item.get("disabled", False):
                    new_disconnected.append(
                        {"config": server_item, "error": "Disabled in config", "name": server_name}
                    )
                    continue

                try:
                    if server_item.get("url", None) or server_item.get("serverUrl", None):
                        server = MCPServerRemote(**server_item)
                    else:
                        server = MCPServerLocal(**server_item)
                    
                    # Store data to initialize later
                    new_servers.append((server, server_item))
                except Exception as e:
                    new_disconnected.append(
                        {"config": server_item, "error": str(e), "name": server_name}
                    )

            # 2. Sequential Async Initialization (In parallel where permitted by semaphore)
            semaphore = mcp_state.get_mcp_init_semaphore()

            final_valid_servers = []
            
            async def init_with_throttling(server_obj, item):
                async with semaphore:
                    try:
                        PrintStyle.debug(f"[MCP_DEBUG] MCPConfig.update: Initializing server '{server_obj.name}'...")
                        # Stagger starts slightly to reduce sudden CPU spikes
                        await asyncio.sleep(0.1) 
                        
                        # Add a global timeout for server initialization to prevent one hanging server
                        # from blocking the entire system initialization.
                        await asyncio.wait_for(
                            server_obj.initialize(item),
                            timeout=60.0 # 60s max for full initialization including tool discovery
                        )
                        
                        PrintStyle.debug(f"[MCP_DEBUG] MCPConfig.update: Server '{server_obj.name}' ready.")
                        return server_obj
                    except Exception as e:
                        PrintStyle.error(f"[MCP_DEBUG] MCPConfig.update: Server '{server_obj.name}' initialization failed: {e}")
                        raise e

            if new_servers:
                tasks = [init_with_throttling(s, i) for s, i in new_servers]
                # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
                # to prevent MCP server initialization from hanging the entire system boot
                futures = [asyncio.ensure_future(t) for t in tasks]
                done, pending = await asyncio.wait(futures, timeout=120.0)
                if pending:
                    PrintStyle.error(
                        f"[MCP_DEBUG] MCPConfig.update: {len(pending)}/{len(futures)} server init tasks "
                        f"timed out after 120s, cancelling"
                    )
                    for p in pending:
                        p.cancel()
                    await asyncio.wait(pending, timeout=5.0)
                # Collect results preserving order
                results = []
                for f in futures:
                    if f in done and not f.cancelled():
                        try:
                            results.append(f.result())
                        except Exception as e:
                            results.append(e)
                    else:
                        results.append(asyncio.TimeoutError("MCP server init timed out after 120s"))
                
                for i, res in enumerate(results):
                    server_obj, item_data = new_servers[i]
                    if isinstance(res, Exception):
                        error_msg = str(res)
                        PrintStyle(background_color="grey", font_color="red", padding=True).print(
                            f"MCPConfig.update: Failed to initialize MCPServer '{server_obj.name}': {error_msg}"
                        )
                        new_disconnected.append(
                            {"config": item_data, "error": error_msg, "name": server_obj.name}
                        )
                    else:
                        final_valid_servers.append(res)

            # 3. Final Pass: Apply to singleton
            with mcp_state._mcp_config_lock:
                instance = cls.get_instance()
                PrintStyle.debug(f"[MCP_DEBUG] MCPConfig.update: Applying {len(final_valid_servers)} servers (from {len(servers_data)} data items)")
                
                instance.servers = final_valid_servers
                instance.disconnected_servers = new_disconnected
                mcp_state._mcp_config_initialized = True
                mcp_state._mcp_config_str = config_str
                mcp_state._mcp_config_init_in_progress = False
                
                # Set event for ALL loops to prevent cross-loop deadlocks
                for ev in mcp_state._mcp_config_ready_events.values():
                    ev.set()
                    
                return instance
        except Exception as e:
            PrintStyle.error(f"[MCP_DEBUG] MCPConfig.update: Critical initialization error: {e}")
            raise e
        finally:
            with mcp_state._mcp_config_lock:
                mcp_state._mcp_config_init_in_progress = False
                # Always set all events to prevent deadlocks in wait_until_ready callers across loops
                for ev in mcp_state._mcp_config_ready_events.values():
                    ev.set()

    @classmethod
    def normalize_config(cls, servers: Any):
        if isinstance(servers, list):
            return servers
        elif isinstance(servers, dict):
            # forgejo / anthropic style: { "mcpServers": { "name": { ... } } }
            if "mcpServers" in servers:
                return cls.normalize_config(servers["mcpServers"])
            
            # just a dict of servers: { "name": { ... } }
            normalized = []
            for name, config in servers.items():
                if isinstance(config, dict):
                    config["name"] = name
                    normalized.append(config)
            return normalized
        return []

    # Method to check if an MCP server's tools should be refreshed based on adaptive cooldown
    @classmethod
    def should_refresh_server(cls, server_name: str) -> bool:
        """
        Check if an MCP server's tools should be refreshed based on adaptive cooldown.
        This prevents rapid-fire retries for failing servers while allowing quick recovery.
        """
        import time
        
        last_attempt = mcp_state._mcp_server_refresh_cooldown.get(server_name, 0.0)
        now = time.time()
        
        # Default cooldown: 60 seconds
        cooldown = 60.0
        
        # If the server is currently in a circuit breaker OPEN state, use the circuit breaker's timeout
        if cls.is_server_circuit_open(server_name):
            breaker = mcp_state._mcp_circuit_breakers.get(server_name)
            if breaker:
                # Use the current adaptive timeout from the breaker
                cooldown = breaker.get_current_timeout()
        
        if now - last_attempt < cooldown:
            return False
            
        mcp_state._mcp_server_refresh_cooldown[server_name] = now
        return True

    @classmethod
    def get_server_log(cls, server_name: str) -> str:
        with mcp_state._mcp_config_lock:
            for server in cls.get_instance().servers:
                if server.name == server_name:
                    return server.get_log()
            return ""

    @classmethod
    def get_servers_status(cls) -> list[dict[str, Any]]:
        """Get status of all servers"""
        result = []
        with mcp_state._mcp_config_lock:
            instance = cls.get_instance()
            # add connected/working servers
            for server in instance.servers:
                # get server name
                name = server.name
                # get tool count
                tool_count = len(server.get_tools())
                # check if server is connected
                connected = True  # tool_count > 0
                # get error message if any
                error = server.get_error()
                # get log bool
                has_log = server.get_log() != ""

                # add server status to result
                result.append(
                    {
                        "name": name,
                        "connected": connected,
                        "error": error,
                        "tool_count": tool_count,
                        "has_log": has_log,
                    }
                )

            # add failed servers
            for disconnected in instance.disconnected_servers:
                result.append(
                    {
                        "name": disconnected["name"],
                        "connected": False,
                        "error": disconnected["error"],
                        "tool_count": 0,
                        "has_log": False,
                    }
                )

        return result

    @classmethod
    def get_server_detail(cls, server_name: str) -> dict[str, Any]:
        with mcp_state._mcp_config_lock:
            for server in cls.get_instance().servers:
                if server.name == server_name:
                    try:
                        tools = server.get_tools()
                    except Exception:
                        tools = []
                    return {
                        "name": server.name,
                        "description": server.description,
                        "tools": tools,
                    }
            return {}

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if the client is initialized"""
        with mcp_state._mcp_config_lock:
            return mcp_state._mcp_config_initialized

    @classmethod
    def get_tools(cls) -> List[dict[str, dict[str, Any]]]:
        """Get all tools from all servers"""
        with mcp_state._mcp_config_lock:
            tools = []
            for server in cls.get_instance().servers:
                for tool in server.get_tools():
                    tool_copy = tool.copy()
                    tool_copy["server"] = server.name
                    tools.append({f"{server.name}.{tool['name']}": tool_copy})
            return tools

    @classmethod
    def get_tool_schema(cls, tool_name: str) -> Optional[Dict[str, Any]]:
        """Look up a tool's inputSchema by its full name (server.toolName).

        Used by RepairableGuard to inject schema-aware correction hints
        for -32602 errors.

        Args:
            tool_name: Full tool name (e.g., 'context7.resolve-library-id')

        Returns:
            The tool's inputSchema dict, or None if not found.
        """
        try:
            if "." in tool_name:
                req_server, req_tool = tool_name.split(".", 1)
            else:
                req_server, req_tool = None, tool_name

            with mcp_state._mcp_config_lock:
                for server in cls.get_instance().servers:
                    if req_server:
                        norm_srv = server.name.lower().replace("_", "").replace("-", "")
                        norm_req = req_server.lower().replace("_", "").replace("-", "")
                        if norm_srv != norm_req:
                            continue
                    for tool in server.get_tools():
                        tname = tool["name"]
                        pure_tname = tname.split(":", 1)[1] if ":" in tname else tname
                        if pure_tname.lower() == req_tool.lower():
                            return tool.get("inputSchema", {})
        except Exception:
            pass  # Best-effort — return None on any error
        return None

    @classmethod
    async def wait_until_ready(cls):
        """Wait for MCP initialization to complete in the current event loop.
        
        Has a 90s timeout to prevent indefinite hangs during restart recovery.
        If MCP servers are slow, agents proceed without MCP tools rather than
        blocking the entire monologue loop.
        """
        if mcp_state._mcp_config_initialized:
            return
        
        event = mcp_state.get_mcp_ready_event()
        if not event.is_set():
            PrintStyle.debug("[MCP_DEBUG] wait_until_ready: Waiting for MCP initialization (timeout=90s)...")
            try:
                await asyncio.wait_for(event.wait(), timeout=90.0)
            except asyncio.TimeoutError:
                PrintStyle.error(
                    "[MCP_DEBUG] wait_until_ready: TIMED OUT after 90s waiting for MCP init. "
                    "Proceeding without MCP tools to prevent monologue deadlock."
                )
                # Don't block the agent — proceed without MCP tools
                return

    @classmethod
    async def get_tools_prompt(cls, server_name: str = "", filter_by_profile: str = "") -> str:
        """Get a prompt for all tools"""

        # Wait for initialization to finish if in progress
        await cls.get_instance().wait_until_ready()

        prompt = '## "Remote (MCP Server) Agent Tools" available:\n\n'
        server_names = []
        instance = cls.get_instance()
        for server in instance.servers:
            if not server_name or server.name == server_name:
                server_names.append(server.name)

        if server_name and server_name not in server_names:
            raise ValueError(f"Server {server_name} not found")

        for server in instance.servers:
            if server.name in server_names:
                server_name = server.name
                prompt += f"### {server_name}\n"
                if server.description:
                    prompt += f"{server.description}\n"
                
                # Check for circuit breaker state
                if cls.is_server_circuit_open(server_name):
                    prompt += "> [!WARNING]\n"
                    prompt += "> Connection to this server is currently UNSTABLE. Tools may fail.\n\n"
                
                for tool in server.get_tools():
                    tool_full_name = f"{server_name}.{tool['name']}"
                    if filter_by_profile:
                        from python.helpers.tool_selector import ToolSelector
                        if not ToolSelector.get_instance().should_include_tool(tool_full_name, filter_by_profile):
                            continue

                    prompt += f"#### {tool_full_name}\n"
                    prompt += f"{tool['description']}\n"
                    from python.helpers.mcp_handler import _format_input_schema
                    prompt += _format_input_schema(tool.get('inputSchema', {})) + "\n"
        return prompt

    async def get_tool_async(self, agent: Any, tool_name: str) -> Optional[dict[str, Any]]:
        """Instance method for getting a tool asynchronously, used by agent.py"""
        # Ensure we are initialized or at least wait a moment
        if not self.is_initialized():
             for _ in range(10):
                 if self.is_initialized(): break
                 await asyncio.sleep(0.2)
        
        # Split tool name if it's in server.tool format
        requested_tool = tool_name
        requested_server = None
        if "." in tool_name:
            requested_server, requested_tool = tool_name.split(".", 1)
        
        # Apply tool name alias redirect (e.g., get-library-docs → query-docs)
        if requested_server:
            from python.helpers.mcp_handler import _resolve_tool_alias
            requested_tool = _resolve_tool_alias(requested_server, requested_tool)

        # Search for the tool
        matching_tools = []
        with mcp_state._mcp_config_lock:
            # 1. First pass: try to find a server that matches the tool name if no server was specified
            if not requested_server:
                matching_server = next((s for s in self.servers if s.name.lower() == requested_tool.lower() or 
                                       s.name.lower().replace("_", "").replace("-", "") == requested_tool.lower().replace("_", "").replace("-", "")), None)
                if matching_server:
                    available_tools = matching_server.get_tools()
                    # 1.1 Try exact/fuzzy match inside this server
                    for tool in available_tools:
                        tname = tool["name"]
                        pure_tname = tname.split(":", 1)[1] if ":" in tname else tname
                        if pure_tname.lower() == requested_tool.lower() or \
                           pure_tname.lower().replace("_", "").replace("-", "") == requested_tool.lower().replace("_", "").replace("-", ""):
                            tool_copy = tool.copy()
                            tool_copy["server"] = matching_server.name
                            return tool_copy
                    
                    # 1.2 If no match but only one tool exists, assume it's the one
                    if len(available_tools) == 1:
                        tool_copy = available_tools[0].copy()
                        tool_copy["server"] = matching_server.name
                        return tool_copy
                    
                    # 1.3 Try to find if any tool name is a substring or otherwise related
                    for tool in available_tools:
                        if requested_tool.lower() in tool["name"].lower() or tool["name"].lower() in requested_tool.lower():
                            tool_copy = tool.copy()
                            tool_copy["server"] = matching_server.name
                            return tool_copy

            # 2. Regular Search (Exact or Fuzzy)
            for server in self.servers:
                if requested_server and server.name != requested_server:
                    continue
                
                for tool in server.get_tools():
                    tname = tool["name"]
                    pure_tname = tname.split(":", 1)[1] if ":" in tname else tname
                    
                    if pure_tname.lower() == requested_tool.lower() or \
                       pure_tname.lower().replace("_", "").replace("-", "") == requested_tool.lower().replace("_", "").replace("-", ""):
                        
                        tool_copy = tool.copy()
                        tool_copy["server"] = server.name
                        matching_tools.append(tool_copy)

        if len(matching_tools) == 1:
            return matching_tools[0]
        elif len(matching_tools) > 1:
            # Prefer exact name match if possible
            for t in matching_tools:
                if t["name"].lower() == requested_tool.lower():
                    return t
            return matching_tools[0]

        # 3. Fallback: strip redundant server-name prefix from tool name
        # Handles cases like forgejo.forgejo_list_comments → list_comments
        if requested_server and not matching_tools:
            stripped_tool = requested_tool
            server_prefix = requested_server.lower().replace("-", "_") + "_"
            if requested_tool.lower().startswith(server_prefix):
                stripped_tool = requested_tool[len(server_prefix):]
            
            if stripped_tool != requested_tool:
                with mcp_state._mcp_config_lock:
                    for server in self.servers:
                        if server.name != requested_server:
                            continue
                        for tool in server.get_tools():
                            tname = tool["name"]
                            pure_tname = tname.split(":", 1)[1] if ":" in tname else tname
                            if pure_tname.lower() == stripped_tool.lower() or \
                               pure_tname.lower().replace("_", "").replace("-", "") == stripped_tool.lower().replace("_", "").replace("-", ""):
                                tool_copy = tool.copy()
                                tool_copy["server"] = server.name
                                return tool_copy

        # 4. Fallback: keyword overlap matching on the target server  
        # Weights the LAST keyword most heavily (the object/noun of the tool)
        if requested_server and not matching_tools:
            best_match = None
            best_score = 0
            # Extract keywords from the requested tool name (strip server prefix first)
            tool_for_keywords = requested_tool.lower().replace("-", "_")
            server_prefix = requested_server.lower().replace("-", "_") + "_"
            if tool_for_keywords.startswith(server_prefix):
                tool_for_keywords = tool_for_keywords[len(server_prefix):]
            
            req_keywords = [k for k in tool_for_keywords.split("_") if k]
            req_keywords_set = set(req_keywords)
            req_last_keyword = req_keywords[-1] if req_keywords else ""
            
            with mcp_state._mcp_config_lock:
                for server in self.servers:
                    if server.name != requested_server:
                        continue
                    for tool in server.get_tools():
                        tname = tool["name"]
                        pure_tname = tname.split(":", 1)[1] if ":" in tname else tname
                        tool_kws = [k for k in pure_tname.lower().replace("-", "_").split("_") if k]
                        tool_kws_set = set(tool_kws)
                        tool_last = tool_kws[-1] if tool_kws else ""
                        
                        # Score = shared keywords + 3x bonus for matching last keyword
                        overlap = req_keywords_set & tool_kws_set
                        score = len(overlap)
                        if req_last_keyword and tool_last == req_last_keyword:
                            score += 3  # strong signal: same object/noun
                        
                        if score > best_score:
                            best_score = score
                            best_match = tool.copy()
                            best_match["server"] = server.name
            
            if best_match and best_score >= 1:
                return best_match

        return None

    @staticmethod
    def _normalize_mcp_args(server_name: str, tool_name: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministic argument normalization for MCP tools.
        
        Catches common LLM mistakes and transforms arguments to match
        the MCP server's expected schema. This is a safety net — the LLM
        SHOULD send correct args, but when it doesn't, we fix it here
        instead of letting the call fail with a cryptic -32602 error.
        """
        # Normalize server name for matching
        norm_server = server_name.lower().replace("_", "").replace("-", "")
        norm_tool = tool_name.lower().replace("_", "").replace("-", "")
        
        # === GitHub normalization ===
        if "github" in norm_server:
            # create_repository: Agent often sends 'owner' instead of 'organization'
            # GitHub MCP server expects 'organization' for creating repos under an org
            if "createrepository" in norm_tool or "create_repository" in norm_tool:
                if "owner" in input_data and "organization" not in input_data:
                    input_data["organization"] = input_data.pop("owner")

        # === Perplexity Ask normalization ===
        if "perplexity" in norm_server or "perplexity" in norm_tool:
            # Case 1: Agent sent 'query' or 'content' instead of 'messages'
            if "messages" not in input_data:
                text = input_data.get("query") or input_data.get("content") or input_data.get("prompt") or input_data.get("text", "")
                if text and isinstance(text, str):
                    input_data = {
                        "messages": [{"role": "user", "content": text}]
                    }
            
            # Case 2: Agent sent 'messages' as a plain string instead of array
            if "messages" in input_data and isinstance(input_data["messages"], str):
                input_data["messages"] = [{"role": "user", "content": input_data["messages"]}]
            
            # Case 3: Agent sent 'messages' as a dict instead of array
            if "messages" in input_data and isinstance(input_data["messages"], dict):
                input_data["messages"] = [input_data["messages"]]
        
        # === Sequential Thinking normalization ===
        if "sequential" in norm_server or "sequential" in norm_tool or "thinking" in norm_tool:
            # --- Phase 1: Map wrong param names to correct ones ---
            # Agent commonly sends snake_case or abbreviated names
            
            # 'next_thought' / 'next_thought_needed' → append to 'thought' content
            for alias in ["next_thought", "nextThought"]:
                if alias in input_data and "thought" in input_data:
                    # Agent sent a "next step" hint — fold it into the thought content
                    next_val = input_data.pop(alias, "")
                    if next_val and isinstance(next_val, str):
                        input_data["thought"] = f"{input_data['thought']}\n\nNext: {next_val}"
                elif alias in input_data and "thought" not in input_data:
                    # Agent used wrong key entirely 
                    input_data["thought"] = str(input_data.pop(alias))
            
            # 'next_thought_needed' → 'nextThoughtNeeded'
            for alias in ["next_thought_needed"]:
                if alias in input_data and "nextThoughtNeeded" not in input_data:
                    input_data["nextThoughtNeeded"] = input_data.pop(alias)
            
            # 'step' / 'thought_number' → 'thoughtNumber'
            for alias in ["step", "thought_number", "current_step"]:
                if alias in input_data and "thoughtNumber" not in input_data:
                    val = input_data.pop(alias)
                    try:
                        input_data["thoughtNumber"] = int(val)
                    except (ValueError, TypeError):
                        input_data.pop(alias, None)
            
            # 'total_steps' / 'total_thoughts' → 'totalThoughts'
            for alias in ["total_steps", "total_thoughts", "steps"]:
                if alias in input_data and "totalThoughts" not in input_data:
                    val = input_data.pop(alias)
                    try:
                        input_data["totalThoughts"] = int(val)
                    except (ValueError, TypeError):
                        input_data.pop(alias, None)
            
            # Strip known-bogus extra params that cause validation errors
            # Agent sometimes sends 'plan', 'properties', 'context', 'summary', etc.
            valid_params = {
                "thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts",
                "isRevision", "revisesThought", "branchFromThought", "branchId",
                "needsMoreThoughts"
            }
            bogus_keys = [k for k in input_data if k not in valid_params]
            for k in bogus_keys:
                input_data.pop(k)
            
            # --- Phase 2: Type coercion (existing) ---
            # Boolean fields the agent often sends as strings
            bool_fields = ["nextThoughtNeeded", "isRevision", "needsMoreThoughts"]
            for field in bool_fields:
                if field in input_data and isinstance(input_data[field], str):
                    input_data[field] = input_data[field].lower() in ("true", "1", "yes")
            
            # Ensure required boolean fields have defaults when missing (agent sends null → cleaned_input strips them → undefined)
            if "nextThoughtNeeded" not in input_data:
                input_data["nextThoughtNeeded"] = True  # Default: more thinking needed
            
            # Integer fields the agent often sends as strings or omits entirely
            int_fields = ["thoughtNumber", "totalThoughts", "revisesThought", "branchFromThought"]
            for field in int_fields:
                if field in input_data and isinstance(input_data[field], str):
                    try:
                        input_data[field] = int(input_data[field])
                    except (ValueError, TypeError):
                        pass
            
            # Ensure required integer fields have defaults when missing
            if "thoughtNumber" not in input_data:
                input_data["thoughtNumber"] = 1
            if "totalThoughts" not in input_data:
                input_data["totalThoughts"] = 5

            # ── CRITICAL: Ensure 'thought' is always a non-empty string ──
            # ROOT CAUSE (RCA 216): LLM sends thought:null → call_tool's cleaned_input
            # strips None values → normalizer receives no 'thought' key → MCP server
            # rejects with -32602 "expected string, received undefined" → agent retries
            # identical call → same-message loop → hard-stop after 3 attempts.
            # Fix: provide a sensible default so the call succeeds.
            if "thought" not in input_data or not input_data.get("thought"):
                input_data["thought"] = "(continuing analysis)"

        # === Context7 normalization ===
        # Context7 v2.1.6 resolve-library-id requires BOTH 'query' AND 'libraryName'
        # as separate required params. 'query' ranks results by relevance;
        # 'libraryName' is the actual library name to search.
        # LLMs typically send only ONE. Ensure both are populated.
        if server_name == "context7" and tool_name in ("resolve-library-id", "resolve_library_id"):
            has_query = "query" in input_data and input_data["query"]
            has_lib = "libraryName" in input_data and input_data["libraryName"]
            if has_query and not has_lib:
                input_data["libraryName"] = input_data["query"]
            elif has_lib and not has_query:
                input_data["query"] = input_data["libraryName"]

        # === Generic schema-aware fallback normalization ===
        # If we have the tool schema available, try to map unrecognized args
        # to required schema params that are missing
        try:
            instance = cls.get_instance()
            with mcp_state._mcp_config_lock:
                for srv in instance.servers:
                    if srv.name == server_name:
                        for t in srv.get_tools():
                            if t["name"] == tool_name:
                                schema = t.get("inputSchema", {})
                                required = set(schema.get("required", []))
                                properties = schema.get("properties", {})
                                provided_keys = set(input_data.keys())
                                missing_required = required - provided_keys
                                extra_keys = provided_keys - set(properties.keys())
                                
                                # If there's exactly 1 missing required param and 1 extra provided arg,
                                # auto-map the extra to the missing (common single-arg tools)
                                if len(missing_required) == 1 and len(extra_keys) == 1:
                                    missing_key = next(iter(missing_required))
                                    extra_key = next(iter(extra_keys))
                                    expected_type = properties.get(missing_key, {}).get("type", "string")
                                    value = input_data.pop(extra_key)
                                    # Type coercion for common mismatches
                                    if expected_type == "string" and isinstance(value, list):
                                        value = value[0] if value else ""
                                    if expected_type == "string" and not isinstance(value, str):
                                        value = str(value)
                                    input_data[missing_key] = value
                                    PrintStyle.debug(
                                        f"[MCP_NORMALIZE] {server_name}.{tool_name}: "
                                        f"auto-mapped '{extra_key}' → '{missing_key}' (schema-aware)"
                                    )
                                break
                        break
        except Exception:
            pass  # Schema lookup failed — don't block the call

        return input_data

    @classmethod
    async def call_tool(
        cls, tool_name: str, input_data: Dict[str, Any]
    ) -> CallToolResult:
        """Call a tool with the given input data"""

        # Deterministically expand include placeholders and strip None/null values
        # This prevents validation errors in strict MCP servers (e.g. sequential-thinking)
        cleaned_input = {}
        for key, value in input_data.items():
            if value is not None:
                if isinstance(value, str):
                    cleaned_input[key] = replace_file_includes(value)
                else:
                    cleaned_input[key] = value
        
        input_data = cleaned_input

        # Split tool name into server and tool if period present
        if "." in tool_name:
            req_server_part, req_tool_part = tool_name.split(".", 1)
        else:
            req_server_part, req_tool_part = None, tool_name

        # Apply alias resolution (e.g., get-library-docs → query-docs)
        # This is CRITICAL: get_tool_async resolves aliases for DISCOVERY,
        # but call_tool must ALSO resolve them for EXECUTION since MCPTool.execute
        # passes the original (possibly stale) tool name here.
        if req_server_part:
            from python.helpers.mcp_handler import _resolve_tool_alias
            req_tool_part = _resolve_tool_alias(req_server_part, req_tool_part)

        # Try to find tool matching the request
        target_server = None
        tool_name_part = None
        matching_tools = []
        
        with mcp_state._mcp_config_lock:
            instance = cls.get_instance()
            for server in instance.servers:
                # If server part specified, skip mismatching servers
                if req_server_part:
                    # Fuzzy match: normalize both names (lowercase, strip - and _)
                    norm_server_name = server.name.lower().replace("_", "").replace("-", "")
                    norm_req_server = req_server_part.lower().replace("_", "").replace("-", "")
                    if norm_server_name != norm_req_server:
                        continue

                for t in server.get_tools():
                    tname = t["name"]
                    # Strip colon if server prefix is embedded in the tool name itself (common in some MCP servers)
                    pure_tname = tname.split(":", 1)[1] if ":" in tname else tname
                    
                    # Exact or normalized match
                    if pure_tname.lower() == req_tool_part.lower() or \
                       pure_tname.lower().replace("_", "").replace("-", "") == req_tool_part.lower().replace("_", "").replace("-", ""):
                        matching_tools.append((server, tname, t)) # Keep original tname + tool dict for schema

        if len(matching_tools) == 1:
            target_server, tool_name_part, tool_dict = matching_tools[0]
        elif len(matching_tools) > 1:
            # Check if all matches normalize to the same logical server (#970)
            # e.g. sequential_thinking and sequential-thinking are the same server
            normalized_names = set(
                m[0].name.lower().replace("_", "").replace("-", "")
                for m in matching_tools
            )
            if len(normalized_names) == 1:
                # Same logical server — pick first match
                target_server, tool_name_part, tool_dict = matching_tools[0]
            else:
                raise ValueError(f"Ambiguous tool name '{tool_name}'. Found in multiples: {[f'{s.name}.{t}' for s, t in matching_tools]}")
        else:
            tool_dict = None

        if target_server:
            # Normalize arguments before sending to server
            input_data = cls._normalize_mcp_args(target_server.name, tool_name_part, input_data)

            # Schema-driven normalization fallback: auto-remap unknown params
            # using the tool's actual JSON schema. Catches any param mismatches
            # not covered by the hardcoded normalizer above.
            if tool_dict:
                tool_schema = tool_dict.get("inputSchema") or tool_dict.get("parameters") or {}
                if tool_schema:
                    from python.helpers.mcp_normalizer import schema_driven_normalize
                    input_data = schema_driven_normalize(input_data, tool_schema, tool_name_part)

            return await target_server.call_tool(tool_name_part, input_data)
        elif not "." in tool_name:
            # Fallback: if tool_name matches a server name, try to use its first tool
            with mcp_state._mcp_config_lock:
                for server in cls.get_instance().servers:
                    if server.name == tool_name:
                        tools = server.get_tools()
                        if tools:
                            return await server.call_tool(tools[0]["name"], input_data)
            
        raise ValueError(f"Tool {tool_name} not found")

