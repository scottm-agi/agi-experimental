"""
Agent Model Interaction Methods — Extracted from agent.py (Issue #1200 P0.2).

This module contains the implementation of model-calling methods that were
previously in the Agent class. Each function accepts the agent instance as
its first argument (``agent``) and preserves the exact same logic as the
original inline methods.

The Agent class now delegates to these implementations via thin wrappers,
keeping the public API identical.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Tuple

if TYPE_CHECKING:
    from python.agent import Agent

from langchain_core.messages import BaseMessage

from python.helpers import (
    prompt_router,
    settings,
    tokens,
)
from python.helpers.print_style import PrintStyle
from python.helpers.errors import InterventionException
from python.helpers.observer_mesh import ObserverMesh
from python.models import get_model_context_window
import python.models as models

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# call_utility_model
# ---------------------------------------------------------------------------
async def call_utility_model_impl(
    agent: "Agent",
    system: str,
    message: str,
    callback: Callable[[str], Awaitable[None]] | None = None,
    background: bool = False,
):
    """Implementation of Agent.call_utility_model — extracted verbatim."""
    model = agent.get_utility_model()
    
    # Proactively check if input is likely to exceed the primary utility model's window
    input_tokens = tokens.approximate_tokens(system + message)
    model_ctx = getattr(getattr(model, "agix_model_conf", None), "ctx_length", 0) or get_model_context_window(getattr(model, "name", "")) or 128000
    
    # If input is too large (approaching 90% of model limit), use Grok 4.1 Fast (2M)
    if input_tokens > (model_ctx * 0.9):
        model_name = "Gemini 3 Flash (1M)" if not settings.get_settings().get("grok_fallback_enabled") else "Grok 4.1 Fast (2M)"
        agent.log(
            heading="🚀 Context peak recovery",
            content=f"Input ({input_tokens} tokens) > 90% of limit ({model_ctx}). Using {model_name}.",
            type="system"
        )
        PrintStyle(font_color="yellow").print(f"{agent.agent_name}: Input ({input_tokens} tokens) is too large for primary utility model ({model_ctx}). Falling back to {model_name}...")
        model = agent.get_grok_fallback_model()

    async def perform_call(target_model):
        # call extensions
        call_data = {
            "model": target_model,
            "system": system,
            "message": message,
            "callback": callback,
            "background": background,
        }
        await agent.call_extensions("util_model_call_before", call_data=call_data)

        async def stream_callback(chunk: str, total: str):
            if call_data["callback"]:
                await call_data["callback"](chunk)

        if not call_data["model"]:
            PrintStyle(background_color="black", font_color="yellow", padding=False).print("Utility model not configured, skipping call.")
            return ""

        try:
            # [FIX] Utility model calls use shorter timeout (60s) and fewer retries (3)
            # to prevent indefinite hangs during memory recall after restart.
            # Root cause: unified_call defaults to timeout=300s, retries=10 which means
            # a utility call can silently retry for 50+ minutes if the API is down.
            response, _reasoning, _model, _provider = await call_data["model"].unified_call(
                system_message=call_data["system"],
                user_message=call_data["message"],
                response_callback=stream_callback if call_data["callback"] else None,
                agix_agent=agent,
                rate_limiter_callback=agent.rate_limiter_callback if not call_data["background"] else None,
                agix_cache=True,
                timeout=60,              # 60s max per attempt (vs 300s default)
                agix_retry_attempts=3,     # 3 retries max (vs 10 default)
            )
            return response
        except Exception as e:
            print(f"DEBUG [call_utility_model]: Unified call failed: {e}", file=sys.stderr)
            # If call fails due to context length, retry with Grok fallback if we haven't already
            if ("context_length" in str(e).lower() or "too many tokens" in str(e).lower()) and target_model != agent.get_grok_fallback_model():
                model_name = "Gemini 3 Flash (1M)" if not settings.get_settings().get("grok_fallback_enabled") else "Grok 4.1 Fast (2M)"
                agent.log(
                    heading="🚀 Context peak recovery (Retry)",
                    content=f"Utility model context limit exceeded. Retrying with {model_name}.",
                    type="system"
                )
                PrintStyle(font_color="yellow", padding=True).print(f"{agent.agent_name}: Utility model failed context limits. Retrying with {model_name}...")
                return await perform_call(agent.get_grok_fallback_model())
            raise e

    return await perform_call(model)


# ---------------------------------------------------------------------------
# call_chat_model
# ---------------------------------------------------------------------------
async def call_chat_model_impl(
    agent: "Agent",
    messages: list[BaseMessage],
    response_callback: Callable[[str, str], Awaitable[None]] | None = None,
    reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
    background: bool = False,
) -> Tuple[str, str, str, str]:
    """Implementation of Agent.call_chat_model — extracted verbatim."""
    response = ""

    # Priority logic for role selection:
    # 1. Agent profile-based role (from UI dropdown or call_subordinate) takes primary precedence
    #    This includes subordinates spawned with an explicit profile — they must
    #    retain their assigned identity.
    # 2. Default/unset profile → use the global chat_model
    #
    # NOTE: PromptRouter model-routing DISCONNECTED (2026-06-10).
    # Previously, this code dynamically switched LLM models based on message
    # content (including hardcoding Agent 0 → alex). Now each agent profile
    # defines its own model in role_configurations, and default/Agent 0 use
    # the global chat_model. Agent routing (which agent handles the request)
    # is handled by the route_to_agent tool — a separate concern from model
    # selection. PromptRouter.classify() is reused by route_to_agent for
    # AGENT routing, not model routing.
    role_configs = settings.get_settings().get("role_configurations", {})
    
    if agent.config.profile and agent.config.profile != "default":
        # Use the explicit profile — whether it's in role_configs or not.
        # Subordinates with explicit profiles (e.g. "browser", "code") must retain
        # their assigned identity to prevent re-classification loops.
        profile_id = agent.config.profile
    else:
        # Default profile and Agent 0: use global chat_model.
        # Intent-based routing happens at the TOOL level (route_to_agent),
        # not at the model selection level.
        profile_id = "chat_model"
    
    # Try to use existing model from data ONLY if we're not doing role-based resolution
    # This ensures profile settings from role_configurations are always applied fresh
    model = None
    
    # Only use cached model if profile_id is explicitly "chat_model" (global fallback)
    if profile_id == "chat_model":
        model = agent.get_data("chat_model")
        # Guard: After crash recovery, serialized model comes back as a dict
        # instead of a LazyModelWrapper. Discard and resolve fresh.
        if model and not hasattr(model, "unified_call"):
            logger.warning(
                f"[CHAT_MODEL] Cached chat_model is {type(model).__name__}, "
                f"not a model wrapper. Discarding stale cache."
            )
            model = None
        
    if not model:
        # Always resolve role-based profiles dynamically via role_configurations.
        # This supports agents created/removed at runtime without hardcoded lists.
        # The "chat_model" case is already handled above.
        if profile_id != "chat_model":
            model = models.get_chat_model("role", profile_id)
        else:
            model = models.get_chat_model(
                settings.get_settings().get("chat_model_provider", "openrouter"),
                settings.get_settings().get("chat_model_name", "google/gemini-3-flash-preview"),
            )

    # Proactively check if input is likely to exceed the primary model's window
    try:
        input_text = ""
        for msg in messages:
            if hasattr(msg, "content"):
                if isinstance(msg.content, str):
                    input_text += msg.content
                elif isinstance(msg.content, list):
                    for part in msg.content:
                        if isinstance(part, dict) and "text" in part:
                            input_text += part["text"]
        
        input_tokens = tokens.approximate_tokens(input_text)
        model_ctx = getattr(model, "ctx_length", 0) or get_model_context_window(getattr(model, "name", "")) or 128000
        
    except Exception as e:
        # Fallback estimation failed, just continue with original model
        pass

    retries = 0
    async def perform_call(target_model):
        nonlocal messages, response_callback, reasoning_callback, background, retries
        
        try:
            # Use a specific timeout for fallback calls to prevent hangs
            call_timeout = 180 
            
            # ── Progressive Repetition Recovery: Temperature Override ──
            # If the repetition recovery manager set a temp override,
            # apply it to this call and then clear it (one-shot).
            extra_kwargs = {}
            try:
                from python.helpers.repetition_recovery import (
                    get_temp_override,
                    clear_temp_override,
                )
                temp_delta = get_temp_override(agent.data)
                if temp_delta is not None and temp_delta > 0:
                    extra_kwargs["temperature"] = temp_delta
                    clear_temp_override(agent.data)
                    logger.info(
                        f"[REPETITION_RECOVERY] Applying temp override: "
                        f"temperature={temp_delta} for {agent.agent_name}"
                    )
            except ImportError:
                pass  # Module not available — skip gracefully

            response, reasoning, model_name, provider = await target_model.unified_call(
                messages=messages,
                reasoning_callback=reasoning_callback,
                response_callback=response_callback,
                rate_limiter_callback=agent.rate_limiter_callback if not background else None,
                timeout=call_timeout,
                agix_agent=agent,
                **extra_kwargs,
            )
            return response, reasoning, model_name, provider
        except Exception as e:
            print(f"DEBUG [call_chat_model]: Unified call failed for {getattr(target_model, 'name', 'unknown')}: {e}", file=sys.stderr)
            err_str = str(e).lower()
            is_context_error = (
                "context_length" in err_str or 
                "too many tokens" in err_str or 
                "maximum context length" in err_str or
                "token" in err_str and ("limit" in err_str or "exceed" in err_str) or
                "context window" in err_str
            )

            if is_context_error and retries < 2:
                retries += 1
                
                # REFRESH MESSAGES: On retry, we MUST re-derive messages from history 
                # because they might have been pruned/summarized during the automated 
                # recovery handler's condensation phase.
                if agent.history and hasattr(agent, "history"):
                    # MUST use output_langchain to get LangChain BaseMessage format
                    from python.history import output_langchain
                    messages = output_langchain(agent.history.output())

                agent.log(
                    heading="🚀 Context Recovery (Retry)",
                    content=f"Chat model failed (likely context). Retrying after history refresh. Error: {str(e)[:100]}",
                    type="system"
                )
                PrintStyle(font_color="yellow", padding=True).print(f"{agent.agent_name}: Context overflow detected. Retrying after recovery...")
                return await perform_call(target_model)
            elif is_context_error: # retry failed, move to condensation
                # EVEN FALLBACK FAILED. PERFORM CRITICAL PRUNING AND RETRY.
                # Check if we've already tried condensation
                if retries >= 3:
                    agent.log(
                        heading="❌ Context Recovery Failed",
                        content=f"Max retries ({retries}) exceeded after condensation. Context may be irreducible.",
                        type="error"
                    )
                    raise e
                
                agent.log(
                    heading="⚠️ Context Overflow - Repairing",
                    content="Context failed. Performing aggressive history condensation and retrying...",
                    type="system"
                )
                # This prunes history to 90% of model limit
                success = await agent.force_history_condensation(e)
                if success:
                    # Refresh messages from pruned history (very important!)
                    # MUST use output_langchain to get LangChain BaseMessage format
                    from python.history import output_langchain
                    messages = output_langchain(agent.history.output())
                    after_tokens = agent.history.get_tokens()
                    agent.log(
                        heading="✓ Context Recovered",
                        content=f"History condensed successfully ({after_tokens:,} tokens). Continuing task...",
                        type="system"
                    )
                    retries += 1  # Increment to prevent infinite loop
                    return await perform_call(target_model)
                else:
                    agent.log(
                        heading="❌ Condensation Failed",
                        content="Could not condense history. The context may be irreducible.",
                        type="error"
                    )
                    raise e
            else: # Not a context error, just raise
                raise e
            
            raise e


    # call model
    start_time = time.perf_counter()
    try:
        response, reasoning, model_name, provider = await perform_call(model)
        duration = time.perf_counter() - start_time
        await ObserverMesh.get_instance().record_api_request(
            service=provider,
            method=model_name,
            duration=duration,
            success=True
        )
    except InterventionException:
        # Let interventions bubble up to monologue()
        raise
    except Exception as e:
        duration = time.perf_counter() - start_time
        await ObserverMesh.get_instance().record_api_request(
            service="unknown",
            method="unknown",
            duration=duration,
            success=False
        )
        # Handle other errors
        await agent.handle_critical_exception(e)
        raise  # Re-raise as HandledException usually

    return response, reasoning, model_name, provider


# ---------------------------------------------------------------------------
# rate_limiter_callback
# ---------------------------------------------------------------------------
async def rate_limiter_callback_impl(
    agent: "Agent",
    message: str,
    key: str,
    total: int,
    limit: int,
):
    """Implementation of Agent.rate_limiter_callback — extracted verbatim."""
    # STATUS_CHECK is a silent poll from the rate limiter's sleep loop
    is_status_check = message == "STATUS_CHECK"

    if not is_status_check:
        # Log visible message in chat so supervisor and user can see
        agent.log(
            type="warning",
            heading="⏳ Rate Limit Active",
            content=f"Waiting due to rate limit: {message}\nUsage: {total}/{limit} requests",
        )
        # show the rate limit waiting in a progress bar
        agent.context.log.set_progress(message, True)

    # Skip waiting if we have a pending intervention (especially supervisor guidance)
    # to ensure the agent remains responsive.
    if agent.intervention:
        intervention_str = str(agent.intervention)
        if 'REMOTE SUPERVISOR GUIDANCE' in intervention_str or 'SUPERVISOR HINT' in intervention_str:
            agent.log(type="info", content="Priority supervisor guidance detected, skipping rate limit wait.", verbose=True)
            return True
        return True  # Break for any intervention to be responsive
        
    return False
