"""
GritQL Integration Tool (Issue #801)

AST-aware code transformations using GritQL patterns.
Uses the `grit` CLI (Tree-sitter based) for structural code editing.
Supports Python, JavaScript, TypeScript, Go, Rust, and more.

References:
- https://docs.grit.io/language/overview
- Issue #624 research analysis
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger("tool.grit_transform")


def build_grit_command(
    pattern: str,
    target_files: list[str],
    dry_run: bool = False,
    language: Optional[str] = None,
) -> list[str]:
    """
    Build the grit apply command.

    Args:
        pattern: GritQL pattern (e.g., '`console.log($msg)` => `logger.info($msg)`')
        target_files: List of file paths to transform
        dry_run: If True, preview changes without applying
        language: Optional language hint (python, javascript, etc.)

    Returns:
        Command as list of strings for subprocess

    Raises:
        ValueError: If pattern is empty
    """
    if not pattern or not pattern.strip():
        raise ValueError("GritQL pattern cannot be empty")

    cmd = ["grit", "apply", pattern]

    if dry_run:
        cmd.append("--dry-run")

    if language:
        cmd.extend(["--language", language])

    # Add target files
    for f in target_files:
        cmd.append(f)

    return cmd


class GritTransform(Tool):
    """AST-aware code transformation tool using GritQL.

    Executes `grit apply` via subprocess to perform structural code edits
    based on GritQL patterns. Supports dry-run previews.
    """

    async def execute(self, **kwargs) -> Response:
        pattern: str = self.args.get("pattern", "")
        target: str | list[str] = self.args.get("target", ".")
        dry_run: bool = self.args.get("dry_run", False)
        language: str | None = self.args.get("language", None)

        if not pattern:
            return Response(
                message="Error: 'pattern' is required. Provide a GritQL pattern.",
                break_loop=False,
            )

        # Normalize target to list
        if isinstance(target, str):
            target_files = [target]
        else:
            target_files = list(target)

        try:
            cmd = build_grit_command(
                pattern=pattern,
                target_files=target_files,
                dry_run=dry_run,
                language=language,
            )
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        # Execute via subprocess
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
                        f"**GritQL Error** (exit code {process.returncode}):\n"
                        f"```\n{error_msg}\n```\n\n"
                        f"Command: `{cmd_str}`"
                    ),
                    break_loop=False,
                )

            mode = "Preview (dry-run)" if dry_run else "Applied"
            output = stdout_text or "No changes detected."

            return Response(
                message=(
                    f"**GritQL {mode}**\n\n"
                    f"Pattern: `{pattern}`\n"
                    f"Target: {', '.join(target_files)}\n\n"
                    f"```diff\n{output}\n```"
                ),
                break_loop=False,
            )

        except asyncio.TimeoutError:
            return Response(
                message="Error: grit apply timed out after 120 seconds.",
                break_loop=False,
            )
        except FileNotFoundError:
            return Response(
                message=(
                    "Error: `grit` CLI not found. "
                    "Install it via `npm install -g @getgrit/cli` or check Docker setup."
                ),
                break_loop=False,
            )
        except Exception as e:
            logger.error(f"GritQL execution failed: {e}", exc_info=True)
            return Response(
                message=f"Error executing grit: {str(e)}", break_loop=False
            )
