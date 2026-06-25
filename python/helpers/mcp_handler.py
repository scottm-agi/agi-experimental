from __future__ import annotations
import asyncio
import os
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING, ClassVar, TypeVar, Awaitable, Callable, TextIO
from pydantic import BaseModel, Field, PrivateAttr
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.shared.exceptions import McpError
from contextlib import AsyncExitStack
import httpx

from python.helpers.tool import Tool, Response
import json
import logging
from python.helpers.mcp_tool_tracker import MCPToolTracker

# Global per-tool circuit breaker — shared across all MCP tool instances
_mcp_tool_tracker = MCPToolTracker(max_failures=2)

# MCP Tool Name Aliases — auto-redirect known stale/renamed tool names.
# Key: (server_name_normalized, old_tool_name) → Value: correct_tool_name.
# This prevents 10+ retry loops when prompts reference renamed tools.
_MCP_TOOL_ALIASES: dict[tuple[str, str], str] = {
    ("context7", "get-library-docs"): "query-docs",
    ("context7", "get_library_docs"): "query-docs",
}

def _resolve_tool_alias(server_name: str, tool_name: str) -> str:
    """Resolve a tool name through the alias map. Returns corrected name or original."""
    norm_server = server_name.lower().replace("_", "").replace("-", "")
    key = (norm_server, tool_name.lower())
    # Try exact match first
    if key in _MCP_TOOL_ALIASES:
        resolved = _MCP_TOOL_ALIASES[key]
        PrintStyle.debug(f"[MCP_ALIAS] Redirected {server_name}.{tool_name} → {server_name}.{resolved}")
        return resolved
    return tool_name


def _format_input_schema(schema: dict) -> str:
    """Format an MCP tool's inputSchema into a readable string for the LLM.
    
    Instead of showing just property names (dict_keys), this shows:
    - Property name, type, and description
    - Required markers
    - Nested object structure hints
    """
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    
    if not props:
        return "Args: (none)\n"
    
    lines = []
    for name, prop_schema in props.items():
        prop_type = prop_schema.get("type", "any")
        desc = prop_schema.get("description", "")
        req_marker = " (required)" if name in required else " (optional)"
        
        # Show items type for arrays
        if prop_type == "array" and "items" in prop_schema:
            items = prop_schema["items"]
            items_type = items.get("type", "any")
            if items_type == "object" and "properties" in items:
                item_props = ", ".join(
                    f"{k}: {v.get('type', 'any')}" 
                    for k, v in items["properties"].items()
                )
                prop_type = f"array[{{{item_props}}}]"
            else:
                prop_type = f"array[{items_type}]"
        
        line = f"  - `{name}`: {prop_type}{req_marker}"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    
    return "Args:\n" + "\n".join(lines) + "\n"

