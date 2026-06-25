from __future__ import annotations
"""
Extension: Register agent with supervisor

This extension automatically registers newly created agents with the
LLM supervisor for monitoring.

RCA-249 Phase 7: MasterAgentSupervisor has been permanently removed.
Only the LLM supervisor (SupervisorAgent) is used for registration.
"""

from python.helpers.extension import Extension
from python.helpers.print_style import PrintStyle
import traceback


class SupervisorRegister(Extension):
    """Extension to register agents with the LLM supervisor."""

    async def execute(self, **kwargs):
        """Register agent with the LLM supervisor for monitoring."""
        try:
            agent_name = getattr(self.agent, 'agent_name', str(id(self.agent)))
            PrintStyle().print(f"[SUPERVISOR_REGISTER] Starting registration for agent {agent_name}")
            
            await self._register_with_llm_supervisor(agent_name)
        except Exception as e:
            PrintStyle().print(f"[SUPERVISOR_REGISTER] ERROR in execute: {e}")
            traceback.print_exc()
    
    async def _register_with_llm_supervisor(self, agent_name: str) -> bool:
        """Register with the LLM-based supervisor."""
        try:
            from python.helpers.supervisor_agent import get_llm_supervisor
            
            supervisor = get_llm_supervisor()
            
            PrintStyle().print(f"[SUPERVISOR_REGISTER] Agent {agent_name}: LLM supervisor={supervisor is not None}")
            
            if supervisor is None:
                PrintStyle().print(f"[SUPERVISOR_REGISTER] LLM supervisor not available for agent {agent_name}")
                return False
            
            PrintStyle().print(f"[SUPERVISOR_REGISTER] Agent {agent_name}: supervisor._running={supervisor._running}")
            
            # Register even if not fully running yet - supervisor will start monitoring
            # when it starts. This fixes race condition where agents are created
            # before supervisor.start() completes.
            supervisor.register_agent(self.agent)
            PrintStyle().print(f"[SUPERVISOR_REGISTER] ✅ Registered agent {agent_name} with LLM supervisor")
            return True
            
        except ImportError as e:
            PrintStyle().print(f"[SUPERVISOR_REGISTER] LLM supervisor module not available: {e}")
            return False
        except Exception as e:
            PrintStyle().print(f"[SUPERVISOR_REGISTER] Failed to register agent {agent_name} with LLM supervisor: {e}")
            traceback.print_exc()
            return False

