"""
Build Lock - Asyncio-based setup serialization for git worktree builds.

Only the setup phase (fetch + worktree creation) needs serialization.
Once worktrees are created, all coding/committing/pushing runs concurrently.

Usage:
    from python.tools.repo_automation.build_lock import build_setup_lock

    async with build_setup_lock("your-bot-username/agix-test"):
        # Serialized: fetch + worktree add
        await clone_or_update_repo(...)
        await run_git_command([..., "worktree", "add", ...])
    # Lock released — agent runs concurrently from here
"""

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Dict, Tuple, Optional

logger = logging.getLogger("agix.build_lock")

# EVENT-LOOP-AWARE LOCK MANAGEMENT
# asyncio.Lock objects are bound to the event loop they're created in.
# If the event loop changes (e.g., webhook handler vs scheduled task),
# we must recreate all locks for the new loop.
#
# We store (event_loop, lock) tuples and check for loop mismatch on every access.
# A threading.Lock guards dict mutations (safe across threads/loops).

_repo_locks: Dict[str, Tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_dict_guard = threading.Lock()  # Thread-safe guard for _repo_locks mutations

# Timeout for acquiring the setup lock (seconds)
SETUP_LOCK_TIMEOUT = 120  # 2 minutes max wait


def _get_repo_lock_sync(repo_key: str, current_loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    """
    Get or create a per-repo asyncio.Lock, ensuring it belongs to the current event loop.
    
    Thread-safe via threading.Lock. If the stored lock was created in a different
    event loop, it's discarded and a fresh one is created for the current loop.
    """
    with _dict_guard:
        if repo_key in _repo_locks:
            stored_loop, stored_lock = _repo_locks[repo_key]
            if stored_loop is current_loop:
                return stored_lock
            else:
                logger.info(
                    f"[build_lock] Event loop changed for '{repo_key}' — "
                    f"recreating lock (old loop={id(stored_loop):#x}, "
                    f"new loop={id(current_loop):#x})"
                )
        # Create new lock for current event loop
        new_lock = asyncio.Lock()
        _repo_locks[repo_key] = (current_loop, new_lock)
        logger.debug(f"[build_lock] Created lock for repo '{repo_key}' in loop {id(current_loop):#x}")
        return new_lock


@asynccontextmanager
async def build_setup_lock(repo_key: str):
    """
    Async context manager that serializes the worktree setup phase.
    
    Only holds the lock during fetch + worktree creation (~3-5s).
    The actual build (code/test/commit/push) runs after lock release.
    
    Event-loop-aware: automatically recreates locks if the event loop
    has changed since the lock was originally created.
    
    Args:
        repo_key: Unique identifier for the repo (e.g., "your-bot-username/agix-test")
    
    Raises:
        TimeoutError: If lock cannot be acquired within SETUP_LOCK_TIMEOUT
    """
    current_loop = asyncio.get_running_loop()
    lock = _get_repo_lock_sync(repo_key, current_loop)
    start = time.monotonic()
    
    logger.info(f"[build_lock] Acquiring setup lock for '{repo_key}' in loop {id(current_loop):#x}...")
    
    try:
        try:
            acquired = await asyncio.wait_for(lock.acquire(), timeout=SETUP_LOCK_TIMEOUT)
            if not acquired:
                raise TimeoutError(f"Failed to acquire build setup lock for '{repo_key}'")
        except RuntimeError as e:
            if "different event loop" in str(e):
                # Safety net: lock was somehow still bound to a stale loop.
                # Force-recreate for the current loop and retry.
                logger.warning(
                    f"[build_lock] RuntimeError on lock acquire for '{repo_key}': {e}. "
                    f"Force-recreating lock for current loop."
                )
                with _dict_guard:
                    new_lock = asyncio.Lock()
                    _repo_locks[repo_key] = (current_loop, new_lock)
                lock = new_lock
                acquired = await asyncio.wait_for(lock.acquire(), timeout=SETUP_LOCK_TIMEOUT)
                if not acquired:
                    raise TimeoutError(f"Failed to acquire build setup lock for '{repo_key}' after recreation")
            else:
                raise
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            raise TimeoutError(
                f"Build setup lock timeout after {elapsed:.1f}s for '{repo_key}'. "
                f"Another build's setup may be stuck."
            )
    except TimeoutError:
        raise
    
    elapsed = time.monotonic() - start
    if elapsed > 1.0:
        logger.info(f"[build_lock] Acquired lock for '{repo_key}' after {elapsed:.1f}s wait")
    else:
        logger.debug(f"[build_lock] Acquired lock for '{repo_key}' immediately")
    
    try:
        yield
    finally:
        lock.release()
        logger.debug(f"[build_lock] Released setup lock for '{repo_key}'")
