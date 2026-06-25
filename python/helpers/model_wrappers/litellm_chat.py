from __future__ import annotations
import logging
import sys
import os
import time
from typing import Any, Awaitable, Callable, List, Dict, Optional, Tuple, Union, AsyncIterator, Iterator
from pydantic import ConfigDict, Field, PrivateAttr
from python.helpers.litellm_shim import completion, acompletion
import litellm
from litellm.types.utils import ModelResponse

from python.helpers.llm_cache import get_llm_cache
from python.helpers.token_tracker import get_token_tracker
from python.helpers.agent_tracer import AgentTracer
from python.helpers.tokens import approximate_tokens
from python.helpers.errors import InterventionException, RepetitionException, TruncationException
from python.helpers.strings import sanitize_surrogates
from python.helpers.providers import get_provider_config
from python.helpers import browser_use_monkeypatch

from .base import (
    SimpleChatModel, 
    BaseMessage, 
    AIMessageChunk, 
    HumanMessage, 
    SystemMessage, 
    ChatGenerationChunk,
    ChatGenerationResult,
    ModelConfig,
    _parse_chunk
)
from .rate_limiting import (
    apply_rate_limiter, 
    apply_rate_limiter_sync, 
    notify_llm_failure, 
    notify_llm_retry,
    is_transient_litellm_error
)

logger = logging.getLogger(__name__)

def get_model_context_window(model_id: str) -> int:
    """Returns the context window for a given model ID, or a default value."""
    from python.models import get_model_context_window as global_get_ctx
    return global_get_ctx(model_id)


