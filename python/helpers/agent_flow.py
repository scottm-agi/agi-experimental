"""
Agent Flow Control Methods — Extracted from agent.py (Issue #1200 P0.2).

This module contains flow-control methods extracted during modularization:
- _attempt_supervisor_redirect: Escape hatch supervisor redirect logic
- _log_summarizer: Log summarization via utility LLM

The Agent class delegates to these implementations via thin wrappers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.log import Log

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _attempt_supervisor_redirect
# ---------------------------------------------------------------------------
async def attempt_supervisor_redirect_impl(
    agent: "Agent",
    escape_context: dict,
) -> Optional[str]:
    """
    Escape Hatch (RCA-252): Attempt to get an L2 supervisor redirect
    before falling back to hard-stop termination.

    This method:
    1. Finds the L2 supervisor via the agent reference chain
    2. Calls request_redirect() with the escape context
    3. Returns the redirect prompt string on success, None on failure/timeout

    Args:
        agent: The Agent instance.
        escape_context: Dict with keys 'reason', 'type', 'ts', 'repeat_count'

    Returns:
        Redirect prompt string if supervisor responds, None if unavailable/timeout.
    """
    try:
        # Find the supervisor — walk up the agent reference chain
        supervisor = None

        # Method 0 (RCA-256): Check the GLOBAL supervisor first.
        # This is the canonical path — set by run_ui.py via set_llm_supervisor()
        # and used by _50_supervisor_register.py for agent registration.
        # Previous methods (context._agents, _supervisor_ref) were dead-ends:
        # the SupervisorAgent is never stored in agent context or as an attribute.
        from python.helpers.supervisor_agent import get_llm_supervisor
        supervisor = get_llm_supervisor()

        if not supervisor:
            # Method 1 (fallback): Check if this agent has a supervisor in context
            if hasattr(agent, 'context') and agent.context:
                from python.helpers.supervisor.tools import ToolsMixin
                for ref_agent in getattr(agent.context, '_agents', {}).values():
                    if isinstance(ref_agent, ToolsMixin) or hasattr(ref_agent, 'request_redirect'):
                        supervisor = ref_agent
                        break

        if not supervisor:
            # Method 2 (fallback): Check if there's a supervisor ref attribute
            if hasattr(agent, '_supervisor_ref'):
                supervisor = agent._supervisor_ref

        if not supervisor or not hasattr(supervisor, 'request_redirect'):
            logger.warning(
                f"[ESCAPE_HATCH] {agent.agent_name}: No supervisor with request_redirect found. "
                f"Falling back to hard-stop."
            )
            return None

        # Attempt the redirect with a 30-second timeout
        redirect = await asyncio.wait_for(
            supervisor.request_redirect(agent, escape_context),
            timeout=30.0,
        )

        if redirect and isinstance(redirect, str) and len(redirect.strip()) > 10:
            logger.warning(
                f"[ESCAPE_HATCH] {agent.agent_name}: Supervisor redirect received "
                f"({len(redirect)} chars)."
            )
            return redirect
        else:
            logger.warning(
                f"[ESCAPE_HATCH] {agent.agent_name}: Supervisor returned empty/invalid redirect."
            )
            return None

    except asyncio.TimeoutError:
        logger.warning(
            f"[ESCAPE_HATCH] {agent.agent_name}: Supervisor redirect timed out (30s). "
            f"Falling back to hard-stop."
        )
        return None
    except Exception as e:
        logger.warning(
            f"[ESCAPE_HATCH] {agent.agent_name}: Error during supervisor redirect: {e}. "
            f"Falling back to hard-stop."
        )
        return None


# ---------------------------------------------------------------------------
# _log_summarizer
# ---------------------------------------------------------------------------
async def log_summarizer_impl(agent: "Agent", items: list) -> str:
    """
    Summarize a block of log items using the utility LLM.
    Implementation of Agent._log_summarizer — extracted verbatim (Issue #1200 P0.2).
    """
    if not items:
        return ""

    # Convert items to text representation
    texts = []
    for item in items:
        prefix = f"[{item.type}] {item.heading}" if item.heading else f"[{item.type}]"
        content = item.content if item.content else ""
        texts.append(f"{prefix}\n{content}")
    
    full_text = "\n---\n".join(texts)
    
    system = "You are a concise technical summarizer. Summarize the following sequence of agent activities (tool calls, logs, monologues) into a concise bulleted list or paragraph. IMPORTANT: Explicitly mention which tools were called and their high-level results. Keep it under 100 words."
    
    try:
        # We use utility_model for summarization to preserve chat context for main tasks
        summary = await agent.call_utility_model(
            system=system,
            message=f"Please summarize these logs:\n\n{full_text}"
        )
        return summary.strip()
    except Exception as e:
        logger.warning(f"Log summarization failed: {e}")
        return "(Summary generation unavailable)"

