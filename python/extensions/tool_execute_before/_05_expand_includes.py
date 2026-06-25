"""
CRITICAL FIX: Expand §§include() placeholders in tool args BEFORE tool execution.

Root Cause (Issue traced 2026-01-19):
- Agent uses §§include(<path>) to inline file contents in tool arguments
- These files may be project deliverables, docs, or other project-scoped files
- Individual action handlers (e.g., _comment_github) try to expand, but timing issues exist

Solution:
- Expand §§include() at the earliest possible point: tool_execute_before hook
- This ensures all tool args have expanded content before any tool runs
- Falls back gracefully with [Content unavailable] if file doesn't exist

ADR-83 NOTE: §§include paths should ONLY reference files within the agent's
_active_project_dir (Zone 2). Framework-internal paths like /agix/tmp/chats/
(Zone 1) must NEVER appear in §§include references — those are for framework
code only, not agent-visible content.
"""

from __future__ import annotations
from typing import Any
from python.helpers.extension import Extension
from python.helpers.strings import replace_file_includes
import logging

logger = logging.getLogger("extensions.expand_includes")


class ExpandIncludes(Extension):
    """
    Expand §§include() placeholders in tool arguments before tool execution.
    
    This runs BEFORE any tool executes, ensuring that file includes are expanded
    into actual content. This is critical for tools that post to external APIs
    (GitHub, Forgejo) where raw §§include() macros would be confusing.
    """

    async def execute(self, **kwargs):
        tool_args = kwargs.get("tool_args")
        if not tool_args:
            return

        tool_name = kwargs.get("tool_name", "")
        # Expand §§include() in all string arguments
        for key, value in tool_args.items():
            if isinstance(value, str) and "§§include(" in value:
                original_len = len(value)
                expanded = replace_file_includes(value, wrap_in_markers=(tool_name == "call_subordinate"))
                
                # Log if expansion happened
                if expanded != value:
                    new_len = len(expanded)
                    if "[Content unavailable:" in expanded:
                        logger.warning(f"[ExpandIncludes] Failed to expand §§include in arg '{key}' - file not found")
                    else:
                        logger.debug(f"[ExpandIncludes] Expanded §§include in arg '{key}': {original_len} -> {new_len} chars")
                
                tool_args[key] = expanded
