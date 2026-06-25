"""
Base types and core data structures for model wrappers.

Contains:
- ModelType enum
- ModelConfig dataclass
- ChatChunk TypedDict
- ChatGenerationResult class
- Message stub classes (BaseMessage, HumanMessage, SystemMessage, AIMessageChunk)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, TypedDict

# Optional langchain dependencies - fall back to minimal shims when unavailable
try:
    from langchain_core.messages import (
        BaseMessage,
        AIMessageChunk,
        HumanMessage,
        SystemMessage,
    )
except Exception:
    class BaseMessage:  # type: ignore[override]
        """Minimal stub used only for type annotations when langchain is missing."""
        content: str

        def __init__(self, content: str, *args: Any, **kwargs: Any) -> None:
            self.content = content

    class AIMessageChunk(BaseMessage):
        type: str = "ai"

    class HumanMessage(BaseMessage):
        type: str = "human"

    class SystemMessage(BaseMessage):
        type: str = "system"

class ChatGenerationChunk:
    """Stub for ChatGenerationChunk when langchain is missing."""
    message: BaseMessage
    def __init__(self, message: BaseMessage) -> None:
        self.message = message

class Embeddings:
    """Fallback Embeddings base class for environments without langchain."""
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError
    def embed_query(self, text: str) -> List[float]:
        raise NotImplementedError

class SimpleChatModel:
    """Stub for SimpleChatModel when langchain is missing."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            try:
                setattr(self, key, value)
            except AttributeError:
                # Skip properties or read-only attributes
                pass

class ModelType(Enum):
    """Model type enumeration."""
    CHAT = "Chat"
    EMBEDDING = "Embedding"


@dataclass
class ModelConfig:
    """Configuration for a model instance."""
    type: ModelType
    provider: str
    name: str
    api_base: str = ""
    ctx_length: int = 0
    limit_requests: int = 0
    limit_input: int = 0
    limit_output: int = 0
    max_tokens: int = 0
    vision: bool = False
    privacy: bool = False
    thinking: bool = False
    thinking_tokens: int = 0
    kwargs: dict = field(default_factory=dict)

    def build_kwargs(self) -> dict:
        """Build kwargs for API calls, applying provider-specific settings."""
        kwargs = self.kwargs.copy() or {}
        if self.api_base and "api_base" not in kwargs:
            kwargs["api_base"] = self.api_base

        # HARD ENFORCEMENT: Explicitly inject api_key for deterministic resolution.
        # LiteLLM auto-reads from os.environ, but env var names may not match
        # (e.g., API_KEY_OPENROUTER vs OPENROUTER_API_KEY) and sync_to_environ
        # may not have run yet. This ensures the key is always passed explicitly.
        if "api_key" not in kwargs:
            api_key = self._resolve_api_key()
            if api_key:
                kwargs["api_key"] = api_key

        # Inject OpenRouter specific headers (mandatory per Issue #275)
        if self.provider == "openrouter":
            eh = kwargs.get("extra_headers", {})
            if not isinstance(eh, dict):
                eh = {}
            eh["X-Title"] = "AGIX"
            eh["HTTP-Referer"] = "https://example.com"
            kwargs["extra_headers"] = eh
        
        # Inject output token limit if specified
        if self.max_tokens > 0 and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.max_tokens

        # Inject privacy flags if enabled (Issue #171)
        if self.privacy:
            eb = kwargs.get("extra_body", {})
            if not isinstance(eb, dict):
                eb = {}
            
            # LiteLLM generic: disable message logging in callbacks
            eb["no-log"] = True
            
            if self.provider == "openrouter":
                prov = eb.setdefault("provider", {})
                prov["zdr"] = True
                prov["data_collection"] = "deny"
            elif self.provider == "openai":
                eb["training_data_opt_out"] = True
            
            if eb:
                kwargs["extra_body"] = eb

            if self.provider == "anthropic":
                eh = kwargs.get("extra_headers", {})
                if not isinstance(eh, dict):
                    eh = {}
                eh["anthropic-beta"] = "no-training-data"
                kwargs["extra_headers"] = eh
        
        # Inject thinking/reasoning parameters if enabled (Issue #379)
        if self.thinking:
            if self.provider == "anthropic":
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.thinking_tokens if self.thinking_tokens > 0 else 1024
                }
            else:
                if "reasoning_effort" not in kwargs:
                    if self.thinking_tokens >= 4000:
                        kwargs["reasoning_effort"] = "high"
                    elif self.thinking_tokens >= 2000:
                        kwargs["reasoning_effort"] = "medium"
                    else:
                        kwargs["reasoning_effort"] = "low"
        
        return kwargs

    def _resolve_api_key(self) -> str:
        """Resolve API key for this model's provider from os.environ or SecretsManager.
        
        Checks multiple naming conventions to ensure deterministic resolution
        regardless of which env var format was used to set the key.
        Returns empty string if no key found (litellm will fall back to its own resolution).
        """
        import os
        provider = (self.provider or "").upper().strip()
        if not provider:
            return ""
        
        # Provider-specific aliases (litellm expects specific env var names)
        provider_env_map = {
            "OPENROUTER": ["OPENROUTER_API_KEY", "API_KEY_OPENROUTER", "OR_API_KEY"],
            "OPENAI": ["OPENAI_API_KEY", "API_KEY_OPENAI"],
            "ANTHROPIC": ["ANTHROPIC_API_KEY", "API_KEY_ANTHROPIC"],
            "GOOGLE": ["GOOGLE_API_KEY", "GEMINI_API_KEY", "API_KEY_GOOGLE"],
            "GROQ": ["GROQ_API_KEY", "API_KEY_GROQ"],
            "MISTRAL": ["MISTRAL_API_KEY", "API_KEY_MISTRAL"],
            "DEEPSEEK": ["DEEPSEEK_API_KEY", "API_KEY_DEEPSEEK"],
            "XAI": ["XAI_API_KEY", "API_KEY_XAI"],
            "VENICE": ["VENICE_API_KEY", "API_KEY_VENICE"],
            "TOGETHER": ["TOGETHER_API_KEY", "API_KEY_TOGETHER", "TOGETHERAI_API_KEY"],
            "FIREWORKS": ["FIREWORKS_API_KEY", "API_KEY_FIREWORKS", "FIREWORKS_AI_API_KEY"],
            "PERPLEXITY": ["PERPLEXITY_API_KEY", "PERPLEXITYAI_API_KEY", "API_KEY_PERPLEXITY"],
        }
        
        # 1. Check provider-specific env vars first
        candidates = provider_env_map.get(provider, [])
        # 2. Also generate generic patterns for unknown providers
        if not candidates:
            candidates = [f"{provider}_API_KEY", f"API_KEY_{provider}"]
        
        for env_name in candidates:
            val = os.environ.get(env_name, "")
            if val and not val.startswith("******") and val.lower() not in ("none", "", "na"):
                return val
        
        # 3. Fallback: try SecretsManager (which reads from config_db)
        try:
            from python.helpers.secrets_helper import get_default_secrets_manager
            sm = get_default_secrets_manager()
            for env_name in candidates:
                val = sm.get_secret(env_name)
                if val:
                    return val
        except Exception:
            pass
        
        return ""


