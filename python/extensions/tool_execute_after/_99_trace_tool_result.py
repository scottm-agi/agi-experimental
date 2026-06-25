from __future__ import annotations
"""
Extension hook to trace tool execution result.
Captures tool result and execution status after a tool completes.
"""

from typing import Any

from python.helpers.extension import Extension
from python.helpers.agent_tracer import AgentTracer


class TraceToolAfter(Extension):
    """Extension to trace tool execution result."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        """Trace tool execution result"""
        if not AgentTracer.is_enabled():
            return
        
        # Determine success based on response
        success = True
        result_text = ""
        
        if response is not None:
            if hasattr(response, 'message') and response.message:
                result_text = response.message
                
                # Priority 1: Check metadata for explicit success
                if hasattr(response, 'additional') and isinstance(response.additional, dict):
                    if 'success' in response.additional:
                        success = bool(response.additional['success'])
                    elif 'is_error' in response.additional:
                        success = not bool(response.additional['is_error'])
                
                # Priority 2: Fallback to string matching only if success wasn't explicitly set to False by metadata
                # but only if the first few characters look like an error message (to avoid false positives in long logs)
                if success:
                    msg_lower = response.message.lower()
                    # Check for explicit ERROR: or Exception: prefix
                    if msg_lower.startswith('error:') or msg_lower.startswith('exception:'):
                        success = False
            else:
                result_text = str(response)
        
        AgentTracer.trace_tool_result(
            agent=self.agent,
            tool_name=tool_name,
            result=result_text,
            duration_ms=None,  # Duration tracking would need to be added to tool execution
            success=success
        )
