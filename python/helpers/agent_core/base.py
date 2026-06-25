"""
Base module for agent_core package.

Contains shared constants, imports, enums, and exception classes used
across all agent_core modules.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import string
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Coroutine,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    TypeVar,
    cast,
)

# Third-party imports
import nest_asyncio
nest_asyncio.apply()

# LangChain imports
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

# Package logger
logger = logging.getLogger(__name__)


# ==============================================================================
# ENUMS
# ==============================================================================

class AgentContextType(Enum):
    """Enumeration of agent context types."""
    USER = "user"
    TASK = "task"
    BACKGROUND = "background"
    EVENT_HOOK = "event_hook"


# ==============================================================================
# EXCEPTIONS
# ==============================================================================

class HandledException(Exception):
    """
    Exception that has been handled and should not be processed further.
    
    Used to signal that an error was already logged/displayed and the message
    loop should terminate without additional error handling.
    """
    pass


# ==============================================================================
# CONSTANTS
# ==============================================================================

# Agent data field names
DATA_NAME_SUPERIOR = "_superior"
DATA_NAME_SUBORDINATE = "_subordinate"
DATA_NAME_CTX_WINDOW = "ctx_window"

# Protection markers for history messages
PROTECTION_MARKER = "[KEEP]"
PROTECTION_MESSAGE = "<!-- [KEEP] -->"


# ==============================================================================
# TYPE ALIASES (for clarity)
# ==============================================================================

# Callback type for streaming responses
StreamCallback = Callable[[str, str], Awaitable[None]]

# Callback type for rate limiting
RateLimitCallback = Callable[[str, str, int, int], Awaitable[bool]]