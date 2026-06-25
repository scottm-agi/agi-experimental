from __future__ import annotations
"""
Extension hook to trace tool execution start.
Captures tool name and arguments when a tool is called.
"""

from typing import Optional

from python.helpers.extension import Extension
from python.helpers.agent_tracer import AgentTracer


class TraceToolBefore(Extension):
    """Extension to trace tool execution start."""

    async def execute(self, tool_name: str = "", tool_args: Optional[dict] = None, **kwargs):
        """Trace tool execution start"""
        if not AgentTracer.is_enabled():
            return
        
        AgentTracer.trace_tool_called(
            agent=self.agent,
            tool_name=tool_name,
            tool_args=tool_args or {}
        )
