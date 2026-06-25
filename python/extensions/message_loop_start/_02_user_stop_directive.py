"""
User Stop Directive Detector — 2-Layer Architecture

Layer 1 (regex): Produces weighted confidence signals from pattern matching.
    - Fast, deterministic, cheap.
    - Regex NEVER auto-kills work alone.

Layer 2 (LLM): Makes the final decision using L1 signals as context.
    - For HIGH confidence (>=0.7): Auto-sets flag + injects stop instruction.
      The LLM will almost certainly comply (rubber-stamp).
    - For MEDIUM confidence (0.3-0.7): Injects signals into system prompt
      so the LLM can read the message and decide if it's a stop directive
      vs. a feature description.
    - For LOW confidence (<0.3): No injection — normal message flow.

The LLM ALWAYS has the final say. User messages ALWAYS take priority
over gates and guards.

Ordering: _02 — runs before guardrails (_05), before prompt capture (_03),
  so the stop directive is processed before any other extension acts.
"""

import logging
import re
from python.helpers.extension import Extension

logger = logging.getLogger("agix.user_stop_directive")

# ── Stop directive detection — 2-Layer CANONICAL import ──
# Single source of truth: user_intent_patterns.py
# get_stop_signals = Layer 1 (regex → confidence signals)
# format_signals_for_llm = Format signals for LLM system prompt injection
# is_stop_directive = backward-compat wrapper (high-threshold auto-trigger)
from python.helpers.user_intent_patterns import (
    get_stop_signals,
    format_signals_for_llm,
    is_stop_directive as _is_stop_compat,
)

# System instruction injected when stop is detected
_STOP_INSTRUCTION = (
    "\n\n> [!CAUTION]\n"
    "> **🛑 USER STOP DIRECTIVE — IMMEDIATE COMPLIANCE REQUIRED**\n"
    "> \n"
    "> The user has explicitly requested you STOP ALL WORK. You MUST:\n"
    "> \n"
    "> 1. **DO NOT** start any new tasks, delegations, or tool calls.\n"
    "> 2. **FLUSH STATE**: Save any in-progress work to files immediately.\n"
    ">    - Write any uncommitted state to the project directory.\n"
    ">    - Save progress notes if applicable.\n"
    "> 3. **RESPOND IMMEDIATELY**: Call the `response` tool with a brief\n"
    ">    summary of:\n"
    ">    - What was completed\n"
    ">    - What was in progress (if anything)\n"
    ">    - What remains to be done (if anything)\n"
    "> 4. **DO NOT** continue working after responding.\n"
    "> \n"
    "> This directive overrides ALL other instructions, gates, and plans.\n"
    "> Quality gates have been opened — your response WILL be accepted.\n"
    "> Comply NOW.\n"
)


