from __future__ import annotations
"""
Agent Pool for AGIX Parallel Swarm

This module provides a semaphore-controlled agent pool with:
- Concurrent execution management
- Worker health monitoring
- Configurable retry strategies
- Profile-based agent configuration
"""

import asyncio
import logging
import random
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union
from python.helpers.priority_semaphore import PrioritySemaphore, Priority
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

T = TypeVar('T')


class WorkerState(Enum):
    """State of a worker agent."""
    IDLE = "idle"
    BUSY = "busy"
    UNHEALTHY = "unhealthy"
    TERMINATED = "terminated"


class RetryStrategy(Enum):
    """Available retry strategies."""
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    AGGRESSIVE = "aggressive"
    CONSERVATIVE = "conservative"
    ADAPTIVE = "adaptive"


class RetryableError(Exception):
    """Error that should trigger a retry."""
    pass


class NonRetryableError(Exception):
    """Error that should not be retried."""
    pass


class MaxRetriesExceeded(Exception):
    """All retry attempts exhausted."""
    def __init__(self, message: str, last_error: Optional[Exception] = None):
        super().__init__(message)
        self.last_error = last_error


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: bool = True
    
    # Linear strategy
    increment: float = 2.0
    
    # Adaptive strategy
    success_decrease: float = 0.8
    failure_increase: float = 1.5
    min_delay: float = 0.5


@dataclass
class WorkerProfile:
    """Configuration profile for a worker agent."""
    name: str
    model: str = "default"
    reasoning_model: Optional[str] = None
    execution_model: Optional[str] = None
    vision_model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 16384
    system_prompt_additions: str = ""
    tools_enabled: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerInfo:
    """Information about a worker agent."""
    id: str
    profile: WorkerProfile
    state: WorkerState = WorkerState.IDLE
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    tasks_completed: int = 0
    tasks_failed: int = 0
    consecutive_failures: int = 0
    current_task_id: Optional[str] = None
    agent_instance: Optional[Any] = None
    
    @property
    def is_healthy(self) -> bool:
        """Check if worker is healthy."""
        return self.state != WorkerState.UNHEALTHY and self.state != WorkerState.TERMINATED
    
    @property
    def is_available(self) -> bool:
        """Check if worker is available for tasks."""
        return self.state == WorkerState.IDLE and self.is_healthy
    
    def mark_busy(self, task_id: str) -> None:
        """Mark worker as busy with a task."""
        self.state = WorkerState.BUSY
        self.current_task_id = task_id
        self.last_active = datetime.now()
    
    def mark_idle(self) -> None:
        """Mark worker as idle."""
        self.state = WorkerState.IDLE
        self.current_task_id = None
        self.last_active = datetime.now()
    
    def record_success(self) -> None:
        """Record a successful task completion."""
        self.tasks_completed += 1
        self.consecutive_failures = 0
        self.mark_idle()
    
    def record_failure(self) -> None:
        """Record a task failure."""
        self.tasks_failed += 1
        self.consecutive_failures += 1
        self.mark_idle()
    
    def mark_unhealthy(self) -> None:
        """Mark worker as unhealthy."""
        self.state = WorkerState.UNHEALTHY
        self.current_task_id = None


@dataclass
class PoolConfig:
    """Configuration for the agent pool."""
    # Concurrency
    semaphore_limit: int = 5
    
    # Health monitoring
    health_check_interval: int = 30
    max_consecutive_failures: int = 3
    
    # Retry defaults
    default_retry_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    default_max_retries: int = 3
    
    # Worker management
    worker_timeout: int = 300
    max_workers_per_profile: int = 3
    
    # Profiles
    profiles: Dict[str, WorkerProfile] = field(default_factory=dict)


