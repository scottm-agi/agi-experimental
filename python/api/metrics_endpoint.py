"""
Prometheus Metrics Endpoint for Parallel Swarm Monitoring

Provides HTTP endpoints for Prometheus scraping and health checks.
Integrates with the observer mesh to expose all collected metrics.

Endpoints:
- GET /metrics - Prometheus-format metrics
- GET /health - Health check endpoint
- GET /status - Detailed system status JSON
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from aiohttp import web

logger = logging.getLogger(__name__)


class MetricsEndpointConfig:
    """Configuration for the metrics endpoint."""
    def __init__(self, host: str = "0.0.0.0", port: int = 9090, metrics_path: str = "/metrics", health_path: str = "/health", status_path: str = "/status", enable_cors: bool = True):
        self.host = host
        self.port = port
        self.metrics_path = metrics_path
        self.health_path = health_path
        self.status_path = status_path
        self.enable_cors = enable_cors


class MetricsEndpoint:
    """
    HTTP server for exposing Prometheus metrics and health checks.
    
    Integrates with:
    - ObserverMesh for metrics collection
    - CircuitBreakerRegistry for circuit breaker status
    - AgentPool for agent pool status
    - MessageQueue for queue metrics
    - AutoScaler for auto-scaling metrics
    """
    
    def __init__(
        self,
        config: Optional[MetricsEndpointConfig] = None,
        observer_mesh=None,
        circuit_breaker_registry=None,
        agent_pool=None,
        message_queue=None,
        auto_scaler=None,
    ):
        self.config = config or MetricsEndpointConfig()
        self.observer_mesh = observer_mesh
        self.circuit_breaker_registry = circuit_breaker_registry
        self.agent_pool = agent_pool
        self.message_queue = message_queue
        self.auto_scaler = auto_scaler
        
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._start_time = time.time()
    
    async def start(self) -> None:
        """Start the metrics HTTP server."""
        self._app = web.Application()
        self._setup_routes()
        
        if self.config.enable_cors:
            self._setup_cors()
        
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        self._site = web.TCPSite(
            self._runner,
            self.config.host,
            self.config.port
        )
        await self._site.start()
        
        logger.info(
            f"Metrics endpoint started at "
            f"http://{self.config.host}:{self.config.port}"
        )
    
    async def stop(self) -> None:
        """Stop the metrics HTTP server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Metrics endpoint stopped")
    
    def _setup_routes(self) -> None:
        """Set up HTTP routes."""
        if self._app is None:
            return
        self._app.router.add_get(self.config.metrics_path, self._handle_metrics)
        self._app.router.add_get(self.config.health_path, self._handle_health)
        self._app.router.add_get(self.config.status_path, self._handle_status)
        self._app.router.add_get("/", self._handle_index)
    
    def _setup_cors(self) -> None:
        """Set up CORS middleware."""
        if self._app is None:
            return
        
        @web.middleware
        async def cors_middleware(request, handler):
            response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response
        
        self._app.middlewares.append(cors_middleware)
    
    async def _handle_index(self, request: web.Request) -> web.Response:
        """Handle index page with links to endpoints."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>AGIX Parallel Swarm Metrics</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                h1 { color: #333; }
                ul { list-style-type: none; padding: 0; }
                li { margin: 10px 0; }
                a { color: #0066cc; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .endpoint { font-family: monospace; background: #f4f4f4; padding: 2px 6px; }
            </style>
        </head>
        <body>
            <h1>AGIX Parallel Swarm Metrics</h1>
            <p>Available endpoints:</p>
            <ul>
                <li><a href="/metrics"><span class="endpoint">/metrics</span></a> - Prometheus metrics</li>
                <li><a href="/health"><span class="endpoint">/health</span></a> - Health check</li>
                <li><a href="/status"><span class="endpoint">/status</span></a> - Detailed status (JSON)</li>
            </ul>
        </body>
        </html>
        """
        return web.Response(text=html, content_type="text/html")
    
    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Handle Prometheus metrics endpoint."""
        metrics_lines = []
        
        # Add standard metrics header
        metrics_lines.append("# AGIX Parallel Swarm Metrics")
        metrics_lines.append(f"# Generated at {datetime.now(timezone.utc).isoformat()}")
        metrics_lines.append("")
        
        # Uptime metric
        uptime = time.time() - self._start_time
        metrics_lines.append("# HELP agix_uptime_seconds Time since metrics endpoint started")
        metrics_lines.append("# TYPE agix_uptime_seconds gauge")
        metrics_lines.append(f"agix_uptime_seconds {uptime:.2f}")
        metrics_lines.append("")
        
        # Observer mesh metrics
        if self.observer_mesh:
            try:
                prometheus_metrics = await self.observer_mesh.export_prometheus_metrics()
                if prometheus_metrics:
                    metrics_lines.append("# Observer Mesh Metrics")
                    metrics_lines.append(prometheus_metrics)
                    metrics_lines.append("")
            except Exception as e:
                logger.error(f"Error collecting observer mesh metrics: {e}")
        
        # Circuit breaker metrics
        if self.circuit_breaker_registry:
            try:
                cb_metrics = await self._collect_circuit_breaker_metrics()
                metrics_lines.extend(cb_metrics)
            except Exception as e:
                logger.error(f"Error collecting circuit breaker metrics: {e}")
        
        # Agent pool metrics
        if self.agent_pool:
            try:
                pool_metrics = await self._collect_agent_pool_metrics()
                metrics_lines.extend(pool_metrics)
            except Exception as e:
                logger.error(f"Error collecting agent pool metrics: {e}")
        
        # Message queue metrics
        if self.message_queue:
            try:
                queue_metrics = await self._collect_message_queue_metrics()
                metrics_lines.extend(queue_metrics)
            except Exception as e:
                logger.error(f"Error collecting message queue metrics: {e}")
        
        # Auto-scaler metrics
        if self.auto_scaler:
            try:
                scaler_metrics = self._collect_auto_scaler_metrics()
                metrics_lines.extend(scaler_metrics)
            except Exception as e:
                logger.error(f"Error collecting auto-scaler metrics: {e}")
        
        return web.Response(
            text="\n".join(metrics_lines),
            content_type="text/plain; version=0.0.4; charset=utf-8"
        )
    
    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle health check endpoint."""
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": time.time() - self._start_time,
            "components": {},
        }
        
        overall_healthy = True
        
        # Check observer mesh
        if self.observer_mesh:
            try:
                active_alerts = await self.observer_mesh.alerts.get_active_alerts()
                critical_alerts = [a for a in active_alerts if a.severity.value == "critical"]
                health_status["components"]["observer_mesh"] = {
                    "status": "degraded" if critical_alerts else "healthy",
                    "active_alerts": len(active_alerts),
                    "critical_alerts": len(critical_alerts),
                }
                if critical_alerts:
                    overall_healthy = False
            except Exception as e:
                health_status["components"]["observer_mesh"] = {
                    "status": "error",
                    "error": str(e),
                }
                overall_healthy = False
        
        # Check circuit breakers
        if self.circuit_breaker_registry:
            try:
                cb_health = await self.circuit_breaker_registry.get_health_summary()
                open_circuits = cb_health.get("open", 0)
                health_status["components"]["circuit_breakers"] = {
                    "status": "degraded" if open_circuits > 0 else "healthy",
                    "total": cb_health.get("total_circuits", 0),
                    "open": open_circuits,
                    "health_percentage": cb_health.get("health_percentage", 100),
                }
                if open_circuits > 0:
                    overall_healthy = False
            except Exception as e:
                health_status["components"]["circuit_breakers"] = {
                    "status": "error",
                    "error": str(e),
                }
                overall_healthy = False
        
        # Check agent pool
        if self.agent_pool:
            try:
                pool_stats = self.agent_pool.get_stats()
                health_status["components"]["agent_pool"] = {
                    "status": "healthy",
                    "active_agents": pool_stats.get("active_agents", 0),
                    "available_agents": pool_stats.get("available_agents", 0),
                }
            except Exception as e:
                health_status["components"]["agent_pool"] = {
                    "status": "error",
                    "error": str(e),
                }
        
        # Check message queue
        if self.message_queue:
            try:
                queue_stats = await self.message_queue.get_stats()
                health_status["components"]["message_queue"] = {
                    "status": "healthy",
                    "pending_tasks": queue_stats.get("pending_tasks", 0),
                    "processing_tasks": queue_stats.get("processing_tasks", 0),
                }
            except Exception as e:
                health_status["components"]["message_queue"] = {
                    "status": "error",
                    "error": str(e),
                }
        
        health_status["status"] = "healthy" if overall_healthy else "degraded"
        
        status_code = 200 if overall_healthy else 503
        return web.json_response(health_status, status=status_code)
    
    async def _handle_status(self, request: web.Request) -> web.Response:
        """Handle detailed status endpoint."""
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": time.time() - self._start_time,
            "version": "1.0.0",
            "components": {},
        }
        
        # Observer mesh status
        if self.observer_mesh:
            try:
                dashboard_data = await self.observer_mesh.get_dashboard_data()
                status["components"]["observer_mesh"] = dashboard_data
            except Exception as e:
                status["components"]["observer_mesh"] = {"error": str(e)}
        
        # Circuit breaker status
        if self.circuit_breaker_registry:
            try:
                all_status = await self.circuit_breaker_registry.get_all_status()
                health_summary = await self.circuit_breaker_registry.get_health_summary()
                status["components"]["circuit_breakers"] = {
                    "summary": health_summary,
                    "circuits": all_status,
                }
            except Exception as e:
                status["components"]["circuit_breakers"] = {"error": str(e)}
        
        # Agent pool status
        if self.agent_pool:
            try:
                pool_stats = self.agent_pool.get_stats()
                status["components"]["agent_pool"] = pool_stats
            except Exception as e:
                status["components"]["agent_pool"] = {"error": str(e)}
        
        # Message queue status
        if self.message_queue:
            try:
                queue_stats = await self.message_queue.get_stats()
                status["components"]["message_queue"] = queue_stats
            except Exception as e:
                status["components"]["message_queue"] = {"error": str(e)}
        
        return web.json_response(status)
    
    async def _collect_circuit_breaker_metrics(self) -> List[str]:
        """Collect circuit breaker metrics in Prometheus format."""
        lines = []
        lines.append("# Circuit Breaker Metrics")
        
        if self.circuit_breaker_registry is None:
            return lines
        
        health = await self.circuit_breaker_registry.get_health_summary()
        
        lines.append("# HELP circuit_breaker_total Total number of circuit breakers")
        lines.append("# TYPE circuit_breaker_total gauge")
        lines.append(f"circuit_breaker_total {health.get('total_circuits', 0)}")
        
        lines.append("# HELP circuit_breaker_closed Number of closed circuit breakers")
        lines.append("# TYPE circuit_breaker_closed gauge")
        lines.append(f"circuit_breaker_closed {health.get('closed', 0)}")
        
        lines.append("# HELP circuit_breaker_open Number of open circuit breakers")
        lines.append("# TYPE circuit_breaker_open gauge")
        lines.append(f"circuit_breaker_open {health.get('open', 0)}")
        
        lines.append("# HELP circuit_breaker_half_open Number of half-open circuit breakers")
        lines.append("# TYPE circuit_breaker_half_open gauge")
        lines.append(f"circuit_breaker_half_open {health.get('half_open', 0)}")
        
        lines.append("# HELP circuit_breaker_calls_total Total calls through circuit breakers")
        lines.append("# TYPE circuit_breaker_calls_total counter")
        lines.append(f"circuit_breaker_calls_total {health.get('total_calls', 0)}")
        
        lines.append("# HELP circuit_breaker_failures_total Total failures through circuit breakers")
        lines.append("# TYPE circuit_breaker_failures_total counter")
        lines.append(f"circuit_breaker_failures_total {health.get('total_failures', 0)}")
        
        lines.append("")
        return lines
    
    async def _collect_agent_pool_metrics(self) -> List[str]:
        """Collect agent pool metrics in Prometheus format."""
        lines = []
        lines.append("# Agent Pool Metrics")
        
        if self.agent_pool is None:
            return lines
        
        stats = self.agent_pool.get_stats()
        
        lines.append("# HELP agent_pool_active Active agents in pool")
        lines.append("# TYPE agent_pool_active gauge")
        lines.append(f"agent_pool_active {stats.get('active_agents', 0)}")
        
        lines.append("# HELP agent_pool_available Available agents in pool")
        lines.append("# TYPE agent_pool_available gauge")
        lines.append(f"agent_pool_available {stats.get('available_agents', 0)}")
        
        lines.append("# HELP agent_pool_max_size Maximum pool size")
        lines.append("# TYPE agent_pool_max_size gauge")
        lines.append(f"agent_pool_max_size {stats.get('max_size', 0)}")
        
        lines.append("# HELP agent_pool_tasks_completed Total tasks completed")
        lines.append("# TYPE agent_pool_tasks_completed counter")
        lines.append(f"agent_pool_tasks_completed {stats.get('tasks_completed', 0)}")
        
        lines.append("# HELP agent_pool_tasks_failed Total tasks failed")
        lines.append("# TYPE agent_pool_tasks_failed counter")
        lines.append(f"agent_pool_tasks_failed {stats.get('tasks_failed', 0)}")
        
        lines.append("")
        return lines
    
    async def _collect_message_queue_metrics(self) -> List[str]:
        """Collect message queue metrics in Prometheus format."""
        lines = []
        lines.append("# Message Queue Metrics")
        
        if self.message_queue is None:
            return lines
        
        stats = await self.message_queue.get_stats()
        
        lines.append("# HELP message_queue_pending Pending tasks in queue")
        lines.append("# TYPE message_queue_pending gauge")
        lines.append(f"message_queue_pending {stats.get('pending_tasks', 0)}")
        
        lines.append("# HELP message_queue_processing Tasks currently processing")
        lines.append("# TYPE message_queue_processing gauge")
        lines.append(f"message_queue_processing {stats.get('processing_tasks', 0)}")
        
        lines.append("# HELP message_queue_completed Total tasks completed")
        lines.append("# TYPE message_queue_completed counter")
        lines.append(f"message_queue_completed {stats.get('completed_tasks', 0)}")
        
        lines.append("# HELP message_queue_failed Total tasks failed")
        lines.append("# TYPE message_queue_failed counter")
        lines.append(f"message_queue_failed {stats.get('failed_tasks', 0)}")
        
        lines.append("")
        return lines
    
    def _collect_auto_scaler_metrics(self) -> List[str]:
        """Collect auto-scaler metrics in Prometheus format."""
        lines = []
        lines.append("# Auto-Scaler Metrics")
        
        if self.auto_scaler is None:
            return lines
        
        status = self.auto_scaler.get_status()
        
        # Auto-scaler enabled status
        lines.append("# HELP auto_scaler_enabled Whether auto-scaling is enabled")
        lines.append("# TYPE auto_scaler_enabled gauge")
        lines.append(f"auto_scaler_enabled {1 if status.get('enabled', False) else 0}")
        
        # Auto-scaler running status
        lines.append("# HELP auto_scaler_running Whether auto-scaling loop is running")
        lines.append("# TYPE auto_scaler_running gauge")
        lines.append(f"auto_scaler_running {1 if status.get('running', False) else 0}")
        
        # Policy configuration
        policy = status.get("policy", {})
        
        lines.append("# HELP auto_scaler_min_workers Minimum workers configured")
        lines.append("# TYPE auto_scaler_min_workers gauge")
        lines.append(f"auto_scaler_min_workers {policy.get('min_workers', 0)}")
        
        lines.append("# HELP auto_scaler_max_workers Maximum workers configured")
        lines.append("# TYPE auto_scaler_max_workers gauge")
        lines.append(f"auto_scaler_max_workers {policy.get('max_workers', 0)}")
        
        lines.append("# HELP auto_scaler_target_queue_depth Target queue depth per worker")
        lines.append("# TYPE auto_scaler_target_queue_depth gauge")
        lines.append(f"auto_scaler_target_queue_depth {policy.get('target_queue_depth', 0)}")
        
        lines.append("# HELP auto_scaler_target_latency_p95_ms Target p95 latency in ms")
        lines.append("# TYPE auto_scaler_target_latency_p95_ms gauge")
        lines.append(f"auto_scaler_target_latency_p95_ms {policy.get('target_latency_p95_ms', 0)}")
        
        lines.append("# HELP auto_scaler_scale_up_threshold Utilization threshold for scale up")
        lines.append("# TYPE auto_scaler_scale_up_threshold gauge")
        lines.append(f"auto_scaler_scale_up_threshold {policy.get('scale_up_threshold', 0)}")
        
        lines.append("# HELP auto_scaler_scale_down_threshold Utilization threshold for scale down")
        lines.append("# TYPE auto_scaler_scale_down_threshold gauge")
        lines.append(f"auto_scaler_scale_down_threshold {policy.get('scale_down_threshold', 0)}")
        
        lines.append("# HELP auto_scaler_cooldown_seconds Cooldown period between scaling actions")
        lines.append("# TYPE auto_scaler_cooldown_seconds gauge")
        lines.append(f"auto_scaler_cooldown_seconds {policy.get('cooldown_seconds', 0)}")
        
        # Last scaling time
        lines.append("# HELP auto_scaler_last_scaling_timestamp Unix timestamp of last scaling action")
        lines.append("# TYPE auto_scaler_last_scaling_timestamp gauge")
        lines.append(f"auto_scaler_last_scaling_timestamp {status.get('last_scaling_time', 0)}")
        
        # History sizes
        lines.append("# HELP auto_scaler_metrics_history_size Number of metrics in history")
        lines.append("# TYPE auto_scaler_metrics_history_size gauge")
        lines.append(f"auto_scaler_metrics_history_size {status.get('metrics_history_size', 0)}")
        
        lines.append("# HELP auto_scaler_scaling_history_size Number of scaling decisions in history")
        lines.append("# TYPE auto_scaler_scaling_history_size gauge")
        lines.append(f"auto_scaler_scaling_history_size {status.get('scaling_history_size', 0)}")
        
        # Get recent metrics if available
        try:
            recent_metrics = self.auto_scaler.get_metrics_history(limit=1)
            if recent_metrics:
                latest = recent_metrics[-1]
                
                lines.append("# HELP auto_scaler_current_queue_depth Current queue depth")
                lines.append("# TYPE auto_scaler_current_queue_depth gauge")
                lines.append(f"auto_scaler_current_queue_depth {latest.get('queue_depth', 0)}")
                
                lines.append("# HELP auto_scaler_current_active_workers Current active workers")
                lines.append("# TYPE auto_scaler_current_active_workers gauge")
                lines.append(f"auto_scaler_current_active_workers {latest.get('active_workers', 0)}")
                
                lines.append("# HELP auto_scaler_current_total_workers Current total workers")
                lines.append("# TYPE auto_scaler_current_total_workers gauge")
                lines.append(f"auto_scaler_current_total_workers {latest.get('total_workers', 0)}")
                
                lines.append("# HELP auto_scaler_current_utilization Current worker utilization")
                lines.append("# TYPE auto_scaler_current_utilization gauge")
                lines.append(f"auto_scaler_current_utilization {latest.get('utilization', 0)}")
                
                lines.append("# HELP auto_scaler_current_latency_p95_ms Current p95 latency in ms")
                lines.append("# TYPE auto_scaler_current_latency_p95_ms gauge")
                lines.append(f"auto_scaler_current_latency_p95_ms {latest.get('latency_p95_ms', 0)}")
        except Exception as e:
            logger.warning(f"Failed to get auto-scaler metrics history: {e}")
        
        # Get scaling history counts by direction
        try:
            scaling_history = self.auto_scaler.get_scaling_history(limit=100)
            scale_up_count = sum(1 for d in scaling_history if d.get('direction') == 'up')
            scale_down_count = sum(1 for d in scaling_history if d.get('direction') == 'down')
            
            lines.append("# HELP auto_scaler_scale_up_total Total scale up actions")
            lines.append("# TYPE auto_scaler_scale_up_total counter")
            lines.append(f"auto_scaler_scale_up_total {scale_up_count}")
            
            lines.append("# HELP auto_scaler_scale_down_total Total scale down actions")
            lines.append("# TYPE auto_scaler_scale_down_total counter")
            lines.append(f"auto_scaler_scale_down_total {scale_down_count}")
        except Exception as e:
            logger.warning(f"Failed to get auto-scaler scaling history: {e}")
        
        lines.append("")
        return lines


