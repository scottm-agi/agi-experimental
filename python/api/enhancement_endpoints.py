from __future__ import annotations
"""
HTTP API Endpoints for Parallel Swarm Enhancements

Provides REST API endpoints for:
- Auto-scaling management and status
- Cross-agent learning operations
- Distributed execution routing
- ML optimization predictions

These endpoints enable E2E testing via curl and integration with external systems.
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from aiohttp import web

# Import KnowledgeType enum for proper type conversion
# Define fallback first, then override if import succeeds
from enum import Enum as _Enum

class _FallbackKnowledgeType(str, _Enum):
    PATTERN = "pattern"
    FACT = "fact"
    PROCEDURE = "procedure"
    FAILURE = "failure"

KnowledgeType = _FallbackKnowledgeType

try:
    from python.helpers.cross_agent_learning import KnowledgeType
except ImportError:
    try:
        from helpers.cross_agent_learning import KnowledgeType
    except ImportError:
        pass  # Use fallback defined above

logger = logging.getLogger(__name__)


class EnhancementEndpoints:
    """
    HTTP server for enhancement API endpoints.
    
    Provides REST APIs for:
    - /api/v1/autoscaler/* - Auto-scaling operations
    - /api/v1/knowledge/* - Cross-agent learning operations
    - /api/v1/routing/* - Distributed execution routing
    - /api/v1/ml/* - ML optimization predictions
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9091,
        auto_scaler=None,
        knowledge_learner=None,
        knowledge_propagator=None,
        node_registry=None,
        distributed_router=None,
        ml_optimizer=None,
        feature_extractor=None,
    ):
        self.host = host
        self.port = port
        self.auto_scaler = auto_scaler
        self.knowledge_learner = knowledge_learner
        self.knowledge_propagator = knowledge_propagator
        self.node_registry = node_registry
        self.distributed_router = distributed_router
        self.ml_optimizer = ml_optimizer
        self.feature_extractor = feature_extractor
        
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._start_time = time.time()
    
    async def start(self) -> None:
        """Start the enhancement API server."""
        self._app = web.Application()
        self._setup_routes()
        self._setup_cors()
        
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        self._site = web.TCPSite(
            self._runner,
            self.host,
            self.port
        )
        await self._site.start()
        
        logger.info(
            f"Enhancement API started at "
            f"http://{self.host}:{self.port}"
        )
    
    async def stop(self) -> None:
        """Stop the enhancement API server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Enhancement API stopped")
    
    def _setup_routes(self) -> None:
        """Set up HTTP routes."""
        if self._app is None:
            return
        
        # Index
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/health", self._handle_health)
        
        # Auto-scaler endpoints
        self._app.router.add_get("/api/v1/autoscaler/status", self._handle_autoscaler_status)
        self._app.router.add_get("/api/v1/autoscaler/metrics", self._handle_autoscaler_metrics)
        self._app.router.add_get("/api/v1/autoscaler/history", self._handle_autoscaler_history)
        self._app.router.add_post("/api/v1/autoscaler/scale", self._handle_autoscaler_scale)
        self._app.router.add_post("/api/v1/autoscaler/policy", self._handle_autoscaler_policy)
        
        # Knowledge (Cross-agent Learning) endpoints
        self._app.router.add_get("/api/v1/knowledge/list", self._handle_knowledge_list)
        self._app.router.add_get("/api/v1/knowledge/{knowledge_id}", self._handle_knowledge_get)
        self._app.router.add_post("/api/v1/knowledge", self._handle_knowledge_store)
        self._app.router.add_delete("/api/v1/knowledge/{knowledge_id}", self._handle_knowledge_delete)
        self._app.router.add_post("/api/v1/knowledge/{knowledge_id}/validate", self._handle_knowledge_validate)
        self._app.router.add_post("/api/v1/knowledge/search", self._handle_knowledge_search)
        self._app.router.add_get("/api/v1/knowledge/stats", self._handle_knowledge_stats)
        
        # Distributed Routing endpoints
        self._app.router.add_get("/api/v1/routing/nodes", self._handle_routing_nodes)
        self._app.router.add_post("/api/v1/routing/route", self._handle_routing_route)
        self._app.router.add_get("/api/v1/routing/stats", self._handle_routing_stats)
        self._app.router.add_post("/api/v1/routing/register", self._handle_routing_register)
        
        # ML Optimization endpoints
        self._app.router.add_post("/api/v1/ml/predict/routing", self._handle_ml_predict_routing)
        self._app.router.add_post("/api/v1/ml/predict/timeout", self._handle_ml_predict_timeout)
        self._app.router.add_post("/api/v1/ml/predict/strategy", self._handle_ml_predict_strategy)
        self._app.router.add_post("/api/v1/ml/optimize", self._handle_ml_optimize)
        self._app.router.add_post("/api/v1/ml/record", self._handle_ml_record)
        self._app.router.add_get("/api/v1/ml/stats", self._handle_ml_stats)
        self._app.router.add_post("/api/v1/ml/features", self._handle_ml_features)
    
    def _setup_cors(self) -> None:
        """Set up CORS middleware."""
        if self._app is None:
            return
        
        @web.middleware
        async def cors_middleware(request, handler):
            if request.method == "OPTIONS":
                response = web.Response()
            else:
                response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response
        
        self._app.middlewares.append(cors_middleware)
    
    # =========================================================================
    # Index and Health
    # =========================================================================
    
    async def _handle_index(self, request: web.Request) -> web.Response:
        """Handle index page."""
        endpoints = {
            "enhancement_api": "AGIX Parallel Swarm Enhancement API",
            "version": "1.0.0",
            "endpoints": {
                "autoscaler": [
                    "GET /api/v1/autoscaler/status",
                    "GET /api/v1/autoscaler/metrics",
                    "GET /api/v1/autoscaler/history",
                    "POST /api/v1/autoscaler/scale",
                    "POST /api/v1/autoscaler/policy",
                ],
                "knowledge": [
                    "GET /api/v1/knowledge/list",
                    "GET /api/v1/knowledge/{id}",
                    "POST /api/v1/knowledge",
                    "DELETE /api/v1/knowledge/{id}",
                    "POST /api/v1/knowledge/{id}/validate",
                    "POST /api/v1/knowledge/search",
                    "GET /api/v1/knowledge/stats",
                ],
                "routing": [
                    "GET /api/v1/routing/nodes",
                    "POST /api/v1/routing/route",
                    "GET /api/v1/routing/stats",
                    "POST /api/v1/routing/register",
                ],
                "ml": [
                    "POST /api/v1/ml/predict/routing",
                    "POST /api/v1/ml/predict/timeout",
                    "POST /api/v1/ml/predict/strategy",
                    "POST /api/v1/ml/optimize",
                    "POST /api/v1/ml/record",
                    "GET /api/v1/ml/stats",
                    "POST /api/v1/ml/features",
                ],
            },
        }
        return web.json_response(endpoints)
    
    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle health check."""
        health = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": time.time() - self._start_time,
            "components": {
                "auto_scaler": self.auto_scaler is not None,
                "knowledge_learner": self.knowledge_learner is not None,
                "knowledge_propagator": self.knowledge_propagator is not None,
                "node_registry": self.node_registry is not None,
                "distributed_router": self.distributed_router is not None,
                "ml_optimizer": self.ml_optimizer is not None,
                "feature_extractor": self.feature_extractor is not None,
            },
        }
        return web.json_response(health)
    
    # =========================================================================
    # Auto-scaler Endpoints
    # =========================================================================
    
    async def _handle_autoscaler_status(self, request: web.Request) -> web.Response:
        """Get auto-scaler status."""
        if self.auto_scaler is None:
            return web.json_response(
                {"error": "Auto-scaler not configured"},
                status=503
            )
        
        status = self.auto_scaler.get_status()
        return web.json_response(status)
    
    async def _handle_autoscaler_metrics(self, request: web.Request) -> web.Response:
        """Get auto-scaler metrics history."""
        if self.auto_scaler is None:
            return web.json_response(
                {"error": "Auto-scaler not configured"},
                status=503
            )
        
        limit = int(request.query.get("limit", "100"))
        metrics = self.auto_scaler.get_metrics_history(limit=limit)
        return web.json_response({"metrics": metrics})
    
    async def _handle_autoscaler_history(self, request: web.Request) -> web.Response:
        """Get auto-scaler scaling history."""
        if self.auto_scaler is None:
            return web.json_response(
                {"error": "Auto-scaler not configured"},
                status=503
            )
        
        limit = int(request.query.get("limit", "100"))
        history = self.auto_scaler.get_scaling_history(limit=limit)
        return web.json_response({"history": history})
    
    async def _handle_autoscaler_scale(self, request: web.Request) -> web.Response:
        """Trigger manual scaling."""
        if self.auto_scaler is None:
            return web.json_response(
                {"error": "Auto-scaler not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            direction = data.get("direction", "up")
            count = data.get("count", 1)
            
            # Get current status
            status = self.auto_scaler.get_status()
            current_workers = status.get("policy", {}).get("min_workers", 2)
            
            # Calculate target based on direction
            if direction == "up":
                target = current_workers + count
            elif direction == "down":
                target = max(1, current_workers - count)
            else:
                return web.json_response(
                    {"error": f"Invalid direction: {direction}"},
                    status=400
                )
            
            # Scale through the pool if available
            if hasattr(self.auto_scaler, 'pool') and self.auto_scaler.pool:
                await self.auto_scaler.pool.scale_workers(
                    self.auto_scaler._profile_name,
                    target
                )
                result = True
            else:
                result = False
            
            return web.json_response({
                "success": result,
                "direction": direction,
                "count": count,
                "target_workers": target,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_autoscaler_policy(self, request: web.Request) -> web.Response:
        """Update auto-scaler policy."""
        if self.auto_scaler is None:
            return web.json_response(
                {"error": "Auto-scaler not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            
            # Update policy attributes directly
            policy = self.auto_scaler.policy
            if "min_workers" in data:
                policy.min_workers = data["min_workers"]
            if "max_workers" in data:
                policy.max_workers = data["max_workers"]
            if "target_queue_depth" in data:
                policy.target_queue_depth = data["target_queue_depth"]
            if "target_latency_p95_ms" in data:
                policy.target_latency_p95_ms = data["target_latency_p95_ms"]
            if "scale_up_threshold" in data:
                policy.scale_up_threshold = data["scale_up_threshold"]
            if "scale_down_threshold" in data:
                policy.scale_down_threshold = data["scale_down_threshold"]
            if "cooldown_seconds" in data:
                policy.cooldown_seconds = data["cooldown_seconds"]
            if "enabled" in data:
                policy.enabled = data["enabled"]
            
            return web.json_response({
                "success": True,
                "policy": self.auto_scaler.get_status().get("policy", {}),
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    # =========================================================================
    # Knowledge (Cross-agent Learning) Endpoints
    # =========================================================================
    
    async def _handle_knowledge_list(self, request: web.Request) -> web.Response:
        """List all knowledge entries."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        knowledge_type = request.query.get("type")
        limit = int(request.query.get("limit", "100"))
        
        try:
            if knowledge_type:
                # Convert string to KnowledgeType enum
                kt = KnowledgeType(knowledge_type)
                entries = await self.knowledge_learner.retrieve_by_type(kt, limit=limit)
            else:
                entries = await self.knowledge_learner.retrieve_recent(limit=limit)
        except ValueError as e:
            return web.json_response(
                {"error": f"Invalid knowledge type: {knowledge_type}"},
                status=400
            )
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
        
        # Convert to serializable format
        result = []
        for entry in entries:
            result.append({
                "id": entry.knowledge_id,
                "type": entry.knowledge_type.value if hasattr(entry.knowledge_type, 'value') else str(entry.knowledge_type),
                "content": entry.content,
                "source_agent": entry.source_agent,
                "confidence": entry.confidence,
                "validation_count": entry.validation_count,
                "created_at": entry.created_at,
                "metadata": entry.metadata,
            })
        
        return web.json_response({"knowledge": result, "count": len(result)})
    
    async def _handle_knowledge_get(self, request: web.Request) -> web.Response:
        """Get a specific knowledge entry."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        knowledge_id = request.match_info["knowledge_id"]
        entry = await self.knowledge_learner.get_knowledge(knowledge_id)
        
        if entry is None:
            return web.json_response(
                {"error": f"Knowledge not found: {knowledge_id}"},
                status=404
            )
        
        result = {
            "id": entry.knowledge_id,
            "type": entry.knowledge_type.value if hasattr(entry.knowledge_type, 'value') else str(entry.knowledge_type),
            "content": entry.content,
            "source_agent": entry.source_agent,
            "confidence": entry.confidence,
            "validation_count": entry.validation_count,
            "usage_count": entry.usage_count,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "metadata": entry.metadata,
        }
        
        return web.json_response(result)
    
    async def _handle_knowledge_store(self, request: web.Request) -> web.Response:
        """Store new knowledge."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            
            # Convert string type to KnowledgeType enum
            type_str = data.get("type", "fact")
            try:
                knowledge_type = KnowledgeType(type_str)
            except ValueError:
                return web.json_response(
                    {"error": f"Invalid knowledge type: {type_str}. Valid types: pattern, fact, procedure, failure"},
                    status=400
                )
            
            # Build metadata with source_agent info (since store_knowledge doesn't take source_agent directly)
            metadata = data.get("metadata", {})
            if "source_agent" in data:
                metadata["api_source_agent"] = data.get("source_agent")
            if "tags" in data:
                metadata["tags"] = data.get("tags", [])
            
            # Call async store_knowledge with correct signature
            knowledge = await self.knowledge_learner.store_knowledge(
                content=data.get("content", ""),
                knowledge_type=knowledge_type,
                task_id=data.get("task_id"),
                confidence=data.get("confidence", 0.5),
                metadata=metadata,
            )
            
            return web.json_response({
                "success": True,
                "knowledge_id": knowledge.knowledge_id,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_knowledge_delete(self, request: web.Request) -> web.Response:
        """Delete a knowledge entry."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        knowledge_id = request.match_info["knowledge_id"]
        success = await self.knowledge_learner.delete_knowledge(knowledge_id)
        
        if not success:
            return web.json_response(
                {"error": f"Knowledge not found: {knowledge_id}"},
                status=404
            )
        
        return web.json_response({"success": True, "deleted_id": knowledge_id})
    
    async def _handle_knowledge_validate(self, request: web.Request) -> web.Response:
        """Validate a knowledge entry."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        knowledge_id = request.match_info["knowledge_id"]
        
        try:
            data = await request.json()
            validator_agent = data.get("validator_agent", "api")
            success_flag = data.get("success", True)  # Default to successful validation
            
            # Call async validate_knowledge with correct signature
            result = await self.knowledge_learner.validate_knowledge(
                knowledge_id=knowledge_id,
                validator_agent=validator_agent,
                success=success_flag,
            )
            
            if result is None:
                return web.json_response(
                    {"error": f"Knowledge not found: {knowledge_id}"},
                    status=404
                )
            
            return web.json_response({
                "success": True,
                "knowledge_id": knowledge_id,
                "validator": validator_agent,
                "new_confidence": result.confidence,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_knowledge_search(self, request: web.Request) -> web.Response:
        """Search knowledge entries."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            query = data.get("query", "")
            limit = data.get("limit", 10)
            min_similarity = data.get("min_similarity", 0.3)
            
            # Call async search_knowledge with correct signature (top_k, not limit)
            entries = await self.knowledge_learner.search_knowledge(
                query=query,
                top_k=limit,
                min_similarity=min_similarity,
            )
            
            result = []
            for entry in entries:
                result.append({
                    "id": entry.knowledge_id,
                    "type": entry.knowledge_type.value if hasattr(entry.knowledge_type, 'value') else str(entry.knowledge_type),
                    "content": entry.content,
                    "confidence": entry.confidence,
                    "metadata": entry.metadata,
                })
            
            return web.json_response({"results": result, "count": len(result)})
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_knowledge_stats(self, request: web.Request) -> web.Response:
        """Get knowledge statistics."""
        if self.knowledge_learner is None:
            return web.json_response(
                {"error": "Knowledge learner not configured"},
                status=503
            )
        
        # Call async get_stats
        stats = await self.knowledge_learner.get_stats()
        return web.json_response(stats)
    
    # =========================================================================
    # Distributed Routing Endpoints
    # =========================================================================
    
    async def _handle_routing_nodes(self, request: web.Request) -> web.Response:
        """Get registered nodes."""
        if self.node_registry is None:
            return web.json_response(
                {"error": "Node registry not configured"},
                status=503
            )
        
        try:
            nodes = await self.node_registry.get_healthy_nodes()
            
            result = []
            for node in nodes:
                result.append({
                    "id": node.node_id,
                    "host": node.host,
                    "port": node.port,
                    "profiles": node.profiles,
                    "capacity": node.worker_capacity,
                    "current_load": node.active_workers,
                    "status": node.status.value if hasattr(node.status, 'value') else str(node.status),
                })
            
            return web.json_response({"nodes": result, "count": len(result)})
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_routing_route(self, request: web.Request) -> web.Response:
        """Route a task to a node."""
        if self.distributed_router is None:
            return web.json_response(
                {"error": "Distributed router not configured"},
                status=503
            )
        
        try:
            # Import TaskInfo for creating task objects
            try:
                from python.helpers.distributed_router import TaskInfo
            except ImportError:
                from helpers.distributed_router import TaskInfo
            
            data = await request.json()
            task_id = data.get("task_id", f"task_{int(time.time())}")
            profile = data.get("profile", "default")
            preferred_node = data.get("preferred_node")
            message = data.get("message", "")
            
            # Create TaskInfo object with correct signature
            task = TaskInfo(
                task_id=task_id,
                message=message,
                profile=profile,
                preferred_node=preferred_node,
            )
            
            # route_task returns RoutingDecision, not node directly
            decision = await self.distributed_router.route_task(task)
            
            if decision is None or decision.selected_node is None:
                return web.json_response(
                    {"error": "No available nodes for routing"},
                    status=503
                )
            
            # Get node info from registry
            node = await self.node_registry.get_node(decision.selected_node)
            
            return web.json_response({
                "task_id": task_id,
                "routed_to": {
                    "id": decision.selected_node,
                    "host": node.host if node else "unknown",
                    "port": node.port if node else 0,
                },
                "score": decision.score,
                "reason": decision.reason,
                "alternatives": decision.alternatives,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_routing_stats(self, request: web.Request) -> web.Response:
        """Get routing statistics."""
        if self.distributed_router is None:
            return web.json_response(
                {"error": "Distributed router not configured"},
                status=503
            )
        
        stats = self.distributed_router.get_stats()
        return web.json_response(stats)
    
    async def _handle_routing_register(self, request: web.Request) -> web.Response:
        """Register a new node."""
        if self.node_registry is None:
            return web.json_response(
                {"error": "Node registry not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            
            # Use register_local_node with correct signature
            node = await self.node_registry.register_local_node(
                host=data.get("host", "localhost"),
                port=data.get("port", 5000),
                worker_capacity=data.get("capacity", 10),
                profiles=data.get("profiles", []),
            )
            
            return web.json_response({
                "success": True,
                "node_id": node.node_id,
                "host": node.host,
                "port": node.port,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    # =========================================================================
    # ML Optimization Endpoints
    # =========================================================================
    
    async def _handle_ml_predict_routing(self, request: web.Request) -> web.Response:
        """Predict optimal routing profile."""
        if self.ml_optimizer is None:
            return web.json_response(
                {"error": "ML optimizer not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            message = data.get("message", "")
            context = data.get("context", {})
            
            # predict_routing takes (message, context) - no features parameter
            prediction = self.ml_optimizer.predict_routing(
                message=message,
                context=context,
            )
            
            # Convert alternatives from List[Tuple[str, float]] to list of dicts
            alternatives = [{"profile": p, "score": s} for p, s in prediction.alternatives]
            
            return web.json_response({
                "profile": prediction.recommended_profile,
                "confidence": prediction.confidence,
                "alternatives": alternatives,
                "reasoning": prediction.reasoning,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_ml_predict_timeout(self, request: web.Request) -> web.Response:
        """Predict optimal timeout."""
        if self.ml_optimizer is None:
            return web.json_response(
                {"error": "ML optimizer not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            message = data.get("message", "")
            profile = data.get("profile")
            context = data.get("context", {})
            
            # predict_timeout takes (message, profile, context) - no features parameter
            prediction = self.ml_optimizer.predict_timeout(
                message=message,
                profile=profile,
                context=context,
            )
            
            # confidence_interval is a Tuple[float, float]
            return web.json_response({
                "predicted_duration": prediction.predicted_duration,
                "confidence_interval": {"low": prediction.confidence_interval[0], "high": prediction.confidence_interval[1]},
                "recommended_timeout": prediction.recommended_timeout,
                "confidence": prediction.confidence,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_ml_predict_strategy(self, request: web.Request) -> web.Response:
        """Predict optimal parallelization strategy."""
        if self.ml_optimizer is None:
            return web.json_response(
                {"error": "ML optimizer not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            message = data.get("message", "")
            context = data.get("context", {})
            
            # predict_strategy takes (message, context) - no features parameter
            prediction = self.ml_optimizer.predict_strategy(
                message=message,
                context=context,
            )
            
            return web.json_response({
                "strategy": prediction.recommended_strategy,
                "worker_count": prediction.recommended_worker_count,
                "expected_speedup": prediction.expected_speedup,
                "confidence": prediction.confidence,
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_ml_optimize(self, request: web.Request) -> web.Response:
        """Get combined optimization for a task."""
        if self.ml_optimizer is None:
            return web.json_response(
                {"error": "ML optimizer not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            message = data.get("message", "")
            context = data.get("context", {})
            
            # optimize_task takes (message, context) - returns dict with routing, timeout, strategy
            optimization = self.ml_optimizer.optimize_task(
                message=message,
                context=context,
            )
            
            routing = optimization['routing']
            timeout = optimization['timeout']
            strategy = optimization['strategy']
            
            # Convert alternatives from List[Tuple[str, float]] to list of dicts
            alternatives = [{"profile": p, "score": s} for p, s in routing.alternatives]
            
            return web.json_response({
                "routing": {
                    "profile": routing.recommended_profile,
                    "confidence": routing.confidence,
                    "alternatives": alternatives,
                    "reasoning": routing.reasoning,
                },
                "timeout": {
                    "predicted_duration": timeout.predicted_duration,
                    "confidence_interval": {"low": timeout.confidence_interval[0], "high": timeout.confidence_interval[1]},
                    "recommended_timeout": timeout.recommended_timeout,
                    "confidence": timeout.confidence,
                },
                "strategy": {
                    "strategy": strategy.recommended_strategy,
                    "worker_count": strategy.recommended_worker_count,
                    "expected_speedup": strategy.expected_speedup,
                    "confidence": strategy.confidence,
                },
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_ml_record(self, request: web.Request) -> web.Response:
        """Record execution for training."""
        if self.ml_optimizer is None:
            return web.json_response(
                {"error": "ML optimizer not configured"},
                status=503
            )
        
        try:
            # Import ExecutionRecord for creating record objects
            try:
                from python.helpers.ml_optimizer import ExecutionRecord
            except ImportError:
                from helpers.ml_optimizer import ExecutionRecord
            
            data = await request.json()
            
            # Create ExecutionRecord object with correct signature
            record = ExecutionRecord(
                task_id=data.get("task_id", ""),
                task_message=data.get("message", ""),
                task_profile=data.get("profile", "default"),
                worker_profile_used=data.get("profile", "default"),
                timeout_set=data.get("timeout", 300),
                actual_duration=data.get("duration", 0),
                success=data.get("success", True),
                error_type=data.get("error_type"),
                parallelization_used=data.get("strategy", "sequential"),
                worker_count=data.get("worker_count", 1),
                queue_depth_at_start=data.get("queue_depth", 0),
                timestamp=time.time(),
            )
            
            self.ml_optimizer.record_execution(record)
            
            return web.json_response({
                "success": True,
                "task_id": data.get("task_id"),
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )
    
    async def _handle_ml_stats(self, request: web.Request) -> web.Response:
        """Get ML optimizer statistics."""
        if self.ml_optimizer is None:
            return web.json_response(
                {"error": "ML optimizer not configured"},
                status=503
            )
        
        stats = self.ml_optimizer.get_stats()
        return web.json_response(stats)
    
    async def _handle_ml_features(self, request: web.Request) -> web.Response:
        """Extract features from a message."""
        if self.feature_extractor is None:
            return web.json_response(
                {"error": "Feature extractor not configured"},
                status=503
            )
        
        try:
            data = await request.json()
            message = data.get("message", "")
            context = data.get("context", {})
            
            task_features = self.feature_extractor.extract_task_features(message)
            context_features = self.feature_extractor.extract_context_features(context)
            
            # Use correct attribute names from TaskFeatures dataclass
            return web.json_response({
                "task_features": {
                    "message_length": task_features.message_length,
                    "word_count": task_features.word_count,
                    "sentence_count": task_features.sentence_count,
                    "avg_word_length": task_features.avg_word_length,
                    "complexity_score": task_features.complexity_score,
                    "has_code_keywords": task_features.has_code_keywords,
                    "has_research_keywords": task_features.has_research_keywords,
                    "has_analysis_keywords": task_features.has_analysis_keywords,
                    "has_creative_keywords": task_features.has_creative_keywords,
                    "question_count": task_features.question_count,
                    "command_indicators": task_features.command_indicators,
                    "urgency_score": task_features.urgency_score,
                    "technical_density": task_features.technical_density,
                    "estimated_subtasks": task_features.estimated_subtasks,
                },
                "context_features": {
                    "queue_depth": context_features.queue_depth,
                    "active_workers": context_features.active_workers,
                    "total_workers": context_features.total_workers,
                    "utilization": context_features.utilization,
                    "recent_error_rate": context_features.recent_error_rate,
                    "avg_task_duration": context_features.avg_task_duration,
                    "hour_of_day": context_features.hour_of_day,
                    "day_of_week": context_features.day_of_week,
                    "is_peak_hours": context_features.is_peak_hours,
                },
            })
        except Exception as e:
            return web.json_response(
                {"error": str(e)},
                status=500
            )


# =============================================================================
# Factory Function
# =============================================================================

def create_enhancement_endpoints(
    host: str = "0.0.0.0",
    port: int = 9091,
    auto_scaler=None,
    knowledge_learner=None,
    knowledge_propagator=None,
    node_registry=None,
    distributed_router=None,
    ml_optimizer=None,
    feature_extractor=None,
) -> EnhancementEndpoints:
    """Create an enhancement endpoints instance."""
    return EnhancementEndpoints(
        host=host,
        port=port,
        auto_scaler=auto_scaler,
        knowledge_learner=knowledge_learner,
        knowledge_propagator=knowledge_propagator,
        node_registry=node_registry,
        distributed_router=distributed_router,
        ml_optimizer=ml_optimizer,
        feature_extractor=feature_extractor,
    )


# =============================================================================
# Standalone Server
# =============================================================================

async def run_enhancement_server(
    host: str = "0.0.0.0",
    port: int = 9091,
) -> None:
    """Run the enhancement API server as a standalone process."""
    endpoint = create_enhancement_endpoints(host=host, port=port)
    
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
    # Run standalone enhancement API server for testing
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_enhancement_server())
