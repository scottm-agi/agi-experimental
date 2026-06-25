"""
File-Read Dedup Tracker — Session-Scoped File Read Memory

Issue: Cross-reference audit Gap #5 (P2)

Tracks which files an agent has read during the current session and
generates deduplication hints when the same file is re-read. This
prevents agents from wasting tool calls re-reading files that fell
out of the LLM context window.

Architecture:
    - State stored on agent.data["_files_read_tracker"]
    - record_file_read(): Called after each file read
    - get_read_count(): Returns how many times a file was read
    - build_dedup_hint(): Returns None (first read) or a dedup hint string

Hint escalation:
    - 1st read: No hint (silent tracking)
    - 2nd read: Gentle hint ("You already read this file at iteration N")
    - 3rd+ read: Strong nudge ("Save important parts to a scratch file")
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("agix.file_read_tracker")

# Key in agent.data where tracking state is stored
_TRACKER_KEY = "_files_read_tracker"


def _ensure_tracker(agent_data: dict) -> Dict[str, Any]:
    """Ensure the tracker dict exists in agent_data and return it."""
    if _TRACKER_KEY not in agent_data:
        agent_data[_TRACKER_KEY] = {}
    return agent_data[_TRACKER_KEY]


def record_file_read(agent_data: dict, filepath: str, iteration: int) -> None:
    """Record that a file was read at this iteration.

    Args:
        agent_data: The agent's data dict (agent.data).
        filepath: Absolute or relative path to the file that was read.
        iteration: The current monologue loop iteration number.
    """
    tracker = _ensure_tracker(agent_data)

    if filepath not in tracker:
        tracker[filepath] = {
            "read_count": 1,
            "first_read_iteration": iteration,
            "last_read_iteration": iteration,
        }
    else:
        tracker[filepath]["read_count"] += 1
        tracker[filepath]["last_read_iteration"] = iteration

    logger.debug(
        f"File read tracked: {filepath} "
        f"(count={tracker[filepath]['read_count']}, iteration={iteration})"
    )


def get_read_count(agent_data: dict, filepath: str) -> int:
    """Return how many times a file has been read this session.

    Args:
        agent_data: The agent's data dict.
        filepath: Path to check.

    Returns:
        Number of times the file has been recorded as read (0 if never).
    """
    tracker = _ensure_tracker(agent_data)
    entry = tracker.get(filepath)
    if entry is None:
        return 0
    return entry["read_count"]


def build_dedup_hint(
    agent_data: dict, filepath: str, iteration: int,
) -> Optional[str]:
    """Build a dedup hint if the file has been read before.

    Args:
        agent_data: The agent's data dict.
        filepath: Path to the file about to be read.
        iteration: Current monologue loop iteration.

    Returns:
        None if this is the first read (no hint needed).
        A hint string if the file was previously read.
    """
    tracker = _ensure_tracker(agent_data)
    entry = tracker.get(filepath)

    if entry is None:
        # First read — no hint
        return None

    read_count = entry["read_count"]
    last_iter = entry["last_read_iteration"]
    basename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath

    if read_count >= 2:
        # Strong nudge (3rd+ read)
        return (
            f"⚠️ You have already read `{basename}` {read_count} times "
            f"(last at iteration {last_iter}). Consider saving the important "
            f"parts to a scratch file or variable to avoid re-reading. "
            f"If you need to re-read specific sections, use line ranges "
            f"instead of reading the entire file."
        )
    else:
        # Gentle hint (2nd read)
        return (
            f"ℹ️ You already read `{basename}` at iteration {last_iter}. "
            f"Re-reading now. If this file's content is important for your "
            f"ongoing work, consider noting the key details to avoid "
            f"needing to read it again."
        )


def build_recovery_read_context(agent_data: dict, max_files: int = 8) -> str:
    """Build a file-read context summary for truncation recovery hints.

    When a response is truncated and the agent needs to recover, this
    function produces a compact list of files the agent already read
    so it doesn't waste tool calls re-reading them.

    Args:
        agent_data: The agent's data dict.
        max_files: Maximum number of files to include (default 8).

    Returns:
        Empty string if no files have been read.
        A formatted context string if files have been read.
    """
    tracker = agent_data.get(_TRACKER_KEY, {})
    if not tracker:
        return ""

    # Sort by last_read_iteration descending (most recent first)
    sorted_files = sorted(
        tracker.items(),
        key=lambda x: x[1].get("last_read_iteration", 0),
        reverse=True,
    )

    # Take top N files
    limited = sorted_files[:max_files]

    # Build compact list with basenames
    lines = []
    for filepath, info in limited:
        basename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
        read_count = info.get("read_count", 1)
        last_iter = info.get("last_read_iteration", 0)
        lines.append(f"  - `{basename}` (read {read_count}x, last at iter {last_iter})")

    overflow = len(sorted_files) - max_files
    if overflow > 0:
        lines.append(f"  - ... and {overflow} more files")

    return (
        "\n\nFiles already read this session (DO NOT re-read unless modified):\n"
        + "\n".join(lines)
    )

