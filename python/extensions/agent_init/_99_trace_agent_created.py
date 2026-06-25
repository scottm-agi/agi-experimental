from __future__ import annotations
"""
Extension: Trace agent creation

This extension traces when a new agent is created.
"""

from python.helpers.extension import Extension
from python.helpers.agent_tracer import AgentTracer
from python.agent import Agent


class TraceAgentCreated(Extension):
    """Extension to trace agent creation."""

    async def execute(self, **kwargs):
        """Trace agent creation"""
        # Get parent agent if this is a subordinate
        parent_agent = self.agent.get_data(Agent.DATA_NAME_SUPERIOR)
        
        AgentTracer.trace_agent_created(self.agent, parent_agent)

        # Wire trace session to chat ID when root agent is created
        # This moves the JSONL file from logs/trace_{session}.jsonl → logs/{chat_id}/trace.jsonl
        if self.agent.number == 0 and self.agent.context:
            chat_id = self.agent.context.id
            if chat_id:
                try:
                    AgentTracer.set_chat_id(chat_id)
                except Exception:
                    pass  # Non-fatal — trace still works with session ID
