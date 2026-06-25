"""
Base module for supervisor package.

Contains shared imports, constants, logger, and type definitions.
"""
from __future__ import annotations

import asyncio
import json
import sys
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent, AgentConfig, AgentContextType


# Logger
logger = logging.getLogger(__name__)


# Constants
DEFAULT_MODEL_PROVIDER = "openrouter"
DEFAULT_MODEL_NAME = "openai/gpt-4o"
DEFAULT_CHECK_INTERVAL_MINUTES = 3.0
DEFAULT_CONTEXT_CONDENSE_THRESHOLD = 0.76
DEFAULT_MAX_CONTEXT_CHARS = 100000  # ~25K tokens
DEFAULT_MAX_FILE_LINES = 500
DEFAULT_CONDENSE_THRESHOLD_CHARS = 80000
DEFAULT_LESSONS_FILE_PATH = "memory-bank/lessons-learned/supervisor_lessons.md"
DEFAULT_LESSONS_CHUNK_SIZE = 10000
DEFAULT_MAX_INTERVENTIONS_PER_AGENT = 10
DEFAULT_INTERVENTION_COOLDOWN_SECONDS = 60.0


# Type aliases
SignalList = List["AgentSignal"]
ToolCallList = List[Dict[str, Any]]