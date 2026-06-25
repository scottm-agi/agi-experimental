from __future__ import annotations
import time
from typing import Dict, Any, Optional
from python.redis_client import RedisClient

class DistributedRateLimiter:
    """
    Redis-backed distributed rate limiter for global coordination.
    """
    
    BACKOFF_KEY_PREFIX = "ratelimit:backoff:"
    USAGE_KEY_PREFIX = "ratelimit:usage:"
    
    def __init__(self, redis_client: Optional[RedisClient] = None):
        """
        Initialize the distributed rate limiter.
        
        Args:
            redis_client: Optional RedisClient instance. If not provided,
                         the loop-local singleton will be used.
        """
        self._redis = redis_client

    @property
    def redis(self) -> RedisClient:
        """Get the loop-local RedisClient instance."""
        if self._redis:
            return self._redis
        return RedisClient.get_instance()

    def _get_usage_key(self, provider: str, timeframe_key: str, metric: str) -> str:
        """Get the Redis key for usage tracking."""
        return f"{self.USAGE_KEY_PREFIX}{provider}:{timeframe_key}:{metric}"

    def _get_backoff_key(self, provider: str) -> str:
        """Get the Redis key for backoff signaling."""
        return f"{self.BACKOFF_KEY_PREFIX}{provider}"

    def _get_current_timeframe_key(self) -> str:
        """Get a string key for the current minute (or relevant timeframe)."""
        # Using minute-level buckets for simplicity
        return str(int(time.time() / 60))

    async def add_usage(self, provider: str, requests: int = 0, tokens: int = 0) -> Dict[str, int]:
        """
        Add usage to global buckets and return current totals.
        Uses a Lua script to perform multiple increments and expires in one atomic round-trip.
        
        Args:
            provider: Provider/key to track.
            requests: Number of requests to add.
            tokens: Number of tokens to add.
            
        Returns:
            Dict containing current "requests" and "tokens" totals.
        """
        tf_key = self._get_current_timeframe_key()
        req_key = self._get_usage_key(provider, tf_key, "requests")
        tok_key = self._get_usage_key(provider, tf_key, "tokens")
        
        # Collapse 4 Redis round-trips (incr, expire, incr, expire) into 1 atomic Lua script call
        script = """
        local req_inc = tonumber(ARGV[1])
        local tok_inc = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])
        local req_res = 0
        local tok_res = 0

        if req_inc > 0 then
            req_res = redis.call('INCRBY', KEYS[1], req_inc)
            redis.call('EXPIRE', KEYS[1], ttl)
        end
        if tok_inc > 0 then
            tok_res = redis.call('INCRBY', KEYS[2], tok_inc)
            redis.call('EXPIRE', KEYS[2], ttl)
        end
        return {req_res, tok_res}
        """
        
        try:
            # We use eval to execute the script. 
            # KEYS: [req_key, tok_key]
            # ARGV: [requests, tokens, ttl]
            results_list = await self.redis.eval(script, 2, req_key, tok_key, requests, tokens, 120)
            
            return {
                "requests": int(results_list[0]) if results_list and len(results_list) > 0 else 0,
                "tokens": int(results_list[1]) if results_list and len(results_list) > 1 else 0
            }
        except Exception as e:
            # Fallback to individual calls if Lua script fails (unlikely, but for safety)
            from python.redis_client import logger
            logger.error(f"DistributedRateLimiter Lua script failed, falling back: {e}")
            
            try:
                results = {"requests": 0, "tokens": 0}
                if requests > 0:
                    results["requests"] = await self.redis.incrby(req_key, requests)
                    await self.redis.expire(req_key, 120)
                if tokens > 0:
                    results["tokens"] = await self.redis.incrby(tok_key, tokens)
                    await self.redis.expire(tok_key, 120)
                return results
            except Exception as fallback_err:
                # Redis entirely unavailable — degrade gracefully to local-only
                logger.warning(f"DistributedRateLimiter fallback also failed (Redis unavailable): {fallback_err}")
                return {"requests": 0, "tokens": 0}

    async def get_usage(self, provider: str) -> Dict[str, int]:
        """
        Get current global usage for a provider in the current timeframe.
        """
        tf_key = self._get_current_timeframe_key()
        req_key = self._get_usage_key(provider, tf_key, "requests")
        tok_key = self._get_usage_key(provider, tf_key, "tokens")
        
        req_total = await self.redis.get(req_key)
        tok_total = await self.redis.get(tok_key)
        
        return {
            "requests": int(req_total) if req_total else 0,
            "tokens": int(tok_total) if tok_total else 0
        }

    async def set_backoff(self, provider: str, duration_ms: int):
        """
        Signal a global backoff for all agents.
        
        Args:
            provider: The provider hitting rate limits.
            duration_ms: Duration in milliseconds for the backoff.
        """
        key = self._get_backoff_key(provider)
        # Use PX for millisecond precision
        await self.redis.set(key, "1", px=duration_ms)

    async def is_backing_off(self, provider: str) -> bool:
        """
        Check if a global backoff is currently in effect.
        """
        key = self._get_backoff_key(provider)
        val = await self.redis.get(key)
        return val is not None
