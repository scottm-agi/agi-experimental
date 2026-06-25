"""
Progressive Repetition Recovery Manager (P0).

Provides a 5-layer escalation ladder for model repetition loops:
1. Text hint (attempts 1-2): Progressively stronger advice messages
2. Temperature bump (attempt 3): Increase model temperature to break pattern
3. Context condensation (attempt 4): Summarize history to remove repetitive content
4. History truncation (attempt 5): Keep only the last N messages
5. Hard stop (attempt 6+): Terminate the agent loop

Usage:
    from python.helpers.repetition_recovery import RepetitionRecoveryManager

    mgr = RepetitionRecoveryManager()
    strategy = mgr.get_recovery_strategy(attempt=3)
    # => {"action": "temp_bump", "temp_delta": 0.15, "advice": "..."}

    # Agent.data helpers:
    attempt = increment_attempt(agent.data)
    strategy = mgr.get_recovery_strategy(attempt)
    
    # On non-repetitive success:
    reset_attempt(agent.data)
"""

from __future__ import annotations
from typing import Any, Dict, Optional

# ═══════════════════════════════════════════════════════════════════════
# Agent data keys — shared constants for wiring
# ═══════════════════════════════════════════════════════════════════════

AGENT_DATA_ATTEMPT_KEY = "_repetition_recovery_attempt"
AGENT_DATA_TEMP_OVERRIDE_KEY = "_temp_override"


# ═══════════════════════════════════════════════════════════════════════
# Agent data helper functions
# ═══════════════════════════════════════════════════════════════════════

def get_attempt(agent_data: Dict[str, Any]) -> int:
    """Get the current repetition recovery attempt count."""
    return agent_data.get(AGENT_DATA_ATTEMPT_KEY, 0)


def increment_attempt(agent_data: Dict[str, Any]) -> int:
    """Increment and return the repetition recovery attempt count."""
    current = get_attempt(agent_data)
    new_val = current + 1
    agent_data[AGENT_DATA_ATTEMPT_KEY] = new_val
    return new_val


def reset_attempt(agent_data: Dict[str, Any]) -> None:
    """Reset the repetition recovery attempt count to 0."""
    agent_data[AGENT_DATA_ATTEMPT_KEY] = 0


def get_temp_override(agent_data: Dict[str, Any]) -> Optional[float]:
    """Get the current temperature override, or None if not set."""
    return agent_data.get(AGENT_DATA_TEMP_OVERRIDE_KEY, None)


def set_temp_override(agent_data: Dict[str, Any], delta: float) -> None:
    """Set the temperature override delta."""
    agent_data[AGENT_DATA_TEMP_OVERRIDE_KEY] = delta


def clear_temp_override(agent_data: Dict[str, Any]) -> None:
    """Remove the temperature override."""
    agent_data.pop(AGENT_DATA_TEMP_OVERRIDE_KEY, None)


# ═══════════════════════════════════════════════════════════════════════
# Recovery Strategy Manager
# ═══════════════════════════════════════════════════════════════════════

class RepetitionRecoveryManager:
    """Pure-function recovery strategy provider.

    Given an attempt number, returns a strategy dict describing what
    recovery action to take and what advice to give the agent.

    This class is stateless — all state lives in agent.data via the
    helper functions above.
    """

    # Escalation ladder thresholds
    MAX_TEXT_HINTS = 2       # Attempts 1-2: text hints
    TEMP_BUMP_ATTEMPT = 3   # Attempt 3: temperature bump
    CONDENSE_ATTEMPT = 4    # Attempt 4: context condensation
    TRUNCATE_ATTEMPT = 5    # Attempt 5: history truncation
    HARD_STOP_ATTEMPT = 6   # Attempt 6+: hard stop

    # Strategy parameters
    TEMP_BUMP_DELTA = 0.15  # How much to increase temperature
    TRUNCATE_KEEP_LAST = 4  # How many messages to keep on truncation

    def get_recovery_strategy(self, attempt: int) -> Dict[str, Any]:
        """Return the recovery strategy dict for the given attempt number.

        Args:
            attempt: The 1-based attempt number (how many consecutive
                     repetition errors have been seen).

        Returns:
            A dict with keys:
                "action": str — one of "text_hint", "temp_bump",
                    "condense", "truncate", "hard_stop"
                "advice": str — human-readable advice for the agent
                Additional keys depending on action (e.g. "temp_delta",
                "keep_last")
        """
        if attempt <= 0:
            attempt = 1

        if attempt <= self.MAX_TEXT_HINTS:
            return self._text_hint_strategy(attempt)
        elif attempt == self.TEMP_BUMP_ATTEMPT:
            return self._temp_bump_strategy()
        elif attempt == self.CONDENSE_ATTEMPT:
            return self._condense_strategy()
        elif attempt == self.TRUNCATE_ATTEMPT:
            return self._truncate_strategy()
        else:
            return self._hard_stop_strategy()

    # ─── Private strategy builders ─────────────────────────────────

    def _text_hint_strategy(self, attempt: int) -> Dict[str, Any]:
        """Return escalating text hint advice."""
        if attempt == 1:
            advice = (
                "Your previous response was detected as repetitive. "
                "Try a fundamentally different approach: use a different tool, "
                "change your strategy, or ask the user for clarification. "
                "Do NOT rephrase the same message."
            )
        else:
            advice = (
                "WARNING: You are still repeating yourself (attempt 2). "
                "Your approach is not working. You MUST change tactics completely: "
                "try a completely different tool, break the problem into smaller steps, "
                "or explicitly state what is blocking you. "
                "Continuing to repeat will escalate to forced recovery actions."
            )
        return {
            "action": "text_hint",
            "advice": advice,
        }

    def _temp_bump_strategy(self) -> Dict[str, Any]:
        """Return temperature bump strategy."""
        return {
            "action": "temp_bump",
            "temp_delta": self.TEMP_BUMP_DELTA,
            "advice": (
                "CRITICAL: You have repeated yourself 3 times. The system is increasing "
                "model temperature to force output diversity. You MUST produce a "
                "substantially different response. Consider: (1) Using a completely "
                "different tool, (2) Explicitly listing what you've already tried and "
                "what you haven't, (3) Asking the user for help."
            ),
        }

    def _condense_strategy(self) -> Dict[str, Any]:
        """Return context condensation strategy."""
        return {
            "action": "condense",
            "advice": (
                "CRITICAL: You have repeated yourself 4 times. The system is condensing "
                "your conversation history to remove repetitive context that may be "
                "causing this loop. After condensation, approach the problem fresh — "
                "review your task from scratch and try an entirely new strategy."
            ),
        }

    def _truncate_strategy(self) -> Dict[str, Any]:
        """Return history truncation strategy."""
        return {
            "action": "truncate",
            "keep_last": self.TRUNCATE_KEEP_LAST,
            "advice": (
                "EMERGENCY: You have repeated yourself 5 times. The system is truncating "
                "your history to only the last 4 messages to break the repetition loop. "
                "This is your final chance before hard stop. You MUST produce a completely "
                "novel response or explicitly report that you are stuck."
            ),
        }

    def _hard_stop_strategy(self) -> Dict[str, Any]:
        """Return hard stop strategy."""
        return {
            "action": "hard_stop",
            "advice": (
                "HARD STOP: You have repeated yourself 6+ times. All progressive recovery "
                "strategies have been exhausted. The agent loop is being terminated. "
                "The system will inform the user that the agent was unable to break out "
                "of a repetition loop."
            ),
        }
