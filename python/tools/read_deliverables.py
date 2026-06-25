"""Read deliverables tool — content-writer reads specialist agent outputs.

The content-writer calls this tool to load all specialist outputs from the
project's deliverables/ directory (or search for them via grep fallback).
"""
from __future__ import annotations

import os
import subprocess

from python.helpers import files, projects
from python.helpers.print_style import PrintStyle
from python.helpers.tool import Tool, Response


DELIVERABLES_DIR = "deliverables"


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from content. Returns (meta, body)."""
    meta = {}
    body = content
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            fm_block = content[4:end]
            body = content[end + 5:]  # skip past \n---\n
            for line in fm_block.split("\n"):
                if ": " in line:
                    key, val = line.split(": ", 1)
                    meta[key.strip()] = val.strip()
    return meta, body


class ReadDeliverables(Tool):
    """Read specialist agent deliverables from the project deliverables dir."""

    async def execute(self, **kwargs) -> Response:
        mode = self.args.get("mode", "read_all")
        agent_role = self.args.get("agent_role", "")
        query = self.args.get("query", "")

        # Resolve deliverables directory
        project_name = projects.get_context_project_name(self.agent.context)
        if project_name:
            project_dir = projects.get_project_folder(project_name)
        else:
            project_dir = files.get_abs_path("tmp")

        deliverables_dir = os.path.join(project_dir, DELIVERABLES_DIR)

        if mode == "list":
            return self._list_deliverables(deliverables_dir)
        elif mode == "read_all":
            return self._read_all(deliverables_dir)
        elif mode == "read":
            return self._read_by_role(deliverables_dir, agent_role)
        elif mode == "search":
            return self._search(deliverables_dir, query, project_dir)
        else:
            return Response(
                message=f"Error: Invalid mode '{mode}'. Use 'list', 'read_all', 'read', or 'search'.",
                break_loop=True,
            )

    def _get_deliverable_files(self, deliverables_dir: str) -> list[str]:
        """Get sorted list of .md files in deliverables dir."""
        if not os.path.isdir(deliverables_dir):
            return []
        return sorted(
            f for f in os.listdir(deliverables_dir) if f.endswith(".md")
        )

    def _list_deliverables(self, deliverables_dir: str) -> Response:
        """List all deliverable files with metadata."""
        md_files = self._get_deliverable_files(deliverables_dir)
        if not md_files:
            return Response(
                message="No deliverables found in the project deliverables directory.",
                break_loop=False,
            )

        lines = [f"📋 Found {len(md_files)} deliverable(s) in {deliverables_dir}:\n"]
        for fname in md_files:
            filepath = os.path.join(deliverables_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                meta, _ = _parse_frontmatter(content)
                agent = meta.get("agent", "unknown")
                title = meta.get("title", "untitled")
                ts = meta.get("timestamp", "")
                size = len(content)
                lines.append(f"- **{agent}**: {title} ({size} chars) — `{fname}`")
            except Exception:
                lines.append(f"- `{fname}` (unreadable)")

        return Response(message="\n".join(lines), break_loop=False)

    def _read_all(self, deliverables_dir: str) -> Response:
        """Read and concatenate ALL deliverables."""
        md_files = self._get_deliverable_files(deliverables_dir)

        # RCA-400 F-8: Fallback to docs/ if deliverables/ is empty
        # Root cause: agents sometimes save files to docs/ (via write_to_file)
        # instead of deliverables/ (via save_deliverable). The researcher
        # tries read_deliverables but finds nothing.
        docs_dir = os.path.join(os.path.dirname(deliverables_dir), "docs")
        docs_files = []
        if not md_files and os.path.isdir(docs_dir):
            docs_files = sorted(
                f for f in os.listdir(docs_dir) if f.endswith(".md")
            )
            if docs_files:
                PrintStyle.hint(
                    f"[READ_DELIVERABLES] RCA-400 F-8: No files in deliverables/, "
                    f"falling back to docs/ ({len(docs_files)} .md files)"
                )

        if not md_files and not docs_files:
            return Response(
                message="No deliverables found. Specialist agents have not saved "
                        "their outputs yet. Ask the orchestrator to verify delegation.",
                break_loop=False,
            )

        sections = []
        for fname in md_files:
            filepath = os.path.join(deliverables_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                meta, body = _parse_frontmatter(content)
                agent = meta.get("agent", "unknown")
                title = meta.get("title", "untitled")
                header = f"## 📄 {agent.upper()}: {title}\n**Source**: `{fname}`\n"
                sections.append(header + "\n" + body.strip())
            except Exception as e:
                sections.append(f"## ⚠️ Error reading `{fname}`: {e}")

        # RCA-400 F-8: Also read docs/ fallback files
        for fname in docs_files:
            filepath = os.path.join(docs_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                meta, body = _parse_frontmatter(content)
                agent = meta.get("agent", "docs")
                title = meta.get("title", fname.replace(".md", ""))
                header = f"## 📄 {agent.upper()}: {title}\n**Source**: `docs/{fname}` (fallback)\n"
                sections.append(header + "\n" + body.strip())
            except Exception as e:
                sections.append(f"## ⚠️ Error reading `docs/{fname}`: {e}")

        combined = "\n\n---\n\n".join(sections)
        msg = (
            f"📚 Loaded {len(sections)} specialist deliverable(s):\n\n"
            f"{combined}"
        )
        return Response(message=msg, break_loop=False)

    def _read_by_role(self, deliverables_dir: str, agent_role: str) -> Response:
        """Read deliverables filtered by agent role."""
        if not agent_role:
            return Response(
                message="Error: 'agent_role' is required for 'read' mode.",
                break_loop=True,
            )

        md_files = self._get_deliverable_files(deliverables_dir)
        matches = []
        for fname in md_files:
            if agent_role.lower() in fname.lower():
                filepath = os.path.join(deliverables_dir, fname)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    meta, body = _parse_frontmatter(content)
                    title = meta.get("title", "untitled")
                    matches.append(f"## {agent_role}: {title}\n\n{body.strip()}")
                except Exception as e:
                    matches.append(f"Error reading {fname}: {e}")

        if not matches:
            return Response(
                message=f"No deliverables found for agent role: {agent_role}",
                break_loop=False,
            )

        return Response(
            message="\n\n---\n\n".join(matches),
            break_loop=False,
        )

    def _search(self, deliverables_dir: str, query: str, project_dir: str) -> Response:
        """Search deliverables for content matching query, with grep fallback."""
        if not query:
            return Response(
                message="Error: 'query' is required for 'search' mode.",
                break_loop=True,
            )

        # First try deliverables dir
        search_dir = deliverables_dir if os.path.isdir(deliverables_dir) else project_dir
        
        try:
            result = subprocess.run(
                ["grep", "-ril", query, search_dir],
                capture_output=True, text=True, timeout=10,
            )
            matching_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        except Exception:
            matching_files = []

        if not matching_files:
            return Response(
                message=f"No deliverables found matching '{query}'.",
                break_loop=False,
            )

        sections = []
        for filepath in matching_files[:10]:  # Cap at 10
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                meta, body = _parse_frontmatter(content)
                agent = meta.get("agent", os.path.basename(filepath))
                # Extract lines containing the query
                matching_lines = [
                    line for line in body.split("\n") 
                    if query.lower() in line.lower()
                ]
                excerpt = "\n".join(matching_lines[:20])
                sections.append(
                    f"**{agent}** (`{os.path.basename(filepath)}`):\n{excerpt}"
                )
            except Exception:
                continue

        return Response(
            message=f"🔍 Found matches in {len(sections)} file(s):\n\n" + "\n\n---\n\n".join(sections),
            break_loop=False,
        )
