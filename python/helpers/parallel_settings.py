from __future__ import annotations
"""
Parallel Settings for AGIX Parallel Swarm

This module provides configuration loading for parallel execution features.
It loads settings from conf/parallel_config.yaml and provides typed access.
"""

import os
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from urllib.parse import urlparse

from python.helpers import files

logger = logging.getLogger(__name__)

# Path to parallel config file
PARALLEL_CONFIG_FILE = files.get_abs_path("conf/parallel_config.yaml")

# Global cached settings
_parallel_settings: Optional["ParallelSettings"] = None


@dataclass
class RedisPoolConfig:
    """Redis connection pool configuration."""
    max_connections: int = 100  # RCA Phase 2 (P2-1): scaled from 50 for 50-agent swarms
    timeout: int = 30           # RCA Phase 2 (P2-1): scaled from 10 for parallel workloads
    retry_on_timeout: bool = True


@dataclass
class RedisStreamsConfig:
    """Redis Streams configuration."""
    task_queue: str = "agix:tasks"
    result_queue: str = "agix:results"
    error_queue: str = "agix:errors"
    consumer_group: str = "agix-workers"
    max_length: int = 10000
    block_timeout: int = 5000


@dataclass
class RedisConfig:
    """Redis configuration."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    pool: RedisPoolConfig = field(default_factory=RedisPoolConfig)
    streams: RedisStreamsConfig = field(default_factory=RedisStreamsConfig)


@dataclass
class MilvusConnectionConfig:
    """Milvus connection configuration."""
    timeout: int = 30
    retry_attempts: int = 3


@dataclass
class MilvusConfig:
    """Milvus vector database configuration."""
    host: str = "localhost"
    port: int = 19530
    collection_name: str = "agent_memories"
    embedding_dim: int = 384
    index_type: str = "IVF_FLAT"
    index_params: Dict[str, Any] = field(default_factory=lambda: {"nlist": 1024})
    search_params: Dict[str, Any] = field(default_factory=lambda: {"nprobe": 16})
    connection: MilvusConnectionConfig = field(default_factory=MilvusConnectionConfig)


@dataclass
class PrivateMemoryConfig:
    """Private memory tier configuration."""
    max_entries: int = 1000
    ttl: int = 0


@dataclass
class SharedMemoryTierConfig:
    """Shared memory tier configuration."""
    max_entries: int = 10000
    ttl: int = 0
    provenance_tracking: bool = True
    broadcast_enabled: bool = True


@dataclass
class RetrievalConfig:
    """Memory retrieval configuration."""
    top_k: int = 10
    min_similarity: float = 0.5
    perspective_boost: bool = True
    own_memory_boost: float = 1.2


@dataclass
class SharedMemoryConfig:
    """Shared memory system configuration."""
    two_tier_enabled: bool = True
    private: PrivateMemoryConfig = field(default_factory=PrivateMemoryConfig)
    shared: SharedMemoryTierConfig = field(default_factory=SharedMemoryTierConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)


@dataclass
class WorkerProfileConfig:
    """Worker profile configuration."""
    model: str = "default"
    reasoning_model: Optional[str] = None
    execution_model: Optional[str] = None
    vision_model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 16384


@dataclass
class AgentPoolConfig:
    """Agent pool configuration."""
    semaphore_limit: int = 5
    health_check_interval: int = 30
    max_consecutive_failures: int = 3
    profiles: Dict[str, WorkerProfileConfig] = field(default_factory=dict)


@dataclass
class RetryStrategyConfig:
    """Individual retry strategy configuration."""
    base_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: bool = True
    increment: float = 2.0
    initial_delay: float = 1.0
    success_decrease: float = 0.8
    failure_increase: float = 1.5
    min_delay: float = 0.5


@dataclass
class RetryConfig:
    """Retry configuration."""
    default_strategy: str = "exponential"
    max_attempts: int = 3
    strategies: Dict[str, RetryStrategyConfig] = field(default_factory=dict)
    retryable_errors: List[str] = field(default_factory=list)
    non_retryable_errors: List[str] = field(default_factory=list)


@dataclass
class EscalationConfig:
    """Error escalation configuration."""
    alternative_worker: bool = True
    alternative_model: bool = True
    human_escalation: bool = True
    human_escalation_timeout: int = 600


@dataclass
class DeadLetterConfig:
    """Dead letter queue configuration."""
    enabled: bool = True
    max_entries: int = 1000
    retention_days: int = 7


@dataclass
class ErrorRecoveryConfig:
    """Error recovery configuration."""
    challenger_enabled: bool = True
    inspector_enabled: bool = True
    escalation: EscalationConfig = field(default_factory=EscalationConfig)
    dead_letter: DeadLetterConfig = field(default_factory=DeadLetterConfig)


@dataclass
class AdaptiveCircuitConfig:
    """Adaptive circuit breaker configuration."""
    window_size: int = 100
    min_requests: int = 10
    failure_rate_threshold: float = 0.5

@dataclass
class AutoScalingConfig:
    """Auto-scaling configuration."""
    enabled: bool = True
    min_workers: int = 2
    max_workers: int = 20
    target_queue_depth: int = 5
    target_latency_p95_ms: int = 5000
    scale_up_threshold: float = 0.8
    scale_down_threshold: float = 0.3
    cooldown_seconds: int = 60
    scale_up_increment: int = 2
    scale_down_increment: int = 1
    evaluation_interval: int = 30
    metrics_history_size: int = 60


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    enabled: bool = True
    failure_threshold: int = 5
    success_threshold: int = 3
    recovery_timeout: int = 60
    adaptive_enabled: bool = True
    adaptive: AdaptiveCircuitConfig = field(default_factory=AdaptiveCircuitConfig)


@dataclass
class AnomalyDetectionConfig:
    """Anomaly detection configuration."""
    action_repetition_threshold: int = 3
    reasoning_loop_threshold: int = 5
    progress_stall_threshold: int = 120
    contradiction_detection: bool = True


@dataclass
class AlertsConfig:
    """Observer alerts configuration."""
    log_enabled: bool = True
    auto_recovery: bool = True
    notify_orchestrator: bool = True


@dataclass
class ObserverMeshConfig:
    """Observer mesh configuration."""
    enabled: bool = True
    observation_interval: int = 10
    anomaly_detection: AnomalyDetectionConfig = field(default_factory=AnomalyDetectionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)


@dataclass
class MetricsConfig:
    """Prometheus metrics configuration."""
    enabled: bool = True
    endpoint: str = "/metrics"
    port: int = 8000
    latency_buckets: List[float] = field(default_factory=lambda: [0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0])


@dataclass
class TracingConfig:
    """Distributed tracing configuration."""
    enabled: bool = True
    jaeger_host: str = "localhost"
    jaeger_port: int = 6831
    service_name: str = "agix"
    sampling_rate: float = 1.0


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_parallel_execution: bool = True
    log_memory_operations: bool = False
    log_retry_attempts: bool = True


@dataclass
class ParallelModeConfig:
    """Parallel mode master configuration."""
    enabled: bool = False
    max_concurrent_workers: int = 5
    default_task_timeout: int = 300
    wave_execution: bool = True
    max_waves: int = 10


@dataclass
class ParallelSettings:
    """Complete parallel settings configuration."""
    parallel: ParallelModeConfig = field(default_factory=ParallelModeConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)
    shared_memory: SharedMemoryConfig = field(default_factory=SharedMemoryConfig)
    agent_pool: AgentPoolConfig = field(default_factory=AgentPoolConfig)
    auto_scaling: AutoScalingConfig = field(default_factory=AutoScalingConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    error_recovery: ErrorRecoveryConfig = field(default_factory=ErrorRecoveryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    observer_mesh: ObserverMeshConfig = field(default_factory=ObserverMeshConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _load_yaml_config() -> Dict[str, Any]:
    """Load configuration from YAML file."""
    if not os.path.exists(PARALLEL_CONFIG_FILE):
        logger.warning(f"Parallel config file not found: {PARALLEL_CONFIG_FILE}")
        return {}
    
    try:
        with open(PARALLEL_CONFIG_FILE, 'r') as f:
            config = yaml.safe_load(f)
            return config or {}
    except Exception as e:
        logger.error(f"Failed to load parallel config: {e}")
        return {}


def _parse_nested_config(data: Dict[str, Any], config_class: type, prefix: str = "") -> Any:
    """Parse nested configuration into dataclass."""
    if not data:
        return config_class()
    
    kwargs = {}
    for field_name, field_type in config_class.__dataclass_fields__.items():
        if field_name in data:
            value = data[field_name]
            
            # Check if field type is a dataclass
            if hasattr(field_type.type, '__dataclass_fields__'):
                kwargs[field_name] = _parse_nested_config(value, field_type.type, f"{prefix}.{field_name}")
            elif hasattr(field_type.type, '__origin__') and field_type.type.__origin__ is dict:
                # Handle Dict types
                kwargs[field_name] = value
            elif hasattr(field_type.type, '__origin__') and field_type.type.__origin__ is list:
                # Handle List types
                kwargs[field_name] = value
            else:
                kwargs[field_name] = value
    
    return config_class(**kwargs)


def _parse_parallel_settings(data: Dict[str, Any]) -> ParallelSettings:
    """Parse raw YAML data into ParallelSettings."""
    settings = ParallelSettings()
    
    if "parallel" in data:
        settings.parallel = _parse_nested_config(data["parallel"], ParallelModeConfig)
    
    if "redis" in data:
        redis_data = data["redis"]
        settings.redis = RedisConfig(
            host=redis_data.get("host", "localhost"),
            port=redis_data.get("port", 6379),
            db=redis_data.get("db", 0),
            password=redis_data.get("password", ""),
            pool=_parse_nested_config(redis_data.get("pool", {}), RedisPoolConfig),
            streams=_parse_nested_config(redis_data.get("streams", {}), RedisStreamsConfig),
        )
    
    if "milvus" in data:
        milvus_data = data["milvus"]
        settings.milvus = MilvusConfig(
            host=milvus_data.get("host", "localhost"),
            port=milvus_data.get("port", 19530),
            collection_name=milvus_data.get("collection_name", "agent_memories"),
            embedding_dim=milvus_data.get("embedding_dim", 384),
            index_type=milvus_data.get("index_type", "IVF_FLAT"),
            index_params=milvus_data.get("index_params", {"nlist": 1024}),
            search_params=milvus_data.get("search_params", {"nprobe": 16}),
            connection=_parse_nested_config(milvus_data.get("connection", {}), MilvusConnectionConfig),
        )
    
    if "shared_memory" in data:
        sm_data = data["shared_memory"]
        settings.shared_memory = SharedMemoryConfig(
            two_tier_enabled=sm_data.get("two_tier_enabled", True),
            private=_parse_nested_config(sm_data.get("private", {}), PrivateMemoryConfig),
            shared=_parse_nested_config(sm_data.get("shared", {}), SharedMemoryTierConfig),
            retrieval=_parse_nested_config(sm_data.get("retrieval", {}), RetrievalConfig),
        )
    
    if "agent_pool" in data:
        pool_data = data["agent_pool"]
        profiles = {}
        for name, profile_data in pool_data.get("profiles", {}).items():
            profiles[name] = WorkerProfileConfig(
                model=profile_data.get("model", "default"),
                reasoning_model=profile_data.get("reasoning_model"),
                execution_model=profile_data.get("execution_model"),
                vision_model=profile_data.get("vision_model"),
                temperature=profile_data.get("temperature", 0.7),
                max_tokens=profile_data.get("max_tokens", 16384),
            )
        settings.agent_pool = AgentPoolConfig(
            semaphore_limit=pool_data.get("semaphore_limit", 5),
            health_check_interval=pool_data.get("health_check_interval", 30),
            max_consecutive_failures=pool_data.get("max_consecutive_failures", 3),
            profiles=profiles,
        )
    
    if "retry" in data:
        retry_data = data["retry"]
        strategies = {}
        for name, strategy_data in retry_data.get("strategies", {}).items():
            strategies[name] = RetryStrategyConfig(
                base_delay=strategy_data.get("base_delay", 1.0),
                max_delay=strategy_data.get("max_delay", 60.0),
                multiplier=strategy_data.get("multiplier", 2.0),
                jitter=strategy_data.get("jitter", True),
            )
        settings.retry = RetryConfig(
            default_strategy=retry_data.get("default_strategy", "exponential"),
            max_attempts=retry_data.get("max_attempts", 3),
            strategies=strategies,
            retryable_errors=retry_data.get("retryable_errors", []),
            non_retryable_errors=retry_data.get("non_retryable_errors", []),
        )
    
    if "error_recovery" in data:
        er_data = data["error_recovery"]
        settings.error_recovery = ErrorRecoveryConfig(
            challenger_enabled=er_data.get("challenger_enabled", True),
            inspector_enabled=er_data.get("inspector_enabled", True),
            escalation=_parse_nested_config(er_data.get("escalation", {}), EscalationConfig),
            dead_letter=_parse_nested_config(er_data.get("dead_letter", {}), DeadLetterConfig),
        )
    
    if "circuit_breaker" in data:
        cb_data = data["circuit_breaker"]
        settings.circuit_breaker = CircuitBreakerConfig(
            enabled=cb_data.get("enabled", True),
            failure_threshold=cb_data.get("failure_threshold", 5),
            success_threshold=cb_data.get("success_threshold", 3),
            recovery_timeout=cb_data.get("recovery_timeout", 60),
            adaptive_enabled=cb_data.get("adaptive_enabled", True),
            adaptive=_parse_nested_config(cb_data.get("adaptive", {}), AdaptiveCircuitConfig),
        )
    
    if "observer_mesh" in data:
        om_data = data["observer_mesh"]
        settings.observer_mesh = ObserverMeshConfig(
            enabled=om_data.get("enabled", True),
            observation_interval=om_data.get("observation_interval", 10),
            anomaly_detection=_parse_nested_config(om_data.get("anomaly_detection", {}), AnomalyDetectionConfig),
            alerts=_parse_nested_config(om_data.get("alerts", {}), AlertsConfig),
        )
    
    if "metrics" in data:
        settings.metrics = _parse_nested_config(data["metrics"], MetricsConfig)
    
    if "tracing" in data:
        settings.tracing = _parse_nested_config(data["tracing"], TracingConfig)
    
    if "auto_scaling" in data:
        settings.auto_scaling = _parse_nested_config(data["auto_scaling"], AutoScalingConfig)
    
    if "logging" in data:
        settings.logging = _parse_nested_config(data["logging"], LoggingConfig)
    
    return settings


def get_parallel_settings() -> ParallelSettings:
    """
    Get parallel settings, loading from file if not cached.
    
    Returns:
        ParallelSettings instance.
    """
    global _parallel_settings
    
    if _parallel_settings is None:
        data = _load_yaml_config()
        _parallel_settings = _parse_parallel_settings(data)
        
        # Apply environment-driven overrides for performance tiering (#679)
        from python.helpers import settings as settings_helper
        performance_tier = settings_helper.get_settings().get("performance_tier", "standard")
        
        if os.getenv("RAILWAY_MAX_PERF", "").lower() == "true" or performance_tier == "max":
            logger.info(f"Performance tier '{performance_tier}' detected (ENV RAILWAY_MAX_PERF={os.getenv('RAILWAY_MAX_PERF')}). Applying high-performance overrides.")
            
            # Scale up concurrency
            _parallel_settings.parallel.max_concurrent_workers = 50
            _parallel_settings.agent_pool.semaphore_limit = 50
            
            # Update auto-scaling limits for high performance
            _parallel_settings.auto_scaling.max_workers = 50
            _parallel_settings.auto_scaling.scale_up_increment = 5
            _parallel_settings.auto_scaling.cooldown_seconds = 15
            
            # Harden infrastructure for high volume
            _parallel_settings.redis.streams.max_length = 50000
            _parallel_settings.shared_memory.shared.max_entries = 100000
            
            # Adjust retry strategy for concurrency
            if "exponential" in _parallel_settings.retry.strategies:
                _parallel_settings.retry.strategies["exponential"].max_delay = 120.0
                _parallel_settings.retry.strategies["exponential"].base_delay = 2.0
        
        logger.info(f"Loaded parallel settings (enabled={_parallel_settings.parallel.enabled})")
    
    return _parallel_settings


def reload_parallel_settings() -> ParallelSettings:
    """
    Force reload of parallel settings from file.
    
    Returns:
        Fresh ParallelSettings instance.
    """
    global _parallel_settings
    _parallel_settings = None
    return get_parallel_settings()


def is_parallel_enabled() -> bool:
    """
    Check if parallel mode is enabled.
    
    Returns:
        True if parallel mode is enabled.
    """
    settings = get_parallel_settings()
    return settings.parallel.enabled


def get_redis_config() -> Dict[str, Any]:
    """
    Get Redis configuration as dictionary with robust environment variable support.
    Prioritizes REDIS_URL, then Railway-specific names, then standard names.
    
    Returns:
        Redis configuration dictionary.
    """
    settings = get_parallel_settings()
    
    # 1. Try REDIS_URL (Common in Railway and other Heroku-like platforms)
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            parsed = urlparse(redis_url)
            return {
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 6379,
                "db": int(parsed.path[1:]) if parsed.path and len(parsed.path) > 1 else 0,
                "password": parsed.password or "",
                "pool": {
                    "max_connections": int(os.getenv("REDIS_MAX_CONNECTIONS", settings.redis.pool.max_connections)),
                    "timeout": int(os.getenv("REDIS_TIMEOUT", settings.redis.pool.timeout)),
                    "retry_on_timeout": os.getenv("REDIS_RETRY_ON_TIMEOUT", str(settings.redis.pool.retry_on_timeout)).lower() == "true",
                },
            }
        except Exception as e:
            logger.error(f"Failed to parse REDIS_URL: {e}. Falling back to individual variables.")

    # 2. Try individual variables (prioritize Railway's conventions over standard)
    host = os.getenv("REDISHOST") or os.getenv("REDIS_HOST") or settings.redis.host
    port = int(os.getenv("REDISPORT") or os.getenv("REDIS_PORT") or settings.redis.port)
    db = int(os.getenv("REDIS_DB", settings.redis.db))
    password = os.getenv("REDISPW") or os.getenv("REDIS_PASSWORD") or settings.redis.password
    
    return {
        "host": host,
        "port": port,
        "db": db,
        "password": password,
        "pool": {
            "max_connections": int(os.getenv("REDIS_MAX_CONNECTIONS", settings.redis.pool.max_connections)),
            "timeout": int(os.getenv("REDIS_TIMEOUT", settings.redis.pool.timeout)),
            "retry_on_timeout": os.getenv("REDIS_RETRY_ON_TIMEOUT", str(settings.redis.pool.retry_on_timeout)).lower() == "true",
        },
    }


def get_queue_config() -> Dict[str, Any]:
    """
    Get message queue configuration as dictionary.
    
    Returns:
        Queue configuration dictionary.
    """
    settings = get_parallel_settings()
    return {
        "task_queue": settings.redis.streams.task_queue,
        "result_queue": settings.redis.streams.result_queue,
        "error_queue": settings.redis.streams.error_queue,
        "consumer_group": settings.redis.streams.consumer_group,
        "max_length": settings.redis.streams.max_length,
        "block_timeout": settings.redis.streams.block_timeout,
    }


def get_shared_memory_config() -> Dict[str, Any]:
    """
    Get shared memory configuration as dictionary.
    
    Returns:
        Shared memory configuration dictionary.
    """
    settings = get_parallel_settings()
    return {
        "two_tier_enabled": settings.shared_memory.two_tier_enabled,
        "private": {
            "max_entries": settings.shared_memory.private.max_entries,
            "ttl": settings.shared_memory.private.ttl,
        },
        "shared": {
            "max_entries": settings.shared_memory.shared.max_entries,
            "ttl": settings.shared_memory.shared.ttl,
            "provenance_tracking": settings.shared_memory.shared.provenance_tracking,
            "broadcast_enabled": settings.shared_memory.shared.broadcast_enabled,
        },
        "retrieval": {
            "top_k": settings.shared_memory.retrieval.top_k,
            "min_similarity": settings.shared_memory.retrieval.min_similarity,
            "perspective_boost": settings.shared_memory.retrieval.perspective_boost,
            "own_memory_boost": settings.shared_memory.retrieval.own_memory_boost,
        },
    }


def get_agent_pool_config() -> Dict[str, Any]:
    """
    Get agent pool configuration as dictionary.
    
    Returns:
        Agent pool configuration dictionary.
    """
    settings = get_parallel_settings()
    profiles = {}
    for name, profile in settings.agent_pool.profiles.items():
        profiles[name] = {
            "model": profile.model,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
        }
    
    return {
        "semaphore_limit": settings.agent_pool.semaphore_limit,
        "health_check_interval": settings.agent_pool.health_check_interval,
        "max_consecutive_failures": settings.agent_pool.max_consecutive_failures,
        "profiles": profiles,
    }
