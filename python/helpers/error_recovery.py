from __future__ import annotations
"""
Error Recovery Module for Parallel Swarm Execution

Provides comprehensive error handling, retry strategies, and recovery mechanisms
for distributed agent execution. Implements multiple retry patterns with
configurable backoff strategies and circuit breaker integration.

Key Features:
- Multiple retry strategies (exponential, linear, aggressive, conservative, adaptive)
- Error classification and categorization
- Recovery action recommendations
- Dead letter queue handling
- Error aggregation and reporting
- Integration with circuit breaker pattern
"""

import asyncio
import logging
import random
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Generic, Awaitable
import uuid

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors for classification."""
    TRANSIENT = "transient"           # Temporary, likely to succeed on retry
    PERMANENT = "permanent"           # Will not succeed on retry
    RESOURCE = "resource"             # Resource exhaustion (memory, connections)
    TIMEOUT = "timeout"               # Operation timed out
    VALIDATION = "validation"         # Input validation failed
    DEPENDENCY = "dependency"         # External dependency failed
    CONFIGURATION = "configuration"   # Configuration error
    UNKNOWN = "unknown"               # Unclassified error


class ErrorSeverity(Enum):
    """Severity levels for errors."""
    LOW = "low"           # Minor issue, can continue
    MEDIUM = "medium"     # Significant issue, may need attention
    HIGH = "high"         # Serious issue, likely needs intervention
    CRITICAL = "critical" # System-threatening, immediate action needed


class RecoveryAction(Enum):
    """Recommended recovery actions."""
    RETRY = "retry"                   # Retry the operation
    RETRY_WITH_BACKOFF = "retry_with_backoff"  # Retry with delay
    SKIP = "skip"                     # Skip this task, continue others
    ABORT = "abort"                   # Abort the entire batch
    ESCALATE = "escalate"             # Escalate to human/supervisor
    FALLBACK = "fallback"             # Use fallback mechanism
    DEAD_LETTER = "dead_letter"       # Move to dead letter queue
    CIRCUIT_BREAK = "circuit_break"   # Trigger circuit breaker


class RetryStrategy(Enum):
    """Retry backoff strategies."""
    EXPONENTIAL = "exponential"       # 2^n * base_delay
    LINEAR = "linear"                 # n * base_delay
    AGGRESSIVE = "aggressive"         # Minimal delays, many retries
    CONSERVATIVE = "conservative"     # Long delays, few retries
    ADAPTIVE = "adaptive"             # Adjusts based on error patterns
    FIBONACCI = "fibonacci"           # Fibonacci sequence delays
    DECORRELATED_JITTER = "decorrelated_jitter"  # AWS-style jitter


@dataclass
class ErrorContext:
    """Context information about an error occurrence."""
    error_id: str
    timestamp: datetime
    error_type: str
    error_message: str
    stack_trace: Optional[str]
    category: ErrorCategory
    severity: ErrorSeverity
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    operation: Optional[str] = None
    attempt_number: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_id": self.error_id,
            "timestamp": self.timestamp.isoformat(),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "stack_trace": self.stack_trace,
            "category": self.category.value,
            "severity": self.severity.value,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "operation": self.operation,
            "attempt_number": self.attempt_number,
            "metadata": self.metadata,
        }


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    jitter_factor: float = 0.1
    retry_on: List[ErrorCategory] = field(default_factory=lambda: [
        ErrorCategory.TRANSIENT,
        ErrorCategory.TIMEOUT,
        ErrorCategory.RESOURCE,
    ])
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "max_retries": self.max_retries,
            "base_delay": self.base_delay,
            "max_delay": self.max_delay,
            "jitter": self.jitter,
            "jitter_factor": self.jitter_factor,
            "retry_on": [c.value for c in self.retry_on],
        }


@dataclass
class RecoveryResult:
    """Result of a recovery attempt."""
    success: bool
    action_taken: RecoveryAction
    attempts: int
    final_error: Optional[ErrorContext] = None
    result: Any = None
    duration: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action_taken": self.action_taken.value,
            "attempts": self.attempts,
            "final_error": self.final_error.to_dict() if self.final_error else None,
            "duration": self.duration,
        }


class ErrorClassifier:
    """Classifies errors into categories and severity levels."""
    
    # Error type patterns for classification
    TRANSIENT_PATTERNS = [
        "timeout", "connection", "temporary", "unavailable",
        "rate limit", "throttl", "retry", "busy", "overload",
        "503", "504", "429", "EAGAIN", "ECONNRESET",
    ]
    
    RESOURCE_PATTERNS = [
        "memory", "disk", "quota", "limit", "exhausted",
        "out of", "insufficient", "capacity", "full",
    ]
    
    VALIDATION_PATTERNS = [
        "invalid", "validation", "required", "missing",
        "format", "type error", "value error", "schema",
    ]
    
    CONFIGURATION_PATTERNS = [
        "config", "setting", "environment", "not found",
        "undefined", "not configured", "missing key",
    ]
    
    def classify(self, error: Exception) -> tuple[ErrorCategory, ErrorSeverity]:
        """Classify an error into category and severity."""
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Check patterns
        if self._matches_patterns(error_str, error_type, self.TRANSIENT_PATTERNS):
            return ErrorCategory.TRANSIENT, ErrorSeverity.MEDIUM
        
        if self._matches_patterns(error_str, error_type, self.RESOURCE_PATTERNS):
            return ErrorCategory.RESOURCE, ErrorSeverity.HIGH
        
        if self._matches_patterns(error_str, error_type, self.VALIDATION_PATTERNS):
            return ErrorCategory.VALIDATION, ErrorSeverity.LOW
        
        if self._matches_patterns(error_str, error_type, self.CONFIGURATION_PATTERNS):
            return ErrorCategory.CONFIGURATION, ErrorSeverity.HIGH
        
        # Check specific exception types
        if isinstance(error, asyncio.TimeoutError):
            return ErrorCategory.TIMEOUT, ErrorSeverity.MEDIUM
        
        if isinstance(error, (ConnectionError, OSError)):
            return ErrorCategory.TRANSIENT, ErrorSeverity.MEDIUM
        
        if isinstance(error, (ValueError, TypeError)):
            return ErrorCategory.VALIDATION, ErrorSeverity.LOW
        
        if isinstance(error, MemoryError):
            return ErrorCategory.RESOURCE, ErrorSeverity.CRITICAL
        
        # Default classification
        return ErrorCategory.UNKNOWN, ErrorSeverity.MEDIUM
    
    def _matches_patterns(
        self,
        error_str: str,
        error_type: str,
        patterns: List[str]
    ) -> bool:
        """Check if error matches any pattern."""
        combined = f"{error_str} {error_type}"
        return any(pattern in combined for pattern in patterns)
    
    def recommend_action(
        self,
        category: ErrorCategory,
        severity: ErrorSeverity,
        attempt: int,
        max_retries: int
    ) -> RecoveryAction:
        """Recommend a recovery action based on error classification."""
        # Critical errors should escalate
        if severity == ErrorSeverity.CRITICAL:
            return RecoveryAction.ESCALATE
        
        # Permanent errors should not retry
        if category == ErrorCategory.PERMANENT:
            return RecoveryAction.DEAD_LETTER
        
        # Validation errors should skip
        if category == ErrorCategory.VALIDATION:
            return RecoveryAction.SKIP
        
        # Configuration errors should abort
        if category == ErrorCategory.CONFIGURATION:
            return RecoveryAction.ABORT
        
        # Check if retries exhausted
        if attempt >= max_retries:
            if severity == ErrorSeverity.HIGH:
                return RecoveryAction.ESCALATE
            return RecoveryAction.DEAD_LETTER
        
        # Transient and timeout errors should retry
        if category in [ErrorCategory.TRANSIENT, ErrorCategory.TIMEOUT]:
            return RecoveryAction.RETRY_WITH_BACKOFF
        
        # Resource errors might need circuit breaker
        if category == ErrorCategory.RESOURCE:
            if attempt > max_retries // 2:
                return RecoveryAction.CIRCUIT_BREAK
            return RecoveryAction.RETRY_WITH_BACKOFF
        
        # Default to retry with backoff
        return RecoveryAction.RETRY_WITH_BACKOFF


class BackoffCalculator:
    """Calculates retry delays based on strategy."""
    
    def __init__(self, config: RetryConfig):
        self.config = config
        self._fibonacci_cache = [0, 1]
        self._last_delay = config.base_delay
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number."""
        strategy = self.config.strategy
        base = self.config.base_delay
        max_delay = self.config.max_delay
        
        if strategy == RetryStrategy.EXPONENTIAL:
            delay = base * (2 ** attempt)
        
        elif strategy == RetryStrategy.LINEAR:
            delay = base * (attempt + 1)
        
        elif strategy == RetryStrategy.AGGRESSIVE:
            delay = base * 0.5  # Minimal delay
        
        elif strategy == RetryStrategy.CONSERVATIVE:
            delay = base * (3 ** attempt)  # Slower growth
        
        elif strategy == RetryStrategy.FIBONACCI:
            delay = base * self._get_fibonacci(attempt + 2)
        
        elif strategy == RetryStrategy.DECORRELATED_JITTER:
            # AWS-style decorrelated jitter
            delay = min(max_delay, random.uniform(base, self._last_delay * 3))
            self._last_delay = delay
            return delay
        
        elif strategy == RetryStrategy.ADAPTIVE:
            # Start aggressive, become conservative
            if attempt < 2:
                delay = base * (attempt + 1)
            else:
                delay = base * (2 ** (attempt - 1))
        
        else:
            delay = base * (2 ** attempt)
        
        # Apply max delay cap
        delay = min(delay, max_delay)
        
        # Apply jitter if enabled
        if self.config.jitter:
            jitter_range = delay * self.config.jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)
    
    def _get_fibonacci(self, n: int) -> int:
        """Get nth Fibonacci number with caching."""
        while len(self._fibonacci_cache) <= n:
            self._fibonacci_cache.append(
                self._fibonacci_cache[-1] + self._fibonacci_cache[-2]
            )
        return self._fibonacci_cache[n]


