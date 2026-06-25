"""
Auto Download Link Extension (Issue #813)

After file-writing tools execute successfully, this extension:
1. Extracts the file path from the tool arguments or response message
2. Stages the file to tmp/downloads/ for HTTP access
3. Appends a download link to the response message

This ensures hosted (Railway) users can download files created by agents
even without direct file browser access.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers import files

logger = logging.getLogger("ext.auto_download_link")

# Tools that create/write files and should get download links
FILE_WRITING_TOOLS = frozenset({
    "write_to_file",
    "save_deliverable",
    "apply_diff",
    "replace_in_file",
})


class AutoDownloadLink(Extension):
    # Context-aware: fires for all profiles, only on write tools
    TOOLS = frozenset({"write_to_file", "save_deliverable", "apply_diff", "replace_in_file"})

    """Inject download links after file-writing tools execute."""

    async def execute(self, response: Response | str | None = None, tool_name: str = "", **kwargs):
        if not response:
            return
        if not hasattr(response, "message") or not response.message:
            return

        # Only process file-writing tools
        if tool_name not in FILE_WRITING_TOOLS:
            return

        # Skip error responses
        msg_lower = response.message.lower()
        if msg_lower.startswith("error:") or "failed" in msg_lower[:60]:
            return

        # Extract file path from tool args or response message
        file_path = self._extract_file_path(tool_name, response=response)
        if not file_path:
            return

        # Resolve absolute path — RCA-318: project-aware resolution
        if not os.path.isabs(file_path):
            try:
                from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError
                file_path = resolve_agent_path(file_path, self.agent)
            except (ProjectContextError, Exception):
                # Best-effort: can't resolve without project context
                file_path = files.get_abs_path(file_path)

        # Verify file exists (use fix_dev_path for Docker dev environments)
        check_path = files.fix_dev_path(file_path)
        if not os.path.exists(check_path):
            logger.debug(f"Auto-download: file not found at {check_path}, skipping")
            return

        try:
            # Stage file for download
            context_id = self.agent.context.id if self.agent.context else "unknown"
            staging_dir = files.get_abs_path(f"tmp/downloads/{context_id}")
            os.makedirs(staging_dir, exist_ok=True)

            display_name = os.path.basename(file_path)
            unique_id = str(uuid.uuid4())[:8]
            staged_name = f"{unique_id}_{display_name}"
            staged_path = os.path.join(staging_dir, staged_name)

            if os.path.isdir(check_path):
                shutil.make_archive(staged_path, "zip", check_path)
                staged_name = f"{staged_name}.zip"
                display_name = f"{display_name}.zip"
            else:
                shutil.copy2(check_path, staged_path)

            download_url = f"/download_work_dir_file?path=tmp/downloads/{context_id}/{staged_name}"

            # Append download link to response
            response.message += f"\n📥 Download: [{display_name}]({download_url})"
            logger.info(f"Auto-download link injected for {display_name}")

        except Exception as e:
            logger.warning(f"Auto-download staging failed: {e}")
            # Don't break the tool response on staging failure

    def _extract_file_path(self, tool_name: str, response: Response | None = None) -> str | None:
        """Extract file path from tool args or response message."""
        tool_args = self.agent.data.get("last_tool_args", {})
        if not isinstance(tool_args, dict):
            return None

        # Different tools store the path under different arg names
        path = tool_args.get("path") or tool_args.get("file_path") or tool_args.get("filepath")

        # save_deliverable computes its path internally — extract from response
        if not path and tool_name == "save_deliverable" and response and hasattr(response, "message"):
            import re
            match = re.search(r"Deliverable saved:\s*(.+?)(?:\n|$)", response.message)
            if match:
                path = match.group(1).strip()

        return path
