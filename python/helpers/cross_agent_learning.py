from __future__ import annotations
"""
Cross-Agent Learning System for AGIX

This module provides knowledge sharing and learning propagation between agents:
- Knowledge storage and retrieval
- Validation and trust system
- Real-time broadcast propagation
- Periodic consolidation
- Knowledge distillation

Based on design specification in research/future-enhancements-design.md Section 4.
"""

import asyncio
import json
import logging
import uuid
import time
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================

class KnowledgeType(str, Enum):
    """Types of knowledge that can be learned and shared."""
    PATTERN = "pattern"      # Successful patterns (what worked)
    FACT = "fact"            # Domain knowledge (facts learned)
    PROCEDURE = "procedure"  # Procedural knowledge (how to do things)
    FAILURE = "failure"      # Failure patterns (what to avoid)


class ValidationLevel(str, Enum):
    """Validation levels for knowledge trust."""
    SELF_VALIDATED = "self_validated"        # Source agent verified
    PEER_VALIDATED = "peer_validated"        # Another agent confirmed
    MULTI_VALIDATED = "multi_validated"      # 3+ agents confirmed
    PRODUCTION_PROVEN = "production_proven"  # Used successfully 10+ times


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class LearningConfig:
    """Configuration for the cross-agent learning system."""
    # Feature flags
    enabled: bool = True
    broadcast_enabled: bool = True
    
    # Timing
    consolidation_interval: int = 3600  # seconds (1 hour)
    
    # Confidence thresholds
    min_confidence_threshold: float = 0.3
    initial_confidence: float = 0.5
    
    # Validation boosts
    validation_boost_self: float = 0.1
    validation_boost_peer: float = 0.2
    validation_boost_multi: float = 0.3
    validation_boost_production: float = 0.4
    
    # Validation penalties
    validation_penalty: float = 0.15
    usage_success_boost: float = 0.05
    usage_failure_penalty: float = 0.1
    
    # Production proven threshold
    production_proven_usage_count: int = 10
    
    # Limits
    max_knowledge_entries: int = 10000
    knowledge_ttl: int = 0  # 0 = no expiry
    
    # Redis keys
    knowledge_prefix: str = "learning:knowledge"
    broadcast_channel: str = "learning:broadcast"
    index_prefix: str = "learning:index"


