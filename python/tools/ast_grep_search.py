"""
AstGrepSearch Tool (RCA-358 / F-27)

Structural code search for TypeScript/JavaScript/JSX/TSX using the `sg` (ast-grep) CLI.
Complements ast_symbol_search.py which handles Python-only AST search via stdlib ast.

Uses `sg run --pattern '<pattern>' --json <path>` for structural matching.
Falls back gracefully if `sg` is not installed.

References:
- https://ast-grep.github.io/
- F-27 in RCA-358
"""
from __future__ import annotations

import asyncio
import json
import logging
import shlex
from typing import Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger("tool.ast_grep_search")

SUPPORTED_LANGUAGES = frozenset({"typescript", "javascript", "tsx", "jsx"})


class AstGrepSearch(Tool):
    """Structural code search for TS/JS/JSX/TSX using the ast-grep (`sg`) CLI.

    For Python structural search, use `ast_symbol_search` instead.
    """

    async def execute(
        self,
        path: str = ".",
        pattern: str = "",
        language: str = "typescript",
        **kwargs,
    ) -> Response:
        """
        Execute structural code search using ast-grep.

        Args:
            path: File or directory to search (defaults to current directory).
            pattern: ast-grep structural pattern (e.g. 'export function $NAME').
            language: Target language: typescript, javascript, tsx, jsx.
        """
        # Also allow args from self.args (agent tool-call pattern)
        pattern = pattern or self.args.get("pattern", "")
        path = path if path != "." else self.args.get("path", ".")
        language = language if language != "typescript" else self.args.get("language", "typescript")

        if not pattern or not pattern.strip():
            return Response(
                message="Error: 'pattern' is required. Provide an ast-grep structural pattern "
                        "(e.g. `export function $NAME`).",
                break_loop=False,
            )

        if language not in SUPPORTED_LANGUAGES:
            return Response(
                message=(
                    f"Error: Unsupported language '{language}'. "
                    f"Supported languages: {', '.join(sorted(SUPPORTED_LANGUAGES))}. "
                    f"For Python, use the `ast_symbol_search` tool instead."
                ),
                break_loop=False,
            )

        # Build sg command
        cmd = ["sg", "run", "--pattern", pattern, "--json", "--lang", language, path]

        try:
            cmd_str = " ".join(shlex.quote(c) for c in cmd)
            logger.info(f"Executing: {cmd_str}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=120
            )

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if process.returncode != 0:
                error_msg = stderr_text or stdout_text or "Unknown error"
                return Response(
                    message=(
                        f"**ast-grep Error** (exit code {process.returncode}):\n"
                        f"```\n{error_msg}\n```\n\n"
                        f"Command: `{cmd_str}`"
                    ),
                    break_loop=False,
                )

            # Parse JSON output
            results = self._parse_sg_output(stdout_text)

            if not results:
                return Response(
                    message=(
                        f"No matches found for pattern `{pattern}` "
                        f"in `{path}` (language: {language})."
                    ),
                    break_loop=False,
                )

            # Format results
            output = f"### ast-grep Search Results\n\n"
            output += f"Pattern: `{pattern}`\n"
            output += f"Path: `{path}` | Language: {language}\n"
            output += f"Found **{len(results)}** match(es):\n\n"

            for res in results[:100]:
                file_name = res.get("file", "unknown")
                line = res.get("line", "?")
                text = res.get("text", "").strip()
                # Truncate long match text
                if len(text) > 120:
                    text = text[:117] + "..."
                output += f"- `{file_name}` (Line {line}): `{text}`\n"

            if len(results) > 100:
                output += f"\n... and {len(results) - 100} more match(es).\n"

            summary = f"📊 Found {len(results)} match(es) for `{pattern}` in `{path}`"

            return Response(
                message=output,
                break_loop=False,
                summary=summary,
                additional={"results": results},
            )

        except asyncio.TimeoutError:
            return Response(
                message="Error: ast-grep search timed out after 120 seconds.",
                break_loop=False,
            )
        except FileNotFoundError:
            return Response(
                message=(
                    "Error: `sg` (ast-grep) CLI not found in PATH. "
                    "Install it via `npm install -g @ast-grep/cli` or "
                    "`cargo install ast-grep`. See https://ast-grep.github.io/guide/quick-start.html"
                ),
                break_loop=False,
            )
        except Exception as e:
            logger.error(f"ast-grep execution failed: {e}", exc_info=True)
            return Response(
                message=f"Error executing ast-grep: {str(e)}",
                break_loop=False,
            )

    def _parse_sg_output(self, stdout_text: str) -> list[dict]:
        """Parse sg --json output into a list of structured match dicts.

        Each result dict has keys: text, file, line, column, language.
        Handles both single-array and newline-delimited JSON formats.
        """
        if not stdout_text:
            return []

        try:
            raw = json.loads(stdout_text)
        except json.JSONDecodeError:
            # Try newline-delimited JSON (some sg versions)
            raw = []
            for line in stdout_text.splitlines():
                line = line.strip()
                if line:
                    try:
                        raw.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if not isinstance(raw, list):
            raw = [raw]

        results = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            rng = item.get("range", {})
            start = rng.get("start", {})
            results.append({
                "text": item.get("text", ""),
                "file": item.get("file", ""),
                "line": start.get("line", 0),
                "column": start.get("column", 0),
                "language": item.get("language", ""),
            })

        return results
