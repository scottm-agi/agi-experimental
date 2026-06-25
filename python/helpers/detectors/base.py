"""
Base classes and data structures for pattern detection.

This module provides the foundational types used by all pattern detectors:
- DetectedPattern: Represents a detected problematic pattern
- AgentState: Snapshot of agent state for pattern detection
- PatternDetector: Abstract base class for all detectors
"""

from __future__ import annotations
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from python.helpers.error_ledger import get_error_ledger

from python.helpers.loop_prevention import PatternType

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for performance optimization
RE_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}')
RE_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
RE_DIGITS = re.compile(r'\b\d+\b')
RE_TOOL_NAME = re.compile(r'"tool_name"\s*:\s*"([^"]+)"')
RE_WHITESPACE = re.compile(r'\s+')


@dataclass
class DetectedPattern:
    """Represents a detected problematic pattern."""
    pattern_type: PatternType
    agent_id: str
    context_id: str
    confidence: float  # 0.0 to 1.0
    severity: str  # "low", "medium", "high", "critical"
    description: str
    detected_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_type": self.pattern_type.value,
            "agent_id": self.agent_id,
            "context_id": self.context_id,
            "confidence": self.confidence,
            "severity": self.severity,
            "description": self.description,
            "detected_at": self.detected_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class AgentState:
    """Snapshot of agent state for pattern detection."""
    agent_id: str
    context_id: str
    agent_number: int
    iteration: int
    
    # Context window info
    context_tokens: int
    max_context_tokens: int
    
    # Response history
    recent_responses: List[str]
    last_response: str
    
    # Tool execution info
    recent_tool_calls: List[Dict[str, Any]]
    recent_tool_results: List[Dict[str, Any]]
    
    # Timing info
    iteration_start_time: Optional[datetime]
    last_response_time: Optional[datetime]
    
    # Subordinate info
    subordinate_depth: int
    subordinate_count: int
    
    # Error info
    recent_errors: List[str]
    
    # Original request
    initial_prompt: str = ""
    
    # Additional data
    extra: Dict[str, Any] = field(default_factory=dict)

    # Task metadata (Issue #168 & Supervisor Refinement)
    task_type: Optional[str] = None
    task_name: Optional[str] = None
    task_uuid: Optional[str] = None
    is_monitoring_task: bool = False
    recent_lineage: List[Dict[str, Any]] = field(default_factory=list)
    is_retrying: bool = False
    retry_info: Dict[str, Any] = field(default_factory=dict)

    # #1110: Context type for LLM supervisor awareness
    # Lets the intelligent supervisor know if this is a TASK/BACKGROUND agent
    context_type: Optional[str] = None

    # Sender metadata (Issue #243)
    recent_sender_types: List[str] = field(default_factory=list)
    last_sender_type: str = ""
    
    @classmethod
    def from_agent(cls, agent: "Agent") -> "AgentState":
        """Create AgentState from an Agent instance."""
        # Get context window info
        ctx_window = agent.data.get("ctx_window", {})
        context_tokens = ctx_window.get("tokens", 0)
        
        # Get max tokens from model config (estimate if not available)
        max_tokens = getattr(agent.config.chat_model, "ctx_length", 128000)
        
        # Get recent responses from python.history
        recent_responses = []
        recent_sender_types = []
        if hasattr(agent, "history") and agent.history:
            history_output = agent.history.output()
            for msg in history_output[-10:]:
                if msg.get("role") == "assistant" or msg.get("ai"):
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        recent_responses.append(content)
                        recent_sender_types.append(msg.get("sender_type", "agent"))
                else:
                    # Collect human messages as well for attribution
                    recent_sender_types.append(msg.get("sender_type", "user"))
        
        # Get initial prompt
        initial_prompt = ""
        if hasattr(agent, "history") and agent.history:
            history_output = agent.history.output()
            if history_output:
                # First user message is usually the goal
                for msg in history_output:
                    if msg.get("role") == "user":
                        initial_prompt = msg.get("content", "")
                        break
        
        # Get recent tool calls
        recent_tool_calls = agent.data.get("recent_tool_calls", [])
        recent_tool_results = agent.data.get("recent_tool_results", [])
        
        # Get subordinate info
        subordinate_depth = agent.number
        
        # Handle both single subordinate and batch pool
        sub = agent.data.get("_subordinate")
        pool = agent.data.get("_batch_agent_pool", [])
        
        subordinate_count = 0
        if isinstance(pool, list):
            subordinate_count += len(pool)
        
        if sub:
            if isinstance(sub, list):
                subordinate_count += len(sub)
            else:
                subordinate_count += 1
        
        # Get recent errors from ErrorLedger (context-scoped, string-based)
        # Replaces legacy agent.data["recent_errors"] which stored dicts (type mismatch)
        recent_errors_raw: List[str] = []
        try:
            context_id = agent.context.id if agent.context else None
            if context_id:
                ledger = get_error_ledger()
                entries = ledger.get_recent(context_id, limit=5)
                recent_errors_raw = [
                    f"{e.summary}: {e.details}" if e.details else e.summary
                    for e in entries
                ]
        except Exception:
            # Fallback: if ledger fails, try legacy agent.data
            legacy = agent.data.get("recent_errors", [])
            for item in legacy[-5:]:
                if isinstance(item, str):
                    recent_errors_raw.append(item)
                elif isinstance(item, dict):
                    recent_errors_raw.append(item.get("text", str(item)))

        # Extract task metadata (Issue #168 & Supervisor Refinement)
        task_type = None
        task_name = None
        task_uuid = None
        is_monitoring = False
        
        if agent.context and hasattr(agent.context, 'extra'):
            task_meta = agent.context.extra.get("task_metadata", {})
            if not task_meta and hasattr(agent.context, 'task'):
                # Try to get from context task object if available
                task_obj = getattr(agent.context, 'task', None)
                if task_obj:
                    task_type = getattr(task_obj, 'type', None)
                    task_name = getattr(task_obj, 'name', None)
                    task_uuid = getattr(task_obj, 'uuid', None)
            else:
                task_type = task_meta.get("type")
                task_name = task_meta.get("name")
                task_uuid = task_meta.get("uuid")

        # Heuristic for monitoring tasks if not explicitly labeled
        if task_type == "scheduled" or (task_name and "monitor" in task_name.lower()):
            is_monitoring = True

        # Load recent lineage if applicable
        recent_lineage = []
        if task_uuid:
            try:
                lineage_file = f"data/scheduler/{task_uuid}/lineage.json"
                if os.path.exists(lineage_file):
                    with open(lineage_file, 'r') as f:
                        lineage_data = json.load(f)
                        recent_lineage = lineage_data.get("runs", [])
            except Exception as e:
                logger.error(f"Error loading lineage for task {task_uuid}: {e}")

        # Extract context_id safely (Issue #168 regression fix)
        context_id = agent.context.id if agent.context else ""

        return cls(
            agent_id=agent.agent_name,
            context_id=context_id,
            agent_number=agent.number,
            iteration=getattr(agent, "loop_data", None).iteration if getattr(agent, "loop_data", None) else 0,
            context_tokens=context_tokens,
            max_context_tokens=max_tokens,
            recent_responses=recent_responses,
            recent_sender_types=recent_sender_types[-10:] if recent_sender_types else [],
            last_response=recent_responses[-1] if recent_responses else "",
            last_sender_type=recent_sender_types[-1] if recent_sender_types else "",
            recent_tool_calls=recent_tool_calls[-10:] if recent_tool_calls else [],
            recent_tool_results=recent_tool_results[-10:] if recent_tool_results else [],
            iteration_start_time=None,
            last_response_time=datetime.now(timezone.utc),
            subordinate_depth=subordinate_depth,
            subordinate_count=subordinate_count,
            recent_errors=recent_errors_raw[-5:] if recent_errors_raw else [],
            task_type=task_type,
            task_name=task_name,
            task_uuid=task_uuid,
            is_monitoring_task=is_monitoring,
            recent_lineage=recent_lineage,
            initial_prompt=initial_prompt,
            is_retrying=agent.data.get("is_retrying", False),
            retry_info=agent.data.get("retry_info", {}),
            context_type=getattr(agent.context, 'type', None).value if hasattr(getattr(agent.context, 'type', None), 'value') else None,
        )


class PatternDetector(ABC):
    """Abstract base class for pattern detectors."""
    
    @property
    @abstractmethod
    def pattern_type(self) -> PatternType:
        """The type of pattern this detector identifies."""
        pass
    
    @property
    def is_deep(self) -> bool:
        """Whether this detector is 'deep' (slow/thorough) and should run less frequently."""
        return False

    @abstractmethod
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        """
        Detect if the pattern is present in the agent state.
        
        Args:
            state: Current agent state snapshot
            
        Returns:
            DetectedPattern if pattern found, None otherwise
        """
        pass
    
    def _create_pattern(
        self,
        state: AgentState,
        confidence: float,
        severity: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DetectedPattern:
        """Helper to create a DetectedPattern."""
        return DetectedPattern(
            pattern_type=self.pattern_type,
            agent_id=state.agent_id,
            context_id=state.context_id,
            confidence=confidence,
            severity=severity,
            description=description,
            detected_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )