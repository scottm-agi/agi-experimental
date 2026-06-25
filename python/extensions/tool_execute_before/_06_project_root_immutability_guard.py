"""
Project Root Immutability Guard — tool_execute_before extension.

U-10 Fix: Framework path-safety was designed for containment (keep files
inside sandbox) but has zero protection for stability (sandbox itself must
not move). No guard intercepted mv/os.rename/shutil.move on the project root.

This guard blocks any code_execution_tool command that would rename, move,
or delete the project root directory.
"""
from __future__ import annotations

import logging
import re
from python.helpers.extension import Extension
from python.helpers.tool import Response


logger = logging.getLogger("agix.project_root_immutability")

# Patterns that indicate a command targets the project root for rename/move/delete
_DANGEROUS_PATTERNS = [
    re.compile(r'\bmv\s+(?:--\S+\s+)*["\']?(/agix/usr/projects/[^/\s"\']+)["\']?\s', re.IGNORECASE),
    re.compile(r'\brename\s.*(/agix/usr/projects/[^/\s"\']+)', re.IGNORECASE),
    re.compile(r'\brm\s+(?:-rf?\s+|-fr?\s+)(?:--\S+\s+)*["\']?(/agix/usr/projects/[^/\s"\']+)["\']?\s*$', re.IGNORECASE),
    re.compile(r'shutil\.(?:move|rmtree)\s*\(["\']?(/agix/usr/projects/[^/\s"\']+)', re.IGNORECASE),
    re.compile(r'os\.rename\s*\(["\']?(/agix/usr/projects/[^/\s"\']+)', re.IGNORECASE),
]


class ProjectRootImmutabilityGuard(Extension):
    """Block commands that would rename, move, or delete the project root.

    U-10 Fix: _active_project_dir is set once at init and never re-validated.
    If the project root is moved, all subsequent file operations fail silently
    or fall back to /agix (code_execution.py:770-773).
    """

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        if not tool_name:
            return

        if tool_name.lower() != "code_execution_tool":
            return

        if not tool_args or not isinstance(tool_args, dict):
            return

        code = tool_args.get("code", "") or tool_args.get("runtime_code", "")
        if not code:
            return

        # Get active project dir
        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir:
            return

        # Check for dangerous patterns targeting the project root
        for pattern in _DANGEROUS_PATTERNS:
            match = pattern.search(code)
            if match:
                target_path = match.group(1)
                # Only block if the target is the project root itself
                # (not subdirectories like /agix/usr/projects/myapp/src)
                if target_path.rstrip("/") == project_dir.rstrip("/"):


                    logger.error(
                        f"[PROJECT ROOT IMMUTABILITY] BLOCKED: command targets "
                        f"project root '{target_path}'. Pattern: {pattern.pattern}"
                    )
                    return Response(
                        message=(
                            f"🛑 BLOCKED: You cannot rename, move, or delete the project root "
                            f"directory '{target_path}'. The project root is IMMUTABLE during "
                            f"execution. If you need to reorganize files, work WITHIN the "
                            f"project directory instead."
                        ),
                        break_loop=False,
                    )
