"""Scratchpad Tool — cross-agent shared state via Redis.

Exposes the Scratchpad (blackboard pattern) as an agent tool.
Enables agents to share state across delegation boundaries.

Operations:
  - write: Store data in a namespace with TTL
  - read: Retrieve data from a namespace
  - exists: Check if a namespace exists
  - delete: Remove a namespace entry
"""
from python.helpers.tool import Tool, Response
from python.helpers.scratchpad import Scratchpad
import json


class ScratchpadTool(Tool):

    async def execute(self, **kwargs):
        operation = self.args.get("operation", "read").lower()
        namespace = self.args.get("namespace", "")

        if not namespace:
            return Response(message="Error: 'namespace' is required.", break_loop=False)

        try:
            pad = Scratchpad.for_agent(self.agent)

            if operation == "write":
                data = self.args.get("data", "")
                ttl = int(self.args.get("ttl", 3600))
                await pad.set(namespace, data, ttl=ttl)
                return Response(message=f"Written to scratchpad namespace '{namespace}'", break_loop=False)

            elif operation == "read":
                result = await pad.get(namespace)
                if result is None:
                    return Response(message=f"Namespace '{namespace}' not found or expired.", break_loop=False)
                formatted = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
                return Response(message=f"Scratchpad '{namespace}':\n{formatted}", break_loop=False)

            elif operation == "exists":
                exists = await pad.exists(namespace)
                return Response(message=f"Namespace '{namespace}' exists: {exists}", break_loop=False)

            elif operation == "delete":
                await pad.delete(namespace)
                return Response(message=f"Deleted scratchpad namespace '{namespace}'", break_loop=False)

            else:
                return Response(message=f"Unknown operation '{operation}'. Use: write, read, exists, delete", break_loop=False)

        except Exception as e:
            return Response(message=f"Scratchpad error: {e}", break_loop=False)
