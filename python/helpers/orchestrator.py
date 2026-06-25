from __future__ import annotations
"""
Orchestrator for AGIX Parallel Swarm

This module provides the central orchestration logic for:
- Task decomposition and dependency analysis
- Wave-based parallel execution
- Result aggregation and synthesis
- Challenger/Inspector quality assurance
- Error recovery coordination
"""

import asyncio
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from python.redis_client import RedisClient, create_redis_client
from python.helpers.message_queue import (
    MessageQueue, Task, TaskResult, TaskStatus, TaskPriority,
    create_message_queue
)
from python.helpers.shared_memory import (
    SharedMemoryManager, MemoryEntry, MemoryTier, MemoryType,
    create_shared_memory_manager
)
from python.helpers.agent_pool import (
    AgentPool, RetryConfig, RetryStrategy, WorkerProfile,
    RetryableError, MaxRetriesExceeded, create_agent_pool
)
from python.helpers.parallel_settings import (
    get_parallel_settings, is_parallel_enabled
)

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """Types of tasks that can be orchestrated."""
    RESEARCH = "research"
    CODE = "code"
    ANALYZE = "analyze"
    REVIEW = "review"
    CHALLENGE = "challenge"
    INSPECT = "inspect"
    SYNTHESIZE = "synthesize"


class ExecutionMode(Enum):
    """Execution modes for the orchestrator."""
    SEQUENTIAL = "sequential"  # Traditional sequential execution
    PARALLEL = "parallel"      # Full parallel execution
    WAVE = "wave"              # Wave-based parallel (respects dependencies)
    ADAPTIVE = "adaptive"      # Dynamically choose based on task


@dataclass
class SubTask:
    """Represents a decomposed subtask."""
    id: str
    parent_id: str
    task_type: TaskType
    description: str
    payload: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.NORMAL
    timeout: int = 300
    profile: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Execution state
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    assigned_agent: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def to_task(self) -> Task:
        """Convert to message queue Task."""
        return Task(
            id=self.id,
            type=self.task_type.value,
            payload={
                "description": self.description,
                **self.payload,
            },
            priority=self.priority,
            parent_task_id=self.parent_id,
            timeout=self.timeout,
            metadata={
                "profile": self.profile,
                "dependencies": self.dependencies,
                **self.metadata,
            },
        )


@dataclass
class ExecutionWave:
    """A wave of tasks that can be executed in parallel."""
    wave_number: int
    tasks: List[SubTask]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    @property
    def is_complete(self) -> bool:
        """Check if all tasks in wave are complete."""
        return all(
            t.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.DEAD_LETTER]
            for t in self.tasks
        )
    
    @property
    def success_count(self) -> int:
        """Count of successful tasks."""
        return sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
    
    @property
    def failure_count(self) -> int:
        """Count of failed tasks."""
        return sum(1 for t in self.tasks if t.status in [TaskStatus.FAILED, TaskStatus.DEAD_LETTER])


@dataclass
class OrchestratorConfig:
    """Configuration for the orchestrator."""
    # Execution settings
    mode: ExecutionMode = ExecutionMode.WAVE
    max_concurrent_tasks: int = 5
    max_waves: int = 10
    default_task_timeout: int = 300
    
    # Quality assurance
    enable_challenger: bool = True
    enable_inspector: bool = True
    challenge_threshold: float = 0.8  # Confidence threshold for challenging
    
    # Error handling
    max_retries: int = 3
    retry_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    
    # Result aggregation
    synthesis_enabled: bool = True
    min_results_for_synthesis: int = 2


