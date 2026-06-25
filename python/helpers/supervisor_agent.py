from __future__ import annotations
"""
LLM-Based Supervisor Agent for AGIX.

This is a thin facade that re-exports from the supervisor package.
All implementation details are in python/helpers/supervisor/.

Usage:
    from python.helpers.supervisor_agent import SupervisorAgent, SupervisorConfig
    
    config = SupervisorConfig()
    supervisor = SupervisorAgent(agent_config, config)
    await supervisor.start()
    
    # Supervisor runs in background, monitoring agents
    
    await supervisor.stop()
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

# Import from supervisor package
from python.helpers.supervisor import (
    SupervisorConfig,
    MonitoringMixin,
    LLMInteractionMixin,
    ContextManagementMixin,
    ToolsMixin,
    logger,
    DEFAULT_MODEL_PROVIDER,
    DEFAULT_MODEL_NAME,
)

import python.models as models
from python.helpers.parameters import get_parameters_manager
from python.helpers.settings import get_settings
from python.helpers.event_bus import get_event_bus

if TYPE_CHECKING:
    from python.agent import Agent, AgentConfig


class SupervisorAgent(
    MonitoringMixin,
    LLMInteractionMixin,
    ContextManagementMixin,
    ToolsMixin,
):
    """
    LLM-powered supervisor that monitors and helps stuck agents.
    
    Uses mixins from supervisor package for:
    - MonitoringMixin: Monitoring loop, signal handling, check-ins
    - LLMInteractionMixin: LLM calls, context building, system prompts
    - ContextManagementMixin: Smart file/memory loading
    - ToolsMixin: Tool implementations for interventions
    
    Key Features:
    - Uses LLM for intelligent decision making
    - Receives signals from agents via event bus
    - Periodic check-ins every 3 minutes
    - Loads lessons learned into system prompt
    - Records outcomes for self-improvement
    """
    
    def __init__(
        self,
        agent_config: Optional["AgentConfig"] = None,
        config: Optional[SupervisorConfig] = None,
    ):
        self.agent_config = agent_config
        self.config = config or SupervisorConfig()
        
        # State
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._pending_signals: List[Any] = []  # AgentSignal type
        self._agent_refs: Dict[str, "Agent"] = {}
        self._intervention_history: Dict[str, List[datetime]] = {}
        self._current_model_index = 0
        self.model_candidates: List[tuple[str, str]] = []
        
        # Gap 6: Fingerprinted cooldown - key is (agent_id, signal_fingerprint)
        self._fingerprinted_cooldowns: Dict[str, datetime] = {}
        
        # Gap 5: Pending verification tracking
        self._pending_verifications: Dict[str, Dict[str, Any]] = {}

        # Load model configuration (with project-specific parameters and round-robin support)
        self._load_model_config()
        
        # Event bus
        self.event_bus = get_event_bus()
        
        # Tools (initialized in _setup_tools from ToolsMixin)
        self.tools: List[Dict[str, Any]] = []
        self._setup_tools()
        
        # Statistics
        self._stats = {
            "signals_received": 0,
            "check_ins_performed": 0,
            "interventions_executed": 0,
            "interventions_successful": 0,
            "lessons_recorded": 0,
            "start_time": None,
        }

    @property
    def model_provider(self) -> str:
        """Current model provider."""
        if not self.model_candidates:
            return DEFAULT_MODEL_PROVIDER
        return self.model_candidates[self._current_model_index % len(self.model_candidates)][0]

    @property
    def model_name(self) -> str:
        """Current model name."""
        if not self.model_candidates:
            return DEFAULT_MODEL_NAME
        return self.model_candidates[self._current_model_index % len(self.model_candidates)][1]

    def _rotate_model(self):
        """Move to the next model candidate."""
        if self.model_candidates:
            self._current_model_index = (self._current_model_index + 1) % len(self.model_candidates)
            logger.info(f"Supervisor rotating to next model: {self.model_provider} / {self.model_name}")

    def _load_model_config(self):
        """Load model configuration with support for lists and project parameters."""
        # Check if project is active
        context = self.agent_config.context if self.agent_config else None
        params = get_parameters_manager(context)

        # 1. Start with config if provided
        provider_str = self.config.model_provider
        name_str = self.config.model_name

        # 2. Try project/global parameters if not in config
        if not provider_str:
            provider_str = params.get_parameter("supervisor_model_provider")
        if not name_str:
            name_str = params.get_parameter("supervisor_model_name")

        # 3. Fallback to settings if still empty
        if not provider_str or not name_str:
            settings = get_settings()
            s_provider = settings.get("supervisor_model_provider")
            s_name = settings.get("supervisor_model_name")
            logger.info(f"Supervisor config fallback to settings: provider={s_provider}, name={s_name}")
            provider_str = provider_str or s_provider
            name_str = name_str or s_name
            self.config.model_max_tokens = self.config.model_max_tokens or settings.get("supervisor_model_max_tokens", 0)
            self.config.model_thinking = self.config.model_thinking or settings.get("supervisor_model_thinking", False)
            self.config.model_thinking_tokens = self.config.model_thinking_tokens or settings.get("supervisor_model_thinking_tokens", 0)

        # 4. Final fallback to agent's chat model if we have an agent
        if (not provider_str or not name_str) and self.agent_config:
            provider_str = provider_str or self.agent_config.chat_model.provider
            name_str = name_str or self.agent_config.chat_model.name
            logger.info(f"Supervisor config final fallback to agent chat model: {provider_str}/{name_str}")

        # 5. Last resort fallback
        if not provider_str:
            provider_str = DEFAULT_MODEL_PROVIDER
        if not name_str:
            name_str = DEFAULT_MODEL_NAME

        logger.info(f"Supervisor resolved model config: providers='{provider_str}', names='{name_str}'")

        # Parse candidates
        self.model_candidates = self._get_model_candidates(provider_str, name_str)
        logger.info(f"Supervisor model candidates: {self.model_candidates}")
        self._current_model_index = 0

    def _get_model_candidates(self, providers: str, models_str: str) -> List[tuple[str, str]]:
        """Parse comma-separated providers and models into pairs."""
        p_list = [p.strip() for p in str(providers).split(",") if p.strip()]
        m_list = [m.strip() for m in str(models_str).split(",") if m.strip()]

        if not p_list or not m_list:
            return []

        # Zip lists, repeating the shorter one
        candidates = []
        max_len = max(len(p_list), len(m_list))
        for i in range(max_len):
            p = p_list[i] if i < len(p_list) else p_list[-1]
            m = m_list[i] if i < len(m_list) else m_list[-1]
            candidates.append((p, m))
        return candidates
    
    # =========================================================================
    # Lifecycle
    # =========================================================================
    
    async def start(self) -> None:
        """Start the supervisor."""
        if self._running:
            return
        
        if not self.config.enabled:
            logger.info("LLM Supervisor is disabled")
            return
        
        # Check if we should skip task contexts
        settings = get_settings()
        self._ignore_task_contexts = settings.get("supervisor_ignore_task_contexts", False)
        if self._ignore_task_contexts:
            logger.info("LLM Supervisor will ignore TASK context agents (supervisor_ignore_task_contexts=True)")
        
        self._running = True
        self._stats["start_time"] = datetime.now(timezone.utc)
        
        # Subscribe to event bus
        self.event_bus.subscribe_async(self._on_signal)
        
        # Start monitoring loop
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        
        logger.info(f"LLM Supervisor Agent started (model: {self.model_provider}/{self.model_name})")
    
    async def stop(self) -> None:
        """Stop the supervisor."""
        if not self._running:
            return
        
        self._running = False
        
        # Unsubscribe from event bus
        self.event_bus.unsubscribe(self._on_signal)
        
        # Cancel monitoring task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                logger.debug("[SupervisorAgent] Monitor task cancelled during stop")
        
        logger.info("LLM Supervisor Agent stopped")
    
    # =========================================================================
    # Agent Registration
    # =========================================================================
    
    def register_agent(self, agent: "Agent") -> None:
        """Register an agent for monitoring."""
        agent_name = getattr(agent, 'agent_name', str(id(agent)))
        context_id = agent.context.id if hasattr(agent, 'context') and agent.context else ""
        
        # Determine the primary key: composite if context available, otherwise base
        primary_id = f"{agent_name}@{context_id}" if context_id else agent_name
        
        # Register primary reference ONLY under composite ID
        # FIX #742: Do NOT also store under bare agent_name — that causes
        # cross-chat bleeding when a second chat registers the same agent name
        self._agent_refs[primary_id] = agent
        self._intervention_history[primary_id] = []
        
        logger.info(f"Registered agent {agent_name} with ID {primary_id} in LLM supervisor")
    
    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        agent = self._agent_refs.pop(agent_id, None)
        self._intervention_history.pop(agent_id, None)
        
        # If this was a base ID and we have an agent, try to find and remove composite IDs
        if agent and "@" not in agent_id:
            agent_name = agent_id
            context_id = agent.context.id if hasattr(agent, 'context') and agent.context else ""
            if context_id:
                composite_id = f"{agent_name}@{context_id}"
                self._agent_refs.pop(composite_id, None)
                self._intervention_history.pop(composite_id, None)
        
        logger.info(f"Unregistered agent {agent_id} from LLM supervisor")
    
    def get_agent(self, agent_id: str) -> Optional["Agent"]:
        """Get a registered agent by its ID (base or composite)."""
        return self._agent_refs.get(agent_id)
    
    def get_registered_agents(self) -> List[str]:
        """Get list of registered agent IDs."""
        return list(self._agent_refs.keys())
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get supervisor statistics."""
        uptime = None
        if self._stats["start_time"]:
            uptime = (datetime.now(timezone.utc) - self._stats["start_time"]).total_seconds()
        
        return {
            **self._stats,
            "running": self._running,
            "uptime_seconds": uptime,
            "monitored_agents": len(self._agent_refs),
            "pending_signals": len(self._pending_signals),
            "model": f"{self.model_provider}/{self.model_name}",
        }