class MCPTool(Tool):
    """MCP Tool wrapper for the agent loop"""

    # SS-2: Cross-agent MCP circuit breaker threshold.
    # After this many failures, the tool is skipped with a cached error.
    _MCP_CB_THRESHOLD = 3

    async def execute(self, **kwargs: Any):
        # SS-2: Pre-execution circuit breaker check.
        # Check agent.data['_mcp_health_registry'] BEFORE calling MCPConfig.call_tool().
        # This prevents wasting API calls on tools known to be broken across
        # the entire agent hierarchy (parent → subordinate propagation).
        tool_key = self.name  # e.g. "context7.resolve-library-id"
        registry = self.agent.data.get("_mcp_health_registry", {})
        entry = registry.get(tool_key, {})
        if entry.get("failures", 0) >= self._MCP_CB_THRESHOLD:
            cached_error = entry.get("last_error", "Unknown repeated failure")
            cb_msg = (
                f"⚠️ MCP CIRCUIT BREAKER — Tool '{tool_key}' has been SKIPPED "
                f"(failed {entry['failures']} times across agent hierarchy). "
                f"Last error: {cached_error}. "
                f"Use a DIFFERENT tool or approach. Do NOT retry this tool."
            )
            self.agent.log(
                type="warning",
                content=f"[MCP_CB] Skipped '{tool_key}': {entry['failures']} failures",
            )
            return Response(
                message=cb_msg,
                break_loop=False,
                additional={"success": False, "error": cached_error},
            )

        error = ""
        try:
            from python.helpers.mcp_handler import MCPConfig
            response = await MCPConfig.call_tool(self.name, kwargs)
            
            # Extract text content from MCP response
            message = ""
            if hasattr(response, "content") and response.content:
                message = "\n\n".join(
                    [item.text for item in response.content if hasattr(item, "type") and item.type == "text"]
                )
            elif isinstance(response, dict) and "content" in response:
                 message = "\n\n".join(
                    [item["text"] for item in response["content"] if item.get("type") == "text"]
                )
            else:
                message = str(response)

            # P2: Sanitize surrogates at MCP ingestion boundary.
            # MCP tool outputs can contain lone surrogate characters (\\ud800-\\udfff)
            # that cause UnicodeEncodeError in downstream .encode('utf-8') calls.
            from python.helpers.strings import sanitize_surrogates
            message = sanitize_surrogates(message)

            if getattr(response, "isError", False) or (isinstance(response, dict) and response.get("isError")):
                error = message
        except Exception as e:
            error = f"MCP Tool Exception: {str(e)}"
            message = f"ERROR: {str(e)}"

        if error:
            # Record failure in per-tool circuit breaker
            server_name = getattr(self, '_server_name', self.name.split('__')[0] if '__' in self.name else 'unknown')
            tool_name_clean = self.name.split('__')[-1] if '__' in self.name else self.name
            _mcp_tool_tracker.record_failure(server_name, tool_name_clean)

            # SS-2: Record failure in agent.data health registry (cross-agent)
            if "_mcp_health_registry" not in self.agent.data:
                self.agent.data["_mcp_health_registry"] = {}
            reg = self.agent.data["_mcp_health_registry"]
            reg_entry = reg.setdefault(tool_key, {"failures": 0, "last_error": ""})
            reg_entry["failures"] += 1
            reg_entry["last_error"] = error[:500]  # Cap error length

            # Check if tool is now blacklisted
            if _mcp_tool_tracker.is_blacklisted(server_name, tool_name_clean):
                blacklist_warning = _mcp_tool_tracker.get_warning(server_name, tool_name_clean)
                if blacklist_warning and hasattr(self.agent, 'hist_add_warning'):
                    try:
                        await self.agent.hist_add_warning(blacklist_warning)
                    except Exception:
                        pass  # Best-effort

            self.agent.log(
                type="warning",
                content=f"MCP Tool '{self.name}' failed: {error}",
            )
            # === RepairableGuard: structured self-correction with loop protection ===
            try:
                from python.helpers.repairable import RepairableGuard
                guard = self.agent.get_data("_repair_guard")
                if guard is None:
                    guard = RepairableGuard(max_retries=3)
                    self.agent.set_data("_repair_guard", guard)
                
                if guard.should_retry(self.name, error):
                    attempt = guard.get_attempt_count(self.name, error)
                    # Schema-aware hint for -32602 errors (root cause fix)
                    tool_schema = None
                    try:
                        tool_schema = MCPConfig.get_tool_schema(self.name)
                    except Exception:
                        pass  # Best-effort schema lookup
                    warning = guard.build_warning_with_schema(
                        self.name, error, attempt,
                        input_schema=tool_schema,
                        actual_args=kwargs,
                    )
                    # Inject structured warning into agent history —
                    # LLM will see this and can self-correct on next iteration
                    if hasattr(self.agent, 'hist_add_warning'):
                        await self.agent.hist_add_warning(warning)
                    elif hasattr(self.agent, 'append_message'):
                        self.agent.append_message(warning, human=False)
                    PrintStyle.debug(
                        f"[REPAIR_GUARD] {self.name}: attempt {attempt}/{guard.max_retries} — "
                        f"injected structured warning for self-correction"
                    )
                else:
                    # Exhausted retries — inject final exhaustion warning
                    attempt = guard.get_attempt_count(self.name, error)
                    # Schema-aware hint even on exhaustion (helps agent pivot)
                    tool_schema = None
                    try:
                        tool_schema = MCPConfig.get_tool_schema(self.name)
                    except Exception:
                        pass
                    warning = guard.build_warning_with_schema(
                        self.name, error, attempt,
                        input_schema=tool_schema,
                        actual_args=kwargs,
                    )
                    if hasattr(self.agent, 'hist_add_warning'):
                        await self.agent.hist_add_warning(warning)
                    elif hasattr(self.agent, 'append_message'):
                        self.agent.append_message(warning, human=False)
                    PrintStyle.debug(
                        f"[REPAIR_GUARD] {self.name}: retries EXHAUSTED after {attempt} attempts — "
                        f"agent instructed to skip tool"
                    )
            except Exception as repair_err:
                PrintStyle.debug(f"[REPAIR_GUARD] Error in guard: {repair_err}")
        else:
            # Record success in per-tool circuit breaker (resets failure counter)
            server_name = getattr(self, '_server_name', self.name.split('__')[0] if '__' in self.name else 'unknown')
            tool_name_clean = self.name.split('__')[-1] if '__' in self.name else self.name
            _mcp_tool_tracker.record_success(server_name, tool_name_clean)

            # SS-2: Reset failure count in agent.data health registry on success
            if "_mcp_health_registry" in self.agent.data:
                reg = self.agent.data["_mcp_health_registry"]
                if tool_key in reg:
                    reg[tool_key]["failures"] = 0
                    reg[tool_key]["last_error"] = ""

        return Response(
            message=message, 
            break_loop=False, 
            additional={
                "success": not bool(error),
                "error": error
            }
        )


    async def before_execution(self, **kwargs: Any):
        self.agent.log(
            type="info", 
            heading=f"Using tool '{self.name}'",
            verbose=True
        )

    async def after_execution(self, response: Response, **kwargs: Any):
        # Result already added to history by agent.py
        pass
