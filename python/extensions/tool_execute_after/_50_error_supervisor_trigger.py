"""
Error Supervisor Trigger — tool_execute_after extension (order 50)

SIMPLIFIED (#1185): Only handles supervisor event bus notification for Python
exceptions. All error pattern detection is done by _12_tool_failure_tracker.py
(order 12). System timeout detection is done by _12's timeout tracking.

Runs AFTER the tracker (order 50 > 12), so it can read tracker results.

Previous version: 409 lines with duplicated error pattern detection.
This version: ~60 lines — single responsibility (supervisor notification).

Hooks into: tool_execute_after (order 50)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from python.helpers.extension import Extension

logger = logging.getLogger("agix.error_supervisor_trigger")


class ErrorSupervisorTrigger(Extension):
    """Notify the supervisor event bus when Python exceptions occur in tools.

    Logical errors (tool returns "Error: ...") are handled by
    _12_tool_failure_tracker.py + ErrorLedger → _76_error_awareness.py.
    This extension ONLY fires on uncaught exceptions that bubble up.
    """

    async def execute(
        self,
        tool_name: str = "",
        response: Any = None,
        error: Optional[Exception] = None,
        **kwargs,
    ) -> None:
        # Only fire on actual Python exceptions — logical errors are
        # handled by _12_tool_failure_tracker's ErrorLedger recording
        if error is None:
            return None

        try:
            from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
            from python.helpers.output_truncation import truncate_output_middle_out
            import traceback

            error_type = type(error).__name__
            error_msg = str(error)
            error_tb = traceback.format_exc()

            signal = AgentSignal(
                signal_type=SignalType.AGENT_ERROR,
                agent_id=str(getattr(self.agent, "number", 0)),
                context_id=(
                    self.agent.context.id if self.agent.context else "unknown"
                ),
                timestamp=datetime.now(timezone.utc),
                severity="critical",
                details={
                    "error_type": error_type,
                    "error_message": truncate_output_middle_out(
                        error_msg, max_lines=10, max_chars=500, head_ratio=0.5
                    ),
                    "tool_name": tool_name,
                    "traceback": truncate_output_middle_out(
                        error_tb, max_lines=30, max_chars=2000, head_ratio=0.3
                    ),
                    "source": "tool_execute_after",
                },
                tool_name=tool_name,
                error_message=error_msg[:500],
            )

            event_bus = get_event_bus()
            await event_bus.publish(signal)

            logger.info(
                f"[SUPERVISOR TRIGGER] {self.agent.agent_name}: "
                f"Exception in {tool_name} ({error_type}) reported to supervisor"
            )

        except Exception as e:
            logger.debug(f"Supervisor notification failed: {e}")

        return None
