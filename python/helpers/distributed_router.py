from __future__ import annotations
"""
Distributed Router for Distributed Execution (Option 3)

Provides intelligent task routing across multiple nodes based on
capacity, affinity, locality, and load balancing.

Part of the AGIX parallel swarm transformation project.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from collections import defaultdict

if TYPE_CHECKING:
    from python.helpers.node_registry import NodeRegistry, NodeInfo

logger = logging.getLogger(__name__)


@dataclass
class RouterConfig:
    """Configuration for the distributed router"""
    # Scoring weights (should sum to 1.0)
    capacity_weight: float = 0.4
    affinity_weight: float = 0.3
    locality_weight: float = 0.2
    load_weight: float = 0.1
    
    # Affinity settings
    affinity_bonus: float = 0.2
    
    # Retry settings
    max_retries: int = 3
    retry_delay: float = 1.0
    
    # History settings
    max_history_size: int = 1000


@dataclass
class TaskInfo:
    """Information about a task to be routed"""
    task_id: str
    message: str
    profile: str = "default"
    priority: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    preferred_node: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "task_id": self.task_id,
            "message": self.message,
            "profile": self.profile,
            "priority": self.priority,
            "metadata": self.metadata,
            "preferred_node": self.preferred_node
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskInfo":
        """Deserialize from dictionary"""
        return cls(
            task_id=data["task_id"],
            message=data["message"],
            profile=data.get("profile", "default"),
            priority=data.get("priority", 1),
            metadata=data.get("metadata", {}),
            preferred_node=data.get("preferred_node")
        )


@dataclass
class RoutingDecision:
    """Result of a routing decision"""
    task_id: str
    selected_node: Optional[str]
    score: float
    reason: str = ""
    alternatives: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class DistributedRouter:
    """
    Distributed Router for intelligent task routing.
    
    Routes tasks to the best available node based on:
    - Available capacity
    - Profile affinity
    - Geographic locality
    - Current load
    """
    
    def __init__(
        self,
        registry: "NodeRegistry",
        config: Optional[RouterConfig] = None
    ):
        """
        Initialize the distributed router.
        
        Args:
            registry: Node registry for discovering nodes
            config: Optional router configuration
        """
        self.registry = registry
        self.config = config or RouterConfig()
        self._routing_history: List[RoutingDecision] = []
        self._node_task_counts: Dict[str, int] = defaultdict(int)
    
    async def route_task(self, task: TaskInfo) -> Optional[RoutingDecision]:
        """
        Route a task to the best available node.
        
        Args:
            task: Task information
            
        Returns:
            RoutingDecision with selected node, or None if no nodes available
        """
        # Get available nodes
        nodes = self._get_available_nodes()
        
        if not nodes:
            logger.warning(f"No nodes available for task {task.task_id}")
            return None
        
        # Check for preferred node
        if task.preferred_node:
            preferred = next(
                (n for n in nodes if n.node_id == task.preferred_node),
                None
            )
            if preferred and preferred.available_capacity > 0:
                decision = RoutingDecision(
                    task_id=task.task_id,
                    selected_node=preferred.node_id,
                    score=1.0,
                    reason="Preferred node selected",
                    alternatives=[n.node_id for n in nodes if n.node_id != preferred.node_id]
                )
                self._record_decision(decision)
                return decision
        
        # Score all nodes
        scored_nodes = []
        for node in nodes:
            score = self._calculate_total_score(node, task)
            scored_nodes.append((node, score))
        
        # Sort by score (highest first)
        scored_nodes.sort(key=lambda x: x[1], reverse=True)
        
        if not scored_nodes:
            return None
        
        # Select best node
        best_node, best_score = scored_nodes[0]
        alternatives = [n.node_id for n, _ in scored_nodes[1:4]]  # Top 3 alternatives
        
        decision = RoutingDecision(
            task_id=task.task_id,
            selected_node=best_node.node_id,
            score=best_score,
            reason=self._get_routing_reason(best_node, task),
            alternatives=alternatives
        )
        
        self._record_decision(decision)
        return decision
    
    def _get_available_nodes(self) -> List["NodeInfo"]:
        """Get list of available nodes from registry"""
        from python.helpers.node_registry import NodeStatus
        
        nodes = []
        for node in self.registry._nodes.values():
            # Only include healthy nodes with capacity
            if node.status == NodeStatus.HEALTHY:
                nodes.append(node)
        
        return nodes
    
    def _calculate_capacity_score(self, node: "NodeInfo") -> float:
        """
        Calculate capacity score for a node.
        
        Args:
            node: Node to score
            
        Returns:
            Score between 0.0 and 1.0
        """
        if node.worker_capacity == 0:
            return 0.0
        return node.available_capacity / node.worker_capacity
    
    def _calculate_affinity_score(self, node: "NodeInfo", task: TaskInfo) -> float:
        """
        Calculate affinity score based on profile matching.
        
        Args:
            node: Node to score
            task: Task being routed
            
        Returns:
            Score between 0.0 and 1.0
        """
        if task.profile in node.profiles:
            return self.config.affinity_bonus
        return 0.0
    
    def _calculate_locality_score(self, node: "NodeInfo") -> float:
        """
        Calculate locality score.
        
        For now, returns a constant. In a real implementation,
        this would consider geographic proximity.
        
        Args:
            node: Node to score
            
        Returns:
            Score between 0.0 and 1.0
        """
        # TODO: Implement actual locality scoring based on region/zone
        return 0.5
    
    def _calculate_load_score(self, node: "NodeInfo") -> float:
        """
        Calculate load score (inverse of utilization).
        
        Args:
            node: Node to score
            
        Returns:
            Score between 0.0 and 1.0
        """
        return 1.0 - node.utilization
    
    def _calculate_total_score(self, node: "NodeInfo", task: TaskInfo) -> float:
        """
        Calculate total weighted score for a node.
        
        Args:
            node: Node to score
            task: Task being routed
            
        Returns:
            Weighted score between 0.0 and 1.0
        """
        capacity_score = self._calculate_capacity_score(node)
        affinity_score = self._calculate_affinity_score(node, task)
        locality_score = self._calculate_locality_score(node)
        load_score = self._calculate_load_score(node)
        
        total = (
            capacity_score * self.config.capacity_weight +
            affinity_score * self.config.affinity_weight +
            locality_score * self.config.locality_weight +
            load_score * self.config.load_weight
        )
        
        return min(1.0, max(0.0, total))
    
    def _get_routing_reason(self, node: "NodeInfo", task: TaskInfo) -> str:
        """Generate a human-readable routing reason"""
        reasons = []
        
        if node.available_capacity > 0:
            reasons.append(f"capacity={node.available_capacity}/{node.worker_capacity}")
        
        if task.profile in node.profiles:
            reasons.append(f"profile_match={task.profile}")
        
        reasons.append(f"utilization={node.utilization:.1%}")
        
        return ", ".join(reasons)
    
    def _record_decision(self, decision: RoutingDecision) -> None:
        """Record a routing decision in history"""
        self._routing_history.append(decision)
        
        # Trim history if needed
        if len(self._routing_history) > self.config.max_history_size:
            self._routing_history = self._routing_history[-self.config.max_history_size:]
        
        # Update node task counts
        if decision.selected_node:
            self._node_task_counts[decision.selected_node] += 1
    
    def get_routing_history(self, limit: Optional[int] = None) -> List[RoutingDecision]:
        """
        Get routing history.
        
        Args:
            limit: Maximum number of decisions to return
            
        Returns:
            List of routing decisions
        """
        if limit is None:
            return list(self._routing_history)
        return list(self._routing_history[-limit:])
    
    def clear_routing_history(self) -> None:
        """Clear routing history"""
        self._routing_history.clear()
        self._node_task_counts.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get router statistics.
        
        Returns:
            Dictionary of statistics
        """
        total_routed = len(self._routing_history)
        
        if total_routed == 0:
            return {
                "total_routed": 0,
                "average_score": 0.0,
                "node_distribution": {}
            }
        
        average_score = sum(d.score for d in self._routing_history) / total_routed
        
        # Calculate node distribution
        node_distribution = defaultdict(int)
        for decision in self._routing_history:
            if decision.selected_node:
                node_distribution[decision.selected_node] += 1
        
        return {
            "total_routed": total_routed,
            "average_score": average_score,
            "node_distribution": dict(node_distribution),
            "history_size": len(self._routing_history)
        }


def create_distributed_router(
    registry: "NodeRegistry",
    config: Optional[RouterConfig] = None
) -> DistributedRouter:
    """
    Factory function to create a DistributedRouter.
    
    Args:
        registry: Node registry for discovering nodes
        config: Optional router configuration
        
    Returns:
        Configured DistributedRouter instance
    """
    return DistributedRouter(registry, config)
