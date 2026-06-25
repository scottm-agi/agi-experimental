from __future__ import annotations
"""
Extension hook to trace monologue completion.
Captures final response and iteration count when agent monologue ends.
"""

from python.helpers.extension import Extension
from python.helpers.agent_tracer import AgentTracer


class TraceMonologueEnd(Extension):
    """Extension to trace monologue completion."""

    async def execute(self, loop_data=None, **kwargs):
        """Trace monologue end"""
        if not AgentTracer.is_enabled():
            return
        
        # Get iteration count from loop_data
        iterations = 0
        final_response = None
        
        if loop_data:
            if hasattr(loop_data, 'iteration'):
                iterations = loop_data.iteration
            
            # Get final response if available
            if hasattr(loop_data, 'last_response') and loop_data.last_response:
                final_response = str(loop_data.last_response)
        
        AgentTracer.trace_monologue_end(
            agent=self.agent,
            iterations=iterations,
            final_response=final_response
        )
