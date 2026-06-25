"""
Supervisor package for AGIX.

This package provides the LLM-based supervisor agent that monitors and helps
stuck agents using intelligent decision making.

Modules:
- base: Constants, logger, and type definitions
- config: SupervisorConfig dataclass
- monitoring: Monitoring loop and signal handling
- llm_interaction: LLM calls and context building
- context_management: Smart file/memory loading
- tools: Tool implementations for interventions

Usage:
    from python.helpers.supervisor import SupervisorAgent, SupervisorConfig
    
    config = SupervisorConfig()
    supervisor = SupervisorAgent(agent_config, config)
    await supervisor.start()
"""
from __future__ import annotations

# Re-export config
from .config import SupervisorConfig

# Re-export base items
from .base import (
    logger,
    DEFAULT_MODEL_PROVIDER,
    DEFAULT_MODEL_NAME,
    DEFAULT_CHECK_INTERVAL_MINUTES,
    DEFAULT_CONTEXT_CONDENSE_THRESHOLD,
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_MAX_FILE_LINES,
    DEFAULT_CONDENSE_THRESHOLD_CHARS,
    DEFAULT_LESSONS_FILE_PATH,
    DEFAULT_LESSONS_CHUNK_SIZE,
    DEFAULT_MAX_INTERVENTIONS_PER_AGENT,
    DEFAULT_INTERVENTION_COOLDOWN_SECONDS,
)

# Re-export mixins for composition
from .monitoring import MonitoringMixin
from .llm_interaction import LLMInteractionMixin
from .context_management import ContextManagementMixin
from .tools import ToolsMixin


__all__ = [
    # Main exports
    "SupervisorConfig",
    
    # Constants
    "logger",
    "DEFAULT_MODEL_PROVIDER",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_CHECK_INTERVAL_MINUTES",
    "DEFAULT_CONTEXT_CONDENSE_THRESHOLD",
    "DEFAULT_MAX_CONTEXT_CHARS",
    "DEFAULT_MAX_FILE_LINES",
    "DEFAULT_CONDENSE_THRESHOLD_CHARS",
    "DEFAULT_LESSONS_FILE_PATH",
    "DEFAULT_LESSONS_CHUNK_SIZE",
    "DEFAULT_MAX_INTERVENTIONS_PER_AGENT",
    "DEFAULT_INTERVENTION_COOLDOWN_SECONDS",
    
    # Mixins
    "MonitoringMixin",
    "LLMInteractionMixin",
    "ContextManagementMixin",
    "ToolsMixin",
]