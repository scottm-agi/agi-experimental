from __future__ import annotations
"""
Extension: Trace message loop iteration

This extension traces each iteration of the message loop.
"""

from python.helpers.extension import Extension
from python.helpers.agent_tracer import AgentTracer


class TraceIteration(Extension):
    """Extension to trace message loop iterations."""

    async def execute(self, loop_data=None, **kwargs):
        """Trace message loop iteration"""
        iteration = loop_data.iteration if loop_data else 0
        AgentTracer.trace_message_loop_iteration(self.agent, iteration)