def _is_degenerate_repetition(
    text: str,
    min_len: int = 4000,
    threshold: int = 3,
) -> bool:
    """Detect true model degeneration: the same block repeated consecutively.

    RCA-370: The old heuristic ``text[-20:].count() > 5`` false-positived on
    structured markdown completion reports where section endings naturally
    repeat (e.g., bulleted lists, JSON closing braces). This replacement
    checks for *consecutive* repetition of trailing content, dynamically
    discovering the repeating unit size.

    Args:
        text: The LLM response text to check.
        min_len: Minimum text length before checking (avoids short texts).
        threshold: How many consecutive copies constitute degeneration.

    Returns:
        True if the trailing portion of the text contains ``threshold`` or
        more consecutive copies of the same block.
    """
    if len(text) < min_len:
        return False

    # Try multiple window sizes from small (1 char) to large (200 chars)
    # to discover the repeating unit at any granularity
    for w in range(1, min(201, len(text) // threshold + 1)):
        suffix = text[-w:]
        if not suffix.strip():
            continue  # Skip all-whitespace
        # Count consecutive backward copies starting from the end
        consecutive = 0
        pos = len(text)
        while pos >= w:
            candidate = text[pos - w : pos]
            if candidate == suffix:
                consecutive += 1
                pos -= w
            else:
                break
            if consecutive >= threshold:
                return True

    return False


class LiteLLMChatWrapper(SimpleChatModel):

    model_name: str
    provider: str
    kwargs: dict = {}
    agix_model_conf: Optional[ModelConfig] = None
    agix_fallback_delay: float = 0.0

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow",
        validate_assignment=False,
    )

    def __init__(
        self,
        model: str,
        provider: str,
        model_config: Optional[ModelConfig] = None,
        **kwargs: Any,
    ):
        provider_prefix = f"{provider}/"
        if not model.startswith(provider_prefix):
            model_value = f"{provider}/{model}"
        else:
            model_value = model
        self._model_name_val = model_value

        # [FIX] Extract internal profile parameters and set them as attributes
        # This keeps self.kwargs clean for the LLM API while allowing properties like ctx_length to function.
        filtered_kwargs = kwargs.copy()
        for k in list(filtered_kwargs.keys()):
            if k.startswith("_profile_") or k == "agix_agent":
                setattr(self, str(k), filtered_kwargs.pop(k))
        
        # Filter out model_name to avoid "can't set attribute" errors
        filtered_kwargs.pop("model_name", None)
        agix_fallback_delay = float(filtered_kwargs.pop("agix_fallback_delay", 0.0))

        super().__init__(model_name=model_value, provider=provider, kwargs=filtered_kwargs, agix_model_conf=model_config, agix_fallback_delay=agix_fallback_delay)  # type: ignore

    @property
    def display_provider(self) -> str:
        """Returns the human-readable provider name from configuration."""
        config = get_provider_config("chat", self.provider)
        if config:
            return config.get("name") or self.provider.capitalize()
        return self.provider.capitalize()

    @property
    def display_model(self) -> str:
        """Returns a clean model name, removing redundant vendor/provider prefixes."""
        name = self.model_name
        parts = name.split("/")
        if parts[0].lower() == self.provider.lower():
            parts = parts[1:]
        clean_name = "/".join(parts)
        if self.provider.lower() == "venice" and clean_name.startswith("openai/"):
            clean_name = clean_name.replace("openai/", "", 1)
        return clean_name

    @property
    def ctx_length(self) -> int:
        """Returns the resolved context window for this model, preferring profile overrides."""
        if self.agix_model_conf and hasattr(self.agix_model_conf, "ctx_length") and self.agix_model_conf.ctx_length > 0:
            return self.agix_model_conf.ctx_length
        profile_len = getattr(self, "_profile_ctx_length", 0)
        if profile_len > 0:
            return profile_len
        return get_model_context_window(self.model_name)
    
    @property
    def max_tokens(self) -> int:
        """Returns the resolved max tokens for this model."""
        profile_max = getattr(self, "_profile_max_tokens", 0)
        if profile_max > 0:
            return profile_max
        if self.agix_model_conf:
            return self.agix_model_conf.max_tokens or 0
        return 0

    @property
    def model_name(self) -> str:
        """Compatibility property for tests."""
        return self._model_name_val

    @property
    def name(self) -> str:
        """Compatibility property for tests."""
        return self._model_name_val

    @property
    def _llm_type(self) -> str:
        return "litellm-chat"

    def _convert_messages(self, messages: List[BaseMessage]) -> List[dict]:
        result = []
        role_mapping = {
            "human": "user",
            "ai": "assistant",
            "system": "system",
            "tool": "tool",
            "aimessagechunk": "assistant",
            "humanmessage": "user",
            "systemmessage": "system"
        }
        for m in messages:
            if isinstance(m, dict):
                m_type = m.get("role", "user")
                content = m.get("content", "")
            else:
                m_type = getattr(m, "type", None)
                if m_type is None:
                    cls_name = type(m).__name__.lower()
                    if "system" in cls_name: m_type = "system"
                    elif "ai" in cls_name: m_type = "ai"
                    elif "human" in cls_name: m_type = "human"
                    elif "tool" in cls_name: m_type = "tool"
                    else: m_type = cls_name
                content = getattr(m, "content", "")
            
            role = role_mapping.get(m_type.lower(), m_type)
            message_dict = {"role": role, "content": content}

            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                new_tool_calls = []
                for tool_call in tool_calls:
                    args = tool_call["args"]
                    import json
                    args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                    new_tool_calls.append({
                        "id": tool_call.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tool_call["name"],
                            "arguments": args_str,
                        },
                    })
                message_dict["tool_calls"] = new_tool_calls

            tool_call_id = getattr(m, "tool_call_id", None)
            if tool_call_id:
                message_dict["tool_call_id"] = tool_call_id
            result.append(message_dict)
        return result

    async def unified_call(
        self,
        system_message="",
        user_message="",
        messages: Optional[List[BaseMessage]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        response_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
        reasoning_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
        tokens_callback: Optional[Callable[[str, int], Awaitable[None]]] = None,
        rate_limiter_callback: Optional[
            Callable[[str, str, int, int], Awaitable[bool]]
        ] = None,
        **kwargs: Any,
    ) -> Tuple[str, str, str, str]:
        agix_agent = kwargs.pop("agix_agent", None)
        # F-3 (RCA-467): Allow utility summarization calls to skip repetition
        # detection. The check is designed for interactive agent responses
        # where repetition = stuck loop. For summarization of repetitive
        # history content, it produces false positives.
        agix_skip_repetition_check = kwargs.pop("agix_skip_repetition_check", False)
        if agix_agent:
            agix_agent.data["is_retrying"] = False
            agix_agent.data["retry_info"] = {}

        if not messages:
            messages = []
        if system_message:
            messages.insert(0, SystemMessage(content=system_message))
        if user_message:
            messages.append(HumanMessage(content=user_message))

        msgs_conv = self._convert_messages(messages)
        limiter = await apply_rate_limiter(self.agix_model_conf, str(msgs_conv), rate_limiter_callback)

        call_kwargs: dict[str, Any] = {**self.kwargs, **kwargs}
        max_retries: int = int(call_kwargs.pop("agix_retry_attempts", 10))
        retry_delay_s: float = float(call_kwargs.pop("agix_retry_delay_seconds", 1.5))
        stream = reasoning_callback is not None or response_callback is not None or tokens_callback is not None

        attempt = 0
        _call_start_time = time.time()
        while True:
            result = ChatGenerationResult()  # [FIX] Reset result on each retry attempt
            got_any_chunk = False
            try:
                actual_kwargs = call_kwargs.copy()
                silent_failover = actual_kwargs.pop("agix_silent_failover", False)
                # Fix (RCA-2026-04-20): Reduced from 600→300s. 600s caused batch
                # hangs on dead CLOSE_WAIT connections. 300s covers non-streaming
                # utility model calls while failing 2× faster on dead connections.
                # The batch-level asyncio.wait(timeout=) is the real safety net.
                actual_kwargs["timeout"] = actual_kwargs.get("timeout", 300.0)
                
                # [FIX] Heartbeat log so Docker logs show the agent is alive during LLM calls.
                # Without this, the agent appears completely dead during 300s timeout waits.
                logger.info(
                    f"[LLM_CALL] model={self.model_name} attempt={attempt+1}/{max_retries} "
                    f"timeout={actual_kwargs['timeout']}s stream={stream}"
                )
                
                if tools:
                    actual_kwargs["tools"] = tools
                    if tool_choice:
                        actual_kwargs["tool_choice"] = tool_choice

                from python.helpers.settings import get_settings as _get_settings
                use_cache = actual_kwargs.pop("agix_cache", _get_settings().get("llm_cache_enabled", True))
                cache = get_llm_cache()
                if use_cache:
                    cached = await cache.get(self.model_name, msgs_conv, **actual_kwargs)
                    if cached:
                        return cached.get("response", ""), cached.get("reasoning", ""), self.display_model, self.display_provider

                if self.agix_model_conf:
                    actual_kwargs = {**self.agix_model_conf.build_kwargs(), **actual_kwargs}

                # [FIX] Strip all internal keys starting with '_' before passing to LiteLLM
                # These keys (like _profile_ctx_length) are for our internal logic only.
                actual_kwargs = {k: v for k, v in actual_kwargs.items() if not k.startswith("_")}

                # [FIX] Resolve LiteLLM provider from config if available (e.g. Venice -> OpenAI)
                config = get_provider_config("chat", self.provider)
                litellm_provider = config.get("litellm_provider") if config else self.provider

                # [FIX] Double-prefix bug fix:
                # When litellm_provider == self.provider (e.g. both "openrouter"), pass self.model_name
                # directly — litellm routes from "openrouter/model" natively and strips correctly.
                # When shimmed (e.g. venice→openai), strip our prefix and use custom_llm_provider.
                # Root cause: acompletion(model="google/gemini-3-flash-preview", custom_llm_provider="openrouter")
                # causes litellm to re-add the openrouter/ prefix in the JSON body, making OpenRouter reject it.
                is_shimmed = litellm_provider and litellm_provider.lower() != self.provider.lower()
                if is_shimmed:
                    # Strip our internal provider prefix, let litellm use custom_llm_provider
                    actual_model = self.model_name
                    if "/" in actual_model:
                        parts = actual_model.split("/", 1)
                        if parts[0].lower() == self.provider.lower():
                            actual_model = parts[1]
                    extra = {"custom_llm_provider": litellm_provider}
                else:
                    # Non-shimmed: pass the full "provider/model" string directly, no custom_llm_provider
                    # litellm recognizes the prefix and routes correctly without double-prefixing
                    actual_model = self.model_name
                    extra = {}

                _completion = await acompletion(
                    model=actual_model,
                    messages=msgs_conv,
                    stream=stream,
                    **extra,
                    **actual_kwargs,
                )

                if stream:
                    async for chunk in _completion:  # type: ignore
                        got_any_chunk = True
                        parsed = _parse_chunk(chunk)
                        output = result.add_chunk(parsed)
                        if output["reasoning_delta"]:
                            if reasoning_callback: await reasoning_callback(output["reasoning_delta"], result.reasoning)
                            if tokens_callback: await tokens_callback(output["reasoning_delta"], approximate_tokens(output["reasoning_delta"]))
                            if limiter: limiter.add(output=approximate_tokens(output["reasoning_delta"]))
                        if output["response_delta"]:
                            if response_callback: await response_callback(output["response_delta"], result.response)
                            if tokens_callback: await tokens_callback(output["response_delta"], approximate_tokens(output["response_delta"]))
                            if limiter: limiter.add(output=approximate_tokens(output["response_delta"]))
                else:
                    parsed = _parse_chunk(_completion)
                    output = result.add_chunk(parsed)
                    if limiter:
                        if output["response_delta"]: limiter.add(output=approximate_tokens(output["response_delta"]))
                        if output["reasoning_delta"]: limiter.add(output=approximate_tokens(output["reasoning_delta"]))

                response, reasoning = await self._finalize_result(result, use_cache, msgs_conv, actual_kwargs)

                # P2: Sanitize surrogates at LLM ingestion boundary.
                # LLM responses can contain lone surrogate characters (\ud800-\udfff)
                # that cause UnicodeEncodeError in downstream .encode('utf-8') calls.
                response = sanitize_surrogates(response)
                reasoning = sanitize_surrogates(reasoning)
                result.response = response
                result.reasoning = reasoning
                
                if not agix_skip_repetition_check and _is_degenerate_repetition(result.response):
                     raise RepetitionException(response, reasoning, self.display_model, self.display_provider)

                if result.finish_reason == "length":
                     raise TruncationException(response, reasoning, self.display_model, self.display_provider)

                # Log token usage for analytics (Issue #799)
                call_duration_ms = (time.time() - _call_start_time) * 1000
                try:
                    _settings_for_tracking = _get_settings()
                    if _settings_for_tracking.get("token_tracking_enabled", True):
                        tracker = get_token_tracker()
                        # Extract token counts from the response usage data if available
                        usage_in = getattr(result, '_usage_prompt_tokens', approximate_tokens(str(msgs_conv)))
                        usage_out = approximate_tokens(result.response + (result.reasoning or ""))
                        await tracker.log_usage(
                            model=self.model_name,
                            tokens_in=usage_in,
                            tokens_out=usage_out,
                            call_site="unified_call",
                            agent_id=str(getattr(agix_agent, 'number', '')) if agix_agent else "",
                            agent_name=getattr(agix_agent, 'agent_name', '') if agix_agent else "",
                            duration_ms=call_duration_ms,
                            session_id=getattr(AgentTracer.get_current_trace(), 'trace_id', '') if AgentTracer.is_enabled() else '',
                            chat_id=getattr(agix_agent.context, 'id', '') if agix_agent and getattr(agix_agent, 'context', None) else '',
                            project=agix_agent.data.get('_active_project_name', '') if agix_agent and hasattr(agix_agent, 'data') else '',
                        )
                except Exception as _track_err:
                    logger.debug(f"Token tracking log failed (non-critical): {_track_err}")

                if attempt > 0:
                    logger.warning(
                        f"[LLM_RECOVERED] model={self.model_name} | "
                        f"recovered after {attempt} retries"
                    )
                logger.info(f"Using model: {self.model_name}")
                if agix_agent:
                    agix_agent.data["is_retrying"] = False
                return result.response, result.reasoning, self.display_model, self.display_provider

            except Exception as e:
                import asyncio
                if isinstance(e, InterventionException): raise
                # [FIX] Removed got_any_chunk blocker to allow retries for mid-stream transient errors (Issue #Recovery)
                if not is_transient_litellm_error(e) or attempt >= max_retries:
                    if not silent_failover: notify_llm_failure(self.provider, self.model_name, e)
                    if agix_agent:
                        agix_agent.data["is_retrying"] = False
                    raise
                
                from .rate_limiting import handle_rate_limit_error, _is_rate_limit_error
                if _is_rate_limit_error(e):
                    delay = await handle_rate_limit_error(e, self.agix_model_conf, attempt, max_retries)
                else:
                    from .rate_limiting import calculate_retry_delay_for_error
                    delay = calculate_retry_delay_for_error(e, attempt, base_delay=retry_delay_s)
                
                attempt += 1
                error_type = "RATE_LIMIT" if _is_rate_limit_error(e) else "TRANSIENT"
                logger.warning(
                    f"[LLM_BACKOFF] {error_type} | model={self.model_name} | "
                    f"attempt={attempt}/{max_retries} | delay={delay:.1f}s | "
                    f"error={str(e)[:200]}"
                )
                if not silent_failover:
                    notify_llm_retry(self.provider, self.model_name, attempt, max_retries, delay, e)
                
                if agix_agent:
                    agix_agent.data["is_retrying"] = True
                    agix_agent.data["retry_info"] = {
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "delay": delay,
                        "error": str(e)
                    }
                
                await asyncio.sleep(delay)

    async def _finalize_result(self, result: ChatGenerationResult, use_cache, msgs_conv, actual_kwargs):
        response, reasoning = result.response, result.reasoning
        tool_calls = getattr(result, "tool_calls", None)
        if tool_calls:
            import json
            for tc in result.tool_calls:
                agix_format = {
                    "thoughts": [reasoning] if reasoning else [],
                    "tool_name": tc["function"]["name"],
                    "tool_args": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                }
                agix_json = json.dumps(agix_format, indent=2)
                response = (response + "\n\n" + agix_json) if response else agix_json
                break
        
        if use_cache:
            cache = get_llm_cache()
            await cache.set(self.model_name, msgs_conv, {"response": response, "reasoning": reasoning}, **actual_kwargs)
        return response, reasoning

class BrowserCompatibleChatWrapper(SimpleChatModel):
    model_config = ConfigDict(extra='allow', arbitrary_types_allowed=True, protected_namespaces=())
    model: str = Field(default="")
    provider: str = Field(default="")
    model_config_data: Optional[ModelConfig] = Field(default=None)
    kwargs: dict = Field(default_factory=dict)
    _wrapper: LiteLLMChatWrapper = PrivateAttr()

    def __init__(self, wrapper: Optional[LiteLLMChatWrapper] = None, model: str = "", provider: str = "", model_config: Optional[ModelConfig] = None, **kwargs: Any):
        super().__init__(model=model, provider=provider, model_config_data=model_config, kwargs=kwargs) 
        if wrapper is not None: self._wrapper = wrapper
        else: self._wrapper = LiteLLMChatWrapper(model=model, provider=provider, model_config=model_config, **kwargs)
        self.model = self._wrapper.model_name
        self.provider = self._wrapper.provider
        self._verified_api_keys = True

    @property
    def ctx_length(self) -> int: return self._wrapper.ctx_length
    @property
    def model_name(self) -> str: return self._wrapper.model_name
    @property
    def max_tokens(self) -> int: return self._wrapper.max_tokens
    @property
    def _llm_type(self) -> str: return "browser_compatible_chat_wrapper"

    @staticmethod
    def _serialize_content(content: Any) -> Any:
        """Serialize browser_use ContentPart* Pydantic objects to dicts for langchain compatibility."""
        if isinstance(content, list):
            serialized = []
            for part in content:
                if hasattr(part, "model_dump"):
                    serialized.append(part.model_dump())
                elif hasattr(part, "dict"):
                    serialized.append(part.dict())
                elif isinstance(part, (str, dict)):
                    serialized.append(part)
                else:
                    serialized.append(str(part))
            return serialized
        return content

    async def ainvoke(self, messages: List[Any], output_format: Optional[type] = None, **kwargs: Any) -> Any:
        az_messages = []
        for m in messages:
            content = getattr(m, "content", getattr(m, "text", ""))
            content = self._serialize_content(content)
            role = getattr(m, "role", "user")
            if role == "system": az_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                msg = AIMessageChunk(content=content if isinstance(content, str) else str(content))
                tool_calls = getattr(m, "tool_calls", [])
                if tool_calls:
                    msg.tool_calls = [{"id": t.get("id"), "type": "function", "function": {"name": t.get("function", {}).get("name"), "arguments": t.get("function", {}).get("arguments")}} for t in tool_calls]
                az_messages.append(msg)
            else: az_messages.append(HumanMessage(content=content))

        if output_format: kwargs["response_format"] = output_format
        if "tools" in kwargs and hasattr(kwargs["tools"], "model_json_schema"):
            if not kwargs.get("response_format"): kwargs["response_format"] = kwargs["tools"]
            kwargs.pop("tools")

        if "response_format" in kwargs and hasattr(kwargs["response_format"], "model_json_schema"):
            p_cls = kwargs["response_format"]
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": p_cls.__name__, "schema": p_cls.model_json_schema()}}

        resp = await self._acall(az_messages, **kwargs)
        from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
        content, thinking, result_content = "", "", None
        try:
            choice = resp.choices[0]
            content = choice.message.content or ""
            if hasattr(choice.message, "reasoning_content"): thinking = choice.message.reasoning_content
            if content: content = browser_use_monkeypatch.clean_and_conform_browser_use_output(content)
            if output_format and content:
                import json
                clean_content = content.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_content)
                # Strip unknown fields if model forbids extras (e.g. AgentOutput)
                if hasattr(output_format, "model_fields"):
                    known_fields = set(output_format.model_fields.keys())
                    stripped = {k for k in data.keys() if k not in known_fields}
                    if stripped:
                        logger.warning(f"[BROWSER_PARSE] Stripped unknown fields: {stripped}")
                    data = {k: v for k, v in data.items() if k in known_fields}
                    
                    # Deep-clean action items: strip extra nested fields that cause
                    # "Extra inputs are not permitted" on action models like DoneActionModel
                    if "action" in data and isinstance(data["action"], list):
                        cleaned_actions = []
                        for act in data["action"]:
                            if isinstance(act, dict):
                                for act_key, act_val in act.items():
                                    if isinstance(act_val, dict):
                                        # For 'done'/'complete_task' action: handle both DoneAction 
                                        # and StructuredOutputAction[DoneResult] formats
                                        if act_key in ("done", "complete_task"):
                                            # If 'data' field exists, this is StructuredOutputAction format
                                            if "data" in act_val and isinstance(act_val["data"], dict):
                                                # Keep the data structure, just ensure success is present
                                                cleaned_done = {
                                                    "data": act_val["data"],
                                                    "success": act_val.get("success", True),
                                                }
                                            else:
                                                # LLM returned flat fields — construct proper structure
                                                # Try to build DoneResult-compatible data from available fields
                                                text = act_val.get("text", 
                                                       act_val.get("title",
                                                       act_val.get("response", "")))
                                                title = act_val.get("title", str(text)[:100])
                                                response = act_val.get("response", str(text))
                                                page_summary = act_val.get("page_summary", "")
                                                
                                                # Produce both formats: 'text' for DoneAction,
                                                # 'data' for StructuredOutputAction — the parser
                                                # uses model_validate which ignores unknown fields
                                                cleaned_done = {
                                                    "text": str(text),
                                                    "success": act_val.get("success", True),
                                                    "data": {
                                                        "title": str(title),
                                                        "response": str(response),
                                                        "page_summary": str(page_summary),
                                                    },
                                                }
                                            cleaned_actions.append({act_key: cleaned_done})
                                        else:
                                            cleaned_actions.append({act_key: act_val})
                                    else:
                                        cleaned_actions.append({act_key: act_val})
                                    break  # Only process first key-value pair
                            else:
                                cleaned_actions.append(act)
                        data["action"] = cleaned_actions
                
                try:
                    result_content = output_format(**data)
                except Exception as e1:
                    logger.warning(f"[BROWSER_PARSE] Direct construction failed: {e1}")
                    # Fallback: try model_validate which is more lenient
                    try:
                        result_content = output_format.model_validate(data)
                    except Exception as e2:
                        logger.warning(f"[BROWSER_PARSE] model_validate also failed: {e2}")
                        logger.warning(f"[BROWSER_PARSE] Data keys: {list(data.keys())}")
                        # result_content stays None → string fallback below handles it
        except Exception as e: logger.error(f"Failed to extract completion: {e}")

        usage_data = getattr(resp, "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        if isinstance(usage_data, str):
            usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        elif not isinstance(usage_data, dict): 
            try:
                usage_data = usage_data.dict() if hasattr(usage_data, "dict") else vars(usage_data)
            except TypeError:
                usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        return ChatInvokeCompletion(
            completion=result_content if result_content else content,
            thinking=thinking,
            usage=ChatInvokeUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
                prompt_cached_tokens=usage_data.get("prompt_cached_tokens", 0),
                prompt_cache_creation_tokens=usage_data.get("prompt_cache_creation_tokens", 0),
                prompt_image_tokens=usage_data.get("prompt_image_tokens", 0),
            )
        )

    async def _acall(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any):
        msgs_conv = self._wrapper._convert_messages(messages)
        apply_rate_limiter_sync(self._wrapper.agix_model_conf, str(msgs_conv))
        kwrgs = {**self._wrapper.kwargs, **kwargs}
        if "tools" in kwrgs and (hasattr(kwrgs["tools"], "model_json_schema") or hasattr(kwrgs["tools"], "schema")):
            if not kwrgs.get("response_format"): kwrgs["response_format"] = kwrgs["tools"]
            kwrgs.pop("tools")
        if "response_format" in kwrgs and (hasattr(kwrgs["response_format"], "model_json_schema") or hasattr(kwrgs["response_format"], "schema")):
            p_cls = kwrgs["response_format"]
            kwrgs["response_format"] = {"type": "json_schema", "json_schema": {"name": p_cls.__name__, "schema": p_cls.model_json_schema()}}
        if "tools" in kwrgs and kwrgs["tools"] is not None and not isinstance(kwrgs["tools"], list): kwrgs["tools"] = [kwrgs["tools"]]

        resp = await acompletion(model=self._wrapper.model_name, messages=msgs_conv, stop=stop, **kwrgs)
        try:
            if "response_format" in kwrgs and ("json_schema" in kwrgs["response_format"] or "json_object" in kwrgs["response_format"]):
                if resp.choices[0].message.content is not None and not resp.choices[0].message.content.startswith("{"):
                    import python.helpers.dirty_json as dirty_json
                    js = dirty_json.parse(resp.choices[0].message.content)
                    resp.choices[0].message.content = dirty_json.stringify(js)
        except (ValueError, KeyError, TypeError): pass
        return resp