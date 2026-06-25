from __future__ import annotations
"""
LLM Result Caching Utility (PERF-003)

Provides Redis-backed caching for LLM calls to reduce latency and cost
of repetitive utility tasks.
"""

from python.helpers.hashing import dedup_hash
import json
import logging
from typing import Any, Dict, List, Optional, Union
from datetime import timedelta

from python.redis_client import RedisClient

logger = logging.getLogger("agix.llm_cache")

class LLMCache:
    """
    Persistent cache for LLM request/response pairs using Redis.
    """
    
    def __init__(self, redis_client: Optional[RedisClient] = None, ttl_seconds: int = 3600):
        """
        Initialize LLM Cache.
        
        Args:
            redis_client: Redis client to use. If None, caching is disabled.
            ttl_seconds: Default time-to-live for cache entries (1 hour).
        """
        self.redis = redis_client or RedisClient.get_instance()
        self.ttl = ttl_seconds
        self.prefix = "llm_cache:"
        # Issue #836: Cache statistics tracking
        self.hits = 0
        self.misses = 0

    def _generate_key(self, model: str, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        Generate a unique stable key for a request.
        """
        # Sort kwargs to ensure stability
        sorted_kwargs = sorted(kwargs.items())
        
        # Serialize components
        data = {
            "model": model,
            "messages": messages,
            "params": sorted_kwargs
        }
        serialized = json.dumps(data, sort_keys=True)
        
        # Hash to fixed length
        h = dedup_hash(serialized)
        return f"{self.prefix}{h}"

    async def get(self, model: str, messages: List[Dict[str, str]], **kwargs) -> Optional[Dict[str, Any]]:
        """
        Retrieve a cached response if available.
        """
        if not self.redis or not self.redis.is_connected:
            self.misses += 1
            return None
            
        key = self._generate_key(model, messages, **kwargs)
        try:
            result = await self.redis.get_json(key)
            if result is not None:
                self.hits += 1
            else:
                self.misses += 1
            return result
        except Exception as e:
            logger.warning(f"Failed to retrieve from LLM cache: {e}")
            self.misses += 1
            return None

    async def set(self, model: str, messages: List[Dict[str, str]], response: Dict[str, Any], ttl: Optional[int] = None, **kwargs) -> bool:
        """
        Cache an LLM response.
        """
        if not self.redis or not self.redis.is_connected:
            return False
            
        key = self._generate_key(model, messages, **kwargs)
        try:
            return await self.redis.set_json(key, response, ex=ttl or self.ttl)
        except Exception as e:
            logger.warning(f"Failed to store in LLM cache: {e}")
            return False

    async def clear_model_cache(self, model: str):
        """
        Wipe all cache entries for a specific model (caution).
        """
        # This is expensive and requires scanning keys if not organized by hash tags.
        # For now, we'll implement it as a placeholder.
        pass

    def get_stats(self) -> Dict[str, Any]:
        """
        Return cache statistics.
        
        Returns:
            dict with hits, misses, total, hit_rate
        """
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": total,
            "hit_rate": round(hit_rate, 1),
            "enabled": True,  # Instance exists = enabled
        }

# Loop-local cache instances
_cache_instances: Dict[int, LLMCache] = {}

def get_llm_cache() -> LLMCache:
    """Get or create a loop-local LLMCache instance."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop_id = 0  # Default/Global ID for non-async contexts
        
    if loop_id not in _cache_instances:
        _cache_instances[loop_id] = LLMCache()
    return _cache_instances[loop_id]
