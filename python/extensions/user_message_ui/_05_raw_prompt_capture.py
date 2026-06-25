"""
Raw Prompt Capture — user_message_ui extension (order 05).

Captures the original, unmodified user message to agent.data['_raw_user_prompt']
BEFORE the prompt enhancement extension (_20) can rewrite it. This ensures the
manifest fidelity gate always compares against the raw prompt, not an LLM-rewritten
version that might paraphrase literal values.

RCA-244: If prompt enhancement is enabled and rewrites "Claude Sonnet 4" to
"Claude 3.5 Sonnet" (or any other LLM-induced drift), the fidelity gate will
still have the original text to compare against.

Also persists the raw prompt to {project_dir}/prompt.md for file-based fallback.
"""
from __future__ import annotations

import logging
import os

from python.helpers.extension import Extension

logger = logging.getLogger(__name__)


class RawPromptCapture(Extension):
    """Capture the raw user message before any enhancement mutates it."""

    async def execute(self, data: dict = None, **kwargs):
        if not data or "message" not in data:
            return

        message = data["message"]
        if not message or not message.strip():
            return

        # Only capture the FIRST message (the project prompt)
        if self.agent.data.get("_raw_user_prompt"):
            return

        self.agent.data["_raw_user_prompt"] = message
        logger.info(
            f"[RAW PROMPT CAPTURE] Saved raw prompt ({len(message)} chars) "
            f"to agent.data['_raw_user_prompt']"
        )

        # RCA-ITR4 FIX-1: Detect planning-only mode from prompt prefix.
        # Root cause: _03_prompt_capture.py has _detect_planning_only_mode()
        # but fails to capture the prompt (last_raw_user_message is None on
        # first loop). This extension (_05) runs at user_message_ui and always
        # gets the raw message. So we MUST also detect planning-only here.
        self._detect_planning_only_mode(message)

        # RCA-ITR4 FIX-2: Cross-populate _original_user_prompt as fallback.
        # _03_prompt_capture.py checks this key but often fails to set it.
        # Setting it here ensures downstream consumers always have access.
        if not self.agent.data.get("_original_user_prompt"):
            self.agent.data["_original_user_prompt"] = message
            logger.info(
                f"[RAW PROMPT CAPTURE] Cross-populated _original_user_prompt "
                f"({len(message)} chars)"
            )

        # Also persist to project dir for file-based fallback
        project_dir = self.agent.data.get("_active_project_dir", "")
        if project_dir and os.path.isdir(project_dir):
            prompt_path = os.path.join(project_dir, "prompt.md")
            try:
                with open(prompt_path, "w") as f:
                    f.write(message)
                logger.info(f"[RAW PROMPT CAPTURE] Persisted prompt to {prompt_path}")
            except IOError as e:
                logger.warning(f"[RAW PROMPT CAPTURE] Failed to write prompt.md: {e}")

    def _detect_planning_only_mode(self, prompt_text: str) -> None:
        """RCA-ITR4 FIX-1: Detect planning-only mode from prompt text.

        Mirrors the detection logic in _03_prompt_capture.py to ensure the
        _planning_only flag is set regardless of which extension captures first.

        Sets agent.data['_planning_only'] = True when the prompt contains
        PLANNING_ONLY or PLANNING PHASES ONLY in the first 500 chars.
        """
        prefix = prompt_text[:500].upper()
        if "PLANNING_ONLY" in prefix or "PLANNING PHASES ONLY" in prefix:
            self.agent.data["_planning_only"] = True
            logger.info(
                "[RAW PROMPT CAPTURE] Detected planning-only mode from prompt prefix"
            )
