from __future__ import annotations
"""
LLM Request Batching Utility (PERF-002)

Provides mechanisms for batching multiple LLM calls to improve throughput
and reduce total latency for utility operations.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import litellm

logger = logging.getLogger("agix.llm_batcher")

class LLMBatcher:
    """
    Utility for executing multiple LLM calls in parallel or batches.
    """
    
    def __init__(self, max_concurrent: int = 5):
        self._max_concurrent = max_concurrent
        self._semaphores: Dict[int, asyncio.Semaphore] = {}

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """Lazily create a semaphore for the current event loop."""
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        if loop_id not in self._semaphores:
            self._semaphores[loop_id] = asyncio.Semaphore(self._max_concurrent)
        return self._semaphores[loop_id]

    async def batch_complete(self, model: str, message_sets: List[List[Dict[str, str]]], **kwargs) -> List[Any]:
        """
        Execute multiple completion calls for the same model in parallel using manual parallelization.
        This approach is more robust across different litellm versions and provider combinations.
        """
        if not message_sets:
            return []
            
        logger.info(f"Batch executing {len(message_sets)} requests for model {model} (Manual Parallel)")
        return await self._parallel_execute(model, message_sets, **kwargs)

    async def _parallel_execute(self, model: str, message_sets: List[List[Dict[str, str]]], **kwargs) -> List[Any]:
        """
        Execute requests in parallel using a semaphore to control concurrency.
        """
        async def _single_call(msgs):
            async with self.semaphore:
                # Use agix_cache=False for batching to avoid cache-lock contention 
                # or redundancy within a single parallel burst if desired.
                # However, for utility calls, cache is usually beneficial.
                import python.models as models
                # Split model string into provider and name (e.g., "openai/gpt-4")
                provider = "openai"
                name = model
                if "/" in model:
                    provider, name = model.split("/", 1)
                
                m = models.get_chat_model(provider, name)
                resp, _, _, _ = await m.unified_call(messages=msgs, **kwargs)
                return resp
        
        tasks = [asyncio.ensure_future(_single_call(msgs)) for msgs in message_sets]
        # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
        LLM_BATCH_TIMEOUT = 300.0  # 5 minutes for LLM calls
        results: List[Any] = [None] * len(tasks)
        if tasks:
            done, pending = await asyncio.wait(
                tasks,
                timeout=LLM_BATCH_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                logger.warning(
                    f"[LLM_BATCHER] {len(pending)} LLM calls timed out after "
                    f"{LLM_BATCH_TIMEOUT}s — cancelling"
                )
                for p in pending:
                    p.cancel()
                await asyncio.wait(pending, timeout=5.0)
            for i, t in enumerate(tasks):
                if t in done:
                    try:
                        results[i] = t.result()
                    except Exception as e:
                        results[i] = e
                else:
                    results[i] = asyncio.TimeoutError(f"LLM batch call {i} timed out")
        return results

# Global batcher instance
_batcher: Optional[LLMBatcher] = None

def get_llm_batcher() -> LLMBatcher:
    global _batcher
    if _batcher is None:
        _batcher = LLMBatcher()
    return _batcher
