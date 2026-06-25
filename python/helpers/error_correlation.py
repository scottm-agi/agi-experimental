"""
Unified Error Identity Tracker (F-15)

Shared data structure for cross-extension error tracking. Enables the
supervisor, structural guards, build error advisor, and build loop hook
to correlate errors across turns and detect when the same error persists.

Also provides redirect history tracking (F-9), per-fingerprint escalation
levels (F-10), and duplicate redirect detection.

Usage:
    tracker = ErrorCorrelationTracker()
    tracker.record_error("fp_abc", error_class="TypeError", turn=5, strategy="null check")
    if tracker.is_same_error("fp_abc"):
        attempts = tracker.get_attempts("fp_abc")
    tracker.mark_resolved("fp_abc")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.error_correlation")

# ─── Escalation Ladder Constants (F-10) ────────────────────────────
# Level 1 (attempt 1): Standard deterministic guidance (current behavior)
# Level 2 (attempt 2): "Previous guidance didn't work. Read FULL error."
# Level 3 (attempt 3): "Two attempts failed. Root cause likely upstream."
# Level 4 (attempt 4+): STOP_AND_DELIVER with evidence
ESCALATION_STOP_LEVEL = 4


class ErrorCorrelationTracker:
    """Cross-extension error identity tracker.

    Tracks error occurrences keyed by fingerprint. Each entry records:
    - error_class: The type/category of error
    - first_seen_turn: The turn when this error was first observed
    - attempts: How many times the error has been seen
    - strategies_tried: List of strategies attempted to fix it
    - resolved: Whether the error has been resolved

    Thread-safe for single-agent use (each agent gets its own tracker).
    """

    def __init__(self) -> None:
        self._errors: Dict[str, Dict[str, Any]] = {}

    def record_error(
        self,
        fingerprint: str,
        error_class: str = "unknown",
        turn: int = 0,
        strategy: str = "",
    ) -> None:
        """Record an error occurrence.

        If this fingerprint is new, creates a new entry.
        If it already exists, increments attempts and adds the strategy.

        Args:
            fingerprint: Stable error fingerprint from _compute_error_fingerprint.
            error_class: The error class/type (e.g., "TypeError", "BuildError").
            turn: The turn number when this error was observed.
            strategy: The strategy/guidance given to fix this error.
        """
        if fingerprint not in self._errors:
            self._errors[fingerprint] = {
                "error_class": error_class,
                "first_seen_turn": turn,
                "attempts": 1,
                "strategies_tried": [strategy] if strategy else [],
                "resolved": False,
            }
            logger.info(
                f"[ERROR CORRELATION] New error recorded: "
                f"fp={fingerprint[:16]}... class={error_class} turn={turn}"
            )
        else:
            entry = self._errors[fingerprint]
            entry["attempts"] += 1
            if strategy and strategy not in entry["strategies_tried"]:
                entry["strategies_tried"].append(strategy)
            logger.info(
                f"[ERROR CORRELATION] Recurring error: "
                f"fp={fingerprint[:16]}... attempts={entry['attempts']}"
            )

    def is_same_error(self, fingerprint: str) -> bool:
        """Check if an error with this fingerprint has been seen before."""
        return fingerprint in self._errors

    def get_attempts(self, fingerprint: str) -> int:
        """Get the number of attempts for an error fingerprint.

        Returns 0 if the fingerprint has never been seen.
        """
        entry = self._errors.get(fingerprint)
        return entry["attempts"] if entry else 0

    def get_entry(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Get the full entry for an error fingerprint, or None if not found."""
        return self._errors.get(fingerprint)

    def mark_resolved(self, fingerprint: str) -> None:
        """Mark an error as resolved."""
        entry = self._errors.get(fingerprint)
        if entry:
            entry["resolved"] = True
            logger.info(
                f"[ERROR CORRELATION] Error resolved: fp={fingerprint[:16]}..."
            )


# ─── Redirect History Functions (F-9) ──────────────────────────────
# These operate on agent.data dicts (not ErrorCorrelationTracker instances)
# because redirect history needs to persist across extension activations
# through the agent's data dict.

_REDIRECT_HISTORY_KEY = "_redirect_history"
_ESCALATION_LEVELS_KEY = "_error_escalation_levels"


