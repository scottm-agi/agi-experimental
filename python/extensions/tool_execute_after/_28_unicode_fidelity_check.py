"""
Post-Write Unicode Fidelity Check — tool_execute_after extension.

Runs AFTER write_to_file, replace_in_file, and save_to_file executions.
Checks the written file for encoding corruption (broken emoji, replacement
chars, null bytes).

F-5 Fix: Emoji characters rendered as `??` in generated code. This extension
catches the corruption at write time and injects a warning into the agent's
history to trigger a re-write of the affected lines.

Hooks into: tool_execute_after (order 28)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from python.helpers.extension import Extension
from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError

logger = logging.getLogger("agix.unicode_fidelity_check")

# Tools that write files
WRITE_TOOLS = {"write_to_file", "replace_in_file", "save_to_file"}


class UnicodeFidelityCheck(Extension):
    # Context-aware: code agents, write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "replace_in_file", "save_to_file"})

    """Check written files for unicode/encoding corruption.

    After each file-writing tool execution, scans the target file for
    common encoding corruption patterns:
    - Consecutive question marks (likely corrupted emoji)
    - Unicode replacement character (U+FFFD)
    - Null bytes in text files

    If corruption is detected, injects a warning into the agent's history
    to prompt re-writing the affected lines.
    """

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict = None,
        response: Any = None,
        **kwargs,
    ):
        if not tool_name or tool_name.lower() not in WRITE_TOOLS:
            return

        tool_args = tool_args or {}

        # Extract file path from tool args
        file_path = (
            tool_args.get("path")
            or tool_args.get("file_path")
            or tool_args.get("filename")
        )

        if not file_path:
            return

        # Resolve relative paths (same pattern as _27_post_write_verifier.py)
        resolved_path = file_path
        if not os.path.isabs(resolved_path):
            try:
                resolved_path = resolve_agent_path(resolved_path, self.agent)
            except ProjectContextError:
                logger.debug(
                    f"[UNICODE FIDELITY] Cannot resolve '{file_path}' — "
                    f"no project context. Checking raw path."
                )

        # Import here to avoid circular imports at module level
        from python.helpers.checks.unicode_fidelity import check_unicode_fidelity

        try:
            result = check_unicode_fidelity(resolved_path)
        except Exception as e:
            logger.debug(f"[UNICODE FIDELITY] Check failed for {file_path}: {e}")
            return

        if result["pass"]:
            return

        # Corruption detected — build warning message
        issues = result["issues"]
        issue_count = len(issues)

        logger.warning(
            f"[UNICODE FIDELITY] ⚠️ Encoding corruption detected in "
            f"{file_path}: {issue_count} issue(s)"
        )

        # Build a concise warning with the first few issues
        issue_lines = []
        for issue in issues[:5]:
            issue_lines.append(
                f"  Line {issue['line']}, col {issue['column']}: "
                f"{issue['type']} — {issue['snippet']!r}"
            )

        warning_msg = (
            f"⚠️ UNICODE FIDELITY CHECK: Encoding corruption detected in "
            f"'{file_path}' ({issue_count} issue(s)):\n"
            + "\n".join(issue_lines)
        )
        if issue_count > 5:
            warning_msg += f"\n  ... and {issue_count - 5} more issue(s)"
        warning_msg += (
            "\n\nThis usually means emoji characters were corrupted during "
            "file writing. Please re-write the affected lines with correct "
            "Unicode characters."
        )

        await self.agent.hist_add_warning(warning_msg)
