from __future__ import annotations
"""
Redis Client for AGIX Parallel Swarm

This module provides an async Redis client wrapper with:
- Connection pooling
- Health checks
- Graceful reconnection
- Distributed state management
"""

import asyncio
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Union, Callable, Awaitable, TypeVar
from dataclasses import dataclass, field
from datetime import datetime
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import ConnectionError, TimeoutError, RedisError

from python.helpers.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerError

logger = logging.getLogger(__name__)

T = TypeVar('T')

@dataclass
class RedisConfig:
    """Configuration for Redis connection."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 100  # RCA Phase 2 (P2-1): scaled from 20 for 50-agent swarms
    timeout: int = 30           # RCA Phase 2 (P2-1): scaled from 10 for parallel workloads
    retry_on_timeout: bool = True
    health_check_interval: int = 30

    @classmethod
    def from_parallel_settings(cls) -> "RedisConfig":
        """Load configuration from parallel_settings.py."""
        from python.helpers.parallel_settings import get_redis_config
        settings = get_redis_config()
        pool_settings = settings.get("pool", {})
        
        # Verbose logging of discovered config
        host = settings.get("host", "localhost")
        port = settings.get("port", 6379)
        password = settings.get("password")
        has_password = bool(password and password.strip())
        
        logger.info(f"Redis Config Discovery: host={host}, port={port}, database={settings.get('db', 0)}, has_password={has_password}")
        
        return cls(
            host=host,
            port=port,
            db=settings.get("db", 0),
            password=password if has_password else None,
            max_connections=pool_settings.get("max_connections", 100),
            timeout=pool_settings.get("timeout", 30),
            retry_on_timeout=pool_settings.get("retry_on_timeout", True),
        )


@dataclass
class ConnectionState:
    """Tracks the state of Redis connection."""
    connected: bool = False
    last_health_check: Optional[datetime] = None
    consecutive_failures: int = 0
    total_reconnects: int = 0


class RedisClient:
    """
    Async Redis client wrapper with connection pooling and health monitoring.
    
    Features:
    - Automatic connection pooling
    - Health checks with configurable intervals
    - Graceful reconnection on failures
    - JSON serialization for complex objects
    - Distributed locking support
    """
    
    _instances: Dict[int, 'RedisClient'] = {}

    @classmethod
    def get_instance(cls, config: Optional[RedisConfig] = None) -> 'RedisClient':
        """Get or create loop-local singleton instance of RedisClient."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop_id = 0 # Default/Global ID
            
        if loop_id not in cls._instances:
            if not config:
                logger.info("Initializing RedisClient loop-local singleton...")
                # This will trigger the from_parallel_settings() discovery logs
                config = RedisConfig.from_parallel_settings()
            
            # Log discovery result explicitly here as well
            logger.info(f"Redis Client Registry: Creating instance for loop={loop_id} host={config.host}")
            
            cls._instances[loop_id] = RedisClient(config)
        return cls._instances[loop_id]
    
    def __init__(self, config: Optional[RedisConfig] = None):
        """
        Initialize Redis client.
        
        Args:
            config: Redis configuration. Uses defaults if not provided.
        """
        self.config = config or RedisConfig.from_parallel_settings()
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[redis.Redis] = None
        self._state = ConnectionState()
        self._lock = asyncio.Lock()
        self._health_check_task: Optional[asyncio.Task] = None
        self._reinit_lock = threading.Lock()
        self._bound_loop_id: Optional[int] = None  # Explicit loop binding (Python 3.12 compat)
        
        # Initialize circuit breaker
        self.breaker = CircuitBreaker(
            name=f"redis:{self.config.host}:{self.config.port}",
            config=CircuitBreakerConfig(
                failure_threshold=20,
                timeout=30.0,  # Wait 30s before retry when open
                excluded_exceptions=[json.JSONDecodeError, TypeError, ValueError]
            )
        )
    
    async def connect(self) -> bool:
        """
        Establish connection to Redis with connection pooling.
        Protected by circuit breaker.
        
        Returns:
            True if connection successful, False otherwise.
        """
        try:
            return await self.breaker.call(self._do_connect)
        except CircuitBreakerError as e:
            logger.warning(f"Redis connection skipped: {e}")
            return False
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            return False

    async def _do_connect(self) -> bool:
        """Internal connection logic."""
        async with self._lock:
            if self._client and self._state.connected:
                return True
            
            # Create connection pool
            logger.info(f"Creating Redis ConnectionPool: host={self.config.host}, connections={self.config.max_connections}, timeout={self.config.timeout}s")
            self._pool = ConnectionPool(
                host=self.config.host,
                port=self.config.port,
                db=self.config.db,
                password=self.config.password if self.config.password else None,
                max_connections=self.config.max_connections,
                socket_timeout=self.config.timeout,
                socket_connect_timeout=self.config.timeout,
                retry_on_timeout=self.config.retry_on_timeout,
                decode_responses=True,
            )
            
            # Create client with pool
            self._client = redis.Redis(connection_pool=self._pool)
            
            # Test connection
            logger.info(f"Pinging Redis at {self.config.host}:{self.config.port}...")
            await self._client.ping()
            
            self._state.connected = True
            self._state.consecutive_failures = 0
            self._state.last_health_check = datetime.now()
            
            # Bind to current loop for _ensure_correct_loop detection
            try:
                self._bound_loop_id = id(asyncio.get_running_loop())
            except RuntimeError:
                self._bound_loop_id = None
            
            logger.info(f"Connected to Redis at {self.config.host}:{self.config.port} (loop={self._bound_loop_id})")
            
            # Start health check task
            if self._health_check_task is None or self._health_check_task.done():
                self._health_check_task = asyncio.create_task(self._health_check_loop())
            
            return True
    
    async def disconnect(self) -> None:
        """Close Redis connection and cleanup resources."""
        async with self._lock:
            # Cancel health check task
            if self._health_check_task and not self._health_check_task.done():
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    logger.debug("[RedisClient] Health check task cancelled during disconnect")
            
            # Close client
            if self._client:
                await self._client.close()
                self._client = None
            
            # Close pool
            if self._pool:
                await self._pool.disconnect()
                self._pool = None
            
            self._state.connected = False
            logger.info("Disconnected from Redis")
    
    async def _health_check_loop(self) -> None:
        """Background task for periodic health checks."""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await self._perform_health_check()
            except asyncio.CancelledError:
                logger.debug("[RedisClient] Health check loop cancelled — shutting down gracefully")
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _perform_health_check(self) -> bool:
        """
        Perform a health check on the Redis connection.
        
        Returns:
            True if healthy, False otherwise.
        """
        try:
            if self._client:
                await self._client.ping()
                self._state.last_health_check = datetime.now()
                self._state.consecutive_failures = 0
                return True
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Health check failed: {e}")
            self._state.consecutive_failures += 1
            
            # Attempt reconnection after multiple failures
            if self._state.consecutive_failures >= 3:
                logger.info("Attempting reconnection after health check failures")
                self._state.connected = False
                await self.connect()
        
        return False
    
    async def _ensure_connected(self) -> None:
        """Ensure client is connected, reconnect if necessary."""
        if not self._state.connected or not self._client:
            await self.connect()
        
        if not self._state.connected:
            raise ConnectionError("Unable to connect to Redis")
            
    async def _execute(self, func: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """Execute a Redis operation protected by circuit breaker."""
        await self._ensure_correct_loop()
        # Ensure we always use the latest self._client instance
        method_name = func.__name__
        return await self.breaker.call(self._do_execute, method_name, *args, **kwargs)

    async def _ensure_correct_loop(self):
        """Ensure the client and its lock are bound to the current running loop.
        
        Uses explicit _bound_loop_id instead of Lock._loop because:
        - Python 3.12 asyncio.Lock() doesn't expose _loop attribute
        - Using getattr(self._lock, '_loop', None) returns None in Python 3.12
        - None != current_loop is ALWAYS True, causing reinit on every call
        - This race created 'Future attached to a different loop' errors
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        current_loop_id = id(current_loop)
        
        # Check if we need to re-initialize due to loop change
        # Use explicit _bound_loop_id instead of unreliable Lock._loop (Python 3.12 compat)
        if self._bound_loop_id is not None and self._bound_loop_id == current_loop_id:
            return  # Same loop, no reinit needed
        
        if self._bound_loop_id is None:
            # Fresh client, just bind to current loop without full reinit
            self._bound_loop_id = current_loop_id
            return
        
        # Different loop detected — full reinit required
        with self._reinit_lock:
            # Double check under thread lock
            if self._bound_loop_id == current_loop_id:
                return
            
            logger.info(f"Detected event loop change (bound={self._bound_loop_id}, current={current_loop_id}), re-initializing Redis connection...")
            
            # Cancel stale health check task from old loop
            if self._health_check_task and not self._health_check_task.done():
                self._health_check_task.cancel()
                logger.info("Cancelled stale health check task from previous event loop")
            self._health_check_task = None
            
            # Re-initialize for the new loop
            self._lock = asyncio.Lock()
            self._pool = None
            self._client = None
            self._state = ConnectionState()
            self._bound_loop_id = current_loop_id
            
            # Also re-initialize circuit breaker for the new loop
            self.breaker = CircuitBreaker(
                name=f"redis:{self.config.host}:{self.config.port}",
                config=CircuitBreakerConfig(
                    failure_threshold=20,
                    timeout=30.0,
                    excluded_exceptions=[json.JSONDecodeError, TypeError, ValueError]
                )
            )
            
            # We don't call connect() here to avoid recursion,
            # the subsequent _ensure_connected() will handle it.

    async def _do_execute(self, method_name: str, *args, **kwargs) -> T:
        """Actually execute the Redis command with retry logic for timeouts."""
        await self._ensure_connected()
        max_retries = 3
        last_exception = None
        
        for attempt in range(max_retries):
            # Check for loop starvation before starting
            loop = asyncio.get_event_loop()
            pre_call_time = loop.time()
            
            try:
                func = getattr(self._client, method_name)
                # First check: Did we even get to start the call? 
                # If duration here is > 1s, the loop was blocked BEFORE this code ran.
                
                result = await func(*args, **kwargs)
                
                duration = loop.time() - pre_call_time
                if duration > 1.0:
                    logger.warning(
                        f"EVENT LOOP LAG DETECTED: Redis {method_name} took {duration:.2f}s "
                        f"(threshold 1.0s). This usually indicates loop starvation by CPU work."
                    )
                return result
            except (asyncio.TimeoutError, TimeoutError) as e:
                duration = loop.time() - pre_call_time
                last_exception = e
                wait_time = 0.5 * (attempt + 1)
                
                # Check if this looks like pool exhaustion or just a slow server
                pool_size = len(self._pool._available_connections) + len(self._pool._in_use_connections)
                in_use = len(self._pool._in_use_connections)
                
                msg = (
                    f"Redis timeout on {method_name} after {duration:.2f}s "
                    f"(attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s... "
                    f"Pool state: [size={pool_size}, in_use={in_use}]"
                )
                
                if duration > 5.0 and in_use >= (pool_size * 0.9):
                    logger.error(f"REDIS POOL SATURATION: {msg}")
                elif duration > 5.0:
                    logger.warning(f"HEAVY LOOP LAG / REDIS HANG: {msg}")
                else:
                    logger.warning(msg)
                    
                await asyncio.sleep(wait_time)
            except ConnectionError as e:
                logger.error(f"Redis connection error on {method_name}: {e}. Forcing reconnection.")
                self._state.connected = False
                await self.connect()
                last_exception = e
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Redis unexpected error on {method_name}: {type(e).__name__}: {e}")
                raise e
        
        if last_exception:
            logger.error(f"Redis command {method_name} failed after {max_retries} attempts.")
            raise last_exception

    # ==========================================================================
    # Basic Operations
    # ==========================================================================
    
    async def get(self, key: str) -> Optional[str]:
        """
        Get a value by key.
        """
        return await self._execute(self._client.get, key)
    
    async def set(
        self,
        key: str,
        value: str,
        ex: Optional[int] = None,
        px: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        """
        Set a key-value pair.
        """
        result = await self._execute(self._client.set, key, value, ex=ex, px=px, nx=nx, xx=xx)
        return result is not None
    
    async def delete(self, *keys: str) -> int:
        """
        Delete one or more keys.
        """
        return await self._execute(self._client.delete, *keys)
    
    async def exists(self, *keys: str) -> int:
        """
        Check if keys exist.
        """
        return await self._execute(self._client.exists, *keys)

    async def incrby(self, key: str, amount: int = 1) -> int:
        """
        Increment a key by a specific amount.
        """
        return await self._execute(self._client.incrby, key, amount)

    async def expire(self, key: str, seconds: int) -> bool:
        """
        Set a TTL for a key.
        """
        return await self._execute(self._client.expire, key, seconds)

    async def eval(self, script: str, numkeys: int, *args) -> Any:
        """
        Execute a Lua script.
        """
        return await self._execute(self._client.eval, script, numkeys, *args)

    async def scard(self, key: str) -> int:
        """
        Get the number of members in a set.
        """
        return await self._execute(self._client.scard, key)
    
    # ==========================================================================
    # JSON Operations
    # ==========================================================================
    
    async def get_json(self, key: str) -> Optional[Any]:
        """
        Get a JSON value by key.
        
        Args:
            key: The key to retrieve.
            
        Returns:
            The deserialized JSON value or None.
        """
        value = await self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.warning(f"Failed to decode JSON for key: {key}")
        return None
    
    async def set_json(
        self,
        key: str,
        value: Any,
        ex: Optional[int] = None,
    ) -> bool:
        """
        Set a JSON value.
        
        Args:
            key: The key to set.
            value: The value to serialize and set.
            ex: Expiry in seconds.
            
        Returns:
            True if set successfully.
        """
        try:
            json_value = json.dumps(value, default=str)
            return await self.set(key, json_value, ex=ex)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize JSON for key {key}: {e}")
            return False
    
    # ==========================================================================
    # Hash Operations
    # ==========================================================================
    
    async def hget(self, name: str, key: str) -> Optional[str]:
        """Get a hash field value."""
        return await self._execute(self._client.hget, name, key)
    
    async def hset(self, name: str, key: str, value: str) -> int:
        """Set a hash field value."""
        return await self._execute(self._client.hset, name, key, value)
    
    async def hgetall(self, name: str) -> Dict[str, str]:
        """Get all hash fields and values."""
        return await self._execute(self._client.hgetall, name)
    
    async def hdel(self, name: str, *keys: str) -> int:
        """Delete hash fields."""
        return await self._execute(self._client.hdel, name, *keys)
    
    # ==========================================================================
    # List Operations
    # ==========================================================================
    
    async def lpush(self, name: str, *values: str) -> int:
        """Push values to the left of a list."""
        return await self._execute(self._client.lpush, name, *values)
    
    async def rpush(self, name: str, *values: str) -> int:
        """Push values to the right of a list."""
        return await self._execute(self._client.rpush, name, *values)
    
    async def lpop(self, name: str, count: Optional[int] = None) -> Optional[Union[str, List[str]]]:
        """Pop values from the left of a list."""
        return await self._execute(self._client.lpop, name, count)
    
    async def rpop(self, name: str, count: Optional[int] = None) -> Optional[Union[str, List[str]]]:
        """Pop values from the right of a list."""
        return await self._execute(self._client.rpop, name, count)
    
    async def lrange(self, name: str, start: int, end: int) -> List[str]:
        """Get a range of values from a list."""
        return await self._execute(self._client.lrange, name, start, end)
    
    async def llen(self, name: str) -> int:
        """Get the length of a list."""
        return await self._execute(self._client.llen, name)
    
    # ==========================================================================
    # Set Operations
    # ==========================================================================
    
    async def sadd(self, name: str, *values: str) -> int:
        """Add values to a set."""
        return await self._execute(self._client.sadd, name, *values)
    
    async def srem(self, name: str, *values: str) -> int:
        """Remove values from a set."""
        return await self._execute(self._client.srem, name, *values)
    
    async def smembers(self, name: str) -> set:
        """Get all members of a set."""
        return await self._execute(self._client.smembers, name)
    
    async def sismember(self, name: str, value: str) -> bool:
        """Check if value is a member of a set."""
        return await self._execute(self._client.sismember, name, value)
    
    # ==========================================================================
    # Pub/Sub Operations
    # ==========================================================================
    
    async def publish(self, channel: str, message: str) -> int:
        """
        Publish a message to a channel.
        
        Args:
            channel: The channel to publish to.
            message: The message to publish.
            
        Returns:
            Number of subscribers that received the message.
        """
        await self._ensure_connected()
        return await self._client.publish(channel, message)
    
    async def subscribe(self, *channels: str):
        """
        Subscribe to channels.
        
        Args:
            channels: Channels to subscribe to.
            
        Returns:
            PubSub object for receiving messages.
        """
        await self._ensure_connected()
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*channels)
        return pubsub
    
    # ==========================================================================
    # Distributed Locking
    # ==========================================================================
    
    async def acquire_lock(
        self,
        lock_name: str,
        timeout: int = 10,
        blocking: bool = True,
        blocking_timeout: Optional[float] = None,
    ) -> Optional[str]:
        """
        Acquire a distributed lock.
        
        Args:
            lock_name: Name of the lock.
            timeout: Lock expiry in seconds.
            blocking: Whether to block waiting for lock.
            blocking_timeout: Maximum time to wait for lock.
            
        Returns:
            Lock token if acquired, None otherwise.
        """
        await self._ensure_connected()
        
        import uuid
        token = str(uuid.uuid4())
        lock_key = f"lock:{lock_name}"
        
        if blocking:
            end_time = None
            if blocking_timeout:
                end_time = asyncio.get_event_loop().time() + blocking_timeout
            
            while True:
                if await self.set(lock_key, token, ex=timeout, nx=True):
                    return token
                
                if end_time and asyncio.get_event_loop().time() >= end_time:
                    return None
                
                await asyncio.sleep(0.1)
        else:
            if await self.set(lock_key, token, ex=timeout, nx=True):
                return token
            return None
    
    async def release_lock(self, lock_name: str, token: str) -> bool:
        """
        Release a distributed lock.
        
        Args:
            lock_name: Name of the lock.
            token: Lock token from acquire_lock.
            
        Returns:
            True if lock was released, False otherwise.
        """
        await self._ensure_connected()
        
        lock_key = f"lock:{lock_name}"
        
        # Use Lua script for atomic check-and-delete
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        
        result = await self._client.eval(script, 1, lock_key, token)
        return result == 1
    
    # ==========================================================================
    # Stream Operations (for Message Queue)
    # ==========================================================================
    
    async def xadd(
        self,
        name: str,
        fields: Dict[str, str],
        id: str = "*",
        maxlen: Optional[int] = None,
        approximate: bool = True,
    ) -> str:
        """
        Add an entry to a stream.
        
        Args:
            name: Stream name.
            fields: Field-value pairs to add.
            id: Entry ID (* for auto-generate).
            maxlen: Maximum stream length.
            approximate: Use approximate trimming.
            
        Returns:
            The entry ID.
        """
        await self._ensure_connected()
        return await self._client.xadd(
            name,
            fields,
            id=id,
            maxlen=maxlen,
            approximate=approximate,
        )
    
    async def xread(
        self,
        streams: Dict[str, str],
        count: Optional[int] = None,
        block: Optional[int] = None,
    ) -> List:
        """
        Read from streams.
        
        Args:
            streams: Dict of stream names to IDs.
            count: Maximum entries to return.
            block: Block timeout in milliseconds.
            
        Returns:
            List of stream entries.
        """
        await self._ensure_connected()
        return await self._client.xread(streams, count=count, block=block)
    
    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        """
        Create a consumer group.
        
        Args:
            name: Stream name.
            groupname: Consumer group name.
            id: Starting ID.
            mkstream: Create stream if doesn't exist.
            
        Returns:
            True if created successfully.
        """
        await self._ensure_connected()
        try:
            await self._client.xgroup_create(name, groupname, id=id, mkstream=mkstream)
            return True
        except RedisError as e:
            if "BUSYGROUP" in str(e):
                # Group already exists
                return True
            raise
    
    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Dict[str, str],
        count: Optional[int] = None,
        block: Optional[int] = None,
        noack: bool = False,
    ) -> List:
        """
        Read from streams as a consumer group member.
        
        Args:
            groupname: Consumer group name.
            consumername: Consumer name.
            streams: Dict of stream names to IDs.
            count: Maximum entries to return.
            block: Block timeout in milliseconds.
            noack: Don't add to pending entries list.
            
        Returns:
            List of stream entries.
        """
        await self._ensure_connected()
        return await self._client.xreadgroup(
            groupname,
            consumername,
            streams,
            count=count,
            block=block,
            noack=noack,
        )
    
    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        """
        Acknowledge stream entries.
        
        Args:
            name: Stream name.
            groupname: Consumer group name.
            ids: Entry IDs to acknowledge.
            
        Returns:
            Number of entries acknowledged.
        """
        await self._ensure_connected()
        return await self._client.xack(name, groupname, *ids)
    
    async def xlen(self, name: str) -> int:
        """Get the length of a stream."""
        await self._ensure_connected()
        return await self._client.xlen(name)
    
    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: Optional[int] = None,
    ) -> List:
        """Get a range of entries from a stream."""
        await self._ensure_connected()
        return await self._client.xrange(name, min=min, max=max, count=count)
    
    # ==========================================================================
    # Utility Methods
    # ==========================================================================
    
    async def ping(self) -> bool:
        """
        Ping the Redis server.
        
        Returns:
            True if server responds.
        """
        await self._ensure_connected()
        result = await self._client.ping()
        return result
    
    async def info(self, section: Optional[str] = None) -> Dict:
        """
        Get Redis server info.
        
        Args:
            section: Specific section to retrieve.
            
        Returns:
            Server info dictionary.
        """
        await self._ensure_connected()
        return await self._client.info(section)
    
    async def flushdb(self) -> bool:
        """Flush the current database. Use with caution!"""
        await self._ensure_connected()
        return await self._client.flushdb()
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._state.connected
    
    @property
    def connection_state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state


# =============================================================================
# Factory Function
# =============================================================================

async def create_redis_client(config: Optional[Dict] = None) -> RedisClient:
    """
    Factory function to create and connect a Redis client.
    
    Args:
        config: Configuration dictionary. If not provided, loads from parallel_settings.
        
    Returns:
        Connected RedisClient instance.
    """
    if config:
        # Use provided config but start from defaults
        redis_config = RedisConfig()
        redis_config.host = config.get("host", redis_config.host)
        redis_config.port = config.get("port", redis_config.port)
        redis_config.db = config.get("db", redis_config.db)
        redis_config.password = config.get("password", redis_config.password)
        
        pool_config = config.get("pool", {})
        redis_config.max_connections = pool_config.get("max_connections", redis_config.max_connections)
        redis_config.timeout = pool_config.get("timeout", redis_config.timeout)
        redis_config.retry_on_timeout = pool_config.get("retry_on_timeout", redis_config.retry_on_timeout)
    else:
        # Load robustly from parallel_settings (respects REDIS_URL/Railway vars)
        redis_config = RedisConfig.from_parallel_settings()
    
    client = RedisClient(redis_config)
    await client.connect()
    return client
