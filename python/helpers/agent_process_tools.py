"""
Agent Tool Processing — Extracted from agent.py (Issue #778)

Contains the process_tools_impl function, moving ~477 lines out of agent.py.
This handles tool call parsing, deduplication, MCP tool resolution, and execution.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from python.agent import Agent

from python.helpers import (
    extract_tools,
    errors,
    tokens,
    dirty_json,
    tool_registry,
    event_bus,
    settings,
)
from python.helpers.agent_core import AgentContextType, LoopData
from python.helpers.errors import InterventionException
from python.helpers.print_style import PrintStyle
from python.helpers.dirty_json import DirtyJson
from python.helpers.redis_history import get_redis_history_helper
from python.helpers.observer_mesh import ObserverMesh
from python.helpers.tool_cache import ToolResultCache

logger = logging.getLogger(__name__)

# RCA-309: Maximum number of tool calls to execute from a single LLM response.
# Batches exceeding this are truncated; overflow tools are deferred to the next turn.
# Prevents context bloat, cascade errors, and feedback-loop bypass.
MAX_BATCH_TOOLS = 8


def _build_batch_fence_notice(
    deferred_names: list[str],
    planning_count: int,
) -> str:
    """Build the batch fence deferral notice with actionable guidance.

    F-4 (ITR-51): The original notice was too terse — the LLM kept
    co-submitting planning+execution tools. This version includes:
    - The standalone delegation rule (F-16 from fullstack-dev SKILL)
    - Iteration waste warning to motivate compliance
    - Correct vs incorrect pattern examples
    """
    deferred_str = ", ".join(f"`{n}`" for n in deferred_names)
    return (
        f"Mixed planning+execution batch detected. "
        f"Executed {planning_count} planning tool(s). "
        f"Deferred {len(deferred_names)} execution tool(s): {deferred_str}.\n\n"
        f"**STANDALONE DELEGATION RULE (F-16)**: Delegation tools "
        f"(`call_subordinate`, `call_subordinate_batch`) MUST be the ONLY "
        f"tool in your next response. Do NOT combine them with `requirements`, "
        f"`sequential_thinking`, or any other tool.\n\n"
        f"**On your next turn**: Submit ONLY {deferred_str} as a standalone "
        f"tool call — no other tools in the same response.\n\n"
        f"**Why this matters**: Each fence activation wastes an iteration "
        f"(~30-60 seconds). Submit delegation standalone to avoid the fence."
    )


def _build_batch_fence_auto_execute_notice(consecutive_count: int) -> str:
    """Build the auto-execute notice after 3+ consecutive fences.

    F-4/F-15: After 3 consecutive fences, we force-execute the deferred
    tools to break the loop. This message explains what happened.
    """
    return (
        f"Batch fence triggered {consecutive_count} consecutive times. "
        f"Auto-executing all deferred tools to break the loop.\n\n"
        f"**Action required**: On future delegations, submit "
        f"`call_subordinate` as the ONLY tool in your response. "
        f"Never combine it with planning tools like `requirements` "
        f"or `sequential_thinking`. This costs {consecutive_count} "
        f"wasted iterations each time."
    )


def _format_no_output_summary(tool_names: list[str]) -> str | None:
    """Collapse multiple 'no output' messages into a single summary line.

    RCA-365 F-5: Prevents N× repetitive 'executed but returned no output'
    messages from polluting the context window.

    Returns None if no tools returned no output.
    """
    if not tool_names:
        return None
    count = len(tool_names)
    names_str = ", ".join(f"'{n}'" for n in tool_names)
    return f"{count} tool(s) executed but returned no output: {names_str}"


# ── F-6: Per-tool checkpoint ─────────────────────────────────────────────
# Maximum chars for checkpoint result content to prevent oversized history entries
_CHECKPOINT_MAX_CHARS = 1000


def checkpoint_tool_result(
    tool_name: str,
    result: Any,
    tool_index: int,
    total_tools: int,
) -> str:
    """Format a per-tool checkpoint message for immediate history persistence.

    F-6: In multi-tool batches, results are accumulated in combined_results
    but only persisted to history AFTER the entire batch completes. If a crash
    occurs mid-batch, intermediate results are lost. This function formats
    individual tool results for immediate persistence via hist_add_warning.

    Args:
        tool_name: Name of the tool that executed.
        result: The tool's return value (str, Response, None, etc.).
        tool_index: 1-based index of this tool in the batch.
        total_tools: Total number of tools in the batch.

    Returns:
        Formatted checkpoint message string with [TOOL_RESULT] prefix.
    """
    result_str = str(result) if result is not None else "(no output)"

    # Truncate long results to prevent oversized history entries
    if len(result_str) > _CHECKPOINT_MAX_CHARS:
        result_str = result_str[:_CHECKPOINT_MAX_CHARS] + f"... [truncated {len(str(result)) - _CHECKPOINT_MAX_CHARS} chars]"

    return (
        f"[TOOL_RESULT] {tool_index}/{total_tools} '{tool_name}': {result_str}"
    )


# Recognized keys that indicate a parsed JSON dict is a tool call or thinking response.
# If a parsed dict lacks ALL of these keys, it's likely a non-tool-call JSON fragment
# (e.g., {{verbatim:...}} template syntax, code examples, or random data).
_TOOL_KEYS = frozenset({
    "tool_name", "name", "tool", "action", "command", "function_name",
    "tool_call", "function", "type", "tool_args", "parameters", "arguments",
    "args", "params", "input",
})
_THINKING_KEYS = frozenset({
    "thought", "thoughts", "thinking", "reasoning", "analysis",
    "observation", "headline",
    # sequential_thinking MCP tool response keys (RCA: LLM regurgitates these)
    "thoughtNumber", "totalThoughts", "nextThoughtNeeded",
    "isRevision", "revisesThought", "branchFromThought", "branchId",
})
_ALL_RECOGNIZED_KEYS = _TOOL_KEYS | _THINKING_KEYS


def _has_recognized_keys(parsed: dict) -> bool:
    """Check if a parsed JSON dict contains at least one recognized tool or thinking key.

    Returns True if the dict looks like a tool call or thinking response.
    Returns False if it's garbage JSON (e.g., from template syntax like {{verbatim:...}}).
    """
    if not parsed or not isinstance(parsed, dict):
        return False
    return bool(set(parsed.keys()) & _ALL_RECOGNIZED_KEYS)


def _ensure_string_message(value: Any) -> str:
    """Unwrap a value to a plain string, handling nested Response objects.

    RCA-330: code_execution.execute() wraps its return in Response(message=response),
    but some code paths (execute_terminal_command guards) already return a Response
    object, causing double-wrapping: Response(message=Response(message="...")).

    This function extracts the innermost string from any level of nesting.
    Used by code_execution.py to sanitize before wrapping in the final Response.
    """
    from python.helpers.tool import Response
    # Unwrap nested Response objects
    for _ in range(10):  # Safety limit to prevent infinite loops
        if isinstance(value, Response):
            value = value.message
        else:
            break
    # Coerce to string
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value


def _coerce_response_message(response: Any) -> Any:
    """Ensure response.message is a string, unwrapping nested Response objects.

    RCA-330 defense-in-depth: Even after fixing the root cause in code_execution.py,
    this guard protects agent_process_tools.py from any future tool that accidentally
    nests Response objects.

    Returns the same Response object with .message guaranteed to be a str.
    """
    from python.helpers.tool import Response
    if not isinstance(response, Response):
        return response
    if isinstance(response.message, Response):
        # Unwrap nested Response to innermost string
        response.message = _ensure_string_message(response.message)
    elif response.message is None:
        response.message = ""
    elif not isinstance(response.message, str):
        response.message = str(response.message)
    return response


# ── ISSUE-1: Fingerprint Repetition Skip (Claude Code pattern) ──────────
# Threshold for how many identical fingerprints in last 20 actions
# before we skip tool execution. Same as DETECTOR 1's normal threshold.
_FINGERPRINT_SKIP_THRESHOLD = 3


def check_fingerprint_repetition_skip(
    agent: "Agent",
    tool_name: str,
    tool_args: dict,
) -> "Response | None":
    """Check if a tool call should be SKIPPED due to fingerprint repetition.

    ISSUE-1: When L1 structural guards have already detected md5_repetition
    (stored in agent.data['_l2_escalation_signals']), AND the current tool
    call's fingerprint matches >= _FINGERPRINT_SKIP_THRESHOLD times in the
    last 20 entries of _md5_action_log, we SKIP execution.

    This forces the LLM to try a different approach instead of retrying
    the same failing tool call endlessly — matching Claude Code behaviour.

    Args:
        agent: The agent instance.
        tool_name: Name of the tool about to execute.
        tool_args: Arguments to the tool.

    Returns:
        A Response with skip message if the tool should be skipped,
        or None if execution should proceed normally.
    """
    from python.helpers.tool import Response
    from python.helpers.hashing import content_hash

    # Step 1: Check if L2 escalation signals contain md5_repetition
    _l2_signals = agent.data.get("_l2_escalation_signals", [])
    _has_md5_signal = any(
        isinstance(s, dict) and s.get("detector") == "md5_repetition"
        for s in _l2_signals
    )
    if not _has_md5_signal:
        return None  # No md5 repetition signal — allow execution

    # Step 2: Compute the fingerprint for the current tool call
    # Uses the same format as agent_process_tools.py line 1153:
    #   _fp_content = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
    #   agent.fingerprint_action("tool_call", _fp_content)
    # content_hash computes: md5(f"tool_call:{_fp_content}")
    _fp_content = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
    _fp_hash = content_hash(f"tool_call:{_fp_content}")

    # Step 3: Count how many times this fingerprint appears in last 20 actions
    _md5_log = getattr(agent, "_md5_action_log", [])
    _recent = _md5_log[-20:]
    _match_count = sum(
        1 for entry in _recent
        if isinstance(entry, dict) and entry.get("fingerprint") == _fp_hash
    )

    if _match_count < _FINGERPRINT_SKIP_THRESHOLD:
        return None  # Below threshold — allow execution

    # Step 4: SKIP the tool — build the response
    skip_msg = (
        f"[TOOL SKIPPED — REPEATED ACTION DETECTED]\n"
        f"The tool call '{tool_name}' with identical arguments was already "
        f"executed {_match_count}x with the same result each time.\n"
        f"This tool was NOT executed to break the loop.\n\n"
        f"You MUST try a DIFFERENT approach:\n"
        f"- Use a different tool\n"
        f"- Change the arguments substantially\n"
        f"- Ask for help via response tool\n"
        f"- Skip this requirement and move to the next one"
    )
    response = Response(message=skip_msg, break_loop=False)

    agent.log(
        type="warning",
        heading="🔄 Tool Skipped (Fingerprint Loop)",
        content=f"Skipped '{tool_name}' — {_match_count}x identical calls detected",
    )

    # Step 5: Still fingerprint this action so the count keeps going up
    # (escalation pressure — supervisor sees the count increasing)
    try:
        agent.fingerprint_action("tool_call_skipped", _fp_content)
    except Exception:
        pass  # Fingerprinting must never break the flow

    return response



async def _execute_single_tool_request(
    agent: "Agent",
    tool_request: dict,
    original_msg: str,
    msg_content: str,
) -> any:
    """Execute a single pre-parsed tool call through the standard pipeline.

    RCA-306: This helper is called once per tool call when the LLM batches
    multiple calls in a single response. It synthesizes a single-tool JSON
    string from the pre-parsed dict and feeds it through process_tools_impl's
    single-tool path, preserving all guards, dedup, and extensions.
    """
    import json
    # Serialize the single tool request back to JSON so the standard
    # single-tool path in process_tools_impl can process it normally.
    single_msg = json.dumps(tool_request, default=str)
    # Call process_tools_impl with a single-tool message — it will parse
    # exactly ONE tool call and execute it through the full pipeline.
    return await process_tools_impl(agent, single_msg)


async def process_tools_impl(agent: "Agent", msg: str):
    """Process tool requests from agent message. Returns final response or None.

    RCA-306: Now processes ALL tool calls in a single LLM response by using
    json_parse_dirty_all() and executing each sequentially. Previously only
    the first tool call was processed (rfind bug), silently dropping the rest.
    """
    from python.helpers.tool import Response

    msg_content = msg.strip() if msg else ""

    # RCA-306: Parse ALL tool calls from the message
    all_tool_requests = extract_tools.json_parse_dirty_all(msg) if msg else []

    if len(all_tool_requests) > 1:
        # RCA-309 / RCA-323b: When the LLM emits more than MAX_BATCH_TOOLS,
        # execute ALL of them sequentially — do NOT drop overflow tools.
        # The original design truncated to 8 and deferred the rest, but agents
        # never reliably re-submitted deferred tools (especially `response`),
        # causing infinite re-delegation loops and silent work loss.
        #
        # The sequential execution loop below already processes each tool one
        # at a time with crash isolation, so large batches are safe. We log an
        # informational warning so the LLM learns to batch smaller, but we
        # execute everything to prevent lost work.
        if len(all_tool_requests) > MAX_BATCH_TOOLS:
            original_count = len(all_tool_requests)
            num_chunks = (original_count + MAX_BATCH_TOOLS - 1) // MAX_BATCH_TOOLS
            agent.log(
                type="warning",
                heading="⚠️ Large Tool Batch (RCA-323b)",
                content=f"LLM emitted {original_count} tool calls — executing ALL "
                        f"in {num_chunks} sequential chunks of {MAX_BATCH_TOOLS}. "
                        f"Prefer ≤{MAX_BATCH_TOOLS} calls per response for faster feedback.",
            )


        agent.log(
            type="info",
            heading="🔧 Multi-Tool Execution (RCA-306)",
            content=f"Processing {len(all_tool_requests)} tool calls from single LLM response.",
        )

    # If multiple tool calls found, execute each sequentially and collect results.
    # COMPLETENESS INVARIANT: Every tool call MUST produce feedback to the LLM.
    # No silent drops — if a tool errors, crashes, or returns None, the LLM still
    # sees a result for that tool so it can retry or adapt.
    #
    # RCA-307 FIX: The multi-tool batch path MUST only return a truthy value when
    # a tool in the batch sets break_loop=True (i.e., the response tool). If no
    # tool sets break_loop, the batch results are injected into history as tool
    # feedback and this function returns None — signaling the monologue loop in
    # agent.py to CONTINUE (not exit). Previously, the combined result string was
    # always returned (truthy), which agent.py line 960 interpreted as "task done,
    # exit monologue", causing premature orchestrator termination.
    if len(all_tool_requests) > 1:
        # ── RCA-346 F-2: BATCH FENCE — Planning-Before-Execution ──
        # When the LLM batches planning tools (sequential_thinking, requirements,
        # generate_guid) with execution tools (call_subordinate, call_subordinate_batch),
        # the execution tools fire BEFORE the LLM processes planning results.
        # Fix: detect mixed batches, execute only planning+other tools, defer execution
        # tools with a notice so the LLM re-submits them on the next turn.
        PLANNING_TOOLS = {"sequential_thinking", "requirements", "generate_guid", "sequentialthinking"}
        EXECUTION_TOOLS = {"call_subordinate", "call_subordinate_batch"}

        tool_names_in_batch = {r.get("tool_name", "") for r in all_tool_requests}
        has_planning = bool(tool_names_in_batch & PLANNING_TOOLS)
        has_execution = bool(tool_names_in_batch & EXECUTION_TOOLS)

        if has_planning and has_execution:
            # Split: planning+other execute now, execution deferred
            planning_and_other = [r for r in all_tool_requests if r.get("tool_name", "") not in EXECUTION_TOOLS]
            deferred_execution = [r for r in all_tool_requests if r.get("tool_name", "") in EXECUTION_TOOLS]

            # F-15: Track consecutive fence activations to detect infinite deferral loops
            _batch_fence_count = agent.data.get('_batch_fence_count', 0) + 1
            agent.set_data('_batch_fence_count', _batch_fence_count)

            if _batch_fence_count >= 3:
                # F-15 AUTO-ESCALATION: After 3 consecutive fences, force-execute
                # deferred tools to break the loop. The LLM keeps co-submitting
                # planning + execution despite warnings; stop deferring.
                agent.log(
                    type='warning',
                    heading='🚧 BATCH FENCE AUTO-EXECUTE (3+ consecutive fences)',
                    content=_build_batch_fence_auto_execute_notice(_batch_fence_count),
                )
                # Don't defer — let everything execute
                all_tool_requests = planning_and_other + deferred_execution
                deferred_execution = []
                agent.set_data('_batch_fence_count', 0)
            else:
                # Normal fence behavior — defer execution tools
                deferred_names = [r.get("tool_name") for r in deferred_execution]
                agent.log(
                    type="warning",
                    heading="🚧 BATCH FENCE (RCA-346 F-2)",
                    content=_build_batch_fence_notice(
                        deferred_names, len(planning_and_other)
                    ),
                )

                # Replace all_tool_requests with planning+other only
                all_tool_requests = planning_and_other

                # Append a synthetic "deferred" notice that will be injected as feedback
                # after the planning tools execute (handled in the combined_results below)
                agent.set_data("_batch_fence_deferred", deferred_execution)
        else:
            # No fence needed — reset consecutive fence counter
            agent.set_data('_batch_fence_count', 0)

        combined_results = []
        total = len(all_tool_requests)
        executed = 0
        had_break_loop = False
        no_output_tools = []  # RCA-365 F-5: Collect names of tools that returned None
        for idx, tool_req in enumerate(all_tool_requests):
            tool_name_hint = tool_req.get("tool_name", "unknown")
            try:
                result = await _execute_single_tool_request(agent, tool_req, msg, msg_content)
                executed += 1
                if result is not None:
                    if isinstance(result, Response):
                        combined_results.append(result.message if hasattr(result, 'message') else str(result))
                        if result.break_loop:
                            had_break_loop = True
                            # Log remaining unexecuted tools so the LLM knows they were skipped
                            remaining = total - idx - 1
                            if remaining > 0:
                                skipped_names = [r.get("tool_name", "unknown") for r in all_tool_requests[idx+1:]]
                                skip_msg = (
                                    f"⚠️ break_loop triggered by tool {idx+1}/{total} ('{tool_name_hint}'). "
                                    f"{remaining} subsequent tool(s) were NOT executed: {skipped_names}"
                                )
                                combined_results.append(skip_msg)
                                agent.log(type="warning", content=skip_msg)
                            # Exit the for-loop immediately (skip remaining tools)
                            break
                    else:
                        # RCA-360: Detect response tool's string return as break_loop.
                        # When break_loop=True, _execute_single_tool_request returns
                        # response.message (a string, not a Response object). Without
                        # this check, the batch keeps had_break_loop=False, the monologue
                        # continues, and the agent produces duplicate response LogItems
                        # visible in the UI.
                        if tool_name_hint == "response" and isinstance(result, str) and result:
                            combined_results.append(str(result))
                            had_break_loop = True
                            remaining = total - idx - 1
                            if remaining > 0:
                                skipped_names = [r.get("tool_name", "unknown") for r in all_tool_requests[idx+1:]]
                                skip_msg = (
                                    f"⚠️ break_loop triggered by tool {idx+1}/{total} ('{tool_name_hint}'). "
                                    f"{remaining} subsequent tool(s) were NOT executed: {skipped_names}"
                                )
                                combined_results.append(skip_msg)
                                agent.log(type="warning", content=skip_msg)
                            break
                        combined_results.append(str(result))
                else:
                    # RCA-365 F-5: Collect no-output tools for deferred summary
                    no_output_tools.append(tool_name_hint)
            except Exception as e:
                # RCA-309: InterventionException is a supervisor control signal —
                # it MUST propagate to the monologue loop, not be swallowed by
                # crash isolation. Mirrors single-tool path at line ~789.
                if isinstance(e, InterventionException):
                    raise
                # One tool crashing must NOT kill the remaining tools
                executed += 1
                err_msg = f"Error: Tool '{tool_name_hint}' ({idx+1}/{total}) crashed: {str(e)}"
                combined_results.append(err_msg)
                agent.log(type="error", heading="Multi-Tool Crash Isolation", content=err_msg)
                # Record to ErrorLedger
                try:
                    from python.helpers.error_ledger import get_error_ledger, ErrorEntry
                    context_id = agent.context.id if agent.context else None
                    if context_id:
                        get_error_ledger().record(context_id, ErrorEntry(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            source="tool",
                            severity="high",
                            summary=f"Tool '{tool_name_hint}' crashed in multi-tool batch",
                            details=str(e),
                            tool_name=tool_name_hint,
                            five_why_hint="This tool threw an unhandled exception. Check arguments and try again.",
                        ))
                except Exception as el_err:
                    logger.warning(f"[MULTI-TOOL] ErrorLedger recording failed: {el_err}")

        # RCA-365 F-5: Append collapsed no-output summary instead of N× verbose messages
        if no_output_tools:
            no_output_summary = _format_no_output_summary(no_output_tools)
            if no_output_summary:
                combined_results.append(no_output_summary)

        # Summary line for observability
        if executed != total:
            agent.log(type="warning", content=f"Multi-tool batch: {executed}/{total} tools executed.")

        # RCA-346 F-2: Append deferral notice for execution tools that were fenced
        deferred = agent.data.pop("_batch_fence_deferred", None)
        if deferred and not had_break_loop:
            deferred_names = [r.get("tool_name") for r in deferred]
            deferred_args_preview = []
            for r in deferred:
                args = r.get("tool_args", {})
                preview = json.dumps(args, default=str)  # ITR-42 F-3: No truncation — LLM needs full args to re-submit
                deferred_args_preview.append(f"  - {r.get('tool_name')}: {preview}")
            defer_notice = (
                f"\n\n🚧 **BATCH FENCE — {len(deferred)} execution tool(s) DEFERRED**\n\n"
                f"⚠️ **CRITICAL**: On your next response, submit ONLY the deferred tool(s) below. "
                f"Do NOT include ANY other tools (requirements, sequential_thinking, read_file, etc.) "
                f"in the same batch — this will trigger the fence again and no other tool will execute.\n\n"
                f"Submit ONLY:\n\n"
                + "\n".join(deferred_args_preview)
                + f"\n\n"
                f"**Rule: The deferred tool must be the ONLY tool call in your next response.**"
            )
            combined_results.append(defer_notice)

        # RCA-307: Only return a truthy value when break_loop was triggered.
        # This is the ONLY case where the monologue should exit.
        if had_break_loop and combined_results:
            return "\n\n---\n\n".join(combined_results)

        # No break_loop in the batch → inject feedback into history so the LLM
        # sees results on the next turn, but return None so the monologue continues.
        if combined_results:
            feedback = "\n\n---\n\n".join(combined_results)
            # RCA-320: Use hist_add_warning instead of hist_add_tool_result.
            # hist_add_tool_result("multi_tool_batch",...) injected a synthetic
            # tool name into the LLM's conversation history. The LLM then tried
            # to call "multi_tool_batch" as a real tool, triggering
            # ProfileToolEnforcement blocks and wasting agent iterations.
            # hist_add_warning uses the fw.warning.md template which does NOT
            # include a tool_name field, preventing LLM tool-name pollution.
            await agent.hist_add_warning(
                message=f"Multi-tool batch results ({executed}/{total} tools executed):\n\n{feedback}"
            )
            logger.info(
                f"[MULTI_TOOL_BATCH] {agent.agent_name}: {executed}/{total} tools executed, "
                f"no break_loop — injected {len(feedback)} chars into history, continuing monologue."
            )
        return None

    # Single tool call (or zero) — use the original single-tool path
    tool_request = all_tool_requests[0] if all_tool_requests else None

    if tool_request is None and msg_content:
        # If json_parse_dirty returned None, there is NO valid tool call JSON in
        # the message — regardless of whether the text contains '{' characters
        # (e.g. code examples, descriptions). Auto-wrap as a response tool call.
        # This prevents infinite misformat loops where the model outputs plain
        # text that happens to contain curly braces.
        # Pattern inspired by Roo Code's noToolsUsed + consecutiveNoToolUseCount.
        agent.log(type="info", content=f"{agent.agent_name}: Non-JSON response detected (json_parse_dirty=None). Auto-wrapping as response.", verbose=True)
        if agent.get_tool("response"):
            # Reset misformat counter on successful auto-wrap
            agent.data["_consecutive_misformat_count"] = 0
            tool_request = {
                "thoughts": ["Agent provided plain text response, auto-wrapping in 'response' tool."],
                "headline": "Final Response (Auto-Wrap)",
                "tool_name": "response",
                "tool_args": {"text": msg_content}
            }

    if tool_request is not None:
        # Reset consecutive misformat counter on any successful parse (like Roo)
        agent.data["_consecutive_misformat_count"] = 0
        # RCA-325b: Reset blocked flag — will be set True ONLY if extension blocks
        agent.data["_last_tool_was_blocked"] = False

        # Initialize failed tool counter if not exists to detect loops
        if not hasattr(agent, "_failed_tool_count"):
            agent._failed_tool_count = 0

        raw_tool_name = tool_request.get("tool_name", "")
        tool_args = tool_request.get("tool_args", {})

        # Fallback for OpenAI-compatible tool call format
        if not raw_tool_name:
            tool_name_keys = ["name", "tool", "action", "command", "function_name", "tool_call"]
            for key in tool_name_keys:
                if key in tool_request and tool_request.get(key):
                    raw_tool_name = str(tool_request.get(key)).strip()
                    break

            # Handle {"type": "function", "name": "...", "parameters": {...}} (OpenAI format)
            if not raw_tool_name and "function" in tool_request and isinstance(tool_request["function"], dict):
                raw_tool_name = tool_request["function"].get("name", "")

            # Handle {"type": "function", "function": {"name": "...", "arguments": {...}}} (nested)
            if not raw_tool_name and "type" in tool_request and tool_request.get("type") == "function":
                func_data = tool_request.get("function", {})
                if isinstance(func_data, dict):
                    raw_tool_name = func_data.get("name", "")

            if not tool_args:
                args_keys = ["parameters", "arguments", "args", "tool_args", "params", "input"]
                for key in args_keys:
                    if key in tool_request and tool_request.get(key):
                        tool_args = tool_request.get(key)
                        break

                # Nested function arguments
                if not tool_args and "function" in tool_request and isinstance(tool_request["function"], dict):
                    tool_args = tool_request["function"].get("arguments") or tool_request["function"].get("parameters") or {}

        # If arguments/parameters is a string, try to parse it
        if isinstance(tool_args, str):
            try:
                tool_args = dirty_json.DirtyJson.parse(tool_args)
            except Exception as e:
                # INTENTIONAL: Dirty JSON parse fallback — falls through to raw string args
                logger.debug(f"[TOOLS] JSON repair fallback triggered: {e}")

        # ============================================================
        # GUARD: Reject non-tool-call JSON (e.g., {{verbatim:...}} templates)
        # If json_parse_dirty returned a dict but it has NO recognized tool
        # or thinking keys, it's garbage JSON from template syntax, code
        # examples, etc. Auto-wrap the original message as a response.
        # ============================================================
        if not raw_tool_name and not _has_recognized_keys(tool_request):
            agent.log(
                type="info",
                content=f"{agent.agent_name}: Parsed JSON has no recognized tool/thinking keys "
                        f"(keys: {list(tool_request.keys())[:5]}). Auto-wrapping as response.",
                verbose=True
            )
            if agent.get_tool("response"):
                agent.data["_consecutive_misformat_count"] = 0
                tool_request = {
                    "thoughts": ["Parsed JSON has no tool keys. Auto-wrapping original message as response."],
                    "headline": "Final Response (Non-Tool JSON Auto-Wrap)",
                    "tool_name": "response",
                    "tool_args": {"text": msg_content}
                }
                raw_tool_name = "response"
                tool_args = {"text": msg_content}

        # Guard against empty tool name to prevent infinite failed tool loops
        if not raw_tool_name or not str(raw_tool_name).strip():
            agent._failed_tool_count += 1

            json_preview = str(tool_request)[:500] if tool_request else "None"
            received_keys = list(tool_request.keys()) if tool_request else []

            # Check if this is a "thinking only" response
            meaningful_keys = {k for k in received_keys if k and k.strip()}
            thinking_keys = {"thought", "thoughts", "thinking", "reasoning", "analysis", "observation", "headline",
                             "thoughtNumber", "totalThoughts", "nextThoughtNeeded", "isRevision",
                             "revisesThought", "branchFromThought", "branchId"}
            if meaningful_keys and meaningful_keys.issubset(thinking_keys):
                headline = tool_request.get("headline", "")
                thoughts = tool_request.get("thoughts", tool_request.get("thought", ""))

                # AUTO-INJECT RESPONSE TOOL
                success_indicators = ["✅", "complete", "confirmed", "passed", "acknowledged", "acknowledgment", "ack", "done", "success", "processed", "received", "turn"]
                if headline and any(ind.lower() in headline.lower() for ind in success_indicators):
                    agent.log(
                        type="info",
                        heading="🔄 Auto-Injected Response",
                        content="LLM produced thinking with success headline but no tool. Auto-injecting response tool.",
                        verbose=True
                    )
                    response_text = f"## {headline}\n\n"
                    if isinstance(thoughts, list):
                        response_text += "\n".join(f"- {t}" for t in thoughts[:3])
                    elif thoughts:
                        response_text += str(thoughts)[:500]

                    raw_tool_name = "response"
                    tool_args = {"text": response_text}
                    tool_request["tool_name"] = raw_tool_name
                    tool_request["tool_args"] = tool_args
                else:
                    agent.log(
                        type="info",
                        heading="💭 Thinking Response (No Tool Call)",
                        content=f"You provided reasoning but no tool execution. You MUST call a tool to proceed.\n\nYour thought: {str(thoughts)[:200]}...",
                        verbose=True
                    )
                    return (
                        f"You provided reasoning/thoughts but did not call a tool. "
                        f"To proceed with your task, you MUST use a tool. "
                        f"Example format:\n"
                        f'{{"tool_name": "code_execution", "tool_args": {{"runtime": "python", "code": "print(\'hello\')"}}}}\n\n'
                        f"Available tools include: code_execution, response, parameter_get, parameter_set, repository_automation, maintain_memory_bank.\n"
                        f"Please call the appropriate tool now."
                    )

            # After potential auto-inject, check if raw_tool_name is still empty
            if not raw_tool_name or not str(raw_tool_name).strip():
                agent.log(
                    type="warning",
                    heading="⚠️ Empty Tool Name",
                    content=f"Agent attempted to call a tool with an empty name (Attempt {agent._failed_tool_count}/3).\n\nReceived JSON: {json_preview}"
                )

                if agent._failed_tool_count >= 3:
                    loop_msg = f"Agent is stuck in 'Empty Tool Name' loop. Received: {json_preview[:200]}. Triggering supervisor intervention."
                    agent.log(type="error", heading="🔄 Tool Loop Detected", content=loop_msg)
                    raise InterventionException(loop_msg)

                return f'Error: You provided an empty tool_name. Received JSON keys: {received_keys}. Please use the format: {{"tool_name": "tool_name_here", "tool_args": {{...}}}}'
        tool_args = tool_request.get("tool_args", {})

        tool_name = raw_tool_name
        tool_method = None

        if ":" in raw_tool_name:
            tool_name, tool_method = raw_tool_name.split(":", 1)

        tool = None

        # Try getting tool from MCP first
        try:
            import python.helpers.mcp_handler as mcp_helper

            print(f"[DEBUG_AGENT] Attempting to resolve tool: {tool_name}", flush=True)
            mcp_tool_candidate = await mcp_helper.MCPConfig.get_instance().get_tool_async(
                agent, tool_name
            )
            if mcp_tool_candidate:
                print(f"[DEBUG_AGENT] Found MCP tool candidate: {mcp_tool_candidate.get('name')} on server {mcp_tool_candidate.get('server')}", flush=True)
                from python.helpers.mcp_handler import MCPTool
                tool = MCPTool(agent=agent, name=tool_name, method=None, args=tool_args, message=msg, loop_data=agent.loop_data)
            else:
                print(f"[DEBUG_AGENT] No MCP tool candidate found for: {tool_name}", flush=True)
        except ImportError:
            PrintStyle(
                background_color="black", font_color="yellow", padding=True
            ).print("MCP helper module not found. Skipping MCP tool lookup.")
        except Exception as e:
            PrintStyle(
                background_color="black", font_color="red", padding=True
            ).print(f"Failed to get MCP tool '{tool_name}': {e}")

        # Fallback to local get_tool if MCP tool was not found
        if not tool:
            tool = agent.get_tool(
                name=tool_name, method=tool_method, args=tool_args, message=msg, loop_data=agent.loop_data
            )

        # ============================================================
        # TOOL CALL DEDUPLICATION GUARD (Phases 1-5)
        # ============================================================
        from python.helpers.hashing import normalized_tool_hash, dedup_hash_short
        from python.helpers.loop_detection import (
            get_tool_thresholds, detect_ping_pong_streak, detect_no_progress_streak,
            detect_same_tool_streak, get_same_tool_thresholds, is_mcp_tool
        )
        RECENT_CALLS_WINDOW = 30

        if tool_name and tool_name.strip().lower() != "response":
            _call_sig = normalized_tool_hash(tool_name, tool_args)

            # Phase 4: Profile-aware thresholds (research tools get higher limits)
            _thresholds = get_tool_thresholds(tool_name)
            MAX_CONSECUTIVE_IDENTICAL_CALLS = _thresholds["max_consecutive"]
            MAX_TOTAL_IDENTICAL_CALLS = _thresholds["max_total"]
            HARD_BREAK_THRESHOLD = _thresholds["hard_break"]

            if "_tool_call_dedup" not in agent.data:
                agent.data["_tool_call_dedup"] = []

            _dedup_history = agent.data["_tool_call_dedup"]

            # Phase 3: Ping-pong detection (A→B→A→B alternating patterns)
            _ping_pong = detect_ping_pong_streak(_dedup_history)
            if _ping_pong >= 5:
                _pp_msg = (
                    f"⚠️ PING-PONG LOOP DETECTED: Agent is alternating between the same two tool calls "
                    f"{_ping_pong} times without progress. Break the pattern by trying a completely different approach."
                )
                agent.log(type="warning", heading="🏓 Ping-Pong Loop", content=_pp_msg)
                await agent.hist_add_warning(message=_pp_msg)
                if _ping_pong >= 10:
                    agent.log(type="error", heading="🛑 Ping-Pong Hard Break", content=_pp_msg)
                    raise InterventionException(_pp_msg)
                await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                return _pp_msg

            # Phase 5: Same-tool streak detection (args-independent)
            # Catches agents calling the same tool N+ times with different args.
            # E.g., Google Chat agent calling list_messages with page_size=25, then 35, then 50.
            _same_tool_streak = detect_same_tool_streak(_dedup_history)
            _st_thresholds = get_same_tool_thresholds(tool_name)
            if _same_tool_streak >= _st_thresholds["block"]:
                _st_msg = (
                    f"🛑 SAME-TOOL LOOP HARD BLOCK: You have called '{tool_name}' "
                    f"{_same_tool_streak} times consecutively with different arguments. "
                    f"This tool is NOT giving you what you need — change your approach entirely. "
                    f"Use a DIFFERENT tool or deliver your current results with the 'response' tool."
                )
                agent.log(type="error", heading="🛑 Same-Tool Hard Block", content=_st_msg)
                await agent.hist_add_warning(message=_st_msg)
                await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                raise InterventionException(_st_msg)
            elif _same_tool_streak >= _st_thresholds["warn"]:
                _st_msg = (
                    f"⚠️ SAME-TOOL STREAK WARNING: You have called '{tool_name}' "
                    f"{_same_tool_streak} times consecutively. Each call used different arguments "
                    f"but you're still calling the same tool. You likely already have the data you need "
                    f"in your conversation history. Stop calling this tool and:\n"
                    f"1. USE the results you already received\n"
                    f"2. Try a COMPLETELY DIFFERENT tool\n"
                    f"3. Deliver your current results with the 'response' tool"
                )
                agent.log(type="warning", heading="⚠️ Same-Tool Streak", content=_st_msg)
                await agent.hist_add_warning(message=_st_msg)
                await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                return _st_msg

            _consecutive_success = 0
            _consecutive_total = 0
            for entry in reversed(_dedup_history):
                if entry["sig"] == _call_sig:
                    _consecutive_total += 1
                    if not entry.get("err", False):
                        _consecutive_success += 1
                else:
                    break

            _total_count = sum(1 for e in _dedup_history if e["sig"] == _call_sig)
            _total_success = sum(1 for e in _dedup_history if e["sig"] == _call_sig and not e.get("err", False))

            # Phase 2: Check no-progress streak (same args AND same results)
            # MD5 hashes (sig + result_hash) prove identical call + identical result
            _no_progress = detect_no_progress_streak(_dedup_history)
            if _no_progress >= MAX_CONSECUTIVE_IDENTICAL_CALLS:
                # Debug: trace dedup state for diagnostics
                _err_flags = [e.get("err", False) for e in _dedup_history[-_no_progress:]]
                print(f"[DEDUP_DEBUG] tool={tool_name} no_progress={_no_progress} "
                      f"consec_success={_consecutive_success} consec_total={_consecutive_total} "
                      f"err_flags={_err_flags}", flush=True)
                if _consecutive_success > 0:
                    # Success case: same args, same result — agent isn't learning, BLOCK
                    _np_msg = (
                        f"⚠️ NO-PROGRESS LOOP: Tool '{tool_name}' called {_no_progress} times with identical args "
                        f"AND identical successful results — making zero progress. "
                        f"The tool IS WORKING — you already have the results in your conversation history. "
                        f"DO NOT call this tool again. Instead:\n"
                        f"1. USE the results already returned to you above\n"
                        f"2. If you need different data, try DIFFERENT arguments\n"
                        f"3. Move on to the next part of your task"
                    )
                    agent.log(type="warning", heading="📊 No Progress Detected", content=_np_msg)
                    await agent.hist_add_warning(message=_np_msg)
                    await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                    # FIX: Record the BLOCKED call in dedup history so the streak
                    # never resets. Without this, the next identical call sees
                    # no_progress=1 (streak reset) and executes again, causing
                    # an infinite block→execute→block→execute loop.
                    # Use the result_hash from the last matching entry so
                    # detect_no_progress_streak sees a continuous streak.
                    _last_result_hash = None
                    for _prev in reversed(_dedup_history):
                        if _prev.get("sig") == _call_sig and _prev.get("result_hash"):
                            _last_result_hash = _prev["result_hash"]
                            break
                    _dedup_history.append({
                        "sig": _call_sig, "err": False, "tool": tool_name,
                        "result_hash": _last_result_hash, "blocked": True
                    })
                    # ESCALATE: If this is the 2nd+ consecutive block for an MCP tool,
                    # hard-break via InterventionException. The agent had its warning;
                    # continuing to retry wastes tokens and API calls.
                    _consecutive_blocks = 0
                    for _entry in reversed(_dedup_history):
                        if _entry.get("sig") == _call_sig and _entry.get("blocked"):
                            _consecutive_blocks += 1
                        else:
                            break
                    if _consecutive_blocks >= 2 and is_mcp_tool(tool_name):
                        _escalate_msg = (
                            f"🛑 NO-PROGRESS HARD BLOCK: Tool '{tool_name}' blocked {_consecutive_blocks} times "
                            f"consecutively with identical args. You MUST use a DIFFERENT tool or the 'response' tool now."
                        )
                        agent.log(type="error", heading="🛑 No-Progress Hard Block", content=_escalate_msg)
                        raise InterventionException(_escalate_msg)
                    return _np_msg
                else:
                    # Error case: same args, same error — agent isn't adjusting args
                    # BLOCK the call — returning the error message tells the agent to stop
                    _np_msg = (
                        f"⚠️ SAME-ERROR LOOP BLOCKED ({_no_progress}x): Tool '{tool_name}' returned "
                        f"the same error {_no_progress} times with identical arguments "
                        f"(MD5 verified). The call has been BLOCKED to prevent infinite retries. "
                        f"You MUST either:\n"
                        f"1. ADJUST your arguments based on the error message\n"
                        f"2. Use a COMPLETELY DIFFERENT tool to achieve the same goal\n"
                        f"3. Skip this step and continue with the rest of your task\n\n"
                        f"DO NOT retry with the same arguments."
                    )
                    agent.log(type="warning", heading="🔄 Same-Error Loop BLOCKED", content=_np_msg)
                    await agent.hist_add_warning(message=_np_msg)
                    await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                    # FIX: Record blocked error call in dedup history (same fix as success case)
                    _last_result_hash = None
                    for _prev in reversed(_dedup_history):
                        if _prev.get("sig") == _call_sig and _prev.get("result_hash"):
                            _last_result_hash = _prev["result_hash"]
                            break
                    _dedup_history.append({
                        "sig": _call_sig, "err": True, "tool": tool_name,
                        "result_hash": _last_result_hash, "blocked": True
                    })
                    # Hard break if error loop is extreme
                    if _no_progress >= HARD_BREAK_THRESHOLD:
                        agent.log(type="error", heading="🛑 Error Loop Hard Break", content=_np_msg)
                        raise InterventionException(_np_msg)
                    # ESCALATE: 2nd+ consecutive block for MCP tools → hard break
                    _consecutive_blocks = 0
                    for _entry in reversed(_dedup_history):
                        if _entry.get("sig") == _call_sig and _entry.get("blocked"):
                            _consecutive_blocks += 1
                        else:
                            break
                    if _consecutive_blocks >= 2 and is_mcp_tool(tool_name):
                        _escalate_msg = (
                            f"🛑 SAME-ERROR HARD BLOCK: Tool '{tool_name}' blocked {_consecutive_blocks} times "
                            f"consecutively with identical error. You MUST use a DIFFERENT tool or the 'response' tool now."
                        )
                        agent.log(type="error", heading="🛑 Same-Error Hard Block", content=_escalate_msg)
                        raise InterventionException(_escalate_msg)
                    return _np_msg

            _dedup_index = len(_dedup_history)
            _dedup_history.append({"sig": _call_sig, "err": False, "tool": tool_name})
            if len(_dedup_history) > RECENT_CALLS_WINDOW:
                agent.data["_tool_call_dedup"] = _dedup_history[-RECENT_CALLS_WINDOW:]
                _dedup_index = len(agent.data["_tool_call_dedup"]) - 1

            if _total_count >= HARD_BREAK_THRESHOLD:
                _loop_msg = (
                    f"CRITICAL: Tool '{tool_name}' called {_total_count + 1} times with identical (normalized) arguments. "
                    f"Breaking loop via supervisor intervention."
                )
                agent.log(type="error", heading="🛑 Tool Loop Hard Break", content=_loop_msg)
                await agent.hist_add_warning(message=_loop_msg)
                raise InterventionException(_loop_msg)

            elif _consecutive_total >= MAX_CONSECUTIVE_IDENTICAL_CALLS and _consecutive_success > 0:
                _dedup_warning = (
                    f"⚠️ DUPLICATE TOOL CALL BLOCKED: You have already called '{tool_name}' with the same arguments "
                    f"{_consecutive_total} times consecutively and received successful results. "
                    f"DO NOT call it again. Instead:\n"
                    f"1. USE the results already in your conversation history\n"
                    f"2. If the results were insufficient, try a DIFFERENT tool or approach\n"
                    f"3. If you have enough information, use the 'response' tool to provide your answer"
                )
                agent.log(type="warning", heading="🔄 Duplicate Tool Call Blocked", content=_dedup_warning)
                await agent.hist_add_warning(message=_dedup_warning)
                await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                return _dedup_warning

            elif _total_count >= MAX_TOTAL_IDENTICAL_CALLS and _total_success > 0:
                _dedup_warning = (
                    f"⚠️ TOOL LOOP DETECTED: You have called '{tool_name}' with identical (normalized) arguments "
                    f"{_total_count + 1} times in this conversation turn. "
                    f"You already have the results. DO NOT call it again. Instead:\n"
                    f"1. USE the results already in your conversation history\n"
                    f"2. Move on to the NEXT task\n"
                    f"3. If you have enough information, use the 'response' tool to provide your answer"
                )
                agent.log(type="warning", heading="🔄 Interleaved Tool Loop Blocked", content=_dedup_warning)
                await agent.hist_add_warning(message=_dedup_warning)
                await ObserverMesh.get_instance().record_tool_execution(tool_name=tool_name, duration=0, success=False)
                return _dedup_warning
        # ============================================================

        # Record tool call in agent data for supervisor/monitoring
        if "recent_tool_calls" not in agent.data:
            agent.data["recent_tool_calls"] = []
        agent.data["recent_tool_calls"].append({
            "tool_name": tool_name,
            "tool_args": tool_args,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        agent.data["recent_tool_calls"] = agent.data["recent_tool_calls"][-20:]

        if tool:
            agent.loop_data.current_tool = tool

            # Reset failed tool counter on valid tool call (excluding Unknown)
            from python.tools.unknown import Unknown
            if not isinstance(tool, Unknown):
                agent._failed_tool_count = 0
            else:
                agent._failed_tool_count += 1

                if agent._failed_tool_count >= 3:
                    loop_msg = f"Agent is stuck in 'Tool not found' loop for tool '{tool_name}'. Triggering supervisor intervention."

                    from python.helpers.mcp_handler import MCPConfig
                    mcp_config = MCPConfig.get_instance()
                    mcp_servers = mcp_config.servers if mcp_config else []

                    server = next((s for s in mcp_servers if s.name == tool_name), None)
                    if server:
                        avail_tools = [f"{server.name}.{t['name']}" for t in server.get_tools()]
                        hint = f"\n\n[HINT] '{tool_name}' is an MCP server, not a tool. Available tools on this server: {', '.join(avail_tools)}.\nUse the format 'server_name.tool_name'."
                        loop_msg += hint

                    agent.log(type="error", heading="🔄 Tool Loop Detected", content=loop_msg)

                    try:
                        signal = event_bus.AgentSignal(
                            signal_type=event_bus.SignalType.TOOL_FAILURE_LOOP,
                            agent_id=agent.agent_name,
                            context_id=agent.context.id if agent.context else "N/A",
                            timestamp=datetime.now(timezone.utc),
                            severity="critical",
                            tool_name=tool_name,
                            error_message=loop_msg
                        )
                        event_bus.get_event_bus().publish_sync(signal)
                    except Exception as e:
                        agent.log(type="debug", content=f"Failed to emit loop signal: {e}")

                    agent._failed_tool_count = 0
                    await agent.hist_add_warning(message=loop_msg)
                    raise InterventionException(loop_msg)

            start_time = time.perf_counter()
            try:
                await agent.handle_intervention()

                if tool_args is None:
                    tool_args = {}

                # ── RCA-298: Schema validation BEFORE execution ──
                # Validates tool_args against declared schemas. Blocks execution
                # and returns a formatted error to the LLM if validation fails.
                # Tools without schemas pass through (backward compatible).
                try:
                    from python.helpers.tool_schema_validator import ToolSchemaValidator
                    _schema_result = ToolSchemaValidator.validate(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        agent_data=agent.data,
                    )
                    if not _schema_result.valid:
                        logger.warning(
                            f"[SCHEMA] Tool '{tool_name}' blocked — "
                            f"{len(_schema_result.errors)} validation error(s)"
                        )
                        await agent.hist_add_warning(
                            message=_schema_result.error_message
                        )
                        return _schema_result.error_message
                except Exception as _schema_err:
                    logger.debug(f"[SCHEMA] Validation failed (non-fatal): {_schema_err}")

                await tool.before_execution(**tool_args)
                await agent.handle_intervention()

                ext_result = await agent.call_extensions("tool_execute_before", tool_args=tool_args, tool_name=tool_name)

                # Extension Blocking Protocol: If any extension (e.g. ProfileToolEnforcement)
                # returns a non-None result, it means the tool is BLOCKED. Use the extension's
                # response as the tool result and skip execution entirely.
                if ext_result is not None:
                    if isinstance(ext_result, Response):
                        response = ext_result
                    else:
                        response = Response(message=str(ext_result), break_loop=False)
                    agent.log(
                        type="warning",
                        heading="🚫 Tool Blocked by Extension",
                        content=f"Extension blocked '{tool_name}': {response.message[:200]}",
                    )
                    # Record the block as a warning so the model sees it
                    await agent.hist_add_warning(message=response.message)
                    # RCA-325b: Signal to agent.py that this was a genuine extension block
                    agent.data["_last_tool_was_blocked"] = True
                    return None  # Continue monologue loop, don't break

                # ── ISSUE-1: Fingerprint Repetition Skip Guard ──
                # If L1 structural guards detected md5_repetition AND this
                # tool call's fingerprint matches the repeated pattern, SKIP.
                _fp_skip_result = check_fingerprint_repetition_skip(
                    agent, tool_name, tool_args
                )
                if _fp_skip_result is not None:
                    await agent.hist_add_warning(message=_fp_skip_result.message)
                    agent.data["_last_tool_was_blocked"] = True
                    return None  # Continue monologue loop, don't break

                # GAP-5: Check tool result cache before execution
                _cache_hit = False
                if ToolResultCache.is_cacheable(tool_name):
                    try:
                        _tool_cache = ToolResultCache.get_instance()
                        _cached_result = await _tool_cache.get(tool_name, tool_args)
                        if _cached_result is not None:
                            response = Response(
                                message=f"[Cached] {_cached_result}",
                                break_loop=False
                            )
                            _cache_hit = True
                            duration_ms = (time.perf_counter() - start_time) * 1000
                            agent.log(
                                type="info",
                                content=f"ToolCache HIT: '{tool_name}' returned cached result ({duration_ms:.1f}ms)",
                                verbose=True
                            )
                    except Exception as cache_err:
                        logger.debug(f"ToolCache check failed for '{tool_name}': {cache_err}")

                if not _cache_hit:
                    # RCA-357: Stamp heartbeat BEFORE tool execution starts.
                    # Long-running tools (npm install, git clone, build) can take
                    # 2-5 minutes. Without this pre-stamp, the idle timer fires
                    # at 120s because stamp_tool_activity_heartbeat (L841) only
                    # fires AFTER the tool completes.
                    try:
                        from python.helpers.subordinate_timeout import stamp_tool_activity_heartbeat
                        stamp_tool_activity_heartbeat(agent)
                    except Exception:
                        pass
                    response = await tool.execute(**tool_args)
                    duration_ms = (time.perf_counter() - start_time) * 1000

                    # GLOBAL ROBUSTNESS FIX: Ensure response is a Response object
                    if not isinstance(response, Response):
                        agent.log(
                            type="warning",
                            content=f"Tool '{tool_name}' returned {type(response).__name__} instead of Response object. Wrapping automatically.",
                            verbose=True
                        )
                        if isinstance(response, dict):
                            raw_msg = response.get("message") or response.get("error") or response.get("status")
                            if not raw_msg:
                                raw_msg = json.dumps(response, indent=2)
                            response = Response(message=str(raw_msg), break_loop=False, additional=response)
                        else:
                            response = Response(message=str(response), break_loop=False)

                    # RCA-330: Unwrap nested Response objects in .message
                    # This prevents the crash at the dedup tracker below where
                    # .lower() is called on response.message (must be a string).
                    response = _coerce_response_message(response)

                    # RCA-330: Activity heartbeat — stamp last tool execution time.
                    # _run_with_activity_timeout reads this to reset the idle timer.
                    # An npm build taking 90s no longer burns 15% of timeout budget.
                    # SAFETY: Wrapped in try/except — heartbeat is observational,
                    # an import/call failure must NEVER crash tool execution.
                    try:
                        from python.helpers.subordinate_timeout import stamp_tool_activity_heartbeat
                        stamp_tool_activity_heartbeat(agent)
                    except Exception:
                        pass  # Heartbeat is best-effort, never crash tools

                    # GAP-5: Store result in cache for cacheable tools
                    if ToolResultCache.is_cacheable(tool_name) and isinstance(response, Response) and response.message:
                        try:
                            _tool_cache = ToolResultCache.get_instance()
                            await _tool_cache.set(tool_name, tool_args, response.message)
                        except Exception as cache_err:
                            logger.debug(f"ToolCache store failed for '{tool_name}': {cache_err}")

                await ObserverMesh.get_instance().record_tool_execution(
                    tool_name=tool_name,
                    duration=duration_ms / 1000.0,
                    success=True
                )

                # Update dedup tracker: mark as error + store result hash for no-progress detection
                if "_tool_call_dedup" in agent.data and '_dedup_index' in dir():
                    _result_is_error = False
                    if isinstance(response, Response):
                        # First check: use MCP tool's explicit error flag if available
                        _additional = getattr(response, 'additional', None) or {}
                        if isinstance(_additional, dict) and 'success' in _additional:
                            _result_is_error = not _additional['success']
                        else:
                            # Fallback: check response text for error markers
                            # Use boundary-aware matching to avoid false positives
                            # inside content text (e.g., chat messages mentioning errors)
                            msg_lower = (response.message or "").lower()
                            _error_markers_prefix = [
                                'system_error:', 'httperror 403', 'httperror 400',
                                'httperror 404', 'httperror 500',
                            ]
                            # These markers only match at start of line or response
                            _result_is_error = any(
                                msg_lower.startswith(marker) or f'\n{marker}' in msg_lower
                                for marker in _error_markers_prefix
                            )
                            if not _result_is_error:
                                # Check for explicit error prefixes (not in content body)
                                _result_is_error = (
                                    msg_lower.startswith('error:') or
                                    msg_lower.startswith('permission denied') or
                                    '\nerror:' in msg_lower
                                )
                    _dedup_list = agent.data["_tool_call_dedup"]
                    if _dedup_index < len(_dedup_list):
                        if _result_is_error:
                            _dedup_list[_dedup_index]["err"] = True
                        # Phase 2: Store result hash for no-progress streak detection
                        if isinstance(response, Response) and response.message:
                            _dedup_list[_dedup_index]["result_hash"] = dedup_hash_short(
                                response.message[:2048]  # Cap at 2K to avoid hashing huge results
                            )

                agent.log(
                    type="info",
                    content=f"Tool Performance: '{tool_name}' executed in {duration_ms:.2f}ms",
                    verbose=True
                )

                # 5-Why RCA (2026-04-22): Wire fingerprint_action() so every tool
                # call populates _md5_action_log. This was NEVER called before,
                # making DETECTOR 1 (md5_repetition in _10_structural_guards.py)
                # dead code — it checked an always-empty list.
                try:
                    _fp_content = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
                    agent.fingerprint_action("tool_call", _fp_content)
                except Exception:
                    pass  # Fingerprinting must never break tool execution

                # Track last tool executed for loop detection
                # (_10_structural_guards reads this to detect consecutive same-tool loops)
                agent.set_data("last_tool_executed", tool_name)
                agent.set_data("last_tool_args", tool_args)
                agent.set_data("last_tool_time", time.time())

                await agent.handle_intervention()
                await agent.call_extensions("tool_execute_after", response=response, tool_name=tool_name, tool_args=tool_args)
                await tool.after_execution(response)
                await agent.handle_intervention()

                if response.break_loop:
                    # Set last_tool so message_loop_end extensions can detect
                    # which tool caused the loop to end (critical for fidelity check,
                    # which gates on last_tool == "response" per Issue #789)
                    if agent.loop_data:
                        agent.loop_data.last_tool = tool_name
                    return response.message
                else:
                    # RCA-362 F-1: Gate blocked this response (break_loop=False).
                    # Return None so the monologue continues — the agent will
                    # read the gate feedback and retry on the next iteration.
                    #
                    # WHY NOT return the Response object?
                    # Response is a @dataclass with no __bool__ override — it's
                    # ALWAYS truthy. agent.py:1017 uses `if tools_result:` to
                    # decide whether to exit the monologue. A truthy Response
                    # causes premature exit, preventing the agent from ever
                    # reading gate feedback and retrying.
                    #
                    # WHY is this safe?
                    # response.py:after_execution() (L310-333) already injected
                    # the gate's block message into conversation history as both
                    # a tool result AND a warning. The agent WILL see it on the
                    # next monologue iteration.
                    #
                    # BATCH PATH: When called from _execute_single_tool_request
                    # (L119-138), the batch loop at L244-248 will receive None.
                    # This is safe because after_execution() already injected
                    # the feedback. The batch path's combined_results may miss
                    # the gate message for this slot, but the full feedback is
                    # in history — which is what the agent reads.
                    return None

            except Exception as e:
                duration_ms = (time.perf_counter() - start_time) * 1000
                await ObserverMesh.get_instance().record_tool_execution(
                    tool_name=tool_name,
                    duration=duration_ms / 1000.0,
                    success=False
                )

                if "_tool_call_dedup" in agent.data and '_dedup_index' in dir():
                    _dedup_list = agent.data["_tool_call_dedup"]
                    if _dedup_index < len(_dedup_list):
                        _dedup_list[_dedup_index]["err"] = True

                if isinstance(e, InterventionException):
                    raise e

                import python.helpers.errors as errors
                full_traceback = errors.format_error(e)
                short_error = errors.get_short_error(e)

                err_msg = f"Tool '{tool_name}' execution failed: {short_error}"
                agent.log(
                    type="error",
                    heading="Tool Execution Failed",
                    content=err_msg,
                    kvps={"technical_details": full_traceback}
                )

                await agent.hist_add_tool_result(tool_name, f"Error: {err_msg}", success=False)

                # Record to ErrorLedger for prompt-level awareness
                try:
                    from python.helpers.error_ledger import get_error_ledger, ErrorEntry
                    from python.helpers.strings import truncate_text_by_ratio
                    context_id = agent.context.id if agent.context else None
                    if context_id:
                        get_error_ledger().record(context_id, ErrorEntry(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            source="tool",
                            severity="high",
                            summary=truncate_text_by_ratio(short_error, 200),
                            details=truncate_text_by_ratio(err_msg, 500),
                            tool_name=tool_name,
                            five_why_hint="Check your arguments, verify the target exists, and try a different approach if repeating.",
                        ))
                except Exception:
                    pass  # Must never break tool execution flow

                return f"Error: {err_msg}. Please check your arguments and try again."
            finally:
                agent.loop_data.current_tool = None
        else:
            error_detail = (
                f"Tool '{raw_tool_name}' not found or could not be initialized."
            )
            await agent.hist_add_tool_result(raw_tool_name or "unknown_or_empty", error_detail, success=False)

            await ObserverMesh.get_instance().record_tool_execution(
                tool_name=raw_tool_name or "unknown_or_empty",
                duration=0,
                success=False
            )

            PrintStyle(font_color="red", padding=True).print(error_detail)
            agent.log(
                type="error", content=f"{agent.agent_name}: {error_detail}"
            )

            # Record to ErrorLedger for prompt-level awareness (#1185)
            try:
                from python.helpers.error_ledger import get_error_ledger, ErrorEntry
                context_id = agent.context.id if agent.context else None
                if context_id:
                    get_error_ledger().record(context_id, ErrorEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="tool",
                        severity="medium",
                        summary=f"Tool '{raw_tool_name}' not found or could not be initialized",
                        details=error_detail,
                        tool_name=raw_tool_name or "unknown",
                        five_why_hint=(
                            f"'{raw_tool_name}' is not a valid tool name. "
                            "Check the tool list in your system prompt. "
                            "If this is an MCP tool, use the full tool name "
                            "(e.g., 'resolve-library-id'), not the server name (e.g., 'context7')."
                        ),
                    ))
            except Exception:
                pass  # Ledger recording must never break tool execution
    else:
        # Track consecutive misformat failures (like Roo Code's consecutiveNoToolUseCount).
        # Grace retry on first failure; force auto-wrap after 2 consecutive misformats.
        misformat_count = agent.data.get("_consecutive_misformat_count", 0) + 1
        agent.data["_consecutive_misformat_count"] = misformat_count

        if misformat_count >= 2 and msg_content and agent.get_tool("response"):
            # Hard backstop: after 2 consecutive misformat warnings, force auto-wrap.
            # This prevents infinite format-correction loops where the model keeps
            # producing non-JSON output despite repeated formatting hints.
            agent.data["_consecutive_misformat_count"] = 0
            agent.log(
                type="warning",
                content=f"{agent.agent_name}: {misformat_count} consecutive misformat failures. "
                        f"Force auto-wrapping as response (Roo-style backstop).",
            )
            tool_request = {
                "thoughts": [f"Force auto-wrap after {misformat_count} consecutive misformat failures."],
                "headline": "Final Response (Misformat Backstop)",
                "tool_name": "response",
                "tool_args": {"text": msg_content}
            }
            # Execute the forced response tool
            tool = agent.get_tool("response", args=tool_request.get("tool_args", {}), message=msg)
            if tool:
                await agent.call_extensions("tool_execute_before", tool=tool)
                response = await tool.execute(**tool_request.get("tool_args", {}))
                await agent.call_extensions("tool_execute_after", response=response, tool_name="response", tool_args=tool_request.get("tool_args", {}))
                await agent.hist_add_tool_result("response", response.message if response else "auto-wrapped", success=True)
                return response
        else:
            # First misformat: send a formatting hint (grace retry)
            warning_msg_misformat = agent.read_prompt("fw.msg_misformat.md")
            await agent.hist_add_warning(warning_msg_misformat)
            PrintStyle(font_color="yellow", padding=True).print(warning_msg_misformat)
            agent.log(
                type="warning",
                content=f"{agent.agent_name}: Agent provided a non-JSON response "
                        f"(attempt {misformat_count}/2). Sending formatting hint.",
            )
