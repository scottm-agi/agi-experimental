"""
Error Awareness Injector — message_loop_prompts_after extension (order 76).

Injects the ErrorLedger's prompt injection into the agent's extras on every
LLM turn. Zero overhead when no errors exist (render returns "").

Position 76 places it after project extras (75) and before depth
instructions (80), so the agent sees error context alongside project state.
"""
from __future__ import annotations

from python.helpers.extension import Extension
from python.agent import LoopData
from python.helpers.error_ledger import get_error_ledger


class ErrorAwarenessInjector(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        context_id = self.agent.context.id if self.agent.context else None
        if not context_id:
            return

        ledger = get_error_ledger()
        prompt_injection = ledger.render_prompt_injection(context_id)

        if prompt_injection:
            loop_data.extras_temporary["error_awareness"] = prompt_injection
