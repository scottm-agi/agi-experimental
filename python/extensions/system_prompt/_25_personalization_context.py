from __future__ import annotations
import os
import logging
from python.helpers.extension import Extension
from python.agent import Agent, LoopData
from python.helpers import settings

logger = logging.getLogger(__name__)

# Default data directory for personalization storage
PERSONALIZATION_DATA_DIR = os.environ.get(
    "PERSONALIZATION_DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "personalization"),
)


class PersonalizationContext(Extension):
    """
    Injects personalization profile context into the system prompt.
    
    This extension loads the user's personality profile and formats it
    as additional system prompt context so the agent can adapt its
    communication style, detail level, and proactivity.
    """

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs,
    ):
        set = settings.get_settings()

        # Check if personalization is enabled
        if not set.get("personalization_enabled", True):
            return

        # Check if personalized replies are enabled (style/voice adaptation)
        if not set.get("personalized_reply", True):
            return

        try:
            from python.helpers.personalization import (
                PersonalizationProfile,
                format_prompt_context,
            )

            data_dir = os.path.abspath(PERSONALIZATION_DATA_DIR)
            user_id = "default"  # Single-user system

            # Try to load existing profile
            profile = PersonalizationProfile.load(user_id, data_dir)
            if profile is None:
                return  # No profile yet — nothing to inject

            # Format profile into prompt context
            prompt_text = format_prompt_context(profile, confidence_threshold=0.3)
            if prompt_text:
                system_prompt.append(prompt_text)
                logger.debug(
                    "Personalization context injected (%d chars)", len(prompt_text)
                )

        except Exception as e:
            logger.warning("Personalization context extension error: %s", e)
