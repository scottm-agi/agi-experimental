from __future__ import annotations
"""
Tool Output Fidelity — Response Verification Extension (LEGACY)

DEPRECATED: Active fidelity checking has been moved to 
tool_execute_after/_20_response_fidelity_gate.py which intercepts the 
'response' tool BEFORE break_loop sends the response.

This message_loop_end extension fires TOO LATE — after the response
is already returned. Kept as a logging-only fallback for monitoring.

See Issue #789 for the full fix history.
"""

import logging
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.tool_fidelity_check")


class ToolFidelityCheck(Extension):
    """Legacy fidelity check — logging only. Active check is in _20_response_fidelity_gate."""

    async def execute(self, loop_data: Any = None, **kwargs):
        """Log fidelity status at message_loop_end (monitoring only)."""
        if not loop_data:
            return

        # Just log — active checking is done in _20_response_fidelity_gate.py
        anchors = self.agent.data.get("tool_data_anchors", [])
        if anchors:
            logger.debug(
                f"[FIDELITY CHECK] {len(anchors)} remaining anchors at message_loop_end "
                f"(active check handled by response_fidelity_gate)"
            )
