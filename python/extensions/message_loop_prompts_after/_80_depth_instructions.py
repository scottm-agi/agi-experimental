from __future__ import annotations
from python.helpers.extension import Extension
from python.agent import LoopData
import logging

logger = logging.getLogger(__name__)


class DepthInstructions(Extension):
    """
    Injects depth-appropriate instructions into agent prompts based on
    the user's most recent message. Uses regex-weighted classification
    ported from agix _get_intent_weights() pattern.

    Runs after memory recall (_75), so depth context enriches the prompt.
    """

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        from python.helpers.depth_classifier import classify_depth

        # Get the user's latest message from the conversation history
        user_message = self._get_latest_user_message()
        if not user_message:
            return

        # Classify depth expectation
        result = classify_depth(user_message)

        # Only inject if non-standard depth detected
        if result.instructions:
            loop_data.extras_temporary["depth_instructions"] = result.instructions
            self.agent.context.log.log(
                type="info",
                content=f"[DEPTH] Classified as {result.level} (score={result.score:.2f})",
            )

    def _get_latest_user_message(self) -> str:
        """Extract the latest user message from conversation history."""
        try:
            history = self.agent.context.history
            if not history:
                return ""
            # Walk backwards to find most recent user message
            for msg in reversed(history):
                if hasattr(msg, "role") and msg.role == "user":
                    content = getattr(msg, "content", "")
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, list):
                        # Multi-part message — extract text parts
                        texts = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                texts.append(part.get("text", ""))
                            elif isinstance(part, str):
                                texts.append(part)
                        return " ".join(texts)
            return ""
        except Exception as e:
            logger.warning(f"[DEPTH INSTRUCTIONS] Failed to extract latest user message: {e}")
            return ""
