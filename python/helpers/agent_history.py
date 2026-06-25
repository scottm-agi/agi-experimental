"""
Agent History Methods — Extracted from agent.py (Issue #1200 P0.2).

This module contains the implementation of all history-related methods
that were previously in the Agent class. Each function accepts the agent
instance as its first argument (``agent``) and preserves the exact same
logic as the original inline methods.

The Agent class now delegates to these implementations via thin wrappers,
keeping the public API identical.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from python.agent import Agent

import python.history as history
from python.helpers import (
    dirty_json,
    event_bus,
    files,
)
from python.helpers.print_style import PrintStyle
from python.helpers.redis_history import get_redis_history_helper
from python.helpers.output_truncation import truncate_output_middle_out

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# F-8: External tools whitelist for conditional tool result injection.
# Only these tools produce data from EXTERNAL sources (web, APIs, knowledge
# bases) that warrants the "VERIFIED EXTERNAL DATA" anti-hallucination wrapper.
# All other tools (file I/O, code execution, etc.) use a minimal template
# to avoid false-positive duplicate-detection in the response quality gate.
# ---------------------------------------------------------------------------
EXTERNAL_TOOLS = {
    "web_search",
    "knowledge_tool",
    "perplexity_ask",
    "tavily_search",
    "tavily_extract",
    "tavily_crawl",
    "tavily_research",
    "examine",
    "query_docs",
    "resolve_library_id",
    "fetch_url",
    "search_web",
    "mcp_perplexity",
    "mcp_tavily",
}


def _is_external_tool(tool_name: str) -> bool:
    """Return True if tool_name is an external/data-fetching tool."""
    # Exact match first
    if tool_name in EXTERNAL_TOOLS:
        return True
    # Prefix match for MCP tools (e.g., "mcp_perplexity_ask_question")
    for prefix in ("mcp_perplexity", "mcp_tavily"):
        if tool_name.startswith(prefix):
            return True
    return False


def _get_tool_result_template(tool_name: str) -> str:
    """Select the appropriate tool result template based on tool type.

    External tools → fw.tool_result.md (full anti-hallucination wrapper)
    Internal tools → fw.tool_result_internal.md (minimal wrapper)
    """
    if _is_external_tool(tool_name):
        return "fw.tool_result.md"
    return "fw.tool_result_internal.md"


# ---------------------------------------------------------------------------
# hist_add_message
# ---------------------------------------------------------------------------
async def hist_add_message_impl(
    agent: "Agent",
    ai: bool,
    content: history.MessageContent,
    model: str = "",
    provider: str = "",
    id: str = "",
    protected: bool = False,
    sender_type: str = "",
    sender_id: str = "",
    **kwargs,
):
    """Implementation of Agent.hist_add_message — extracted verbatim."""
    # Default attribution for AI messages (this agent speaking)
    if ai:
        if not sender_type:
            sender_type = "agent"
        if not sender_id:
            sender_id = agent.agent_name

    # build content object from pieces (some extensions might add data)
    content_data = {"content": content, "kwargs": kwargs}
    # extensions call before adding to history
    await agent.call_extensions("hist_add_message_before", ai=ai, content_data=content_data)

    # check for protection marker at the end of content
    if not protected:
        test_content = content_data["content"]
        if isinstance(test_content, str) and test_content.strip().endswith(agent.PROTECTION_MARKER):
            protected = True

    # Check if this is a completion message (AI response using 'response' or similar)
    # Often completion messages contain final results we want to protect.
    if ai:
        text_content = str(content_data["content"])
        if '"tool_name": "response"' in text_content or '"tool_name": "notify_user"' in text_content:
            protected = True

    # add to history
    msg = agent.history.add_message(ai, content=content_data["content"], model=model, provider=provider, id=id, protected=protected, sender_type=sender_type, sender_id=sender_id, **content_data["kwargs"])
    if protected:
        msg.protected = True

    if not ai:
        agent.history.new_topic()  # user message starts a new topic in history
        agent.data["last_user_message"] = datetime.now(timezone.utc).isoformat()
        agent.last_user_message = msg
    else:
        agent.data["last_ai_message"] = datetime.now(timezone.utc).isoformat()
        agent.loop_data.last_ai_message = msg

    # Allow extensions to process content after adding to history
    try:
        await agent.call_extensions("hist_add_message_after", msg=msg)
    except Exception:
        pass

    return msg


# ---------------------------------------------------------------------------
# hist_add_user_message
# ---------------------------------------------------------------------------
async def hist_add_user_message_impl(
    agent: "Agent",
    message,  # UserMessage
    intervention: bool = False,
    protected: bool = False,
    sender_type: str = "",
    sender_id: str = "",
):
    """Implementation of Agent.hist_add_user_message — extracted verbatim."""
    # user message starts a new topic in history

    # Capture the RAW user message BEFORE template processing
    # This is needed for chat naming which should use the actual user input, not template-expanded content
    agent.last_raw_user_message = message.message

    # Store attachment paths on this agent so subordinates can discover them
    # via hierarchy walking (Forgejo #977 — attachment forwarding fix)
    if message.attachments:
        existing = agent.data.get("_root_attachments", [])
        # Deduplicate while preserving order
        seen = set(existing)
        for path in message.attachments:
            if path not in seen:
                existing.append(path)
                seen.add(path)
        agent.set_data("_root_attachments", existing)

    # load message template based on intervention
    if intervention:
        content = agent.parse_prompt(
            "fw.intervention.md",
            message=message.message,
            attachments=message.attachments,
            system_message=message.system_message,
        )
    else:
        content = agent.parse_prompt(
            "fw.user_message.md",
            message=message.message,
            attachments=message.attachments,
            system_message=message.system_message,
        )

    # remove empty parts from template
    if isinstance(content, dict):
        content = {k: v for k, v in content.items() if v}

    # add to history
    return await hist_add_message_impl(agent, False, content=content, protected=protected, sender_type=sender_type, sender_id=sender_id)


# ---------------------------------------------------------------------------
# hist_add_ai_response
# ---------------------------------------------------------------------------
async def hist_add_ai_response_impl(
    agent: "Agent",
    message: str,
    model: str = "",
    provider: str = "",
    id: str = "",
    protected: bool = False,
    sender_type: str = "",
    sender_id: str = "",
):
    """Implementation of Agent.hist_add_ai_response — extracted verbatim."""
    if agent.loop_data:
        agent.loop_data.last_response = message
    content = agent.parse_prompt("fw.ai_response.md", message=message)
    return await hist_add_message_impl(agent, True, content=content, model=model, provider=provider, id=id, protected=protected, sender_type=sender_type, sender_id=sender_id)


# ---------------------------------------------------------------------------
# hist_add_warning
# ---------------------------------------------------------------------------
async def hist_add_warning_impl(
    agent: "Agent",
    message: history.MessageContent,
    id: str = "",
):
    """Implementation of Agent.hist_add_warning — extracted verbatim.
    
    P1-4 Systems Audit: All warnings now flow through HintCoordinator
    for deduplication and per-turn capping (max 3/turn). This is the
    SINGLE chokepoint for 50+ hist_add_warning call sites across 30+ files.
    """
    # P1-4: Coordinate hints to prevent cascade storms
    from python.helpers.hint_coordinator import get_hint_coordinator, HintPriority
    coordinator = get_hint_coordinator()
    agent_id = getattr(agent, 'agent_name', '') or str(id(agent))
    hint_text = str(message) if message else ""
    
    # Determine priority from content
    priority = HintPriority.MEDIUM
    if any(kw in hint_text.lower() for kw in ("critical", "budget", "stop", "terminate", "emergency")):
        priority = HintPriority.CRITICAL
    elif any(kw in hint_text.lower() for kw in ("blocked", "error", "failed", "tier 3")):
        priority = HintPriority.HIGH
    
    if not coordinator.should_deliver(agent_id, hint_text, priority):
        return  # Hint suppressed — logged by coordinator
    
    content = agent.parse_prompt("fw.warning.md", message=message)
    return await hist_add_message_impl(agent, False, content=content, id=id)


# ---------------------------------------------------------------------------
# hist_add_tool_result
# ---------------------------------------------------------------------------
async def hist_add_tool_result_impl(
    agent: "Agent",
    tool_name: str,
    tool_result: str,
    sender_type: str = "",
    sender_id: str = "",
    **kwargs,
):
    """
    Record a tool execution result in the agent's history and data.
    Implementation of Agent.hist_add_tool_result — extracted verbatim.
    """
    # Universal tool result truncation (Issue #989 hardening)
    # Apply middle-out truncation to ALL tool results before any processing.
    # code_execution_tool already truncates, but browser, MCP, and other tools
    # can return 500KB+ and flood the context window → compaction spirals.
    #
    # U-3: Scale truncation by agent depth (self.number). Subordinates
    # accumulate tool results faster relative to their useful work history.
    # Root agents keep 50K, depth 1 → 30K, depth 2+ → 18K (floor).
    from python.helpers.output_truncation import truncate_output_middle_out
    max_chars = 50_000
    if agent.number > 0:
        max_chars = max(18_000, int(50_000 * (0.6 ** agent.number)))

    if isinstance(tool_result, str) and len(tool_result) > max_chars:
        original_len = len(tool_result)
        tool_result = truncate_output_middle_out(tool_result, max_chars=max_chars)
        PrintStyle(font_color="cyan").print(
            f"[TOOL_TRUNCATION] {tool_name}: {original_len:,} → {len(tool_result):,} chars "
            f"(middle-out, depth={agent.number}, budget={max_chars:,})"
        )

    if agent.loop_data:
        agent.loop_data.last_tool = tool_name
        agent.loop_data.last_tool_result = tool_result

    # process tool result via extensions
    res_data = {"tool_name": tool_name, "tool_result": tool_result, "kwargs": kwargs}
    await agent.call_extensions("hist_add_tool_result_before", res_data=res_data)

    # F-8: Conditional tool result injection — only external/data-fetching tools
    # get the "VERIFIED EXTERNAL DATA" wrapper. Internal file-ops use a simpler
    # template to prevent false-positive duplicate-detection in the quality gate.
    _template = _get_tool_result_template(res_data["tool_name"])

    # load template
    content = agent.parse_prompt(
        _template,
        tool_name=res_data["tool_name"],
        tool_result=res_data["tool_result"],
    )

    # check for protection
    protected = kwargs.get("protected", False)

    # history (only if not hidden)
    if kwargs.get("hidden", False):
        return await hist_add_message_impl(
            agent, False, content=content, protected=protected, sender_type=sender_type, sender_id=sender_id
        )

    original_result = tool_result
    res_len = len(str(tool_result))

    # Threshold for offloading large results to Redis and summarizing.
    # Configurable via settings to support various context window sizes:
    # - 100KB default: Safe for 128K context models
    # - 200-500KB: Recommended for 1-2M context models
    # - Set via "tool_result_summarization_threshold" in settings.json
    from python.helpers import settings as settings_module
    current_settings = settings_module.get_settings()
    threshold = current_settings.get("tool_result_summarization_threshold", 100000)  # Default 100KB
    
    # Check if the tool result explicitly requests to skip summarization
    skip_summary = False
    if isinstance(tool_result, dict):
        skip_summary = tool_result.get("skip_summary", False)
    elif isinstance(kwargs.get("metadata"), dict):
        skip_summary = kwargs["metadata"].get("skip_summary", False)

    is_large = res_len > threshold and not skip_summary
    summary = ""
    redis_key = ""

    # Logic for offloading large results to Redis
    if is_large:
        message_id = str(uuid.uuid4())
        redis_helper = get_redis_history_helper()
        session_id = agent.context.id or "default"
        
        try:
            # 1. Store in Redis
            await redis_helper.store_large_result(session_id, message_id, tool_result)
            redis_key = f"chat:large_result:{session_id}:{message_id}"
            
            # 2. Generate summary
            system_prompt = agent.read_prompt("fw.tool_summary.sys.md")
            # Include tool name in the message to give context to the summarizer
            msg_content = f"Tool: {tool_name}\nResult (truncated for summarizer): {truncate_output_middle_out(str(tool_result), max_lines=80, max_chars=4000, head_ratio=0.3)}" 
            
            # We use the utility model for summarization
            summary = await agent.call_utility_model(system_prompt, msg_content)
            tool_result = summary if summary else truncate_output_middle_out(str(tool_result), max_lines=10, max_chars=500, head_ratio=0.3) + "... [Truncated]"
            # Ensure the tool result is still correctly formatted for the template
            kwargs["is_summarized"] = True
            kwargs["redis_key"] = redis_key
        except Exception as e:
            PrintStyle(font_color="yellow").print(f"Failed to offload tool result to Redis or summarize: {e}")
            tool_result = truncate_output_middle_out(str(tool_result), max_lines=10, max_chars=500, head_ratio=0.3) + "... [Full output truncated due to error]"

    content = agent.parse_prompt("fw.tool_result.md", tool_name=tool_name, tool_result=tool_result)

    # Allow extensions to preprocess tool result
    data_before = {"tool_name": tool_name, "tool_result": tool_result, "kwargs": kwargs}
    await agent.call_extensions("hist_add_tool_result_before", data=data_before)
    tool_result = data_before["tool_result"]

    # Record result in agent data for supervisor/monitoring
    if "recent_tool_results" not in agent.data:
        agent.data["recent_tool_results"] = []
    
    # Limit the size of recent_tool_results to avoid memory bloat
    agent.data["recent_tool_results"].append({
        "tool_name": tool_name,
        "tool_result": truncate_output_middle_out(str(original_result), max_lines=20, max_chars=1000, head_ratio=0.3),  # Middle-out: preserve head + tail
        "success": kwargs.get("success", True),
        "error": kwargs.get("error", "") or (tool_result if not kwargs.get("success", True) else ""),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    agent.data["recent_tool_results"] = agent.data["recent_tool_results"][-20:]

    # Emit signal for failures
    if not kwargs.get("success", True):
        consecutive_failures = 0
        for r in reversed(agent.data.get("recent_tool_results", [])):
            if not r.get("success", True):
                consecutive_failures += 1
            else:
                break
        
        # Construct signal
        signal = event_bus.AgentSignal(
            signal_type=event_bus.SignalType.TOOL_FAILURE_LOOP if consecutive_failures >= 3 else event_bus.SignalType.AGENT_ERROR,
            agent_id=agent.agent_name,
            context_id=agent.context.id if agent.context else "N/A",
            timestamp=datetime.now(timezone.utc),
            severity="high" if consecutive_failures >= 3 else "medium",
            details={
                "consecutive_failures": consecutive_failures, 
                "tool_name": tool_name,
                "arguments": kwargs.get("arguments", {})
            },
            tool_name=tool_name,
            error_message=str(tool_result)[:500],
            iteration=getattr(agent.loop_data, 'iteration', 0)
        )
        event_bus.get_event_bus().publish_sync(signal)

    # add to history
    return await hist_add_message_impl(agent, False, content=content, **kwargs)


# ---------------------------------------------------------------------------
# prepare_prompt
# ---------------------------------------------------------------------------
async def prepare_prompt_impl(agent: "Agent", loop_data) -> list:
    """Implementation of Agent.prepare_prompt — extracted verbatim (Issue #1200 P0.2)."""
    from langchain_core.messages import BaseMessage, SystemMessage
    from langchain_core.prompts import ChatPromptTemplate
    from python.helpers import tokens

    agent.context.log.set_progress("Building prompt")

    # call extensions before setting prompts
    logger.info(f"[PROMPT_TRACE] {agent.agent_name} prompts_before START")
    await agent.call_extensions("message_loop_prompts_before", loop_data=loop_data)
    logger.info(f"[PROMPT_TRACE] {agent.agent_name} prompts_before DONE")

    # set system prompt and message history
    logger.info(f"[PROMPT_TRACE] {agent.agent_name} get_system_prompt START")
    loop_data.system = await agent.get_system_prompt(agent.loop_data)
    logger.info(f"[PROMPT_TRACE] {agent.agent_name} get_system_prompt DONE")
    loop_data.history_output = agent.history.output()

    # and allow extensions to edit them
    logger.info(f"[PROMPT_TRACE] {agent.agent_name} prompts_after START")
    await agent.call_extensions("message_loop_prompts_after", loop_data=loop_data)
    logger.info(f"[PROMPT_TRACE] {agent.agent_name} prompts_after DONE")

    # concatenate system prompt
    system_text = "\n\n".join(loop_data.system)

    # join extras
    extras = history.Message(  # type: ignore[abstract]
        False,
        content=agent.read_prompt(
            "agent.context.extras.md",
            extras=dirty_json.stringify(
                {**loop_data.extras_persistent, **loop_data.extras_temporary}
            ),
        ),
    ).output()
    loop_data.extras_temporary.clear()

    # convert history + extras to LLM format
    history_langchain: list[BaseMessage] = history.output_langchain(
        loop_data.history_output + extras
    )

    # build full prompt from system prompt, message history and extras
    full_prompt: list[BaseMessage] = [
        SystemMessage(content=system_text),
        *history_langchain,
    ]
    full_text = ChatPromptTemplate.from_messages(full_prompt).format()

    # store as last context window content
    tokens_count = tokens.approximate_tokens(full_text)
    agent.set_data(
        agent.DATA_NAME_CTX_WINDOW,
        {
            "text": full_text,
            "tokens": tokens_count,
        },
    )

    # Proactive History Compression (CP-001)
    # Check if we are approaching the context limit before making the LLM call
    from python.helpers import settings
    conf = settings.get_settings()
    threshold = agent.config.additional.get("context_condense_threshold", conf.get("context_condense_threshold", 0.72))
    
    # FIX Issue #407: Use model's actual context window, not global settings
    # The model's context window takes precedence over settings default (e.g., 1M for Gemini vs 200k default)
    ctx_limit = conf.get("chat_model_ctx_length", 0)  # Use 0 to enable model-specific resolution below
    try:
        chat_model = agent.config.chat_model
        chat_model_name = chat_model.name if chat_model else None
        
        # Priority 1: Use explicitly configured ctx_length from model config or wrapper property
        if chat_model and hasattr(chat_model, "ctx_length") and chat_model.ctx_length > 0:
            ctx_limit = chat_model.ctx_length
        
        # Priority 2: Try to resolve via model metadata lookup (consistent with prepare_prompt logic)
        elif chat_model_name:
            from python.models import get_model_context_window
            model_ctx = get_model_context_window(chat_model_name)
            if model_ctx > 0:
                ctx_limit = model_ctx
        
        # Priority 3: Final safe fallback
        if ctx_limit <= 0:
            ctx_limit = 128000

        # Print to console for monitoring if ratio is significant or it's a fresh prompt
        ratio = tokens_count / ctx_limit if ctx_limit > 0 else 0
        if ratio > 0.5 or len(loop_data.history_output) <= 2:
            PrintStyle(font_color="cyan").print(f"DEBUG [prepare_prompt]: model={chat_model_name}, tokens={tokens_count}, limit={ctx_limit}, threshold={threshold}, ratio={ratio:.2f}")
    except Exception as e:
        # Keep settings default on error
        pass
    
    # Use a flag in loop_data to prevent infinite recursion
    has_condensed = getattr(loop_data, "_has_condensed", False)

    if tokens_count > (ctx_limit * threshold) and not has_condensed:
        # Issue #416: Intelligent handling of context limits
        if len(loop_data.history_output) > 2:
            # Normal proactive compression path for existing history
            agent.log(
                type="info",
                heading="🔄 Context condensed",
                content=f"Context usage ({tokens_count}) > {int(threshold * 100)}% of limit ({ctx_limit}).",
            )
        else:
            # Turn 1 path - massive prompt recovery
            agent.log(
                type="info",
                heading="🧘 Prompt condensed",
                content=f"Initial prompt ({tokens_count}) > limit ({ctx_limit}). Correcting...",
            )

        loop_data._has_condensed = True
        from python.helpers.context_error_recovery import get_recovery_handler
        await get_recovery_handler()._condense_for_recovery(agent)
        
        # Re-prepare prompt with condensed history/messages
        return await prepare_prompt_impl(agent, loop_data)

    return full_prompt

