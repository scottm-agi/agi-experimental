from __future__ import annotations
import os
import sys
import importlib.util
from typing import Optional, Any
from python.helpers.tool import Tool, Response
from python.helpers.deployment_interface import DeploymentConfig
from python.helpers.railway_helper import RailwayHelper

class DeployTool(Tool):
    """
    Orchestrates deployments to cloud providers using Python IaC definitions.
    """
    async def execute(self, config_file: str = "infra.py", provider: str = "railway", **kwargs):
        if not os.path.exists(config_file):
            return Response(message=f"Error: Configuration file '{config_file}' not found.", break_loop=False)

        # Load the configuration module
        try:
            spec = importlib.util.spec_from_file_location("infra_config", config_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules["infra_config"] = module
                spec.loader.exec_module(module)
                
                # Expecting a variable 'deployment_config' in the module
                config = getattr(module, "deployment_config", None)
                if not isinstance(config, DeploymentConfig):
                    return Response(message=f"Error: '{config_file}' must define 'deployment_config' of type DeploymentConfig.", break_loop=False)
            else:
                 return Response(message=f"Error: Could not load spec for '{config_file}'.", break_loop=False)

        except Exception as e:
            return Response(message=f"Error loading configuration: {e}", break_loop=False)

        # Select Provider
        cloud_provider = None
        if provider.lower() == "railway":
            cloud_provider = RailwayHelper()
        else:
             return Response(message=f"Error: Unsupported provider '{provider}'.", break_loop=False)

        # Deploy
        try:
            print(f"Initiating deployment via {provider}...")
            result = await cloud_provider.deploy(config)
            return Response(message=f"Deployment success:\n{result}", break_loop=False)
        except Exception as e:
             return Response(message=f"Deployment failed: {e}", break_loop=False)
