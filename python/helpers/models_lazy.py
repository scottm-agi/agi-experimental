from __future__ import annotations
import logging
import asyncio
from typing import Any, Callable

logger = logging.getLogger("agix.lazy_model")

class LazyModelWrapper:
    """
    A wrapper that defers the creation of a model until it's actually used.
    Used to speed up agent initialization by avoiding heavy model loading (like sentence-transformers)
    at creation time.
    """
    def __init__(self, loader: Callable[..., Any], *args, **kwargs):
        self._loader = loader
        self._args = args
        self._kwargs = kwargs
        self._model = None
        self._lock = asyncio.Lock()

    def _get_model(self):
        """Synchronous model access."""
        if self._model is None:
            # We use a bit of a hack here because this might be called from synchronous code
            # but we want it to be thread-safe if possible. 
            # In most cases, the first access will be from an 'await'able call.
            logger.info("Lazy loading model (sync access)...")
            self._model = self._loader(*self._args, **self._kwargs)
        return self._model

    async def _aget_model(self):
        """Asynchronous model access with locking."""
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    logger.info("Lazy loading model (async access)...")
                    # Check if loader is async or sync
                    if asyncio.iscoroutinefunction(self._loader):
                        self._model = await self._loader(*self._args, **self._kwargs)
                    else:
                        # Even if loader is sync, wrap it for consistent async behavior
                        self._model = self._loader(*self._args, **self._kwargs)
        return self._model

    def __getattr__(self, name: str) -> Any:
        # Avoid infinite recursion for private attributes or if model not loaded
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        
        target = self._get_model()
        return getattr(target, name)

    # Explicit implementations for common LangChain methods to ensure compatibility
    # and to use the async loader when possible.

    def invoke(self, *args, **kwargs):
        return self._get_model().invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        model = await self._aget_model()
        return await model.ainvoke(*args, **kwargs)

    def embed_query(self, *args, **kwargs):
        return self._get_model().embed_query(*args, **kwargs)

    async def aembed_query(self, *args, **kwargs):
        model = await self._aget_model()
        # Some embedding models might not have aembed_query, fallback to sync
        if hasattr(model, "aembed_query"):
            return await model.aembed_query(*args, **kwargs)
        return model.embed_query(*args, **kwargs)

    def embed_documents(self, *args, **kwargs):
        return self._get_model().embed_documents(*args, **kwargs)

    async def aembed_documents(self, *args, **kwargs):
        model = await self._aget_model()
        if hasattr(model, "aembed_documents"):
            return await model.aembed_documents(*args, **kwargs)
        return model.embed_documents(*args, **kwargs)

    @property
    def model_name(self):
        # We can often return the name from kwargs without loading the model
        if "model" in self._kwargs:
            return self._kwargs["model"]
        if len(self._args) > 1: # Usually (provider, name, ...)
             return self._args[1]
        return getattr(self._get_model(), "model_name", "unknown")

    @property
    def provider(self):
        if "provider" in self._kwargs:
            return self._kwargs["provider"]
        if len(self._args) > 0:
             return self._args[0]
        return getattr(self._get_model(), "provider", "unknown")
