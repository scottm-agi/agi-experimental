"""
Dev Server Restart Hook — tool_execute_after extension (_14_)

Modeled after Cline's PostToolUse → contextModification pattern.

Detects when agents perform bulk file writes to a project with an active
dev server and injects a context warning telling the agent to restart
the dev server via services_mgt to prevent stale HMR/webpack cache errors.

Root cause: The code agent writes 5+ files AFTER the dev server is started,
causing webpack chunk cache invalidation → "Cannot find module './948.js'"
→ 404 on all routes. This hook prevents that by alerting the agent.
"""

import logging
import re
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.dev_server_restart_hook")

# Threshold: after this many file writes, inject restart warning
FILE_WRITE_RESTART_THRESHOLD = 3

# Patterns that indicate file write operations in code_execution_tool output
FILE_WRITE_PATTERNS = [
    re.compile(r"Created file\b", re.IGNORECASE),
    re.compile(r"Wrote (?:to|\d+)", re.IGNORECASE),
    re.compile(r"cat\s*>", re.IGNORECASE),
    re.compile(r"tee\s+", re.IGNORECASE),
    re.compile(r"echo\s+.*>(?!>)", re.IGNORECASE),       # echo ... > file (not >>)
    re.compile(r">\s*/\S+", re.IGNORECASE),               # redirect to absolute path
    re.compile(r"cp\s+\S+\s+\S+", re.IGNORECASE),         # cp source dest
    re.compile(r"mv\s+\S+\s+\S+", re.IGNORECASE),         # mv source dest
    re.compile(r"mkdir\s+", re.IGNORECASE),                # mkdir (often precedes writes)
]


def _count_file_writes(response_text: str) -> int:
    """Count the number of file-write indicators in tool output."""
    count = 0
    for line in response_text.split("\n"):
        for pattern in FILE_WRITE_PATTERNS:
            if pattern.search(line):
                count += 1
                break  # Don't double-count a line
    return count


class DevServerRestartHook(Extension):
    # Context-aware: code agents only, write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "replace_in_file", "apply_diff", "save_to_file"})

    """PostToolUse hook: detect bulk file writes and inject dev server restart hint.

    Lifecycle position: _14_ (after masks at _10_, before failure tracker at _12_)

    Tracking keys on agent.data:
        _dev_server_started: bool    — set by services_mgt tool
        _file_writes_since_restart: int — write counter, reset on restart
    """

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        tool_lower = tool_name.lower()

        # ── services_mgt call: reset the write counter ──
        if tool_lower == "services_mgt":
            self.agent.data["_file_writes_since_restart"] = 0
            logger.debug(
                f"[DEV_SERVER_HOOK] {self.agent.agent_name}: "
                f"services_mgt called, reset file write counter"
            )
            return

        # ── Only track code_execution_tool ──
        if tool_lower != "code_execution_tool":
            return

        # ── Only act when dev server is running ──
        if not self.agent.data.get("_dev_server_started", False):
            return

        # ── Extract response text ──
        response_text = ""
        if hasattr(response, "message"):
            response_text = response.message or ""
        elif isinstance(response, str):
            response_text = response

        if not response_text:
            return

        # ── Count file writes in this response ──
        write_count = _count_file_writes(response_text)
        if write_count == 0:
            return

        # ── Increment counter ──
        current = self.agent.data.get("_file_writes_since_restart", 0)
        new_total = current + write_count
        self.agent.data["_file_writes_since_restart"] = new_total

        logger.info(
            f"[DEV_SERVER_HOOK] {self.agent.agent_name}: "
            f"+{write_count} file writes detected (total: {new_total}/"
            f"{FILE_WRITE_RESTART_THRESHOLD})"
        )

        # ── Check threshold and inject warning ──
        if new_total >= FILE_WRITE_RESTART_THRESHOLD:
            warning = (
                f"⚡ DEV SERVER RESTART NEEDED: You have written {new_total} files "
                f"since the dev server was started. The dev server's HMR/webpack "
                f"cache is likely stale and will cause build errors (404s, chunk "
                f"not found). You MUST restart the dev server using the services_mgt "
                f"tool with action='restart_service' before testing the application "
                f"in the browser. This will clear the .next cache and re-launch."
            )
            await self.agent.hist_add_warning(warning)
            logger.warning(
                f"[DEV_SERVER_HOOK] {self.agent.agent_name}: "
                f"Restart warning injected at {new_total} writes"
            )

