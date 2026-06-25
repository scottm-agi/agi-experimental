"""
OUTBOUND COMMENT DEDUPE — POST-EXECUTION CONFIRMATION

Companion to _10_outbound_comment_dedupe.py (tool_execute_before hook).

The before-hook tags the comment body and marks the hash as "pending".
This after-hook checks the tool response:
  - If SUCCESS → confirms the pending hash (moves to cache, blocks future dupes)
  - If FAILURE → cancels the pending hash (allows retry on next attempt)

This prevents the silent failure where the circuit breaker blocks the MCP call
but the agent thinks the comment was posted because the dedupe cache already
has the entry.

Root Cause: Issue #704 / Build #848 investigation.
"""

from __future__ import annotations
import logging
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("extensions.outbound_dedupe_confirm")


class OutboundCommentDedupeConfirm(Extension):
    """
    Post-execution hook to confirm or cancel pending dedupe hashes.

    After a comment tool call completes:
    - Success → confirm the pending hash so future dupes are blocked
    - Failure → cancel the pending hash so the agent can retry
    """

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        # Only process tools that the before-hook intercepts
        normalized = tool_name.strip().lower()

        # Import from the before-hook module (single source of truth)
        try:
            from python.extensions.tool_execute_before._10_outbound_comment_dedupe import (
                COMMENT_TOOLS,
                _pending_hashes,
                _confirm_pending,
                _cancel_pending,
                _cache_key,
                _generate_hash,
            )
        except ImportError:
            logger.debug("[OutboundDedupeConfirm] Could not import from before-hook — skipping")
            return

        tool_config = COMMENT_TOOLS.get(normalized)
        if not tool_config:
            return

        # Determine success from response
        success = True
        if hasattr(response, "additional") and isinstance(response.additional, dict):
            if "success" in response.additional:
                success = bool(response.additional["success"])
            elif "is_error" in response.additional:
                success = not bool(response.additional["is_error"])

        # Also check response message for circuit breaker errors
        if hasattr(response, "message") and response.message:
            msg_lower = response.message.lower()
            if "circuit breaker" in msg_lower and "open" in msg_lower:
                success = False
            elif msg_lower.startswith("error:") or msg_lower.startswith("mcp tool exception:"):
                success = False

        # Find and process any pending hashes
        # We need to reconstruct the cache key from the tool args
        # but we don't have tool_args in tool_execute_after.
        # Instead, we process ALL pending hashes for this tool type.
        # This is safe because pending hashes have a TTL.
        import time
        pending_keys = list(_pending_hashes.keys())

        if not pending_keys:
            return

        if success:
            for key in pending_keys:
                _confirm_pending(key)
                logger.info(f"[OutboundDedupeConfirm] CONFIRMED hash {key} (MCP call succeeded)")
        else:
            for key in pending_keys:
                _cancel_pending(key)
                logger.warning(
                    f"[OutboundDedupeConfirm] CANCELLED hash {key} "
                    f"(MCP call failed — comment NOT posted, retry allowed)"
                )
