"""
BuildKB — Curated build knowledge base tool for Prisma/deployment/workflow patterns.
====================================================================================

Provides on-demand access to distilled build patterns from:
- Prisma ORM documentation and v7 migration guides
- Next.js 15 production checklist and build optimization
- Railway/Vercel/Docker deployment best practices
- AGIX smoke test history (build-loop RCA)

This is the REACTIVE layer of the build KB system.
The PROACTIVE layer is handled via prompt injection of build workflow docs.

Usage:
    # By category
    build_kb(category="prisma_patterns")
    
    # By query — searches all KB files for matching sections
    build_kb(query="connection pool singleton")
"""

from __future__ import annotations

import os
import logging
from typing import Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.build_kb")

# Valid KB categories mapped to filenames and version metadata
# P2-3: Version-gated — each category includes the framework/lib version it covers
KB_CATEGORIES = {
    "prisma_patterns": {
        "file": "prisma_patterns.md",
        "versions": "Prisma 6.x / 7.x",
        "note": "Contains both Prisma 6 and 7 patterns. Check project package.json for actual version.",
    },
    "build_workflow": {
        "file": "build_workflow.md",
        "versions": "Next.js 14+, Vite 5+",
        "note": "Framework-agnostic build patterns with Next.js/Vite specifics.",
    },
    "deployment_patterns": {
        "file": "deployment_patterns.md",
        "versions": "Vercel, Railway, Docker",
        "note": "Multi-platform deployment. Check project target before applying.",
    },
    "integration_patterns": {
        "file": "integration_patterns.md",
        "versions": "REST/GraphQL, Stripe, Resend, Supabase",
        "note": "API integration patterns. Verify actual API versions in project deps.",
    },
}

# Maximum output characters to prevent token overflow
MAX_OUTPUT_CHARS = 4000


class BuildKB(Tool):
    """Query the build knowledge base for Prisma/deployment/workflow patterns."""

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
                    "## Build KB — Available Categories\n\n"
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
        kb_dir = os.path.join(base, "prompts", "patterns", "build_kb")
        if os.path.isdir(kb_dir):
            return kb_dir

        # Fallback: try from working directory
        kb_dir = os.path.join(os.getcwd(), "prompts", "patterns", "build_kb")
        if os.path.isdir(kb_dir):
            return kb_dir

        logger.warning("BuildKB: KB directory not found")
        return ""

    async def _lookup_category(self, kb_dir: str, category: str) -> Response:
        """Return the full content of a specific KB category file."""
        if category not in KB_CATEGORIES:
            valid = ", ".join(KB_CATEGORIES.keys())
            return Response(
                message=f"❌ Unknown category: `{category}`. Valid categories: {valid}",
                break_loop=False,
            )

        cat_info = KB_CATEGORIES[category]
        fname = cat_info["file"]
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

            # P2-3: Prepend version context so agents know what this applies to
            version_header = (
                f"> **KB Version Context**: {cat_info['versions']}\n"
                f"> {cat_info['note']}\n\n"
            )

            logger.info(f"BuildKB: Returned category '{category}' ({len(content)} chars)")
            return Response(message=version_header + content, break_loop=False)

        except (IOError, OSError) as e:
            return Response(
                message=f"❌ Failed to read KB file {fname}: {e}",
                break_loop=False,
            )

    async def _search_query(self, kb_dir: str, query: str) -> Response:
        """Search all KB files for sections matching the query."""
        if not kb_dir or not os.path.isdir(kb_dir):
            return Response(
                message="❌ Build KB directory not found.",
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

        output_parts = [f"## Build KB — Results for: `{query}`\n"]
        total_len = 0

        for score, category, header, body in top_results:
            section_output = f"### [{category}] {header}\n\n{body}\n\n---\n"
            if total_len + len(section_output) > MAX_OUTPUT_CHARS:
                break
            output_parts.append(section_output)
            total_len += len(section_output)

        logger.info(
            f"BuildKB: Query '{query}' returned {len(top_results)} sections"
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