class ChatChunk(TypedDict):
    """Simplified response chunk for chat models."""
    response_delta: str
    reasoning_delta: str
    finish_reason: Optional[str]


class ChatGenerationResult:
    """Chat generation result object that accumulates streamed chunks."""
    
    def __init__(self, chunk: Optional[ChatChunk] = None):
        self.reasoning = ""
        self.response = ""
        self.finish_reason = None
        self.thinking = False
        self.thinking_tag = ""
        self.unprocessed = ""
        self.native_reasoning = False
        self.thinking_pairs = [("<think>", "</think>"), ("<reasoning>", "</reasoning>")]
        if chunk:
            self.add_chunk(chunk)

    def add_chunk(self, chunk: ChatChunk) -> ChatChunk:
        """Process and add a chunk to the result."""
        if chunk.get("finish_reason"):
            self.finish_reason = chunk["finish_reason"]

        if chunk["reasoning_delta"]:
            self.native_reasoning = True

        # if native reasoning detection works, there's no need to worry about thinking tags
        if self.native_reasoning:
            processed_chunk = ChatChunk(
                response_delta=chunk["response_delta"], 
                reasoning_delta=chunk["reasoning_delta"],
                finish_reason=chunk.get("finish_reason")
            )
        else:
            # if the model outputs thinking tags, we need to parse them manually as reasoning
            processed_chunk = self._process_thinking_chunk(chunk)

        self.reasoning += processed_chunk["reasoning_delta"]
        self.response += processed_chunk["response_delta"]

        return processed_chunk

    def _process_thinking_chunk(self, chunk: ChatChunk) -> ChatChunk:
        """Process chunk for thinking tag parsing."""
        response_delta = self.unprocessed + chunk["response_delta"]
        self.unprocessed = ""
        return self._process_thinking_tags(response_delta, chunk["reasoning_delta"])

    def _process_thinking_tags(self, response: str, reasoning: str) -> ChatChunk:
        """Parse thinking tags from response and move to reasoning."""
        if self.thinking:
            close_pos = response.find(self.thinking_tag)
            if close_pos != -1:
                reasoning += response[:close_pos]
                response = response[close_pos + len(self.thinking_tag):]
                self.thinking = False
                self.thinking_tag = ""
            else:
                if self._is_partial_closing_tag(response):
                    self.unprocessed = response
                    response = ""
                else:
                    reasoning += response
                    response = ""
        else:
            for opening_tag, closing_tag in self.thinking_pairs:
                if response.startswith(opening_tag):
                    response = response[len(opening_tag):]
                    self.thinking = True
                    self.thinking_tag = closing_tag

                    close_pos = response.find(closing_tag)
                    if close_pos != -1:
                        reasoning += response[:close_pos]
                        response = response[close_pos + len(closing_tag):]
                        self.thinking = False
                        self.thinking_tag = ""
                    else:
                        if self._is_partial_closing_tag(response):
                            self.unprocessed = response
                            response = ""
                        else:
                            reasoning += response
                            response = ""
                    break
                elif len(response) < len(opening_tag) and self._is_partial_opening_tag(response, opening_tag):
                    self.unprocessed = response
                    response = ""
                    break

        return ChatChunk(response_delta=response, reasoning_delta=reasoning, finish_reason=None)

    def _is_partial_opening_tag(self, text: str, opening_tag: str) -> bool:
        """Check if text is a partial match for an opening tag."""
        for i in range(1, len(opening_tag)):
            if text == opening_tag[:i]:
                return True
        return False

    def _is_partial_closing_tag(self, text: str) -> bool:
        """Check if text is a partial match for a closing tag."""
        if not self.thinking_tag or not text:
            return False
        max_check = min(len(text), len(self.thinking_tag) - 1)
        for i in range(1, max_check + 1):
            if text.endswith(self.thinking_tag[:i]):
                return True
        return False

    def output(self) -> ChatChunk:
        """Get final output, including any unprocessed content."""
        response = self.response
        reasoning = self.reasoning
        if self.unprocessed:
            if reasoning and not response:
                reasoning += self.unprocessed
            else:
                response += self.unprocessed
        return ChatChunk(response_delta=response, reasoning_delta=reasoning, finish_reason=self.finish_reason)