from python.helpers import files, errors, settings, secrets_helper, strings, circuit_breaker, dirty_json
from python.helpers.print_style import PrintStyle
from python.helpers.defer import DeferredTask
from python.helpers.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

def replace_file_includes(text: str) -> str:
    """
    Helper to expand file include placeholders in tool arguments.
    Example: '{{ include "path/to/file.py" }}' -> file content
    """
    if not isinstance(text, str):
        return text
    
    # We use a primitive but effective search for include patterns
    import re
    pattern = r'\{\{\s*include\s*"([^"]+)"\s*\}\}'
    
    def replacer(match):
        rel_path = match.group(1)
        # Search for file in various locations
        try:
            # Try workspace root
            content = files.read_file(rel_path)
            return content
        except Exception:
            return match.group(0) # Return original if not found
            
    return re.sub(pattern, replacer, text)

# --- Module-level synchronization and state for MCP ---
# All globals now live in mcp_state.py to avoid circular imports.
# Re-exported here for backward compatibility.
from python.helpers.mcp_state import (
    _mcp_config_lock,
    _mcp_config_semaphores,
    _mcp_config_ready_events,
    _mcp_server_locks,
    _mcp_server_clients,
    _mcp_circuit_breakers,
    _mcp_server_refresh_cooldown,
    _mcp_circuit_breaker_config,
    get_mcp_init_semaphore,
    get_mcp_ready_event,
    get_server_lock,
    get_server_client,
    set_server_client,
)


# Re-export from sub-modules for backward compatibility
from python.helpers.mcp_server import MCPServer, MCPServerLocal, MCPServerRemote
from python.helpers.mcp_client import MCPClientBase, MCPClientLocal, MCPClientRemote, CustomHTTPClientFactory


# Re-export MCPConfig for backward compatibility
from python.helpers.mcp_config import MCPConfig


def initialize_mcp(mcp_servers_config: str, force: bool = False):
    """
    Initialize MCP servers from configuration string.
    This is called during initialization or when config changes.
    Matches the pattern used in previous versions but with improved robustness.
    """
    if not mcp_servers_config or not mcp_servers_config.strip():
        return

    # Check if already initialized and matches
    if not force and MCPConfig.is_initialized() and MCPConfig.get_config_str() == mcp_servers_config:
        return

    async def _init():
        try:
            await MCPConfig.update(mcp_servers_config)
            PrintStyle.debug(f"[MCP_DEBUG] initialize_mcp: Successfully initialized from config string (len={len(mcp_servers_config)})")
        except Exception as e:
            PrintStyle.error(f"[MCP_DEBUG] initialize_mcp: Failed to initialize: {e}")

    # Launch background task for initialization
    DeferredTask().start_task(_init)
    PrintStyle.debug("[MCP_DEBUG] initialize_mcp: Dispatched background initialization task")

def call_tool(tool_name: str, input_data: Dict[str, Any]):
    """
    Synchronous wrapper for call_tool (used in non-async contexts if any).
    In this framework, nearly everything is async, so this is rarely used
    but provided for compatibility.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(MCPConfig.call_tool(tool_name, input_data))
    finally:
        loop.close()


# Type alias for tool execution results
CallToolResult = Any
