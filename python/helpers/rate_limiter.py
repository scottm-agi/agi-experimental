from __future__ import annotations
import asyncio
import time
import random
from typing import Callable, Awaitable, Optional, Dict
from dataclasses import dataclass, field
from enum import Enum
from python.redis_client import RedisClient
from python.helpers.distributed_rate_limiter import DistributedRateLimiter


class RateLimitState(Enum):
    """State of rate limiting for coordination between agents."""
    NORMAL = "normal"
    THROTTLED = "throttled"
    BACKING_OFF = "backing_off"


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff."""
    initial_delay: float = 1.0  # Initial delay in seconds
    max_delay: float = 60.0  # Maximum delay in seconds
    multiplier: float = 2.0  # Exponential multiplier
    jitter: float = 0.1  # Random jitter factor (0.1 = 10%)
    max_retries: int = 10  # Maximum number of retries


@dataclass
class RateLimitEvent:
    """Event emitted when rate limiting occurs."""
    timestamp: float
    key: str
    total: int
    limit: int
    delay: float
    attempt: int


class RateLimiter:
    """
    Rate limiter with exponential backoff and retry support.
    
    Features:
    - Token bucket style rate limiting
    - Exponential backoff with jitter
    - Coordination support for multi-agent scenarios
    - Event callbacks for monitoring
    """
    
    # Class-level state for coordination between agents
    _global_state: dict[str, RateLimitState] = {}
    _global_backoff_until: dict[str, float] = {}
    _global_locks: Dict[int, asyncio.Lock] = {}
    _dist_limiters: Dict[int, Optional[DistributedRateLimiter]] = {}

    @classmethod
    def _get_global_lock(cls) -> asyncio.Lock:
        """Get or create loop-local lock for global state."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop_id = 0
            
        if loop_id not in cls._global_locks:
            cls._global_locks[loop_id] = asyncio.Lock()
        return cls._global_locks[loop_id]

    @classmethod
    async def _get_dist_limiter(cls) -> Optional[DistributedRateLimiter]:
        """Get or initialize the loop-local distributed rate limiter."""
        try:
            current_loop = asyncio.get_running_loop()
            loop_id = id(current_loop)
        except RuntimeError:
            loop_id = 0
            
        if loop_id not in cls._dist_limiters or cls._dist_limiters[loop_id] is None:
            # We don't use a shared lock here because each loop is isolated
            redis = RedisClient.get_instance()
            if await redis.connect():
                cls._dist_limiters[loop_id] = DistributedRateLimiter(redis)
        return cls._dist_limiters.get(loop_id)
    
    def __init__(
        self, 
        seconds: int = 60, 
        backoff_config: Optional[BackoffConfig] = None,
        **limits: int
    ):
        self.timeframe = seconds
        self.limits = {key: value if isinstance(value, (int, float)) else 0 for key, value in (limits or {}).items()}
        self.values = {key: [] for key in self.limits.keys()}
        self._lock = asyncio.Lock()
        self.backoff_config = backoff_config or BackoffConfig()
        self._current_attempt = 0
        self._last_backoff_delay = 0.0
        self._events: list[RateLimitEvent] = []
        self._event_callbacks: list[Callable[[RateLimitEvent], Awaitable[None]]] = []

    def add(self, **kwargs: int):
        """Add usage to the rate limiter."""
        now = time.time()
        for key, value in kwargs.items():
            if not key in self.values:
                self.values[key] = []
            self.values[key].append((now, value))
            
            # Also add to distributed limiter if available
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self._add_to_dist(key, value))
            except RuntimeError:
                # No loop running, skip background task
                pass

    async def _add_to_dist(self, key: str, value: int):
        """Add usage to distributed limiter in background.
        
        This is called as a fire-and-forget task, so errors must be
        caught to prevent 'Task exception was never retrieved' warnings.
        """
        try:
            dist = await self._get_dist_limiter()
            if dist:
                # We treat 'requests' vs other metrics (tokens) based on key name
                if "request" in key.lower():
                    await dist.add_usage(key, requests=value)
                else:
                    await dist.add_usage(key, tokens=value)
        except Exception:
            # Redis failures are non-critical for local rate limiting.
            # Silently degrade to local-only tracking.
            pass

    async def cleanup(self):
        """Remove expired entries from the rate limiter."""
        async with self._lock:
            now = time.time()
            cutoff = now - self.timeframe
            for key in self.values:
                self.values[key] = [(t, v) for t, v in self.values[key] if t > cutoff]

    async def get_total(self, key: str) -> int:
        """Get the total usage for a key within the timeframe."""
        async with self._lock:
            if not key in self.values:
                return 0
            return sum(value for _, value in self.values[key])

    def calculate_backoff_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay with full jitter.
        
        Formula: delay = random.uniform(0, min(max_delay, initial_delay * (multiplier ^ attempt)))
        This "Full Jitter" strategy is more effective at resolving congestion.
        """
        config = self.backoff_config
        
        # Calculate base delay with exponential growth
        base_delay = config.initial_delay * (config.multiplier ** attempt)
        
        # Cap at maximum delay
        capped_delay = min(base_delay, config.max_delay)
        
        # Full jitter: random between 0 and capped_delay
        return max(0.1, random.uniform(0, capped_delay))

    async def wait(
        self,
        # NOTE: Use Optional[...] instead of ``| None`` for Python 3.9
        # compatibility, since ``Callable | None`` is not supported on
        callback: Optional[Callable[[str, str, int, int], Awaitable[bool]]] = None,
    ):
        """
        Wait until rate limits are satisfied, using exponential backoff.
        
        Args:
            callback: Optional callback for rate limit notifications.
                     Returns True to skip waiting, False to continue waiting.
        """
        attempt = 0
        
        while True:
            await self.cleanup()
            should_wait = False
            wait_key = None
            wait_total = 0
            wait_limit = 0

            for key, limit in self.limits.items():
                if limit <= 0:  # Skip if no limit set
                    continue

                total = await self.get_total(key)
                if total > limit:
                    should_wait = True
                    wait_key = key
                    wait_total = total
                    wait_limit = limit
                    break

            # Check global backoff if local is fine
            if not should_wait:
                try:
                    dist = await self._get_dist_limiter()
                    for key in self.limits.keys():
                        if dist and await dist.is_backing_off(key):
                            should_wait = True
                            wait_key = key
                            # We don't have totals readily available here for global without extra calls,
                            # but we know we are backing off.
                            break
                except Exception:
                    # Redis failure — skip distributed backoff check, rely on local only
                    pass

            if not should_wait:
                # Reset attempt counter on success
                self._current_attempt = 0
                self._last_backoff_delay = 0.0
                break

            # Calculate backoff delay
            delay = self.calculate_backoff_delay(attempt)
            self._current_attempt = attempt
            self._last_backoff_delay = delay
            
            # Update global state for coordination (local)
            await self._set_global_state(wait_key or "unknown", RateLimitState.BACKING_OFF, delay)
            
            # Update distributed state for coordination (global)
            try:
                dist = await self._get_dist_limiter()
                if dist:
                    await dist.set_backoff(wait_key or "unknown", int(delay * 1000))
            except Exception:
                pass  # Redis failure — local backoff still works
            
            # Create and emit event
            event = RateLimitEvent(
                timestamp=time.time(),
                key=wait_key or "unknown",
                total=wait_total,
                limit=wait_limit,
                delay=delay,
                attempt=attempt
            )
            self._events.append(event)
            await self._emit_event(event)
            
            if callback:
                msg = f"Rate limit exceeded for {wait_key} ({wait_total}/{wait_limit}), backing off {delay:.1f}s (attempt {attempt + 1})"
                skip_wait = await callback(msg, wait_key or "unknown", wait_total, wait_limit)
                if skip_wait:
                    break
            
            # Check if we've exceeded max retries
            if attempt >= self.backoff_config.max_retries:
                raise RateLimitExceededError(
                    f"Rate limit exceeded after {attempt} retries for {wait_key}"
                )
            
            # Wait with exponential backoff, but allowing for periodic status checks to break the wait early.
            # This ensures responsiveness to interventions during long rate limit pauses.
            rem_delay = delay
            while rem_delay > 0:
                # Small sleep segment
                step = min(rem_delay, 1.0)
                await asyncio.sleep(step)
                rem_delay -= step
                
                # Check for interruption via callback (e.g. pending intervention)
                if callback:
                    if await callback("STATUS_CHECK", wait_key or "unknown", wait_total, wait_limit):
                        break  # Immediately break out of wait loop if callback returns True
            
            attempt += 1

    async def wait_for_global_backoff(self, provider_key: str) -> float:
        """
        Wait if there's a global backoff in effect for this provider.
        Used for coordination between agents.
        
        Returns the time waited.
        """
        async with RateLimiter._get_global_lock():
            backoff_until = RateLimiter._global_backoff_until.get(provider_key, 0)
            now = time.time()
            
            if backoff_until > now:
                wait_time = backoff_until - now
                await asyncio.sleep(wait_time)
                return wait_time
            
            return 0.0

    @classmethod
    async def _set_global_state(cls, key: str, state: RateLimitState, delay: float = 0):
        """Set global rate limit state for coordination."""
        async with cls._get_global_lock():
            cls._global_state[key] = state
            if delay > 0:
                cls._global_backoff_until[key] = time.time() + delay

    @classmethod
    async def get_global_state(cls, key: str) -> RateLimitState:
        """Get global rate limit state."""
        async with cls._get_global_lock():
            return cls._global_state.get(key, RateLimitState.NORMAL)

    @classmethod
    async def is_globally_throttled(cls, provider: str) -> bool:
        """Check if a provider is currently being throttled globally."""
        async with cls._get_global_lock():
            backoff_until = cls._global_backoff_until.get(provider, 0)
            return time.time() < backoff_until

    @classmethod
    async def get_global_wait_time(cls, provider: str) -> float:
        """Get remaining wait time for global backoff."""
        async with cls._get_global_lock():
            backoff_until = cls._global_backoff_until.get(provider, 0)
            remaining = backoff_until - time.time()
            return max(0, remaining)

    def add_event_callback(self, callback: Callable[[RateLimitEvent], Awaitable[None]]):
        """Add a callback to be notified of rate limit events."""
        self._event_callbacks.append(callback)

    async def _emit_event(self, event: RateLimitEvent):
        """Emit a rate limit event to all callbacks."""
        for callback in self._event_callbacks:
            try:
                await callback(event)
            except Exception:
                pass  # Don't let callback errors affect rate limiting

    def get_recent_events(self, count: int = 10) -> list[RateLimitEvent]:
        """Get recent rate limit events."""
        return self._events[-count:]

    @property
    def current_attempt(self) -> int:
        """Get the current retry attempt number."""
        return self._current_attempt

    @property
    def last_backoff_delay(self) -> float:
        """Get the last backoff delay used."""
        return self._last_backoff_delay


class RateLimitExceededError(Exception):
    """Raised when rate limit retries are exhausted."""
    pass


class RetryWithBackoff:
    """
    Decorator/context manager for retrying operations with exponential backoff.
    
    Usage:
        async with RetryWithBackoff(max_retries=5) as retry:
            result = await some_api_call()
            
        # Or as decorator
        @RetryWithBackoff.decorator(max_retries=5)
        async def my_function():
            ...
    """
    
    AUTH_ERROR_CODES = (401, 403)  # These get a lower retry cap

    def __init__(
        self,
        max_retries: int = 5,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        jitter: float = 0.1,
        retryable_exceptions: tuple = (Exception,),
        retryable_status_codes: tuple = (401, 403, 408, 429, 500, 502, 503, 504),
        auth_error_max_retries: int = 2,
    ):
        self.config = BackoffConfig(
            initial_delay=initial_delay,
            max_delay=max_delay,
            multiplier=multiplier,
            jitter=jitter,
            max_retries=max_retries,
        )
        self.retryable_exceptions = retryable_exceptions
        self.retryable_status_codes = retryable_status_codes
        self.auth_error_max_retries = auth_error_max_retries
        self.attempt = 0
        self.last_exception: Optional[Exception] = None

    def calculate_delay(self) -> float:
        """Calculate the next backoff delay."""
        base_delay = self.config.initial_delay * (self.config.multiplier ** self.attempt)
        capped_delay = min(base_delay, self.config.max_delay)
        jitter = random.uniform(-self.config.jitter, self.config.jitter)
        return max(0.1, capped_delay * (1 + jitter))

    def is_retryable(self, exc: Exception) -> bool:
        """Check if an exception is retryable."""
        # Check status code if available
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and status_code in self.retryable_status_codes:
            return True
        
        # Check exception type
        return isinstance(exc, self.retryable_exceptions)

    async def execute(self, func: Callable[..., Awaitable], *args, **kwargs):
        """Execute a function with retry logic.
        
        For 401/403 auth errors, uses auth_error_max_retries (default 2)
        instead of max_retries to prevent amplification loops.
        """
        self.attempt = 0
        
        while True:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                self.last_exception = e
                
                if not self.is_retryable(e):
                    raise
                
                # Determine retry cap based on error type
                status_code = getattr(e, "status_code", None)
                if isinstance(status_code, int) and status_code in self.AUTH_ERROR_CODES:
                    effective_max = self.auth_error_max_retries
                else:
                    effective_max = self.config.max_retries
                
                if self.attempt >= effective_max:
                    raise
                
                delay = self.calculate_delay()
                await asyncio.sleep(delay)
                self.attempt += 1

    @classmethod
    def decorator(cls, **kwargs):
        """Create a decorator for retrying functions."""
        def wrapper(func):
            async def wrapped(*args, **func_kwargs):
                retry = cls(**kwargs)
                return await retry.execute(func, *args, **func_kwargs)
            return wrapped
        return wrapper


# Global rate limiter registry for coordination (loop-local)
_rate_limiter_registries: dict[int, dict[str, RateLimiter]] = {}


def get_or_create_rate_limiter(
    provider: str, 
    name: str, 
    requests: int = 0, 
    input_tokens: int = 0, 
    output_tokens: int = 0,
    backoff_config: Optional[BackoffConfig] = None
) -> RateLimiter:
    """
    Get or create a rate limiter for a provider/model combination.
    Uses a loop-local registry for coordination between agents.
    """
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop_id = 0
        
    if loop_id not in _rate_limiter_registries:
        _rate_limiter_registries[loop_id] = {}
        
    registry = _rate_limiter_registries[loop_id]
    key = f"{provider}\\{name}"
    
    if key not in registry:
        registry[key] = RateLimiter(
            seconds=60,
            backoff_config=backoff_config,
        )
    
    limiter = registry[key]
    limiter.limits["requests"] = requests or 0
    limiter.limits["input"] = input_tokens or 0
    limiter.limits["output"] = output_tokens or 0
    
    return limiter


async def coordinate_agent_wait(provider: str, agent_id: str) -> float:
    """
    Coordinate waiting between agents when rate limited.
    
    This ensures that when one agent hits a rate limit, other agents
    using the same provider also wait appropriately.
    
    Returns the time waited.
    """
    wait_time = await RateLimiter.get_global_wait_time(provider)
    
    if wait_time > 0:
        # Add small random jitter to prevent all agents resuming at once
        jitter = random.uniform(0, 0.5)
        total_wait = wait_time + jitter
        await asyncio.sleep(total_wait)
        return total_wait
    
    return 0.0