def record_redirect(
    agent_data: dict,
    turn: int,
    guidance_summary: str,
    detector: str,
    error_fingerprint: str,
) -> None:
    """Record a supervisor redirect in agent.data.

    Args:
        agent_data: The agent's data dict.
        turn: Current turn number.
        guidance_summary: Summary of the guidance provided.
        detector: Which detector triggered the redirect.
        error_fingerprint: The fingerprint of the error being addressed.
    """
    history: list = agent_data.get(_REDIRECT_HISTORY_KEY, [])
    history.append({
        "turn": turn,
        "guidance_summary": guidance_summary,
        "detector": detector,
        "error_fingerprint": error_fingerprint,
    })
    agent_data[_REDIRECT_HISTORY_KEY] = history
    logger.info(
        f"[REDIRECT HISTORY] Recorded redirect at turn {turn}: "
        f"detector={detector} fp={error_fingerprint[:16]}..."
    )


def get_redirect_history(agent_data: dict) -> List[Dict[str, Any]]:
    """Get the full redirect history from agent.data."""
    return agent_data.get(_REDIRECT_HISTORY_KEY, [])


def is_duplicate_redirect(
    agent_data: dict,
    error_fingerprint: str,
    guidance_summary: str,
) -> bool:
    """Check if we already gave the SAME guidance for the SAME error fingerprint.

    Returns True if the exact combination of fingerprint + guidance was
    already recorded. This prevents repeating the same ineffective advice.
    """
    history = agent_data.get(_REDIRECT_HISTORY_KEY, [])
    for entry in history:
        if (entry.get("error_fingerprint") == error_fingerprint
                and entry.get("guidance_summary") == guidance_summary):
            return True
    return False


# ─── Per-Fingerprint Escalation Levels (F-10) ──────────────────────

def get_escalation_level(agent_data: dict, error_fingerprint: str) -> int:
    """Get the escalation level for a specific error fingerprint.

    Returns 1 (base level) if no escalation has been recorded.
    """
    levels = agent_data.get(_ESCALATION_LEVELS_KEY, {})
    return levels.get(error_fingerprint, 1)


def increment_escalation(agent_data: dict, error_fingerprint: str) -> int:
    """Increment the escalation level for a specific error fingerprint.

    Returns the new escalation level.
    """
    levels = agent_data.get(_ESCALATION_LEVELS_KEY, {})
    current = levels.get(error_fingerprint, 1)
    new_level = current + 1
    levels[error_fingerprint] = new_level
    agent_data[_ESCALATION_LEVELS_KEY] = levels
    logger.info(
        f"[ESCALATION] Incremented fp={error_fingerprint[:16]}... "
        f"to level {new_level}"
    )
    return new_level


def reset_escalation(agent_data: dict, error_fingerprint: str) -> None:
    """Reset the escalation level for a specific error fingerprint.

    Called when the redirect was effective (error fingerprint changed).
    """
    levels = agent_data.get(_ESCALATION_LEVELS_KEY, {})
    if error_fingerprint in levels:
        del levels[error_fingerprint]
        agent_data[_ESCALATION_LEVELS_KEY] = levels
        logger.info(
            f"[ESCALATION] Reset fp={error_fingerprint[:16]}... to level 1"
        )


def should_stop_and_deliver(agent_data: dict, error_fingerprint: str) -> bool:
    """Check if this error fingerprint has reached STOP_AND_DELIVER level.

    Returns True when escalation level >= ESCALATION_STOP_LEVEL (4).
    """
    level = get_escalation_level(agent_data, error_fingerprint)
    return level >= ESCALATION_STOP_LEVEL


def get_escalation_guidance(level: int) -> str:
    """Get the appropriate escalation message for a given level.

    Args:
        level: The escalation level (1-4).

    Returns:
        Guidance text appropriate for the escalation level.
    """
    if level <= 1:
        return (
            "Standard guidance: Follow the deterministic fix instructions "
            "provided. Read the error message carefully and apply the "
            "suggested correction."
        )
    elif level == 2:
        return (
            "Previous guidance didn't work. Read the FULL error output — "
            "not just the first line. Try an ALTERNATIVE approach: "
            "if you've been editing the same file, try a different file. "
            "If you've been adding code, try removing code instead."
        )
    elif level == 3:
        return (
            "Two attempts have failed on this same error. The root cause "
            "is likely UPSTREAM of where you're looking. Read the last 3 "
            "build outputs end-to-end. Check: (1) Are imports correct? "
            "(2) Is the dependency installed? (3) Is there a config issue "
            "(tsconfig.json, next.config.js, package.json)?"
        )
    else:
        return (
            "STOP — this error has persisted through 3+ fix attempts. "
            "You must call the response tool NOW and deliver your current "
            "progress. Include a description of the unresolved error and "
            "what you tried. Do NOT attempt another fix."
        )
