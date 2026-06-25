from __future__ import annotations
import os
import sys
import json
import asyncio
import subprocess
from typing import Dict, Any, Optional
from python.helpers.deployment_interface import CloudProvider, DeploymentConfig

class RailwayHelper(CloudProvider):
    """
    Helper class to manage Railway Cloud deployments via CLI and GraphQL API.
    """
    
    API_URL = os.environ.get("RAILWAY_API_URL", "https://backboard.railway.com/graphql/v2")
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("RAILWAY_TOKEN")
        if not self.token:
            print("WARNING: RAILWAY_TOKEN not found in environment.", file=sys.stderr)

    async def run_cli(self, command: list[str], cwd: Optional[str] = None) -> tuple[int, str, str]:
        """Run a Railway CLI command."""
        env = os.environ.copy()
        if self.token:
            env["RAILWAY_TOKEN"] = self.token
            
        process = await asyncio.create_subprocess_exec(
            "railway", *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd
        )
        
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode().strip(), stderr.decode().strip()

    async def call_api(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call Railway GraphQL API."""
        if not self.token:
            raise ValueError("RAILWAY_TOKEN is required for API calls.")
            
        import httpx
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.API_URL,
                json={"query": query, "variables": variables or {}},
                headers=headers
            )
            response.raise_for_status()
            return response.json()

    async def set_variable(self, service_id: str, name: str, value: str):
        """Set an environment variable on a Railway service."""
        mutation = """
        mutation serviceVariableCreate($serviceId: String!, $name: String!, $value: String!) {
          serviceVariableCreate(
            serviceId: $serviceId
            name: $name
            value: $value
          ) {
            id
            name
          }
        }
        """
        variables = {
            "serviceId": service_id,
            "name": name,
            "value": value
        }
        return await self.call_api(mutation, variables)

    async def execute_cli_deploy(self, cwd: Optional[str] = None):
        """Execute deployment via CLI."""
        print(f"Deploying to Railway from {cwd or 'current directory'}...", file=sys.stderr)
        code, out, err = await self.run_cli(["up", "--detach"], cwd=cwd)
        if code != 0:
            raise Exception(f"Railway deployment failed: {err}")
        return out

    async def get_project_info(self, project_id: str):
        """Fetch project services and environments."""
        query = """
        query getProject($id: String!) {
          project(id: $id) {
            name
            services {
              id
              name
            }
            environments {
              id
              name
            }
          }
        }
        """
        return await self.call_api(query, {"id": project_id})

    async def get_logs(self, project_name: str, limit: int = 50) -> str:
        """
        Fetch latest execution logs via CLI.
        """
        code, out, err = await self.run_cli(["logs", "--limit", str(limit)])
        if code != 0:
            raise Exception(f"Failed to fetch Railway logs: {err}")
        return out

    async def get_health(self, project_name: str, service_name: Optional[str] = None) -> HealthReport:
        """
        Fetch health status via CLI, logs, and API discovery.
        """
        from datetime import datetime, timezone
        code, out, err = await self.run_cli(["status", "--json"])
        
        report = HealthReport(
            status="unknown",
            message="Initial health check...",
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        if code == 0:
            try:
                # Basic status parsing
                status_data = json.loads(out)
                report.status = "healthy"
                report.message = "Service status retrieved successfully via CLI."
                
                # 3a. Discover Endpoints (Domains)
                project_id = os.environ.get("RAILWAY_PROJECT_ID")
                if project_id:
                    query = """
                    query getServiceDomains($projectId: String!) {
                      project(id: $projectId) {
                        services {
                          name
                          domains {
                            domain
                          }
                        }
                      }
                    }
                    """
                    discovery_res = await self.call_api(query, {"projectId": project_id})
                    services = discovery_res.get("data", {}).get("project", {}).get("services", [])
                    for s in services:
                        if not service_name or s["name"] == service_name:
                            domains = [d["domain"] for d in s.get("domains", [])]
                            report.endpoints.extend(domains)
                
                # Scan logs for errors
                logs = await self.get_logs(project_name, limit=50)
                errors = [line for line in logs.split("\n") if "error" in line.lower()]
                if errors:
                    report.status = "warning"
                    report.errors = errors[:5]
                    report.message = f"Found {len(errors)} potential errors in recent logs."
            except Exception as e:
                report.status = "critical"
                report.message = f"Failed to parse health data: {e}"
        else:
            report.status = "critical"
            report.message = f"Railway status check failed: {err}"
            
        return report

    async def deploy(self, config: DeploymentConfig) -> str:
        """
        Deploy the given configuration.
        """
        project_id = os.environ.get("RAILWAY_PROJECT_ID")
        services_map = {}
        
        if project_id:
            try:
                info = await self.get_project_info(project_id)
                services = info.get("data", {}).get("project", {}).get("services", [])
                services_map = {s["name"]: s["id"] for s in services}
            except Exception as e:
                print(f"Warning: Failed to fetch project info: {e}", file=sys.stderr)
        
        # Sync variables
        for service_config in config.services:
            svc_id = services_map.get(service_config.name)
            if svc_id:
                # Merge env vars and secrets
                all_vars = {**service_config.environment_variables, **service_config.secrets}
                for k, v in all_vars.items():
                    print(f"Syncing variable {k} to service {service_config.name}", file=sys.stderr)
                    try:
                        await self.set_variable(svc_id, k, v)
                    except Exception as e:
                        print(f"Failed to set var {k}: {e}", file=sys.stderr)
            else:
                print(f"Warning: Service ID not found for {service_config.name}. Variables not synced.", file=sys.stderr)

        # Trigger deployment
        return await self.execute_cli_deploy()
