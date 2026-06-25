from __future__ import annotations
import logging
from typing import Any, List, Optional, Tuple, Union
from pydantic import ConfigDict

from .base import SimpleChatModel, BaseMessage, Embeddings
from .litellm_chat import LiteLLMChatWrapper

logger = logging.getLogger(__name__)

class FallbackChatWrapper(LiteLLMChatWrapper):
    """Chat model wrapper that falls back to next provider on failure (cascading)."""
    wrappers: List[LiteLLMChatWrapper] = []
    _verified_api_keys: bool = True

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow",
        validate_assignment=False,
    )

    def __init__(self, wrappers: List[LiteLLMChatWrapper], **kwargs: Any):
        first_wrapper = wrappers[0] if wrappers else None
        model_name = first_wrapper.model_name if first_wrapper else "fallback-unknown"
        provider = first_wrapper.provider if first_wrapper else "unknown"
        
        # Filter out model_name from kwargs to avoid "can't set attribute" error
        filtered_kwargs = {k: v for k, v in kwargs.items() if k != "model_name"}
        
        super().__init__(model=model_name, provider=provider, **filtered_kwargs)
        self.wrappers = wrappers
        self.model = model_name

    @property
    def ctx_length(self) -> int:
        return self.wrappers[0].ctx_length if self.wrappers else 128000

    @property
    def _llm_type(self) -> str:
        return "fallback-chat"

    @property
    def display_provider(self) -> str:
        return self.wrappers[0].display_provider if self.wrappers else "Unknown"

    @property
    def provider(self) -> str:
        return self.wrappers[0].provider if self.wrappers else "unknown"

    @property
    def name(self) -> str:
        return self.wrappers[0].model_name if self.wrappers else "unknown"

    @property
    def model_name(self) -> str:
        return self.name

    @property
    def display_model(self) -> str:
        return self.wrappers[0].display_model if self.wrappers else "Unknown"

    async def unified_call(
        self,
        system_message: str = "",
        user_message: str = "",
        messages: Optional[List[BaseMessage]] = None,
        tools: Optional[List[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[str, str, str, str]:
        last_error = None
        import time
        import asyncio
        start_time = time.time()
        
        for i, wrapper in enumerate(self.wrappers):
            try:
                actual_kwargs = kwargs.copy()
                if i > 0:
                    actual_kwargs["agix_silent_failover"] = True
                
                # Check for fallback delay
                if i > 0 and wrapper.agix_fallback_delay > 0:
                    elapsed = time.time() - start_time
                    remaining = wrapper.agix_fallback_delay - elapsed
                    if remaining > 0:
                        logger.info(f"Waiting {remaining:.2f}s before trying fallback model {wrapper.model_name} (delay: {wrapper.agix_fallback_delay}s)")
                        await asyncio.sleep(remaining)

                return await wrapper.unified_call(
                    system_message=system_message,
                    user_message=user_message,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    **actual_kwargs
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Model {wrapper.model_name} failed, trying next candidate in cascade: {str(e)}"
                )
        
        if last_error:
            raise last_error
        return "", "", "", ""

class FallbackEmbeddingWrapper(Embeddings):
    """Embedding wrapper that falls back to next provider on failure."""

    def __init__(self, wrappers: List[Embeddings]):
        self.wrappers = wrappers

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        last_error = None
        for wrapper in self.wrappers:
            try:
                return wrapper.embed_documents(texts)
            except Exception as e:
                last_error = e
                model_n = getattr(wrapper, "model_name", "unknown")
                logger.warning(f"Embedding documents failed with {model_n}, trying next: {str(e)}")
        if last_error:
            raise last_error
        return []

    def embed_query(self, text: str) -> List[float]:
        last_error = None
        for wrapper in self.wrappers:
            try:
                return wrapper.embed_query(text)
            except Exception as e:
                last_error = e
                model_n = getattr(wrapper, "model_name", "unknown")
                logger.warning(f"Embedding query failed with {model_n}, trying next: {str(e)}")
        if last_error:
            raise last_error
        return []

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Async version of embed_documents with fallback."""
        last_error = None
        for wrapper in self.wrappers:
            try:
                if hasattr(wrapper, "aembed_documents"):
                    return await wrapper.aembed_documents(texts)
                else:
                    return wrapper.embed_documents(texts)
            except Exception as e:
                last_error = e
                model_n = getattr(wrapper, "model_name", "unknown")
                logger.warning(f"Async embedding documents failed with {model_n}, trying next: {str(e)}")
        if last_error:
            raise last_error
        return []

    async def aembed_query(self, text: str) -> List[float]:
        """Async version of embed_query with fallback."""
        last_error = None
        for wrapper in self.wrappers:
            try:
                if hasattr(wrapper, "aembed_query"):
                    return await wrapper.aembed_query(text)
                else:
                    return wrapper.embed_query(text)
            except Exception as e:
                last_error = e
                model_n = getattr(wrapper, "model_name", "unknown")
                logger.warning(f"Async embedding query failed with {model_n}, trying next: {str(e)}")
        if last_error:
            raise last_error
        return []
