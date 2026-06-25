"""
Stale File Awareness — message_loop_prompts_after extension (order 79).

Modeled after Roo-Code's FileContextTracker pattern (RCA-316). At each message
loop iteration, checks whether any files the agent has previously read have been
modified externally (by other agents, user edits, or filesystem changes).

Injects a "Recently Modified Files" warning into the agent's context so it knows
to re-read before editing. This is more robust than the write-time
read_before_write_guard because it catches stale context BEFORE the agent even
attempts a write.

Hook: message_loop_prompts_after (order 79 — after error awareness, before depth)

Key behavior:
    - Lightweight: only stat() calls, no file reads, no LLM calls
    - Invalidates stale files from read history so RBW guard will re-require reads
    - Limits warning to first 5 files to avoid context bloat
"""
from __future__ import annotations
import os
from python.helpers.extension import Extension


class StaleFileAwareness(Extension):
    """Detect and warn about externally-modified files in agent context."""

    async def execute(self, **kwargs):
        """Fire at message_loop_prompts_after to detect stale files.

        Checks every file the agent has read. If any have been modified
        since the read, injects a warning and invalidates the read history
        so the read-before-write guard will require a fresh read.
        """
        from python.helpers.read_before_write_guard import (
            get_stale_files,
            invalidate_stale_reads,
        )

        agent = self.agent
        agent_id = str(getattr(agent, 'number', 'unknown'))

        # Detect stale files (lightweight — just stat() calls)
        stale = get_stale_files(agent_id)
        if not stale:
            return ""  # No stale files — no context injection

        # Invalidate stale reads so RBW guard requires fresh reads
        invalidated = invalidate_stale_reads(agent_id)

        # Build warning (limit to 5 files to avoid context bloat)
        file_list = []
        for path in invalidated[:5]:
            basename = os.path.basename(path)
            file_list.append(f"  - {basename}")

        remaining = len(invalidated) - 5
        if remaining > 0:
            file_list.append(f"  - ... and {remaining} more")

        warning = (
            "⚠️ STALE FILE WARNING: The following files have been modified "
            "since you last read them. You MUST re-read these files with "
            "`read_file` before making any edits:\n"
            + "\n".join(file_list)
        )

        return warning
