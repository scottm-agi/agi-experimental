from __future__ import annotations
"""
Loop Prevention Engine for Master Agent Supervisor

This module provides the critical loop prevention system that ensures
the supervisor doesn't create infinite correction loops when intervening
with stuck agents.

Key Features:
- Intervention cooldown management
- Same-pattern repeat detection
- Intervention fingerprinting
- Meta-loop detection (supervisor correcting its own corrections)
- Escalation triggers
"""

import asyncio
from python.helpers.hashing import dedup_hash
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

logger = logging.getLogger(__name__)


class InterventionOutcome(Enum):
    """Outcome of an intervention attempt."""
    PENDING = "pending"           # Intervention delivered, awaiting result
    SUCCESS = "success"           # Agent recovered after intervention
    PARTIAL = "partial"           # Some improvement but not fully resolved
    FAILURE = "failure"           # Intervention didn't help
    ESCALATED = "escalated"       # Had to escalate to higher level
    BLOCKED = "blocked"           # Intervention was blocked by loop prevention


class PatternType(Enum):
    """Types of problematic patterns that can be detected."""
    CONTEXT_OVERFLOW = "context_overflow"
    RESPONSE_LOOP = "response_loop"
    TOOL_FAILURE_LOOP = "tool_failure_loop"
    PROGRESS_STALL = "progress_stall"
    RATE_LIMIT = "rate_limit"
    INFINITE_RECURSION = "infinite_recursion"
    OUTPUT_DEGRADATION = "output_degradation"
    REPETITIVE_ACTION = "repetitive_action"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    PREMATURE_COMPLETION = "premature_completion"
    VERDICT_PATTERN = "verdict_pattern"  # Issue #1093: Repeated verdict strings in tool results
    DELEGATION_RETEST_LOOP = "delegation_retest_loop"  # Iter73: Same test profile delegated repeatedly without code-fix
    UNKNOWN = "unknown"


class InterventionType(Enum):
    """Types of interventions the supervisor can perform."""
    CONTEXT_CONDENSATION = "context_condensation"
    LOOP_BREAKING = "loop_breaking"
    TOOL_ALTERNATIVE = "tool_alternative"
    TASK_REDIRECTION = "task_redirection"
    BACKOFF_WAIT = "backoff_wait"
    PROVIDE_HINT = "provide_hint"
    INJECT_TOOL = "inject_tool"
    FORCE_RESPONSE = "force_response"
    ESCALATE = "escalate"


@dataclass
class InterventionRecord:
    """Record of a single intervention attempt."""
    id: str
    agent_id: str
    context_id: str
    pattern_type: PatternType
    intervention_type: InterventionType
    fingerprint: str  # Hash of intervention content for deduplication
    timestamp: datetime
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    outcome: InterventionOutcome = InterventionOutcome.PENDING
    outcome_timestamp: Optional[datetime] = None
    outcome_details: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "context_id": self.context_id,
            "pattern_type": self.pattern_type.value,
            "intervention_type": self.intervention_type.value,
            "fingerprint": self.fingerprint,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message[:200] + "..." if len(self.message) > 200 else self.message,
            "metadata": self.metadata,
            "outcome": self.outcome.value,
            "outcome_timestamp": self.outcome_timestamp.isoformat() if self.outcome_timestamp else None,
            "outcome_details": self.outcome_details,
        }


@dataclass
class LoopPreventionConfig:
    """Configuration for loop prevention behavior."""
    # Per-agent limits
    max_interventions_per_agent: int = 5
    intervention_cooldown_seconds: float = 60.0
    max_same_pattern_interventions: int = 3
    
    # Per-task limits
    max_interventions_per_task: int = 10
    
    # Meta-loop detection
    meta_loop_detection_window_seconds: float = 300.0  # 5 minutes
    meta_loop_fingerprint_threshold: int = 2  # Same fingerprint appearing twice
    
    # Escalation triggers
    escalation_threshold: int = 3  # Failed interventions before escalation
    
    # Fingerprint similarity threshold (0-1)
    fingerprint_similarity_threshold: float = 0.8
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_interventions_per_agent": self.max_interventions_per_agent,
            "intervention_cooldown_seconds": self.intervention_cooldown_seconds,
            "max_same_pattern_interventions": self.max_same_pattern_interventions,
            "max_interventions_per_task": self.max_interventions_per_task,
            "meta_loop_detection_window_seconds": self.meta_loop_detection_window_seconds,
            "meta_loop_fingerprint_threshold": self.meta_loop_fingerprint_threshold,
            "escalation_threshold": self.escalation_threshold,
            "fingerprint_similarity_threshold": self.fingerprint_similarity_threshold,
        }


