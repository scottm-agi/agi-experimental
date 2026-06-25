"""
Failure Classifier — tag subordinate reports with failure_reason.

Audit report finding: Subordinate reports lack structured failure reasons,
making it hard for orchestrators to determine what went wrong and how to
re-delegate. This module classifies failures from agent message history.
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger("agix.failure_classifier")

# Pattern → reason classification
_CLASSIFICATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"blocked in mode|not available in.*mode|tool not allowed", re.IGNORECASE), "tool_blocked"),
    (re.compile(r"context.*(?:window|limit).*exceeded|token limit|max.*tokens", re.IGNORECASE), "context_overflow"),
    (re.compile(r"same.?message.*(?:loop|detect)|repetitive.*loop|loop.*detect", re.IGNORECASE), "loop_detected"),
    (re.compile(r"timeout|timed?\s*out|deadline exceeded", re.IGNORECASE), "timeout"),
    (re.compile(r"gate.*exhaustion|force.?deliver|force.?allow", re.IGNORECASE), "gate_exhaustion"),
    (re.compile(r"build.*(?:fail|error)|compilation.*(?:fail|error)|type\s*error", re.IGNORECASE), "build_failure"),
    (re.compile(r"cannot|unable|outside my (?:profile|capabilities)", re.IGNORECASE), "capability_mismatch"),
]


def classify_failure_reason(messages: list[str]) -> str:
    """Classify the failure reason from an agent's message history.

    Scans the last N messages for known failure patterns and returns
    the most specific classification.

    Args:
        messages: List of agent message strings (most recent last).

    Returns:
        Classification string: 'tool_blocked', 'context_overflow',
        'loop_detected', 'timeout', 'gate_exhaustion', 'build_failure',
        'capability_mismatch', or 'unknown'.
    """
    # Check last 5 messages for patterns
    recent = messages[-5:] if len(messages) > 5 else messages
    combined = " ".join(recent)

    for pattern, reason in _CLASSIFICATION_PATTERNS:
        if pattern.search(combined):
            logger.info(f"[FAILURE CLASSIFIER] Classified as '{reason}'")
            return reason

    return "unknown"
