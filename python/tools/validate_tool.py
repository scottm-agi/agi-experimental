from __future__ import annotations
from python.helpers.tool import Tool, Response
from python.helpers import tool_registry
from typing import Optional

class ValidateTool(Tool):
    """
    Validates a proposed new tool name and description against existing tools to prevent duplication.
    """
    async def execute(self, name: str, description: str, **kwargs):
        if not name:
            return Response(message="Error: Tool name is required.", break_loop=False)

        # 1. Check for exact name collision
        known_tools = tool_registry.get_known_tools()
        if name in known_tools:
            metadata = tool_registry.get_tool_metadata(name) or {}
            existing_desc = metadata.get("description", "No description available.")
            return Response(
                message=f"Collision detected: A tool named '{name}' already exists.\nExisting description: {existing_desc}\nPlease enhance the existing tool or choose a more specific name.",
                break_loop=False
            )

        # 2. Check for similar names
        similar_tools = tool_registry.find_similar_tools(name, threshold=0.7)
        if similar_tools:
            report = f"Warning: Proposed tool name '{name}' is highly similar to existing tools:\n"
            for tool in similar_tools:
                report += f"- {tool['name']} (Match: {int(tool['ratio']*100)}%): {tool['description']}\n"
            report += "\nPlease verify if these tools already offer the functionality you need."
            return Response(message=report, break_loop=False)

        # 3. Success
        return Response(
            message=f"No direct collisions detected for '{name}'. You may proceed with creating the tool, but ensure its functionality is unique and modular.",
            break_loop=False
        )
