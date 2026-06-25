"""
Disk Usage Check — Pure logic for disk space pre-check (F-7).

This module contains the pure, testable logic for checking disk space.
It has NO agent dependencies — the extension wraps it.

Root Cause (MainStreet test): 2 of 8 delegations wasted on emergency disk
cleanup because the container hit 100% disk. No pre-check existed.

Usage:
    from python.helpers.disk_usage_check import check_disk_usage
    warning = check_disk_usage()
    if warning:
        await agent.hist_add_warning(message=warning)
"""

from __future__ import annotations

import logging
import shutil
from typing import Optional

logger = logging.getLogger("agix.disk_usage_check")

# Thresholds
WARNING_THRESHOLD = 0.90   # 90% — inject advisory warning
CRITICAL_THRESHOLD = 0.98  # 98% — inject critical warning


def check_disk_usage(path: str = "/") -> Optional[str]:
    """Check disk usage and return a user-facing warning if usage is high.

    Args:
        path: Filesystem path to check. Defaults to root '/'.

    Returns:
        None if disk usage is below threshold (healthy).
        Warning message string if usage >= 90%.
        Critical message string if usage >= 98%.
    """
    try:
        usage = shutil.disk_usage(path)
    except (OSError, RuntimeError, Exception) as e:
        # Fail-open: if we can't check disk, don't block the agent
        logger.debug(f"Failed to check disk usage: {e}")
        return None

    total = usage.total
    if total == 0:
        return None  # Avoid division by zero

    used_pct = usage.used / total
    free_bytes = usage.free

    if used_pct < WARNING_THRESHOLD:
        return None  # Disk is healthy

    # Format available space for human readability
    available_str = _format_bytes(free_bytes)
    usage_pct_str = f"{used_pct:.0%}"

    if used_pct >= CRITICAL_THRESHOLD:
        return _build_critical_message(usage_pct_str, available_str)
    else:
        return _build_warning_message(usage_pct_str, available_str)


def _build_warning_message(usage_pct: str, available: str) -> str:
    """Build the warning-level disk space message."""
    return (
        f"⚠️ DISK SPACE WARNING: The system disk is {usage_pct} full "
        f"({available} remaining).\n\n"
        f"This may block file write operations soon. To continue:\n"
        f"1. Ask me to clean up old projects and build artifacts "
        f"(node_modules, .next, __pycache__, dist/)\n"
        f"2. Or manually free disk space in the container\n"
        f"3. Then ask me to retry the current task\n\n"
        f"Current operation has been paused to prevent data corruption."
    )


def _build_critical_message(usage_pct: str, available: str) -> str:
    """Build the critical-level disk space message."""
    return (
        f"🚨 DISK SPACE CRITICAL: The system disk is {usage_pct} full "
        f"({available} remaining).\n\n"
        f"This is blocking all file write operations. To continue:\n"
        f"1. Ask me to clean up old projects and build artifacts "
        f"(node_modules, .next, __pycache__, dist/)\n"
        f"2. Or manually free disk space in the container\n"
        f"3. Then ask me to retry the current task\n\n"
        f"Current operation has been paused to prevent data corruption."
    )


def _format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string (e.g. '5.0 GB')."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"
