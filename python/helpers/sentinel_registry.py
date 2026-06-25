"""
Sentinel Registry — Single Source of Truth for All Sentinel Tags.

RCA-U13: Each downstream consumer (call_subordinate, delegation_result_processing,
boomerang_context) maintained its own hardcoded list of sentinel tags. When
[FORCE_ACCEPTED_INCOMPLETE] was added to response.py, none of the 4 consumers
were updated, causing force-accepted incomplete work to be classified as SUCCESS.

This module centralizes all sentinel tags so adding a new sentinel automatically
propagates to all consumers.

Architecture:
    response.py (generator) → sentinel_registry.py (source of truth) ← all consumers
"""
from __future__ import annotations

from typing import Dict, List, Tuple


# ─── SENTINEL REGISTRY ──────────────────────────────────────────────────
# Each sentinel has:
#   tag:           The exact string tag (e.g., "[CANCELLED]")
#   status:        How to classify delegation results with this tag
#                  ("partial", "failed", "cancelled")
#   is_error:      Whether is_error_result() should return True
#   diagnostic:    (short_label, detail_message) for _classify_failure_type
#   description:   Human-readable explanation
# ─────────────────────────────────────────────────────────────────────────

SENTINEL_REGISTRY: Dict[str, dict] = {
    "[ITERATION_LIMIT]": {
        "status": "partial",
        "is_error": True,
        "diagnostic": (
            "hit its iteration limit",
            "ran out of iterations before completing. Scope-reduce the task or "
            "break it into smaller subtasks.",
        ),
        "description": "Agent exceeded maximum message loop iterations.",
    },
    "[CHAIN_LIMIT]": {
        "status": "partial",
        "is_error": True,
        "diagnostic": (
            "hit the chain depth limit",
            "exceeded the subordinate nesting depth. Flatten the delegation "
            "hierarchy or complete in fewer steps.",
        ),
        "description": "Subordinate chain nesting depth exceeded.",
    },
    "[RESTART_LIMIT]": {
        "status": "partial",
        "is_error": True,
        "diagnostic": (
            "hit the restart limit",
            "was restarted too many times without completing. The task may need "
            "a fundamentally different approach.",
        ),
        "description": "Agent was restarted too many times.",
    },
    "[HARD_STOP]": {
        "status": "failed",
        "is_error": True,
        "diagnostic": (
            "was hard-stopped",
            "was stuck in a loop and forcibly terminated. Completely rethink "
            "the approach before re-attempting.",
        ),
        "description": "Agent was hard-stopped by the loop limiter.",
    },
    "[ESCAPE_HATCH]": {
        "status": "partial",
        "is_error": True,
        "diagnostic": (
            "was redirected by the supervisor",
            "was rerouted due to persistent issues. Consider a different "
            "delegation strategy.",
        ),
        "description": "Agent was redirected by the intelligent supervisor.",
    },
    "[CANCELLED]": {
        "status": "cancelled",
        "is_error": True,
        "diagnostic": (
            "was cancelled",
            "was cancelled before completing. The work may be partially done.",
        ),
        "description": "Agent was cancelled externally.",
    },
    "[FORCE_ACCEPTED_INCOMPLETE]": {
        "status": "partial",
        "is_error": True,
        "diagnostic": (
            "was force-accepted with incomplete work",
            "hit the gate rejection cap and was force-accepted. The deliverable "
            "is INCOMPLETE — scope-reduce or delegate missing parts separately.",
        ),
        "description": "Agent hit gate rejection cap; work force-accepted as incomplete.",
    },
    "[RESPONSE_REJECTED]": {
        "status": "partial",
        "is_error": False,  # Not an error per se, just a rejected attempt
        "diagnostic": (
            "had its response rejected",
            "response was rejected by the quality gate. Review the rejection "
            "reason and address the specific issues.",
        ),
        "description": "Agent's response was rejected by the quality gate.",
    },
    "LOOP BLOCKED": {
        "status": "failed",
        "is_error": True,
        "diagnostic": (
            "was blocked by the loop limiter",
            "repeatedly called tools with identical arguments without making progress.",
        ),
        "description": "Agent hit the loop limiter for repeating identical tool calls.",
    },
    "HARD BLOCK": {
        "status": "failed",
        "is_error": True,
        "diagnostic": (
            "was hard-blocked by the loop limiter",
            "repeatedly hit the exact same error or made no progress with identical arguments.",
        ),
        "description": "Agent hit a hard block for repeating identical tool calls.",
    },
    "TOOL CALL BLOCKED": {
        "status": "failed",
        "is_error": True,
        "diagnostic": (
            "was blocked for duplicate tool calls",
            "repeatedly called a tool with arguments that already succeeded.",
        ),
        "description": "Agent hit the duplicate success block.",
    },
    "TOOL LOOP DETECTED": {
        "status": "failed",
        "is_error": True,
        "diagnostic": (
            "was blocked for an interleaved tool loop",
            "called a tool with identical arguments too many times in one conversation.",
        ),
        "description": "Agent hit the interleaved loop block.",
    },
}


def get_limit_tags() -> List[str]:
    """Return all sentinel tags that should trigger handle_limit_tags().

    Used by call_subordinate.py and delegation_result_processing.py
    to detect sentinel-tagged results.
    """
    return list(SENTINEL_REGISTRY.keys())


def get_error_sentinels() -> List[str]:
    """Return sentinel strings for is_error_result() detection.

    Used by boomerang_context.py to determine if a delegation result
    represents an error/failure.
    """
    return [
        tag for tag, meta in SENTINEL_REGISTRY.items()
        if meta.get("is_error", False)
    ]


def get_tag_diagnostics() -> Dict[str, Tuple[str, str]]:
    """Return tag → (short_label, detail_message) mapping.

    Used by delegation_result_processing.py _classify_failure_type()
    to produce failure-specific diagnostic messages.
    """
    return {
        tag: meta["diagnostic"]
        for tag, meta in SENTINEL_REGISTRY.items()
        if "diagnostic" in meta
    }


def get_sentinel_status(tag: str) -> str:
    """Return the delegation result status for a given sentinel tag.

    Returns "partial" as default for unknown sentinels (safe fallback).
    """
    meta = SENTINEL_REGISTRY.get(tag, {})
    return meta.get("status", "partial")
