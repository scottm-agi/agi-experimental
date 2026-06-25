from __future__ import annotations
"""
Extension: Trace monologue start

This extension traces when an agent starts its monologue.
"""

from python.helpers.extension import Extension
from python.helpers.agent_tracer import AgentTracer


class TraceMonologueStart(Extension):
    """Extension to trace monologue start."""

    async def execute(self, loop_data=None, **kwargs):
        """Trace monologue start"""
        AgentTracer.trace_monologue_start(self.agent)
        
        # If there's a user message, trace the task assignment
        if loop_data and loop_data.user_message:
            # Extract message content
            content = loop_data.user_message.content
            if isinstance(content, dict):
                message = content.get("message", str(content))
            else:
                message = str(content)
            
            AgentTracer.trace_task_assigned(self.agent, message)
