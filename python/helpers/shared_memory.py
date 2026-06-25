from __future__ import annotations
"""
Shared Memory Manager for AGIX Parallel Swarm

This module provides a two-tier memory system with:
- Private memory: Agent-specific working memory
- Shared memory: Cross-agent collaboration memory
- Provenance tracking for audit trails
- Real-time synchronization via Redis Streams
- Perspective-based retrieval with boosting
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import numpy as np

from python.redis_client import RedisClient

logger = logging.getLogger(__name__)


class ConflictError(Exception):
    """Error raised when an optimistic locking conflict occurs."""
    pass


class MemoryTier(Enum):
    """Memory tier classification."""
    PRIVATE = "private"
    SHARED = "shared"


class MemoryType(Enum):
    """Type of memory content."""
    FACT = "fact"
    INSIGHT = "insight"
    DECISION = "decision"
    OBSERVATION = "observation"
    TASK_RESULT = "task_result"
    ERROR = "error"
    CONTEXT = "context"


@dataclass
class MemoryEntry:
    """Represents a memory entry in the system."""
    id: str
    content: str
    embedding: Optional[List[float]] = None
    tier: MemoryTier = MemoryTier.PRIVATE
    memory_type: MemoryType = MemoryType.FACT
    agent_id: str = ""
    task_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    access_count: int = 0
    version: int = 1
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert memory entry to dictionary."""
        return {
            "id": self.id,
            "content": self.content,
            "embedding": json.dumps(self.embedding) if self.embedding else "",
            "tier": self.tier.value,
            "memory_type": self.memory_type.value,
            "agent_id": self.agent_id,
            "task_id": self.task_id or "",
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": json.dumps(self.metadata),
            "provenance": json.dumps(self.provenance),
            "relevance_score": str(self.relevance_score),
            "access_count": str(self.access_count),
            "version": self.version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """Create memory entry from dictionary."""
        return cls(
            id=data["id"],
            content=data["content"],
            embedding=json.loads(data["embedding"]) if data.get("embedding") else None,
            tier=MemoryTier(data["tier"]),
            memory_type=MemoryType(data["memory_type"]),
            agent_id=data["agent_id"],
            task_id=data.get("task_id") or None,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            metadata=json.loads(data["metadata"]) if data.get("metadata") else {},
            provenance=json.loads(data["provenance"]) if data.get("provenance") else {},
            relevance_score=float(data.get("relevance_score", 0)),
            access_count=int(data.get("access_count", 0)),
            version=int(data.get("version", 1)),
        )


@dataclass
class Provenance:
    """Tracks the origin and history of a memory."""
    source_agent_id: str
    source_task_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    derivation_chain: List[str] = field(default_factory=list)
    confidence: float = 1.0
    verification_status: str = "unverified"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert provenance to dictionary."""
        return {
            "source_agent_id": self.source_agent_id,
            "source_task_id": self.source_task_id or "",
            "created_at": self.created_at.isoformat(),
            "derivation_chain": self.derivation_chain,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provenance":
        """Create provenance from dictionary."""
        return cls(
            source_agent_id=data["source_agent_id"],
            source_task_id=data.get("source_task_id") or None,
            created_at=datetime.fromisoformat(data["created_at"]),
            derivation_chain=data.get("derivation_chain", []),
            confidence=float(data.get("confidence", 1.0)),
            verification_status=data.get("verification_status", "unverified"),
        )


@dataclass
class SharedMemoryConfig:
    """Configuration for shared memory system."""
    # Redis keys
    private_prefix: str = "memory:private"
    shared_prefix: str = "memory:shared"
    broadcast_channel: str = "memory:broadcast"
    
    # Limits
    max_private_entries: int = 1000
    max_shared_entries: int = 10000
    
    # TTL (0 = no expiry)
    private_ttl: int = 0
    shared_ttl: int = 0
    
    # Features
    provenance_tracking: bool = True
    broadcast_enabled: bool = True
    
    # Retrieval
    default_top_k: int = 10
    min_similarity: float = 0.5
    perspective_boost: bool = True
    own_memory_boost: float = 1.2
    
    # Embedding
    embedding_dim: int = 384


class SharedMemoryManager:
    """
    Two-tier memory system for agent collaboration.
    
    Features:
    - Private memory: Agent-specific working memory
    - Shared memory: Cross-agent collaboration memory
    - Provenance tracking for audit trails
    - Real-time synchronization via Redis
    - Perspective-based retrieval with boosting
    """
    
    def __init__(
        self,
        redis_client: RedisClient,
        agent_id: str,
        config: Optional[SharedMemoryConfig] = None,
        embedding_model: Optional[Any] = None,
    ):
        """
        Initialize shared memory manager.
        
        Args:
            redis_client: Connected Redis client.
            agent_id: Unique identifier for this agent.
            config: Memory configuration.
            embedding_model: Model for generating embeddings.
        """
        self.redis = redis_client
        self.agent_id = agent_id
        self.config = config or SharedMemoryConfig()
        self.embedding_model = embedding_model
        self._broadcast_task: Optional[asyncio.Task] = None
        self._local_cache: Dict[str, MemoryEntry] = {}
    
    # ==========================================================================
    # Private Memory Operations
    # ==========================================================================
    
    async def store_private(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> MemoryEntry:
        """
        Store a memory in private tier.
        
        Args:
            content: Memory content.
            memory_type: Type of memory.
            task_id: Associated task ID.
            metadata: Additional metadata.
            embedding: Pre-computed embedding.
            
        Returns:
            The created MemoryEntry.
        """
        # Generate embedding if not provided
        if embedding is None and self.embedding_model:
            embedding = await self._generate_embedding(content)
        
        # Create provenance
        provenance = Provenance(
            source_agent_id=self.agent_id,
            source_task_id=task_id,
        )
        
        # Create memory entry
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            embedding=embedding,
            tier=MemoryTier.PRIVATE,
            memory_type=memory_type,
            agent_id=self.agent_id,
            task_id=task_id,
            metadata=metadata or {},
            provenance=provenance.to_dict() if self.config.provenance_tracking else {},
        )
        
        # Store in Redis
        key = f"{self.config.private_prefix}:{self.agent_id}:{entry.id}"
        await self.redis.set_json(key, entry.to_dict(), ex=self.config.private_ttl or None)
        
        # Add to index
        await self._add_to_index(entry, MemoryTier.PRIVATE)
        
        # Update local cache
        self._local_cache[entry.id] = entry
        
        logger.debug(f"Stored private memory {entry.id} for agent {self.agent_id}")
        return entry
    
    async def get_private(self, memory_id: str) -> Optional[MemoryEntry]:
        """
        Get a private memory by ID.
        
        Args:
            memory_id: Memory ID.
            
        Returns:
            MemoryEntry if found, None otherwise.
        """
        # Check local cache first
        if memory_id in self._local_cache:
            entry = self._local_cache[memory_id]
            if entry.tier == MemoryTier.PRIVATE and entry.agent_id == self.agent_id:
                return entry
        
        # Fetch from Redis
        key = f"{self.config.private_prefix}:{self.agent_id}:{memory_id}"
        data = await self.redis.get_json(key)
        
        if data:
            entry = MemoryEntry.from_dict(data)
            self._local_cache[memory_id] = entry
            return entry
        
        return None
    
    async def delete_private(self, memory_id: str) -> bool:
        """
        Delete a private memory.
        
        Args:
            memory_id: Memory ID to delete.
            
        Returns:
            True if deleted, False if not found.
        """
        key = f"{self.config.private_prefix}:{self.agent_id}:{memory_id}"
        result = await self.redis.delete(key)
        
        if memory_id in self._local_cache:
            del self._local_cache[memory_id]
        
        await self._remove_from_index(memory_id, MemoryTier.PRIVATE)
        
        return result > 0
    
    # ==========================================================================
    # Shared Memory Operations
    # ==========================================================================
    
    async def store_shared(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
        broadcast: bool = True,
    ) -> MemoryEntry:
        """
        Store a memory in shared tier.
        
        Args:
            content: Memory content.
            memory_type: Type of memory.
            task_id: Associated task ID.
            metadata: Additional metadata.
            embedding: Pre-computed embedding.
            broadcast: Whether to broadcast to other agents.
            
        Returns:
            The created MemoryEntry.
        """
        # Generate embedding if not provided
        if embedding is None and self.embedding_model:
            embedding = await self._generate_embedding(content)
        
        # Create provenance
        provenance = Provenance(
            source_agent_id=self.agent_id,
            source_task_id=task_id,
        )
        
        # Create memory entry
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            embedding=embedding,
            tier=MemoryTier.SHARED,
            memory_type=memory_type,
            agent_id=self.agent_id,
            task_id=task_id,
            metadata=metadata or {},
            provenance=provenance.to_dict() if self.config.provenance_tracking else {},
        )
        
        # Store in Redis
        key = f"{self.config.shared_prefix}:{entry.id}"
        await self.redis.set_json(key, entry.to_dict(), ex=self.config.shared_ttl or None)
        
        # Add to index
        await self._add_to_index(entry, MemoryTier.SHARED)
        
        # Update local cache
        self._local_cache[entry.id] = entry
        
        # Broadcast to other agents
        if broadcast and self.config.broadcast_enabled:
            await self._broadcast_memory(entry)
        
        logger.info(f"Stored shared memory {entry.id} from python.agent {self.agent_id}")
        return entry

    async def update_shared(
        self,
        memory_id: str,
        content: str,
        expected_version: int,
        metadata: Optional[Dict[str, Any]] = None,
        broadcast: bool = True,
    ) -> MemoryEntry:
        """
        Update a shared memory entry using optimistic locking.
        
        Args:
            memory_id: ID of the memory to update.
            content: New content.
            expected_version: The version the agent expects the entry to have.
            metadata: Updated metadata (merged if provided).
            broadcast: Whether to broadcast the update.
            
        Returns:
            Updated MemoryEntry.
            
        Raises:
            ConflictError: If the version in Redis doesn't match expected_version.
            ValueError: If memory_id not found.
        """
        # Fetch current entry
        entry = await self.get_shared(memory_id)
        if not entry:
            raise ValueError(f"Shared memory {memory_id} not found")
        
        # Check version
        if entry.version != expected_version:
            raise ConflictError(
                f"Conflict updating shared memory {memory_id}: "
                f"expected version {expected_version}, but found {entry.version}"
            )
        
        # Prepare new data
        entry.content = content
        entry.version += 1
        entry.updated_at = datetime.now()
        if metadata:
            entry.metadata.update(metadata)
        
        new_data = json.dumps(entry.to_dict())
        key = f"{self.config.shared_prefix}:{entry.id}"
        
        # Lua script for atomic version check and update
        # Returns: 1 on success, 0 on conflict, -1 on not found
        script = """
        local key = KEYS[1]
        local expected_version = tonumber(ARGV[1])
        local new_data = ARGV[2]
        
        local current_raw = redis.call("GET", key)
        if not current_raw then return -1 end
        
        local current = fjson.decode(current_raw)
        if tonumber(current.version) ~= expected_version then
            return 0
        end
        
        redis.call("SET", key, new_data)
        return 1
        """
        # Wait, fjson or cjson? Most Redis environments use cjson.
        # But wait, my entries are stored with dicts, cjson.decode works.
        script = script.replace("fjson", "cjson")
        
        result = await self.redis.eval(script, 1, key, expected_version, new_data)
        
        if result == 0:
            raise ConflictError(
                f"Atomic conflict updating shared memory {memory_id}: "
                f"expected version {expected_version}"
            )
        elif result == -1:
            raise ValueError(f"Shared memory {memory_id} not found in Redis during atomic update")
        
        # Update local cache and broadcast
        self._local_cache[entry.id] = entry
        if broadcast and self.config.broadcast_enabled:
            await self._broadcast_memory(entry)
            
        logger.info(f"Updated shared memory {entry.id} to version {entry.version} (atomically)")
        return entry
    
    async def get_shared(self, memory_id: str) -> Optional[MemoryEntry]:
        """
        Get a shared memory by ID.
        
        Args:
            memory_id: Memory ID.
            
        Returns:
            MemoryEntry if found, None otherwise.
        """
        # Check local cache first
        if memory_id in self._local_cache:
            entry = self._local_cache[memory_id]
            if entry.tier == MemoryTier.SHARED:
                return entry
        
        # Fetch from Redis
        key = f"{self.config.shared_prefix}:{memory_id}"
        data = await self.redis.get_json(key)
        
        if data:
            entry = MemoryEntry.from_dict(data)
            self._local_cache[memory_id] = entry
            return entry
        
        return None
    
    async def delete_shared(self, memory_id: str) -> bool:
        """
        Delete a shared memory.
        
        Args:
            memory_id: Memory ID to delete.
            
        Returns:
            True if deleted, False if not found.
        """
        key = f"{self.config.shared_prefix}:{memory_id}"
        result = await self.redis.delete(key)
        
        if memory_id in self._local_cache:
            del self._local_cache[memory_id]
        
        await self._remove_from_index(memory_id, MemoryTier.SHARED)
        
        return result > 0
    
    # ==========================================================================
    # Retrieval Operations
    # ==========================================================================
    
    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        tier: Optional[MemoryTier] = None,
        memory_types: Optional[List[MemoryType]] = None,
        min_similarity: Optional[float] = None,
        include_provenance: bool = True,
    ) -> List[MemoryEntry]:
        """
        Retrieve memories based on semantic similarity.
        
        Args:
            query: Query string.
            top_k: Maximum results to return.
            tier: Filter by tier (None = both).
            memory_types: Filter by memory types.
            min_similarity: Minimum similarity threshold.
            include_provenance: Include provenance in results.
            
        Returns:
            List of matching MemoryEntry objects.
        """
        top_k = top_k or self.config.default_top_k
        min_similarity = min_similarity or self.config.min_similarity
        
        # Generate query embedding
        query_embedding = None
        if self.embedding_model:
            query_embedding = await self._generate_embedding(query)
        
        # Get candidate memories
        candidates = await self._get_candidates(tier, memory_types)
        
        # Score and rank
        scored_results = []
        for entry in candidates:
            score = await self._compute_similarity(query_embedding, entry, query)
            
            # Apply perspective boost
            if self.config.perspective_boost and entry.agent_id == self.agent_id:
                score *= self.config.own_memory_boost
            
            if score >= min_similarity:
                entry.relevance_score = score
                scored_results.append(entry)
        
        # Sort by score and return top_k
        scored_results.sort(key=lambda x: x.relevance_score, reverse=True)
        results = scored_results[:top_k]
        
        # Update access counts
        for entry in results:
            entry.access_count += 1
        
        logger.debug(f"Retrieved {len(results)} memories for query: {query[:50]}...")
        return results
    
    async def retrieve_all(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> Dict[str, List[MemoryEntry]]:
        """
        Retrieve from both private and shared tiers.
        
        Args:
            query: Query string.
            top_k: Maximum results per tier.
            min_similarity: Minimum similarity threshold.
            
        Returns:
            Dict with 'private' and 'shared' memory lists.
        """
        private_results = await self.retrieve(
            query=query,
            top_k=top_k,
            tier=MemoryTier.PRIVATE,
            min_similarity=min_similarity,
        )
        
        shared_results = await self.retrieve(
            query=query,
            top_k=top_k,
            tier=MemoryTier.SHARED,
            min_similarity=min_similarity,
        )
        
        return {
            "private": private_results,
            "shared": shared_results,
        }
    
    async def retrieve_by_task(self, task_id: str) -> List[MemoryEntry]:
        """
        Retrieve all memories associated with a task.
        
        Args:
            task_id: Task ID to filter by.
            
        Returns:
            List of MemoryEntry objects.
        """
        results = []
        
        # Search in local cache first
        for entry in self._local_cache.values():
            if entry.task_id == task_id:
                results.append(entry)
        
        # Search in Redis indexes
        # This is a simplified implementation - production would use proper indexing
        private_index = await self.redis.smembers(f"index:private:{self.agent_id}")
        for memory_id in private_index:
            if memory_id not in self._local_cache:
                entry = await self.get_private(memory_id)
                if entry and entry.task_id == task_id:
                    results.append(entry)
        
        shared_index = await self.redis.smembers("index:shared")
        for memory_id in shared_index:
            if memory_id not in self._local_cache:
                entry = await self.get_shared(memory_id)
                if entry and entry.task_id == task_id:
                    results.append(entry)
        
        return results
    
    # ==========================================================================
    # Synchronization
    # ==========================================================================
    
    async def start_broadcast_listener(self) -> None:
        """Start listening for memory broadcasts from other agents."""
        if self._broadcast_task and not self._broadcast_task.done():
            return
        
        self._broadcast_task = asyncio.create_task(self._broadcast_listener())
        logger.info(f"Started broadcast listener for agent {self.agent_id}")
    
    async def stop_broadcast_listener(self) -> None:
        """Stop the broadcast listener."""
        if self._broadcast_task and not self._broadcast_task.done():
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                logger.debug(f"[SharedMemory] Broadcast task cancelled during stop (agent={self.agent_id})")
        logger.info(f"Stopped broadcast listener for agent {self.agent_id}")
    
    async def _broadcast_memory(self, entry: MemoryEntry) -> None:
        """
        Broadcast a memory to other agents.
        
        Args:
            entry: Memory entry to broadcast.
        """
        message = json.dumps({
            "type": "memory_update",
            "memory_id": entry.id,
            "agent_id": self.agent_id,
            "tier": entry.tier.value,
            "timestamp": datetime.now().isoformat(),
        })
        
        await self.redis.publish(self.config.broadcast_channel, message)
        logger.debug(f"Broadcast memory {entry.id}")
    
    async def _broadcast_listener(self) -> None:
        """Background task for listening to memory broadcasts."""
        try:
            pubsub = await self.redis.subscribe(self.config.broadcast_channel)
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        
                        # Skip our own broadcasts
                        if data.get("agent_id") == self.agent_id:
                            continue
                        
                        # Handle memory update
                        if data.get("type") == "memory_update":
                            memory_id = data.get("memory_id")
                            if memory_id:
                                # Fetch and cache the new memory
                                entry = await self.get_shared(memory_id)
                                if entry:
                                    logger.info(f"COLLABORATION: Received broadcast memory {memory_id} from python.agent {data.get('agent_id')}")
                    except Exception as e:
                        logger.error(f"Error processing broadcast: {e}")
        except asyncio.CancelledError:
            logger.debug("[SharedMemory] Broadcast listener cancelled — shutting down gracefully")
    
    # ==========================================================================
    # Helper Methods
    # ==========================================================================
    
    async def _generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for text.
        
        Args:
            text: Text to embed.
            
        Returns:
            Embedding vector.
        """
        if self.embedding_model is None:
            # Return zero vector if no model
            return [0.0] * self.config.embedding_dim
        
        try:
            # This assumes the embedding model has an encode method
            # Adjust based on actual model interface
            embedding = self.embedding_model.encode(text)
            return embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return [0.0] * self.config.embedding_dim
    
    async def _compute_similarity(
        self,
        query_embedding: Optional[List[float]],
        entry: MemoryEntry,
        query_text: str,
    ) -> float:
        """
        Compute similarity between query and memory entry.
        
        Args:
            query_embedding: Query embedding vector.
            entry: Memory entry to compare.
            query_text: Original query text.
            
        Returns:
            Similarity score (0.0 - 1.0).
        """
        if query_embedding and entry.embedding:
            # Cosine similarity
            query_vec = np.array(query_embedding)
            entry_vec = np.array(entry.embedding)
            
            dot_product = np.dot(query_vec, entry_vec)
            norm_product = np.linalg.norm(query_vec) * np.linalg.norm(entry_vec)
            
            if norm_product > 0:
                return float(dot_product / norm_product)
        
        # Fallback to simple text matching
        query_words = set(query_text.lower().split())
        content_words = set(entry.content.lower().split())
        
        if not query_words or not content_words:
            return 0.0
        
        intersection = query_words & content_words
        union = query_words | content_words
        
        return len(intersection) / len(union) if union else 0.0
    
    async def _get_candidates(
        self,
        tier: Optional[MemoryTier],
        memory_types: Optional[List[MemoryType]],
    ) -> List[MemoryEntry]:
        """
        Get candidate memories for retrieval.
        
        Args:
            tier: Filter by tier.
            memory_types: Filter by memory types.
            
        Returns:
            List of candidate MemoryEntry objects.
        """
        candidates = []
        
        # Get from private tier
        if tier is None or tier == MemoryTier.PRIVATE:
            private_index = await self.redis.smembers(f"index:private:{self.agent_id}")
            for memory_id in private_index:
                entry = await self.get_private(memory_id)
                if entry:
                    if memory_types is None or entry.memory_type in memory_types:
                        candidates.append(entry)
        
        # Get from shared tier
        if tier is None or tier == MemoryTier.SHARED:
            shared_index = await self.redis.smembers("index:shared")
            for memory_id in shared_index:
                entry = await self.get_shared(memory_id)
                if entry:
                    if memory_types is None or entry.memory_type in memory_types:
                        candidates.append(entry)
        
        return candidates
    
    async def _add_to_index(self, entry: MemoryEntry, tier: MemoryTier) -> None:
        """Add memory to appropriate index."""
        if tier == MemoryTier.PRIVATE:
            await self.redis.sadd(f"index:private:{self.agent_id}", entry.id)
        else:
            await self.redis.sadd("index:shared", entry.id)
    
    async def _remove_from_index(self, memory_id: str, tier: MemoryTier) -> None:
        """Remove memory from appropriate index."""
        if tier == MemoryTier.PRIVATE:
            await self.redis.srem(f"index:private:{self.agent_id}", memory_id)
        else:
            await self.redis.srem("index:shared", memory_id)
    
    # ==========================================================================
    # Statistics and Management
    # ==========================================================================
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get memory statistics.
        
        Returns:
            Dictionary with memory statistics.
        """
        private_count = await self.redis.scard(f"index:private:{self.agent_id}") if hasattr(self.redis, 'scard') else 0
        shared_count = await self.redis.scard("index:shared") if hasattr(self.redis, 'scard') else 0
        
        return {
            "agent_id": self.agent_id,
            "private_count": private_count,
            "shared_count": shared_count,
            "local_cache_size": len(self._local_cache),
            "config": {
                "max_private": self.config.max_private_entries,
                "max_shared": self.config.max_shared_entries,
                "provenance_tracking": self.config.provenance_tracking,
                "broadcast_enabled": self.config.broadcast_enabled,
            },
        }
    
    async def clear_private(self) -> int:
        """
        Clear all private memories for this agent.
        
        Returns:
            Number of memories cleared.
        """
        index_key = f"index:private:{self.agent_id}"
        memory_ids = await self.redis.smembers(index_key)
        
        count = 0
        for memory_id in memory_ids:
            if await self.delete_private(memory_id):
                count += 1
        
        await self.redis.delete(index_key)
        
        logger.warning(f"Cleared {count} private memories for agent {self.agent_id}")
        return count
    
    def clear_local_cache(self) -> int:
        """
        Clear the local memory cache.
        
        Returns:
            Number of entries cleared.
        """
        count = len(self._local_cache)
        self._local_cache.clear()
        return count


# =============================================================================
# Factory Function
# =============================================================================

async def create_shared_memory_manager(
    redis_client: RedisClient,
    agent_id: str,
    config: Optional[Dict] = None,
    embedding_model: Optional[Any] = None,
) -> SharedMemoryManager:
    """
    Factory function to create a shared memory manager.
    
    Args:
        redis_client: Connected Redis client.
        agent_id: Unique agent identifier.
        config: Configuration dictionary.
        embedding_model: Model for generating embeddings.
        
    Returns:
        Initialized SharedMemoryManager instance.
    """
    memory_config = SharedMemoryConfig()
    
    if config:
        memory_config.max_private_entries = config.get("private", {}).get("max_entries", memory_config.max_private_entries)
        memory_config.max_shared_entries = config.get("shared", {}).get("max_entries", memory_config.max_shared_entries)
        memory_config.private_ttl = config.get("private", {}).get("ttl", memory_config.private_ttl)
        memory_config.shared_ttl = config.get("shared", {}).get("ttl", memory_config.shared_ttl)
        memory_config.provenance_tracking = config.get("shared", {}).get("provenance_tracking", memory_config.provenance_tracking)
        memory_config.broadcast_enabled = config.get("shared", {}).get("broadcast_enabled", memory_config.broadcast_enabled)
        
        retrieval_config = config.get("retrieval", {})
        memory_config.default_top_k = retrieval_config.get("top_k", memory_config.default_top_k)
        memory_config.min_similarity = retrieval_config.get("min_similarity", memory_config.min_similarity)
        memory_config.perspective_boost = retrieval_config.get("perspective_boost", memory_config.perspective_boost)
        memory_config.own_memory_boost = retrieval_config.get("own_memory_boost", memory_config.own_memory_boost)
    
    manager = SharedMemoryManager(redis_client, agent_id, memory_config, embedding_model)
    
    # Start broadcast listener if enabled
    if memory_config.broadcast_enabled:
        await manager.start_broadcast_listener()
    
    return manager

# Backward-compat alias: tests import SharedMemory
SharedMemory = SharedMemoryManager
