"""
MemoryBankCache — Redis write-through cache for memory bank files.

Sits between maintain_memory_bank tool / _20_memory_bank_context extension
and the filesystem.

Strategy:
- READ: Check Redis hash first → fall back to disk → populate cache on miss
- WRITE (overwrite): Write to disk + update Redis hash
- WRITE (append): Write to disk + invalidate Redis key (force re-read)
- TTL: Session-scoped (default 5 minutes), refreshed on access

Redis key structure:
  mb:{project_name} → Hash { file_name: content }

Graceful degradation: If Redis is unavailable, falls back to disk transparently.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Default TTL in seconds (5 minutes — refreshed on each access)
DEFAULT_TTL = 300

# Redis key prefix for memory bank caches
KEY_PREFIX = "mb"


class MemoryBankCache:
    """
    Write-through Redis cache for memory bank file reads.
    
    Uses Redis hashes: one hash per project, one field per file.
    Falls back to disk transparently if Redis is unavailable.
    """

    def __init__(self, redis_client=None, ttl: int = DEFAULT_TTL):
        """
        Args:
            redis_client: A RedisClient instance (or mock for testing).
                         If None, will attempt to get the singleton.
            ttl: Cache TTL in seconds (default 300 = 5 minutes).
        """
        self._redis = redis_client
        self._ttl = ttl

    def _cache_key(self, project_name: str) -> str:
        """Build the Redis hash key for a project's memory bank."""
        return f"{KEY_PREFIX}:{project_name}"

    async def _get_redis(self):
        """Lazily get Redis client, return None if unavailable."""
        if self._redis is not None:
            return self._redis
        try:
            from python.redis_client import RedisClient
            self._redis = RedisClient.get_instance()
            await self._redis.connect()
            return self._redis
        except Exception as e:
            logger.debug(f"Redis unavailable for memory bank cache: {e}")
            return None

    async def read(
        self,
        project_name: str,
        file_name: str,
        mb_dir: Optional[str] = None,
    ) -> Optional[str]:
        """
        Read a memory bank file, using Redis cache when available.
        
        Args:
            project_name: Project identifier for cache scoping.
            file_name: Memory bank file name (e.g., "progress.md").
            mb_dir: Path to the memory bank directory on disk.
        
        Returns:
            File contents as string, or None if file doesn't exist.
        """
        cache_key = self._cache_key(project_name)

        # 1. Try Redis cache first
        try:
            redis = await self._get_redis()
            if redis:
                cached = await redis.hget(cache_key, file_name)
                if cached is not None:
                    # Refresh TTL on access
                    try:
                        await redis.expire(cache_key, self._ttl)
                    except Exception:
                        pass
                    return cached
        except Exception as e:
            logger.debug(f"Redis cache read failed for {file_name}: {e}")

        # 2. Fall back to disk
        if not mb_dir:
            return None

        file_path = os.path.join(mb_dir, file_name)
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Disk read failed for {file_path}: {e}")
            return None

        # 3. Populate cache on miss
        try:
            redis = await self._get_redis()
            if redis:
                await redis.hset(cache_key, file_name, content)
                await redis.expire(cache_key, self._ttl)
        except Exception as e:
            logger.debug(f"Redis cache populate failed for {file_name}: {e}")

        return content

    async def invalidate_file(self, project_name: str, file_name: str) -> None:
        """
        Invalidate a single cached file after a disk write.
        
        Call this after any direct disk write (append/overwrite) so the
        next read() re-fetches from disk and populates fresh cache.
        """
        cache_key = self._cache_key(project_name)
        try:
            redis = await self._get_redis()
            if redis:
                await redis.hdel(cache_key, file_name)
        except Exception as e:
            logger.debug(f"Redis cache invalidation failed for {file_name}: {e}")

    async def invalidate(self, project_name: str) -> None:
        """
        Clear all cached files for a project.
        
        Call this when the memory bank directory changes externally.
        """
        cache_key = self._cache_key(project_name)
        try:
            redis = await self._get_redis()
            if redis:
                await redis.delete(cache_key)
        except Exception as e:
            logger.debug(f"Redis cache invalidation failed: {e}")


# Module-level singleton for shared use
_instance: Optional[MemoryBankCache] = None


def get_memory_bank_cache() -> MemoryBankCache:
    """Get or create the global MemoryBankCache singleton."""
    global _instance
    if _instance is None:
        _instance = MemoryBankCache()
    return _instance
