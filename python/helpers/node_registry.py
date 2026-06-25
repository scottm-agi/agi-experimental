from __future__ import annotations
"""
Node Registry for Distributed Execution (Option 3)

Provides service registration, discovery, health checks, and leader election
using Consul as the service registry backend.

Part of the AGIX parallel swarm transformation project.
"""

import asyncio
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class NodeStatus(Enum):
    """Status of a node in the cluster"""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"
    OFFLINE = "offline"


@dataclass
class NodeInfo:
    """Information about a node in the cluster"""
    node_id: str
    host: str
    port: int
    worker_capacity: int
    active_workers: int
    status: NodeStatus = NodeStatus.HEALTHY
    is_leader: bool = False
    profiles: List[str] = field(default_factory=list)
    last_heartbeat: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def available_capacity(self) -> int:
        """Calculate available worker capacity"""
        return max(0, self.worker_capacity - self.active_workers)
    
    @property
    def utilization(self) -> float:
        """Calculate utilization percentage (0.0 - 1.0)"""
        if self.worker_capacity == 0:
            return 0.0
        return self.active_workers / self.worker_capacity
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "worker_capacity": self.worker_capacity,
            "active_workers": self.active_workers,
            "status": self.status.value,
            "is_leader": self.is_leader,
            "profiles": self.profiles,
            "last_heartbeat": self.last_heartbeat,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeInfo":
        """Deserialize from dictionary"""
        return cls(
            node_id=data["node_id"],
            host=data["host"],
            port=data["port"],
            worker_capacity=data["worker_capacity"],
            active_workers=data["active_workers"],
            status=NodeStatus(data.get("status", "healthy")),
            is_leader=data.get("is_leader", False),
            profiles=data.get("profiles", []),
            last_heartbeat=data.get("last_heartbeat", time.time()),
            metadata=data.get("metadata", {})
        )


@dataclass
class RegistryConfig:
    """Configuration for the node registry"""
    consul_host: str = "localhost"
    consul_port: int = 8500
    service_name: str = "agix-worker"
    heartbeat_interval: float = 10.0
    health_check_interval: float = 30.0
    node_timeout: float = 60.0
    leader_ttl: float = 30.0
    leader_key: str = "agix/leader"


