from __future__ import annotations
"""
Extension hook: File-Read Dedup Tracker.

Intercepts file-reading tool calls (code_execution_tool with read_file,
view_file) and:
  1. Checks if the file was already read this session
  2. If yes, injects a dedup hint into tool_args (extra_info field)
  3. Records the read for future dedup checks

This prevents agents from wasting 2-3 tool calls re-reading the same files
that fell out of the LLM context window. Instead, the agent gets a gentle
(or strong) nudge to reference its earlier read.

Priority 20 — runs after project scope enforcer but before mode filters.
"""

import logging
from typing import Optional

from python.helpers.extension import Extension
from python.helpers.file_read_tracker import (
    record_file_read,
    build_dedup_hint,
)

logger = logging.getLogger("agix.ext.file_read_dedup")

# Tool names and argument keys that involve file reading
_FILE_READ_TOOLS = {
    "code_execution_tool": "path",  # code exec with read_file
}

# Additional tools that might read files
_DIRECT_READ_TOOLS = {
    "view_file": "AbsolutePath",
    "read_file": "path",
}


class FileReadDedup(Extension):
    """Inject dedup hints when agents re-read previously read files."""

    async def execute(
        self,
        tool_name: str = "",
        tool_args: Optional[dict] = None,
        **kwargs,
    ):
        """Check for file-read duplication and inject hints."""
        if not tool_args:
            return

        # Determine the file path being read
        filepath = None

        if tool_name in _DIRECT_READ_TOOLS:
            arg_key = _DIRECT_READ_TOOLS[tool_name]
            filepath = tool_args.get(arg_key, "")

        elif tool_name in _FILE_READ_TOOLS:
            # For code_execution_tool, check if it's a read operation
            runtime = tool_args.get("runtime", "")
            code = tool_args.get("code", "")
            if runtime == "python" and ("open(" in code and "read" in code):
                # Extract file path from code (best-effort)
                import re
                match = re.search(r"open\(['\"]([^'\"]+)['\"]", code)
                if match:
                    filepath = match.group(1)

        if not filepath or len(filepath) < 3:
            return

        # Get current iteration from agent context
        iteration = getattr(
            self.agent.context, "_chain_monologue_iterations", 0
        ) if hasattr(self.agent, "context") else 0

        # Check for dedup hint BEFORE recording this read
        hint = build_dedup_hint(self.agent.data, filepath, iteration)

        if hint:
            # Inject hint into tool_args as extra context
            existing_info = tool_args.get("_dedup_hint", "")
            tool_args["_dedup_hint"] = hint
            logger.info(f"FILE_READ_DEDUP: Hint injected for {filepath}")

        # Record this read
        record_file_read(self.agent.data, filepath, iteration)
