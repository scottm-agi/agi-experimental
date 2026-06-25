"""
Skill Activation Gate — tool_execute_before (defense-in-depth)

Fires on the FIRST call_subordinate / call_subordinate_batch tool call and
checks whether the orchestrator has auto-activated a relevant skill via
_72_skill_auto_activation (message_loop_prompts_after). If not, auto-triggers
the activation NOW — before the first delegation executes.

This is defense-in-depth: if the auto-activation extension didn't fire (e.g.,
user message was too short, or the loop hadn't processed prompts_after yet),
this gate catches it before delegation.

Priority: 15d (after profile enforcement at _15_, before decomposition gate at _16_)

Behavior:
  - Only fires for orchestrator profiles (multiagentdev, alex, default)
  - Only fires for delegation tools (call_subordinate, call_subordinate_batch)
  - Checks if _skill_auto_activated is True — if so, skip
  - If not activated, extracts user message from history and runs
    auto_activate_for_prompt() to inject skill instructions
  - Sets _skill_auto_activated = True regardless (one-shot gate)
  - Returns None (advisory, never blocks the tool call)
"""

from python.helpers.extension import Extension

import logging

logger = logging.getLogger("agix.skill_activation_gate")

_DELEGATION_TOOLS = {"call_subordinate", "call_subordinate_batch"}
# FIX-020: Use centralized profile registry instead of hardcoded names
from python.helpers.profile_registry import ORCHESTRATOR_PROFILES as _ORCHESTRATOR_PROFILES


class SkillActivationGate(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"})

    """Pre-delegation gate that ensures skill auto-activation has occurred."""

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        """Check and trigger skill auto-activation before first delegation.

        Returns None always — this is advisory, never blocking.
        """
        # Only intercept delegation tools
        if tool_name not in _DELEGATION_TOOLS:
            return None

        # Only applies to orchestrator profiles
        profile = getattr(self.agent.config, "profile", None) or ""
        if profile.lower() not in _ORCHESTRATOR_PROFILES:
            return None

        # Skip if already activated (one-shot gate)
        if self.agent.get_data("_skill_auto_activated"):
            return None

        # ── Try to auto-activate ─────────────────────────────────
        try:
            self._try_auto_activate()
        except Exception as e:
            logger.warning(f"[SKILL GATE] Auto-activation failed (non-fatal): {e}")

        # Always set the flag so we never fire again
        self.agent.set_data("_skill_auto_activated", True)

        return None  # Advisory — never block

    def _try_auto_activate(self):
        """Attempt to auto-activate a matching skill from user prompt."""
        # Need skills_manager
        if not hasattr(self.agent, "skills_manager"):
            logger.debug("[SKILL GATE] No skills_manager on agent — skipping")
            return

        # Get user message from history
        user_msg = self._get_user_message()
        if not user_msg:
            logger.debug("[SKILL GATE] No user message found — skipping")
            return

        # Run auto-activation
        activation = self.agent.skills_manager.auto_activate_for_prompt(user_msg)
        if not activation:
            logger.info("[SKILL GATE] No matching skill for user prompt")
            return

        # Determine role for prompt format
        profile = (self.agent.config.profile or "").lower()
        role = "orchestrator" if profile in _ORCHESTRATOR_PROFILES else "subordinate"

        # Build and cache the prompt section
        prompt_section = self.agent.skills_manager.build_auto_activation_prompt(
            activation, role=role
        )
        self.agent.set_data("_skill_auto_activation_prompt", prompt_section)
        self.agent.set_data("_activated_skill_name", activation["name"])

        logger.info(
            f"[SKILL GATE] Auto-activated skill '{activation['name']}' "
            f"(score={activation.get('score', '?')}, role={role})"
        )

    def _get_user_message(self) -> str:
        """Extract the user's first message from history."""
        try:
            for msg in self.agent.history.messages_all:
                if not msg.ai:
                    content = msg.content
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, list):
                        text_parts = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, str):
                                text_parts.append(part)
                        return " ".join(text_parts)
        except Exception:
            pass
        return ""
