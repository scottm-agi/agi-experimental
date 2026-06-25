"""
Thread monitoring daemon for AGIX.

Periodically logs active thread count and provides stats for
the /health endpoint. Warns if thread count exceeds threshold.

Part of the thread exhaustion fix (Forgejo #762).
"""
import threading
import time
import logging
import os

logger = logging.getLogger("agix.thread_monitor")

# ── Configuration ───────────────────────────────────────────────────
MONITOR_INTERVAL = int(os.environ.get("THREAD_MONITOR_INTERVAL", "60"))  # seconds
THREAD_WARN_THRESHOLD = int(os.environ.get("THREAD_WARN_THRESHOLD", "500"))

# ── State ───────────────────────────────────────────────────────────
_monitor_thread = None
_last_stats = {"count": 0, "peak": 0, "names": [], "timestamp": 0}
_lock = threading.Lock()


def is_thread_safe() -> bool:
    """
    Check if the system has capacity for new work.
    
    Returns False when thread count exceeds the warning threshold,
    enabling callers (e.g., webhook handler) to apply backpressure
    and reject new work until threads are freed.
    
    Fix for Forgejo #1034 — thread monitor was previously monitor-only
    with no active protection.
    """
    count = threading.active_count()
    return count <= THREAD_WARN_THRESHOLD


def get_thread_stats() -> dict:
    """Get current thread statistics. Called by /health endpoint."""
    with _lock:
        count = threading.active_count()
        names = [t.name for t in threading.enumerate()]
        _last_stats["count"] = count
        _last_stats["names"] = names
        _last_stats["timestamp"] = time.time()
        if count > _last_stats["peak"]:
            _last_stats["peak"] = count
        return {
            "count": count,
            "peak": _last_stats["peak"],
            "names": names[:20],  # Cap to avoid huge responses
            "threshold": THREAD_WARN_THRESHOLD,
            "safe": count <= THREAD_WARN_THRESHOLD,
        }


def _monitor_loop():
    """Background loop that logs thread stats periodically."""
    while True:
        try:
            stats = get_thread_stats()
            count = stats["count"]
            peak = stats["peak"]

            if count > THREAD_WARN_THRESHOLD:
                logger.warning(
                    f"[THREAD_MONITOR] ⚠ HIGH THREAD COUNT: {count} "
                    f"(threshold={THREAD_WARN_THRESHOLD}, peak={peak})"
                )
                # Log all thread names for diagnosis
                for name in stats["names"]:
                    logger.warning(f"  Thread: {name}")
            else:
                logger.info(
                    f"[THREAD_MONITOR] Threads: {count} (peak={peak}, "
                    f"threshold={THREAD_WARN_THRESHOLD})"
                )
        except Exception as e:
            logger.error(f"[THREAD_MONITOR] Error: {e}")

        time.sleep(MONITOR_INTERVAL)


def start_monitor():
    """Start the background thread monitor daemon."""
    global _monitor_thread
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return  # Already running

    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        daemon=True,
        name="ThreadMonitor",
    )
    _monitor_thread.start()
    logger.info(
        f"[THREAD_MONITOR] Started (interval={MONITOR_INTERVAL}s, "
        f"threshold={THREAD_WARN_THRESHOLD})"
    )
