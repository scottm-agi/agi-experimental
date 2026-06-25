from __future__ import annotations
"""
Auto-scaling for AGIX Parallel Swarm

This module provides automatic worker pool scaling based on:
- Queue depth metrics
- Latency metrics (p95)
- Utilization percentage
- Predictive load patterns

Integrates with AgentPool for scaling operations and Redis for metrics collection.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ScalingDirection(Enum):
    """Direction of scaling action."""
    UP = "up"
    DOWN = "down"
    NONE = "none"


@dataclass
class ScalingPolicy:
    """
    Configuration for auto-scaling behavior.
    
    Attributes:
        enabled: Whether auto-scaling is enabled
        min_workers: Minimum number of workers to maintain
        max_workers: Maximum number of workers allowed
        target_queue_depth: Target tasks per worker
        target_latency_p95_ms: Target p95 latency in milliseconds
        scale_up_threshold: Utilization threshold to trigger scale up (0.0-1.0)
        scale_down_threshold: Utilization threshold to trigger scale down (0.0-1.0)
        cooldown_seconds: Minimum seconds between scaling actions
        scale_up_increment: Number of workers to add when scaling up
        scale_down_increment: Number of workers to remove when scaling down
    """
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


@dataclass
class ScalingMetrics:
    """
    Current metrics used for scaling decisions.
    
    Attributes:
        queue_depth: Number of tasks in queue
        active_workers: Number of workers currently processing tasks
        total_workers: Total number of workers in pool
        latency_p95_ms: 95th percentile latency in milliseconds
        latency_p50_ms: 50th percentile latency in milliseconds
        utilization: Worker utilization ratio (0.0-1.0)
        tasks_per_minute: Task throughput rate
        error_rate: Error rate (0.0-1.0)
        timestamp: When metrics were collected
    """
    queue_depth: int = 0
    active_workers: int = 0
    total_workers: int = 0
    latency_p95_ms: int = 0
    latency_p50_ms: int = 0
    utilization: float = 0.0
    tasks_per_minute: int = 0
    error_rate: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ScalingDecision:
    """
    Result of scaling evaluation.
    
    Attributes:
        direction: Whether to scale up, down, or no change
        current_workers: Current number of workers
        target_workers: Target number of workers after scaling
        reason: Human-readable reason for the decision
        trigger: Which metric triggered the decision
        timestamp: When decision was made
    """
    direction: ScalingDirection
    current_workers: int
    target_workers: int
    reason: str
    trigger: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class AutoScaler:
    """
    Automatic worker pool scaling controller.
    
    Features:
    - Queue depth-based scaling
    - Latency-based scaling
    - Utilization-based scaling
    - Predictive load detection
    - Cooldown enforcement
    - Min/max bounds enforcement
    
    Example:
        ```python
        scaler = AutoScaler(
            pool=agent_pool,
            redis_client=redis,
            policy=ScalingPolicy(min_workers=2, max_workers=20),
            profile_name="default",
        )
        await scaler.start()
        ```
    """
    
    def __init__(
        self,
        pool: Any,  # AgentPool
        redis_client: Optional[Any] = None,  # RedisClient
        policy: Optional[ScalingPolicy] = None,
        profile_name: str = "default",
        evaluation_interval: int = 30,
        metrics_history_size: int = 60,
    ):
        """
        Initialize AutoScaler.
        
        Args:
            pool: AgentPool instance to scale
            redis_client: Redis client for metrics collection
            policy: Scaling policy configuration
            profile_name: Worker profile to scale
            evaluation_interval: Seconds between scaling evaluations
            metrics_history_size: Number of historical metrics to retain
        """
        self.pool = pool
        self.redis_client = redis_client
        self.policy = policy or ScalingPolicy()
        self._profile_name = profile_name
        self._evaluation_interval = evaluation_interval
        self._metrics_history_size = metrics_history_size
        
        # State
        self._running = False
        self._scaling_task: Optional[asyncio.Task] = None
        self._last_scaling_time: float = 0
        self._metrics_history: List[ScalingMetrics] = []
        self._scaling_history: List[ScalingDecision] = []
        
        # Queue name for Redis (if available)
        self._queue_name = "agix:tasks"
    
    # =========================================================================
    # Lifecycle Management
    # =========================================================================
    
    async def start(self) -> None:
        """Start the auto-scaling loop."""
        if not self.policy.enabled:
            logger.info("Auto-scaling is disabled, not starting scaling loop")
            return
        
        if self._running:
            logger.warning("AutoScaler already running")
            return
        
        self._running = True
        self._scaling_task = asyncio.create_task(self._scaling_loop())
        logger.info(
            f"AutoScaler started for profile '{self._profile_name}' "
            f"(min={self.policy.min_workers}, max={self.policy.max_workers})"
        )
    
    async def stop(self) -> None:
        """Stop the auto-scaling loop."""
        self._running = False
        
        if self._scaling_task and not self._scaling_task.done():
            self._scaling_task.cancel()
            try:
                await self._scaling_task
            except asyncio.CancelledError:
                logger.debug("[AutoScaler] Scaling task cancelled during stop")
        
        logger.info("AutoScaler stopped")
    
    async def _scaling_loop(self) -> None:
        """Background task for periodic scaling evaluation."""
        while self._running:
            try:
                # Collect metrics
                metrics = await self._collect_metrics()
                
                # Store in history
                self._metrics_history.append(metrics)
                if len(self._metrics_history) > self._metrics_history_size:
                    self._metrics_history.pop(0)
                
                # Evaluate scaling decision
                decision = self._evaluate_scaling(metrics)
                
                # Execute if needed
                if decision.direction != ScalingDirection.NONE:
                    await self._execute_scaling(decision)
                    self._scaling_history.append(decision)
                    
                    # Trim history
                    if len(self._scaling_history) > 100:
                        self._scaling_history.pop(0)
                
                # Wait for next evaluation
                await asyncio.sleep(self._evaluation_interval)
                
            except asyncio.CancelledError:
                logger.debug("[AutoScaler] Scaling loop cancelled — shutting down gracefully")
                break
            except Exception as e:
                logger.error(f"Error in scaling loop: {e}")
                await asyncio.sleep(self._evaluation_interval)
    
    # =========================================================================
    # Metrics Collection
    # =========================================================================
    
    async def _collect_metrics(self) -> ScalingMetrics:
        """
        Collect current metrics from pool and Redis.
        
        Returns:
            ScalingMetrics with current values.
        """
        # Get pool status
        pool_status = self.pool.get_pool_status()
        
        # Count active (busy) workers
        workers_by_state = pool_status.get("workers_by_state", {})
        active_workers = workers_by_state.get("busy", 0)
        total_workers = pool_status.get("total_workers", 0)
        
        # Calculate utilization
        utilization = active_workers / total_workers if total_workers > 0 else 0.0
        
        # Get queue depth from Redis if available
        queue_depth = 0
        if self.redis_client:
            try:
                queue_depth = await self.redis_client.xlen(self._queue_name)
            except Exception as e:
                logger.warning(f"Failed to get queue depth: {e}")
        
        # Get latency metrics from Redis if available
        latency_p95 = 0
        latency_p50 = 0
        if self.redis_client:
            try:
                latency_data = await self.redis_client.get_json("agix:metrics:latency")
                if latency_data:
                    latency_p95 = latency_data.get("p95", 0)
                    latency_p50 = latency_data.get("p50", 0)
            except Exception as e:
                logger.warning(f"Failed to get latency metrics: {e}")
        
        # Calculate tasks per minute from python.history
        tasks_per_minute = 0
        if len(self._metrics_history) >= 2:
            recent = self._metrics_history[-1]
            older = self._metrics_history[-min(6, len(self._metrics_history))]
            time_diff = recent.timestamp - older.timestamp
            if time_diff > 0:
                # Estimate from queue depth changes
                tasks_per_minute = int(60 / time_diff * abs(recent.queue_depth - older.queue_depth))
        
        # Calculate error rate
        total_tasks = pool_status.get("total_tasks_submitted", 0)
        failed_tasks = pool_status.get("total_tasks_failed", 0)
        error_rate = failed_tasks / total_tasks if total_tasks > 0 else 0.0
        
        return ScalingMetrics(
            queue_depth=queue_depth,
            active_workers=active_workers,
            total_workers=total_workers,
            latency_p95_ms=latency_p95,
            latency_p50_ms=latency_p50,
            utilization=utilization,
            tasks_per_minute=tasks_per_minute,
            error_rate=error_rate,
            timestamp=time.time(),
        )
    
    # =========================================================================
    # Scaling Evaluation
    # =========================================================================
    
    def _evaluate_scaling(self, metrics: ScalingMetrics) -> ScalingDecision:
        """
        Evaluate whether scaling is needed based on current metrics.
        
        Args:
            metrics: Current scaling metrics.
            
        Returns:
            ScalingDecision with direction and target.
        """
        current_workers = metrics.total_workers
        
        # Check cooldown
        time_since_last_scaling = time.time() - self._last_scaling_time
        if time_since_last_scaling < self.policy.cooldown_seconds:
            return ScalingDecision(
                direction=ScalingDirection.NONE,
                current_workers=current_workers,
                target_workers=current_workers,
                reason=f"In cooldown period ({int(self.policy.cooldown_seconds - time_since_last_scaling)}s remaining)",
                trigger=None,
            )
        
        # Check for scale up triggers (in priority order)
        scale_up_decision = self._check_scale_up(metrics, current_workers)
        if scale_up_decision:
            return scale_up_decision
        
        # Check for scale down triggers
        scale_down_decision = self._check_scale_down(metrics, current_workers)
        if scale_down_decision:
            return scale_down_decision
        
        # No scaling needed
        return ScalingDecision(
            direction=ScalingDirection.NONE,
            current_workers=current_workers,
            target_workers=current_workers,
            reason="Metrics within acceptable range",
            trigger=None,
        )
    
    def _check_scale_up(
        self, metrics: ScalingMetrics, current_workers: int
    ) -> Optional[ScalingDecision]:
        """
        Check if scale up is needed.
        
        Args:
            metrics: Current metrics.
            current_workers: Current worker count.
            
        Returns:
            ScalingDecision if scale up needed, None otherwise.
        """
        # Already at max
        if current_workers >= self.policy.max_workers:
            return None
        
        target_workers = min(
            current_workers + self.policy.scale_up_increment,
            self.policy.max_workers
        )
        
        # Check utilization threshold
        if metrics.utilization > self.policy.scale_up_threshold:
            return ScalingDecision(
                direction=ScalingDirection.UP,
                current_workers=current_workers,
                target_workers=target_workers,
                reason=f"High utilization ({metrics.utilization:.1%} > {self.policy.scale_up_threshold:.1%})",
                trigger="utilization",
            )
        
        # Check latency threshold
        if metrics.latency_p95_ms > self.policy.target_latency_p95_ms:
            return ScalingDecision(
                direction=ScalingDirection.UP,
                current_workers=current_workers,
                target_workers=target_workers,
                reason=f"High latency (p95={metrics.latency_p95_ms}ms > {self.policy.target_latency_p95_ms}ms)",
                trigger="latency",
            )
        
        # Check queue depth threshold
        # Scale up if queue > target × workers × 1.5
        queue_threshold = self.policy.target_queue_depth * current_workers * 1.5
        if metrics.queue_depth > queue_threshold:
            return ScalingDecision(
                direction=ScalingDirection.UP,
                current_workers=current_workers,
                target_workers=target_workers,
                reason=f"High queue depth ({metrics.queue_depth} > {queue_threshold:.0f})",
                trigger="queue_depth",
            )
        
        # Check predictive scaling
        prediction = self._predict_load()
        if prediction.get("should_preemptive_scale"):
            return ScalingDecision(
                direction=ScalingDirection.UP,
                current_workers=current_workers,
                target_workers=target_workers,
                reason=f"Predictive scaling (trend: {prediction.get('trend')})",
                trigger="predictive",
            )
        
        return None
    
    def _check_scale_down(
        self, metrics: ScalingMetrics, current_workers: int
    ) -> Optional[ScalingDecision]:
        """
        Check if scale down is needed.
        
        Args:
            metrics: Current metrics.
            current_workers: Current worker count.
            
        Returns:
            ScalingDecision if scale down needed, None otherwise.
        """
        # Already at min
        if current_workers <= self.policy.min_workers:
            return None
        
        target_workers = max(
            current_workers - self.policy.scale_down_increment,
            self.policy.min_workers
        )
        
        # Check utilization threshold
        if metrics.utilization < self.policy.scale_down_threshold:
            return ScalingDecision(
                direction=ScalingDirection.DOWN,
                current_workers=current_workers,
                target_workers=target_workers,
                reason=f"Low utilization ({metrics.utilization:.1%} < {self.policy.scale_down_threshold:.1%})",
                trigger="utilization",
            )
        
        # Check queue depth threshold
        # Scale down if queue < target × workers × 0.3
        queue_threshold = self.policy.target_queue_depth * current_workers * 0.3
        if metrics.queue_depth < queue_threshold:
            return ScalingDecision(
                direction=ScalingDirection.DOWN,
                current_workers=current_workers,
                target_workers=target_workers,
                reason=f"Low queue depth ({metrics.queue_depth} < {queue_threshold:.0f})",
                trigger="queue_depth",
            )
        
        return None
    
    # =========================================================================
    # Predictive Load Detection
    # =========================================================================
    
    def _predict_load(self) -> Dict[str, Any]:
        """
        Analyze historical metrics to predict load trends.
        
        Returns:
            Dictionary with trend analysis and scaling recommendation.
        """
        if len(self._metrics_history) < 3:
            return {
                "trend": "unknown",
                "should_preemptive_scale": False,
                "confidence": 0.0,
            }
        
        # Get recent metrics
        recent_metrics = self._metrics_history[-5:] if len(self._metrics_history) >= 5 else self._metrics_history
        
        # Calculate queue depth trend
        queue_depths = [m.queue_depth for m in recent_metrics]
        utilizations = [m.utilization for m in recent_metrics]
        
        # Simple linear regression for trend
        n = len(queue_depths)
        if n < 2:
            return {
                "trend": "unknown",
                "should_preemptive_scale": False,
                "confidence": 0.0,
            }
        
        # Calculate slope for queue depth
        x_mean = (n - 1) / 2
        y_mean = sum(queue_depths) / n
        
        numerator = sum((i - x_mean) * (queue_depths[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            queue_slope = 0
        else:
            queue_slope = numerator / denominator
        
        # Calculate slope for utilization
        u_mean = sum(utilizations) / n
        u_numerator = sum((i - x_mean) * (utilizations[i] - u_mean) for i in range(n))
        
        if denominator == 0:
            util_slope = 0
        else:
            util_slope = u_numerator / denominator
        
        # Determine trend
        # Significant increase: slope > 10% of mean per sample
        queue_threshold = y_mean * 0.1 if y_mean > 0 else 1
        util_threshold = 0.05  # 5% utilization change per sample
        
        if queue_slope > queue_threshold or util_slope > util_threshold:
            trend = "increasing"
            # Preemptive scale if utilization is already at or above 60%
            should_scale = u_mean >= 0.6
        elif queue_slope < -queue_threshold or util_slope < -util_threshold:
            trend = "decreasing"
            should_scale = False
        else:
            trend = "stable"
            should_scale = False
        
        # Calculate confidence based on consistency
        confidence = min(1.0, n / 5)  # More samples = higher confidence
        
        return {
            "trend": trend,
            "should_preemptive_scale": should_scale,
            "confidence": confidence,
            "queue_slope": queue_slope,
            "util_slope": util_slope,
        }
    
    # =========================================================================
    # Scaling Execution
    # =========================================================================
    
    async def _execute_scaling(self, decision: ScalingDecision) -> None:
        """
        Execute a scaling decision.
        
        Args:
            decision: The scaling decision to execute.
        """
        if decision.direction == ScalingDirection.NONE:
            return
        
        logger.info(
            f"Executing scaling: {decision.direction.value} "
            f"({decision.current_workers} -> {decision.target_workers}) "
            f"Reason: {decision.reason}"
        )
        
        try:
            await self.pool.scale_workers(self._profile_name, decision.target_workers)
            self._last_scaling_time = time.time()
            
            logger.info(
                f"Scaling complete: {self._profile_name} now has {decision.target_workers} workers"
            )
        except Exception as e:
            logger.error(f"Failed to execute scaling: {e}")
    
    # =========================================================================
    # Status and Metrics Export
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current AutoScaler status.
        
        Returns:
            Dictionary with status information.
        """
        return {
            "enabled": self.policy.enabled,
            "running": self._running,
            "profile_name": self._profile_name,
            "policy": {
                "min_workers": self.policy.min_workers,
                "max_workers": self.policy.max_workers,
                "target_queue_depth": self.policy.target_queue_depth,
                "target_latency_p95_ms": self.policy.target_latency_p95_ms,
                "scale_up_threshold": self.policy.scale_up_threshold,
                "scale_down_threshold": self.policy.scale_down_threshold,
                "cooldown_seconds": self.policy.cooldown_seconds,
            },
            "last_scaling_time": self._last_scaling_time,
            "metrics_history_size": len(self._metrics_history),
            "scaling_history_size": len(self._scaling_history),
        }
    
    def get_scaling_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent scaling decisions.
        
        Args:
            limit: Maximum number of decisions to return.
            
        Returns:
            List of scaling decision dictionaries.
        """
        recent = self._scaling_history[-limit:] if self._scaling_history else []
        return [
            {
                "direction": d.direction.value,
                "current_workers": d.current_workers,
                "target_workers": d.target_workers,
                "reason": d.reason,
                "trigger": d.trigger,
                "timestamp": d.timestamp,
            }
            for d in recent
        ]
    
    def get_metrics_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent metrics.
        
        Args:
            limit: Maximum number of metrics to return.
            
        Returns:
            List of metrics dictionaries.
        """
        recent = self._metrics_history[-limit:] if self._metrics_history else []
        return [
            {
                "queue_depth": m.queue_depth,
                "active_workers": m.active_workers,
                "total_workers": m.total_workers,
                "utilization": m.utilization,
                "latency_p95_ms": m.latency_p95_ms,
                "timestamp": m.timestamp,
            }
            for m in recent
        ]