# =============================================================================
# Factory Function
# =============================================================================

def create_metrics_endpoint(
    host: str = "0.0.0.0",
    port: int = 9090,
    observer_mesh=None,
    circuit_breaker_registry=None,
    agent_pool=None,
    message_queue=None,
) -> MetricsEndpoint:
    """Create a metrics endpoint instance."""
    config = MetricsEndpointConfig(host=host, port=port)
    return MetricsEndpoint(
        config=config,
        observer_mesh=observer_mesh,
        circuit_breaker_registry=circuit_breaker_registry,
        agent_pool=agent_pool,
        message_queue=message_queue,
    )


# =============================================================================
# Standalone Server
# =============================================================================

async def run_metrics_server(
    host: str = "0.0.0.0",
    port: int = 9090,
    observer_mesh=None,
    circuit_breaker_registry=None,
    agent_pool=None,
    message_queue=None,
) -> None:
    """Run the metrics server as a standalone process."""
    endpoint = create_metrics_endpoint(
        host=host,
        port=port,
        observer_mesh=observer_mesh,
        circuit_breaker_registry=circuit_breaker_registry,
        agent_pool=agent_pool,
        message_queue=message_queue,
    )
    
    await endpoint.start()
    
    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await endpoint.stop()


if __name__ == "__main__":
    # Run standalone metrics server for testing
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_metrics_server())
