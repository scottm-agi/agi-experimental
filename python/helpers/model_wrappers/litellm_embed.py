from __future__ import annotations
import asyncio
import logging
from typing import Any, List, Optional
from python.helpers.litellm_shim import embedding

from .base import Embeddings, ModelConfig
from .rate_limiting import apply_rate_limiter_sync, _notify_llm_failure

logger = logging.getLogger(__name__)

class LiteLLMEmbeddingWrapper(Embeddings):
    model_name: str
    provider: str
    kwargs: dict = {}
    agix_model_conf: Optional[ModelConfig] = None

    def __init__(
        self,
        model: str,
        provider: str,
        model_config: Optional[ModelConfig] = None,
        **kwargs: Any,
    ):
        self.provider = provider
        provider_prefix = f"{provider}/"
        if not model.startswith(provider_prefix):
            self.model_name = f"{provider}/{model}"
        else:
            self.model_name = model
        self.kwargs = kwargs
        self.agix_model_conf = model_config

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        apply_rate_limiter_sync(self.agix_model_conf, " ".join(texts))
        try:
            resp = embedding(
                model=self.model_name, 
                input=texts, 
                custom_llm_provider=self.provider,
                **self.kwargs
            )
        except Exception as e:
            silent_failover = self.kwargs.get("agix_silent_failover", False)
            if not silent_failover:
                _notify_llm_failure(self.agix_model_conf.provider if self.agix_model_conf else "unknown", self.model_name, e)
            raise e

        return [
            item.get("embedding") if isinstance(item, dict) else item.embedding  # type: ignore
            for item in resp.data  # type: ignore
        ]

    def embed_query(self, text: str) -> List[float]:
        apply_rate_limiter_sync(self.agix_model_conf, text)
        try:
            resp = embedding(model=self.model_name, input=[text], **self.kwargs)
        except Exception as e:
            logger.error(f"LiteLLMEmbeddingWrapper.embed_query failed for model {self.model_name}: {str(e)}")
            _notify_llm_failure(self.agix_model_conf.provider if self.agix_model_conf else "unknown", self.model_name, e)
            raise

        item = resp.data[0]  # type: ignore
        return item.get("embedding") if isinstance(item, dict) else item.embedding  # type: ignore

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Async version of embed_documents - runs sync version in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_documents, texts)

    async def aembed_query(self, text: str) -> List[float]:
        """Async version of embed_query - runs sync version in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_query, text)

class LocalSentenceTransformerWrapper(Embeddings):
    """Local wrapper for sentence-transformers models to avoid HuggingFace API calls"""

    def __init__(
        self,
        provider: str,
        model: str,
        model_config: Optional[ModelConfig] = None,
        **kwargs: Any,
    ):
        self.model_name = model
        self.agix_model_conf = model_config
        self._model = None # Lazy load

    @property
    def model(self):
        """Synchronous getter for the model (blocks loop if not loaded)."""
        if self._model is not None:
            return self._model
        
        from python.models import _ST_MODEL_CACHE, _ST_MODEL_LOCK, sentence_transformers_lib
        
        if self.model_name in _ST_MODEL_CACHE:
            self._model = _ST_MODEL_CACHE[self.model_name]
            return self._model

        with _ST_MODEL_LOCK:
            if self.model_name in _ST_MODEL_CACHE:
                self._model = _ST_MODEL_CACHE[self.model_name]
                return self._model

            logger.info(f"[EMBEDDINGS] Loading SentenceTransformer model: {self.model_name}...")
            import time
            start_t = time.perf_counter()
            
            try:
                model_kwargs = {"low_cpu_mem_usage": False}
                m = sentence_transformers_lib.SentenceTransformer(self.model_name, device="cpu", model_kwargs=model_kwargs)
                _ST_MODEL_CACHE[self.model_name] = m
                self._model = m
            except Exception as e:
                logger.warning(f"[EMBEDDINGS] Faster loading failed, falling back: {e}")
                m = sentence_transformers_lib.SentenceTransformer(self.model_name)
                m.to("cpu")
                _ST_MODEL_CACHE[self.model_name] = m
                self._model = m
            
            end_t = time.perf_counter()
            logger.info(f"[EMBEDDINGS] Model {self.model_name} loaded in {end_t - start_t:.2f}s")
            
        return self._model

    async def get_model(self):
        """Async getter for the model to ensure loading doesn't block the loop."""
        if self._model is not None:
            return self._model
        
        from python.models import _ST_MODEL_CACHE, _ST_MODEL_LOCK, sentence_transformers_lib
        
        # Double-check cache with threading lock first (fast)
        if self.model_name in _ST_MODEL_CACHE:
            self._model = _ST_MODEL_CACHE[self.model_name]
            return self._model

        # Only one async task should perform the thread offload
        # Note: we use self._async_lock which we should initialize in __init__
        if not hasattr(self, '_async_lock'):
            self._async_lock = asyncio.Lock()

        async with self._async_lock:
            if self.model_name in _ST_MODEL_CACHE:
                self._model = _ST_MODEL_CACHE[self.model_name]
                return self._model

            logger.info(f"[EMBEDDINGS] Offloading SentenceTransformer model load down to thread: {self.model_name}...")
            
            def _load_task():
                # We reuse the property logic inside the thread
                return self.model

            self._model = await asyncio.to_thread(_load_task)
            return self._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Sync embedding docs."""
        apply_rate_limiter_sync(self.agix_model_conf, " ".join(texts))
        embeddings = self.model.encode(texts, convert_to_tensor=False, show_progress_bar=False)
        return embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings

    def embed_query(self, text: str) -> List[float]:
        """Sync embedding query."""
        apply_rate_limiter_sync(self.agix_model_conf, text)
        try:
            embedding = self.model.encode([text], convert_to_tensor=False, show_progress_bar=False)
            result = (embedding[0].tolist() if hasattr(embedding[0], "tolist") else embedding[0])
            return result
        except Exception as e:
            logger.error(f"LocalSentenceTransformerWrapper.embed_query failed for model {self.model_name}: {str(e)}")
            raise

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Async embedding docs (offloads to thread)."""
        model = await self.get_model()
        apply_rate_limiter_sync(self.agix_model_conf, " ".join(texts))
        # Offload encoding as well since it's CPU intensive
        def _encode():
            return model.encode(texts, convert_to_tensor=False, show_progress_bar=False)
        
        embeddings = await asyncio.to_thread(_encode)
        return embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings

    async def aembed_query(self, text: str) -> List[float]:
        """Async embedding query (offloads to thread)."""
        model = await self.get_model()
        apply_rate_limiter_sync(self.agix_model_conf, text)
        try:
            def _encode():
                return model.encode([text], convert_to_tensor=False, show_progress_bar=False)
            
            embedding = await asyncio.to_thread(_encode)
            result = (embedding[0].tolist() if hasattr(embedding[0], "tolist") else embedding[0])
            return result
        except Exception as e:
            logger.error(f"LocalSentenceTransformerWrapper.aembed_query failed: {str(e)}")
            raise