T = TypeVar('T')


class RetryHandler(Generic[T]):
    """
    Handles retry logic for async operations with configurable strategies.
    
    Usage:
        handler = RetryHandler(config)
        result = await handler.execute(async_operation, *args, **kwargs)
    """
    
    def __init__(
        self,
        config: Optional[RetryConfig] = None,
        classifier: Optional[ErrorClassifier] = None,
        on_retry: Optional[Callable[[ErrorContext, int], Awaitable[None]]] = None,
        on_failure: Optional[Callable[[ErrorContext], Awaitable[None]]] = None,
    ):
        self.config = config or RetryConfig()
        self.classifier = classifier or ErrorClassifier()
        self.backoff = BackoffCalculator(self.config)
        self.on_retry = on_retry
        self.on_failure = on_failure
        self.error_history: List[ErrorContext] = []
    
    async def execute(
        self,
        operation: Callable[..., Awaitable[T]],
        *args,
        **kwargs
    ) -> RecoveryResult:
        """
        Execute an operation with retry logic.
        
        Args:
            operation: Async callable to execute
            *args: Positional arguments for operation
            **kwargs: Keyword arguments for operation
        
        Returns:
            RecoveryResult with success status and result/error
        """
        start_time = time.time()
        attempt = 0
        last_error: Optional[ErrorContext] = None
        
        while attempt <= self.config.max_retries:
            try:
                result = await operation(*args, **kwargs)
                return RecoveryResult(
                    success=True,
                    action_taken=RecoveryAction.RETRY if attempt > 0 else RecoveryAction.SKIP,
                    attempts=attempt + 1,
                    result=result,
                    duration=time.time() - start_time,
                )
            
            except Exception as e:
                # Create error context
                category, severity = self.classifier.classify(e)
                error_ctx = ErrorContext(
                    error_id=str(uuid.uuid4())[:8],
                    timestamp=datetime.now(timezone.utc),
                    error_type=type(e).__name__,
                    error_message=str(e),
                    stack_trace=traceback.format_exc(),
                    category=category,
                    severity=severity,
                    attempt_number=attempt + 1,
                    metadata={"args": str(args)[:200], "kwargs": str(kwargs)[:200]},
                )
                
                self.error_history.append(error_ctx)
                last_error = error_ctx
                
                # Get recommended action
                action = self.classifier.recommend_action(
                    category, severity, attempt + 1, self.config.max_retries
                )
                
                # Check if we should retry
                if category not in self.config.retry_on:
                    action = RecoveryAction.DEAD_LETTER
                
                if action in [RecoveryAction.ABORT, RecoveryAction.DEAD_LETTER,
                              RecoveryAction.ESCALATE, RecoveryAction.SKIP]:
                    if self.on_failure:
                        await self.on_failure(error_ctx)
                    
                    return RecoveryResult(
                        success=False,
                        action_taken=action,
                        attempts=attempt + 1,
                        final_error=error_ctx,
                        duration=time.time() - start_time,
                    )
                
                # Calculate delay and retry
                if attempt < self.config.max_retries:
                    delay = self.backoff.calculate_delay(attempt)
                    
                    if self.on_retry:
                        await self.on_retry(error_ctx, attempt + 1)
                    
                    logger.info(
                        f"Retry attempt {attempt + 1}/{self.config.max_retries} "
                        f"after {delay:.2f}s delay. Error: {error_ctx.error_message}"
                    )
                    
                    await asyncio.sleep(delay)
                
                attempt += 1
        
        # All retries exhausted
        if self.on_failure and last_error:
            await self.on_failure(last_error)
        
        return RecoveryResult(
            success=False,
            action_taken=RecoveryAction.DEAD_LETTER,
            attempts=attempt,
            final_error=last_error,
            duration=time.time() - start_time,
        )


