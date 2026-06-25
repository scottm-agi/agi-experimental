"""
Response Size Guard Extension — deterministic save-before-respond enforcement.

When a subordinate agent calls the `response` tool with content exceeding
RESPONSE_SIZE_THRESHOLD characters, this extension intercepts the call and
redirects the agent to use `save_deliverable` first, then respond with a
short reference to the saved file.

This is the deterministic Layer 1 enforcement that prevents the reactive
"Response Truncated" recovery from ever firing. The truncation recovery
handler in agent_error_handler.py remains as a Layer 2 fail-safe.

RCA-294: Response Truncated (52K chars) — subordinate code agent tried to
return full file listings, code snippets, build output, and verbose
explanations in a single `response` tool call because there was no
proactive size gate. The truncation recovery (reactive) wasted a full
turn + token spend. This extension prevents the waste by blocking the
oversized response BEFORE it hits the model's output limit.

Applies to: ALL agent profiles (universal — in core tool_execute_before).
Priority: 29 (after surgical edit enforcer at 23, before production sandbox at 30).
"""
from __future__ import annotations

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.response_size_guard")

# Responses over this threshold get redirected to save_deliverable.
# 4000 chars ≈ 1000 tokens ≈ 3.1% of output budget.
# Rationale: 2K was too tight — a code agent listing 5+ modified files with
# concise descriptions legitimately hits 1500-2000 chars. 4K prevents the
# real problem (52K inline dumps causing truncation) without triggering on
# legitimate concise-but-specific summaries.
RESPONSE_SIZE_THRESHOLD = 4000


class ResponseSizeGuard(Extension):
    # Context-aware: subordinate agents responding
    TOOLS = frozenset({"response"})

    """Intercepts oversized `response` tool calls and redirects to save_deliverable.

    When a subordinate agent tries to return >4K chars of content via the
    `response` tool, this extension blocks the call and tells the agent to:
    1. Save the detailed content via `save_deliverable`
    2. Then call `response` with a short summary + file reference

    This is deterministic — no LLM judgment required. The threshold is
    char-based, not token-based, for predictable behavior.

    Only applies to subordinate agents (agent.number > 0). Top-level agents
    (agent.number == 0) talking to the user are exempt — their responses
    should be as detailed as the user needs.
    """

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        if tool_name != "response":
            return None

        if not tool_args:
            return None

        # Only apply to subordinate agents — top-level agents talk to users
        if self.agent.number == 0:
            return None

        message = tool_args.get("message", "")
        if not message:
            return None

        content_length = len(message)
        if content_length <= RESPONSE_SIZE_THRESHOLD:
            return None  # Under threshold, proceed normally

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "response_size_guard"):
            return None

        # ── Oversized response detected ──────────────────────────────
        profile = getattr(self.agent.config, "profile", None) or "default"
        logger.warning(
            f"[RESPONSE_SIZE_GUARD] Agent #{self.agent.number} ({profile}) "
            f"attempted response of {content_length:,} chars "
            f"(threshold: {RESPONSE_SIZE_THRESHOLD:,}). Redirecting to "
            f"save_deliverable."
        )

        redirect_message = (
            f"🚫 **RESPONSE TOO LARGE** — Your response is {content_length:,} "
            f"characters ({content_length // 4:,} est. tokens). The maximum "
            f"for a subordinate `response` is {RESPONSE_SIZE_THRESHOLD:,} chars.\n\n"
            f"**Action required:**\n"
            f"1. Call `save_deliverable` with your detailed output "
            f"(title: a descriptive name for this deliverable)\n"
            f"2. Then call `response` with a **CONCISE but SPECIFIC** summary "
            f"(under {RESPONSE_SIZE_THRESHOLD:,} chars) that includes:\n"
            f"   - What was accomplished (1-2 specific sentences, not vague)\n"
            f"   - Files created/modified (exact paths)\n"
            f"   - **The deliverable file path** returned by `save_deliverable` "
            f"so the orchestrator can read your full output\n"
            f"   - Any issues, blockers, or incomplete items\n\n"
            f"Be concise but NEVER vague — the orchestrator needs specifics "
            f"to decide next steps. Always include the deliverable path."
        )

        return Response(
            message=redirect_message,
            break_loop=False,
        )
