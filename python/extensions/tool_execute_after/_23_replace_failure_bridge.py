"""
Replace Failure Bridge — tool_execute_after extension

RCA-241: Bridges the gap between replace_in_file failure detection and the
surgical edit enforcer's escape hatch.

Root Cause: The surgical edit enforcer (tool_execute_before/_23) has an escape
hatch that allows write_to_file after 3 replace_in_file failures. But
replace_in_file is in the tool failure tracker's EXCLUDED_TOOLS set, so its
errors are never detected, and record_replace_failure() is never called.

This creates a DEADLOCK:
  replace_in_file fails (search string mismatch) → agent tries write_to_file
  → surgical enforcer blocks it → escape hatch never fires → infinite loop.

Fix: This extension runs after every tool execution. When it detects a
replace_in_file error ("Could not find search string"), it increments the
surgical enforcer's replace failure counter. After 3 failures on the same
file, the escape hatch opens and write_to_file is allowed.

Priority: 23 (matches the surgical enforcer's priority for logical grouping)
Hook: tool_execute_after
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from python.helpers.extension import Extension

logger = logging.getLogger("agix.replace_failure_bridge")

# Pattern matching the exact error message from replace_in_file.py line 84
_REPLACE_FAILURE_PATTERN = re.compile(
    r"Error: Could not find search string\(s\) in file '([^']+)'"
)


def _is_replace_failure(message: Any) -> bool:
    """Check if a response message indicates a replace_in_file failure.

    Returns True if the message contains the "Could not find search string"
    error pattern emitted by replace_in_file.py.
    """
    if not message:
        return False
    text = str(message)
    return bool(_REPLACE_FAILURE_PATTERN.search(text))


def _extract_file_path_from_error(message: Any) -> Optional[str]:
    """Extract the file path from a replace_in_file error message.

    Returns the path string or None if no match.
    """
    if not message:
        return None
    text = str(message)
    match = _REPLACE_FAILURE_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


class ReplaceFailureBridge(Extension):
    """Bridges replace_in_file failures to the surgical edit enforcer's escape hatch.

    On every tool_execute_after, checks if:
    1. The tool was replace_in_file
    2. The response indicates a "Could not find search string" error
    3. If so, finds the SurgicalEditEnforcer instance and calls
       record_replace_failure() on the file path

    This ensures the escape hatch counter increments, preventing the
    deadlock where the agent can neither replace nor write.
    """

    def __init__(self, agent):
        super().__init__(agent)
        self._enforcer_instance = None  # Lazy-discovered

    def _find_enforcer(self):
        """Find or create a SurgicalEditEnforcer for this agent's context.

        RCA-287: The extension framework creates new instances on every
        call_extensions() call, so there's no persistent instance to find
        in agent._extensions. Instead, we directly instantiate the enforcer.
        Since RCA-287 moved state to module-level dicts keyed by context ID,
        any new instance automatically accesses the shared state.
        """
        if self._enforcer_instance is not None:
            return self._enforcer_instance

        try:
            from python.extensions.tool_execute_before._23_surgical_edit_enforcer import (
                SurgicalEditEnforcer,
            )

            # RCA-287: Directly instantiate — module-level state is shared
            self._enforcer_instance = SurgicalEditEnforcer(agent=self.agent)
            return self._enforcer_instance

        except ImportError:
            logger.warning("[REPLACE BRIDGE] SurgicalEditEnforcer module not found")

        return None

    async def execute(
        self,
        tool_name: str = "",
        response: Any = None,
        **kwargs,
    ) -> Optional[Any]:
        """Check if replace_in_file failed and forward to the surgical enforcer."""
        # Only act on replace_in_file
        if not tool_name or tool_name.lower() != "replace_in_file":
            return None

        if response is None:
            return None

        # Extract response message
        msg = ""
        if hasattr(response, "message") and response.message:
            msg = str(response.message)
        elif isinstance(response, str):
            msg = response

        if not msg:
            return None

        # Check if this is a "Could not find search string" failure
        if not _is_replace_failure(msg):
            return None

        # Extract the file path
        file_path = _extract_file_path_from_error(msg)
        if not file_path:
            return None

        # Find the surgical edit enforcer and record the failure
        enforcer = self._find_enforcer()
        basename = os.path.basename(file_path)

        if enforcer is not None:
            enforcer.record_replace_failure(file_path)
            normalized = os.path.normpath(file_path)
            fail_count = enforcer._replace_failures.get(normalized, 0)

            from python.extensions.tool_execute_before._23_surgical_edit_enforcer import (
                REPLACE_FAILURE_ESCAPE_THRESHOLD,
            )

            logger.info(
                f"[REPLACE BRIDGE] Forwarded replace_in_file failure for "
                f"'{basename}' to surgical edit enforcer escape hatch "
                f"({fail_count}/{REPLACE_FAILURE_ESCAPE_THRESHOLD})"
            )

            # ── Inject agent-facing guidance ──
            if fail_count >= REPLACE_FAILURE_ESCAPE_THRESHOLD:
                # Escape hatch is NOW open — tell the agent
                guidance = (
                    f"✅ ESCAPE HATCH OPEN: `replace_in_file` has failed "
                    f"{fail_count}x on `{basename}`. The surgical edit enforcer "
                    f"will now ALLOW `write_to_file` on this file as a last resort.\n\n"
                    f"**Action:** Use `read_file` to read the current file content, "
                    f"then use `write_to_file` to write the corrected version."
                )
            else:
                remaining = REPLACE_FAILURE_ESCAPE_THRESHOLD - fail_count
                guidance = (
                    f"⚠️ REPLACE FAILURE ({fail_count}/{REPLACE_FAILURE_ESCAPE_THRESHOLD}): "
                    f"Your search string did NOT match the actual content of `{basename}`.\n\n"
                    f"**Before retrying**, you MUST:\n"
                    f"1. Use `read_file` on `{file_path}` to see the ACTUAL current content\n"
                    f"2. Copy the EXACT text you want to replace (including whitespace)\n"
                    f"3. Then call `replace_in_file` with the correct search string\n\n"
                    f"After {remaining} more failure(s), `write_to_file` will be unlocked "
                    f"as a last resort."
                )

            try:
                await self.agent.hist_add_warning(guidance)
            except Exception:
                pass  # Guidance injection must never break tool execution

        else:
            logger.warning(
                f"[REPLACE BRIDGE] replace_in_file failed on '{basename}' "
                f"but SurgicalEditEnforcer not found — escape hatch inactive"
            )

        return None
