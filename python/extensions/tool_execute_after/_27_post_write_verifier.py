"""
Post-Write Verification Gate — tool_execute_after extension.

Runs AFTER write_to_file and replace_in_file executions.
Verifies that the target file was actually created/modified on disk.

This catches "hallucinated writes" — where an agent claims to have written
a file (and the LLM produces a tool-call response) but no file was actually
created. This was a systemic failure identified in RCA-249 (MSR_Smoke_1777658113).

Universal fix: works for ALL projects, never modifies project code.

Hooks into: tool_execute_after (order 27)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from python.helpers.extension import Extension
from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError

logger = logging.getLogger("agix.post_write_verifier")

# Tools that are expected to create/modify files
WRITE_TOOLS = {"write_to_file", "replace_in_file", "save_to_file"}


class PostWriteVerifier(Extension):
    # Context-aware: code agents only, write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "replace_in_file", "save_to_file"})

    """Verify that file-writing tools actually created/modified the target file.

    After each write_to_file/replace_in_file/save_to_file execution,
    checks that:
    1. The target file exists on disk
    2. The file is not empty (0 bytes)

    If verification fails, injects a warning into the agent's history
    to trigger a retry. Never modifies the project code itself.
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
            logger.debug(
                f"[POST-WRITE VERIFIER] No path found in tool_args for {tool_name}"
            )
            return

        # Initialize tracking
        if "_write_verifications" not in self.agent.data:
            self.agent.data["_write_verifications"] = {
                "verified": 0,
                "failed": 0,
                "files_checked": [],
            }

        tracking = self.agent.data["_write_verifications"]

        # ── RCA-318: Canonical project-aware path resolution ──
        resolved_path = file_path
        if not os.path.isabs(resolved_path):
            try:
                resolved_path = resolve_agent_path(resolved_path, self.agent)
            except ProjectContextError:
                # Can't resolve — try the raw path, log warning
                logger.warning(
                    f"[POST-WRITE VERIFIER] Cannot resolve '{file_path}' — "
                    f"no project context. Checking raw path."
                )

        # Verify file existence and size
        file_exists = os.path.exists(resolved_path)
        file_size = os.path.getsize(resolved_path) if file_exists else 0

        if file_exists and file_size > 0:
            # Check if this is a NEW file (not previously tracked)
            previously_tracked = any(
                entry.get("path") == file_path
                for entry in tracking.get("files_checked", [])
            )

            # Success — file was created with content
            tracking["verified"] += 1
            tracking["files_checked"].append({
                "path": file_path,
                "size": file_size,
                "status": "verified",
            })
            logger.debug(
                f"[POST-WRITE VERIFIER] ✅ Verified: {file_path} ({file_size} bytes)"
            )

            # ── ITR-25: Reset build retry counters for NEW source files ──
            # When the agent creates a new source file, stale build failures
            # (from .next/ cache, missing files) should no longer block builds.
            if not previously_tracked and tool_name.lower() == "write_to_file":
                self._maybe_reset_build_counters(file_path)
        else:
            # Failure — file missing or empty
            tracking["failed"] += 1
            tracking["files_checked"].append({
                "path": file_path,
                "resolved": resolved_path,
                "exists": file_exists,
                "size": file_size,
                "status": "failed",
            })

            if not file_exists:
                warning_msg = (
                    f"⚠️ POST-WRITE VERIFICATION FAILED: File '{file_path}' "
                    f"was NOT created on disk. The {tool_name} call may have "
                    f"failed silently. You MUST call {tool_name} again to "
                    f"actually create this file."
                )
            else:
                warning_msg = (
                    f"⚠️ POST-WRITE VERIFICATION FAILED: File '{file_path}' "
                    f"exists but is EMPTY (0 bytes). The {tool_name} call did "
                    f"not write any content. You MUST call {tool_name} again "
                    f"with the full content."
                )

            logger.warning(
                f"[POST-WRITE VERIFIER] ❌ FAILED: {file_path} "
                f"(exists={file_exists}, size={file_size})"
            )
            await self.agent.hist_add_warning(warning_msg)

    # ── ITR-25: Build retry counter reset for new source files ──

    # Source file extensions that warrant a build counter reset
    _SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".prisma"}

    def _maybe_reset_build_counters(self, file_path: str) -> None:
        """Reset build retry counters if the file is a source file.

        When the agent creates a NEW source file, stale build errors from
        .next/ cache or previously missing files should no longer block
        npm run build. This resets:
        - _build_attempt_count (used by _30_build_retry_gate.py)
        - _build_retry_count (used by node_project.py)
        - _same_build_error_count (used by node_project.py)
        """
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in self._SOURCE_EXTENSIONS:
            return

        # Reset all three build retry counters
        self.agent.data["_build_attempt_count"] = 0
        self.agent.data["_build_retry_count"] = 0
        self.agent.data["_same_build_error_count"] = 0

        # ─── P2-C: ADAPTER SYNC — consolidate raw keys → typed state ──
        # Runs after build counter resets so _build_state reflects new zeros.
        # WRAP-not-replace: raw keys still drive all decisions.
        from python.helpers.agent_data_adapter import sync_build_state
        sync_build_state(self.agent.data)

        logger.info(
            f"[BUILD RETRY RESET] New source file '{file_path}' created — "
            f"build retry counter reset to 0"
        )
