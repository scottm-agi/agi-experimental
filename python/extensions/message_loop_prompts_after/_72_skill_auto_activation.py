"""
Skill Auto-Activation Extension — message_loop_prompts_after

RCA-MSR-Smoke-1779240828: The fullstack-dev skill was never loaded because
the LLM never called discover_skills — even though the prompt says
"MANDATORY Phase 0 Gate". This extension removes the LLM-voluntary
dependency by automatically detecting matching skills from the user's
first message and injecting their full instructions into the system prompt.

Fires once per conversation (caches result after first activation).

For orchestrators (multiagentdev, alex): Injects FULL skill body.
For others: Injects a concise skill reference.
"""
from __future__ import annotations
from python.helpers.extension import Extension
from python.agent import LoopData

import logging

logger = logging.getLogger("agix.skill_auto_activation")


class SkillAutoActivation(Extension):
    """Auto-activate matching skills based on user prompt content."""

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        """Inject auto-activated skill into system prompt extras.

        This fires on every message_loop_prompts_after, but only activates
        once (first time user message is available). Result is cached.
        """
        # Skip if already activated this conversation
        if self.agent.get_data("_skill_auto_activated"):
            cached = self.agent.get_data("_skill_auto_activation_prompt")
            if cached:
                loop_data.extras_temporary["skill_auto_activation"] = cached
            return

        # CRITICAL: Skip auto-activation for EVENT_HOOK (webhook) contexts.
        # Webhook prompts contain boilerplate text ("repository_automation",
        # "analyze_issue", "provider", etc.) that falsely triggers fullstack-dev
        # with score ~4.29 via keyword/semantic matching. This causes the agent
        # to run the full Phase 0→7 build pipeline for simple tool calls like
        # "analyze this issue". Webhook tasks should use the Step 0 Scope
        # Assessment in the solving prompt to decide complexity tier.
        if self.agent.context and hasattr(self.agent.context, 'type'):
            from python.agent import AgentContextType
            if self.agent.context.type == AgentContextType.EVENT_HOOK:
                logger.info("SKILL AUTO-ACTIVATION: Skipped for EVENT_HOOK context (webhook)")
                return

        # Need skills_manager
        if not hasattr(self.agent, 'skills_manager'):
            return

        # Get the user's first message
        user_msg = self._get_user_message()
        if not user_msg or len(user_msg) < 50:
            return

        # Auto-detect matching skill
        try:
            activation = self.agent.skills_manager.auto_activate_for_prompt(user_msg)
            if not activation:
                # S-3 Fix: Do NOT set _skill_auto_activated=True when no skill matched.
                # Setting True here told downstream gates that activation succeeded,
                # preventing re-attempts and causing skill-less execution.
                self.agent.set_data("_skill_auto_activated", False)
                logger.info("SKILL AUTO-ACTIVATION: No matching skill for user prompt")
                return

            # Determine role for prompt format
            profile = (self.agent.config.profile or "").lower()
            # FIX-020: Use centralized profile registry instead of hardcoded names
            from python.helpers.profile_registry import is_orchestrator
            role = "orchestrator" if is_orchestrator(profile) else "subordinate"

            # Build prompt section
            prompt_section = self.agent.skills_manager.build_auto_activation_prompt(
                activation, role=role
            )

            # Cache it for future loops
            self.agent.set_data("_skill_auto_activated", True)
            self.agent.set_data("_skill_auto_activation_prompt", prompt_section)
            self.agent.set_data("_activated_skill_name", activation["name"])

            # Inject into extras_temporary (the proper extension API)
            loop_data.extras_temporary["skill_auto_activation"] = prompt_section

            logger.info(
                f"SKILL AUTO-ACTIVATED: {activation['name']} "
                f"(score={activation['score']}, role={role}, "
                f"layers={activation['layers_hit']})"
            )

        except Exception as e:
            # S-3 Fix: Log at ERROR level — skill activation failures are NOT non-fatal.
            # A missing skill means the agent runs without crucial instructions,
            # leading to architecture gaps, missing phases, and project failures.
            logger.error(f"Skill auto-activation FAILED: {e}", exc_info=True)

    def _get_user_message(self) -> str:
        """Extract the user's first message from history."""
        try:
            for msg in self.agent.history.messages_all:
                if not msg.ai:
                    content = msg.content
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, list):
                        # Multi-part message
                        text_parts = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, str):
                                text_parts.append(part)
                        return " ".join(text_parts)
        except Exception as e:
            logger.warning(f"[SKILL ACTIVATION] Failed to get user message from history: {e}")
        return ""
