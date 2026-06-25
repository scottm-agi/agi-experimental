from __future__ import annotations
"""
Port Manager for AGIX Projects
=====================================

Manages dynamic port allocation for project services to avoid conflicts
when running multiple projects simultaneously.

Features:
- Dynamic port allocation based on project name hash
- Port registry persistence in .agix.proj/ports.json (legacy: .agix.proj/ports.json)
- Service lifecycle tracking (start/stop/status)
- Health check integration
"""

import os
import json
import socket
from python.helpers.hashing import dedup_hash_short
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

import logging

from python.helpers.projects import PROJECT_META_DIR, LEGACY_PROJECT_META_DIR

logger = logging.getLogger("agix.port_manager")


@dataclass
class ServiceInfo:
    """Information about a running service"""
    port: int
    pid: Optional[int] = None
    status: str = "stopped"  # stopped, starting, running, error
    started_at: Optional[str] = None
    health_endpoint: Optional[str] = None
    command: Optional[str] = None


@dataclass
class ProjectPorts:
    """Port allocation for a project"""
    project_name: str
    services: Dict[str, ServiceInfo]
    created_at: str
    updated_at: str


class PortManager:
    """
    Manages port allocation for project services.
    
    Port Ranges:
    - Frontend (React, Vue, etc.): 3000-3099
    - Backend (Flask, Express, etc.): 5000-5099
    - Database proxies: 5400-5499
    - Other services: 8000-8099
    """
    
    # Port ranges for different service types — MUST NOT overlap
    # CRITICAL: These ranges MUST match docker-compose port mappings:
    #   - 5100-5199 mapped directly (host:container)
    #   - 5400-5499 mapped as 15400-15499 on host
    # Any port outside these ranges will NOT be accessible from the host!
    PORT_RANGES = {
        "frontend": (5100, 5139),   # 40 ports — compose: 5100-5199
        "backend": (5140, 5179),    # 40 ports — compose: 5100-5199
        "other": (5180, 5199),      # 20 ports — compose: 5100-5199
        "database": (5400, 5499),   # 100 ports — compose: 15400-15499
    }
    
    # Default ports for common frameworks — staggered to reduce collisions
    DEFAULT_PORTS = {
        "react": 5100,
        "vue": 5105,
        "vite": 5110,
        "next": 5115,
        "flask": 5150,
        "express": 5155,
        "fastapi": 5160,
        "django": 5165,
        "node": 5120,
        "python": 5170,
    }
    
    def __init__(self, projects_dir: str = None):
        if projects_dir is None:
            projects_dir = "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects"
        self.projects_dir = Path(projects_dir)
        self._port_cache: Dict[str, ProjectPorts] = {}
        
    def _get_ports_file(self, project_name: str) -> Path:
        """Get the ports.json file path for a project"""
        new_path = self.projects_dir / project_name / PROJECT_META_DIR / "ports.json"
        if new_path.exists():
            return new_path
        legacy_path = self.projects_dir / project_name / LEGACY_PROJECT_META_DIR / "ports.json"
        if legacy_path.exists():
            return legacy_path
        return new_path  # Default to new convention
    
    def _hash_to_offset(self, project_name: str, service_type: str) -> int:
        """Generate a deterministic port offset from project name.
        
        Issue #1008: Uses universal hashing utility instead of raw hashlib.
        """
        hash_input = f"{project_name}:{service_type}"
        hash_val = int(dedup_hash_short(hash_input, length=8), 16)
        return hash_val % 100  # 0-99 offset within range
    
    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available for binding"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.bind(("127.0.0.1", port))
                return True
        except (socket.error, OSError):
            return False
    
    def _get_all_registered_ports(self, exclude_project: Optional[str] = None) -> set:
        """Scan ALL projects' ports.json files to find registered ports.
        
        This prevents cross-project overlap by checking the registry,
        not just socket availability.
        
        Args:
            exclude_project: Project name to exclude (so a project
                           doesn't block itself when re-allocating)
        Returns:
            Set of port numbers that are registered to other projects
        """
        registered = set()
        
        if not self.projects_dir.exists():
            return registered
        
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            
            project_name = project_dir.name
            if exclude_project and project_name == exclude_project:
                continue
            
            # Check both new and legacy meta dirs
            ports_file = project_dir / PROJECT_META_DIR / "ports.json"
            if not ports_file.exists():
                ports_file = project_dir / LEGACY_PROJECT_META_DIR / "ports.json"
                if not ports_file.exists():
                    continue
            
            try:
                with open(ports_file) as f:
                    data = json.load(f)
                for service_info in data.get("services", {}).values():
                    port = service_info.get("port")
                    if port is not None:
                        registered.add(port)
            except (json.JSONDecodeError, IOError):
                continue
        
        return registered
    
    def _find_available_port(
        self, 
        start_port: int, 
        end_port: int, 
        preferred: Optional[int] = None,
        excluded_ports: Optional[set] = None
    ) -> int:
        """Find an available port in the given range.
        
        A port is available only if:
        1. It is not registered to another project (excluded_ports)
        2. It is not currently bound (socket check)
        """
        if excluded_ports is None:
            excluded_ports = set()
        
        # Try preferred port first
        if preferred is not None and start_port <= preferred <= end_port:
            if preferred not in excluded_ports and self._is_port_available(preferred):
                return preferred
        
        # Scan range for available port
        for port in range(start_port, end_port + 1):
            if port not in excluded_ports and self._is_port_available(port):
                return port
        
        raise RuntimeError(f"No available ports in range {start_port}-{end_port}")
    
    def _evict_oldest_project(
        self,
        exclude_project: Optional[str] = None,
        service_type: Optional[str] = None,
    ) -> bool:
        """Evict port allocations from the oldest stale project.
        
        Scans all projects' ports.json files, finds the one with the oldest
        `updated_at` timestamp (excluding the requesting project), and removes
        its service entries that match the given service_type — freeing those
        ports for reallocation.
        
        Args:
            exclude_project: Never evict this project (the one requesting ports)
            service_type: Only evict services of this type (if specified)
            
        Returns:
            True if a project was evicted, False if nothing to evict
        """
        if not self.projects_dir.exists():
            return False
        
        oldest_project = None
        oldest_time = None
        oldest_ports_file = None
        
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            
            project_name = project_dir.name
            if exclude_project and project_name == exclude_project:
                continue
            
            ports_file = project_dir / PROJECT_META_DIR / "ports.json"
            if not ports_file.exists():
                ports_file = project_dir / LEGACY_PROJECT_META_DIR / "ports.json"
                if not ports_file.exists():
                    continue
            
            try:
                with open(ports_file) as f:
                    data = json.load(f)
                updated_at = data.get("updated_at", data.get("created_at", ""))
                if not updated_at:
                    continue
                
                # Check if this project has any services (nothing to evict if empty)
                services = data.get("services", {})
                if not services:
                    continue
                
                if oldest_time is None or updated_at < oldest_time:
                    oldest_time = updated_at
                    oldest_project = project_name
                    oldest_ports_file = ports_file
            except (json.JSONDecodeError, IOError):
                continue
        
        if oldest_project is None or oldest_ports_file is None:
            return False
        
        # Evict: remove service entries from the oldest project
        try:
            with open(oldest_ports_file) as f:
                data = json.load(f)
            
            services = data.get("services", {})
            if service_type:
                # Only remove services matching the requested type
                keys_to_remove = [
                    k for k, v in services.items()
                    if v.get("type") == service_type
                ]
            else:
                keys_to_remove = list(services.keys())
            
            evicted_ports = []
            for key in keys_to_remove:
                evicted_ports.append(services[key].get("port"))
                del services[key]
            
            data["updated_at"] = datetime.now().isoformat()
            with open(oldest_ports_file, "w") as f:
                json.dump(data, f, indent=2)
            
            logger.info(
                f"[PORT_MANAGER] Self-healing: evicted {len(evicted_ports)} port(s) "
                f"{evicted_ports} from oldest project '{oldest_project}' "
                f"(last updated: {oldest_time})"
            )
            return True
            
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[PORT_MANAGER] Failed to evict from {oldest_project}: {e}")
            return False

    def allocate_port(
        self, 
        project_name: str, 
        service_name: str,
        service_type: str = "backend",
        preferred_port: Optional[int] = None
    ) -> int:
        """
        Allocate a port for a project service.
        
        Cross-project aware: checks ALL projects' registries before allocating
        to prevent port overlap even when sockets appear free.
        
        Self-healing: on exhaustion, evicts the oldest stale project's port
        registrations and retries once before raising.
        
        Args:
            project_name: Name of the project
            service_name: Name of the service (e.g., "api", "web", "db")
            service_type: Type of service (frontend, backend, database, other)
            preferred_port: Preferred port to use if available
            
        Returns:
            Allocated port number
        """
        # Get port range for service type
        port_range = self.PORT_RANGES.get(service_type, self.PORT_RANGES["other"])
        start_port, end_port = port_range
        
        # Calculate preferred port based on project hash if not specified
        if preferred_port is None:
            offset = self._hash_to_offset(project_name, service_name)
            preferred_port = start_port + offset
        
        # Get ports registered to OTHER projects (exclude self to allow re-allocation)
        registered_ports = self._get_all_registered_ports(exclude_project=project_name)
        
        try:
            # Find available port (cross-project aware)
            port = self._find_available_port(start_port, end_port, preferred_port, registered_ports)
        except RuntimeError:
            # Self-healing: evict oldest project and retry
            logger.warning(
                f"[PORT_MANAGER] Port exhaustion in range {start_port}-{end_port}. "
                f"Attempting self-healing eviction..."
            )
            evicted = self._evict_oldest_project(
                exclude_project=project_name,
                service_type=service_type,
            )
            if not evicted:
                raise  # Nothing to evict — truly exhausted
            
            # Retry with updated registry
            registered_ports = self._get_all_registered_ports(exclude_project=project_name)
            port = self._find_available_port(
                start_port, end_port, preferred_port, registered_ports
            )
        
        # Save allocation
        self._save_port_allocation(project_name, service_name, port, service_type)
        
        return port
    
    def _save_port_allocation(
        self, 
        project_name: str, 
        service_name: str, 
        port: int,
        service_type: str
    ):
        """Save port allocation to project's ports.json"""
        ports_file = self._get_ports_file(project_name)
        
        # Ensure directory exists
        ports_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing or create new
        if ports_file.exists():
            with open(ports_file) as f:
                data = json.load(f)
        else:
            data = {
                "project_name": project_name,
                "services": {},
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        
        # Update service info
        data["services"][service_name] = {
            "port": port,
            "type": service_type,
            "pid": None,
            "status": "allocated",
            "allocated_at": datetime.now().isoformat(),
        }
        data["updated_at"] = datetime.now().isoformat()
        
        # Save
        with open(ports_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def get_port(self, project_name: str, service_name: str) -> Optional[int]:
        """Get the allocated port for a service"""
        ports_file = self._get_ports_file(project_name)
        
        if not ports_file.exists():
            return None
        
        with open(ports_file) as f:
            data = json.load(f)
        
        service = data.get("services", {}).get(service_name)
        return service.get("port") if service else None
    
    def get_all_ports(self, project_name: str) -> Dict[str, int]:
        """Get all allocated ports for a project"""
        ports_file = self._get_ports_file(project_name)
        
        if not ports_file.exists():
            return {}
        
        with open(ports_file) as f:
            data = json.load(f)
        
        return {
            name: info.get("port")
            for name, info in data.get("services", {}).items()
        }
    
    def update_service_status(
        self, 
        project_name: str, 
        service_name: str,
        status: str,
        pid: Optional[int] = None
    ):
        """Update the status of a service"""
        ports_file = self._get_ports_file(project_name)
        
        if not ports_file.exists():
            return
        
        with open(ports_file) as f:
            data = json.load(f)
        
        if service_name in data.get("services", {}):
            data["services"][service_name]["status"] = status
            if pid is not None:
                data["services"][service_name]["pid"] = pid
            if status == "running":
                data["services"][service_name]["started_at"] = datetime.now().isoformat()
            data["updated_at"] = datetime.now().isoformat()
            
            with open(ports_file, "w") as f:
                json.dump(data, f, indent=2)
    
    def release_port(self, project_name: str, service_name: str):
        """Release a port allocation"""
        ports_file = self._get_ports_file(project_name)
        
        if not ports_file.exists():
            return
        
        with open(ports_file) as f:
            data = json.load(f)
        
        if service_name in data.get("services", {}):
            del data["services"][service_name]
            data["updated_at"] = datetime.now().isoformat()
            
            with open(ports_file, "w") as f:
                json.dump(data, f, indent=2)
    
    def get_running_services(self) -> List[Dict]:
        """Get all running services across all projects"""
        running = []
        
        if not self.projects_dir.exists():
            return running
        
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            
            ports_file = project_dir / PROJECT_META_DIR / "ports.json"
            if not ports_file.exists():
                ports_file = project_dir / LEGACY_PROJECT_META_DIR / "ports.json"
                if not ports_file.exists():
                    continue
            
            with open(ports_file) as f:
                data = json.load(f)
            
            for service_name, info in data.get("services", {}).items():
                if info.get("status") == "running":
                    running.append({
                        "project": data.get("project_name"),
                        "service": service_name,
                        "port": info.get("port"),
                        "pid": info.get("pid"),
                        "started_at": info.get("started_at"),
                    })
        
        return running
    
    def suggest_ports(self, project_name: str, framework: str) -> Dict[str, int]:
        """
        Suggest ports for a project based on framework.
        
        Returns dict of service_name -> suggested_port
        """
        suggestions = {}
        
        # Determine service types based on framework
        if framework in ["react", "vue", "next", "vite"]:
            suggestions["frontend"] = self.allocate_port(
                project_name, "frontend", "frontend"
            )
        elif framework in ["flask", "fastapi", "django", "express"]:
            suggestions["backend"] = self.allocate_port(
                project_name, "backend", "backend"
            )
        elif framework == "fullstack":
            suggestions["frontend"] = self.allocate_port(
                project_name, "frontend", "frontend"
            )
            suggestions["backend"] = self.allocate_port(
                project_name, "backend", "backend"
            )
        else:
            # Generic - allocate backend port
            suggestions["main"] = self.allocate_port(
                project_name, "main", "backend"
            )
        
        return suggestions
    
    def cleanup_stale_services(self) -> List[Dict]:
        """
        Clean up stale service entries where the process is no longer running.
        
        Returns list of cleaned up services.
        """
        cleaned = []
        
        if not self.projects_dir.exists():
            return cleaned
        
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            
            ports_file = project_dir / PROJECT_META_DIR / "ports.json"
            if not ports_file.exists():
                ports_file = project_dir / LEGACY_PROJECT_META_DIR / "ports.json"
                if not ports_file.exists():
                    continue
            
            with open(ports_file) as f:
                data = json.load(f)
            
            modified = False
            for service_name, info in list(data.get("services", {}).items()):
                if info.get("status") == "running":
                    pid = info.get("pid")
                    if pid:
                        # Check if process is still running
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            # Process not running
                            info["status"] = "stopped"
                            info["pid"] = None
                            modified = True
                            cleaned.append({
                                "project": data.get("project_name"),
                                "service": service_name,
                                "port": info.get("port"),
                                "old_pid": pid,
                            })
            
            if modified:
                data["updated_at"] = datetime.now().isoformat()
                with open(ports_file, "w") as f:
                    json.dump(data, f, indent=2)
        
        return cleaned
    
    def check_health(
        self,
        port: int,
        endpoints: Optional[List[str]] = None,
        timeout: float = 5.0,
        project_name: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Check if a service is healthy by probing health endpoints.
        
        Args:
            port: Port to check
            endpoints: List of health endpoints to try (default: common ones)
            timeout: Request timeout in seconds
            project_name: If provided, verify the responding server belongs
                          to this project (prevents port collision masking)
            
        Returns:
            Tuple of (is_healthy, message)
        """
        if endpoints is None:
            endpoints = ["/health", "/healthz", "/api/health", "/", "/ping"]
        
        for endpoint in endpoints:
            url = f"http://127.0.0.1:{port}{endpoint}"
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    if response.status < 400:
                        # If project_name is given, verify identity
                        if project_name:
                            body = ""
                            try:
                                body = response.read().decode("utf-8", errors="replace")
                            except Exception:
                                pass
                            
                            identity_result = self._verify_project_identity(
                                body, project_name
                            )
                            if identity_result is False:
                                return False, (
                                    f"Wrong project on port {port} "
                                    f"(identity mismatch — expected '{project_name}')"
                                )
                        
                        return True, f"Healthy at {endpoint} (status {response.status})"
            except urllib.error.HTTPError as e:
                # 4xx/5xx errors - service is responding but not healthy
                if e.code < 500:
                    return True, f"Responding at {endpoint} (status {e.code})"
            except (urllib.error.URLError, socket.timeout, OSError):
                # Connection failed, try next endpoint
                continue
        
        return False, f"No response on port {port}"
    
    def _verify_project_identity(
        self, response_body: str, expected_project: str
    ) -> Optional[bool]:
        """
        Verify if a response body belongs to the expected project.
        
        Returns:
            True  — response contains expected project name
            False — response contains a DIFFERENT project name
            None  — cannot determine (no project markers found)
        """
        if not response_body or not expected_project:
            return None
        
        body_lower = response_body.lower()
        expected_lower = expected_project.lower()
        
        # Normalize project name variants (kebab-case, snake_case, title)
        expected_variants = {
            expected_lower,
            expected_lower.replace("-", "_"),
            expected_lower.replace("_", "-"),
            expected_lower.replace("-", " "),
            expected_lower.replace("_", " "),
        }
        
        # Check if expected project name appears in the response
        for variant in expected_variants:
            if variant in body_lower:
                return True
        
        # Check if a DIFFERENT known project name pattern appears
        # Look for project-name patterns in <title>, package.json name, etc.
        import re
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", body_lower)
        if title_match:
            title_text = title_match.group(1).strip()
            # If we found a title and it doesn't match any expected variant
            if title_text and title_text not in ("", "react app", "next app", 
                                                   "vite app", "loading...", 
                                                   "index", "app"):
                # There's a meaningful title that doesn't match our project
                for variant in expected_variants:
                    if variant in title_text:
                        return True  # Match found in title
                # Title exists but doesn't match — likely a different project
                return False
        
        # No project markers found — can't determine
        return None
    
    def kill_project_dev_servers(self, project_name: str) -> int:
        """
        Kill orphaned dev server processes for a specific project.
        
        Searches for node/next/vite processes running from the project's
        directory and terminates them.
        
        Args:
            project_name: Name of the project whose servers to kill
            
        Returns:
            Number of processes killed
        """
        import subprocess
        import signal
        
        killed = 0
        project_dir = self.projects_dir / project_name
        
        if not project_dir.exists():
            return 0
        
        try:
            # Find node processes with CWD in project directory
            result = subprocess.run(
                ["pgrep", "-f", f"node.*{project_name}"],
                capture_output=True, text=True, timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                pids = [
                    int(p) for p in result.stdout.strip().split("\n")
                    if p.strip().isdigit()
                ]
                
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        killed += 1
                        logger.info(
                            f"Killed orphaned dev server PID {pid} "
                            f"for project '{project_name}'"
                        )
                    except OSError:
                        pass  # Process already dead
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # pgrep not available or timed out
            pass
        
        return killed
    
    def purge_project_ports(self, project_name: str) -> int:
        """
        Remove ALL port registrations for a project.
        
        Used during inter-test cleanup to ensure no stale registrations
        persist from previous test runs.
        
        Args:
            project_name: Name of the project to purge
            
        Returns:
            Number of service registrations purged
        """
        project_dir = self.projects_dir / project_name
        
        if not project_dir.exists():
            return 0
        
        # Try both meta dir conventions
        for meta_dir_name in [PROJECT_META_DIR, LEGACY_PROJECT_META_DIR]:
            ports_file = project_dir / meta_dir_name / "ports.json"
            if ports_file.exists():
                try:
                    with open(ports_file) as f:
                        data = json.load(f)
                    
                    services = data.get("services", {})
                    purged_count = len(services)
                    
                    if purged_count > 0:
                        data["services"] = {}
                        data["updated_at"] = datetime.now().isoformat()
                        with open(ports_file, "w") as f:
                            json.dump(data, f, indent=2)
                        
                        logger.info(
                            f"Purged {purged_count} port registrations "
                            f"for project '{project_name}'"
                        )
                    
                    return purged_count
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Error purging ports for {project_name}: {e}")
                    return 0
        
        return 0
    
    def cleanup_all_stopped_services(self) -> int:
        """
        Remove all stopped service registrations across all projects.
        
        Preserves running services but clears any stopped/stale entries
        to prevent resource tracking inflation.
        
        Returns:
            Total number of stopped services purged
        """
        total_purged = 0
        
        if not self.projects_dir.exists():
            return 0
        
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            
            for meta_dir_name in [PROJECT_META_DIR, LEGACY_PROJECT_META_DIR]:
                ports_file = project_dir / meta_dir_name / "ports.json"
                if not ports_file.exists():
                    continue
                
                try:
                    with open(ports_file) as f:
                        data = json.load(f)
                    
                    modified = False
                    services = data.get("services", {})
                    
                    for service_name in list(services.keys()):
                        info = services[service_name]
                        if info.get("status") != "running":
                            del services[service_name]
                            total_purged += 1
                            modified = True
                    
                    if modified:
                        data["updated_at"] = datetime.now().isoformat()
                        with open(ports_file, "w") as f:
                            json.dump(data, f, indent=2)
                
                except (json.JSONDecodeError, IOError):
                    continue
                
                break  # Only process one meta dir per project
        
        return total_purged
    
    def kill_all_dev_servers(self) -> int:
        """
        Kill ALL dev server processes (node/next/vite/webpack) across ALL projects.
        
        Unlike kill_project_dev_servers() which targets a single project,
        this method kills ALL dev server processes system-wide. Used during
        inter-test cleanup to ensure zero resource leakage between tests.
        
        Returns:
            Number of processes killed
        """
        import subprocess
        import signal
        
        killed = 0
        
        # Patterns that identify dev server processes
        # These match: next dev, vite, webpack-dev-server, react-scripts start
        dev_server_patterns = [
            "next dev",
            "next start",
            "vite",
            "webpack-dev-server",
            "react-scripts start",
        ]
        
        for pattern in dev_server_patterns:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", pattern],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    pids = [
                        int(p) for p in result.stdout.strip().split("\n")
                        if p.strip().isdigit()
                    ]
                    
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGTERM)
                            killed += 1
                            logger.info(
                                f"kill_all_dev_servers: killed PID {pid} "
                                f"matching pattern '{pattern}'"
                            )
                        except OSError:
                            pass  # Process already dead
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # pgrep not available or timed out
                pass
        
        if killed > 0:
            logger.info(f"kill_all_dev_servers: killed {killed} total processes")
        else:
            logger.info("kill_all_dev_servers: no dev server processes found")
        
        return killed

    def full_inter_test_cleanup(self) -> Dict:
        """
        Perform full inter-test cleanup to prevent port collisions,
        orphaned servers, and resource exhaustion between smoke tests.
        
        Orchestrates:
        0. kill_all_dev_servers() — kill ALL running dev server processes
        1. cleanup_stale_services() — mark dead PIDs as stopped
        2. cleanup_all_stopped_services() — purge stopped registrations
        3. Clear port cache — invalidate stale lookups
        
        Returns:
            Summary dict with cleanup statistics
        """
        result = {
            "dev_servers_killed": 0,
            "stale_services_cleaned": 0,
            "stopped_services_purged": 0,
            "cache_cleared": False,
        }
        
        # Step 0: Kill ALL running dev server processes
        result["dev_servers_killed"] = self.kill_all_dev_servers()
        
        # Step 1: Mark dead processes as stopped
        stale_cleaned = self.cleanup_stale_services()
        result["stale_services_cleaned"] = len(stale_cleaned)
        
        # Step 2: Purge all stopped registrations
        stopped_purged = self.cleanup_all_stopped_services()
        result["stopped_services_purged"] = stopped_purged
        
        # Step 3: Invalidate port cache
        self._port_cache.clear()
        result["cache_cleared"] = True
        
        logger.info(
            f"Inter-test cleanup: {result['dev_servers_killed']} dev servers killed, "
            f"{result['stale_services_cleaned']} stale, "
            f"{result['stopped_services_purged']} stopped purged, cache cleared"
        )
        
        return result

    def wait_for_health(
        self,
        port: int,
        max_wait: float = 30.0,
        poll_interval: float = 1.0,
        endpoints: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """
        Wait for a service to become healthy.
        
        Args:
            port: Port to check
            max_wait: Maximum time to wait in seconds
            poll_interval: Time between checks in seconds
            endpoints: Health endpoints to try
            
        Returns:
            Tuple of (is_healthy, message)
        """
        import time
        
        start_time = time.time()
        last_message = ""
        
        while time.time() - start_time < max_wait:
            is_healthy, message = self.check_health(port, endpoints)
            last_message = message
            
            if is_healthy:
                return True, message
            
            time.sleep(poll_interval)
        
        return False, f"Timeout after {max_wait}s: {last_message}"


# Global instance
_port_manager: Optional[PortManager] = None


def get_port_manager() -> PortManager:
    """Get the global port manager instance"""
    global _port_manager
    if _port_manager is None:
        _port_manager = PortManager()
    return _port_manager
