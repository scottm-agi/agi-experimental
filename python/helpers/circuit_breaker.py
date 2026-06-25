from __future__ import annotations
"""
Circuit Breaker Pattern for Parallel Swarm Execution

Implements the circuit breaker pattern to prevent cascading failures
in distributed agent execution. Provides automatic failure detection,
recovery testing, and graceful degradation.

Key Features:
- Three-state circuit (CLOSED, OPEN, HALF_OPEN)
- Configurable failure thresholds
- Automatic recovery testing
- Integration with observer mesh for monitoring
- Multiple circuit breaker instances for different services
"""

import asyncio
import logging
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar, Generic, Awaitable
import uuid

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """States of the circuit breaker."""
    CLOSED = "closed"       # Normal operation, requests pass through
    OPEN = "open"           # Circuit tripped, requests fail fast
    HALF_OPEN = "half_open" # Testing recovery, limited requests allowed


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior with adaptive backoff."""
    failure_threshold: int = 5          # Failures before opening circuit
    success_threshold: int = 3          # Successes in half-open to close
    timeout: float = 30.0               # Base timeout in seconds before trying half-open
    half_open_max_calls: int = 3        # Max concurrent calls in half-open
    excluded_exceptions: List[type] = field(default_factory=list)  # Don't count these
    
    # Adaptive backoff settings
    use_exponential_backoff: bool = True   # Enable exponential backoff on consecutive failures
    min_timeout: float = 5.0               # Minimum timeout (floor)
    max_timeout: float = 300.0             # Maximum timeout (ceiling) 
    backoff_multiplier: float = 2.0        # Multiplier for each consecutive failure
    jitter_factor: float = 0.2             # Random jitter (0.0 = none, 0.5 = ±50%)
    
    def calculate_timeout(self, consecutive_failures: int) -> float:
        """Calculate adaptive timeout with exponential backoff and jitter.
        
        Args:
            consecutive_failures: Number of consecutive failures
            
        Returns:
            Timeout in seconds, with exponential backoff and random jitter
        """
        import random
        
        if not self.use_exponential_backoff or consecutive_failures <= 1:
            base_timeout = self.timeout
        else:
            # Exponential backoff: base * multiplier^(failures-1)
            base_timeout = self.timeout * (self.backoff_multiplier ** (consecutive_failures - 1))
        
        # Apply floor/ceiling
        base_timeout = max(self.min_timeout, min(self.max_timeout, base_timeout))
        
        # Apply jitter (random variation to prevent thundering herd)
        if self.jitter_factor > 0:
            jitter_range = base_timeout * self.jitter_factor
            jitter = random.uniform(-jitter_range, jitter_range)
            base_timeout = max(self.min_timeout, base_timeout + jitter)
        
        return base_timeout
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_threshold": self.failure_threshold,
            "success_threshold": self.success_threshold,
            "timeout": self.timeout,
            "half_open_max_calls": self.half_open_max_calls,
            "excluded_exceptions": [e.__name__ for e in self.excluded_exceptions],
            "use_exponential_backoff": self.use_exponential_backoff,
            "min_timeout": self.min_timeout,
            "max_timeout": self.max_timeout,
            "backoff_multiplier": self.backoff_multiplier,
            "jitter_factor": self.jitter_factor,
        }


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    state_changes: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    last_state_change: Optional[datetime] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "rejected_calls": self.rejected_calls,
            "state_changes": self.state_changes,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_success_time": self.last_success_time.isoformat() if self.last_success_time else None,
            "last_state_change": self.last_state_change.isoformat() if self.last_state_change else None,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
        }
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_calls == 0:
            return 1.0
        return self.successful_calls / self.total_calls
    
    @property
    def failure_rate(self) -> float:
        """Calculate failure rate."""
        if self.total_calls == 0:
            return 0.0
        return self.failed_calls / self.total_calls


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    
    def __init__(self, circuit_name: str, state: CircuitState, message: str = ""):
        self.circuit_name = circuit_name
        self.state = state
        self.message = message or f"Circuit breaker '{circuit_name}' is {state.value}"
        super().__init__(self.message)


class CircuitBreaker(Generic[T]):
    """
    Circuit breaker implementation for fault tolerance.
    
    Usage:
        breaker = CircuitBreaker("my-service")
        result = await breaker.call(async_operation, *args, **kwargs)
    
    Or as decorator:
        @breaker
        async def my_operation():
            ...
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        on_state_change: Optional[Callable[[str, CircuitState, CircuitState], Awaitable[None]]] = None,
        on_failure: Optional[Callable[[str, Exception], Awaitable[None]]] = None,
        on_success: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.on_state_change = on_state_change
        self.on_failure = on_failure
        self.on_success = on_success
        
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
        self._reinit_lock = threading.Lock()
    
    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state
    
    @property
    def stats(self) -> CircuitBreakerStats:
        """Circuit breaker statistics."""
        return self._stats
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)."""
        return self._state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self._state == CircuitState.HALF_OPEN
    
    async def call(
        self,
        operation: Callable[..., Awaitable[T]],
        *args,
        **kwargs
    ) -> T:
        """
        Execute an operation through the circuit breaker.
        
        Args:
            operation: Async callable to execute
            *args: Positional arguments for operation
            **kwargs: Keyword arguments for operation
        
        Returns:
            Result of the operation
        
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: If operation fails
        """
        await self._ensure_correct_loop()
        async with self._lock:
            # Check if we should transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    await self._transition_to(CircuitState.HALF_OPEN)
                else:
                    self._stats.rejected_calls += 1
                    raise CircuitBreakerError(
                        self.name,
                        self._state,
                        f"Circuit breaker '{self.name}' is open. "
                        f"Retry after {self._time_until_retry():.1f}s"
                    )
            
            # Check half-open call limit
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    self._stats.rejected_calls += 1
                    raise CircuitBreakerError(
                        self.name,
                        self._state,
                        f"Circuit breaker '{self.name}' half-open call limit reached"
                    )
                self._half_open_calls += 1
        
        # Execute the operation
        self._stats.total_calls += 1
        
        try:
            result = await operation(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            # Check if exception should be excluded
            if self._is_excluded_exception(e):
                await self._on_success()
                raise
            
            await self._on_failure(e)
            raise

    # Alias for call method to support legacy integrations or semantic preferences
    execute_async = call

    async def _ensure_correct_loop(self):
        """Ensure the lock is bound to the current running loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        # Check if we need to re-initialize due to loop change
        if getattr(self._lock, '_loop', None) != current_loop:
            with self._reinit_lock:
                # Double check loop binding under thread lock
                if getattr(self._lock, '_loop', None) != current_loop:
                    actual_lock_loop = getattr(self._lock, '_loop', None)
                    logger.debug(f"Detected event loop change in CircuitBreaker '{self.name}' (Lock loop: {id(actual_lock_loop)}, Current loop: {id(current_loop)}), re-initializing lock...")
                    # Re-initialize the lock for the new loop
                    self._lock = asyncio.Lock()
                    # Also reset half_open_calls if we switched loops, for safety
                    self._half_open_calls = 0
    
    async def _on_success(self) -> None:
        """Handle successful operation."""
        async with self._lock:
            self._stats.successful_calls += 1
            self._stats.consecutive_successes += 1
            self._stats.consecutive_failures = 0
            self._stats.last_success_time = datetime.now(timezone.utc)
            
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls -= 1
                
                if self._stats.consecutive_successes >= self.config.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)
        
        if self.on_success:
            try:
                await self.on_success(self.name)
            except Exception as e:
                logger.error(f"Error in on_success callback: {e}")
    
    async def _on_failure(self, error: Exception) -> None:
        """Handle failed operation."""
        async with self._lock:
            self._stats.failed_calls += 1
            self._stats.consecutive_failures += 1
            self._stats.consecutive_successes = 0
            self._stats.last_failure_time = datetime.now(timezone.utc)
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls -= 1
                await self._transition_to(CircuitState.OPEN)
            
            elif self._state == CircuitState.CLOSED:
                if self._stats.consecutive_failures >= self.config.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
        
        if self.on_failure:
            try:
                await self.on_failure(self.name, error)
            except Exception as e:
                logger.error(f"Error in on_failure callback: {e}")
    
    async def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._stats.state_changes += 1
        self._stats.last_state_change = datetime.now(timezone.utc)
        
        if new_state == CircuitState.CLOSED:
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._stats.consecutive_successes = 0
        elif new_state == CircuitState.OPEN:
            self._last_failure_time = time.time()
        
        logger.info(
            f"Circuit breaker '{self.name}' state change: "
            f"{old_state.value} -> {new_state.value}"
        )
        
        if self.on_state_change:
            try:
                await self.on_state_change(self.name, old_state, new_state)
            except Exception as e:
                logger.error(f"Error in on_state_change callback: {e}")
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset (uses adaptive timeout)."""
        if self._last_failure_time is None:
            return True
        # Use adaptive timeout based on consecutive failures
        adaptive_timeout = self.config.calculate_timeout(self._stats.consecutive_failures)
        return time.time() - self._last_failure_time >= adaptive_timeout
    
    def _time_until_retry(self) -> float:
        """Calculate time until retry is allowed (uses adaptive timeout)."""
        if self._last_failure_time is None:
            return 0.0
        elapsed = time.time() - self._last_failure_time
        # Use adaptive timeout based on consecutive failures
        adaptive_timeout = self.config.calculate_timeout(self._stats.consecutive_failures)
        return max(0.0, adaptive_timeout - elapsed)
    
    def _is_excluded_exception(self, error: Exception) -> bool:
        """Check if exception type is excluded from failure counting."""
        return any(
            isinstance(error, exc_type)
            for exc_type in self.config.excluded_exceptions
        )
    
    async def reset(self) -> None:
        """Manually reset the circuit breaker to closed state."""
        async with self._lock:
            await self._transition_to(CircuitState.CLOSED)
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes = 0
            self._half_open_calls = 0
    
    async def force_open(self) -> None:
        """Manually force the circuit breaker to open state."""
        async with self._lock:
            await self._transition_to(CircuitState.OPEN)
    
    async def can_execute(self) -> bool:
        """
        Check if operation can be executed through the circuit breaker.
        
        This is a lightweight check for HTTP client wrappers that manage
        their own request lifecycle but want circuit breaker protection.
        
        Returns:
            True if operation can proceed, False if circuit is open
        """
        await self._ensure_correct_loop()
        async with self._lock:
            # Check if we should transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    await self._transition_to(CircuitState.HALF_OPEN)
                else:
                    self._stats.rejected_calls += 1
                    return False
            
            # Check half-open call limit
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    self._stats.rejected_calls += 1
                    return False
                self._half_open_calls += 1
            
            self._stats.total_calls += 1
            return True
    
    async def record_success(self) -> None:
        """
        Record a successful operation for circuit breaker state management.
        
        Use this when managing request lifecycle manually (e.g., HTTP clients).
        """
        await self._on_success()
    
    async def record_failure(self, error: Exception) -> None:
        """
        Record a failed operation for circuit breaker state management.
        
        Use this when managing request lifecycle manually (e.g., HTTP clients).
        
        Args:
            error: The exception that occurred
        """
        # Check if exception should be excluded
        if self._is_excluded_exception(error):
            await self._on_success()  # Excluded exceptions count as success
            return
        
        await self._on_failure(error)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status."""
        return {
            "name": self.name,
            "state": self._state.value,
            "config": self.config.to_dict(),
            "stats": self._stats.to_dict(),
            "time_until_retry": self._time_until_retry() if self.is_open else 0,
        }
    
    def __call__(
        self,
        func: Callable[..., Awaitable[T]]
    ) -> Callable[..., Awaitable[T]]:
        """Use circuit breaker as a decorator."""
        async def wrapper(*args, **kwargs) -> T:
            return await self.call(func, *args, **kwargs)
        return wrapper


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers.
    
    Provides centralized management, monitoring, and configuration
    of circuit breakers across the system.
    """
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
        self._global_callbacks: Dict[str, List[Callable]] = {
            "on_state_change": [],
            "on_failure": [],
            "on_success": [],
        }
    
    async def get_or_create(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ) -> CircuitBreaker:
        """Get existing circuit breaker or create new one."""
        async with self._lock:
            if name not in self._breakers:
                breaker = CircuitBreaker(
                    name=name,
                    config=config,
                    on_state_change=self._create_state_change_handler(),
                    on_failure=self._create_failure_handler(),
                    on_success=self._create_success_handler(),
                )
                self._breakers[name] = breaker
            return self._breakers[name]
    
    async def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get a circuit breaker by name."""
        async with self._lock:
            return self._breakers.get(name)
    
    async def remove(self, name: str) -> bool:
        """Remove a circuit breaker."""
        async with self._lock:
            if name in self._breakers:
                del self._breakers[name]
                return True
            return False
    
    async def reset_all(self) -> None:
        """Reset all circuit breakers to closed state."""
        async with self._lock:
            for breaker in self._breakers.values():
                await breaker.reset()
    
    async def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers."""
        async with self._lock:
            return {
                name: breaker.get_status()
                for name, breaker in self._breakers.items()
            }
    
    async def get_open_circuits(self) -> List[str]:
        """Get names of all open circuit breakers."""
        async with self._lock:
            return [
                name for name, breaker in self._breakers.items()
                if breaker.is_open
            ]
    
    async def get_health_summary(self) -> Dict[str, Any]:
        """Get health summary of all circuit breakers."""
        async with self._lock:
            total = len(self._breakers)
            closed = sum(1 for b in self._breakers.values() if b.is_closed)
            open_count = sum(1 for b in self._breakers.values() if b.is_open)
            half_open = sum(1 for b in self._breakers.values() if b.is_half_open)
            
            total_calls = sum(b.stats.total_calls for b in self._breakers.values())
            total_failures = sum(b.stats.failed_calls for b in self._breakers.values())
            
            return {
                "total_circuits": total,
                "closed": closed,
                "open": open_count,
                "half_open": half_open,
                "health_percentage": (closed / total * 100) if total > 0 else 100,
                "total_calls": total_calls,
                "total_failures": total_failures,
                "overall_failure_rate": (total_failures / total_calls) if total_calls > 0 else 0,
            }
    
    def on_state_change(
        self,
        callback: Callable[[str, CircuitState, CircuitState], Awaitable[None]]
    ) -> None:
        """Register global state change callback."""
        self._global_callbacks["on_state_change"].append(callback)
    
    def on_failure(
        self,
        callback: Callable[[str, Exception], Awaitable[None]]
    ) -> None:
        """Register global failure callback."""
        self._global_callbacks["on_failure"].append(callback)
    
    def on_success(
        self,
        callback: Callable[[str], Awaitable[None]]
    ) -> None:
        """Register global success callback."""
        self._global_callbacks["on_success"].append(callback)
    
    def _create_state_change_handler(
        self
    ) -> Callable[[str, CircuitState, CircuitState], Awaitable[None]]:
        """Create state change handler that calls global callbacks."""
        async def handler(name: str, old_state: CircuitState, new_state: CircuitState):
            for callback in self._global_callbacks["on_state_change"]:
                try:
                    await callback(name, old_state, new_state)
                except Exception as e:
                    logger.error(f"Error in global state change callback: {e}")
        return handler
    
    def _create_failure_handler(
        self
    ) -> Callable[[str, Exception], Awaitable[None]]:
        """Create failure handler that calls global callbacks."""
        async def handler(name: str, error: Exception):
            for callback in self._global_callbacks["on_failure"]:
                try:
                    await callback(name, error)
                except Exception as e:
                    logger.error(f"Error in global failure callback: {e}")
        return handler
    
    def _create_success_handler(
        self
    ) -> Callable[[str], Awaitable[None]]:
        """Create success handler that calls global callbacks."""
        async def handler(name: str):
            for callback in self._global_callbacks["on_success"]:
                try:
                    await callback(name)
                except Exception as e:
                    logger.error(f"Error in global success callback: {e}")
        return handler


# =============================================================================
# Integration with Observer Mesh
# =============================================================================

class ObserverMeshIntegration:
    """
    Integrates circuit breakers with the observer mesh for monitoring.
    
    Automatically publishes circuit breaker events and metrics to the
    observer mesh for centralized monitoring.
    """
    
    def __init__(self, registry: CircuitBreakerRegistry):
        self.registry = registry
        self._observer = None
    
    def connect(self, observer) -> None:
        """Connect to an observer mesh instance."""
        from python.helpers.observer_mesh import ObserverMesh, EventType, AlertSeverity
        
        self._observer = observer
        
        # Register callbacks
        self.registry.on_state_change(self._on_state_change)
        self.registry.on_failure(self._on_failure)
        self.registry.on_success(self._on_success)
    
    async def _on_state_change(
        self,
        name: str,
        old_state: CircuitState,
        new_state: CircuitState
    ) -> None:
        """Handle circuit state change."""
        if not self._observer:
            return
        
        from python.helpers.observer_mesh import EventType, AlertSeverity, Event
        
        # Publish event
        event_type = (
            EventType.CIRCUIT_OPENED if new_state == CircuitState.OPEN
            else EventType.CIRCUIT_CLOSED if new_state == CircuitState.CLOSED
            else EventType.CUSTOM
        )
        
        await self._observer.events.publish(Event(
            id=str(uuid.uuid4())[:8],
            type=event_type,
            timestamp=datetime.now(timezone.utc),
            source=f"circuit_breaker:{name}",
            data={
                "circuit_name": name,
                "old_state": old_state.value,
                "new_state": new_state.value,
            },
        ))
        
        # Update metrics
        await self._observer.metrics.gauge(
            f"circuit_breaker_state",
            1 if new_state == CircuitState.CLOSED else 0,
            labels={"circuit": name}
        )
        
        # Fire alert if circuit opened
        if new_state == CircuitState.OPEN:
            await self._observer.alerts.fire(
                severity=AlertSeverity.WARNING,
                title=f"Circuit Breaker Opened: {name}",
                message=f"Circuit breaker '{name}' has opened due to failures",
                source=f"circuit_breaker:{name}",
                labels={"circuit": name},
            )
    
    async def _on_failure(self, name: str, error: Exception) -> None:
        """Handle circuit failure."""
        if not self._observer:
            return
        
        await self._observer.metrics.increment(
            "circuit_breaker_failures_total",
            labels={"circuit": name, "error_type": type(error).__name__}
        )
    
    async def _on_success(self, name: str) -> None:
        """Handle circuit success."""
        if not self._observer:
            return
        
        await self._observer.metrics.increment(
            "circuit_breaker_successes_total",
            labels={"circuit": name}
        )


# =============================================================================
# Factory Functions
# =============================================================================

# Global registry instance
_global_registry: Optional[CircuitBreakerRegistry] = None


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Get the global circuit breaker registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = CircuitBreakerRegistry()
    return _global_registry


async def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None
) -> CircuitBreaker:
    """Get or create a circuit breaker from the global registry."""
    registry = get_circuit_breaker_registry()
    return await registry.get_or_create(name, config)


def create_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    success_threshold: int = 3,
    timeout: float = 30.0,
    **kwargs
) -> CircuitBreaker:
    """Create a standalone circuit breaker."""
    config = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        success_threshold=success_threshold,
        timeout=timeout,
        **kwargs
    )
    return CircuitBreaker(name=name, config=config)


# =============================================================================
# Convenience Decorator
# =============================================================================

def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    success_threshold: int = 3,
    timeout: float = 30.0,
):
    """
    Decorator to wrap async functions with circuit breaker protection.
    
    Usage:
        @circuit_breaker("my-service", failure_threshold=3)
        async def call_external_service():
            ...
    """
    breaker = create_circuit_breaker(
        name=name,
        failure_threshold=failure_threshold,
        success_threshold=success_threshold,
        timeout=timeout,
    )
    
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(*args, **kwargs) -> T:
            return await breaker.call(func, *args, **kwargs)
        setattr(wrapper, "_circuit_breaker", breaker)  # Expose breaker for testing
        return wrapper
    return decorator


# =============================================================================
# Context Manager
# =============================================================================

class CircuitBreakerContext:
    """
    Context manager for circuit breaker operations.
    
    Usage:
        async with CircuitBreakerContext("my-service") as breaker:
            result = await breaker.call(operation)
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ):
        self.name = name
        self.config = config
        self.breaker: Optional[CircuitBreaker] = None
    
    async def __aenter__(self) -> CircuitBreaker:
        self.breaker = await get_circuit_breaker(self.name, self.config)
        return self.breaker
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # Circuit breaker state persists, no cleanup needed
        pass
