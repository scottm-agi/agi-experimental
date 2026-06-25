from __future__ import annotations
"""
Context Error Recovery Extension

This extension integrates the context error recovery system with the main LLM call.
It wraps the LLM call to automatically detect and recover from context window errors.

The recovery process:
1. Detects context overflow errors from any provider (OpenAI, Anthropic, Bedrock, Google)
2. Automatically condenses history by 25%
3. Retries the failed request (up to 3 times)
4. Continues task execution seamlessly

This extension works alongside the proactive context watcher (_40_context_watcher.py)
to provide both proactive and reactive context management.
"""

import logging
from typing import TYPE_CHECKING

from python.helpers.extension import Extension
from python.helpers.context_error_recovery import (
    detect_context_error,
    get_recovery_handler,
)

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.context_error_recovery_ext")


class ContextErrorRecoveryExtension(Extension):
    """
    Extension that enables context error recovery for the main LLM call.
    
    This extension stores a reference to itself in the agent's data so that
    the error handling in agent.py can use it for recovery.
    """
    
    async def execute(self, loop_data: "LoopData | None" = None, **kwargs):
        """
        Store recovery handler reference in agent data for use during LLM calls.
        
        The actual recovery happens in the error handling code, but we need
        to make the handler available.
        """
        # Store the recovery handler in agent data for access during error handling
        self.agent.set_data("_context_recovery_handler", get_recovery_handler())
        
        # Log that recovery is enabled
        if loop_data and loop_data.iteration == 0:
            logger.debug(
                f"Context error recovery enabled for agent {self.agent.agent_name}"
            )