class RetryHandler:
    """Handles retry logic with configurable strategies."""
    
    def __init__(self, config: RetryConfig):
        """
        Initialize retry handler.
        
        Args:
            config: Retry configuration.
        """
        self.config = config
        self._adaptive_delay = config.base_delay
    
    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay before next retry.
        
        Args:
            attempt: Current attempt number (0-indexed).
            
        Returns:
            Delay in seconds.
        """
        if self.config.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.config.base_delay * (self.config.multiplier ** attempt)
        elif self.config.strategy == RetryStrategy.LINEAR:
            delay = self.config.base_delay + (self.config.increment * attempt)
        elif self.config.strategy == RetryStrategy.AGGRESSIVE:
            delay = self.config.base_delay * (1.5 ** attempt)
            delay = min(delay, 10.0)  # Cap at 10 seconds
        elif self.config.strategy == RetryStrategy.CONSERVATIVE:
            delay = self.config.base_delay * (3.0 ** attempt)
        elif self.config.strategy == RetryStrategy.ADAPTIVE:
            delay = self._adaptive_delay
        else:
            delay = self.config.base_delay
        
        # Apply max delay cap
        delay = min(delay, self.config.max_delay)
        
        # Apply jitter
        if self.config.jitter:
            jitter = random.uniform(0, delay * 0.1)
            delay += jitter
        
        return delay
    
    def record_success(self) -> None:
        """Record a successful attempt (for adaptive strategy)."""
        if self.config.strategy == RetryStrategy.ADAPTIVE:
            self._adaptive_delay = max(
                self.config.min_delay,
                self._adaptive_delay * self.config.success_decrease
            )
    
    def record_failure(self) -> None:
        """Record a failed attempt (for adaptive strategy)."""
        if self.config.strategy == RetryStrategy.ADAPTIVE:
            self._adaptive_delay = min(
                self.config.max_delay,
                self._adaptive_delay * self.config.failure_increase
            )
    
    def should_retry(self, attempt: int, error: Exception) -> bool:
        """
        Determine if a retry should be attempted.
        
        Args:
            attempt: Current attempt number.
            error: The error that occurred.
            
        Returns:
            True if should retry, False otherwise.
        """
        # Check max attempts
        if attempt >= self.config.max_attempts:
            return False
        
        # Check error type
        if isinstance(error, NonRetryableError):
            return False
        
        if isinstance(error, RetryableError):
            return True
        
        # Default: retry on most errors
        return True


class AgentPool:
    """
    Semaphore-controlled pool of worker agents.
    
    Features:
    - Concurrent execution with semaphore control
    - Worker health monitoring
    - Configurable retry strategies
    - Profile-based agent configuration
    - Automatic worker recovery
    """
    
    def __init__(
        self,
        config: Optional[PoolConfig] = None,
        agent_factory: Optional[Callable[[WorkerProfile], Any]] = None,
    ):
        """
        Initialize agent pool.
        
        Args:
            config: Pool configuration.
            agent_factory: Factory function to create agent instances.
        """
        self.config = config or PoolConfig()
        self.agent_factory = agent_factory
        
        # Semaphore for concurrency control
        self._semaphore = PrioritySemaphore(self.config.semaphore_limit)
        
        # Worker tracking
        self._workers: Dict[str, WorkerInfo] = {}
        self._workers_by_profile: Dict[str, List[str]] = {}
        
        # Health monitoring
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Metrics
        self._total_tasks_submitted = 0
        self._total_tasks_completed = 0
        self._total_tasks_failed = 0
        
        # Initialize default profiles
        self._init_default_profiles()
    
    def _init_default_profiles(self) -> None:
        """Initialize default worker profiles."""
        if not self.config.profiles:
            self.config.profiles = {
                "default": WorkerProfile(name="default"),
                "researcher": WorkerProfile(
                    name="researcher",
                    temperature=0.3,
                    max_tokens=16384,
                ),
                "code": WorkerProfile(
                    name="code",
                    temperature=0.2,
                    max_tokens=16384,
                ),
                "creative": WorkerProfile(
                    name="creative",
                    temperature=0.9,
                    max_tokens=16384,
                ),
            }
    
    async def start(self) -> None:
        """Start the agent pool and health monitoring."""
        if self._running:
            return
        
        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info(f"Agent pool started with semaphore limit {self.config.semaphore_limit}")
    
    async def stop(self) -> None:
        """Stop the agent pool and cleanup resources."""
        self._running = False
        
        # Cancel health check task
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                logger.debug("[AgentPool] Health check task cancelled during stop")
        
        # Terminate all workers
        for worker_id in list(self._workers.keys()):
            await self._terminate_worker(worker_id)
        
        logger.info("Agent pool stopped")
    
    # ==========================================================================
    # Worker Management
    # ==========================================================================
    
    async def get_worker(
        self,
        profile_name: str = "default",
        create_if_needed: bool = True,
    ) -> Optional[WorkerInfo]:
        """
        Get an available worker with the specified profile.
        
        Args:
            profile_name: Name of the worker profile.
            create_if_needed: Create new worker if none available.
            
        Returns:
            WorkerInfo if available, None otherwise.
        """
        # Check for available worker with this profile
        if profile_name in self._workers_by_profile:
            for worker_id in self._workers_by_profile[profile_name]:
                worker = self._workers.get(worker_id)
                if worker and worker.is_available:
                    return worker
        
        # Create new worker if allowed
        if create_if_needed:
            profile = self.config.profiles.get(profile_name)
            if profile:
                # Check if we can create more workers for this profile
                current_count = len(self._workers_by_profile.get(profile_name, []))
                if current_count < self.config.max_workers_per_profile:
                    return await self._create_worker(profile)
        
        return None
    
    async def _create_worker(self, profile: WorkerProfile) -> WorkerInfo:
        """
        Create a new worker agent.
        
        Args:
            profile: Worker profile configuration.
            
        Returns:
            The created WorkerInfo.
        """
        worker_id = f"worker-{profile.name}-{uuid.uuid4().hex[:8]}"
        
        # Create agent instance if factory provided
        agent_instance = None
        if self.agent_factory:
            try:
                agent_instance = self.agent_factory(profile)
            except Exception as e:
                logger.error(f"Failed to create agent instance: {e}")
        
        worker = WorkerInfo(
            id=worker_id,
            profile=profile,
            agent_instance=agent_instance,
        )
        
        # Register worker
        self._workers[worker_id] = worker
        if profile.name not in self._workers_by_profile:
            self._workers_by_profile[profile.name] = []
        self._workers_by_profile[profile.name].append(worker_id)
        
        logger.info(f"Created worker {worker_id} with profile {profile.name}")
        return worker
    
    async def _terminate_worker(self, worker_id: str) -> None:
        """
        Terminate a worker agent.
        
        Args:
            worker_id: ID of worker to terminate.
        """
        worker = self._workers.get(worker_id)
        if not worker:
            return
        
        worker.state = WorkerState.TERMINATED
        
        # Remove from tracking
        del self._workers[worker_id]
        if worker.profile.name in self._workers_by_profile:
            if worker_id in self._workers_by_profile[worker.profile.name]:
                self._workers_by_profile[worker.profile.name].remove(worker_id)
        
        logger.info(f"Terminated worker {worker_id}")
    
    # ==========================================================================
    # Task Execution
    # ==========================================================================
    
    async def execute(
        self,
        task_func: Callable[..., T],
        *args,
        profile_name: str = "default",
        priority: Priority = Priority.NORMAL,
        retry_config: Optional[RetryConfig] = None,
        timeout: Optional[int] = None,
        task_id: Optional[str] = None,
        **kwargs,
    ) -> T:
        """
        Execute a task with semaphore control and retry logic.
        
        Args:
            task_func: Async function to execute.
            *args: Positional arguments for task_func.
            profile_name: Worker profile to use.
            retry_config: Retry configuration.
            timeout: Task timeout in seconds.
            task_id: Optional task identifier.
            **kwargs: Keyword arguments for task_func.
            
        Returns:
            Result of task_func.
            
        Raises:
            MaxRetriesExceeded: If all retries exhausted.
            asyncio.TimeoutError: If task times out.
        """
        task_id = task_id or str(uuid.uuid4())
        timeout = timeout or self.config.worker_timeout
        retry_config = retry_config or RetryConfig(
            strategy=self.config.default_retry_strategy,
            max_attempts=self.config.default_max_retries,
        )
        
        retry_handler = RetryHandler(retry_config)
        self._total_tasks_submitted += 1
        
        last_error: Optional[Exception] = None
        
        for attempt in range(retry_config.max_attempts):
            try:
                # Acquire semaphore with priority
                async with self._semaphore.priority(priority):
                    # Get worker
                    worker = await self.get_worker(profile_name)
                    if not worker:
                        raise RetryableError(f"No worker available for profile {profile_name}")
                    
                    worker.mark_busy(task_id)
                    
                    try:
                        # Execute with timeout
                        result = await asyncio.wait_for(
                            task_func(*args, **kwargs),
                            timeout=timeout,
                        )
                        
                        # Record success
                        worker.record_success()
                        retry_handler.record_success()
                        self._total_tasks_completed += 1
                        
                        return result
                        
                    except asyncio.TimeoutError:
                        worker.record_failure()
                        raise RetryableError(f"Task {task_id} timed out after {timeout}s")
                    except Exception as e:
                        worker.record_failure()
                        
                        # Check if worker should be marked unhealthy
                        if worker.consecutive_failures >= self.config.max_consecutive_failures:
                            worker.mark_unhealthy()
                            logger.warning(f"Worker {worker.id} marked unhealthy after {worker.consecutive_failures} failures")
                        
                        raise
                        
            except Exception as e:
                last_error = e
                retry_handler.record_failure()
                
                if not retry_handler.should_retry(attempt, e):
                    break
                
                # Wait before retry
                delay = retry_handler.get_delay(attempt)
                logger.warning(f"Task {task_id} failed (attempt {attempt + 1}/{retry_config.max_attempts}), retrying in {delay:.2f}s: {e}")
                await asyncio.sleep(delay)
        
        # All retries exhausted
        self._total_tasks_failed += 1
        raise MaxRetriesExceeded(
            f"Task {task_id} failed after {retry_config.max_attempts} attempts",
            last_error,
        )
    
    async def execute_batch(
        self,
        tasks: List[Tuple[Callable[..., Any], tuple, dict]],
        profile_name: str = "default",
        priority: Priority = Priority.NORMAL,
        retry_config: Optional[RetryConfig] = None,
        timeout: Optional[int] = None,
        return_exceptions: bool = False,
    ) -> List[Any]:
        """
        Execute multiple tasks in parallel.
        
        Args:
            tasks: List of (func, args, kwargs) tuples.
            profile_name: Worker profile to use.
            retry_config: Retry configuration.
            timeout: Task timeout in seconds.
            return_exceptions: If True, return exceptions instead of raising.
            
        Returns:
            List of results (or exceptions if return_exceptions=True).
        """
        coroutines = [
            self.execute(
                func,
                *args,
                profile_name=profile_name,
                priority=priority,
                retry_config=retry_config,
                timeout=timeout,
                **kwargs,
            )
            for func, args, kwargs in tasks
        ]
        
        # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
        POOL_BATCH_TIMEOUT = 600.0  # 10 minutes
        batch_tasks = [asyncio.ensure_future(c) for c in coroutines]
        results = [None] * len(batch_tasks)
        if batch_tasks:
            done, pending = await asyncio.wait(
                batch_tasks,
                timeout=POOL_BATCH_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                logger.warning(
                    f"[POOL] {len(pending)} batch tasks timed out after "
                    f"{POOL_BATCH_TIMEOUT}s — cancelling"
                )
                for p in pending:
                    p.cancel()
                await asyncio.wait(pending, timeout=5.0)
            for i, bt in enumerate(batch_tasks):
                if bt in done:
                    try:
                        results[i] = bt.result()
                    except Exception as e:
                        if return_exceptions:
                            results[i] = e
                        else:
                            raise
                elif bt in pending:
                    err = asyncio.TimeoutError(f"Batch task {i} timed out")
                    if return_exceptions:
                        results[i] = err
                    else:
                        raise err
        return results
    
    # ==========================================================================
    # Health Monitoring
    # ==========================================================================
    
    async def _health_check_loop(self) -> None:
        """Background task for periodic health checks."""
        while self._running:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await self._perform_health_checks()
            except asyncio.CancelledError:
                logger.debug("[AgentPool] Health check loop cancelled — shutting down gracefully")
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _perform_health_checks(self) -> None:
        """Perform health checks on all workers."""
        unhealthy_workers = []
        
        for worker_id, worker in self._workers.items():
            # Check for stuck workers
            if worker.state == WorkerState.BUSY:
                busy_duration = (datetime.now() - worker.last_active).total_seconds()
                if busy_duration > self.config.worker_timeout:
                    logger.warning(f"Worker {worker_id} appears stuck (busy for {busy_duration:.0f}s)")
                    worker.mark_unhealthy()
                    unhealthy_workers.append(worker_id)
            
            # Check consecutive failures
            if worker.consecutive_failures >= self.config.max_consecutive_failures:
                if worker.state != WorkerState.UNHEALTHY:
                    worker.mark_unhealthy()
                    unhealthy_workers.append(worker_id)
        
        # Attempt to recover unhealthy workers
        for worker_id in unhealthy_workers:
            await self._recover_worker(worker_id)
    
    async def _recover_worker(self, worker_id: str) -> Optional[WorkerInfo]:
        """
        Attempt to recover an unhealthy worker.
        
        Args:
            worker_id: ID of worker to recover.
            
        Returns:
            New WorkerInfo if recovered, None otherwise.
        """
        worker = self._workers.get(worker_id)
        if not worker:
            return None
        
        profile = worker.profile
        
        # Terminate old worker
        await self._terminate_worker(worker_id)
        
        # Create replacement
        try:
            new_worker = await self._create_worker(profile)
            logger.info(f"Recovered worker {worker_id} -> {new_worker.id}")
            return new_worker
        except Exception as e:
            logger.error(f"Failed to recover worker {worker_id}: {e}")
            return None
    
    # ==========================================================================
    # Statistics and Management
    # ==========================================================================
    
    def get_pool_status(self) -> Dict[str, Any]:
        """
        Get current pool status.
        
        Returns:
            Dictionary with pool statistics.
        """
        workers_by_state = {state.value: 0 for state in WorkerState}
        for worker in self._workers.values():
            workers_by_state[worker.state.value] += 1
        
        return {
            "semaphore_limit": self.config.semaphore_limit,
            "semaphore_available": self._semaphore.available_units,
            "semaphore_queues": self._semaphore.get_queue_depths(),
            "total_workers": len(self._workers),
            "workers_by_state": workers_by_state,
            "workers_by_profile": {
                profile: len(workers)
                for profile, workers in self._workers_by_profile.items()
            },
            "total_tasks_submitted": self._total_tasks_submitted,
            "total_tasks_completed": self._total_tasks_completed,
            "total_tasks_failed": self._total_tasks_failed,
            "success_rate": (
                self._total_tasks_completed / self._total_tasks_submitted
                if self._total_tasks_submitted > 0 else 0.0
            ),
        }
    
    def get_worker_stats(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """
        Get statistics for a specific worker.
        
        Args:
            worker_id: Worker ID.
            
        Returns:
            Worker statistics or None if not found.
        """
        worker = self._workers.get(worker_id)
        if not worker:
            return None
        
        return {
            "id": worker.id,
            "profile": worker.profile.name,
            "state": worker.state.value,
            "created_at": worker.created_at.isoformat(),
            "last_active": worker.last_active.isoformat(),
            "tasks_completed": worker.tasks_completed,
            "tasks_failed": worker.tasks_failed,
            "consecutive_failures": worker.consecutive_failures,
            "is_healthy": worker.is_healthy,
            "is_available": worker.is_available,
        }
    
    def list_workers(self, profile_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all workers.
        
        Args:
            profile_name: Filter by profile name.
            
        Returns:
            List of worker statistics.
        """
        workers = []
        for worker_id, worker in self._workers.items():
            if profile_name is None or worker.profile.name == profile_name:
                stats = self.get_worker_stats(worker_id)
                if stats:
                    workers.append(stats)
        return workers
    
    async def scale_workers(self, profile_name: str, count: int) -> int:
        """
        Scale workers for a profile to a specific count.
        
        Args:
            profile_name: Profile to scale.
            count: Target worker count.
            
        Returns:
            Actual worker count after scaling.
        """
        profile = self.config.profiles.get(profile_name)
        if not profile:
            logger.error(f"Unknown profile: {profile_name}")
            return 0
        
        current_workers = self._workers_by_profile.get(profile_name, [])
        current_count = len(current_workers)
        
        if count > current_count:
            # Scale up
            for _ in range(count - current_count):
                await self._create_worker(profile)
        elif count < current_count:
            # Scale down (remove idle workers first)
            workers_to_remove = []
            for worker_id in current_workers:
                worker = self._workers.get(worker_id)
                if worker and worker.is_available:
                    workers_to_remove.append(worker_id)
                    if len(workers_to_remove) >= current_count - count:
                        break
            
            for worker_id in workers_to_remove:
                await self._terminate_worker(worker_id)
        
        return len(self._workers_by_profile.get(profile_name, []))


