"""
Supervisor configuration module.

Contains the SupervisorConfig dataclass with all configuration options.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .base import (
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


@dataclass
class SupervisorConfig:
    """Configuration for the LLM supervisor."""
    
    # Model settings (defaults to chat model if not specified)
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    model_max_tokens: int = 0
    model_thinking: bool = False
    model_thinking_tokens: int = 0
    model_kwargs: Dict[str, Any] = field(default_factory=dict)
    
    # Monitoring settings
    check_interval_minutes: float = DEFAULT_CHECK_INTERVAL_MINUTES
    context_condense_threshold: float = DEFAULT_CONTEXT_CONDENSE_THRESHOLD
    
    # Lessons settings
    lessons_file_path: str = DEFAULT_LESSONS_FILE_PATH
    lessons_chunk_size: int = DEFAULT_LESSONS_CHUNK_SIZE
    
    # Intervention settings
    max_interventions_per_agent: int = DEFAULT_MAX_INTERVENTIONS_PER_AGENT
    intervention_cooldown_seconds: float = DEFAULT_INTERVENTION_COOLDOWN_SECONDS
    
    # Context budget settings (in characters, ~4 chars per token)
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    max_file_lines: int = DEFAULT_MAX_FILE_LINES
    condense_threshold_chars: int = DEFAULT_CONDENSE_THRESHOLD_CHARS
    
    # Enabled flag
    enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "model_max_tokens": self.model_max_tokens,
            "model_thinking": self.model_thinking,
            "model_thinking_tokens": self.model_thinking_tokens,
            "model_kwargs": self.model_kwargs,
            "check_interval_minutes": self.check_interval_minutes,
            "context_condense_threshold": self.context_condense_threshold,
            "lessons_file_path": self.lessons_file_path,
            "lessons_chunk_size": self.lessons_chunk_size,
            "max_interventions_per_agent": self.max_interventions_per_agent,
            "intervention_cooldown_seconds": self.intervention_cooldown_seconds,
            "enabled": self.enabled,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SupervisorConfig":
        """Create config from dictionary."""
        return cls(
            model_provider=data.get("model_provider"),
            model_name=data.get("model_name"),
            model_max_tokens=data.get("model_max_tokens", 0),
            model_thinking=data.get("model_thinking", False),
            model_thinking_tokens=data.get("model_thinking_tokens", 0),
            model_kwargs=data.get("model_kwargs", {}),
            check_interval_minutes=data.get("check_interval_minutes", DEFAULT_CHECK_INTERVAL_MINUTES),
            context_condense_threshold=data.get("context_condense_threshold", DEFAULT_CONTEXT_CONDENSE_THRESHOLD),
            lessons_file_path=data.get("lessons_file_path", DEFAULT_LESSONS_FILE_PATH),
            lessons_chunk_size=data.get("lessons_chunk_size", DEFAULT_LESSONS_CHUNK_SIZE),
            max_interventions_per_agent=data.get("max_interventions_per_agent", DEFAULT_MAX_INTERVENTIONS_PER_AGENT),
            intervention_cooldown_seconds=data.get("intervention_cooldown_seconds", DEFAULT_INTERVENTION_COOLDOWN_SECONDS),
            enabled=data.get("enabled", True),
        )