@dataclass
class LearnedKnowledge:
    """Represents a unit of learned knowledge."""
    knowledge_id: str
    knowledge_type: KnowledgeType
    content: str
    source_agent: str
    source_task: Optional[str] = None
    confidence: float = 0.5
    validation_count: int = 0
    usage_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    validators: Set[str] = field(default_factory=set)
    usage_history: List[Dict] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "knowledge_id": self.knowledge_id,
            "knowledge_type": self.knowledge_type.value if isinstance(self.knowledge_type, KnowledgeType) else self.knowledge_type,
            "content": self.content,
            "source_agent": self.source_agent,
            "source_task": self.source_task or "",
            "confidence": self.confidence,
            "validation_count": self.validation_count,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "embedding": json.dumps(self.embedding) if self.embedding else "",
            "metadata": json.dumps(self.metadata),
            "validators": json.dumps(list(self.validators)),
            "usage_history": json.dumps(self.usage_history[-100:]),  # Keep last 100
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LearnedKnowledge":
        """Create from dictionary."""
        knowledge_type = data.get("knowledge_type", "fact")
        if isinstance(knowledge_type, str):
            knowledge_type = KnowledgeType(knowledge_type)
        
        embedding = data.get("embedding")
        if isinstance(embedding, str) and embedding:
            embedding = json.loads(embedding)
        
        metadata = data.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        
        validators = data.get("validators", [])
        if isinstance(validators, str):
            validators = json.loads(validators) if validators else []
        
        usage_history = data.get("usage_history", [])
        if isinstance(usage_history, str):
            usage_history = json.loads(usage_history) if usage_history else []
        
        return cls(
            knowledge_id=data["knowledge_id"],
            knowledge_type=knowledge_type,
            content=data["content"],
            source_agent=data["source_agent"],
            source_task=data.get("source_task") or None,
            confidence=float(data.get("confidence", 0.5)),
            validation_count=int(data.get("validation_count", 0)),
            usage_count=int(data.get("usage_count", 0)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            embedding=embedding if embedding else None,
            metadata=metadata,
            validators=set(validators),
            usage_history=usage_history,
        )


# =============================================================================
# KnowledgeLearner Class
# =============================================================================

class KnowledgeLearner:
    """
    Manages knowledge learning and storage for a single agent.
    
    Features:
    - Store and retrieve learned knowledge
    - Validate knowledge from other agents
    - Track usage and confidence evolution
    - Semantic search for relevant knowledge
    """
    
    def __init__(
        self,
        redis_client: Any,
        agent_id: str,
        config: Optional[LearningConfig] = None,
        embedding_model: Optional[Any] = None,
    ):
        """
        Initialize the knowledge learner.
        
        Args:
            redis_client: Connected Redis client.
            agent_id: Unique identifier for this agent.
            config: Learning configuration.
            embedding_model: Model for generating embeddings.
        """
        self.redis = redis_client
        self.agent_id = agent_id
        self.config = config or LearningConfig()
        self.embedding_model = embedding_model
        self._local_cache: Dict[str, LearnedKnowledge] = {}
    
    # =========================================================================
    # Knowledge Storage
    # =========================================================================
    
    async def store_knowledge(
        self,
        content: str,
        knowledge_type: KnowledgeType,
        task_id: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> LearnedKnowledge:
        """
        Store new learned knowledge.
        
        Args:
            content: The knowledge content.
            knowledge_type: Type of knowledge.
            task_id: Associated task ID.
            confidence: Initial confidence (default from config).
            metadata: Additional metadata.
            embedding: Pre-computed embedding.
            
        Returns:
            The created LearnedKnowledge instance.
        """
        # Generate embedding if not provided
        if embedding is None and self.embedding_model:
            embedding = await self._generate_embedding(content)
        
        # Create knowledge entry
        knowledge = LearnedKnowledge(
            knowledge_id=str(uuid.uuid4()),
            knowledge_type=knowledge_type,
            content=content,
            source_agent=self.agent_id,
            source_task=task_id,
            confidence=confidence if confidence is not None else self.config.initial_confidence,
            embedding=embedding,
            metadata=metadata or {},
        )
        
        # Store in Redis
        key = f"{self.config.knowledge_prefix}:{knowledge.knowledge_id}"
        ttl = self.config.knowledge_ttl if self.config.knowledge_ttl > 0 else None
        await self.redis.set_json(key, knowledge.to_dict(), ex=ttl)
        
        # Add to indexes
        await self._add_to_indexes(knowledge)
        
        # Update local cache
        self._local_cache[knowledge.knowledge_id] = knowledge
        
        logger.debug(f"Stored knowledge {knowledge.knowledge_id} from python.agent {self.agent_id}")
        return knowledge
    
    async def get_knowledge(self, knowledge_id: str) -> Optional[LearnedKnowledge]:
        """
        Retrieve knowledge by ID.
        
        Args:
            knowledge_id: The knowledge ID.
            
        Returns:
            LearnedKnowledge if found, None otherwise.
        """
        # Check local cache first
        if knowledge_id in self._local_cache:
            return self._local_cache[knowledge_id]
        
        # Fetch from Redis
        key = f"{self.config.knowledge_prefix}:{knowledge_id}"
        data = await self.redis.get_json(key)
        
        if data:
            knowledge = LearnedKnowledge.from_dict(data)
            self._local_cache[knowledge_id] = knowledge
            return knowledge
        
        return None
    
    async def delete_knowledge(self, knowledge_id: str) -> bool:
        """
        Delete knowledge by ID.
        
        Args:
            knowledge_id: The knowledge ID.
            
        Returns:
            True if deleted, False if not found.
        """
        # Get knowledge first to remove from indexes
        knowledge = await self.get_knowledge(knowledge_id)
        if not knowledge:
            return False
        
        # Delete from Redis
        key = f"{self.config.knowledge_prefix}:{knowledge_id}"
        result = await self.redis.delete(key)
        
        # Remove from indexes
        await self._remove_from_indexes(knowledge)
        
        # Remove from local cache
        if knowledge_id in self._local_cache:
            del self._local_cache[knowledge_id]
        
        return result > 0
    
    # =========================================================================
    # Knowledge Retrieval
    # =========================================================================
    
    async def retrieve_by_type(
        self,
        knowledge_type: KnowledgeType,
        limit: int = 100,
    ) -> List[LearnedKnowledge]:
        """
        Retrieve knowledge by type.
        
        Args:
            knowledge_type: Type to filter by.
            limit: Maximum results.
            
        Returns:
            List of matching knowledge entries.
        """
        index_key = f"{self.config.index_prefix}:type:{knowledge_type.value}"
        knowledge_ids = await self.redis.smembers(index_key)
        
        results = []
        for kid in list(knowledge_ids)[:limit]:
            knowledge = await self.get_knowledge(kid)
            if knowledge:
                results.append(knowledge)
        
        return results
    
    async def retrieve_by_confidence(
        self,
        min_confidence: float = 0.5,
        limit: int = 100,
    ) -> List[LearnedKnowledge]:
        """
        Retrieve knowledge above confidence threshold.
        
        Args:
            min_confidence: Minimum confidence threshold.
            limit: Maximum results.
            
        Returns:
            List of matching knowledge entries.
        """
        # Get all knowledge IDs
        all_ids = await self.redis.smembers(f"{self.config.index_prefix}:all")
        
        results = []
        for kid in all_ids:
            knowledge = await self.get_knowledge(kid)
            if knowledge and knowledge.confidence >= min_confidence:
                results.append(knowledge)
                if len(results) >= limit:
                    break
        
        # Sort by confidence descending
        results.sort(key=lambda k: k.confidence, reverse=True)
        return results[:limit]
    
    async def retrieve_recent(
        self,
        limit: int = 10,
        hours: int = 24,
    ) -> List[LearnedKnowledge]:
        """
        Retrieve recently added knowledge.
        
        Args:
            limit: Maximum results.
            hours: Time window in hours.
            
        Returns:
            List of recent knowledge entries.
        """
        cutoff = time.time() - (hours * 3600)
        all_ids = await self.redis.smembers(f"{self.config.index_prefix}:all")
        
        results = []
        for kid in all_ids:
            knowledge = await self.get_knowledge(kid)
            if knowledge and knowledge.created_at >= cutoff:
                results.append(knowledge)
        
        # Sort by created_at descending
        results.sort(key=lambda k: k.created_at, reverse=True)
        return results[:limit]
    
    async def search_knowledge(
        self,
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.3,
    ) -> List[LearnedKnowledge]:
        """
        Semantic search for knowledge.
        
        Args:
            query: Search query.
            top_k: Maximum results.
            min_similarity: Minimum similarity threshold.
            
        Returns:
            List of matching knowledge entries.
        """
        # Generate query embedding
        query_embedding = None
        if self.embedding_model:
            query_embedding = await self._generate_embedding(query)
        
        # Get all knowledge
        all_ids = await self.redis.smembers(f"{self.config.index_prefix}:all")
        
        scored_results = []
        for kid in all_ids:
            knowledge = await self.get_knowledge(kid)
            if knowledge:
                score = self._compute_similarity(query_embedding, knowledge, query)
                if score >= min_similarity:
                    scored_results.append((knowledge, score))
        
        # Sort by score descending
        scored_results.sort(key=lambda x: x[1], reverse=True)
        return [k for k, _ in scored_results[:top_k]]
    
    # =========================================================================
    # Validation
    # =========================================================================
    
    async def validate_knowledge(
        self,
        knowledge_id: str,
        validator_agent: str,
        success: bool,
    ) -> Optional[LearnedKnowledge]:
        """
        Validate knowledge from another agent.
        
        Args:
            knowledge_id: Knowledge to validate.
            validator_agent: Agent performing validation.
            success: Whether validation succeeded.
            
        Returns:
            Updated knowledge, or None if not found.
        """
        knowledge = await self.get_knowledge(knowledge_id)
        if not knowledge:
            return None
        
        # Update validation count
        knowledge.validation_count += 1
        knowledge.validators.add(validator_agent)
        knowledge.updated_at = time.time()
        
        # Calculate confidence adjustment
        if success:
            # Determine validation level
            is_self = validator_agent == knowledge.source_agent
            is_multi = len(knowledge.validators) >= 3
            
            if is_multi:
                boost = self.config.validation_boost_multi
            elif is_self:
                boost = self.config.validation_boost_self
            else:
                boost = self.config.validation_boost_peer
            
            knowledge.confidence = min(1.0, knowledge.confidence + boost)
        else:
            knowledge.confidence = max(0.0, knowledge.confidence - self.config.validation_penalty)
        
        # Save updated knowledge
        key = f"{self.config.knowledge_prefix}:{knowledge_id}"
        await self.redis.set_json(key, knowledge.to_dict())
        self._local_cache[knowledge_id] = knowledge
        
        logger.debug(f"Validated knowledge {knowledge_id}: success={success}, confidence={knowledge.confidence}")
        return knowledge
    
    async def record_usage(
        self,
        knowledge_id: str,
        success: bool,
        task_id: Optional[str] = None,
    ) -> Optional[LearnedKnowledge]:
        """
        Record usage of knowledge.
        
        Args:
            knowledge_id: Knowledge that was used.
            success: Whether usage was successful.
            task_id: Task where knowledge was used.
            
        Returns:
            Updated knowledge, or None if not found.
        """
        knowledge = await self.get_knowledge(knowledge_id)
        if not knowledge:
            return None
        
        # Update usage count
        knowledge.usage_count += 1
        knowledge.updated_at = time.time()
        
        # Record in history
        knowledge.usage_history.append({
            "task_id": task_id,
            "success": success,
            "timestamp": time.time(),
        })
        
        # Adjust confidence based on usage
        if success:
            boost = self.config.usage_success_boost
            # Extra boost if production-proven
            if knowledge.usage_count >= self.config.production_proven_usage_count:
                boost += self.config.validation_boost_production
            knowledge.confidence = min(1.0, knowledge.confidence + boost)
        else:
            knowledge.confidence = max(0.0, knowledge.confidence - self.config.usage_failure_penalty)
        
        # Save updated knowledge
        key = f"{self.config.knowledge_prefix}:{knowledge_id}"
        await self.redis.set_json(key, knowledge.to_dict())
        self._local_cache[knowledge_id] = knowledge
        
        logger.debug(f"Recorded usage for {knowledge_id}: success={success}, usage_count={knowledge.usage_count}")
        return knowledge
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get knowledge statistics.
        
        Returns:
            Dictionary with statistics.
        """
        all_ids = await self.redis.smembers(f"{self.config.index_prefix}:all")
        
        by_type = {}
        total_confidence = 0.0
        total_validations = 0
        total_usage = 0
        
        for kid in all_ids:
            knowledge = await self.get_knowledge(kid)
            if knowledge:
                type_key = knowledge.knowledge_type.value if isinstance(knowledge.knowledge_type, KnowledgeType) else knowledge.knowledge_type
                by_type[type_key] = by_type.get(type_key, 0) + 1
                total_confidence += knowledge.confidence
                total_validations += knowledge.validation_count
                total_usage += knowledge.usage_count
        
        total = len(all_ids)
        return {
            "agent_id": self.agent_id,
            "total_knowledge": total,
            "by_type": by_type,
            "avg_confidence": total_confidence / total if total > 0 else 0,
            "total_validations": total_validations,
            "total_usage": total_usage,
            "local_cache_size": len(self._local_cache),
        }
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text."""
        if self.embedding_model is None:
            return []
        
        try:
            embedding = self.embedding_model.encode(text)
            return embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return []
    
    def _compute_similarity(
        self,
        query_embedding: Optional[List[float]],
        knowledge: LearnedKnowledge,
        query_text: str,
    ) -> float:
        """Compute similarity between query and knowledge."""
        if query_embedding and knowledge.embedding:
            # Use canonical cosine similarity from semantic_embeddings (DUP-6)
            from python.helpers.semantic_embeddings import cosine_similarity
            import numpy as np
            return cosine_similarity(np.array(query_embedding), np.array(knowledge.embedding))
        
        # Fallback to simple text matching
        query_words = set(query_text.lower().split())
        content_words = set(knowledge.content.lower().split())
        
        if not query_words or not content_words:
            return 0.0
        
        intersection = query_words & content_words
        union = query_words | content_words
        
        return len(intersection) / len(union) if union else 0.0
    
    async def _add_to_indexes(self, knowledge: LearnedKnowledge) -> None:
        """Add knowledge to indexes."""
        # All knowledge index
        await self.redis.sadd(f"{self.config.index_prefix}:all", knowledge.knowledge_id)
        
        # Type index
        type_value = knowledge.knowledge_type.value if isinstance(knowledge.knowledge_type, KnowledgeType) else knowledge.knowledge_type
        await self.redis.sadd(f"{self.config.index_prefix}:type:{type_value}", knowledge.knowledge_id)
        
        # Agent index
        await self.redis.sadd(f"{self.config.index_prefix}:agent:{knowledge.source_agent}", knowledge.knowledge_id)
    
    async def _remove_from_indexes(self, knowledge: LearnedKnowledge) -> None:
        """Remove knowledge from indexes."""
        await self.redis.srem(f"{self.config.index_prefix}:all", knowledge.knowledge_id)
        
        type_value = knowledge.knowledge_type.value if isinstance(knowledge.knowledge_type, KnowledgeType) else knowledge.knowledge_type
        await self.redis.srem(f"{self.config.index_prefix}:type:{type_value}", knowledge.knowledge_id)
        
        await self.redis.srem(f"{self.config.index_prefix}:agent:{knowledge.source_agent}", knowledge.knowledge_id)


# =============================================================================
# KnowledgePropagator Class
# =============================================================================

class KnowledgePropagator:
    """
    Manages knowledge propagation between agents.
    
    Features:
    - Real-time broadcast of new knowledge
    - Periodic consolidation of similar knowledge
    - Knowledge distillation for agent profiles
    """
    
    def __init__(
        self,
        redis_client: Any,
        config: Optional[LearningConfig] = None,
    ):
        """
        Initialize the knowledge propagator.
        
        Args:
            redis_client: Connected Redis client.
            config: Learning configuration.
        """
        self.redis = redis_client
        self.config = config or LearningConfig()
        self._listener_task: Optional[asyncio.Task] = None
        self._is_listening = False
        
        # Statistics
        self._broadcasts_sent = 0
        self._broadcasts_received = 0
        self._consolidations_run = 0
        self._last_consolidation: Optional[float] = None
    
    @property
    def is_listening(self) -> bool:
        """Check if listener is active."""
        return self._is_listening
    
    # =========================================================================
    # Broadcasting
    # =========================================================================
    
    async def broadcast_knowledge(self, knowledge: LearnedKnowledge) -> bool:
        """
        Broadcast knowledge to all agents.
        
        Args:
            knowledge: Knowledge to broadcast.
            
        Returns:
            True if broadcast succeeded, False otherwise.
        """
        if not self.config.broadcast_enabled:
            return False
        
        message = json.dumps({
            "type": "knowledge_update",
            "knowledge_id": knowledge.knowledge_id,
            "knowledge_type": knowledge.knowledge_type.value if isinstance(knowledge.knowledge_type, KnowledgeType) else knowledge.knowledge_type,
            "source_agent": knowledge.source_agent,
            "confidence": knowledge.confidence,
            "timestamp": time.time(),
        })
        
        await self.redis.publish(self.config.broadcast_channel, message)
        self._broadcasts_sent += 1
        
        logger.debug(f"Broadcast knowledge {knowledge.knowledge_id}")
        return True
    
    async def start_listener(self) -> None:
        """Start listening for knowledge broadcasts."""
        if self._listener_task and not self._listener_task.done():
            return
        
        self._is_listening = True
        self._listener_task = asyncio.create_task(self._broadcast_listener())
        logger.info("Started knowledge broadcast listener")
    
    async def stop_listener(self) -> None:
        """Stop the broadcast listener."""
        self._is_listening = False
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                logger.debug("[CrossAgentLearning] Listener task cancelled during stop")
        logger.info("Stopped knowledge broadcast listener")
    
    async def _broadcast_listener(self) -> None:
        """Background task for listening to broadcasts."""
        try:
            pubsub = await self.redis.subscribe(self.config.broadcast_channel)
            
            async for message in pubsub.listen():
                if not self._is_listening:
                    break
                    
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        if data.get("type") == "knowledge_update":
                            self._broadcasts_received += 1
                            logger.debug(f"Received broadcast: {data.get('knowledge_id')}")
                    except Exception as e:
                        logger.error(f"Error processing broadcast: {e}")
        except asyncio.CancelledError:
            logger.debug("[CrossAgentLearning] Broadcast listener cancelled — shutting down gracefully")
    
    # =========================================================================
    # Consolidation
    # =========================================================================
    
    async def consolidate_knowledge(self) -> Dict[str, Any]:
        """
        Consolidate and deduplicate knowledge entries.
        
        Returns:
            Consolidation statistics.
        """
        all_ids = await self.redis.smembers(f"{self.config.index_prefix}:all")
        total_before = len(all_ids)
        
        # Group knowledge by type
        by_type: Dict[str, List[LearnedKnowledge]] = {}
        for kid in all_ids:
            key = f"{self.config.knowledge_prefix}:{kid}"
            data = await self.redis.get_json(key)
            if data:
                knowledge = LearnedKnowledge.from_dict(data)
                type_key = knowledge.knowledge_type.value if isinstance(knowledge.knowledge_type, KnowledgeType) else knowledge.knowledge_type
                if type_key not in by_type:
                    by_type[type_key] = []
                by_type[type_key].append(knowledge)
        
        merged_count = 0
        # For now, just count - actual merging would require semantic similarity
        # This is a placeholder for more sophisticated consolidation
        
        self._consolidations_run += 1
        self._last_consolidation = time.time()
        
        total_after = len(all_ids) - merged_count
        
        return {
            "total_before": total_before,
            "total_after": total_after,
            "merged_count": merged_count,
            "by_type": {k: len(v) for k, v in by_type.items()},
            "timestamp": time.time(),
        }
    
    # =========================================================================
    # Distillation
    # =========================================================================
    
    async def distill_for_profile(self, profile: str) -> Dict[str, Any]:
        """
        Distill knowledge for a specific agent profile.
        
        Args:
            profile: Agent profile to distill for.
            
        Returns:
            Distilled knowledge summary.
        """
        all_ids = await self.redis.smembers(f"{self.config.index_prefix}:all")
        
        relevant_knowledge = []
        for kid in all_ids:
            key = f"{self.config.knowledge_prefix}:{kid}"
            data = await self.redis.get_json(key)
            if data:
                knowledge = LearnedKnowledge.from_dict(data)
                # Check if knowledge is relevant to profile
                if knowledge.metadata.get("profile") == profile:
                    relevant_knowledge.append(knowledge)
        
        # Sort by confidence
        relevant_knowledge.sort(key=lambda k: k.confidence, reverse=True)
        
        # Create summary
        summary_parts = []
        for k in relevant_knowledge[:10]:  # Top 10
            summary_parts.append(f"- {k.content} (confidence: {k.confidence:.2f})")
        
        return {
            "profile": profile,
            "knowledge_count": len(relevant_knowledge),
            "summary": "\n".join(summary_parts) if summary_parts else "No knowledge found for this profile.",
            "timestamp": time.time(),
        }
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get propagation statistics.
        
        Returns:
            Dictionary with statistics.
        """
        return {
            "broadcasts_sent": self._broadcasts_sent,
            "broadcasts_received": self._broadcasts_received,
            "consolidations_run": self._consolidations_run,
            "last_consolidation": self._last_consolidation,
            "is_listening": self._is_listening,
        }


# =============================================================================
# Factory Function
# =============================================================================

async def create_knowledge_learner(
    redis_client: Any,
    agent_id: str,
    config: Optional[Dict] = None,
    embedding_model: Optional[Any] = None,
) -> KnowledgeLearner:
    """
    Factory function to create a knowledge learner.
    
    Args:
        redis_client: Connected Redis client.
        agent_id: Unique agent identifier.
        config: Configuration dictionary.
        embedding_model: Model for generating embeddings.
        
    Returns:
        Initialized KnowledgeLearner instance.
    """
    learning_config = LearningConfig()
    
    if config:
        learning_config.enabled = config.get("enabled", learning_config.enabled)
        learning_config.broadcast_enabled = config.get("broadcast_enabled", learning_config.broadcast_enabled)
        learning_config.min_confidence_threshold = config.get("min_confidence_threshold", learning_config.min_confidence_threshold)
        learning_config.max_knowledge_entries = config.get("max_knowledge_entries", learning_config.max_knowledge_entries)
        learning_config.knowledge_ttl = config.get("knowledge_ttl", learning_config.knowledge_ttl)
    
    return KnowledgeLearner(redis_client, agent_id, learning_config, embedding_model)

# Backward-compat alias: tests import CrossAgentLearning.
# _broadcast_listener lives in KnowledgePropagator (not KnowledgeLearner).
CrossAgentLearning = KnowledgePropagator