class NodeRegistry:
    """
    Node Registry for distributed agent execution.
    
    Provides:
    - Service registration with Consul
    - Node discovery
    - Health monitoring
    - Leader election
    """
    
    def __init__(self, config: RegistryConfig, consul_client: Any = None):
        """
        Initialize the node registry.
        
        Args:
            config: Registry configuration
            consul_client: Optional Consul client (for testing/mocking)
        """
        self.config = config
        self._consul = consul_client
        self._nodes: Dict[str, NodeInfo] = {}
        self._local_node: Optional[NodeInfo] = None
        self._is_leader: bool = False
        self._session_id: Optional[str] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running: bool = False
    
    @property
    def is_leader(self) -> bool:
        """Check if this node is the leader"""
        return self._is_leader
    
    @property
    def local_node(self) -> Optional[NodeInfo]:
        """Get the local node info"""
        return self._local_node
    
    async def register_local_node(
        self,
        host: str,
        port: int,
        worker_capacity: int,
        profiles: Optional[List[str]] = None
    ) -> NodeInfo:
        """
        Register this node with the service registry.
        
        Args:
            host: Node host address
            port: Node port
            worker_capacity: Maximum number of workers
            profiles: List of supported agent profiles
            
        Returns:
            NodeInfo for the registered node
        """
        node_id = f"{host}:{port}-{uuid.uuid4().hex[:8]}"
        
        self._local_node = NodeInfo(
            node_id=node_id,
            host=host,
            port=port,
            worker_capacity=worker_capacity,
            active_workers=0,
            profiles=profiles or [],
            last_heartbeat=time.time()
        )
        
        # Register with Consul if available
        if self._consul:
            try:
                self._consul.agent.service.register(
                    name=self.config.service_name,
                    service_id=node_id,
                    address=host,
                    port=port,
                    meta={
                        "worker_capacity": str(worker_capacity),
                        "active_workers": "0",
                        "profiles": ",".join(profiles or [])
                    },
                    check={
                        "ttl": f"{int(self.config.health_check_interval)}s",
                        "deregister_critical_service_after": "5m"
                    }
                )
                logger.info(f"Registered node {node_id} with Consul")
            except Exception as e:
                logger.error(f"Failed to register with Consul: {e}")
        
        # Add to local cache
        self._nodes[node_id] = self._local_node
        
        return self._local_node
    
    async def deregister_local_node(self) -> bool:
        """
        Deregister this node from the service registry.
        
        Returns:
            True if successful
        """
        if not self._local_node:
            return True
        
        node_id = self._local_node.node_id
        
        # Deregister from Consul if available
        if self._consul:
            try:
                self._consul.agent.service.deregister(node_id)
                logger.info(f"Deregistered node {node_id} from Consul")
            except Exception as e:
                logger.error(f"Failed to deregister from Consul: {e}")
        
        # Remove from local cache
        if node_id in self._nodes:
            del self._nodes[node_id]
        
        self._local_node = None
        return True
    
    async def update_local_status(
        self,
        active_workers: Optional[int] = None,
        status: Optional[NodeStatus] = None
    ) -> None:
        """
        Update the local node's status.
        
        Args:
            active_workers: Current number of active workers
            status: New node status
        """
        if not self._local_node:
            return
        
        if active_workers is not None:
            self._local_node.active_workers = active_workers
        
        if status is not None:
            self._local_node.status = status
        
        self._local_node.last_heartbeat = time.time()
        
        # Update in Consul if available
        if self._consul:
            try:
                self._consul.kv.put(
                    f"nodes/{self._local_node.node_id}/status",
                    self._local_node.to_dict().__str__().encode()
                )
            except Exception as e:
                logger.error(f"Failed to update status in Consul: {e}")
    
    async def discover_nodes(self) -> List[NodeInfo]:
        """
        Discover all registered nodes.
        
        Returns:
            List of NodeInfo for all discovered nodes
        """
        if not self._consul:
            return list(self._nodes.values())
        
        try:
            _, services = self._consul.catalog.service(self.config.service_name)
            
            nodes = []
            for svc in services:
                meta = svc.get("ServiceMeta", {})
                node = NodeInfo(
                    node_id=svc["ServiceID"],
                    host=svc["ServiceAddress"],
                    port=svc["ServicePort"],
                    worker_capacity=int(meta.get("worker_capacity", 0)),
                    active_workers=int(meta.get("active_workers", 0)),
                    profiles=meta.get("profiles", "").split(",") if meta.get("profiles") else []
                )
                nodes.append(node)
                self._nodes[node.node_id] = node
            
            return nodes
        except Exception as e:
            logger.error(f"Failed to discover nodes: {e}")
            return list(self._nodes.values())
    
    async def get_healthy_nodes(self) -> List[NodeInfo]:
        """
        Get only healthy nodes.
        
        Returns:
            List of healthy NodeInfo
        """
        if not self._consul:
            return [n for n in self._nodes.values() 
                    if n.status == NodeStatus.HEALTHY and self.is_node_healthy(n)]
        
        try:
            _, services = self._consul.health.service(
                self.config.service_name,
                passing=False  # Get all, we'll filter
            )
            
            healthy_nodes = []
            for svc in services:
                # Check if all health checks are passing
                checks = svc.get("Checks", [])
                all_passing = all(c.get("Status") == "passing" for c in checks)
                
                if all_passing:
                    service = svc["Service"]
                    meta = service.get("Meta", {})
                    node = NodeInfo(
                        node_id=service["ID"],
                        host=service["Address"],
                        port=service["Port"],
                        worker_capacity=int(meta.get("worker_capacity", 0)),
                        active_workers=int(meta.get("active_workers", 0)),
                        status=NodeStatus.HEALTHY
                    )
                    healthy_nodes.append(node)
                    self._nodes[node.node_id] = node
            
            return healthy_nodes
        except Exception as e:
            logger.error(f"Failed to get healthy nodes: {e}")
            return [n for n in self._nodes.values() if n.status == NodeStatus.HEALTHY]
    
    async def get_node(self, node_id: str) -> Optional[NodeInfo]:
        """
        Get a specific node by ID.
        
        Args:
            node_id: The node ID to look up
            
        Returns:
            NodeInfo if found, None otherwise
        """
        return self._nodes.get(node_id)
    
    async def acquire_leadership(self) -> bool:
        """
        Attempt to acquire leadership.
        
        Returns:
            True if leadership was acquired
        """
        if not self._local_node:
            logger.warning("Cannot acquire leadership: no local node registered")
            return False
        
        if not self._consul:
            # Without Consul, just become leader
            self._is_leader = True
            self._local_node.is_leader = True
            return True
        
        try:
            # Create a session for leader election
            self._session_id = self._consul.session.create(
                ttl=int(self.config.leader_ttl),
                behavior="delete"
            )
            
            # Try to acquire the leader lock
            acquired = self._consul.kv.put(
                self.config.leader_key,
                self._local_node.node_id.encode(),
                acquire=self._session_id
            )
            
            if acquired:
                self._is_leader = True
                self._local_node.is_leader = True
                logger.info(f"Node {self._local_node.node_id} acquired leadership")
            else:
                logger.info(f"Node {self._local_node.node_id} failed to acquire leadership")
            
            return acquired
        except Exception as e:
            logger.error(f"Failed to acquire leadership: {e}")
            return False
    
    async def release_leadership(self) -> bool:
        """
        Release leadership.
        
        Returns:
            True if leadership was released
        """
        if not self._is_leader:
            return True
        
        self._is_leader = False
        if self._local_node:
            self._local_node.is_leader = False
        
        if self._consul and self._session_id:
            try:
                self._consul.kv.delete(self.config.leader_key)
                self._consul.session.destroy(self._session_id)
                self._session_id = None
                logger.info("Released leadership")
            except Exception as e:
                logger.error(f"Failed to release leadership: {e}")
        
        return True
    
    async def get_current_leader(self) -> Optional[NodeInfo]:
        """
        Get the current leader node.
        
        Returns:
            NodeInfo of the leader, or None if no leader
        """
        if not self._consul:
            # Without Consul, check local cache
            for node in self._nodes.values():
                if node.is_leader:
                    return node
            return None
        
        try:
            _, data = self._consul.kv.get(self.config.leader_key)
            if data and data.get("Value"):
                leader_id = data["Value"].decode()
                return self._nodes.get(leader_id)
        except Exception as e:
            logger.error(f"Failed to get current leader: {e}")
        
        return None
    
    async def send_heartbeat(self) -> None:
        """Send a heartbeat to update node status"""
        if not self._local_node:
            return
        
        self._local_node.last_heartbeat = time.time()
        
        if self._consul:
            try:
                # Update TTL check
                self._consul.agent.check.ttl_pass(
                    f"service:{self._local_node.node_id}"
                )
                
                # Update node metadata
                self._consul.kv.put(
                    f"nodes/{self._local_node.node_id}/heartbeat",
                    str(self._local_node.last_heartbeat).encode()
                )
            except Exception as e:
                logger.error(f"Failed to send heartbeat: {e}")
    
    def is_node_healthy(self, node: NodeInfo) -> bool:
        """
        Check if a node is healthy based on heartbeat.
        
        Args:
            node: The node to check
            
        Returns:
            True if the node is healthy
        """
        if node.status != NodeStatus.HEALTHY:
            return False
        
        time_since_heartbeat = time.time() - node.last_heartbeat
        return time_since_heartbeat < self.config.node_timeout
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get registry statistics.
        
        Returns:
            Dictionary of statistics
        """
        healthy_count = sum(
            1 for n in self._nodes.values() 
            if n.status == NodeStatus.HEALTHY and self.is_node_healthy(n)
        )
        
        total_capacity = sum(n.worker_capacity for n in self._nodes.values())
        total_active = sum(n.active_workers for n in self._nodes.values())
        
        has_leader = any(n.is_leader for n in self._nodes.values())
        
        return {
            "total_nodes": len(self._nodes),
            "healthy_nodes": healthy_count,
            "total_capacity": total_capacity,
            "total_active": total_active,
            "available_capacity": total_capacity - total_active,
            "has_leader": has_leader,
            "is_local_leader": self._is_leader,
            "local_node_id": self._local_node.node_id if self._local_node else None
        }
    
    async def start(self) -> None:
        """Start the registry (heartbeat loop)"""
        if self._running:
            return
        
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Node registry started")
    
    async def stop(self) -> None:
        """Stop the registry"""
        self._running = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                logger.debug("[NodeRegistry] Heartbeat task cancelled during stop")
        
        await self.release_leadership()
        await self.deregister_local_node()
        logger.info("Node registry stopped")
    
    async def _heartbeat_loop(self) -> None:
        """Background heartbeat loop"""
        while self._running:
            try:
                await self.send_heartbeat()
                await asyncio.sleep(self.config.heartbeat_interval)
            except asyncio.CancelledError:
                logger.debug("[NodeRegistry] Heartbeat loop cancelled — shutting down gracefully")
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(1)


def create_node_registry(config: Optional[RegistryConfig] = None) -> NodeRegistry:
    """
    Factory function to create a NodeRegistry.
    
    Args:
        config: Optional registry configuration
        
    Returns:
        Configured NodeRegistry instance
    """
    if config is None:
        config = RegistryConfig()
    
    return NodeRegistry(config)
