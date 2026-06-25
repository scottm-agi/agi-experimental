from __future__ import annotations
"""
Cat -n / --number Interceptor

Universal tool_execute_before extension that blocks `cat -n`, `cat --number`,
`cat -An`, `cat -bn` and similar line-numbering cat invocations inside
code_execution_tool commands.

These commands prepend line-number prefixes to file content. When agents
subsequently use that output to write back to files, the line numbers
become part of the source code, causing syntax errors and corrupting the
build pipeline.

Root cause: RCA-248 Failure #1 — Agent read file with `cat -n`, then
wrote the line-numbered output back, corrupting the source.
"""

import re
import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.cat_n_interceptor")

# Tools that execute shell/terminal commands
_CODE_EXEC_TOOLS = {"code_execution_tool"}

# Pattern: `cat` as a standalone command (word boundary) followed by flags
# that include `-n`, `--number`, or combined flags like `-An`, `-bn`, `-bns`
# Must NOT match words containing 'cat' (e.g., 'concatenate', 'category')
_CAT_N_PATTERN = re.compile(
    r"""
    (?:^|[;\|\&\n]\s*)   # Start of string/line, or after ; | & \n
    cat\s+               # 'cat' command followed by whitespace
    (?:                   # Flag group:
        --number          #   --number (long form)
        |                 #   OR
        -[a-zA-Z]*n       #   short flags containing 'n' (e.g., -n, -An, -bn)
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

_BLOCK_MESSAGE = (
    "🔴 BLOCKED: `cat -n` / `cat --number` corrupts source files.\n\n"
    "The `-n` flag prepends line numbers (e.g., `1: import React...`) to every line. "
    "If you then write that output back to a file, the line numbers become part of "
    "the source code, causing syntax errors.\n\n"
    "✅ Use the `read_file` tool instead — it returns clean file contents without "
    "line-number prefixes."
)


class CatNInterceptor(Extension):
    # Context-aware: code agents, code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """
    Blocks cat -n / cat --number commands in code_execution_tool.

    Returns a Response with guidance to use read_file instead.
    Does NOT block plain `cat` (no -n flag) — that's legitimate.
    """

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        **kwargs,
    ):
        # Only intercept code execution tools
        if not tool_name or tool_name.lower() not in _CODE_EXEC_TOOLS:
            return None

        if not tool_args or not isinstance(tool_args, dict):
            return None

        code = tool_args.get("code", "")
        if not code:
            return None

        # Check for cat -n patterns
        if _CAT_N_PATTERN.search(code):
            # Escape hatch — prevent infinite blocking loops
            if gate_check(self.agent.data, "cat_n_interceptor"):
                return None

            logger.warning(
                f"[CAT_N_INTERCEPTOR] Blocked `cat -n` command from "
                f"agent #{self.agent.number}: {code[:80]!r}"
            )
            return Response(message=_BLOCK_MESSAGE, break_loop=False)

        return None
