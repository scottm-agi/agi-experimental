from __future__ import annotations
from typing import Protocol, List, Dict, Any, Optional
from pydantic import BaseModel, Field

class ServiceConfig(BaseModel):
    """
    Abstract definition of a service to be deployed.
    """
    name: str
    environment_variables: Dict[str, str] = Field(default_factory=dict)
    secrets: Dict[str, str] = Field(default_factory=dict)
    # Future expansion: build_command, start_command, etc.

class DeploymentConfig(BaseModel):
    """
    Configuration for a full deployment.
    """
    project_name: str
    services: List[ServiceConfig] = Field(default_factory=list)

class HealthReport(BaseModel):
    """
    Standardized health report for a service.
    """
    status: str  # healthy, warning, critical, unknown
    message: str
    endpoints: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: "now")

class CloudProvider(Protocol):
    """
    Protocol that all cloud deployment helpers must implement.
    """
    async def deploy(self, config: DeploymentConfig) -> str:
        """
        Deploy the given configuration.
        """
        ...

class MonitoringProvider(Protocol):
    """
    Protocol for cloud-specific observability and health checks.
    """
    async def get_health(self, project_name: str, service_name: Optional[str] = None) -> HealthReport:
        """
        Fetch health status and logs for a project/service.
        """
        ...
    
    async def get_logs(self, project_name: str, limit: int = 50) -> str:
        """
        Fetch latest execution logs.
        """
        ...