@dataclass
class DeadLetterEntry:
    """Entry in the dead letter queue."""
    entry_id: str
    timestamp: datetime
    error_context: ErrorContext
    original_task: Dict[str, Any]
    retry_count: int
    last_retry: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "error_context": self.error_context.to_dict(),
            "original_task": self.original_task,
            "retry_count": self.retry_count,
            "last_retry": self.last_retry.isoformat() if self.last_retry else None,
        }


class DeadLetterQueue:
    """
    Queue for failed tasks that cannot be recovered automatically.
    
    Provides storage, inspection, and manual retry capabilities for
    tasks that have exhausted automatic recovery options.
    """
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._queue: List[DeadLetterEntry] = []
        self._lock = asyncio.Lock()
    
    async def add(
        self,
        error_context: ErrorContext,
        original_task: Dict[str, Any],
        retry_count: int = 0
    ) -> DeadLetterEntry:
        """Add a failed task to the dead letter queue."""
        async with self._lock:
            entry = DeadLetterEntry(
                entry_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now(timezone.utc),
                error_context=error_context,
                original_task=original_task,
                retry_count=retry_count,
            )
            
            self._queue.append(entry)
            
            # Trim if over max size (remove oldest)
            if len(self._queue) > self.max_size:
                self._queue = self._queue[-self.max_size:]
            
            logger.warning(
                f"Task added to dead letter queue: {entry.entry_id} "
                f"Error: {error_context.error_message}"
            )
            
            return entry
    
    async def get(self, entry_id: str) -> Optional[DeadLetterEntry]:
        """Get a specific entry by ID."""
        async with self._lock:
            for entry in self._queue:
                if entry.entry_id == entry_id:
                    return entry
            return None
    
    async def remove(self, entry_id: str) -> bool:
        """Remove an entry from the queue."""
        async with self._lock:
            for i, entry in enumerate(self._queue):
                if entry.entry_id == entry_id:
                    self._queue.pop(i)
                    return True
            return False
    
    async def list_entries(
        self,
        category: Optional[ErrorCategory] = None,
        limit: int = 100
    ) -> List[DeadLetterEntry]:
        """List entries, optionally filtered by category."""
        async with self._lock:
            entries = self._queue
            
            if category:
                entries = [
                    e for e in entries
                    if e.error_context.category == category
                ]
            
            return entries[-limit:]
    
    async def retry_entry(
        self,
        entry_id: str,
        operation: Callable[..., Awaitable[Any]]
    ) -> Optional[RecoveryResult]:
        """Manually retry a dead letter entry."""
        entry = await self.get(entry_id)
        if not entry:
            return None
        
        entry.retry_count += 1
        entry.last_retry = datetime.now(timezone.utc)
        
        try:
            result = await operation(**entry.original_task)
            await self.remove(entry_id)
            return RecoveryResult(
                success=True,
                action_taken=RecoveryAction.RETRY,
                attempts=entry.retry_count,
                result=result,
            )
        except Exception as e:
            category, severity = ErrorClassifier().classify(e)
            entry.error_context = ErrorContext(
                error_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now(timezone.utc),
                error_type=type(e).__name__,
                error_message=str(e),
                stack_trace=traceback.format_exc(),
                category=category,
                severity=severity,
                attempt_number=entry.retry_count,
            )
            return RecoveryResult(
                success=False,
                action_taken=RecoveryAction.DEAD_LETTER,
                attempts=entry.retry_count,
                final_error=entry.error_context,
            )
    
    @property
    def size(self) -> int:
        """Current queue size."""
        return len(self._queue)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        category_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {}
        
        for entry in self._queue:
            cat = entry.error_context.category.value
            sev = entry.error_context.severity.value
            category_counts[cat] = category_counts.get(cat, 0) + 1
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        
        return {
            "total_entries": len(self._queue),
            "max_size": self.max_size,
            "by_category": category_counts,
            "by_severity": severity_counts,
        }