# =============================================================================
# Global Supervisor Instance
# =============================================================================

_llm_supervisor: Optional[SupervisorAgent] = None


def get_llm_supervisor() -> Optional[SupervisorAgent]:
    """Get the global LLM supervisor instance."""
    return _llm_supervisor


def set_llm_supervisor(supervisor: SupervisorAgent) -> None:
    """Set the global LLM supervisor instance."""
    global _llm_supervisor
    _llm_supervisor = supervisor


async def start_llm_supervisor(
    agent_config: Optional["AgentConfig"] = None,
    config: Optional[SupervisorConfig] = None,
) -> SupervisorAgent:
    """
    Start the global LLM supervisor.
    
    Args:
        agent_config: Agent configuration to use for model settings
        config: Supervisor configuration
    
    Returns:
        The started supervisor instance
    """
    global _llm_supervisor
    
    if _llm_supervisor is not None and _llm_supervisor._running:
        return _llm_supervisor
    
    _llm_supervisor = SupervisorAgent(agent_config, config)
    await _llm_supervisor.start()
    
    return _llm_supervisor


async def stop_llm_supervisor() -> None:
    """Stop the global LLM supervisor."""
    global _llm_supervisor
    
    if _llm_supervisor is not None:
        await _llm_supervisor.stop()
        _llm_supervisor = None