# =============================================================================
# Factory Function
# =============================================================================

def create_agent_pool(
    config: Optional[Dict] = None,
    agent_factory: Optional[Callable[[WorkerProfile], Any]] = None,
) -> AgentPool:
    """
    Factory function to create an agent pool.
    
    Args:
        config: Configuration dictionary.
        agent_factory: Factory function for creating agents.
        
    Returns:
        Configured AgentPool instance.
    """
    pool_config = PoolConfig()
    
    if config:
        pool_config.semaphore_limit = config.get("semaphore_limit", pool_config.semaphore_limit)
        pool_config.health_check_interval = config.get("health_check_interval", pool_config.health_check_interval)
        pool_config.max_consecutive_failures = config.get("max_consecutive_failures", pool_config.max_consecutive_failures)
        pool_config.worker_timeout = config.get("worker_timeout", 300)
        pool_config.max_workers_per_profile = config.get("max_workers_per_profile", pool_config.max_workers_per_profile)
        
        # Parse retry strategy
        strategy_name = config.get("default_retry_strategy", "exponential")
        try:
            pool_config.default_retry_strategy = RetryStrategy(strategy_name)
        except ValueError:
            pool_config.default_retry_strategy = RetryStrategy.EXPONENTIAL
        
        pool_config.default_max_retries = config.get("default_max_retries", pool_config.default_max_retries)
        
        # Parse profiles
        profiles_config = config.get("profiles", {})
        for name, profile_data in profiles_config.items():
            pool_config.profiles[name] = WorkerProfile(
                name=name,
                model=profile_data.get("model", "default"),
                temperature=profile_data.get("temperature", 0.7),
                max_tokens=profile_data.get("max_tokens", 16384),
            )
    
    return AgentPool(pool_config, agent_factory)
