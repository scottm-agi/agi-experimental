"""Compression Progress Tracker — Resume-on-restart for history compression.

RCA-475 Fix P3: When history compression times out due to rate limiting,
this module tracks progress so compression resumes where it left off
on the next activation instead of restarting from scratch.

Stores progress in agent.data['_compression_progress'] (in-memory).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("agix.compression_progress")

_KEY = "_compression_progress"


def record_compression_progress(
    agent_data: dict,
    summarized: int,
    total: int,
    timed_out: bool = False,
) -> None:
    """Record compression progress for resume-on-restart.

    Args:
        agent_data: The agent's data dict.
        summarized: Number of topics successfully summarized so far.
        total: Total number of topics that needed summarization.
        timed_out: Whether this batch timed out (for cooldown logic).
    """
    agent_data[_KEY] = {
        "summarized": summarized,
        "total": total,
        "timed_out": timed_out,
        "timestamp": time.time(),
    }
    if timed_out:
        logger.info(
            "[COMPRESSION] Timed out after %d/%d topics — will resume on next activation",
            summarized, total,
        )


def get_compression_progress(agent_data: dict) -> Optional[Dict[str, Any]]:
    """Get current compression progress, or None if no progress recorded."""
    return agent_data.get(_KEY)


def clear_compression_progress(agent_data: dict) -> None:
    """Clear compression progress (call when all topics are summarized)."""
    agent_data.pop(_KEY, None)


def should_defer_compression(
    agent_data: dict,
    cooldown_seconds: float = 30.0,
) -> bool:
    """Check if compression should be deferred due to recent timeout.

    If the last compression attempt timed out less than cooldown_seconds ago,
    defer to avoid hammering a rate-limited API.

    Args:
        agent_data: The agent's data dict.
        cooldown_seconds: Minimum seconds between compression attempts after timeout.

    Returns:
        True if compression should be deferred.
    """
    progress = get_compression_progress(agent_data)
    if progress is None:
        return False
    if not progress.get("timed_out", False):
        return False
    elapsed = time.time() - progress.get("timestamp", 0)
    return elapsed < cooldown_seconds