# =============================================================================
# Factory Function
# =============================================================================

def create_auto_scaler(
    pool: Any,
    redis_client: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    profile_name: str = "default",
) -> AutoScaler:
    """
    Factory function to create an AutoScaler.
    
    Args:
        pool: AgentPool instance to scale.
        redis_client: Optional Redis client for metrics.
        config: Optional configuration dictionary.
        profile_name: Worker profile to scale.
        
    Returns:
        Configured AutoScaler instance.
    """
    policy = ScalingPolicy()
    
    if config:
        policy = ScalingPolicy(
            enabled=config.get("enabled", policy.enabled),
            min_workers=config.get("min_workers", policy.min_workers),
            max_workers=config.get("max_workers", policy.max_workers),
            target_queue_depth=config.get("target_queue_depth", policy.target_queue_depth),
            target_latency_p95_ms=config.get("target_latency_p95_ms", policy.target_latency_p95_ms),
            scale_up_threshold=config.get("scale_up_threshold", policy.scale_up_threshold),
            scale_down_threshold=config.get("scale_down_threshold", policy.scale_down_threshold),
            cooldown_seconds=config.get("cooldown_seconds", policy.cooldown_seconds),
            scale_up_increment=config.get("scale_up_increment", policy.scale_up_increment),
            scale_down_increment=config.get("scale_down_increment", policy.scale_down_increment),
        )
    
    return AutoScaler(
        pool=pool,
        redis_client=redis_client,
        policy=policy,
        profile_name=profile_name,
    )
