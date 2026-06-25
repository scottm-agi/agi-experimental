"""
Unified Task Hash Utility — Canonical hash for ALL task tracking systems.

Every system that needs to identify a task by content uses this module:
- generate_guid.py (REQ-xxxx GUIDs for orchestrator decomposition)
- delegation_loop_detector.py (loop/failure detection by task hash)
- task_list.py (TodoItem GUID linkage)
- delegation_result.py (tracking fields on handoff envelope)
- call_subordinate.py (metadata injection into subordinate messages)

Design decisions:
- MD5 of normalized text (strip + lowercase) for determinism
- Default 12-char truncation (48 bits, sufficient for task-level dedup)
- REQ- prefix is a presentation layer concern (compute_task_guid only)
- Dynamic delegation context (error relay, progress summaries, task tracking,
  turn budget, boomerang, fidelity warnings) is stripped before hashing so
  semantically-identical re-delegations produce the same hash (RCA-316c).
"""
from __future__ import annotations

import re

# ── Dynamic delegation context patterns (RCA-316c) ──
# These sections are dynamically injected into delegation messages by
# delegation_message.py, delegation_result_processing.py, and
# boomerang_context.py. They change between re-delegations (different
# error text, different attempt numbers, different progress counts) but
# the CORE TASK remains the same. Stripping them ensures the hash
# represents task intent, not accumulated context.
#
# Pattern design: Each regex matches from the section header to either
# the next section header or end-of-string. Compiled once at import time.
_DELEGATION_CONTEXT_PATTERNS = [
    # ── SUFFIX patterns: appended after the core task ──
    # Subordinate Failure Relay (delegation_result_processing.py:57)
    re.compile(r"\n+## ⚠️ Subordinate Failure Relay\n.*", re.DOTALL),
    # Progress Summary (delegation_result_processing.py:26)
    re.compile(r"\n+## Progress Summary\n.*", re.DOTALL),
    # Error Context from Previous Attempt (retry_strategy.py)
    re.compile(r"\n+## Error Context from Previous Attempt\n.*", re.DOTALL),
    # Previous Errors (delegation_loop_detector.py)
    re.compile(r"\n+## Previous Errors\n.*", re.DOTALL),
    # BOOMERANG reminder (boomerang_context.py:159)
    re.compile(r"\n+---\n⚠️ \*\*BOOMERANG —.*", re.DOTALL),
    # Fidelity Warning (delegation_message.py:80)
    re.compile(r"\n⚠️ FIDELITY WARNING — PARENT MANIFEST VIOLATIONS:\n.*", re.DOTALL),
    # E2E Quality Failed routing hint (call_subordinate.py:77)
    re.compile(r"\n+---\n## ⚠️ E2E QUALITY FAILED.*", re.DOTALL),
]

_DELEGATION_CONTEXT_PREFIX_PATTERNS = [
    # ── PREFIX patterns: prepended before the core task ──
    # Task Tracking header (delegation_message.py:256)
    re.compile(
        r"^## Task Tracking\n"
        r"\*\*task_hash\*\*:.*?\n---\n+",
        re.DOTALL,
    ),
    # Turn Budget header (delegation_message.py:280)
    re.compile(r"^## Turn Budget\n.*?\n\n", re.DOTALL),
]


def strip_delegation_context(text: str) -> str:
    """Strip known dynamic delegation context from a task message.

    Removes all sections injected by the delegation pipeline that vary
    between re-delegation attempts while the core task remains the same.
    This ensures that ``compute_task_hash`` produces identical hashes for
    semantically-identical re-delegations, enabling the
    ``DelegationLoopDetector`` to correctly count retries.

    Reuses the pattern from ``same_message_bridge.extract_tool_signature``
    which strips volatile ``thoughts`` from tool calls (RCA-217). This
    function applies the same principle to delegation message text.

    RCA-316c: Without this stripping, each re-delegation with different
    error relay / progress summary / task tracking metadata produced a
    unique hash, defeating the loop detector's threshold (19 cascading
    re-delegations were never caught).

    Args:
        text: The delegation message text (free-form, not JSON).

    Returns:
        The core task text with dynamic sections removed, stripped.
    """
    if not text:
        return ""

    result = text

    # Strip suffix patterns (error relay, progress, boomerang, etc.)
    for pattern in _DELEGATION_CONTEXT_PATTERNS:
        result = pattern.sub("", result)

    # Strip prefix patterns (task tracking, turn budget)
    for pattern in _DELEGATION_CONTEXT_PREFIX_PATTERNS:
        result = pattern.sub("", result)

    return result.strip()


def compute_task_hash(text: str, length: int = 12) -> str:
    """Compute a canonical task hash from text content.

    Normalization pipeline:
    1. Strip dynamic delegation context (error relay, progress, etc.)
    2. Strip leading/trailing whitespace
    3. Collapse internal whitespace (multiple spaces/tabs → single space)
    4. Lowercase

    This ensures:
    - "Build the Frontend" == "build the frontend" (case)
    - "  build the frontend  " == "build the frontend" (trim)
    - "build  the   frontend" == "build the frontend" (collapse)
    - "Build the page" == "Build the page\\n## Progress Summary\\n..."
      (dynamic context stripped — RCA-316c)

    Args:
        text: The task description or content to hash.
        length: Number of hex characters to return (default 12 = 48 bits).

    Returns:
        Hex string of the specified length.
    """
    from python.helpers.hashing import content_hash_short
    stripped = strip_delegation_context(text)
    normalized = re.sub(r"\s+", " ", stripped.strip()).lower()
    return content_hash_short(normalized, length=length)


def compute_task_guid(text: str) -> str:
    """Compute a REQ-prefixed GUID from task text.

    Format: REQ-{first 8 chars of MD5 hash}
    Deterministic, case-insensitive, whitespace-normalized.

    This is the presentation wrapper used by the orchestrator's
    task decomposition system (generate_guid tool).

    Args:
        text: The task/requirement description.

    Returns:
        String in format "REQ-{8 hex chars}" (12 chars total).
    """
    return f"REQ-{compute_task_hash(text, length=8)}"
