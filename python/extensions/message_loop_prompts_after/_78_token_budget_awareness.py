"""
Token Budget Awareness — message_loop_prompts_after extension (order 78).

Injects token budget information into agent context so agents can
plan their output size intelligently. Agents learn:
  - Their per-turn output token limit (from profile/config max_tokens)
  - The user's total API output ceiling (from settings)
  - How to plan their work within these constraints

Root cause (RCA-299):
    Agents were unaware of their output token budget and either:
    (a) Produced truncated outputs because they exceeded the 4k limit, or
    (b) Wasted turns on tiny outputs when they had 16k available.
    
    Making agents aware of both their profile limit AND the user's
    total API ceiling lets them plan responses optimally.

Position 78 places it after time budget (77) so both budget signals
appear in sequence.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from python.helpers.extension import Extension
from python.agent import LoopData

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.token_budget_awareness")


class TokenBudgetAwareness(Extension):
    """Inject token budget context into agent loop."""

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        agent: Agent = self.agent

        # Get per-turn max_tokens from model config
        profile_max_tokens = 0
        if hasattr(agent.config, "chat_model") and agent.config.chat_model:
            profile_max_tokens = getattr(
                agent.config.chat_model, "max_tokens", 0
            ) or 0

        # Get user's total API ceiling from settings
        from python.helpers import settings
        current_settings = settings.get_settings()
        user_api_max = current_settings.get("chat_model_max_tokens", 0) or 0

        # Also check global override if enabled
        if current_settings.get("global_model_enabled", False):
            global_max = current_settings.get("global_model_max_tokens", 0) or 0
            if global_max > 0:
                user_api_max = global_max

        # Only inject if we have meaningful values
        if profile_max_tokens <= 0 and user_api_max <= 0:
            return

        # Build awareness message
        parts = []
        if profile_max_tokens > 0:
            parts.append(
                f"Your per-turn output token budget is **{profile_max_tokens:,}** tokens. "
                f"Plan your responses to use this budget effectively — "
                f"write complete implementations, not fragments."
            )
        if user_api_max > 0 and user_api_max != profile_max_tokens:
            parts.append(
                f"The user's total API output ceiling is **{user_api_max:,}** tokens per request. "
                f"Your profile limit ({profile_max_tokens:,}) is the operative constraint for planning."
            )

        if parts:
            budget_msg = " ".join(parts)
            loop_data.extras_temporary["token_budget"] = budget_msg
            logger.debug(
                f"[TOKEN BUDGET] {agent.agent_name}: "
                f"profile={profile_max_tokens}, user_api={user_api_max}"
            )
