"""Replace-in-deliverable tool — surgically edit deliverable documents.

Mirrors replace_in_file but is scoped exclusively to the project's
deliverables/ directory and non-code file types (.md, .txt, .json, etc.).

This gives architect and specialist agents the ability to update their own
deliverables without needing access to the code-oriented files_write tools.

RCA-318: Architect agents tried replace_in_file to update architecture specs
and got blocked because replace_in_file is in files_write (code tools).
This tool fills the gap — same mechanics, deliverables-only scope.
"""
from __future__ import annotations

import os
import re

from python.helpers import files, projects
from python.helpers.print_style import PrintStyle
from python.helpers.tool import Tool, Response


DELIVERABLES_DIR = "deliverables"

# File extensions allowed for deliverable edits (non-code only).
# Covers architect specs, researcher reports, sales/marketing content,
# and any other document-oriented agent output.
ALLOWED_EXTENSIONS = {
    # Markdown & docs
    ".md", ".markdown", ".mdx", ".rst", ".txt",
    # Office / content
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".pdf",
    # Data / config (non-code)
    ".json", ".yaml", ".yml", ".csv", ".xml", ".toml", ".ini", ".cfg",
    # Web content (non-code templates)
    ".html", ".htm",
    # Logs & misc
    ".log", ".rtf",
}


class ReplaceInDeliverable(Tool):
    """Surgically edit deliverable documents within the project's deliverables/ dir."""

    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path")

        if not path:
            return Response(
                message="Error: Missing 'path' argument. Provide the path to "
                        "the deliverable file to edit (relative to deliverables/ "
                        "or absolute path within deliverables/).",
                break_loop=False,
            )

        # ── Resolve replacement pairs ──
        pairs, err = self._resolve_pairs()
        if err:
            return Response(message=err, break_loop=False)

        # ISS-4: Use canonical resolve_agent_path for project root resolution.
        # Deliverable tools still scope to deliverables/ subdirectory.
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

        # ── Resolve path to absolute ──
        abs_path = self._resolve_path(path, deliverables_dir)

        # ── Boundary check: must be inside deliverables/ ──
        boundary_err = self._check_boundary(abs_path, deliverables_dir)
        if boundary_err:
            return Response(message=boundary_err, break_loop=False)

        # ── File existence check ──
        if not os.path.exists(abs_path):
            return Response(
                message=f"Error: Deliverable file not found: '{path}'. "
                        f"Use `read_deliverables` with mode='list' to see "
                        f"available deliverables.",
                break_loop=False,
            )

        # ── File type check: non-code only ──
        ext = os.path.splitext(abs_path)[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            return Response(
                message=f"Error: File type '{ext}' is not a deliverable format. "
                        f"This tool only edits document files "
                        f"({', '.join(sorted(ALLOWED_EXTENSIONS))}). "
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

        # ── Apply replacements ──
        total_replaced = 0
        not_found = []
        for search_str, replace_str in pairs:
            if search_str in content:
                count = content.count(search_str)
                content = content.replace(search_str, replace_str)
                total_replaced += count
            else:
                not_found.append(search_str[:60])

        if not_found and total_replaced == 0:
            return Response(
                message=f"Error: Could not find search string(s) in deliverable "
                        f"'{os.path.basename(abs_path)}'.\n"
                        f"Not found: [{'; '.join(not_found)}]\n\n"
                        f"Recovery steps:\n"
                        f"1. Use `read_deliverables` to check current content\n"
                        f"2. Ensure search strings match EXACTLY\n"
                        f"3. Re-read the deliverable and retry",
                break_loop=False,
            )

        # ── Write updated content ──
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            return Response(
                message=f"Error writing deliverable '{path}': {e}",
                break_loop=False,
            )

        msg = (
            f"✅ Successfully replaced {total_replaced} occurrence(s) in "
            f"deliverable '{os.path.basename(abs_path)}'."
        )
        if not_found:
            msg += f" Warning: {len(not_found)} search string(s) not found and skipped."

        PrintStyle.hint(msg)
        return Response(message=msg, break_loop=False)

    def _resolve_path(self, path: str, deliverables_dir: str) -> str:
        """Resolve path — relative paths resolve against deliverables/ dir."""
        if os.path.isabs(path):
            return os.path.realpath(path)
        # Relative path → resolve against deliverables/ directory
        return os.path.realpath(os.path.join(deliverables_dir, path))

    def _check_boundary(self, abs_path: str, deliverables_dir: str) -> str | None:
        """Ensure the resolved path is inside the deliverables/ directory.

        Returns error message if outside, None if OK.
        """
        real_deliverables = os.path.realpath(deliverables_dir)
        real_path = os.path.realpath(abs_path)

        if not real_path.startswith(real_deliverables + os.sep) and real_path != real_deliverables:
            return (
                f"Error: Path '{abs_path}' is outside the deliverables/ directory. "
                f"This tool can ONLY edit files within the project's deliverables/ "
                f"directory ({real_deliverables}). For source code edits, delegate "
                f"to a 'code' profile subordinate."
            )
        return None

    def _resolve_pairs(self):
        """Resolve replacement pairs from either batch or legacy format.

        Batch API:
            {"replacements": [{"search": "old", "replace": "new"}, ...]}

        Legacy single-pair:
            {"search_string": "old", "replace_string": "new"}

        Returns:
            (list[tuple[str, str]], str | None): pairs and optional error
        """
        replacements = self.args.get("replacements")

        # Batch API
        if replacements is not None:
            if not isinstance(replacements, list) or len(replacements) == 0:
                return [], (
                    "Error: 'replacements' must be a non-empty array of "
                    "{\"search\": \"...\", \"replace\": \"...\"} objects."
                )
            pairs = []
            for i, r in enumerate(replacements):
                s = r.get("search") or r.get("search_string")
                rp = r.get("replace") or r.get("replace_string")
                if s is None:
                    return [], f"Error: replacements[{i}] missing 'search' key."
                if rp is None:
                    rp = ""
                pairs.append((s, rp))
            return pairs, None

        # Legacy single-pair
        search_string = self.args.get("search_string") or self.args.get("search")
        replace_string = self.args.get("replace_string") or self.args.get("replace")

        if search_string is None:
            return [], (
                "Error: Missing 'search_string' (or 'replacements' array). "
                "Provide the text to find and replace in the deliverable."
            )
        if replace_string is None:
            replace_string = ""

        return [(search_string, replace_string)], None
