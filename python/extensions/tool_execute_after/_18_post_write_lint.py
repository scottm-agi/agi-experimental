"""
ADR-009: Post-Write Lint Injection Extension

After write_to_file or replace_in_file completes, runs lint on the modified
file and appends diagnostics to the tool response. This gives agents immediate
feedback about syntax errors and style violations on the same turn.

Design decisions:
- Advisory only: Appends to response message, does not block
- Async: Uses asyncio subprocess to avoid blocking the event loop
- Scoped: Only triggers for JS/TS/JSX/TSX files in project directories
- Graceful: Silently skips if lint is unavailable or fails
"""
from __future__ import annotations
import os
import asyncio
import logging

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers import projects

logger = logging.getLogger("agix.post_write_lint")

# Tools that write files
WRITE_TOOLS = {"write_to_file", "replace_in_file"}

# File extensions worth linting
LINTABLE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


class PostWriteLint(Extension):
    """Inject lint diagnostics after file write operations."""

    async def execute(self, response: Response | str | None = None, **kwargs):
        if not response or not hasattr(response, 'message'):
            return

        # Only trigger for file-writing tools
        tool_name = kwargs.get("tool_name", "")
        if tool_name not in WRITE_TOOLS:
            return

        # Get the file path from tool args
        tool_args = kwargs.get("tool_args", {})
        file_path = tool_args.get("path", "")
        if not file_path:
            return

        # Only lint JS/TS files
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in LINTABLE_EXTENSIONS:
            return

        # Only lint files in project directories
        project_name = projects.get_context_project_name(self.agent.context)
        if not project_name:
            return

        # Determine project root
        from python.helpers import files
        base = files.get_base_dir()
        project_root = os.path.join(base, "usr", "projects", project_name)
        if not os.path.isdir(project_root):
            return

        # Resolve absolute path
        if not os.path.isabs(file_path):
            abs_path = os.path.join(project_root, file_path)
        else:
            abs_path = file_path

        # Run lint asynchronously
        try:
            from python.helpers.lint_injector import get_file_diagnostics

            # ITR-29: Extract replace failure state from surgical edit enforcer
            # to break the lint/replace feedback trap. When replace_in_file has
            # failed on a file, lint should say "re-read" not "DO NOT re-read".
            failed_replace_files = _get_replace_failure_files(self.agent)

            diagnostics = await get_file_diagnostics(
                abs_path, project_root,
                failed_replace_files=failed_replace_files,
            )
            if diagnostics:
                response.message += (
                    f"\n\n📋 **Lint diagnostics** (fix these before proceeding):\n"
                    f"{diagnostics}"
                )
                logger.info(f"[POST-WRITE LINT] Injected {diagnostics.count(chr(10)) + 1} diagnostics for {os.path.basename(file_path)}")
        except Exception as e:
            # Graceful degradation — never block writes due to lint failures
            logger.debug(f"[POST-WRITE LINT] Skipped: {e}")


def _get_replace_failure_files(agent) -> set:
    """Extract file paths with replace_in_file failures from surgical enforcer.

    ITR-29: Searches the agent's before-extensions for the SurgicalEditEnforcer
    and returns the set of file paths that have replace failures. Returns
    empty set if enforcer not found or has no failures.

    Args:
        agent: The agent instance.

    Returns:
        Set of file paths (normalized) with replace_in_file failures.
    """
    try:
        for ext in getattr(agent, "_before_extensions", []):
            if type(ext).__name__ == "SurgicalEditEnforcer":
                failures = getattr(ext, "_replace_failures", {})
                return set(failures.keys()) if failures else set()
    except Exception:
        pass
    return set()

