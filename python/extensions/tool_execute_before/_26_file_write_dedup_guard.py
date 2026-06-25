"""
File Write Deduplication Guard — tool_execute_before extension.

Prevents the "amnesia loop" pattern where the code agent writes files,
summarization compresses history erasing evidence of the writes, and the
agent then re-creates the same files repeatedly. Existing loop detectors
miss this because each write_to_file call has different paths/content.

Behaviour:
- 1st write to a path: allowed (returns None)
- 2nd write to same path: advisory warning injected, still allowed
- 3rd+ write to same path: hard-blocked (returns blocking string)
- read_file on a path: resets that path's counter (proves read-then-modify intent)
- replace_in_file: NOT counted (it's the correct tool for modifications)
- Escape hatch: after MAX_DEDUP_BLOCKS total blocks, stop blocking

Key design constraint: ALL state stored in agent.data (persistent dict),
NOT in self._ instance variables. The framework creates new extension
instances on every call_extensions() invocation.

Hooks into: tool_execute_before (order 26 — before post_write_verifier at 27)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from python.helpers.extension import Extension
from python.helpers.universal_gate_budget import gate_check


logger = logging.getLogger("agix.file_write_dedup_guard")

# Maximum number of hard-blocks before escape hatch activates
MAX_DEDUP_BLOCKS = 5

# Tools that count as "write" operations (increment counter)
_WRITE_TOOLS = {"write_to_file", "save_to_file"}

# Tools that reset the counter (agent read the file first — intentional modify)
_READ_TOOLS = {"read_file", "view_file"}

# Tools that are modifications (correct behaviour — NOT counted)
_MODIFY_TOOLS = {"replace_in_file", "apply_diff"}


class FileWriteDedupGuard(Extension):
    """Guard against amnesia-loop file rewrites.

    Tracks write_to_file calls by file path in agent.data['_file_write_counts'].
    This data structure survives summarization because agent.data is never compressed.

    On 2nd write: advisory warning.
    On 3rd+ write: hard-block (returns string message).
    read_file resets a path's counter. replace_in_file does NOT increment.
    After MAX_DEDUP_BLOCKS total blocks across all paths, stops blocking.
    """

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict = None,
        **kwargs,
    ) -> Any:
        if not tool_name:
            return None

        tool_args = tool_args or {}
        tool_lower = tool_name.lower()

        # Extract file path from tool args
        file_path = (
            tool_args.get("path")
            or tool_args.get("file_path")
            or tool_args.get("filename")
            or tool_args.get("AbsolutePath")
        )

        if not file_path:
            return None

        # Normalize the path for consistent tracking
        normalized_path = os.path.normpath(file_path)

        # Initialize state in agent.data if not present
        if "_file_write_counts" not in self.agent.data:
            self.agent.data["_file_write_counts"] = {}

        counts = self.agent.data["_file_write_counts"]
        total_blocks = self.agent.data.get("_file_dedup_blocks", 0)

        # --- Handle read_file: reset counter for that path ---
        if tool_lower in _READ_TOOLS:
            if normalized_path in counts:
                logger.info(
                    f"[FILE_WRITE_DEDUP] read_file on '{normalized_path}' — "
                    f"resetting write counter from {counts[normalized_path]} to 0"
                )
                counts[normalized_path] = 0
            return None

        # --- Handle replace_in_file: do NOT increment counter ---
        if tool_lower in _MODIFY_TOOLS:
            logger.debug(
                f"[FILE_WRITE_DEDUP] {tool_name} on '{normalized_path}' — "
                f"not counting (correct modification tool)"
            )
            return None

        # --- Handle write_to_file ---
        if tool_lower not in _WRITE_TOOLS:
            return None

        # Increment write count for this path
        current_count = counts.get(normalized_path, 0) + 1
        counts[normalized_path] = current_count

        logger.info(
            f"[FILE_WRITE_DEDUP] write #{current_count} to '{normalized_path}'"
        )

        # 1st write: allowed
        if current_count == 1:
            return None

        # 2nd write: advisory warning, still allowed
        if current_count == 2:
            warning_msg = (
                f"⚠️ You already wrote `{file_path}` — it exists on disk. "
                f"Use `replace_in_file` to modify existing files."
            )
            logger.warning(
                f"[FILE_WRITE_DEDUP] Advisory warning for '{normalized_path}' "
                f"(write #{current_count})"
            )
            await self.agent.hist_add_warning(warning_msg)
            return None

        # 3rd+ write: check escape hatch first
        if gate_check(self.agent.data, "file_dedup", threshold=MAX_DEDUP_BLOCKS):
            return None

        # Hard-block the write


        block_msg = (
            f"🔴 WRITE LOOP DETECTED: You have written `{file_path}` "
            f"{current_count} times. This file EXISTS on disk. "
            f"STOP recreating it. If you need to modify it, use "
            f"`replace_in_file`. Move on to your next task."
        )

        logger.warning(
            f"[FILE_WRITE_DEDUP] HARD BLOCK #{total_blocks + 1} for "
            f"'{normalized_path}' (write #{current_count})"
        )

        return block_msg
