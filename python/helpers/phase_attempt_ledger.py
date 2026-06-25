"""
Phase Attempt Ledger — tracks per-phase delegation attempt results.

Stores structured records of what each delegation attempt produced:
files created, stubs remaining, build errors, test failures, and status.
Used by the remediation brief builder to construct scoped fix delegations.

Storage: agent_data["_phase_attempts"][phase_seq]["attempts"]

Part of the Incremental Fix Re-Delegation Architecture (T1).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum number of attempt records to keep per phase.
# Prevents unbounded growth in agent.data. Oldest attempts are evicted.
MAX_ATTEMPTS = 10


def record_attempt(agent_data: dict, phase_seq: str, attempt: dict) -> None:
    """Record a delegation attempt for a phase.

    Args:
        agent_data: The agent's data dictionary (mutable).
        phase_seq: Phase sequence identifier (e.g. "3.1").
        attempt: Attempt record dict with keys:
            - delegation_id: str
            - timestamp: str (ISO format)
            - profile: str
            - files_created: list[str]
            - files_modified: list[str]
            - stubs_remaining: list[dict]  ({file, line, content})
            - build_errors: list[str]
            - test_failures: list[dict]  ({test_file, assertion, error})
            - status: str  ("complete" | "partial" | "failed")
    """
    if "_phase_attempts" not in agent_data:
        agent_data["_phase_attempts"] = {}

    phase_data = agent_data["_phase_attempts"]

    if phase_seq not in phase_data:
        phase_data[phase_seq] = {
            "attempts": [],
            "total_attempts": 0,
        }

    entry = phase_data[phase_seq]
    entry["total_attempts"] += 1
    entry["attempts"].append(attempt)

    # Cap the number of stored attempts to prevent unbounded growth.
    # Keep the most recent MAX_ATTEMPTS entries.
    if len(entry["attempts"]) > MAX_ATTEMPTS:
        entry["attempts"] = entry["attempts"][-MAX_ATTEMPTS:]

    logger.info(
        "[PHASE ATTEMPT LEDGER] Recorded attempt #%d for phase %s "
        "(status=%s, files_created=%d, stubs=%d, build_errors=%d, test_failures=%d)",
        entry["total_attempts"],
        phase_seq,
        attempt.get("status", "unknown"),
        len(attempt.get("files_created", [])),
        len(attempt.get("stubs_remaining", [])),
        len(attempt.get("build_errors", [])),
        len(attempt.get("test_failures", [])),
    )


def get_attempt_history(agent_data: dict, phase_seq: str) -> Optional[dict]:
    """Get all attempts for a phase.

    Args:
        agent_data: The agent's data dictionary.
        phase_seq: Phase sequence identifier (e.g. "3.1").

    Returns:
        Dict with "attempts" list and "total_attempts" count,
        or None if no attempts have been recorded for this phase.
    """
    phase_data = agent_data.get("_phase_attempts", {})
    entry = phase_data.get(phase_seq)
    if entry is None:
        return None
    return entry


def get_unresolved_issues(agent_data: dict, phase_seq: str) -> List[dict]:
    """Get unresolved stubs, build errors, and test failures from the latest attempt.

    Returns a flat list of issue dicts, each with a "type" key indicating
    the category (stub, build_error, test_failure) and the original data.

    Args:
        agent_data: The agent's data dictionary.
        phase_seq: Phase sequence identifier (e.g. "3.1").

    Returns:
        List of issue dicts. Empty list if no history or no issues.
    """
    history = get_attempt_history(agent_data, phase_seq)
    if history is None or not history.get("attempts"):
        return []

    latest = history["attempts"][-1]
    issues: List[dict] = []

    # Stubs
    for stub in latest.get("stubs_remaining", []):
        issues.append({
            "type": "stub",
            "file": stub.get("file", ""),
            "line": stub.get("line", 0),
            "content": stub.get("content", ""),
        })

    # Build errors
    for error in latest.get("build_errors", []):
        issues.append({
            "type": "build_error",
            "error": error if isinstance(error, str) else str(error),
        })

    # Test failures
    for failure in latest.get("test_failures", []):
        issues.append({
            "type": "test_failure",
            "test_file": failure.get("test_file", ""),
            "assertion": failure.get("assertion", ""),
            "error": failure.get("error", ""),
        })

    return issues


def get_completed_files(agent_data: dict, phase_seq: str) -> List[str]:
    """Get files successfully created across all attempts (deduplicated).

    Accumulates files_created from every attempt to build a complete picture
    of what has been produced for this phase.

    Args:
        agent_data: The agent's data dictionary.
        phase_seq: Phase sequence identifier (e.g. "3.1").

    Returns:
        Deduplicated list of file paths. Empty list if no history.
    """
    history = get_attempt_history(agent_data, phase_seq)
    if history is None or not history.get("attempts"):
        return []

    seen = set()
    result = []
    for attempt in history["attempts"]:
        for f in attempt.get("files_created", []):
            if f not in seen:
                seen.add(f)
                result.append(f)

    return result
