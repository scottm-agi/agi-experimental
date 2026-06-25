from __future__ import annotations
import os
import re
from python.helpers.tool import Tool, Response
from python.helpers import files, projects
from python.helpers.file_guard import FileGuard
from python.helpers.read_before_write_guard import check_read_before_write, check_read_before_write_proactive, record_file_write
from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError

class ApplyDiff(Tool):
    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path")
        diff = self.args.get("diff")

        if not path:
            return Response(message="Error: Missing 'path' argument.", break_loop=False)
        if not diff:
            return Response(message="Error: Missing 'diff' argument.", break_loop=False)

        try:
            # ── RCA-318: Canonical project-aware path resolution ──
            try:
                abs_path = resolve_agent_path(path, self.agent)
            except ProjectContextError as e:
                return Response(
                    message=f"⚠️ PATH RESOLUTION ERROR: {e}\nCannot resolve '{path}' without project context.",
                    break_loop=False,
                )

            if not os.path.exists(abs_path):
                return Response(message=f"Error: File '{path}' not found.", break_loop=False)

            # ── FileGuard: Enforce project scope ──
            active_project = projects.get_context_project_name(self.agent.context)
            is_allowed, guard_msg = FileGuard.validate_write_path(abs_path, active_project)
            if not is_allowed:
                return Response(
                    message=f"FileGuard: {guard_msg}",
                    break_loop=False
                )

            # ── RCA-241: Read-Before-Write Guard ──
            # Prevent stale-context edits by ensuring agent read the file first.
            # Uses same pattern as write_to_file.py: proactive first, blocking fallback.
            agent_id = str(getattr(self.agent, 'number', 'unknown'))
            advisory_warnings = []

            proactive_result = check_read_before_write_proactive(
                agent_id=agent_id,
                abs_path=abs_path,
                force=False,
            )
            if proactive_result:
                advisory_warnings.append(proactive_result.warning)
            else:
                rbw_msg = check_read_before_write(
                    agent_id=agent_id,
                    abs_path=abs_path,
                    force=False,
                )
                if rbw_msg:
                    return Response(message=rbw_msg, break_loop=False)

            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Simple parser for SEARCH/REPLACE blocks
            # Pattern: <<<<<<< SEARCH\n(search_content)\n=======\n(replace_content)\n>>>>>>> REPLACE
            blocks = re.findall(r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", diff, re.DOTALL)
            
            if not blocks:
                return Response(message="Error: No valid SEARCH/REPLACE blocks found in diff. Use the format:\n<<<<<<< SEARCH\n(exact code to find)\n=======\n(replacement code)\n>>>>>>> REPLACE", break_loop=False)

            new_content = content
            applied_count = 0
            for search_block, replace_block in blocks:
                if search_block in new_content:
                    new_content = new_content.replace(search_block, replace_block, 1) # Only replace first occurrence per block
                    applied_count += 1
                else:
                    return Response(
                        message=f"Error: Could not find exact match for SEARCH block:\n{search_block}\n\n"
                                f"⚠️ DO NOT RETRY THIS IDENTICAL DIFF. The file content does not match your search block. "
                                f"You MUST use the `read_file` tool to view the current file content, find the exact strings, and then try again.",
                        break_loop=False
                    )

            # Atomic write
            files.write_file_atomic(abs_path, new_content)

            # FIX-12: Broadcast write for cross-agent stale detection
            record_file_write(agent_id, abs_path)

            msg = f"Successfully applied {applied_count} change(s) to '{path}'."
            if advisory_warnings:
                msg += "\n\n" + "\n\n".join(advisory_warnings)

            return Response(message=msg, break_loop=False)

        except Exception as e:
            return Response(message=f"Error applying diff to '{path}': {str(e)}", break_loop=False)