class Orchestrator:
    """
    Central orchestrator for parallel swarm execution.
    
    Features:
    - Task decomposition into parallelizable subtasks
    - Dependency analysis and wave-based execution
    - Result aggregation and synthesis
    - Challenger/Inspector quality assurance
    - Error recovery and retry coordination
    """
    
    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        redis_client: Optional[RedisClient] = None,
        message_queue: Optional[MessageQueue] = None,
        shared_memory: Optional[SharedMemoryManager] = None,
        agent_pool: Optional[AgentPool] = None,
        agent_factory: Optional[Callable] = None,
    ):
        """
        Initialize the orchestrator.
        
        Args:
            config: Orchestrator configuration.
            redis_client: Redis client for coordination.
            message_queue: Message queue for task distribution.
            shared_memory: Shared memory manager.
            agent_pool: Pool of worker agents.
            agent_factory: Factory function to create agents.
        """
        self.config = config or OrchestratorConfig()
        self.redis_client = redis_client
        self.message_queue = message_queue
        self.shared_memory = shared_memory
        self.agent_pool = agent_pool
        self.agent_factory = agent_factory
        
        # Execution state
        self._current_task_id: Optional[str] = None
        self._subtasks: Dict[str, SubTask] = {}
        self._waves: List[ExecutionWave] = []
        self._results: Dict[str, Any] = {}
        
        # Metrics
        self._total_tasks_orchestrated = 0
        self._total_subtasks_created = 0
        self._total_waves_executed = 0
    
    async def initialize(self) -> None:
        """Initialize orchestrator components."""
        settings = get_parallel_settings()
        
        # Initialize Redis client if not provided
        if self.redis_client is None:
            redis_config = {
                "host": settings.redis.host,
                "port": settings.redis.port,
                "db": settings.redis.db,
                "password": settings.redis.password or None,
            }
            self.redis_client = await create_redis_client(redis_config)
        
        # Initialize message queue if not provided
        if self.message_queue is None:
            self.message_queue = await create_message_queue(
                redis_config={
                    "host": settings.redis.host,
                    "port": settings.redis.port,
                },
                queue_config={
                    "task_queue": settings.redis.streams.task_queue,
                    "result_queue": settings.redis.streams.result_queue,
                },
                consumer_name=f"orchestrator-{uuid.uuid4().hex[:8]}",
            )
        
        # Initialize shared memory if not provided
        if self.shared_memory is None:
            self.shared_memory = await create_shared_memory_manager(
                self.redis_client,
                agent_id="orchestrator",
                config={
                    "private": {"max_entries": 1000},
                    "shared": {"max_entries": 10000},
                },
            )
        
        # Initialize agent pool if not provided
        if self.agent_pool is None:
            pool_config = {
                "semaphore_limit": self.config.max_concurrent_tasks,
                "health_check_interval": settings.agent_pool.health_check_interval,
                "default_max_retries": self.config.max_retries,
            }
            self.agent_pool = create_agent_pool(pool_config, self.agent_factory)
        
        logger.info("Orchestrator initialized")
    
    async def shutdown(self) -> None:
        """Shutdown orchestrator and cleanup resources."""
        if self.agent_pool:
            await self.agent_pool.stop()
        
        if self.shared_memory:
            await self.shared_memory.stop_broadcast_listener()
        
        if self.redis_client:
            await self.redis_client.disconnect()
        
        logger.info("Orchestrator shutdown complete")
    
    # ==========================================================================
    # Task Decomposition
    # ==========================================================================
    
    async def decompose_task(
        self,
        task_description: str,
        task_type: TaskType = TaskType.RESEARCH,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[SubTask]:
        """
        Decompose a complex task into parallelizable subtasks.
        
        Args:
            task_description: Description of the main task.
            task_type: Type of task.
            context: Additional context for decomposition.
            
        Returns:
            List of SubTask objects.
        """
        self._current_task_id = str(uuid.uuid4())
        self._subtasks.clear()
        self._waves.clear()
        self._results.clear()
        
        # For now, use a simple decomposition strategy
        # In production, this would use LLM to intelligently decompose
        subtasks = await self._simple_decomposition(
            task_description, task_type, context or {}
        )
        
        # Register subtasks
        for subtask in subtasks:
            self._subtasks[subtask.id] = subtask
        
        self._total_subtasks_created += len(subtasks)
        
        logger.info(f"Decomposed task into {len(subtasks)} subtasks")
        return subtasks
    
    async def _simple_decomposition(
        self,
        description: str,
        task_type: TaskType,
        context: Dict[str, Any],
    ) -> List[SubTask]:
        """
        Simple task decomposition strategy.
        
        This is a placeholder - in production, use LLM for intelligent decomposition.
        """
        subtasks = []
        
        # Create main execution subtask
        main_task = SubTask(
            id=str(uuid.uuid4()),
            parent_id=self._current_task_id or "",
            task_type=task_type,
            description=description,
            payload={"context": context},
            priority=TaskPriority.NORMAL,
        )
        subtasks.append(main_task)
        
        # If challenger is enabled, add challenge task
        if self.config.enable_challenger:
            challenge_task = SubTask(
                id=str(uuid.uuid4()),
                parent_id=self._current_task_id or "",
                task_type=TaskType.CHALLENGE,
                description=f"Challenge and verify: {description}",
                payload={"original_task_id": main_task.id, "context": context},
                dependencies=[main_task.id],
                priority=TaskPriority.HIGH,
                profile="challenger",
            )
            subtasks.append(challenge_task)
        
        # If inspector is enabled, add inspection task
        if self.config.enable_inspector:
            inspect_task = SubTask(
                id=str(uuid.uuid4()),
                parent_id=self._current_task_id or "",
                task_type=TaskType.INSPECT,
                description=f"Inspect quality of: {description}",
                payload={"original_task_id": main_task.id, "context": context},
                dependencies=[main_task.id],
                priority=TaskPriority.HIGH,
                profile="inspector",
            )
            subtasks.append(inspect_task)
        
        return subtasks
    
    # ==========================================================================
    # Dependency Analysis
    # ==========================================================================
    
    def analyze_dependencies(self, subtasks: List[SubTask]) -> List[ExecutionWave]:
        """
        Analyze task dependencies and organize into execution waves.
        
        Args:
            subtasks: List of subtasks to analyze.
            
        Returns:
            List of ExecutionWave objects.
        """
        # Build dependency graph
        task_map = {t.id: t for t in subtasks}
        remaining = set(t.id for t in subtasks)
        completed = set()
        waves = []
        
        wave_number = 0
        while remaining and wave_number < self.config.max_waves:
            # Find tasks with all dependencies satisfied
            ready_tasks = []
            for task_id in remaining:
                task = task_map[task_id]
                if all(dep in completed for dep in task.dependencies):
                    ready_tasks.append(task)
            
            if not ready_tasks:
                # Circular dependency or missing dependency
                logger.warning(f"Cannot resolve dependencies for: {remaining}")
                break
            
            # Create wave
            wave = ExecutionWave(
                wave_number=wave_number,
                tasks=ready_tasks,
            )
            waves.append(wave)
            
            # Update tracking
            for task in ready_tasks:
                remaining.remove(task.id)
                completed.add(task.id)
            
            wave_number += 1
        
        self._waves = waves
        logger.info(f"Organized {len(subtasks)} tasks into {len(waves)} waves")
        return waves
    
    # ==========================================================================
    # Parallel Execution
    # ==========================================================================
    
    async def execute_parallel(
        self,
        subtasks: List[SubTask],
        executor: Callable[[SubTask], Any],
    ) -> Dict[str, Any]:
        """
        Execute subtasks in parallel using wave-based execution.
        
        Args:
            subtasks: List of subtasks to execute.
            executor: Function to execute each subtask.
            
        Returns:
            Dictionary mapping task_id to result.
        """
        # Analyze dependencies and create waves
        waves = self.analyze_dependencies(subtasks)
        
        results = {}
        
        for wave in waves:
            wave.started_at = datetime.now()
            logger.info(f"Executing wave {wave.wave_number} with {len(wave.tasks)} tasks")
            
            # Execute all tasks in wave concurrently
            wave_results = await self._execute_wave(wave, executor)
            
            # Collect results
            for task_id, result in wave_results.items():
                results[task_id] = result
                self._results[task_id] = result
                
                # Update subtask status
                subtask = self._subtasks.get(task_id)
                if subtask:
                    if isinstance(result, Exception):
                        subtask.status = TaskStatus.FAILED
                        subtask.error = str(result)
                    else:
                        subtask.status = TaskStatus.COMPLETED
                        subtask.result = result
                    subtask.completed_at = datetime.now()
            
            wave.completed_at = datetime.now()
            self._total_waves_executed += 1
            
            # Store wave results in shared memory
            if self.shared_memory:
                await self._store_wave_results(wave, wave_results)
        
        self._total_tasks_orchestrated += 1
        return results
    
    async def _execute_wave(
        self,
        wave: ExecutionWave,
        executor: Callable[[SubTask], Any],
    ) -> Dict[str, Any]:
        """Execute all tasks in a wave concurrently."""
        async def execute_task(subtask: SubTask) -> Tuple[str, Any]:
            subtask.started_at = datetime.now()
            subtask.status = TaskStatus.PROCESSING
            
            try:
                if self.agent_pool:
                    # Use agent pool for execution
                    result = await self.agent_pool.execute(
                        executor,
                        subtask,
                        profile_name=subtask.profile,
                        timeout=subtask.timeout,
                        task_id=subtask.id,
                    )
                else:
                    # Direct execution
                    result = await executor(subtask)
                
                return (subtask.id, result)
            except Exception as e:
                logger.error(f"Task {subtask.id} failed: {e}")
                return (subtask.id, e)
        
        # Execute all tasks concurrently with timeout protection
        # (Phase 3 hardening: replaces bare asyncio.gather to prevent infinite hangs)
        ORCHESTRATOR_WAVE_TIMEOUT = 600.0  # 10 minutes
        task_coros = [execute_task(subtask) for subtask in wave.tasks]
        async_tasks = [asyncio.ensure_future(t) for t in task_coros]
        results_list = []
        if async_tasks:
            done, pending = await asyncio.wait(
                async_tasks,
                timeout=ORCHESTRATOR_WAVE_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                logger.warning(
                    f"[ORCHESTRATOR] {len(pending)} wave tasks timed out after "
                    f"{ORCHESTRATOR_WAVE_TIMEOUT}s — cancelling"
                )
                for p in pending:
                    p.cancel()
                await asyncio.wait(pending, timeout=5.0)
            for t in done:
                try:
                    results_list.append(t.result())
                except Exception as e:
                    results_list.append(e)
        
        # Convert to dictionary
        return {task_id: result for task_id, result in results_list}
    
    async def _store_wave_results(
        self,
        wave: ExecutionWave,
        results: Dict[str, Any],
    ) -> None:
        """Store wave results in shared memory."""
        if not self.shared_memory:
            return
        
        for task_id, result in results.items():
            if isinstance(result, Exception):
                continue
            
            subtask = self._subtasks.get(task_id)
            if subtask:
                await self.shared_memory.store_shared(
                    content=str(result),
                    memory_type=MemoryType.TASK_RESULT,
                    task_id=task_id,
                    metadata={
                        "wave": wave.wave_number,
                        "task_type": subtask.task_type.value,
                        "parent_id": subtask.parent_id,
                    },
                )
                logger.info(f"COLLABORATION: Task result {task_id} shared in memory for other agents")
    
    # ==========================================================================
    # Result Aggregation
    # ==========================================================================
    
    async def aggregate_results(
        self,
        results: Dict[str, Any],
        aggregator: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Any:
        """
        Aggregate results from parallel execution.
        
        Args:
            results: Dictionary of task results.
            aggregator: Custom aggregation function.
            
        Returns:
            Aggregated result.
        """
        # Filter out errors
        successful_results = {
            k: v for k, v in results.items()
            if not isinstance(v, Exception)
        }
        
        if not successful_results:
            raise RuntimeError("All subtasks failed")
        
        # Use custom aggregator if provided
        if aggregator:
            return await aggregator(successful_results)
        
        # Default aggregation: combine results
        return await self._default_aggregation(successful_results)
    
    async def _default_aggregation(
        self,
        results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Default result aggregation strategy."""
        # Separate by task type
        main_results = []
        challenge_results = []
        inspect_results = []
        
        for task_id, result in results.items():
            subtask = self._subtasks.get(task_id)
            if subtask:
                if subtask.task_type == TaskType.CHALLENGE:
                    challenge_results.append(result)
                elif subtask.task_type == TaskType.INSPECT:
                    inspect_results.append(result)
                else:
                    main_results.append(result)
        
        return {
            "main_results": main_results,
            "challenge_results": challenge_results,
            "inspection_results": inspect_results,
            "task_count": len(results),
            "success_rate": len(results) / len(self._subtasks) if self._subtasks else 0,
        }
    
    # ==========================================================================
    # Challenger/Inspector Quality Assurance
    # ==========================================================================
    
    async def challenge_result(
        self,
        result: Any,
        original_task: SubTask,
        challenger_executor: Callable[[SubTask, Any], Any],
    ) -> Dict[str, Any]:
        """
        Challenge a result using a challenger agent.
        
        The challenger acts as an adversarial reviewer, attempting to find:
        - Logical inconsistencies
        - Missing information
        - Incorrect assumptions
        - Alternative interpretations
        
        Args:
            result: Result to challenge.
            original_task: Original task that produced the result.
            challenger_executor: Function to execute challenge.
            
        Returns:
            Challenge assessment with verdict and confidence.
        """
        challenge_task = SubTask(
            id=str(uuid.uuid4()),
            parent_id=original_task.parent_id,
            task_type=TaskType.CHALLENGE,
            description=f"Challenge result of: {original_task.description}",
            payload={
                "original_result": result,
                "original_task_id": original_task.id,
                "challenge_criteria": [
                    "logical_consistency",
                    "completeness",
                    "accuracy",
                    "assumptions",
                    "alternatives",
                ],
            },
            priority=TaskPriority.HIGH,
            profile="challenger",
        )
        
        assessment = await challenger_executor(challenge_task, result)
        
        # Parse and structure the assessment
        structured_assessment = self._structure_challenge_assessment(assessment)
        
        # Store challenge result in shared memory
        if self.shared_memory:
            await self.shared_memory.store_shared(
                content=str(structured_assessment),
                memory_type=MemoryType.DECISION,
                task_id=challenge_task.id,
                metadata={
                    "type": "challenge_assessment",
                    "original_task_id": original_task.id,
                    "verdict": structured_assessment.get("verdict", "unknown"),
                },
            )
        
        return {
            "original_task_id": original_task.id,
            "challenge_task_id": challenge_task.id,
            "assessment": structured_assessment,
            "challenged_at": datetime.now().isoformat(),
        }
    
    def _structure_challenge_assessment(self, raw_assessment: Any) -> Dict[str, Any]:
        """Structure a raw challenge assessment into standard format."""
        if isinstance(raw_assessment, dict):
            return {
                "verdict": raw_assessment.get("verdict", "needs_review"),
                "confidence": raw_assessment.get("confidence", 0.5),
                "issues_found": raw_assessment.get("issues", []),
                "suggestions": raw_assessment.get("suggestions", []),
                "alternative_approaches": raw_assessment.get("alternatives", []),
                "risk_level": raw_assessment.get("risk_level", "medium"),
                "raw": raw_assessment,
            }
        
        # Parse string assessment
        return {
            "verdict": "needs_review",
            "confidence": 0.5,
            "issues_found": [],
            "suggestions": [],
            "alternative_approaches": [],
            "risk_level": "medium",
            "raw": str(raw_assessment),
        }
    
    async def inspect_result(
        self,
        result: Any,
        original_task: SubTask,
        inspector_executor: Callable[[SubTask, Any], Any],
    ) -> Dict[str, Any]:
        """
        Inspect a result for quality using an inspector agent.
        
        The inspector evaluates quality across multiple dimensions:
        - Completeness: Does it fully address the task?
        - Accuracy: Is the information correct?
        - Clarity: Is it well-organized and understandable?
        - Relevance: Does it stay on topic?
        - Actionability: Can it be used effectively?
        
        Args:
            result: Result to inspect.
            original_task: Original task that produced the result.
            inspector_executor: Function to execute inspection.
            
        Returns:
            Inspection report with quality scores.
        """
        inspect_task = SubTask(
            id=str(uuid.uuid4()),
            parent_id=original_task.parent_id,
            task_type=TaskType.INSPECT,
            description=f"Inspect quality of: {original_task.description}",
            payload={
                "original_result": result,
                "original_task_id": original_task.id,
                "quality_dimensions": [
                    "completeness",
                    "accuracy",
                    "clarity",
                    "relevance",
                    "actionability",
                ],
            },
            priority=TaskPriority.HIGH,
            profile="inspector",
        )
        
        report = await inspector_executor(inspect_task, result)
        
        # Parse and structure the report
        structured_report = self._structure_inspection_report(report)
        
        # Store inspection result in shared memory
        if self.shared_memory:
            await self.shared_memory.store_shared(
                content=str(structured_report),
                memory_type=MemoryType.INSIGHT,
                task_id=inspect_task.id,
                metadata={
                    "type": "inspection_report",
                    "original_task_id": original_task.id,
                    "overall_score": structured_report.get("overall_score", 0),
                },
            )
        
        return {
            "original_task_id": original_task.id,
            "inspect_task_id": inspect_task.id,
            "report": structured_report,
            "inspected_at": datetime.now().isoformat(),
        }
    
    def _structure_inspection_report(self, raw_report: Any) -> Dict[str, Any]:
        """Structure a raw inspection report into standard format."""
        if isinstance(raw_report, dict):
            scores = raw_report.get("scores", {})
            return {
                "overall_score": raw_report.get("overall_score", self._calculate_overall_score(scores)),
                "dimension_scores": {
                    "completeness": scores.get("completeness", 0.5),
                    "accuracy": scores.get("accuracy", 0.5),
                    "clarity": scores.get("clarity", 0.5),
                    "relevance": scores.get("relevance", 0.5),
                    "actionability": scores.get("actionability", 0.5),
                },
                "strengths": raw_report.get("strengths", []),
                "weaknesses": raw_report.get("weaknesses", []),
                "recommendations": raw_report.get("recommendations", []),
                "quality_grade": self._score_to_grade(raw_report.get("overall_score", 0.5)),
                "raw": raw_report,
            }
        
        # Parse string report
        return {
            "overall_score": 0.5,
            "dimension_scores": {
                "completeness": 0.5,
                "accuracy": 0.5,
                "clarity": 0.5,
                "relevance": 0.5,
                "actionability": 0.5,
            },
            "strengths": [],
            "weaknesses": [],
            "recommendations": [],
            "quality_grade": "C",
            "raw": str(raw_report),
        }
    
    def _calculate_overall_score(self, scores: Dict[str, float]) -> float:
        """Calculate weighted overall score from dimension scores."""
        weights = {
            "completeness": 0.25,
            "accuracy": 0.30,
            "clarity": 0.15,
            "relevance": 0.15,
            "actionability": 0.15,
        }
        
        total = 0.0
        weight_sum = 0.0
        
        for dim, weight in weights.items():
            if dim in scores:
                total += scores[dim] * weight
                weight_sum += weight
        
        return total / weight_sum if weight_sum > 0 else 0.5
    
    def _score_to_grade(self, score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 0.9:
            return "A"
        elif score >= 0.8:
            return "B"
        elif score >= 0.7:
            return "C"
        elif score >= 0.6:
            return "D"
        else:
            return "F"
    
    async def run_quality_assurance(
        self,
        result: Any,
        original_task: SubTask,
        challenger_executor: Callable[[SubTask, Any], Any],
        inspector_executor: Callable[[SubTask, Any], Any],
    ) -> Dict[str, Any]:
        """
        Run full quality assurance pipeline (challenge + inspect).
        
        Args:
            result: Result to evaluate.
            original_task: Original task that produced the result.
            challenger_executor: Function to execute challenge.
            inspector_executor: Function to execute inspection.
            
        Returns:
            Combined QA report with verdict and recommendations.
        """
        # Run challenge and inspection in parallel
        challenge_task = self.challenge_result(result, original_task, challenger_executor)
        inspect_task = self.inspect_result(result, original_task, inspector_executor)
        
        # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
        # to prevent LLM-bound challenge/inspect from hanging the QA pipeline
        challenge_future = asyncio.ensure_future(challenge_task)
        inspect_future = asyncio.ensure_future(inspect_task)
        futures = [challenge_future, inspect_future]
        done, pending = await asyncio.wait(futures, timeout=300.0)
        if pending:
            import logging
            logging.getLogger(__name__).warning(
                f"QA pipeline: {len(pending)}/2 tasks timed out after 300s, cancelling"
            )
            for p in pending:
                p.cancel()
            await asyncio.wait(pending, timeout=5.0)
        
        # Extract results safely
        challenge_result = (
            challenge_future.result() if challenge_future in done and not challenge_future.cancelled()
            else asyncio.TimeoutError("Challenge timed out after 300s")
        )
        inspect_result = (
            inspect_future.result() if inspect_future in done and not inspect_future.cancelled()
            else asyncio.TimeoutError("Inspection timed out after 300s")
        )
        
        # Handle errors - ensure we have Dict types
        if isinstance(challenge_result, BaseException):
            challenge_dict: Dict[str, Any] = {"error": str(challenge_result)}
        else:
            challenge_dict = dict(challenge_result) if challenge_result else {}
            
        if isinstance(inspect_result, BaseException):
            inspect_dict: Dict[str, Any] = {"error": str(inspect_result)}
        else:
            inspect_dict = dict(inspect_result) if inspect_result else {}
        
        # Combine results
        qa_report = self._combine_qa_results(challenge_dict, inspect_dict)
        
        return {
            "original_task_id": original_task.id,
            "challenge_result": challenge_result,
            "inspection_result": inspect_result,
            "combined_report": qa_report,
            "qa_completed_at": datetime.now().isoformat(),
        }
    
    def _combine_qa_results(
        self,
        challenge_result: Dict[str, Any],
        inspect_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Combine challenge and inspection results into unified report."""
        # Extract key metrics
        challenge_assessment = challenge_result.get("assessment", {})
        inspection_report = inspect_result.get("report", {})
        
        challenge_confidence = challenge_assessment.get("confidence", 0.5)
        inspection_score = inspection_report.get("overall_score", 0.5)
        
        # Calculate combined quality score
        combined_score = (challenge_confidence + inspection_score) / 2
        
        # Determine overall verdict
        challenge_verdict = challenge_assessment.get("verdict", "unknown")
        issues_found = challenge_assessment.get("issues_found", [])
        
        if combined_score >= 0.8 and challenge_verdict in ["approved", "passed"]:
            overall_verdict = "approved"
        elif combined_score < 0.5 or len(issues_found) > 3:
            overall_verdict = "rejected"
        else:
            overall_verdict = "needs_revision"
        
        # Compile recommendations
        all_recommendations = []
        all_recommendations.extend(challenge_assessment.get("suggestions", []))
        all_recommendations.extend(inspection_report.get("recommendations", []))
        
        return {
            "overall_verdict": overall_verdict,
            "combined_score": combined_score,
            "challenge_confidence": challenge_confidence,
            "inspection_score": inspection_score,
            "quality_grade": self._score_to_grade(combined_score),
            "issues_count": len(issues_found),
            "recommendations": all_recommendations[:10],  # Top 10 recommendations
            "requires_human_review": overall_verdict == "needs_revision" and combined_score < 0.6,
        }
    
    async def consensus_check(
        self,
        results: List[Any],
        task: SubTask,
        consensus_threshold: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Check for consensus among multiple results.
        
        Useful when multiple agents work on the same task and
        we need to determine if they agree.
        
        Args:
            results: List of results to compare.
            task: The task that produced these results.
            consensus_threshold: Minimum agreement ratio for consensus.
            
        Returns:
            Consensus report with agreement metrics.
        """
        if len(results) < 2:
            return {
                "has_consensus": True,
                "agreement_ratio": 1.0,
                "consensus_result": results[0] if results else None,
                "dissenting_results": [],
            }
        
        # Simple consensus: compare string representations
        # In production, use semantic similarity
        result_strings = [str(r) for r in results]
        
        # Count occurrences of each unique result
        result_counts: Dict[str, int] = {}
        for rs in result_strings:
            result_counts[rs] = result_counts.get(rs, 0) + 1
        
        # Find majority result
        majority_result = max(result_counts.items(), key=lambda x: x[1])
        agreement_ratio = majority_result[1] / len(results)
        
        # Identify dissenting results
        dissenting = [
            r for r, rs in zip(results, result_strings)
            if rs != majority_result[0]
        ]
        
        has_consensus = agreement_ratio >= consensus_threshold
        
        return {
            "has_consensus": has_consensus,
            "agreement_ratio": agreement_ratio,
            "consensus_result": results[result_strings.index(majority_result[0])] if has_consensus else None,
            "dissenting_results": dissenting,
            "unique_results_count": len(result_counts),
            "total_results": len(results),
        }
    
    # ==========================================================================
    # High-Level API
    # ==========================================================================
    
    async def orchestrate(
        self,
        task_description: str,
        task_type: TaskType = TaskType.RESEARCH,
        executor: Optional[Callable[[SubTask], Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        High-level orchestration API.
        
        Decomposes task, executes in parallel, and aggregates results.
        
        Args:
            task_description: Description of the task.
            task_type: Type of task.
            executor: Function to execute each subtask.
            context: Additional context.
            
        Returns:
            Orchestration result with aggregated outputs.
        """
        # Default executor if not provided
        if executor is None:
            async def default_executor(subtask: SubTask) -> str:
                return f"Executed: {subtask.description}"
            executor = default_executor
        
        # Decompose task
        subtasks = await self.decompose_task(task_description, task_type, context)
        
        # Execute in parallel
        results = await self.execute_parallel(subtasks, executor)
        
        # Aggregate results
        aggregated = await self.aggregate_results(results)
        
        return {
            "task_id": self._current_task_id,
            "task_description": task_description,
            "subtask_count": len(subtasks),
            "wave_count": len(self._waves),
            "results": aggregated,
            "execution_summary": self.get_execution_summary(),
        }
    
    # ==========================================================================
    # Statistics and Monitoring
    # ==========================================================================
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get summary of current execution."""
        total_tasks = len(self._subtasks)
        completed = sum(1 for t in self._subtasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self._subtasks.values() if t.status in [TaskStatus.FAILED, TaskStatus.DEAD_LETTER])
        
        return {
            "total_subtasks": total_tasks,
            "completed": completed,
            "failed": failed,
            "pending": total_tasks - completed - failed,
            "success_rate": completed / total_tasks if total_tasks > 0 else 0,
            "waves_executed": len([w for w in self._waves if w.completed_at]),
            "total_waves": len(self._waves),
        }
    
    def get_orchestrator_stats(self) -> Dict[str, Any]:
        """Get overall orchestrator statistics."""
        return {
            "total_tasks_orchestrated": self._total_tasks_orchestrated,
            "total_subtasks_created": self._total_subtasks_created,
            "total_waves_executed": self._total_waves_executed,
            "config": {
                "mode": self.config.mode.value,
                "max_concurrent_tasks": self.config.max_concurrent_tasks,
                "max_waves": self.config.max_waves,
                "challenger_enabled": self.config.enable_challenger,
                "inspector_enabled": self.config.enable_inspector,
            },
        }


# =============================================================================
# Factory Function
# =============================================================================

async def create_orchestrator(
    config: Optional[Dict] = None,
    agent_factory: Optional[Callable] = None,
) -> Orchestrator:
    """
    Factory function to create and initialize an orchestrator.
    
    Args:
        config: Configuration dictionary.
        agent_factory: Factory function for creating agents.
        
    Returns:
        Initialized Orchestrator instance.
    """
    orchestrator_config = OrchestratorConfig()
    
    if config:
        if "mode" in config:
            orchestrator_config.mode = ExecutionMode(config["mode"])
        orchestrator_config.max_concurrent_tasks = config.get(
            "max_concurrent_tasks", orchestrator_config.max_concurrent_tasks
        )
        orchestrator_config.max_waves = config.get(
            "max_waves", orchestrator_config.max_waves
        )
        orchestrator_config.enable_challenger = config.get(
            "enable_challenger", orchestrator_config.enable_challenger
        )
        orchestrator_config.enable_inspector = config.get(
            "enable_inspector", orchestrator_config.enable_inspector
        )
        orchestrator_config.max_retries = config.get(
            "max_retries", orchestrator_config.max_retries
        )
    
    orchestrator = Orchestrator(
        config=orchestrator_config,
        agent_factory=agent_factory,
    )
    
    await orchestrator.initialize()
    return orchestrator