class ErrorAggregator:
    """
    Aggregates and analyzes errors across the system.
    
    Provides insights into error patterns, trends, and recommendations
    for system improvements.
    """
    
    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self._errors: List[ErrorContext] = []
        self._lock = asyncio.Lock()
    
    async def record(self, error: ErrorContext) -> None:
        """Record an error occurrence."""
        async with self._lock:
            self._errors.append(error)
            
            # Trim to window size
            if len(self._errors) > self.window_size:
                self._errors = self._errors[-self.window_size:]
    
    async def get_summary(self) -> Dict[str, Any]:
        """Get error summary statistics."""
        async with self._lock:
            if not self._errors:
                return {"total": 0, "by_category": {}, "by_severity": {}}
            
            category_counts: Dict[str, int] = {}
            severity_counts: Dict[str, int] = {}
            type_counts: Dict[str, int] = {}
            
            for error in self._errors:
                cat = error.category.value
                sev = error.severity.value
                typ = error.error_type
                
                category_counts[cat] = category_counts.get(cat, 0) + 1
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
                type_counts[typ] = type_counts.get(typ, 0) + 1
            
            # Find most common errors
            top_types = sorted(
                type_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            
            return {
                "total": len(self._errors),
                "by_category": category_counts,
                "by_severity": severity_counts,
                "top_error_types": dict(top_types),
                "window_size": self.window_size,
            }
    
    async def get_error_rate(self, window_seconds: float = 60.0) -> float:
        """Calculate error rate over time window."""
        async with self._lock:
            if not self._errors:
                return 0.0
            
            cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
            recent = [
                e for e in self._errors
                if e.timestamp.timestamp() > cutoff
            ]
            
            return len(recent) / window_seconds
    
    async def get_recommendations(self) -> List[str]:
        """Generate recommendations based on error patterns."""
        summary = await self.get_summary()
        recommendations = []
        
        # Check for high transient error rate
        transient_count = summary["by_category"].get("transient", 0)
        if transient_count > summary["total"] * 0.5:
            recommendations.append(
                "High transient error rate detected. Consider increasing retry delays "
                "or implementing circuit breaker pattern."
            )
        
        # Check for resource errors
        resource_count = summary["by_category"].get("resource", 0)
        if resource_count > 10:
            recommendations.append(
                "Resource exhaustion errors detected. Consider scaling resources "
                "or implementing rate limiting."
            )
        
        # Check for validation errors
        validation_count = summary["by_category"].get("validation", 0)
        if validation_count > summary["total"] * 0.3:
            recommendations.append(
                "High validation error rate. Review input validation and "
                "improve error messages for users."
            )
        
        # Check for critical errors
        critical_count = summary["by_severity"].get("critical", 0)
        if critical_count > 0:
            recommendations.append(
                f"{critical_count} critical errors detected. Immediate investigation required."
            )
        
        return recommendations


# Factory functions

def create_retry_handler(
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs
) -> RetryHandler:
    """Create a retry handler with specified configuration."""
    config = RetryConfig(
        strategy=strategy,
        max_retries=max_retries,
        base_delay=base_delay,
        **kwargs
    )
    return RetryHandler(config)


def create_dead_letter_queue(max_size: int = 1000) -> DeadLetterQueue:
    """Create a dead letter queue."""
    return DeadLetterQueue(max_size=max_size)


def create_error_aggregator(window_size: int = 1000) -> ErrorAggregator:
    """Create an error aggregator."""
    return ErrorAggregator(window_size=window_size)


# Convenience decorators

def with_retry(
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
    max_retries: int = 3,
    base_delay: float = 1.0,
):
    """
    Decorator to add retry logic to async functions.
    
    Usage:
        @with_retry(strategy=RetryStrategy.EXPONENTIAL, max_retries=3)
        async def my_operation():
            ...
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[RecoveryResult]]:
        async def wrapper(*args, **kwargs) -> RecoveryResult:
            handler = create_retry_handler(
                strategy=strategy,
                max_retries=max_retries,
                base_delay=base_delay,
            )
            return await handler.execute(func, *args, **kwargs)
        return wrapper
    return decorator
