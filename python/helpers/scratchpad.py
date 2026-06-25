"""
GAP-2: Shared Scratchpad / Blackboard Pattern (Forgejo #1163)

Redis-backed shared scratchpad for cross-agent state coordination
within a single context/conversation scope. Designed for lightweight,
ephemeral coordination during fan-out parallel execution.

Key schema: scratchpad:{context_id}:{namespace}
TTL: 1 hour default (configurable per-write)

Complements SharedMemoryManager (long-lived semantic memories) with
a simpler, faster, ephemeral blackboard for in-task coordination.

Uses RedisClient wrapper methods (get/set/delete/exists/eval).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.scratchpad")

DEFAULT_TTL = 3600  # 1 hour


class Scratchpad:
    """Redis-backed shared scratchpad for cross-agent state within a context.

    Scoping rules:
    - Context-scoped: all data is scoped to a single context (conversation)
    - Namespace isolation: agents write to named namespaces (last-write-wins)
    - TTL: all entries auto-expire after DEFAULT_TTL (configurable)
    - Read-any: any agent in the chain can read any namespace
    - Write-tagged: writes are tagged with the writing agent's ID for auditability

    Usage:
        pad = Scratchpad.for_agent(agent)
        await pad.set("research_findings", {"competitors": [...]})
        data = await pad.get("research_findings")
    """

    def __init__(self, context_id: str, agent_id: str = "") -> None:
        self.context_id = context_id
        self.agent_id = agent_id

    @classmethod
    def for_agent(cls, agent: "Agent") -> "Scratchpad":
        """Create a Scratchpad scoped to an agent's context."""
        context_id = agent.context.id if agent.context else "unknown"
        return cls(context_id=context_id, agent_id=agent.agent_name)

    def _key(self, namespace: str) -> str:
        """Build the Redis key for a namespace."""
        return f"scratchpad:{self.context_id}:{namespace}"

    async def _get_redis(self) -> Any:
        """Get the Redis client, lazily imported."""
        from python.redis_client import RedisClient
        return RedisClient.get_instance()

    async def set(self, namespace: str, data: Any, ttl: int = DEFAULT_TTL) -> None:
        """Write data to the shared scratchpad.

        Args:
            namespace: Named key within the context (e.g., "research_findings")
            data: Any JSON-serializable data
            ttl: Time-to-live in seconds (default: 1 hour)
        """
        try:
            redis = await self._get_redis()
            payload = json.dumps({
                "data": data,
                "written_by": self.agent_id,
                "written_at": time.time(),
            }, default=str)
            await redis.set(self._key(namespace), payload, ex=ttl)
            logger.debug(f"Scratchpad.set: {namespace} by {self.agent_id}")
        except Exception as e:
            logger.warning(f"Scratchpad.set failed: {e}")

    async def get(self, namespace: str) -> Optional[Any]:
        """Read data from the shared scratchpad.

        Args:
            namespace: Named key to read

        Returns:
            The stored data, or None on miss/error
        """
        try:
            redis = await self._get_redis()
            raw = await redis.get(self._key(namespace))
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload.get("data")
        except Exception as e:
            logger.warning(f"Scratchpad.get failed: {e}")
            return None

    async def get_metadata(self, namespace: str) -> Optional[dict]:
        """Read the full metadata envelope (including provenance info).

        Args:
            namespace: Named key to read

        Returns:
            Dict with 'data', 'written_by', 'written_at', or None
        """
        try:
            redis = await self._get_redis()
            raw = await redis.get(self._key(namespace))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Scratchpad.get_metadata failed: {e}")
            return None

    async def exists(self, namespace: str) -> bool:
        """Check if a namespace has data.

        Args:
            namespace: Named key to check

        Returns:
            True if data exists, False otherwise
        """
        try:
            redis = await self._get_redis()
            return await redis.exists(self._key(namespace)) > 0
        except Exception:
            return False

    async def delete(self, namespace: str) -> None:
        """Delete a namespace.

        Args:
            namespace: Named key to delete
        """
        try:
            redis = await self._get_redis()
            await redis.delete(self._key(namespace))
            logger.debug(f"Scratchpad.delete: {namespace}")
        except Exception as e:
            logger.warning(f"Scratchpad.delete failed: {e}")

    async def clear_context(self) -> None:
        """Clear all scratchpad data for this context.
        
        Uses a Lua script to atomically scan and delete all matching keys.
        """
        try:
            redis = await self._get_redis()
            prefix = f"scratchpad:{self.context_id}:*"
            lua_script = """
            local keys = redis.call('keys', ARGV[1])
            local count = 0
            for _, key in ipairs(keys) do
                redis.call('del', key)
                count = count + 1
            end
            return count
            """
            keys_deleted = await redis.eval(lua_script, 0, prefix)
            logger.info(f"Scratchpad.clear_context: cleared {keys_deleted} entries for {self.context_id}")
        except Exception as e:
            logger.warning(f"Scratchpad.clear_context failed: {e}")
