"""
docs_lookup — Transparent documentation fallback tool.

3-layer resolution:
  1. Context7 MCP (resolve-library-id → get-library-docs)
  2. Tavily search scoped to docs sites
  3. Perplexity ask as final fallback

Eliminates the "context7 not available" failure that's occurred 20+ times.
"""
from __future__ import annotations
import os
import json
import logging
from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.docs_lookup")


class DocsLookup(Tool):
    """Unified documentation lookup with transparent MCP→Search fallback chain."""

    async def execute(self, **kwargs) -> Response:
        library = self.args.get("library", "").strip()
        query = self.args.get("query", "").strip()
        version = self.args.get("version", "")

        if not library:
            return Response(
                message="Error: Missing 'library' argument. Example: {\"library\": \"next.js\", \"query\": \"app router config\"}",
                break_loop=False,
            )
        if not query:
            query = f"{library} documentation getting started"

        version_suffix = f" version {version}" if version else ""
        full_query = f"{library}{version_suffix} {query}"

        # ── Layer 1: Context7 MCP ──
        result = await self._try_context7(library, query)
        if result:
            return Response(
                message=f"📚 Documentation for {library}{version_suffix} (via Context7):\n\n{result}",
                break_loop=False,
            )

        # ── Layer 2: Tavily MCP search scoped to doc sites ──
        result = await self._try_tavily(full_query, library)
        if result:
            return Response(
                message=f"📚 Documentation for {library}{version_suffix} (via web search):\n\n{result}",
                break_loop=False,
            )

        # ── Layer 3: Perplexity MCP ask ──
        result = await self._try_perplexity(full_query)
        if result:
            return Response(
                message=f"📚 Documentation for {library}{version_suffix} (via research):\n\n{result}",
                break_loop=False,
            )

        # ── All layers failed ──
        return Response(
            message=(
                f"⚠️ Could not find documentation for '{library}'. All lookup layers failed.\n"
                f"Suggestions:\n"
                f"  1. Check the library name spelling\n"
                f"  2. Try search_engine tool with: '{library} documentation site:npmjs.com OR site:github.com'\n"
                f"  3. Check the project's package.json for the exact dependency name and version"
            ),
            break_loop=False,
        )

    async def _try_context7(self, library: str, query: str) -> str | None:
        """Layer 1: Try Context7 MCP tools."""
        try:
            from python.helpers import mcp_handler
            # Step 1: Resolve library ID
            resolve_result = await mcp_handler.MCPConfig.call_tool(
                tool_name="context7.resolve-library-id",
                input_data={"libraryName": library},
            )
            if not resolve_result:
                return None

            # Parse the library ID from result
            library_id = self._extract_library_id(resolve_result)
            if not library_id:
                return None

            # Step 2: Get documentation
            docs_result = await mcp_handler.MCPConfig.call_tool(
                tool_name="context7.get-library-docs",
                input_data={"context7CompatibleLibraryID": library_id, "topic": query},
            )
            if docs_result:
                return self._truncate(str(docs_result), 4000)
            return None

        except Exception as e:
            logger.warning(f"docs_lookup: Context7 MCP failed: {e}")
            return None

    async def _try_tavily(self, query: str, library: str) -> str | None:
        """Layer 2: Try Tavily MCP search scoped to documentation sites."""
        try:
            from python.helpers import mcp_handler
            # Build a docs-focused query
            docs_domains = [
                f"site:{library.replace('.', '')}.org",
                f"site:npmjs.com/package/{library}",
                "site:github.com",
                "site:developer.mozilla.org",
            ]
            scoped_query = f"{query} ({' OR '.join(docs_domains[:2])})"

            result = await mcp_handler.MCPConfig.call_tool(
                tool_name="tavily-mcp.tavily-search",
                input_data={
                    "query": scoped_query,
                    "search_depth": "basic",
                    "max_results": 3,
                },
            )
            if result:
                return self._truncate(str(result), 4000)
            return None

        except Exception as e:
            logger.warning(f"docs_lookup: Tavily search failed: {e}")
            return None

    async def _try_perplexity(self, query: str) -> str | None:
        """Layer 3: Try Perplexity MCP as final fallback."""
        try:
            from python.helpers import mcp_handler
            result = await mcp_handler.MCPConfig.call_tool(
                tool_name="perplexity-ask.perplexity_ask",
                input_data={
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a documentation lookup assistant. Provide concise, accurate, version-specific documentation.",
                        },
                        {"role": "user", "content": query},
                    ]
                },
            )
            if result:
                return self._truncate(str(result), 4000)
            return None

        except Exception as e:
            logger.warning(f"docs_lookup: Perplexity failed: {e}")
            return None

    def _extract_library_id(self, result) -> str | None:
        """Extract the library ID from Context7 resolve result.

        Context7 returns text like:
          Available Libraries:
          - Title: Next.js
          - Context7-compatible library ID: /vercel/next.js
          ...

        We need to extract the FIRST library ID from this text.
        """
        # Handle CallToolResult objects from MCP
        if hasattr(result, 'content'):
            # MCP CallToolResult — extract text from content blocks
            content_blocks = result.content if isinstance(result.content, list) else [result.content]
            text_parts = []
            for block in content_blocks:
                if hasattr(block, 'text'):
                    text_parts.append(block.text)
                elif isinstance(block, dict) and 'text' in block:
                    text_parts.append(block['text'])
                elif isinstance(block, str):
                    text_parts.append(block)
            result = '\n'.join(text_parts) if text_parts else str(result)

        if isinstance(result, str):
            # Try to parse as JSON first
            try:
                data = json.loads(result)
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get("id") or data[0].get("libraryId")
                if isinstance(data, dict):
                    return data.get("id") or data.get("libraryId")
            except (json.JSONDecodeError, TypeError):
                pass

            # Parse Context7's text format: look for "Context7-compatible library ID: /xxx/yyy"
            import re
            id_match = re.search(r'Context7-compatible library ID:\s*(/[^\s\n]+)', result)
            if id_match:
                return id_match.group(1).strip()

            # If it's a short string that looks like a bare ID (e.g. "/vercel/next.js")
            stripped = result.strip()
            if stripped.startswith("/") and "\n" not in stripped and len(stripped) < 100:
                return stripped

        elif isinstance(result, dict):
            return result.get("id") or result.get("libraryId")
        elif isinstance(result, list) and len(result) > 0:
            first = result[0]
            if isinstance(first, dict):
                return first.get("id") or first.get("libraryId")
        return None

    def _truncate(self, text: str, max_len: int) -> str:
        """Truncate text to max_len, adding a notice if truncated."""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "\n\n... [truncated — use more specific query for details]"
