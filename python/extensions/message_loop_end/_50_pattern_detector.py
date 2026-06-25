from __future__ import annotations
"""
Pattern detector extension that emits signals to the event bus.

This extension runs at the end of each message loop iteration and
checks for patterns that indicate the agent may be stuck or failing.
It emits signals to the event bus for the LLM supervisor to process.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from python.helpers.event_bus import (
    get_event_bus,
    AgentSignal,
    SignalType,
)

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger(__name__)


async def execute(agent: "Agent", loop_data: "LoopData", **kwargs):
    """
    Check for patterns and emit signals to the event bus.
    
    This runs at the end of each message loop iteration.
    """
    event_bus = get_event_bus()
    if not event_bus:
        return
    
    # Get agent info
    agent_id = getattr(agent, 'agent_name', str(id(agent)))
    context_id = ""
    context_type = None  # Will be "CHAT", "TASK", "SCHEDULED", etc.
    if hasattr(agent, 'context') and agent.context:
        context_id = getattr(agent.context, 'id', '')
        # Extract context_type for supervisor filtering
        raw_context_type = getattr(agent.context, 'type', None)
        if raw_context_type is not None:
            # Convert enum to string if needed
            context_type = raw_context_type.name if hasattr(raw_context_type, 'name') else str(raw_context_type)
    
    iteration = getattr(loop_data, 'iteration', 0)
    
    # Check context usage
    await _check_context_usage(agent, agent_id, context_id, iteration, event_bus, context_type)
    
    # Check for response loops
    await _check_response_loop(agent, agent_id, context_id, iteration, event_bus, context_type)
    
    # Check for tool failure loops
    await _check_tool_failures(agent, agent_id, context_id, iteration, event_bus, context_type)
    
    # Check for progress stall
    await _check_progress_stall(agent, agent_id, context_id, iteration, event_bus, context_type)


async def _check_context_usage(
    agent: "Agent",
    agent_id: str,
    context_id: str,
    iteration: int,
    event_bus,
    context_type: str = None,
) -> None:
    """Check context window usage and emit warning if high."""
    ctx_window = getattr(agent, 'get_data', lambda x: None)("ctx_window") or {}
    tokens = ctx_window.get("tokens", 0)
    
    # Get max tokens from config
    max_tokens = 128000  # Default
    if hasattr(agent, 'config') and hasattr(agent.config, 'chat_model'):
        max_tokens = getattr(agent.config.chat_model, 'ctx_length', 128000) or 128000
    
    if max_tokens <= 0:
        return
    
    usage_percent = (tokens / max_tokens) * 100
    
    if usage_percent >= 90:
        await event_bus.publish(AgentSignal(
            signal_type=SignalType.CONTEXT_CRITICAL,
            agent_id=agent_id,
            context_id=context_id,
            timestamp=datetime.now(timezone.utc),
            severity="critical",
            details={"usage_percent": usage_percent, "tokens": tokens, "max_tokens": max_tokens},
            iteration=iteration,
            context_type=context_type,
        ))
        logger.warning(f"Agent {agent_id} context critical: {usage_percent:.1f}%")
    elif usage_percent >= 76:
        await event_bus.publish(AgentSignal(
            signal_type=SignalType.CONTEXT_WARNING,
            agent_id=agent_id,
            context_id=context_id,
            timestamp=datetime.now(timezone.utc),
            severity="high",
            details={"usage_percent": usage_percent, "tokens": tokens, "max_tokens": max_tokens},
            iteration=iteration,
            context_type=context_type,
        ))
        logger.info(f"Agent {agent_id} context warning: {usage_percent:.1f}%")


async def _check_response_loop(
    agent: "Agent",
    agent_id: str,
    context_id: str,
    iteration: int,
    event_bus,
    context_type: str = None,
) -> None:
    """Detect if agent is repeating responses."""
    if not hasattr(agent, 'history'):
        return
    
    history = agent.history.output() if hasattr(agent.history, 'output') else []
    if len(history) < 4:
        return
    
    # Get recent AI responses
    recent_responses = []
    for msg in history[-6:]:
        if msg.get("ai", False):
            content = msg.get("content", "")
            if isinstance(content, str):
                recent_responses.append(content[:500])  # First 500 chars
    
    if len(recent_responses) < 3:
        return
    
    # Skip if the agent is actively doing batch delegation — batch tool calls
    # naturally look similar and are NOT a response loop
    last_response = recent_responses[-1]
    delegation_keywords = ["call_subordinate_batch", "call_subordinate", "fan_out_subordinates", "subordinate"]
    if any(kw in last_response.lower() for kw in delegation_keywords):
        return
    
    # Check for exact or near-exact matches
    match_count = sum(1 for r in recent_responses[-3:-1] if r == last_response)
    
    if match_count >= 2:
        await event_bus.publish(AgentSignal(
            signal_type=SignalType.RESPONSE_LOOP,
            agent_id=agent_id,
            context_id=context_id,
            timestamp=datetime.now(timezone.utc),
            severity="high",
            details={
                "repeated_content": last_response[:200],
                "match_count": match_count,
            },
            iteration=iteration,
            context_type=context_type,
        ))
        logger.warning(f"Agent {agent_id} response loop detected")


async def _check_tool_failures(
    agent: "Agent",
    agent_id: str,
    context_id: str,
    iteration: int,
    event_bus,
    context_type: str = None,
) -> None:
    """Count consecutive recent tool failures."""
    if not hasattr(agent, 'history'):
        return
    
    history = agent.history.output() if hasattr(agent.history, 'output') else []
    if len(history) < 3:
        return
    
    consecutive_failures = 0
    last_tool_name = None
    last_error = None
    
    # Check recent messages for tool failures
    for msg in reversed(history[-10:]):
        content = msg.get("content", "")
        
        # Check for tool result messages
        if isinstance(content, dict):
            tool_result = content.get("tool_result", "")
            tool_name = content.get("tool_name", "")
            
            if isinstance(tool_result, str):
                if "error" in tool_result.lower() or "failed" in tool_result.lower():
                    consecutive_failures += 1
                    if last_tool_name is None:
                        last_tool_name = tool_name
                        last_error = tool_result[:200]
                else:
                    break  # Success, stop counting
        elif isinstance(content, str):
            # Check for error patterns in string content
            if "error" in content.lower() and "tool" in content.lower():
                consecutive_failures += 1
                if last_error is None:
                    last_error = content[:200]
            elif consecutive_failures > 0:
                break  # Non-error message, stop counting
    
    if consecutive_failures >= 3:
        await event_bus.publish(AgentSignal(
            signal_type=SignalType.TOOL_FAILURE_LOOP,
            agent_id=agent_id,
            context_id=context_id,
            timestamp=datetime.now(timezone.utc),
            severity="high",
            details={"consecutive_failures": consecutive_failures},
            tool_name=last_tool_name,
            error_message=last_error,
            iteration=iteration,
            context_type=context_type,
        ))
        logger.warning(f"Agent {agent_id} tool failure loop: {consecutive_failures} failures")


async def _check_progress_stall(
    agent: "Agent",
    agent_id: str,
    context_id: str,
    iteration: int,
    event_bus,
    context_type: str = None,
) -> None:
    """Detect if agent is making no progress."""
    # Only check after several iterations
    if iteration < 5:
        return
    
    if not hasattr(agent, 'history'):
        return
    
    history = agent.history.output() if hasattr(agent.history, 'output') else []
    if len(history) < 6:
        return
    
    # Get recent AI responses
    recent_contents = []
    for msg in history[-6:]:
        if msg.get("ai", False):
            content = msg.get("content", "")
            if isinstance(content, str):
                # Normalize: lowercase, first 100 chars
                recent_contents.append(content[:100].lower().strip())
    
    if len(recent_contents) < 4:
        return
    
    # Skip if the agent is doing batch delegation — these naturally look similar
    delegation_keywords = ["call_subordinate_batch", "call_subordinate", "fan_out_subordinates", "subordinate"]
    if any(kw in c for c in recent_contents for kw in delegation_keywords):
        return
    
    # Check for low diversity in responses
    unique_contents = len(set(recent_contents))
    
    if unique_contents <= 2:
        await event_bus.publish(AgentSignal(
            signal_type=SignalType.PROGRESS_STALL,
            agent_id=agent_id,
            context_id=context_id,
            timestamp=datetime.now(timezone.utc),
            severity="medium",
            details={
                "iterations_without_progress": iteration,
                "unique_responses": unique_contents,
            },
            iteration=iteration,
            context_type=context_type,
        ))
        logger.info(f"Agent {agent_id} progress stall detected")
