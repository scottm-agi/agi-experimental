"""
Agent Error Handler — Extracted from agent.py (Issue #778)

Contains handle_critical_exception_impl and format_rate_limit_message,
moving ~190 lines out of agent.py.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from python.helpers.strings import truncate_text_by_ratio

if TYPE_CHECKING:
    from python.agent import Agent

from python.helpers.agent_core import HandledException, AgentContextType
from python.helpers.errors import RepairableException, InterventionException
from python.helpers.print_style import PrintStyle
from python.helpers.circuit_breaker import CircuitBreakerError
from python.models import _is_rate_limit_error, ProviderConfigurationError

import python.helpers.errors as errors
from python.helpers.errors import TruncationException
import python.helpers.event_bus as event_bus

logger = logging.getLogger(__name__)


def _is_malformed_function_call(exception: Exception) -> bool:
    """Detect MALFORMED_FUNCTION_CALL errors from Gemini via OpenRouter.

    These occur when the model produces invalid tool call JSON mid-stream.
    The error propagates as ServiceUnavailableError → MidStreamFallbackError
    with native_finish_reason='MALFORMED_FUNCTION_CALL' in the error string.
    """
    return "MALFORMED_FUNCTION_CALL" in str(exception)


def _extract_model_from_error(exception: Exception) -> str:
    """Extract the model name from an LLM error message, or return 'unknown'."""
    import re
    error_str = str(exception)
    match = re.search(r"model='([^']+)'", error_str)
    if match:
        return match.group(1)
    match = re.search(r"model=([^\s,]+)", error_str)
    if match:
        return match.group(1)
    return "unknown"


def format_rate_limit_message(exception: Exception) -> str:
    """Format a friendly rate limit message for the UI."""
    error_str = str(exception).lower()

    if "too many tokens" in error_str or "tpm" in error_str:
        return "Token rate limit reached. Waiting for quota to reset..."
    elif "too many requests" in error_str or "rpm" in error_str:
        return "Request rate limit reached. Waiting before retry..."
    elif "429" in error_str:
        return "API rate limited (429). Backing off and retrying..."
    elif "quota" in error_str:
        return "API quota exceeded. Waiting for reset..."
    else:
        return "Rate limit encountered. Retrying with backoff..."


async def handle_critical_exception_impl(agent: "Agent", exception: Exception):
    """Handle critical exceptions that may require user intervention or automated recovery."""
    from python.helpers.context_error_recovery import detect_context_error, get_recovery_handler

    if detect_context_error(exception):
        handler = get_recovery_handler()
        await handler._condense_for_recovery(agent, error=exception)

        msg = "Context overflow recovered. History condensed. Please continue with your task."
        if agent.context.type in (AgentContextType.TASK, AgentContextType.BACKGROUND):
            agent.log(type="info", heading="🔄 Context Condensed", content=msg, notify=False)
            raise RepairableException(msg)

        raise RepairableException(msg)
    elif isinstance(exception, (HandledException, InterventionException)):
        raise exception
    elif isinstance(exception, asyncio.CancelledError):
        PrintStyle(font_color="white", background_color="red", padding=True).print(
            f"Context {agent.context.id} terminated during message loop"
        )
        raise HandledException(exception)
    elif isinstance(exception, ProviderConfigurationError):
        friendly_message = str(exception)
        PrintStyle(font_color="yellow", padding=True).print(
            f"{agent.agent_name}: {friendly_message}"
        )
        provider = getattr(getattr(agent.config, "embeddings_model", None), "provider", "")
        agent.log(
            type="error",
            heading="Model provider configuration error",
            content=friendly_message,
            kvps={"provider": provider},
        )
        raise HandledException(exception)
    elif _is_rate_limit_error(exception):
        error_text = errors.error_text(exception)
        friendly_msg = format_rate_limit_message(exception)
        PrintStyle(font_color="yellow", padding=True).print(
            f"{agent.agent_name}: {friendly_msg}"
        )
        agent.log(
            type="info",
            heading="⏳ Rate Limit - Retrying",
            content=friendly_msg,
            kvps={"details": error_text[:500]},
        )
        raise exception
    elif isinstance(exception, CircuitBreakerError):
        friendly_msg = f"System protection active: {str(exception)}"
        PrintStyle(font_color="yellow", padding=True).print(
            f"{agent.agent_name}: {friendly_msg}"
        )
        agent.log(
            type="warning",
            heading="🛡️ Circuit Breaker Active",
            content=f"{friendly_msg}\n\nThe system is temporarily blocking requests to protect against overload. Please wait or retry later.",
        )
        raise exception
    elif "Model repetition detected" in str(exception) or "repetition detected" in str(exception).lower():
        # ═══════════════════════════════════════════════════════════════
        # Progressive Repetition Recovery (P0)
        # Instead of sending the same static hint every time (which
        # caused 381 repetition events in the smoke test), we escalate
        # through a 5-layer recovery ladder:
        #   1-2: Text hint (progressively stronger)
        #   3: Temperature bump (+0.15)
        #   4: Context condensation
        #   5: History truncation (keep last 4 messages)
        #   6+: Hard stop
        # ═══════════════════════════════════════════════════════════════
        from python.helpers.repetition_recovery import (
            RepetitionRecoveryManager,
            increment_attempt,
            get_attempt,
            set_temp_override,
            clear_temp_override,
        )
        mgr = RepetitionRecoveryManager()
        attempt = increment_attempt(agent.data)
        strategy = mgr.get_recovery_strategy(attempt)
        action = strategy["action"]
        advice = strategy["advice"]

        PrintStyle(font_color="yellow", padding=True).print(
            f"{agent.agent_name}: Model repetition (attempt {attempt}). "
            f"Recovery action: {action}"
        )
        agent.log(
            type="warning",
            heading=f"🔄 Repetition Recovery — {action} (attempt {attempt})",
            content=advice,
        )

        if action == "hard_stop":
            # Terminal — agent loop must stop
            raise HandledException(
                Exception(f"Repetition hard stop after {attempt} attempts: {advice}")
            )

        if action == "temp_bump":
            # Set temporary temperature override for next LLM call
            set_temp_override(agent.data, strategy["temp_delta"])

        if action == "condense":
            # Force context condensation to break the repetitive pattern
            try:
                handler = get_recovery_handler()
                await handler._condense_for_recovery(agent, error=exception)
            except Exception as cond_err:
                logger.warning(f"Condense recovery failed: {cond_err}")

        if action == "truncate":
            # Aggressive history truncation — keep only recent messages
            keep_last = strategy["keep_last"]
            try:
                agent.history.prune_to_turns(keep_last)
            except Exception as trunc_err:
                logger.warning(f"Truncate recovery failed: {trunc_err}")

        raise RepairableException(advice)
    elif isinstance(exception, TruncationException):
        # RECOVERY: Save partial response to deliverable, tell agent to summarize
        partial = getattr(exception, 'partial_response', '') or ''
        model_name = getattr(exception, 'model', 'unknown')
        
        PrintStyle(font_color="yellow", padding=True).print(
            f"{agent.agent_name}: Response truncated by {model_name}. Recovering partial data..."
        )
        
        # Try to save partial data as a deliverable so it's not lost
        recovery_hint = (
            "Your response was too long and got truncated. "
            "CRITICAL: Write only ONE file at a time per turn to avoid truncation. "
            "Do NOT try to write multiple files in a single response — each file "
            "must be a separate tool call in a separate turn. "
            "Steps to recover: "
            "1) Identify which file you were writing when truncation occurred. "
            "2) If the file is a CODE file (*.ts, *.tsx, *.py, *.js, etc.), "
            "refactor into MODULAR files under 500 lines each. Do NOT write monolithic "
            "files — split into separate modules (e.g., lib/discovery.ts, lib/outreach.ts, "
            "components/Dashboard.tsx, components/ReviewForm.tsx). "
            "3) If the file is a DOC/SPEC file (*.md, *.json, docs/*), length is fine — "
            "write it in one go with write_to_file. Docs can be 5000+ lines. "
            "4) Use `response` with a SHORT summary (under 2000 chars) referencing saved files. "
            "NEVER put file content in your response text or in code_execution_tool heredocs — "
            "this consumes output tokens and will truncate again."
        )
        
        if partial and len(partial) > 100:
            recovery_hint += f"\n\nYour partial response ({len(partial)} chars) was preserved. Key content:\n{partial[:500]}..."
        
        # Inject file-read context so the agent doesn't re-read files it already has
        try:
            from python.helpers.file_read_tracker import build_recovery_read_context
            read_context = build_recovery_read_context(agent.data)
            if read_context:
                recovery_hint += read_context
        except Exception:
            pass  # File-read context is best-effort — never break recovery
        
        agent.log(
            type="warning",
            heading="⚠️ Response Truncated — Saving to File",
            content=f"The AI's response exceeded the model's output limit. Instructing agent to save data to a deliverable file first.",
        )
        
        raise RepairableException(recovery_hint)
    elif _is_malformed_function_call(exception):
        # ═══════════════════════════════════════════════════════════════
        # MALFORMED_FUNCTION_CALL Recovery (Issue #1119)
        # Gemini sometimes produces invalid tool call JSON. When all
        # retries exhaust, the raw traceback is massive (~3000+ chars
        # of repeated chunk data). Instead of dumping it into the UI
        # and agent context (which causes a cascade: massive error →
        # context overflow → timeout → irrelevant supervisor redirect),
        # we handle it cleanly with a short recovery message.
        # ═══════════════════════════════════════════════════════════════
        model_name = _extract_model_from_error(exception)
        PrintStyle(font_color="yellow", padding=True).print(
            f"{agent.agent_name}: Model returned malformed function call (retries exhausted). Recovering..."
        )
        agent.log(
            type="warning",
            heading="⚠️ Model Error — Malformed Function Call",
            content=(
                f"The AI model ({model_name}) produced invalid tool call JSON and "
                f"all retry attempts were exhausted. This is a transient model-side "
                f"issue. The agent will retry with a fresh attempt."
            ),
        )
        raise RepairableException(
            f"The AI model ({model_name}) returned a malformed function call. "
            f"This is a transient error — retry your current task immediately. "
            f"Do NOT change your approach — the same request will likely succeed on retry. "
            f"Do NOT attempt to read or process any error logs."
        )
    else:
        error_text = errors.error_text(exception)
        full_traceback = errors.format_error(exception)
        short_error = errors.get_short_error(exception)

        error_heading = "Error"
        ui_content = short_error

        try:
            summary_input = f"Error: {error_text}\nStack Trace: {full_traceback[:1000]}"
            summary_prompt = (
                "Summarize this technical error into a single, concise sentence "
                "for a non-technical user. Focus on WHAT went wrong. "
                f"\n\n{summary_input}"
            )
            summary = await agent.call_utility_model(
                system="You are an expert at simplifying complex technical error messages.",
                message=summary_prompt,
            )
            if summary and len(summary) < 200:
                summary_text = summary.strip()
                error_heading = f"Error: {summary_text}"
                ui_content = summary_text
        except Exception as e:
            logger.debug(f"Error summarization failed: {e}")

        PrintStyle(font_color="red", padding=True).print(full_traceback)

        agent.log(
            type="error",
            heading=error_heading,
            content=ui_content,
            kvps={
                "text": error_text,
                "technical_details": full_traceback
            },
        )
        PrintStyle(font_color="red", padding=True).print(
            f"{agent.agent_name}: {error_text}"
        )

        if "recent_errors" not in agent.data:
            agent.data["recent_errors"] = []
        # Middle-out truncate traceback stored in agent.data to prevent context pollution.
        # Full tracebacks (3000+ chars) in recent_errors get injected into the
        # agent's prompt on next iteration, causing timeouts and irrelevant
        # supervisor redirects about "excessively large input logs".
        # Uses middle-out strategy: keeps head (error type) + tail (root cause)
        # while removing the repeated middle (chunk data, nested exceptions).
        from python.helpers.output_truncation import truncate_output_middle_out
        truncated_traceback = truncate_output_middle_out(
            full_traceback, max_lines=15, max_chars=800, head_ratio=0.3
        )
        agent.data["recent_errors"].append({
            "error": truncated_traceback,
            "text": truncate_output_middle_out(str(error_text), max_lines=5, max_chars=500, head_ratio=0.3),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        agent.data["recent_errors"] = agent.data["recent_errors"][-10:]

        # Also write to the universal ErrorLedger for prompt injection
        try:
            from python.helpers.error_ledger import get_error_ledger, ErrorEntry
            from python.helpers.output_truncation import truncate_output_middle_out
            context_id = agent.context.id if agent.context else None
            if context_id:
                get_error_ledger().record(context_id, ErrorEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="llm",
                    severity="high",
                    summary=truncate_output_middle_out(str(ui_content), max_lines=5, max_chars=200, head_ratio=0.5),
                    details=truncate_output_middle_out(str(error_text), max_lines=20, max_chars=500, head_ratio=0.3),
                    five_why_hint="Analyze the error root cause before retrying. Change your approach if the same error repeats.",
                ))
        except Exception:
            pass  # ErrorLedger recording must never break the main flow

        event_bus.get_event_bus().publish_sync(event_bus.AgentSignal(
            signal_type=event_bus.SignalType.AGENT_ERROR,
            agent_id=agent.agent_name,
            context_id=agent.context.id if agent.context else "N/A",
            timestamp=datetime.now(timezone.utc),
            severity="high",
            error_message=str(ui_content)[:500],
            iteration=getattr(agent.loop_data, 'iteration', 0)
        ))

        raise HandledException(exception)
