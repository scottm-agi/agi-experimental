"""
FrontendKB — Curated frontend knowledge base tool for React/TS/Next.js patterns.
================================================================================

Provides on-demand access to distilled frontend patterns from:
- typescript-cheatsheets/react (47K stars)
- awesome-cursorrules (39K stars)
- AGIX smoke test history

This is the REACTIVE layer of the frontend KB system.
The PROACTIVE layer is handled via prompt injection of frontend_cheatsheet.md.

Usage:
    # By category
    frontend_kb(category="react_typescript")
    
    # By query — searches all KB files for matching sections
    frontend_kb(query="Omit HTML attribute")
"""

from __future__ import annotations

import os
import re
import logging
from typing import Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.frontend_kb")

# Valid KB categories mapped to filenames
KB_CATEGORIES = {
    "react_typescript": "react_typescript.md",
    "nextjs_app_router": "nextjs_app_router.md",
    "css_design_systems": "css_design_systems.md",
    "common_pitfalls": "common_pitfalls.md",
}

# Maximum output characters to prevent token overflow
MAX_OUTPUT_CHARS = 4000


class FrontendKB(Tool):
    """Query the frontend knowledge base for React/TS/Next.js/CSS patterns."""

    async def execute(self, **kwargs):
        category = self.args.get("category", "")
        query = self.args.get("query", "")

        if not category and not query:
            # Return available categories
            cats = "\n".join(
                f"- **{cat}**: {fname}" for cat, fname in KB_CATEGORIES.items()
            )
            return Response(
                message=(
                    "## Frontend KB — Available Categories\n\n"
                    f"{cats}\n\n"
                    "Use `category` to get a full reference, or `query` to search all files."
                ),
                break_loop=False,
            )

        kb_dir = self._get_kb_dir()

        if category:
            return await self._lookup_category(kb_dir, category)
        else:
            return await self._search_query(kb_dir, query)

    def _get_kb_dir(self) -> str:
        """Resolve the KB directory path."""
        # Try relative to the prompts directory
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        kb_dir = os.path.join(base, "prompts", "patterns", "frontend_kb")
        if os.path.isdir(kb_dir):
            return kb_dir

        # Fallback: try from working directory
        kb_dir = os.path.join(os.getcwd(), "prompts", "patterns", "frontend_kb")
        if os.path.isdir(kb_dir):
            return kb_dir

        logger.warning("FrontendKB: KB directory not found")
        return ""

    async def _lookup_category(self, kb_dir: str, category: str) -> Response:
        """Return the full content of a specific KB category file."""
        if category not in KB_CATEGORIES:
            valid = ", ".join(KB_CATEGORIES.keys())
            return Response(
                message=f"❌ Unknown category: `{category}`. Valid categories: {valid}",
                break_loop=False,
            )

        fname = KB_CATEGORIES[category]
        fpath = os.path.join(kb_dir, fname)

        if not os.path.isfile(fpath):
            return Response(
                message=f"❌ KB file not found: {fname}. Run the KB setup first.",
                break_loop=False,
            )

        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(MAX_OUTPUT_CHARS)

            if len(content) >= MAX_OUTPUT_CHARS:
                content = content[:MAX_OUTPUT_CHARS] + "\n\n... (truncated)"

            logger.info(f"FrontendKB: Returned category '{category}' ({len(content)} chars)")
            return Response(message=content, break_loop=False)

        except (IOError, OSError) as e:
            return Response(
                message=f"❌ Failed to read KB file {fname}: {e}",
                break_loop=False,
            )

    async def _search_query(self, kb_dir: str, query: str) -> Response:
        """Search all KB files for sections matching the query."""
        if not kb_dir or not os.path.isdir(kb_dir):
            return Response(
                message="❌ Frontend KB directory not found.",
                break_loop=False,
            )

        query_lower = query.lower()
        query_terms = query_lower.split()
        results = []

        for fname in sorted(os.listdir(kb_dir)):
            if not fname.endswith(".md"):
                continue

            fpath = os.path.join(kb_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (IOError, OSError):
                continue

            # Split content into sections by ## headers
            sections = self._split_into_sections(content)

            for header, body in sections:
                # Score section relevance
                section_text = (header + " " + body).lower()
                score = sum(1 for term in query_terms if term in section_text)

                if score > 0:
                    category = fname.replace(".md", "")
                    results.append((score, category, header, body))

        if not results:
            return Response(
                message=(
                    f"No results found for query: `{query}`\n\n"
                    "Try one of these categories directly:\n"
                    + "\n".join(f"- `{cat}`" for cat in KB_CATEGORIES.keys())
                ),
                break_loop=False,
            )

        # Sort by relevance score (descending), take top 3
        results.sort(key=lambda x: x[0], reverse=True)
        top_results = results[:3]

        output_parts = [f"## Frontend KB — Results for: `{query}`\n"]
        total_len = 0

        for score, category, header, body in top_results:
            section_output = f"### [{category}] {header}\n\n{body}\n\n---\n"
            if total_len + len(section_output) > MAX_OUTPUT_CHARS:
                break
            output_parts.append(section_output)
            total_len += len(section_output)

        logger.info(
            f"FrontendKB: Query '{query}' returned {len(top_results)} sections"
        )
        return Response(message="\n".join(output_parts), break_loop=False)

    @staticmethod
    def _split_into_sections(content: str) -> list[tuple[str, str]]:
        """Split markdown content into (header, body) tuples by ## headers."""
        sections = []
        current_header = ""
        current_body_lines = []

        for line in content.split("\n"):
            if line.startswith("## "):
                # Save previous section
                if current_header:
                    sections.append(
                        (current_header, "\n".join(current_body_lines).strip())
                    )
                current_header = line.lstrip("#").strip()
                current_body_lines = []
            else:
                current_body_lines.append(line)

        # Save last section
        if current_header:
            sections.append(
                (current_header, "\n".join(current_body_lines).strip())
            )

        return sections
