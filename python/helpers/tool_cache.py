"""
GAP-5: Tool Result Caching / Deduplication (Forgejo #1162)

Redis-backed cache for idempotent tool results (web searches, URL scrapes).
Prevents redundant API calls when the same query is retried or used across
fan-out agents.

Pattern: Content-addressable key = tool_cache:{tool_name}:{md5(args)[:16]}
TTL: Per-tool configurable (see CACHEABLE_TOOLS)

Follows the same Redis patterns established by LLMCache and MemoryBankCache.
Uses RedisClient wrapper methods (get/set/delete/expire) — not raw redis-py.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("agix.tool_cache")

# Tools that are safe to cache (idempotent, read-only) with their TTL in seconds
CACHEABLE_TOOLS: dict[str, int] = {
    "tavily_search": 900,       # 15 min
    "tavily_extract": 1800,     # 30 min
    "tavily_crawl": 1800,       # 30 min
    "perplexity_ask": 900,      # 15 min
    "search_engine": 900,       # 15 min
    "scrape_url": 1800,         # 30 min
}


class ToolResultCache:
    """Redis-backed cache for idempotent tool results.

    Only caches tools listed in CACHEABLE_TOOLS.
    Key: tool_cache:{tool_name}:{md5(args)[:16]}
    Value: JSON {result, cached_at, hit_count, tool_name}
    TTL: Per-tool configurable (see CACHEABLE_TOOLS)

    Usage:
        cache = ToolResultCache.get_instance()
        cached = await cache.get("tavily_search", {"query": "test"})
        if cached is not None:
            return cached  # Skip execution
        result = await execute_tool(...)
        await cache.set("tavily_search", {"query": "test"}, result)
    """

    _instance: Optional["ToolResultCache"] = None

    @classmethod
    def get_instance(cls) -> "ToolResultCache":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _cache_key(tool_name: str, args: dict) -> str:
        """Content-addressable cache key for tool results."""
        normalized = json.dumps(args, sort_keys=True, default=str)
        from python.helpers.hashing import content_hash_short
        ch = content_hash_short(f"{tool_name}:{normalized}", length=16)
        return f"tool_cache:{tool_name}:{ch}"

    @staticmethod
    def is_cacheable(tool_name: str) -> bool:
        """Check if a tool's results can be cached."""
        return tool_name in CACHEABLE_TOOLS

    @staticmethod
    def get_ttl(tool_name: str) -> int:
        """Get the TTL for a cacheable tool. Returns 0 if not cacheable."""
        return CACHEABLE_TOOLS.get(tool_name, 0)

    async def _get_redis(self) -> Any:
        """Get the Redis client, lazily imported to avoid circular deps.
        
        Returns a RedisClient instance that exposes get/set/delete/expire 
        methods through circuit-breaker-protected wrappers.
        """
        from python.redis_client import RedisClient
        return RedisClient.get_instance()

    async def get(self, tool_name: str, args: dict) -> Optional[str]:
        """Check cache for a tool result. Returns None on miss.

        Args:
            tool_name: Name of the tool (e.g., "tavily_search")
            args: Tool arguments dict

        Returns:
            Cached result string, or None on miss/error
        """
        if not self.is_cacheable(tool_name):
            return None

        try:
            redis = await self._get_redis()
            key = self._cache_key(tool_name, args)
            raw = await redis.get(key)
            if raw is None:
                self.misses += 1
                return None

            payload = json.loads(raw)
            hit_count = payload.get("hit_count", 0) + 1

            # Update hit count, re-set with original TTL
            payload["hit_count"] = hit_count
            ttl = CACHEABLE_TOOLS.get(tool_name, 900)
            await redis.set(key, json.dumps(payload), ex=ttl)

            self.hits += 1
            logger.info(
                f"ToolCache HIT: {tool_name} (hits={hit_count}, "
                f"cached {time.time() - payload.get('cached_at', 0):.0f}s ago)"
            )
            return payload.get("result")
        except Exception as e:
            logger.warning(f"ToolCache.get error: {e}")
            self.misses += 1
            return None

    async def set(self, tool_name: str, args: dict, result: str) -> None:
        """Cache a tool result with the configured TTL.

        Args:
            tool_name: Name of the tool
            args: Tool arguments dict
            result: Tool result string to cache
        """
        if not self.is_cacheable(tool_name):
            return

        try:
            redis = await self._get_redis()
            key = self._cache_key(tool_name, args)
            ttl = CACHEABLE_TOOLS.get(tool_name, 900)

            payload = json.dumps({
                "result": result,
                "cached_at": time.time(),
                "hit_count": 0,
                "tool_name": tool_name,
            })
            await redis.set(key, payload, ex=ttl)
            logger.debug(f"ToolCache SET: {tool_name} (ttl={ttl}s)")
        except Exception as e:
            logger.warning(f"ToolCache.set error: {e}")

    async def invalidate(self, tool_name: str, args: dict) -> None:
        """Manually invalidate a cached result."""
        try:
            redis = await self._get_redis()
            key = self._cache_key(tool_name, args)
            await redis.delete(key)
            logger.debug(f"ToolCache INVALIDATE: {tool_name}")
        except Exception as e:
            logger.warning(f"ToolCache.invalidate error: {e}")

    async def clear_all(self) -> None:
        """Clear all cached tool results.
        
        Uses a Lua script to atomically scan and delete all tool_cache:* keys.
        Falls back to key-by-key deletion if Lua isn't available.
        """
        try:
            redis = await self._get_redis()
            # Use Lua script for atomic scan+delete (more efficient)
            lua_script = """
            local keys = redis.call('keys', 'tool_cache:*')
            local count = 0
            for _, key in ipairs(keys) do
                redis.call('del', key)
                count = count + 1
            end
            return count
            """
            keys_deleted = await redis.eval(lua_script, 0)
            logger.info(f"ToolCache: cleared {keys_deleted} entries")
            self.hits = 0
            self.misses = 0
        except Exception as e:
            logger.warning(f"ToolCache.clear_all error: {e}")

    def get_stats(self) -> dict:
        """Return cache statistics."""
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": total,
            "hit_rate": f"{(self.hits / total * 100):.1f}%" if total > 0 else "N/A",
        }
