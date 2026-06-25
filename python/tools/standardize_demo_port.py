import os
import logging
import json
from typing import Dict, Any, List
from python.helpers.tool import Tool, Response

logger = logging.getLogger("port-standardization")

class StandardizeDemoPort(Tool):
    """
    Issue #473: Port forwarding / connectivity issues for local demos.
    This tool provides a deterministic way to pick a port for local demos
    that is known to be mapped in Docker and host OS (range 5100-5150).
    """

    def __init__(self, agent=None, name="standardize_demo_port", method=None, args=None, message="", loop_data=None, **kwargs):
        super().__init__(agent, name, method, args or {}, message, loop_data, **kwargs)
        self.reserved_range = range(5100, 5151)
        self.state_file = "data/last_used_demo_port.json"

    async def execute(self, action: str = "get_suggested_port", project_name: str = None, **kwargs) -> Response:
        """
        Suggest or reserve a port for a local demo.
        """
        if action == "get_suggested_port":
            port = self._get_next_available_port()
            return Response(
                message=f"Suggested demo port: {port}. Please configure your web application to listen on 0.0.0.0:{port} to ensure it is accessible from the host OS.",
                break_loop=False,
                additional={
                    "status": "success",
                    "suggested_port": port,
                    "range": "5100-5150"
                }
            )
        
        elif action == "reserve_port":
            port = kwargs.get("port")
            if not port or port not in self.reserved_range:
                return Response(message=f"Error: Invalid or missing port. Must be in {self.reserved_range}", break_loop=False)
            
            # Persist as last used
            self._save_last_port(port)
            return Response(
                message=f"Port {port} reserved for project '{project_name}'.",
                break_loop=False,
                additional={
                    "status": "success",
                    "reserved_port": port,
                    "project": project_name
                }
            )

        elif action == "get_all_mapped_ports":
            return Response(
                message=f"All mapped ports for local demos: {list(self.reserved_range)}",
                break_loop=False,
                additional={
                    "status": "success",
                    "mapped_ports": list(self.reserved_range),
                    "preferred": [5100, 5101, 5102, 5103, 5104, 5105]
                }
            )

        return Response(message=f"Error: Unknown action: {action}", break_loop=False)

    def _save_last_port(self, port: int):
        """Save the last used port to the state file."""
        state_path = os.path.join(os.getcwd(), self.state_file)
        try:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w") as f:
                json.dump({"last_port": port}, f)
        except Exception as e:
            logger.warning(f"Failed to save last used port: {e}")

    def _get_next_available_port(self) -> int:
        """
        Delegate to port_manager for proper hash-based allocation
        with cross-project collision checks. Falls back to simple
        cycling if port_manager is unavailable.
        
        RC-19: The old logic just did last_port + 1 with no collision
        prevention. port_manager has hash-based allocation + cross-project
        registry scanning.
        """
        try:
            from python.helpers.port_manager import PortManager
            port_manager = PortManager()
            # Use a generic project name for standalone requests
            port = port_manager.allocate_port(
                project_name="demo",
                service_type="frontend"
            )
            self._save_last_port(port)
            return port
        except Exception as e:
            logger.warning(f"port_manager unavailable, falling back to cycling: {e}")
        
        # Fallback: simple cycling (original behavior)
        last_port = 5100
        
        # Adjust for container internal path
        state_path = os.path.join(os.getcwd(), self.state_file)
        
        try:
            if os.path.exists(state_path):
                with open(state_path, "r") as f:
                    data = json.load(f)
                    last_port = data.get("last_port", 5100)
        except Exception:
            pass

        next_port = last_port + 1
        if next_port > 5150:
            next_port = 5100
            
        self._save_last_port(next_port)
        return next_port