class UserStopDirective(Extension):
    """Detect user stop directives and phase cap updates from ANY user message."""

    async def execute(self, **kwargs):
        loop_data = kwargs.get("loop_data")
        if not loop_data:
            return

        user_message = loop_data.user_message
        if not user_message:
            # Even without a new message, check if phase cap was reached
            self._check_phase_cap_reached(loop_data)
            return

        msg_text = (
            user_message.message
            if hasattr(user_message, "message")
            else str(user_message)
        )

        if not msg_text:
            return

        # ── Phase cap detection (from ANY user message) ──
        self._detect_phase_cap_from_message(msg_text, loop_data)

        # ── USER MESSAGE = GATE RESET (Fix 3/5) ──
        # Any new user message is a direction change. Reset stale gate state
        # so the agent can respond to the new direction without gates blocking
        # based on criteria from the OLD direction.
        # gate_block_counters stub removed — reset_all_gate_counters was a no-op

        # ── 2-Layer Stop Directive Detection ──
        # Layer 1: Regex produces weighted confidence signals
        signals = get_stop_signals(msg_text)

        if signals["confidence"] >= 0.7:
            # HIGH confidence — auto-set flag + inject stop instruction.
            # The LLM rubber-stamps this but still has the final say.
            logger.warning(
                f"[STOP DIRECTIVE] High-confidence stop detected "
                f"(conf={signals['confidence']:.0%}, patterns={signals['matched_patterns']}): "
                f"'{msg_text[:100]}' — setting _user_stop_directive=True"
            )

            # 1. Set the flag so gates open
            self.agent.data["_user_stop_directive"] = True

            # 2. Propagate to context.data for subordinate agents
            if self.agent.context:
                ctx_data = self.agent.context.get_data("_user_stop_directive")
                if not ctx_data:
                    self.agent.context.set_data("_user_stop_directive", True)

            # 3. Inject high-priority system instruction
            if hasattr(loop_data, "system"):
                loop_data.system.insert(0, _STOP_INSTRUCTION)

            # 4. Set force_response to ensure agent responds on next turn
            self.agent.data["_force_response"] = True

            # 5. Log for observability
            self.agent.log(
                type="warning",
                heading="🛑 User Stop Directive Received",
                content=(
                    f"The user has requested all work to stop "
                    f"(confidence: {signals['confidence']:.0%}). "
                    f"Flushing state and preparing completion response. "
                    f"All quality gates have been opened."
                ),
            )

        elif signals["confidence"] >= 0.3:
            # MEDIUM confidence — inject signals for LLM to decide.
            # The LLM reads the signals and the user's full message,
            # then decides whether to stop or continue.
            logger.info(
                f"[STOP SIGNAL] Medium-confidence stop signal "
                f"(conf={signals['confidence']:.0%}, patterns={signals['matched_patterns']}): "
                f"'{msg_text[:100]}' — injecting signals for LLM decision"
            )

            # Inject formatted signals into system prompt
            if hasattr(loop_data, "system"):
                llm_context = format_signals_for_llm(signals)
                if llm_context:
                    loop_data.system.append(llm_context)

            # Log for observability (no flag set — LLM decides)
            self.agent.log(
                type="info",
                heading="⚠️ Stop Signal Detected (LLM Deciding)",
                content=(
                    f"Regex detected possible stop intent "
                    f"(confidence: {signals['confidence']:.0%}) but this may be a feature "
                    f"description. The LLM will read the full message and decide."
                ),
            )
        # else: confidence < 0.3 — no stop signal, normal message flow

    def _detect_phase_cap_from_message(self, msg_text: str, loop_data) -> None:
        """Scan a user message for phase cap directives.

        Called on EVERY user message (not just the initial prompt).
        If a phase cap is detected:
        1. Sets/updates _phase_cap in agent.data
        2. If current phase already exceeds the new cap, triggers forced completion
        """
        try:
            from python.helpers.phase_cap import extract_phase_scope

            cap = extract_phase_scope(msg_text)
            if cap is not None:
                old_cap = self.agent.data.get("_phase_cap")
                self.agent.data["_phase_cap"] = cap
                logger.warning(
                    f"[PHASE CAP] User set phase cap to {cap} "
                    f"(was {old_cap}) from message: '{msg_text[:100]}'"
                )

                # Check if we've already passed this cap
                self._check_phase_cap_reached(loop_data)
        except Exception as e:
            logger.debug(f"[PHASE CAP] Detection error (non-fatal): {e}")

    def _check_phase_cap_reached(self, loop_data) -> None:
        """Check if current phase exceeds the phase cap — trigger forced completion.

        This follows the SAME pattern as _user_stop_directive:
        - Set a flag (_phase_cap_reached) so gates open
        - Inject a completion instruction
        - Set _force_response
        """
        try:
            from python.helpers.phase_cap import (
                check_phase_cap_reached,
                PHASE_CAP_COMPLETION_INSTRUCTION,
            )

            if not check_phase_cap_reached(self.agent.data):
                return

            # Already triggered — don't re-inject
            if self.agent.data.get("_phase_cap_reached"):
                return

            phase_cap = self.agent.data.get("_phase_cap")
            current_phase = self.agent.data.get("_current_phase")

            logger.warning(
                f"[PHASE CAP] 🛑 Phase cap REACHED: current={current_phase}, "
                f"cap={phase_cap} — triggering forced completion"
            )

            # 1. Set flag so completion gate opens
            self.agent.data["_phase_cap_reached"] = True

            # 2. Inject forced completion instruction
            if loop_data and hasattr(loop_data, "system"):
                loop_data.system.insert(0, PHASE_CAP_COMPLETION_INSTRUCTION)

            # 3. Force response on next turn
            self.agent.data["_force_response"] = True

            # 4. Log for observability
            self.agent.log(
                type="warning",
                heading=f"🛑 Phase Cap Reached ({phase_cap})",
                content=(
                    f"Current phase {current_phase} has reached phase cap {phase_cap}. "
                    f"Forcing completion — all quality gates opened. "
                    f"The agent will deliver results via the response tool."
                ),
            )
        except Exception as e:
            logger.debug(f"[PHASE CAP] Reached-check error (non-fatal): {e}")

