"""Apply-diff-deliverable tool — apply SEARCH/REPLACE diff blocks to deliverables.

Mirrors apply_diff but scoped to the project's deliverables/ directory
and non-code file types only. Gives architect, researcher, sales, and
marketing agents the ability to make surgical diff-based edits to their
own deliverable documents.

Part of the deliverables parallel toolset (RCA-318):
  save_deliverable    ≈ write_to_file
  read_deliverables   ≈ read_file
  replace_in_deliverable ≈ replace_in_file
  apply_diff_deliverable ≈ apply_diff
"""
from __future__ import annotations

import os
import re

from python.helpers import projects
from python.helpers.print_style import PrintStyle
from python.helpers.tool import Tool, Response

# Import shared constants from the replace tool
from python.tools.replace_in_deliverable import DELIVERABLES_DIR, ALLOWED_EXTENSIONS


class ApplyDiffDeliverable(Tool):
    """Apply SEARCH/REPLACE diff blocks to deliverable documents."""

    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path")
        diff = self.args.get("diff")

        if not path:
            return Response(
                message="Error: Missing 'path' argument. Provide the path to "
                        "the deliverable file (relative to deliverables/ or absolute).",
                break_loop=False,
            )
        if not diff:
            return Response(
                message="Error: Missing 'diff' argument. Provide SEARCH/REPLACE "
                        "blocks to apply to the deliverable.",
                break_loop=False,
            )

        # ISS-4: Use canonical resolve_agent_path for project root resolution.
        # Deliverable tools still scope to deliverables/ subdirectory, but the
        # project root is resolved via the canonical system for consistency.
        try:
            from python.helpers.resolve_agent_path import resolve_agent_path, ProjectContextError
            project_dir = resolve_agent_path("", self.agent)  # Empty path = project root
        except ProjectContextError:
            return Response(
                message="Error: No active project context. Cannot resolve "
                        "deliverables directory without a project.",
                break_loop=False,
            )

        deliverables_dir = os.path.join(project_dir, DELIVERABLES_DIR)

        # ── Resolve path ──
        if os.path.isabs(path):
            abs_path = os.path.realpath(path)
        else:
            abs_path = os.path.realpath(os.path.join(deliverables_dir, path))

        # ── Boundary check: must be inside deliverables/ ──
        real_deliverables = os.path.realpath(deliverables_dir)
        if not abs_path.startswith(real_deliverables + os.sep) and abs_path != real_deliverables:
            return Response(
                message=f"Error: Path '{path}' is outside the deliverables/ directory. "
                        f"This tool can ONLY edit files within the project's "
                        f"deliverables/ directory ({real_deliverables}). "
                        f"For source code diffs, delegate to a 'code' profile agent.",
                break_loop=False,
            )

        # ── File existence ──
        if not os.path.exists(abs_path):
            return Response(
                message=f"Error: Deliverable file not found: '{path}'. "
                        f"Use `read_deliverables` with mode='list' to see "
                        f"available deliverables.",
                break_loop=False,
            )

        # ── File type check ──
        ext = os.path.splitext(abs_path)[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            return Response(
                message=f"Error: File type '{ext}' is not a deliverable format. "
                        f"This tool only edits document files. "
                        f"For source code files, delegate to a 'code' profile agent.",
                break_loop=False,
            )

        # ── Read current content ──
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return Response(
                message=f"Error reading deliverable '{path}': {e}",
                break_loop=False,
            )

        # ── Parse SEARCH/REPLACE blocks ──
        blocks = re.findall(
            r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
            diff, re.DOTALL,
        )

        if not blocks:
            return Response(
                message="Error: No valid SEARCH/REPLACE blocks found in diff. "
                        "Use the format:\n"
                        "<<<<<<< SEARCH\n"
                        "(exact text to find)\n"
                        "=======\n"
                        "(replacement text)\n"
                        ">>>>>>> REPLACE",
                break_loop=False,
            )

        # ── Apply each block ──
        new_content = content
        applied_count = 0
        for search_block, replace_block in blocks:
            if search_block in new_content:
                new_content = new_content.replace(search_block, replace_block, 1)
                applied_count += 1
            else:
                return Response(
                    message=f"Error: Could not find exact match for SEARCH block "
                            f"in deliverable '{os.path.basename(abs_path)}':\n"
                            f"{search_block[:200]}\n\n"
                            f"⚠️ DO NOT RETRY THIS IDENTICAL DIFF. The content does not match your search block. "
                            f"You MUST use a file reading tool (e.g. `read_file`) to view the current content, "
                            f"find the exact strings, and then try again.",
                    break_loop=False,
                )

        # ── Write updated content ──
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            return Response(
                message=f"Error writing deliverable '{path}': {e}",
                break_loop=False,
            )

        msg = (
            f"✅ Successfully applied {applied_count} diff block(s) to "
            f"deliverable '{os.path.basename(abs_path)}'."
        )
        PrintStyle.hint(msg)
        return Response(message=msg, break_loop=False)