def _parse_chunk(chunk: Any) -> ChatChunk:
    if chunk is None:
        return {"response_delta": "", "reasoning_delta": "", "finish_reason": None}
    
    if isinstance(chunk, dict) and "error" in chunk:
         error_data = chunk["error"]
         if isinstance(error_data, dict):
            error_msg = error_data.get("message") or error_data.get("metadata", {}).get("message") or str(error_data)
            error_code = error_data.get("code")
            e = Exception(f"API Error ({error_code}): {error_msg}")
            if error_code:
                setattr(e, "status_code", error_code)
            raise e
         else:
            raise Exception(f"API Error: {str(error_data)}")

    try:
        choices = chunk.get("choices", [])
        if not choices:
             return ChatChunk(reasoning_delta="", response_delta="", finish_reason=None)
             
        delta = choices[0].get("delta", {})
        message = choices[0].get("message", {}) or choices[0].get(
            "model_extra", {}
        ).get("message", {})
    except (KeyError, IndexError, AttributeError, TypeError):
        return ChatChunk(reasoning_delta="", response_delta="", finish_reason=None)

    response_delta = (
        delta.get("content", "")
        if isinstance(delta, dict)
        else getattr(delta, "content", "")
    ) or (
        message.get("content", "")
        if isinstance(message, dict)
        else getattr(message, "content", "")
    )
    reasoning_delta = (
        delta.get("reasoning_content", "")
        if isinstance(delta, dict)
        else getattr(delta, "reasoning_content", "")
    ) or (
        message.get("reasoning_content", "")
        if isinstance(message, dict)
        else getattr(message, "reasoning_content", "")
    )

    finish_reason = chunk["choices"][0].get("finish_reason") if choices else None

    return ChatChunk(reasoning_delta=reasoning_delta or "", response_delta=response_delta or "", finish_reason=finish_reason)

# Export message stubs for compatibility
__all__ = [
    "ModelType",
    "ModelConfig",
    "ChatChunk",
    "ChatGenerationResult",
    "BaseMessage",
    "HumanMessage",
    "SystemMessage",
    "AIMessageChunk",
    "ChatGenerationChunk",
    "Embeddings",
    "SimpleChatModel",
    "_parse_chunk"
]