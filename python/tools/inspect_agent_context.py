from __future__ import annotations
import os
import json
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle

class InspectAgentContext(Tool):
    """
    A debugging tool to inspect the agent's current perception of the Memory Bank and project context.
    This helps verify that agents are correctly reading memory bank records into their context.
    """

    async def execute(self, **kwargs):
        """
        Returns a summary of the agent's current project context and memory bank state as perceived by the agent.
        """
        from python.helpers import projects
        
        # 1. Project Context
        project_name = projects.get_context_project_name(self.agent.context)
        
        # 2. Memory Bank Context (The strings usually injected into prompt)
        memory_bank_context = projects.get_project_memory_bank_context(project_name) if project_name else {}
        
        # 3. Last Message Index & Log Version
        context_id = self.agent.context.id
        
        result = {
            "active_project": project_name or "NONE (Global)",
            "context_id": context_id,
            "memory_bank_files_loaded": list(memory_bank_context.keys()),
            "memory_bank_preview": {k: v[:200] + "..." if len(v) > 200 else v for k, v in memory_bank_context.items()}
        }
        
        output = "### Agent Context Inspection\n\n"
        output += f"**Active Project**: `{result['active_project']}`\n"
        output += f"**Context ID**: `{result['context_id']}`\n\n"
        
        if result["memory_bank_files_loaded"]:
            output += "#### Memory Bank Snapshot\n"
            for filename, content in result["memory_bank_preview"].items():
                output += f"- **{filename}**:\n```markdown\n{content}\n```\n"
        else:
            output += "> [!WARNING]\n> No memory bank files found or loaded for this context.\n"

        PrintStyle.hint(f"Agent {self.agent.name} inspected their own context.")
        return Response(message=output, break_loop=False)

if __name__ == "__main__":
    pass
