"""Async execution marker files for crash-safe state tracking.

When a context transitions to "executing", a tiny marker file is written
(fire-and-forget) to the markers directory. When it transitions back to
"idle", the marker is deleted. On restart, any remaining markers indicate
contexts that were interrupted by a crash.

This is faster and more reliable than depending on the periodic
save_tmp_chat timer to persist execution_state to chat.json.

Usage:
    # In context.py communicate():
    write_execution_marker(self.id)        # async fire-and-forget
    
    # In context.py _process_chain finally:
    clear_execution_marker(self.id)        # sync cleanup
    
    # In crash_recovery.py _post_restart_nudge():
    interrupted = get_interrupted_contexts()
"""

import os
import time
import logging
import asyncio
from typing import List

logger = logging.getLogger(__name__)

# Default marker directory — resolved at runtime via files.get_abs_path
_DEFAULT_MARKER_DIR = None


def _get_default_marker_dir() -> str:
    """Resolve the default marker directory lazily."""
    global _DEFAULT_MARKER_DIR
    if _DEFAULT_MARKER_DIR is None:
        try:
            from python.helpers import files
            _DEFAULT_MARKER_DIR = files.get_abs_path("tmp", "executing")
        except Exception:
            _DEFAULT_MARKER_DIR = os.path.join("tmp", "executing")
    return _DEFAULT_MARKER_DIR


def write_execution_marker(context_id: str, marker_dir: str = None) -> None:
    """Write a marker file indicating this context is executing.
    
    The marker contains a timestamp for diagnostics. This is designed
    to be called synchronously (it's <1ms for a 10-byte write) but
    can also be wrapped in asyncio.ensure_future for fire-and-forget.
    
    Args:
        context_id: The context ID to mark as executing
        marker_dir: Override marker directory (for testing)
    """
    target_dir = marker_dir or _get_default_marker_dir()
    try:
        os.makedirs(target_dir, exist_ok=True)
        marker_path = os.path.join(target_dir, context_id)
        with open(marker_path, "w") as f:
            f.write(str(time.time()))
        logger.debug(f"Execution marker written for context {context_id}")
    except Exception as e:
        # Fire-and-forget — don't crash the agent if marker write fails.
        # The periodic save_tmp_chat will catch the state eventually.
        logger.warning(f"Failed to write execution marker for {context_id}: {e}")


def clear_execution_marker(context_id: str, marker_dir: str = None) -> None:
    """Remove the marker file when context returns to idle.
    
    Safe to call even if no marker exists (idempotent).
    
    Args:
        context_id: The context ID to unmark
        marker_dir: Override marker directory (for testing)
    """
    target_dir = marker_dir or _get_default_marker_dir()
    marker_path = os.path.join(target_dir, context_id)
    try:
        if os.path.exists(marker_path):
            os.remove(marker_path)
            logger.debug(f"Execution marker cleared for context {context_id}")
    except Exception as e:
        logger.warning(f"Failed to clear execution marker for {context_id}: {e}")


def get_interrupted_contexts(marker_dir: str = None) -> List[str]:
    """Scan for marker files left behind by crashed contexts.
    
    Any marker file that still exists on startup indicates a context
    that was executing when the crash happened.
    
    Args:
        marker_dir: Override marker directory (for testing)
        
    Returns:
        List of context IDs that were interrupted
    """
    target_dir = marker_dir or _get_default_marker_dir()
    if not os.path.isdir(target_dir):
        return []
    
    interrupted = []
    try:
        for filename in os.listdir(target_dir):
            filepath = os.path.join(target_dir, filename)
            if os.path.isfile(filepath):
                interrupted.append(filename)
                logger.info(
                    f"Found interrupted execution marker: {filename}"
                )
    except Exception as e:
        logger.warning(f"Failed to scan execution markers: {e}")
    
    return interrupted


async def async_write_marker(context_id: str) -> None:
    """Async wrapper for write_execution_marker (fire-and-forget).
    
    Usage in context.py:
        asyncio.ensure_future(async_write_marker(self.id))
    """
    # Run the sync write in the default executor to avoid blocking
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, write_execution_marker, context_id)
