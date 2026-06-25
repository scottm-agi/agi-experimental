from __future__ import annotations
"""Destructive Command Guard Extension (ITR-35 RC-W1).

Pre-execution filter for destructive shell commands. Intercepts
code_execution_tool calls and blocks rm -rf, find -delete, and other
destructive patterns on project directories. Allows safe cache clearing
(node_modules/.cache, .next/cache, dist/, build/, coverage/).

Root cause: ITR-35 — agents ran 90 rm -rf commands because no
pre-execution filter existed for project-relative destructive commands.
The helper `is_destructive_command()` existed but was never wired into
the tool pipeline.

Pattern follows _11_dev_server_enforcer.py: intercept code_execution_tool,
extract command from tool_args, check, return None to allow or
Response(break_loop=False) to block.
"""

from typing import Any
from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.destructive_command_guard import is_destructive_command

import logging

logger = logging.getLogger("agix.destructive_command_guard_ext")


class DestructiveCommandGuard(Extension):
    """Pre-execution filter for destructive shell commands.

    Intercepts code_execution_tool calls and blocks rm -rf, find -delete,
    and other destructive patterns on project directories. Allows safe
    cache clearing (node_modules/.cache, .next/cache, etc.).

    Root cause: ITR-35 RC-W1 — agents ran 90 rm -rf commands because
    no pre-execution filter existed for project-relative destructive commands.
    """

    async def execute(
        self,
        tool_args: dict[str, Any] = None,
        tool_name: str = "",
        **kwargs,
    ):
        # Only intercept code execution tools
        if tool_name not in ("code_execution_tool", "code_execution"):
            return None

        if not tool_args:
            return None

        # Extract the command from args (different tools use different keys)
        command = ""
        for key in ("code", "command", "runtime_code"):
            if key in tool_args:
                command = str(tool_args[key])
                break

        if not command or not command.strip():
            return None

        if not is_destructive_command(command):
            return None



        # Block the command
        logger.warning(
            f"[DESTRUCTIVE_GUARD] BLOCKED: {command[:200]} — "
            f"destructive command detected"
        )
        return Response(
            message=(
                "⛔ BLOCKED: Destructive command detected. "
                f"The command `{command[:100]}` contains destructive patterns "
                "(rm -rf, find -delete, etc.) targeting project directories.\n\n"
                "**Safe alternatives:**\n"
                "- Delete specific files: `rm file.txt` (no -rf flag)\n"
                "- Clear caches: `rm -rf node_modules/.cache` or "
                "`rm -rf .next/cache` (explicitly allowed)\n"
                "- Clean build dirs: `rm -rf dist/` or `rm -rf build/` "
                "(explicitly allowed)\n"
                "- Restart service: use `services_mgt` action='restart_service'\n"
            ),
            break_loop=False,
        )