@dataclass
class BlockReason:
    """Reason why an intervention was blocked."""
    rule: str
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "description": self.description,
            "details": self.details,
        }


class LoopPreventionEngine:
    """
    Prevents infinite loops in supervisor interventions.
    
    This is the most critical component of the supervisor system.
    It ensures that the supervisor doesn't create cascading correction
    loops that could destabilize the entire agent system.
    
    Rules enforced:
    1. Cooldown period between interventions for same agent
    2. Maximum interventions per agent limit
    3. Maximum same-pattern interventions limit
    4. Intervention fingerprint deduplication
    5. Meta-loop detection (supervisor correcting its own corrections)
    6. Per-task intervention limits
    """
    
    def __init__(
        self,
        config: Optional[LoopPreventionConfig] = None,
        redis_client: Optional[Any] = None,
    ):
        self.config = config or LoopPreventionConfig()
        self.redis_client = redis_client
        
        # In-memory state (also persisted to Redis if available)
        self._intervention_history: Dict[str, List[InterventionRecord]] = {}  # agent_id -> records
        self._task_interventions: Dict[str, List[str]] = {}  # task_id -> intervention_ids
        self._fingerprint_cache: Dict[str, List[datetime]] = {}  # fingerprint -> timestamps
        self._cooldown_tracker: Dict[str, datetime] = {}  # agent_id -> last_intervention_time
        
        self._lock = asyncio.Lock()
        
        # Statistics
        self._stats = {
            "total_checks": 0,
            "interventions_allowed": 0,
            "interventions_blocked": 0,
            "blocks_by_rule": {},
            "escalations_triggered": 0,
        }
    
    async def should_intervene(
        self,
        agent_id: str,
        context_id: str,
        pattern_type: PatternType,
        intervention_type: InterventionType,
        proposed_message: str,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[BlockReason]]:
        """
        Check if an intervention should be allowed.
        
        Args:
            agent_id: ID of the agent to intervene with
            context_id: ID of the agent context
            pattern_type: Type of pattern detected
            intervention_type: Type of intervention proposed
            proposed_message: The intervention message content
            task_id: Optional task ID for per-task limits
            metadata: Additional metadata about the intervention
            
        Returns:
            Tuple of (should_intervene, block_reason)
            - (True, None) if intervention is allowed
            - (False, BlockReason) if intervention is blocked
        """
        async with self._lock:
            self._stats["total_checks"] += 1
            
            # Generate fingerprint for this intervention
            fingerprint = self._generate_fingerprint(
                agent_id, pattern_type, intervention_type, proposed_message
            )
            
            # Rule 1: Check cooldown period
            block_reason = await self._check_cooldown(agent_id)
            if block_reason:
                return self._block(block_reason)
            
            # Rule 2: Check max interventions per agent
            block_reason = await self._check_agent_limit(agent_id)
            if block_reason:
                return self._block(block_reason)
            
            # Rule 3: Check same-pattern repeat limit
            block_reason = await self._check_pattern_limit(agent_id, pattern_type)
            if block_reason:
                return self._block(block_reason)
            
            # Rule 4: Check fingerprint deduplication
            block_reason = await self._check_fingerprint_duplicate(fingerprint)
            if block_reason:
                return self._block(block_reason)
            
            # Rule 5: Check meta-loop detection
            block_reason = await self._check_meta_loop(agent_id, fingerprint)
            if block_reason:
                return self._block(block_reason)
            
            # Rule 6: Check per-task limit
            if task_id:
                block_reason = await self._check_task_limit(task_id)
                if block_reason:
                    return self._block(block_reason)
            
            # All checks passed - intervention allowed
            self._stats["interventions_allowed"] += 1
            return (True, None)
    
    async def record_intervention(
        self,
        agent_id: str,
        context_id: str,
        pattern_type: PatternType,
        intervention_type: InterventionType,
        message: str,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InterventionRecord:
        """
        Record an intervention that was performed.
        
        This should be called after should_intervene() returns True
        and the intervention is actually delivered.
        """
        async with self._lock:
            fingerprint = self._generate_fingerprint(
                agent_id, pattern_type, intervention_type, message
            )
            
            record = InterventionRecord(
                id=str(uuid.uuid4())[:8],
                agent_id=agent_id,
                context_id=context_id,
                pattern_type=pattern_type,
                intervention_type=intervention_type,
                fingerprint=fingerprint,
                timestamp=datetime.now(timezone.utc),
                message=message,
                metadata=metadata or {},
            )
            
            # Update in-memory state
            if agent_id not in self._intervention_history:
                self._intervention_history[agent_id] = []
            self._intervention_history[agent_id].append(record)
            
            # Update cooldown tracker
            self._cooldown_tracker[agent_id] = record.timestamp
            
            # Update fingerprint cache
            if fingerprint not in self._fingerprint_cache:
                self._fingerprint_cache[fingerprint] = []
            self._fingerprint_cache[fingerprint].append(record.timestamp)
            
            # Update task tracking
            if task_id:
                if task_id not in self._task_interventions:
                    self._task_interventions[task_id] = []
                self._task_interventions[task_id].append(record.id)
            
            # Persist to Redis if available
            if self.redis_client:
                await self._persist_record(record)
            
            logger.info(
                f"Recorded intervention {record.id} for agent {agent_id}: "
                f"{pattern_type.value} -> {intervention_type.value}"
            )
            
            return record
    
    async def record_outcome(
        self,
        intervention_id: str,
        outcome: InterventionOutcome,
        details: Optional[str] = None,
    ) -> bool:
        """
        Record the outcome of an intervention.
        
        This should be called after observing whether the intervention
        helped the agent recover.
        """
        async with self._lock:
            # Find the record
            for agent_records in self._intervention_history.values():
                for record in agent_records:
                    if record.id == intervention_id:
                        record.outcome = outcome
                        record.outcome_timestamp = datetime.now(timezone.utc)
                        record.outcome_details = details
                        
                        # Persist to Redis if available
                        if self.redis_client:
                            await self._persist_record(record)
                        
                        logger.info(
                            f"Recorded outcome for intervention {intervention_id}: {outcome.value}"
                        )
                        return True
            
            return False
    
    async def should_escalate(
        self,
        agent_id: str,
        pattern_type: PatternType,
    ) -> Tuple[bool, str]:
        """
        Check if we should escalate to a higher level.
        
        Returns:
            Tuple of (should_escalate, reason)
        """
        async with self._lock:
            records = self._intervention_history.get(agent_id, [])
            
            # Count failed interventions for this pattern
            failed_count = sum(
                1 for r in records
                if r.pattern_type == pattern_type
                and r.outcome == InterventionOutcome.FAILURE
            )
            
            if failed_count >= self.config.escalation_threshold:
                self._stats["escalations_triggered"] += 1
                return (
                    True,
                    f"Failed {failed_count} interventions for pattern {pattern_type.value}"
                )
            
            # Check total interventions
            total_interventions = len(records)
            if total_interventions >= self.config.max_interventions_per_agent:
                self._stats["escalations_triggered"] += 1
                return (
                    True,
                    f"Reached max interventions ({total_interventions}) for agent"
                )
            
            return (False, "")
    
    async def get_intervention_history(
        self,
        agent_id: str,
        limit: int = 10,
    ) -> List[InterventionRecord]:
        """Get recent intervention history for an agent."""
        async with self._lock:
            records = self._intervention_history.get(agent_id, [])
            return records[-limit:]
    
    async def get_failed_interventions(
        self,
        agent_id: str,
        pattern_type: Optional[PatternType] = None,
    ) -> List[InterventionRecord]:
        """Get failed interventions for an agent."""
        async with self._lock:
            records = self._intervention_history.get(agent_id, [])
            failed = [
                r for r in records
                if r.outcome == InterventionOutcome.FAILURE
            ]
            if pattern_type:
                failed = [r for r in failed if r.pattern_type == pattern_type]
            return failed
    
    async def clear_agent_history(self, agent_id: str) -> None:
        """Clear intervention history for an agent (e.g., on task completion)."""
        async with self._lock:
            if agent_id in self._intervention_history:
                del self._intervention_history[agent_id]
            if agent_id in self._cooldown_tracker:
                del self._cooldown_tracker[agent_id]
            
            logger.info(f"Cleared intervention history for agent {agent_id}")
    
    async def clear_task_history(self, task_id: str) -> None:
        """Clear intervention history for a task."""
        async with self._lock:
            if task_id in self._task_interventions:
                del self._task_interventions[task_id]
            
            logger.info(f"Cleared intervention history for task {task_id}")
    
    async def get_all_interventions(self, limit: int = 100) -> List[InterventionRecord]:
        """Get all intervention records across all agents."""
        async with self._lock:
            all_records = []
            for agent_records in self._intervention_history.values():
                all_records.extend(agent_records)
            
            # Sort by timestamp descending
            all_records.sort(key=lambda r: r.timestamp, reverse=True)
            return all_records[:limit]
    
    async def clear_all_history(self) -> None:
        """Clear all intervention history."""
        async with self._lock:
            self._intervention_history.clear()
            self._task_interventions.clear()
            self._fingerprint_cache.clear()
            self._cooldown_tracker.clear()
            
            logger.info("Cleared all intervention history")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get loop prevention statistics."""
        return {
            **self._stats,
            "active_agents": len(self._intervention_history),
            "active_tasks": len(self._task_interventions),
            "cached_fingerprints": len(self._fingerprint_cache),
            "config": self.config.to_dict(),
        }
    
    # =========================================================================
    # Private Methods - Rule Checks
    # =========================================================================
    
    def _block(self, reason: BlockReason) -> Tuple[bool, BlockReason]:
        """Record a block and return the result."""
        self._stats["interventions_blocked"] += 1
        rule = reason.rule
        if rule not in self._stats["blocks_by_rule"]:
            self._stats["blocks_by_rule"][rule] = 0
        self._stats["blocks_by_rule"][rule] += 1
        
        logger.warning(f"Intervention blocked: {reason.rule} - {reason.description}")
        return (False, reason)
    
    async def _check_cooldown(self, agent_id: str) -> Optional[BlockReason]:
        """Check if agent is in cooldown period."""
        last_intervention = self._cooldown_tracker.get(agent_id)
        if last_intervention:
            elapsed = (datetime.now(timezone.utc) - last_intervention).total_seconds()
            if elapsed < self.config.intervention_cooldown_seconds:
                remaining = self.config.intervention_cooldown_seconds - elapsed
                return BlockReason(
                    rule="cooldown",
                    description=f"Agent {agent_id} is in cooldown period",
                    details={
                        "elapsed_seconds": elapsed,
                        "remaining_seconds": remaining,
                        "cooldown_seconds": self.config.intervention_cooldown_seconds,
                    },
                )
        return None
    
    async def _check_agent_limit(self, agent_id: str) -> Optional[BlockReason]:
        """Check if agent has reached max interventions."""
        records = self._intervention_history.get(agent_id, [])
        if len(records) >= self.config.max_interventions_per_agent:
            return BlockReason(
                rule="agent_limit",
                description=f"Agent {agent_id} has reached max interventions",
                details={
                    "current_count": len(records),
                    "max_allowed": self.config.max_interventions_per_agent,
                },
            )
        return None
    
    async def _check_pattern_limit(
        self,
        agent_id: str,
        pattern_type: PatternType,
    ) -> Optional[BlockReason]:
        """Check if same pattern has been intervened too many times."""
        records = self._intervention_history.get(agent_id, [])
        pattern_count = sum(1 for r in records if r.pattern_type == pattern_type)
        
        if pattern_count >= self.config.max_same_pattern_interventions:
            return BlockReason(
                rule="pattern_limit",
                description=f"Pattern {pattern_type.value} has been intervened too many times",
                details={
                    "pattern_type": pattern_type.value,
                    "current_count": pattern_count,
                    "max_allowed": self.config.max_same_pattern_interventions,
                },
            )
        return None
    
    async def _check_fingerprint_duplicate(
        self,
        fingerprint: str,
    ) -> Optional[BlockReason]:
        """Check if this exact intervention has been tried recently."""
        timestamps = self._fingerprint_cache.get(fingerprint, [])
        
        # Clean old timestamps
        window_start = datetime.now(timezone.utc).timestamp() - self.config.meta_loop_detection_window_seconds
        recent_timestamps = [
            ts for ts in timestamps
            if ts.timestamp() > window_start
        ]
        self._fingerprint_cache[fingerprint] = recent_timestamps
        
        if len(recent_timestamps) >= self.config.meta_loop_fingerprint_threshold:
            return BlockReason(
                rule="fingerprint_duplicate",
                description="This exact intervention has been tried recently",
                details={
                    "fingerprint": fingerprint[:16] + "...",
                    "recent_occurrences": len(recent_timestamps),
                    "threshold": self.config.meta_loop_fingerprint_threshold,
                },
            )
        return None
    
    async def _check_meta_loop(
        self,
        agent_id: str,
        fingerprint: str,
    ) -> Optional[BlockReason]:
        """
        Detect meta-loops where supervisor is correcting its own corrections.
        
        This looks for patterns like:
        - Intervention A -> Agent does X -> Intervention A again
        - Intervention A -> Intervention B -> Intervention A
        """
        records = self._intervention_history.get(agent_id, [])
        if len(records) < 2:
            return None
        
        # Get recent fingerprints
        window_start = datetime.now(timezone.utc).timestamp() - self.config.meta_loop_detection_window_seconds
        recent_fingerprints = [
            r.fingerprint for r in records
            if r.timestamp.timestamp() > window_start
        ]
        
        # Check for repeating patterns
        if len(recent_fingerprints) >= 3:
            # Look for A-B-A pattern
            for i in range(len(recent_fingerprints) - 2):
                if recent_fingerprints[i] == recent_fingerprints[i + 2]:
                    return BlockReason(
                        rule="meta_loop",
                        description="Detected meta-loop pattern in interventions",
                        details={
                            "pattern": "A-B-A",
                            "fingerprint": recent_fingerprints[i][:16] + "...",
                        },
                    )
        
        # Check if proposed fingerprint matches recent ones
        if fingerprint in recent_fingerprints[-3:]:
            return BlockReason(
                rule="meta_loop",
                description="Proposed intervention matches recent intervention",
                details={
                    "fingerprint": fingerprint[:16] + "...",
                    "recent_count": recent_fingerprints.count(fingerprint),
                },
            )
        
        return None
    
    async def _check_task_limit(self, task_id: str) -> Optional[BlockReason]:
        """Check if task has reached max interventions."""
        intervention_ids = self._task_interventions.get(task_id, [])
        if len(intervention_ids) >= self.config.max_interventions_per_task:
            return BlockReason(
                rule="task_limit",
                description=f"Task {task_id} has reached max interventions",
                details={
                    "current_count": len(intervention_ids),
                    "max_allowed": self.config.max_interventions_per_task,
                },
            )
        return None
    
    # =========================================================================
    # Private Methods - Utilities
    # =========================================================================
    
    def _generate_fingerprint(
        self,
        agent_id: str,
        pattern_type: PatternType,
        intervention_type: InterventionType,
        message: str,
    ) -> str:
        """Generate a fingerprint for an intervention."""
        # Normalize message (remove timestamps, specific values)
        normalized_message = self._normalize_message(message)
        
        return dedup_hash({
            "agent_id": agent_id,
            "pattern_type": pattern_type.value,
            "intervention_type": intervention_type.value,
            "message_hash": dedup_hash(normalized_message),
        })
    
    def _normalize_message(self, message: str) -> str:
        """Normalize a message for fingerprinting."""
        import re
        
        # Remove timestamps
        message = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '[TIMESTAMP]', message)
        
        # Remove UUIDs
        message = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '[UUID]', message)
        
        # Remove numbers (but keep structure)
        message = re.sub(r'\b\d+\b', '[NUM]', message)
        
        # Normalize whitespace
        message = ' '.join(message.split())
        
        return message.lower()
    
    async def _persist_record(self, record: InterventionRecord) -> None:
        """Persist an intervention record to Redis."""
        if not self.redis_client:
            return
        
        try:
            key = f"supervisor:intervention:{record.id}"
            await self.redis_client.set_json(
                key,
                record.to_dict(),
                ex=86400 * 7,  # 7 days TTL
            )
            
            # Add to agent's intervention list
            list_key = f"supervisor:agent:{record.agent_id}:interventions"
            await self.redis_client.lpush(list_key, record.id)
            
        except Exception as e:
            logger.error(f"Failed to persist intervention record: {e}")


# =============================================================================
# Factory Function
# =============================================================================

def create_loop_prevention_engine(
    config: Optional[Dict[str, Any]] = None,
    redis_client: Optional[Any] = None,
) -> LoopPreventionEngine:
    """Create a loop prevention engine with optional configuration."""
    lp_config = LoopPreventionConfig()
    
    if config:
        lp_config.max_interventions_per_agent = config.get(
            "max_interventions_per_agent", lp_config.max_interventions_per_agent
        )
        lp_config.intervention_cooldown_seconds = config.get(
            "intervention_cooldown_seconds", lp_config.intervention_cooldown_seconds
        )
        lp_config.max_same_pattern_interventions = config.get(
            "max_same_pattern_interventions", lp_config.max_same_pattern_interventions
        )
        lp_config.max_interventions_per_task = config.get(
            "max_interventions_per_task", lp_config.max_interventions_per_task
        )
        lp_config.meta_loop_detection_window_seconds = config.get(
            "meta_loop_detection_window_seconds", lp_config.meta_loop_detection_window_seconds
        )
        lp_config.escalation_threshold = config.get(
            "escalation_threshold", lp_config.escalation_threshold
        )
    
    return LoopPreventionEngine(config=lp_config, redis_client=redis_client)
