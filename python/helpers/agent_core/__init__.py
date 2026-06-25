"""
Agent Core Package

This package contains the modularized components of the agent infrastructure:
- base.py: Constants, imports, enums, and exception classes
- config.py: AgentConfig, UserMessage, and LoopData dataclasses
- context.py: AgentContext class for agent lifecycle management

Usage:
    # Import from package (recommended)
    from python.helpers.agent_core import (
        AgentContext,
        AgentContextType,
        AgentConfig,
        UserMessage,
        LoopData,
        HandledException,
    )
    
    # Or import specific modules
    from python.helpers.agent_core.context import AgentContext
    from python.helpers.agent_core.config import AgentConfig

All public classes are re-exported from this __init__.py for backwards
compatibility with code that imports from python.agent.
"""

# =============================================================================
# BASE MODULE EXPORTS
# =============================================================================
from .base import (
    # Enums
    AgentContextType,
    # Exceptions
    HandledException,
    # Constants
    DATA_NAME_SUPERIOR,
    DATA_NAME_SUBORDINATE,
    DATA_NAME_CTX_WINDOW,
    PROTECTION_MARKER,
    PROTECTION_MESSAGE,
    # Type aliases
    StreamCallback,
    RateLimitCallback,
    # Re-export commonly used imports
    logger,
)

# =============================================================================
# CONFIG MODULE EXPORTS
# =============================================================================
from .config import (
    AgentConfig,
    UserMessage,
    LoopData,
)

# =============================================================================
# CONTEXT MODULE EXPORTS
# =============================================================================
from .context import (
    AgentContext,
)

# =============================================================================
# PUBLIC API
# =============================================================================
__all__ = [
    # Enums
    "AgentContextType",
    # Classes
    "AgentContext",
    "AgentConfig",
    "UserMessage",
    "LoopData",
    # Exceptions
    "HandledException",
    # Constants
    "DATA_NAME_SUPERIOR",
    "DATA_NAME_SUBORDINATE",
    "DATA_NAME_CTX_WINDOW",
    "PROTECTION_MARKER",
    "PROTECTION_MESSAGE",
    # Type aliases
    "StreamCallback",
    "RateLimitCallback",
    # Logger
    "logger",
]