"""
Disk Usage Gate — Pre-check disk space before each delegation loop iteration.

Extension Point: message_loop_start (fires every turn)
Order: 33 (early — before env validator at 36, budget advisor at 37)

Root Cause (MainStreet test — F-7):
    2 of 8 delegations were wasted on emergency disk cleanup because the
    container hit 100% disk. No pre-check existed. The agent discovered
    the problem only after write failures, then spent entire delegation
    turns running `rm -rf` and `docker system prune`.

Fix:
    This extension fires at the start of every message loop iteration.
    It checks disk usage via shutil.disk_usage('/') and:
    - If usage >= 90%: injects a user-facing WARNING via hist_add_warning
    - If usage >= 98%: injects a CRITICAL warning
    - Rate-limited: max 1 disk warning per monologue (agent.data flag)

Architecture:
    - Pure logic lives in python/helpers/disk_usage_check.py (testable)
    - This extension wraps it with agent integration + rate limiting
    - Fires for ALL agents (disk exhaustion affects everyone)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from python.helpers.extension import Extension
from python.helpers.disk_usage_check import check_disk_usage

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.extensions.disk_usage_gate")


class DiskUsageGate(Extension):
    """Pre-check disk space at message_loop_start to prevent wasted delegations."""

    async def execute(self, **kwargs):
        """Fire at message_loop_start to check disk space."""
        agent = self.agent

        # ── GATE: Rate limit — max 1 disk warning per monologue ──
        if agent.data.get("_disk_warning_sent"):
            return  # Already warned this monologue

        # ── CHECK disk usage ──
        warning = check_disk_usage()

        if warning is None:
            return  # Disk is healthy, proceed normally

        # ── INJECT warning message ──
        agent.data["_disk_warning_sent"] = True
        logger.warning(
            f"[DISK_USAGE_GATE] {agent.agent_name}: "
            f"Disk space warning triggered — injecting user-facing message"
        )
        await agent.hist_add_warning(message=warning)
