from __future__ import annotations
from python.helpers.extension import Extension
from python.agent import LoopData
from python.helpers import settings
from python.helpers.print_style import PrintStyle
import logging

logger = logging.getLogger(__name__)

ENHANCEMENT_SYSTEM_PROMPT = """
You are a prompt engineering expert. Your task is to rewrite the user's prompt to be more clear, detailed, and effective for an AI agent.
Preserve the user's original intent but expand on it to provide more context and better instructions.
If the prompt is already very clear and detailed, you can return it as is or make minor improvements.
Keep it concise but comprehensive. Provide ONLY the enhanced prompt text.
"""

class PromptEnhancement(Extension):

    async def execute(self, data: dict = None, **kwargs):
        if not data or "message" not in data:
            return

        # check if prompt enhancement is enabled
        current_settings = settings.get_settings()
        if not current_settings.get("prompt_enhancement", False):
            return

        original_message = data["message"]
        if not original_message.strip():
            return

        try:
            PrintStyle(font_color="cyan", bold=True).print(f"✨ Enhancing prompt...")
            
            utility_model = self.agent.get_utility_model()
            if not utility_model:
                logger.warning("Prompt enhancement failed: Utility model not available")
                return

            # Call utility model to rewrite prompt via the agent's helper
            # Use agent's call_utility_model helper (NOT the model wrapper's unified_call directly)
            enhanced_message = await self.agent.call_utility_model(
                system=ENHANCEMENT_SYSTEM_PROMPT,
                message=f"User Prompt: {original_message}\n\nEnhanced Prompt:",
            )

            if enhanced_message and enhanced_message.strip():
                data["message"] = enhanced_message.strip()
                
                # Log the enhancement for transparency in the chat history
                self.agent.context.log.log(
                    type="info",
                    heading="Prompt Enhanced",
                    content=f"Original: {original_message}\n\nEnhanced version will be processed.",
                    verbose=True
                )
                PrintStyle(font_color="green").print(f"✨ Prompt enhanced successfully")
            
        except Exception as e:
            logger.error(f"Error during prompt enhancement: {e}")
            PrintStyle(font_color="red").print(f"❌ Prompt enhancement failed: {e}")
