"""
Redis History Helper for AGIX

Manages storage of large tool outputs in Redis to keep chat history concise and maintain context.
"""

import json
import logging
from typing import Any, Optional
from python.redis_client import RedisClient

logger = logging.getLogger(__name__)

class RedisHistoryHelper:
    """Helper to store and retrieve large tool outputs in Redis."""
    
    KEY_PREFIX = "chat:large_result"
    DEFAULT_TTL = 3600 * 24 * 7  # 7 days
    
    def __init__(self, redis_client: Optional[RedisClient] = None):
        self.redis_client = redis_client or RedisClient.get_instance()
        
    async def store_large_result(self, session_id: str, message_id: str, content: Any, ttl: int = DEFAULT_TTL) -> bool:
        """
        Store large content in Redis.
        
        Args:
            session_id: The chat session ID.
            message_id: The message ID.
            content: The content to store (will be JSON serialized).
            ttl: Time-to-live in seconds.
            
        Returns:
            True if stored successfully.
        """
        key = f"{self.KEY_PREFIX}:{session_id}:{message_id}"
        try:
            return await self.redis_client.set_json(key, content, ex=ttl)
        except Exception as e:
            logger.error(f"Failed to store large result in Redis (key: {key}): {e}")
            return False
            
    async def get_large_result(self, session_id: str, message_id: str) -> Optional[Any]:
        """
        Retrieve large content from Redis.
        
        Args:
            session_id: The chat session ID.
            message_id: The message ID.
            
        Returns:
            The deserialized content or None if not found.
        """
        key = f"{self.KEY_PREFIX}:{session_id}:{message_id}"
        try:
            return await self.redis_client.get_json(key)
        except Exception as e:
            logger.error(f"Failed to retrieve large result from Redis (key: {key}): {e}")
            return None
            
    async def delete_session_results(self, session_id: str) -> int:
        """
        Delete all large results for a session.
        Note: This is an expensive operation as it uses KEYS/SCAN.
        """
        # This is a placeholder for session cleanup if needed.
        # Redis TTL will handle individual message cleanup.
        return 0

_instance = None

def get_redis_history_helper() -> RedisHistoryHelper:
    """Get singleton instance of RedisHistoryHelper."""
    global _instance
    if _instance is None:
        _instance = RedisHistoryHelper()
    return _instance
