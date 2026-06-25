"""
Nudge Tracker & Burst Limiter — P1-3 Supervisor Denoising Phase 2.

Provides:
- NudgeRecord: Dataclass for recording supervisor nudge events.
- BurstLimiter: Sliding window rate limiter to prevent nudge storms.
- evaluate_nudge_effectiveness: Determines if a past nudge changed agent behavior.

Design:
- BurstLimiter uses a sliding window of WINDOW_SIZE_TURNS turns.
- After MAX_NUDGES_PER_WINDOW nudges in the window, new nudges are blocked.
- Ineffective nudges trigger a COOLDOWN_AFTER_INEFFECTIVE turn cooldown.
- nudge_turns history is bounded to 20 entries max to avoid memory growth.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.nudge_tracker")

# ─── CONFIGURATION CONSTANTS ────────────────────────────────────────
# Max nudges allowed per sliding window.
MAX_NUDGES_PER_WINDOW: int = 3

# Sliding window size in agent turns.
WINDOW_SIZE_TURNS: int = 10

# Cooldown period (in turns) after an ineffective nudge.
COOLDOWN_AFTER_INEFFECTIVE: int = 5

# Turns to wait before evaluating a nudge's effectiveness.
_EFFECTIVENESS_EVAL_DELAY: int = 3

# Maximum nudge turn history size (prevents unbounded growth).
_MAX_HISTORY_SIZE: int = 20


@dataclass
class NudgeRecord:
    """Record of a supervisor nudge event."""
    detector: str
    nudge_text: str
    timestamp: float
    agent_turn_at_nudge: int
    was_effective: bool = False


class BurstLimiter:
    """Sliding-window rate limiter for supervisor nudges.

    Prevents nudge storms by enforcing:
    1. At most MAX_NUDGES_PER_WINDOW nudges in any WINDOW_SIZE_TURNS window.
    2. A COOLDOWN_AFTER_INEFFECTIVE turn cooldown after any ineffective nudge.
    """

    def __init__(self) -> None:
        self.nudge_turns: List[int] = []
        self._cooldown_until: float = float('-inf')  # Turn at which cooldown expires

    def can_nudge(self, current_turn: int) -> bool:
        """Check if a new nudge is allowed at the given turn.

        Args:
            current_turn: The agent's current turn counter.

        Returns:
            True if a nudge is allowed, False if rate-limited or in cooldown.
        """
        # Check cooldown first
        if current_turn <= self._cooldown_until:
            return False

        # Count nudges within the sliding window
        window_start = current_turn - WINDOW_SIZE_TURNS
        recent = [t for t in self.nudge_turns if t > window_start]
        return len(recent) < MAX_NUDGES_PER_WINDOW

    def record_nudge(self, turn: int, was_effective: bool = True) -> None:
        """Record that a nudge was delivered at the given turn.

        Args:
            turn: The agent turn when the nudge was delivered.
            was_effective: Whether the nudge was evaluated as effective.
        """
        self.nudge_turns.append(turn)

        # Enforce bounded history
        if len(self.nudge_turns) > _MAX_HISTORY_SIZE:
            self.nudge_turns = self.nudge_turns[-_MAX_HISTORY_SIZE:]

        # Ineffective nudges trigger cooldown
        if not was_effective:
            self._cooldown_until = turn + COOLDOWN_AFTER_INEFFECTIVE


def evaluate_nudge_effectiveness(
    record: NudgeRecord,
    current_turn: int,
    agent_data: Dict[str, Any],
) -> bool:
    """Evaluate whether a past nudge was effective.

    A nudge is considered effective if:
    - It's too early to tell (< _EFFECTIVENESS_EVAL_DELAY turns) — assume effective.
    - The detector that triggered the nudge is no longer firing.

    Args:
        record: The NudgeRecord to evaluate.
        current_turn: The agent's current turn counter.
        agent_data: The agent's data dict (for checking active signals).

    Returns:
        True if the nudge was effective or too early to tell.
        False if the same detector is still firing after the delay.
    """
    turns_since = current_turn - record.agent_turn_at_nudge

    # Too early to tell — give the nudge a chance to work
    if turns_since < _EFFECTIVENESS_EVAL_DELAY:
        return True

    # Check if the same detector is still producing signals
    active_signals = agent_data.get("_l2_escalation_signals", [])
    for signal in active_signals:
        if isinstance(signal, dict) and signal.get("detector") == record.detector:
            return False  # Detector still firing — nudge was ineffective

    return True  # Detector stopped firing — nudge worked
