"""
Wrong-Profile Detector — detect capability mismatch loops.

Iteration file T+78: When an agent repeatedly says "I can't do this" or
"outside my profile", it indicates the orchestrator assigned the wrong
profile. This module detects the pattern and suggests re-delegation.
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger("agix.wrong_profile_detector")

# Patterns indicating capability mismatch
_MISMATCH_PATTERNS = [
    re.compile(r"I\s+cannot\b.*(?:execute|perform|write|code|build)", re.IGNORECASE),
    re.compile(r"outside\s+my\s+(?:profile|capabilities|scope)", re.IGNORECASE),
    re.compile(r"I(?:'m|\s+am)\s+unable\s+to\b", re.IGNORECASE),
    re.compile(r"this\s+(?:task\s+)?requires.*(?:code|execution|tools?\s+I\s+don)", re.IGNORECASE),
    re.compile(r"not\s+(?:allowed|permitted|authorized)\s+to\b", re.IGNORECASE),
    re.compile(r"blocked\s+in\s+(?:my\s+)?(?:current\s+)?mode", re.IGNORECASE),
]

# Minimum number of mismatch signals before triggering
_MISMATCH_THRESHOLD = 2


def detect_wrong_profile(messages: list[str]) -> dict:
    """Detect if an agent is stuck due to wrong profile assignment.

    Scans messages for capability-mismatch patterns. If ≥ threshold
    matches are found, flags the agent as wrong-profile.

    Args:
        messages: List of agent message strings.

    Returns:
        Dict with:
          - is_wrong_profile: bool
          - signal_count: int (number of mismatch signals)
          - remediation: str (suggestion for re-delegation)
    """
    signal_count = 0

    for msg in messages:
        for pattern in _MISMATCH_PATTERNS:
            if pattern.search(msg):
                signal_count += 1
                break  # Count each message only once

    is_wrong = signal_count >= _MISMATCH_THRESHOLD

    result = {
        "is_wrong_profile": is_wrong,
        "signal_count": signal_count,
    }

    if is_wrong:
        result["remediation"] = (
            f"🔄 WRONG PROFILE DETECTED ({signal_count} capability-mismatch signals)\n\n"
            f"This agent cannot perform the assigned task in its current profile.\n\n"
            f"**Action**: Re-delegate this task with the correct profile:\n"
            f"- For code/build tasks → profile='code'\n"
            f"- For research/analysis → profile='researcher'\n"
            f"- For architecture/planning → profile='architect'\n"
        )
        logger.warning(
            f"[WRONG PROFILE] Detected {signal_count} capability-mismatch signals "
            f"— recommending re-delegation"
        )

    return result
