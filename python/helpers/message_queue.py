from __future__ import annotations
"""
Message Queue for AGIX Parallel Swarm

This module provides a Redis Streams-based message queue with:
- Task queue for distributing work to agents
- Result queue for collecting agent outputs
- Error queue for failed tasks
- Consumer groups for distributed processing
- Dead letter queue for permanent failures
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from python.redis_client import RedisClient, create_redis_client

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Status of a task in the queue."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


class TaskPriority(Enum):
    """Priority levels for tasks."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Task:
    """Represents a task in the message queue."""
    id: str
    type: str
    payload: Dict[str, Any]
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    retry_count: int = 0
    max_retries: int = 3
    timeout: int = 300  # seconds
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_task_id: Optional[str] = None
    agent_id: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary for serialization."""
        return {
            "id": self.id,
            "type": self.type,
            "payload": json.dumps(self.payload),
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "retry_count": str(self.retry_count),
            "max_retries": str(self.max_retries),
            "timeout": str(self.timeout),
            "metadata": json.dumps(self.metadata),
            "parent_task_id": self.parent_task_id or "",
            "agent_id": self.agent_id or "",
            "result": json.dumps(self.result) if self.result else "",
            "error": self.error or "",
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "Task":
        """Create task from dictionary."""
        return cls(
            id=data["id"],
            type=data["type"],
            payload=json.loads(data["payload"]),
            priority=TaskPriority(int(data["priority"])),
            status=TaskStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            retry_count=int(data["retry_count"]),
            max_retries=int(data["max_retries"]),
            timeout=int(data["timeout"]),
            metadata=json.loads(data["metadata"]) if data["metadata"] else {},
            parent_task_id=data["parent_task_id"] or None,
            agent_id=data["agent_id"] or None,
            result=json.loads(data["result"]) if data["result"] else None,
            error=data["error"] or None,
        )


@dataclass
class TaskResult:
    """Result of a completed task."""
    task_id: str
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    execution_time: float = 0.0
    agent_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, str]:
        """Convert result to dictionary for serialization."""
        return {
            "task_id": self.task_id,
            "success": str(self.success),
            "result": json.dumps(self.result) if self.result else "",
            "error": self.error or "",
            "execution_time": str(self.execution_time),
            "agent_id": self.agent_id or "",
            "timestamp": self.timestamp.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "TaskResult":
        """Create result from dictionary."""
        return cls(
            task_id=data["task_id"],
            success=data["success"].lower() == "true",
            result=json.loads(data["result"]) if data["result"] else None,
            error=data["error"] or None,
            execution_time=float(data["execution_time"]),
            agent_id=data["agent_id"] or None,
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


@dataclass
class QueueConfig:
    """Configuration for message queue."""
    task_queue: str = "agix:tasks"
    result_queue: str = "agix:results"
    error_queue: str = "agix:errors"
    dead_letter_queue: str = "agix:dead-letter"
    consumer_group: str = "agix-workers"
    max_length: int = 10000
    block_timeout: int = 5000  # milliseconds
    claim_timeout: int = 60000  # milliseconds for claiming pending messages


class MessageQueue:
    """
    Redis Streams-based message queue for distributed task processing.
    
    Features:
    - Task queue with priority support
    - Result queue for collecting outputs
    - Error queue for failed tasks
    - Consumer groups for distributed processing
    - Dead letter queue for permanent failures
    - Automatic retry with configurable strategies
    """
    
    def __init__(
        self,
        redis_client: RedisClient,
        config: Optional[QueueConfig] = None,
        consumer_name: Optional[str] = None,
    ):
        """
        Initialize message queue.
        
        Args:
            redis_client: Connected Redis client.
            config: Queue configuration.
            consumer_name: Unique name for this consumer.
        """
        self.redis = redis_client
        self.config = config or QueueConfig()
        self.consumer_name = consumer_name or f"consumer-{uuid.uuid4().hex[:8]}"
        self._initialized = False
        self._processing_tasks: Dict[str, Task] = {}
    
    async def initialize(self) -> None:
        """Initialize the message queue and create consumer groups."""
        if self._initialized:
            return
        
        # Create consumer groups for each queue
        for queue in [self.config.task_queue, self.config.result_queue, self.config.error_queue]:
            try:
                await self.redis.xgroup_create(
                    queue,
                    self.config.consumer_group,
                    id="0",
                    mkstream=True,
                )
                logger.info(f"Created consumer group for {queue}")
            except Exception as e:
                # Group may already exist
                logger.debug(f"Consumer group may already exist for {queue}: {e}")
        
        self._initialized = True
        logger.info(f"Message queue initialized with consumer: {self.consumer_name}")
    
    # ==========================================================================
    # Task Queue Operations
    # ==========================================================================
    
    async def enqueue_task(
        self,
        task_type: str,
        payload: Dict[str, Any],
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: int = 300,
        max_retries: int = 3,
        parent_task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Add a task to the queue.
        
        Args:
            task_type: Type of task (e.g., "research", "code", "analyze").
            payload: Task payload with instructions.
            priority: Task priority level.
            timeout: Task timeout in seconds.
            max_retries: Maximum retry attempts.
            parent_task_id: ID of parent task if this is a subtask.
            metadata: Additional metadata.
            
        Returns:
            The created Task object.
        """
        await self.initialize()
        
        task = Task(
            id=str(uuid.uuid4()),
            type=task_type,
            payload=payload,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            parent_task_id=parent_task_id,
            metadata=metadata or {},
        )
        
        # Add to stream
        entry_id = await self.redis.xadd(
            self.config.task_queue,
            task.to_dict(),
            maxlen=self.config.max_length,
        )
        
        logger.info(f"Enqueued task {task.id} of type {task_type} with entry {entry_id}")
        return task
    
    async def enqueue_batch(
        self,
        tasks: List[Tuple[str, Dict[str, Any]]],
        priority: TaskPriority = TaskPriority.NORMAL,
        parent_task_id: Optional[str] = None,
    ) -> List[Task]:
        """
        Add multiple tasks to the queue.
        
        Args:
            tasks: List of (task_type, payload) tuples.
            priority: Priority for all tasks.
            parent_task_id: Parent task ID for all tasks.
            
        Returns:
            List of created Task objects.
        """
        created_tasks = []
        for task_type, payload in tasks:
            task = await self.enqueue_task(
                task_type=task_type,
                payload=payload,
                priority=priority,
                parent_task_id=parent_task_id,
            )
            created_tasks.append(task)
        return created_tasks
    
    async def dequeue_task(
        self,
        block: bool = True,
        count: int = 1,
    ) -> List[Tuple[str, Task]]:
        """
        Get tasks from the queue.
        
        Args:
            block: Whether to block waiting for tasks.
            count: Maximum number of tasks to retrieve.
            
        Returns:
            List of (entry_id, Task) tuples.
        """
        await self.initialize()
        
        block_timeout = self.config.block_timeout if block else None
        
        # Read from consumer group
        result = await self.redis.xreadgroup(
            self.config.consumer_group,
            self.consumer_name,
            {self.config.task_queue: ">"},
            count=count,
            block=block_timeout,
        )
        
        tasks = []
        if result:
            for stream_name, entries in result:
                for entry_id, fields in entries:
                    try:
                        task = Task.from_dict(fields)
                        task.status = TaskStatus.PROCESSING
                        task.updated_at = datetime.now()
                        self._processing_tasks[task.id] = task
                        tasks.append((entry_id, task))
                        logger.debug(f"Dequeued task {task.id}")
                    except Exception as e:
                        logger.error(f"Failed to parse task from entry {entry_id}: {e}")
        
        return tasks
    
    async def acknowledge_task(self, entry_id: str, task: Task) -> None:
        """
        Acknowledge a task as processed.
        
        Args:
            entry_id: Stream entry ID.
            task: The task that was processed.
        """
        await self.redis.xack(
            self.config.task_queue,
            self.config.consumer_group,
            entry_id,
        )
        
        if task.id in self._processing_tasks:
            del self._processing_tasks[task.id]
        
        logger.debug(f"Acknowledged task {task.id}")
    
    # ==========================================================================
    # Result Queue Operations
    # ==========================================================================
    
    async def publish_result(self, result: TaskResult) -> str:
        """
        Publish a task result.
        
        Args:
            result: The task result.
            
        Returns:
            Stream entry ID.
        """
        await self.initialize()
        
        entry_id = await self.redis.xadd(
            self.config.result_queue,
            result.to_dict(),
            maxlen=self.config.max_length,
        )
        
        logger.info(f"Published result for task {result.task_id}: success={result.success}")
        return entry_id
    
    async def get_results(
        self,
        task_ids: Optional[List[str]] = None,
        count: int = 100,
    ) -> List[TaskResult]:
        """
        Get task results.
        
        Args:
            task_ids: Filter by specific task IDs.
            count: Maximum results to retrieve.
            
        Returns:
            List of TaskResult objects.
        """
        await self.initialize()
        
        # Read from result stream
        entries = await self.redis.xrange(
            self.config.result_queue,
            count=count,
        )
        
        results = []
        for entry_id, fields in entries:
            try:
                result = TaskResult.from_dict(fields)
                if task_ids is None or result.task_id in task_ids:
                    results.append(result)
            except Exception as e:
                logger.error(f"Failed to parse result from entry {entry_id}: {e}")
        
        return results
    
    async def wait_for_result(
        self,
        task_id: str,
        timeout: float = 300.0,
        poll_interval: float = 0.5,
    ) -> Optional[TaskResult]:
        """
        Wait for a specific task result.
        
        Args:
            task_id: Task ID to wait for.
            timeout: Maximum wait time in seconds.
            poll_interval: Polling interval in seconds.
            
        Returns:
            TaskResult if found, None if timeout.
        """
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            results = await self.get_results(task_ids=[task_id], count=1000)
            for result in results:
                if result.task_id == task_id:
                    return result
            
            await asyncio.sleep(poll_interval)
        
        return None
    
    async def wait_for_results(
        self,
        task_ids: List[str],
        timeout: float = 300.0,
        poll_interval: float = 0.5,
    ) -> Dict[str, Optional[TaskResult]]:
        """
        Wait for multiple task results.
        
        Args:
            task_ids: Task IDs to wait for.
            timeout: Maximum wait time in seconds.
            poll_interval: Polling interval in seconds.
            
        Returns:
            Dict mapping task_id to TaskResult (or None if not found).
        """
        start_time = asyncio.get_event_loop().time()
        found_results: Dict[str, TaskResult] = {}
        remaining_ids = set(task_ids)
        
        while remaining_ids and asyncio.get_event_loop().time() - start_time < timeout:
            results = await self.get_results(task_ids=list(remaining_ids), count=1000)
            for result in results:
                if result.task_id in remaining_ids:
                    found_results[result.task_id] = result
                    remaining_ids.remove(result.task_id)
            
            if remaining_ids:
                await asyncio.sleep(poll_interval)
        
        # Return all results, with None for missing ones
        return {
            task_id: found_results.get(task_id)
            for task_id in task_ids
        }
    
    # ==========================================================================
    # Error Queue Operations
    # ==========================================================================
    
    async def publish_error(
        self,
        task: Task,
        error: str,
        should_retry: bool = True,
    ) -> Optional[str]:
        """
        Publish a task error.
        
        Args:
            task: The failed task.
            error: Error message.
            should_retry: Whether to retry the task.
            
        Returns:
            Stream entry ID if published, None if moved to dead letter.
        """
        await self.initialize()
        
        task.error = error
        task.status = TaskStatus.FAILED
        task.updated_at = datetime.now()
        
        if should_retry and task.retry_count < task.max_retries:
            # Retry the task
            task.retry_count += 1
            task.status = TaskStatus.RETRYING
            
            entry_id = await self.redis.xadd(
                self.config.error_queue,
                task.to_dict(),
                maxlen=self.config.max_length,
            )
            
            logger.warning(f"Task {task.id} failed, retry {task.retry_count}/{task.max_retries}: {error}")
            return entry_id
        else:
            # Move to dead letter queue
            await self._move_to_dead_letter(task, error)
            return None
    
    async def get_errors(self, count: int = 100) -> List[Task]:
        """
        Get failed tasks from error queue.
        
        Args:
            count: Maximum errors to retrieve.
            
        Returns:
            List of failed Task objects.
        """
        await self.initialize()
        
        entries = await self.redis.xrange(
            self.config.error_queue,
            count=count,
        )
        
        tasks = []
        for entry_id, fields in entries:
            try:
                task = Task.from_dict(fields)
                tasks.append(task)
            except Exception as e:
                logger.error(f"Failed to parse error from entry {entry_id}: {e}")
        
        return tasks
    
    async def retry_failed_task(self, task: Task) -> Task:
        """
        Retry a failed task by re-enqueueing it.
        
        Args:
            task: The task to retry.
            
        Returns:
            The re-enqueued task.
        """
        task.status = TaskStatus.PENDING
        task.error = None
        task.updated_at = datetime.now()
        
        await self.redis.xadd(
            self.config.task_queue,
            task.to_dict(),
            maxlen=self.config.max_length,
        )
        
        logger.info(f"Re-enqueued task {task.id} for retry")
        return task
    
    # ==========================================================================
    # Dead Letter Queue Operations
    # ==========================================================================
    
    async def _move_to_dead_letter(self, task: Task, error: str) -> str:
        """
        Move a permanently failed task to dead letter queue.
        
        Args:
            task: The failed task.
            error: Final error message.
            
        Returns:
            Stream entry ID.
        """
        task.status = TaskStatus.DEAD_LETTER
        task.error = error
        task.updated_at = datetime.now()
        
        entry_id = await self.redis.xadd(
            self.config.dead_letter_queue,
            task.to_dict(),
            maxlen=self.config.max_length,
        )
        
        logger.error(f"Task {task.id} moved to dead letter queue: {error}")
        return entry_id
    
    async def get_dead_letters(self, count: int = 100) -> List[Task]:
        """
        Get tasks from dead letter queue.
        
        Args:
            count: Maximum tasks to retrieve.
            
        Returns:
            List of dead letter Task objects.
        """
        entries = await self.redis.xrange(
            self.config.dead_letter_queue,
            count=count,
        )
        
        tasks = []
        for entry_id, fields in entries:
            try:
                task = Task.from_dict(fields)
                tasks.append(task)
            except Exception as e:
                logger.error(f"Failed to parse dead letter from entry {entry_id}: {e}")
        
        return tasks
    
    # ==========================================================================
    # Queue Management
    # ==========================================================================
    
    async def get_queue_length(self, queue: Optional[str] = None) -> int:
        """
        Get the length of a queue.
        
        Args:
            queue: Queue name. Defaults to task queue.
            
        Returns:
            Number of entries in the queue.
        """
        queue = queue or self.config.task_queue
        return await self.redis.xlen(queue)
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """
        Get statistics for all queues.
        
        Returns:
            Dictionary with queue statistics.
        """
        return {
            "task_queue_length": await self.get_queue_length(self.config.task_queue),
            "result_queue_length": await self.get_queue_length(self.config.result_queue),
            "error_queue_length": await self.get_queue_length(self.config.error_queue),
            "dead_letter_queue_length": await self.get_queue_length(self.config.dead_letter_queue),
            "processing_tasks": len(self._processing_tasks),
            "consumer_name": self.consumer_name,
        }
    
    async def clear_queue(self, queue: str) -> bool:
        """
        Clear all entries from a queue.
        
        Args:
            queue: Queue name to clear.
            
        Returns:
            True if successful.
        """
        await self.redis.delete(queue)
        logger.warning(f"Cleared queue: {queue}")
        return True
    
    async def claim_pending_tasks(
        self,
        min_idle_time: int = 60000,
        count: int = 10,
    ) -> List[Tuple[str, Task]]:
        """
        Claim pending tasks from other consumers that have timed out.
        
        Args:
            min_idle_time: Minimum idle time in milliseconds.
            count: Maximum tasks to claim.
            
        Returns:
            List of (entry_id, Task) tuples.
        """
        # This would use XAUTOCLAIM in Redis 6.2+
        # For now, we'll use XPENDING + XCLAIM
        # Implementation depends on Redis version
        logger.debug("Claiming pending tasks not fully implemented")
        return []


# =============================================================================
# Factory Function
# =============================================================================

async def create_message_queue(
    redis_config: Optional[Dict] = None,
    queue_config: Optional[Dict] = None,
    consumer_name: Optional[str] = None,
) -> MessageQueue:
    """
    Factory function to create a message queue.
    
    Args:
        redis_config: Redis configuration dictionary.
        queue_config: Queue configuration dictionary.
        consumer_name: Unique consumer name.
        
    Returns:
        Initialized MessageQueue instance.
    """
    redis_client = await create_redis_client(redis_config)
    
    config = QueueConfig()
    if queue_config:
        config.task_queue = queue_config.get("task_queue", config.task_queue)
        config.result_queue = queue_config.get("result_queue", config.result_queue)
        config.error_queue = queue_config.get("error_queue", config.error_queue)
        config.consumer_group = queue_config.get("consumer_group", config.consumer_group)
        config.max_length = queue_config.get("max_length", config.max_length)
        config.block_timeout = queue_config.get("block_timeout", config.block_timeout)
    
    queue = MessageQueue(redis_client, config, consumer_name)
    await queue.initialize()
    return queue
