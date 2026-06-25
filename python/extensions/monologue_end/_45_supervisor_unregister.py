from __future__ import annotations
"""
Extension: Unregister agent from supervisor on monologue end

This extension unregisters agents from the LLM supervisor when their
monologue ends (task completion). This cleans up monitoring resources
and clears intervention history.

RCA-249 Phase 7: MasterAgentSupervisor has been permanently removed.
Only the LLM supervisor (SupervisorAgent) is used for unregistration.
"""

from python.helpers.extension import Extension
import logging

logger = logging.getLogger(__name__)


class SupervisorUnregister(Extension):
    """Extension to unregister agents from the LLM supervisor."""

    async def execute(self, **kwargs):
        """Unregister agent from the LLM supervisor when monologue ends."""
        try:
            # LLM Supervisor (sole supervisor — RCA-249)
            from python.helpers.supervisor_agent import get_llm_supervisor
            llm_supervisor = get_llm_supervisor()
            if llm_supervisor:
                # Use current agent instance for unique ID matching
                llm_supervisor.unregister_agent(self.agent)
                print(f"[SUPERVISOR_UNREGISTER] Unregistered {self.agent.agent_name} from LLM supervisor", flush=True)

        except Exception as e:
            # Don't fail if unregistration fails
            print(f"[SUPERVISOR_UNREGISTER] Failed to unregister agent: {e}", flush=True)
