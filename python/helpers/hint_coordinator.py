"""
Hint Coordinator — P1-4 Systems Audit Fix

Coordinates all hist_add_warning() calls across 30+ files (50+ call sites).
Prevents hint cascade where a single turn injects 10+ warning messages.

Architecture:
- All callers continue using agent.hist_add_warning() — no API change
- This coordinator is consulted INSIDE hist_add_warning_impl() before injection
- Caps at MAX_HINTS_PER_TURN (3) per agent per turn
- Deduplicates identical/similar hints within a turn
- CRITICAL hints bypass the cap (user stop, budget expiry, etc.)
- Counter resets at start of each message loop iteration

Root Cause (Systems Audit H-17):
    50+ hist_add_warning() call sites across 30+ files with ZERO coordination.
    A single turn could inject 10+ warning messages, each triggering different
    agent behavior, creating a cascade of contradictory instructions.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from enum import IntEnum
from typing import Dict, Set

logger = logging.getLogger("agix.hint_coordinator")


class HintPriority(IntEnum):
    """Hint priority levels. Higher values = higher priority."""
    LOW = 1       # Advisory, style suggestions
    MEDIUM = 2    # Warnings, redirects
    HIGH = 3      # Error corrections, tool blocks
    CRITICAL = 4  # Budget expiry, user stop, system safety


# Maximum non-critical hints per agent per turn
MAX_HINTS_PER_TURN = 3


class HintCoordinator:
    """Coordinates hint injection to prevent cascade storms.

    Usage:
        coordinator = get_hint_coordinator()
        if coordinator.should_deliver(agent_id, hint_text, priority):
            # inject the hint
        else:
            # hint suppressed — logged but not injected
    """

    def __init__(self):
        # Per-agent state: {agent_id: count_of_delivered_hints}
        self._delivered_count: Dict[str, int] = defaultdict(int)
        # Per-agent dedup set: {agent_id: set_of_hint_fingerprints}
        self._seen_fingerprints: Dict[str, Set[str]] = defaultdict(set)
        # Per-agent suppressed count
        self._suppressed_count: Dict[str, int] = defaultdict(int)

    def should_deliver(self, agent_id: str, hint_text: str, priority: HintPriority) -> bool:
        """Check if a hint should be delivered to the agent.

        Args:
            agent_id: Unique identifier for the agent (e.g., agent_name or id)
            hint_text: The hint message content
            priority: Priority level of the hint

        Returns:
            True if the hint should be delivered, False if suppressed
        """
        # CRITICAL hints always get through
        if priority >= HintPriority.CRITICAL:
            fingerprint = self._fingerprint(hint_text)
            # Still dedup even critical hints
            if fingerprint in self._seen_fingerprints[agent_id]:
                logger.debug(f"[HINT_COORD] Dedup CRITICAL hint for {agent_id}")
                return False
            self._seen_fingerprints[agent_id].add(fingerprint)
            self._delivered_count[agent_id] += 1
            return True

        # Check dedup first (doesn't count toward cap)
        fingerprint = self._fingerprint(hint_text)
        if fingerprint in self._seen_fingerprints[agent_id]:
            logger.debug(f"[HINT_COORD] Dedup hint for {agent_id}: {hint_text[:50]}...")
            return False

        # Also check if new hint is a substring/superset of existing hints
        for existing_fp in self._seen_fingerprints[agent_id]:
            if fingerprint in existing_fp or existing_fp in fingerprint:
                logger.debug(f"[HINT_COORD] Similar hint dedup for {agent_id}")
                return False

        # Check cap
        if self._delivered_count[agent_id] >= MAX_HINTS_PER_TURN:
            self._suppressed_count[agent_id] += 1
            logger.info(
                f"[HINT_COORD] Suppressed hint #{self._suppressed_count[agent_id]} "
                f"for {agent_id} (cap={MAX_HINTS_PER_TURN}): {hint_text[:80]}..."
            )
            return False

        # Deliver
        self._seen_fingerprints[agent_id].add(fingerprint)
        self._delivered_count[agent_id] += 1
        return True

    def reset_turn(self, agent_id: str) -> None:
        """Reset hint state for a new turn/iteration.

        Call this at the start of each message_loop iteration.
        """
        suppressed = self._suppressed_count.get(agent_id, 0)
        if suppressed > 0:
            logger.info(
                f"[HINT_COORD] Turn reset for {agent_id} — "
                f"{suppressed} hints were suppressed last turn"
            )
        self._delivered_count[agent_id] = 0
        self._seen_fingerprints[agent_id] = set()
        self._suppressed_count[agent_id] = 0

    def get_suppressed_count(self, agent_id: str) -> int:
        """Return how many hints were suppressed this turn."""
        return self._suppressed_count.get(agent_id, 0)

    @staticmethod
    def _fingerprint(text: str) -> str:
        """Create a dedup fingerprint from hint text.

        Normalizes whitespace and lowercases for fuzzy matching.
        """
        return " ".join(text.lower().split())


# Module-level singleton
_coordinator: HintCoordinator | None = None


def get_hint_coordinator() -> HintCoordinator:
    """Get the global HintCoordinator singleton."""
    global _coordinator
    if _coordinator is None:
        _coordinator = HintCoordinator()
    return _coordinator
