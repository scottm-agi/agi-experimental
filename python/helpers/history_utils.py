"""
History utility functions for resilient compression.

RCA-467 Fix:
- deduplicate_messages_middle_out: Collapse consecutive identical messages,
  keep head + tail, note repetition count in lineage annotation.
  User insight: "what value does repetition have to history? note it in
  lineage but not keep it. Use middle-out (keep N chars of tail & head)."
- fallback_truncate_messages: When LLM summarization fails, brute-force
  truncate by keeping head + tail messages, dropping middle with annotation.
"""

import logging
from typing import List

logger = logging.getLogger("agix.history_utils")

# Minimum consecutive duplicates to trigger collapse
MIN_COLLAPSE_THRESHOLD = 3


def deduplicate_messages_middle_out(
    messages: List[str],
    collapse_threshold: int = MIN_COLLAPSE_THRESHOLD,
) -> List[str]:
    """Collapse consecutive identical messages using middle-out strategy.

    Keeps the first occurrence, replaces the repeated middle with a lineage
    annotation noting the count, then continues with the next unique message.

    Args:
        messages: List of message text strings.
        collapse_threshold: Minimum consecutive duplicates to trigger collapse.
            Default 3 (2 identical = kept as-is, 3+ = collapsed).

    Returns:
        Deduplicated list with repetition annotations.

    Example:
        >>> deduplicate_messages_middle_out(["A", "A", "A", "A", "B"])
        ["A", "[⟳ Previous message repeated 3 more times — omitted from history]", "B"]
    """
    if not messages:
        return []

    result: List[str] = []
    i = 0

    while i < len(messages):
        current = messages[i]
        # Count consecutive identical messages starting from i
        run_length = 1
        while i + run_length < len(messages) and messages[i + run_length] == current:
            run_length += 1

        if run_length >= collapse_threshold:
            # Keep the first occurrence (head)
            result.append(current)
            # Add lineage annotation noting how many were omitted
            omitted = run_length - 1
            result.append(
                f"[⟳ Previous message repeated {omitted} more times — omitted from history]"
            )
            i += run_length
        else:
            # Below threshold — keep all
            for j in range(run_length):
                result.append(messages[i + j])
            i += run_length

    return result


# How many head/tail messages to keep during fallback truncation
FALLBACK_HEAD_COUNT = 3
FALLBACK_TAIL_COUNT = 3
# Minimum messages to trigger fallback (shorter lists pass through)
FALLBACK_MIN_MESSAGES = 8


def fallback_truncate_messages(
    messages: List[str],
    head_count: int = FALLBACK_HEAD_COUNT,
    tail_count: int = FALLBACK_TAIL_COUNT,
    min_messages: int = FALLBACK_MIN_MESSAGES,
) -> List[str]:
    """Brute-force truncation fallback when LLM summarization fails.

    Keeps the first `head_count` and last `tail_count` messages, drops the
    middle with an annotation. This guarantees token reduction even when
    the LLM path fails completely.

    Args:
        messages: List of message text strings.
        head_count: Number of messages to keep from the start.
        tail_count: Number of messages to keep from the end.
        min_messages: Lists shorter than this pass through unchanged.

    Returns:
        Truncated list with middle replaced by annotation.
    """
    if len(messages) <= min_messages:
        return list(messages)

    head = messages[:head_count]
    tail = messages[-tail_count:]
    omitted = len(messages) - head_count - tail_count

    annotation = (
        f"[… {omitted} messages omitted (truncated due to summarization failure) …]"
    )

    return head + [annotation] + tail
