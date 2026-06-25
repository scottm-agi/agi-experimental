from __future__ import annotations
"""
Event Bus for AGIX Supervisor System.

This module provides an event-driven communication system for agents to emit
signals when they detect patterns that may indicate they're stuck or failing.
The supervisor subscribes to these signals to decide when to intervene.

Usage:
    from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
    
    # Emit a signal
    await get_event_bus().publish(AgentSignal(
        signal_type=SignalType.RESPONSE_LOOP,
        agent_id="A0",
        context_id="ctx_123",
        timestamp=datetime.now(),
        severity="high",
        details={"iterations": 5}
    ))
    
    # Subscribe to signals
    def on_signal(signal: AgentSignal):
        print(f"Received: {signal.signal_type}")
    
    get_event_bus().subscribe(on_signal)
"""

import asyncio
import logging
import uuid
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Standardized failure signals that agents can emit.

    Wiring Status Legend:
      ACTIVE  — Has both publisher(s) and subscriber(s) connected
      UNWIRED — Defined but has no publisher and/or no subscriber wired up.
                These are reserved for planned supervisor enhancements (Gap 1-6).
                See: docs/architecture/supervisor-reliability-gaps.md
    """

    # Context-related — ACTIVE (published by _50_pattern_detector.py, consumed by supervisor)
    CONTEXT_WARNING = "context_warning"      # 76% context usage
    CONTEXT_CRITICAL = "context_critical"    # 90% context usage

    # Loop-related — ACTIVE (published by _50_pattern_detector.py, agent_history.py)
    RESPONSE_LOOP = "response_loop"          # Repeated responses detected
    TOOL_FAILURE_LOOP = "tool_failure_loop"  # 3+ consecutive tool failures

    # Progress-related — ACTIVE (published by _50_pattern_detector.py)
    PROGRESS_STALL = "progress_stall"        # No progress for 5+ iterations

    # Error-related — UNWIRED (no publisher, no consumer — planned for error classification)
    AUTH_ERROR = "auth_error"                # Authentication failures
    RATE_LIMITED = "rate_limited"            # API rate limits hit
    PERMISSION_ERROR = "permission_error"    # Permission denied errors

    # Depth-related — UNWIRED (no publisher — planned for recursion monitoring)
    RECURSION_DEPTH = "recursion_depth"      # Deep agent nesting

    # General — MIXED
    AGENT_ERROR = "agent_error"              # ACTIVE: published by agent_error_handler.py, _50_error_supervisor_trigger.py
    AGENT_STUCK = "agent_stuck"              # UNWIRED: referenced in monitoring.py display but no publisher
    AGENT_STARTED = "agent_started"          # UNWIRED: planned for agent lifecycle tracking
    AGENT_COMPLETED = "agent_completed"      # UNWIRED: planned for agent lifecycle tracking
    AGENT_STATUS_CHANGED = "agent_status_changed"  # UNWIRED: planned for status change events

    # Intervention-related — MIXED
    INTERVENTION_NEEDED = "intervention_needed"  # UNWIRED: publisher exists in analysis_feedback.py but that file has zero importers
    INTERVENTION_APPLIED = "intervention_applied"  # UNWIRED: no publisher
    INTERVENTION_GUIDANCE = "intervention_guidance" # ACTIVE: published by tools_actions.py, consumed by _40_remote_intervention.py

    # Supervisor Reliability Enhancements (Gap 1-6) — ALL UNWIRED unless noted
    # Gap 1: Progress velocity
    SLOW_PROGRESS = "slow_progress"              # UNWIRED: planned for progress velocity tracking
    GOAL_DRIFT_DETECTED = "goal_drift_detected"  # UNWIRED: no drift detection mechanism exists; _10_goal_tracking.py only extracts goals at iteration 0, never compares ongoing activity

    # Gap 3: Completion verification
    TASK_CLAIMS_COMPLETE = "task_claims_complete"  # ACTIVE: published by _30_completion_detector.py (conditional)
    COMPLETION_VERIFIED = "completion_verified"    # COVERED: supervisor verifies via LLM + _verify_interventions() in monitoring.py
    COMPLETION_REJECTED = "completion_rejected"    # COVERED: supervisor rejects via NUDGE intervention in tools_actions.py

    # Gap 4: Task heartbeats
    HEARTBEAT_OK = "heartbeat_ok"                # ACTIVE: published by _20_task_heartbeat.py (conditional)
    HEARTBEAT_MISSED = "heartbeat_missed"        # COVERED: supervisor detects via _check_dead_agents() in monitoring.py (progressive 7-step recovery: nudge → IO-breaker → parent re-delegate → force return → human escalation)

    # Gap 2: Goal state management
    GOAL_EXTRACTED = "goal_extracted"            # UNWIRED: planned for goal extraction events

    # Gap 5: New instruction detection
    NEW_INSTRUCTION_DETECTED = "new_instruction_detected"  # UNWIRED: planned for instruction detection
    VERIFICATION_REQUIRED = "verification_required"        # UNWIRED: planned for verification triggers

    # N-Attempt failure tracking (ADR-019 hardening) — ACTIVE
    REPEATED_TASK_FAILURE = "repeated_task_failure"  # Same task failed N times (supervisor redirect)


@dataclass
class AgentSignal:
    """Signal emitted by an agent when a pattern is detected."""
    
    signal_type: SignalType
    agent_id: str
    context_id: str
    timestamp: datetime
    severity: str  # "low", "medium", "high", "critical"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    details: Dict[str, Any] = field(default_factory=dict)
    
    # Optional context
    iteration: Optional[int] = None
    tool_name: Optional[str] = None
    error_message: Optional[str] = None
    context_type: Optional[str] = None  # "CHAT", "TASK", "SCHEDULED", etc. for supervisor filtering
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "signal_type": self.signal_type.value,
            "agent_id": self.agent_id,
            "context_id": self.context_id,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity,
            "id": self.id,
            "details": self.details,
            "iteration": self.iteration,
            "tool_name": self.tool_name,
            "error_message": self.error_message,
            "context_type": self.context_type,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSignal":
        """Create from dictionary."""
        return cls(
            signal_type=SignalType(data["signal_type"]),
            agent_id=data["agent_id"],
            context_id=data["context_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            severity=data["severity"],
            id=data.get("id", str(uuid.uuid4())),
            details=data.get("details", {}),
            iteration=data.get("iteration"),
            tool_name=data.get("tool_name"),
            error_message=data.get("error_message"),
            context_type=data.get("context_type"),
        )


SignalCallback = Callable[[AgentSignal], Union[None, Any]]
AsyncSignalCallback = Callable[[AgentSignal], Any]


class EventBus:
    """
    In-memory event bus for agent signals.
    
    Features:
    - Async signal publishing
    - Multiple subscribers (sync and async)
    - Signal history (configurable size)
    - Signal filtering by agent/type
    - Optional Redis backend for distributed systems
    
    Thread Safety:
    - Uses asyncio.Lock for async operations
    - Safe for concurrent publishing from multiple agents
    """
    
    def __init__(
        self,
        max_history: int = 1000,
        redis_client: Optional[Any] = None,
    ):
        """
        Initialize the event bus.
        
        Args:
            max_history: Maximum number of signals to keep in history
            redis_client: Optional Redis client for distributed pub/sub
        """
        self._subscribers: List[SignalCallback] = []
        self._async_subscribers: List[AsyncSignalCallback] = []
        self._history: deque[AgentSignal] = deque(maxlen=max_history)
        self._redis = redis_client
        self._locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
        self._thread_lock = threading.Lock()
        self._stats = {
            "signals_published": 0,
            "signals_by_type": {},
            "signals_by_agent": {},
        }
    
    def _get_loop_lock(self) -> asyncio.Lock:
        """Get or create an asyncio.Lock for the current event loop."""
        loop = asyncio.get_event_loop()
        with self._thread_lock:
            if loop not in self._locks:
                self._locks[loop] = asyncio.Lock()
            return self._locks[loop]

    async def publish(self, signal: AgentSignal) -> None:
        """
        Publish a signal to all subscribers.
        
        Args:
            signal: The signal to publish
        """
        lock = self._get_loop_lock()
        async with lock:
            # Add to history and update stats with thread lock
            with self._thread_lock:
                self._history.append(signal)
                self._stats["signals_published"] += 1
                signal_type = signal.signal_type.value
                self._stats["signals_by_type"][signal_type] = \
                    self._stats["signals_by_type"].get(signal_type, 0) + 1
                self._stats["signals_by_agent"][signal.agent_id] = \
                    self._stats["signals_by_agent"].get(signal.agent_id, 0) + 1
                
                # Copy subscribers to avoid holding lock during calls
                sync_subs = list(self._subscribers)
                async_subs = list(self._async_subscribers)
            
            # Notify sync subscribers
            for subscriber in sync_subs:
                try:
                    subscriber(signal)
                except Exception as e:
                    logger.error(f"Error in sync signal subscriber: {e}")
            
            # Notify async subscribers
            for subscriber in async_subs:
                try:
                    await subscriber(signal)
                except Exception as e:
                    logger.error(f"Error in async signal subscriber: {e}")
            
            # Optionally publish to Redis for distributed systems (distributed pub/sub usually handles its own loop)
            if self._redis:
                await self._publish_to_redis(signal)
        
        logger.debug(
            f"Published signal: {signal.signal_type.value} "
            f"from {signal.agent_id} (severity: {signal.severity})"
        )
    
    def publish_sync(self, signal: AgentSignal) -> None:
        """
        Synchronous publish for non-async contexts.
        
        Args:
            signal: The signal to publish
        """
        try:
            # Try to get the running loop
            loop = asyncio.get_running_loop()
            # If we are in a loop, we can't block, so we schedule it
            asyncio.create_task(self.publish(signal))
        except RuntimeError:
            # No running loop in this thread. 
            # Check if there's a global supervisor loop we can use
            from run_ui import _supervisor_loop
            if _supervisor_loop and _supervisor_loop.is_running():
                asyncio.run_coroutine_threadsafe(self.publish(signal), _supervisor_loop)
            else:
                # Fallback to creating a temporary loop (blocks thread)
                asyncio.run(self.publish(signal))
    
    def subscribe(self, callback: SignalCallback) -> None:
        """
        Subscribe to receive signals (sync callback).
        
        Args:
            callback: Function to call when signal is received
        """
        with self._thread_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
                logger.debug(f"Added sync subscriber: {callback.__name__}")

    def subscribe_async(self, callback: AsyncSignalCallback) -> None:
        """
        Subscribe to receive signals (async callback).
        
        Args:
            callback: Async function to call when signal is received
        """
        with self._thread_lock:
            if callback not in self._async_subscribers:
                self._async_subscribers.append(callback)
                logger.debug(f"Added async subscriber: {callback.__name__}")

    def unsubscribe(self, callback: Union[SignalCallback, AsyncSignalCallback]) -> None:
        """
        Unsubscribe from signals.
        
        Args:
            callback: The callback to remove
        """
        with self._thread_lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
                logger.debug(f"Removed sync subscriber: {callback.__name__}")
            if callback in self._async_subscribers:
                self._async_subscribers.remove(callback)
                logger.debug(f"Removed async subscriber: {callback.__name__}")
    
    def get_recent_signals(
        self,
        agent_id: Optional[str] = None,
        signal_type: Optional[SignalType] = None,
        severity: Optional[str] = None,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[AgentSignal]:
        """
        Get recent signals, optionally filtered.
        
        Args:
            agent_id: Filter by agent ID
            signal_type: Filter by signal type
            severity: Filter by severity level
            limit: Maximum number of signals to return
            since: Only return signals after this time
        
        Returns:
            List of matching signals (most recent last)
        """
        with self._thread_lock:
            signals = list(self._history)
            
            if agent_id:
                signals = [s for s in signals if s.agent_id == agent_id]
            if signal_type:
                signals = [s for s in signals if s.signal_type == signal_type]
            if severity:
                signals = [s for s in signals if s.severity == severity]
            if since:
                signals = [s for s in signals if s.timestamp > since]
            
            return signals[-limit:]
    
    def get_signals_for_agent(
        self,
        agent_id: str,
        limit: int = 50,
    ) -> List[AgentSignal]:
        """
        Get all recent signals for a specific agent.
        
        Args:
            agent_id: The agent ID
            limit: Maximum number of signals
        
        Returns:
            List of signals for this agent
        """
        return self.get_recent_signals(agent_id=agent_id, limit=limit)
    
    def get_critical_signals(
        self,
        limit: int = 20,
    ) -> List[AgentSignal]:
        """
        Get recent critical and high severity signals.
        
        Args:
            limit: Maximum number of signals
        
        Returns:
            List of critical/high severity signals
        """
        signals = [
            s for s in self._history 
            if s.severity in ("critical", "high")
        ]
        return signals[-limit:]
    
    def clear_history(self) -> None:
        """Clear signal history."""
        with self._thread_lock:
            self._history.clear()
        logger.info("Event bus history cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get event bus statistics.
        
        Returns:
            Dictionary with stats
        """
        with self._thread_lock:
            return {
                **self._stats,
                "history_size": len(self._history),
                "subscriber_count": len(self._subscribers) + len(self._async_subscribers),
            }
    
    async def _publish_to_redis(self, signal: AgentSignal) -> None:
        """
        Publish signal to Redis for distributed systems.
        
        Args:
            signal: The signal to publish
        """
        if not self._redis:
            return
        
        try:
            import json
            channel = f"agent_signals:{signal.agent_id}"
            await self._redis.publish(channel, json.dumps(signal.to_dict()))
        except Exception as e:
            logger.error(f"Failed to publish to Redis: {e}")


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """
    Get the global event bus instance.
    
    Returns:
        The global EventBus instance
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def set_event_bus(bus: EventBus) -> None:
    """
    Set the global event bus instance.
    
    Args:
        bus: The EventBus instance to use globally
    """
    global _event_bus
    _event_bus = bus


def reset_event_bus() -> None:
    """Reset the global event bus (mainly for testing)."""
    global _event_bus
    _event_bus = None


# Convenience functions for common signal emissions
async def emit_context_warning(
    agent_id: str,
    context_id: str,
    usage_percent: float,
    iteration: Optional[int] = None,
) -> None:
    """Emit a context warning signal."""
    severity = "critical" if usage_percent >= 90 else "high"
    signal_type = SignalType.CONTEXT_CRITICAL if usage_percent >= 90 else SignalType.CONTEXT_WARNING
    
    await get_event_bus().publish(AgentSignal(
        signal_type=signal_type,
        agent_id=agent_id,
        context_id=context_id,
        timestamp=datetime.now(timezone.utc),
        severity=severity,
        details={"usage_percent": usage_percent},
        iteration=iteration,
    ))


async def emit_response_loop(
    agent_id: str,
    context_id: str,
    repeated_content: str,
    iteration: Optional[int] = None,
) -> None:
    """Emit a response loop signal."""
    await get_event_bus().publish(AgentSignal(
        signal_type=SignalType.RESPONSE_LOOP,
        agent_id=agent_id,
        context_id=context_id,
        timestamp=datetime.now(timezone.utc),
        severity="high",
        details={"repeated_content": repeated_content[:200]},
        iteration=iteration,
    ))


async def emit_tool_failure(
    agent_id: str,
    context_id: str,
    tool_name: str,
    error_message: str,
    consecutive_failures: int,
    iteration: Optional[int] = None,
) -> None:
    """Emit a tool failure signal."""
    severity = "high" if consecutive_failures >= 3 else "medium"
    
    await get_event_bus().publish(AgentSignal(
        signal_type=SignalType.TOOL_FAILURE_LOOP,
        agent_id=agent_id,
        context_id=context_id,
        timestamp=datetime.now(timezone.utc),
        severity=severity,
        details={"consecutive_failures": consecutive_failures},
        tool_name=tool_name,
        error_message=error_message,
        iteration=iteration,
    ))


async def emit_progress_stall(
    agent_id: str,
    context_id: str,
    iterations_without_progress: int,
    iteration: Optional[int] = None,
) -> None:
    """Emit a progress stall signal."""
    await get_event_bus().publish(AgentSignal(
        signal_type=SignalType.PROGRESS_STALL,
        agent_id=agent_id,
        context_id=context_id,
        timestamp=datetime.now(timezone.utc),
        severity="medium",
        details={"iterations_without_progress": iterations_without_progress},
        iteration=iteration,
    ))


async def emit_agent_error(
    agent_id: str,
    context_id: str,
    error_type: str,
    error_message: str,
    iteration: Optional[int] = None,
) -> None:
    """Emit a general agent error signal."""
    await get_event_bus().publish(AgentSignal(
        signal_type=SignalType.AGENT_ERROR,
        agent_id=agent_id,
        context_id=context_id,
        timestamp=datetime.now(timezone.utc),
        severity="high",
        details={"error_type": error_type},
        error_message=error_message,
        iteration=iteration,
    ))


async def emit_repeated_task_failure(
    agent_id: str,
    context_id: str,
    task_hash: str,
    failure_count: int,
    error_summary: list[str],
    task_preview: str = "",
    iteration: Optional[int] = None,
) -> None:
    """Emit a repeated task failure signal for supervisor redirect.
    
    Fired when the same task (by MD5 hash) has failed N times,
    triggering supervisor deep-dive RCA and intelligent redirect.
    
    Args:
        agent_id: The delegating agent (orchestrator) ID.
        context_id: Current context/chat ID.
        task_hash: MD5 short hash of the task message.
        failure_count: How many times this task has failed.
        error_summary: Collected error strings from all attempts.
        task_preview: First ~200 chars of the task message.
        iteration: Current iteration number.
    """
    await get_event_bus().publish(AgentSignal(
        signal_type=SignalType.REPEATED_TASK_FAILURE,
        agent_id=agent_id,
        context_id=context_id,
        timestamp=datetime.now(timezone.utc),
        severity="critical",
        details={
            "task_hash": task_hash,
            "failure_count": failure_count,
            "error_summary": error_summary[:20],  # Cap at 20 entries
            "task_preview": task_preview[:200],
        },
        iteration=iteration,
    ))
