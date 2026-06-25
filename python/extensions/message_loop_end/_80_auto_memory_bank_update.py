from __future__ import annotations
import os
import datetime
from python.helpers.extension import Extension
from python.agent import LoopData, AgentContextType
from python.helpers import projects, files
import logging

logger = logging.getLogger(__name__)

class AutoMemoryBankUpdate(Extension):
    """
    Automatically updates the project's Memory Bank at the end of a message loop
    if a final response was provided.
    """
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        # Only trigger if the current tool is 'response' (final answer)
        if loop_data.current_tool != "response":
            return
            
        # Only trigger for USER or TASK contexts
        if self.agent.context.type == AgentContextType.BACKGROUND:
            return

        project_name = projects.get_context_project_name(self.agent.context)
        
        if project_name:
            mb_dir = projects.get_project_memory_bank_folder(project_name)
            projects.initialize_project_memory_bank(project_name)
        else:
            mb_dir = files.get_abs_path("memory-bank")
            projects.initialize_global_memory_bank()
            
        if not os.path.exists(mb_dir):
            return

        # Prepare update
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last_response = loop_data.last_response if hasattr(loop_data, 'last_response') else ""
        
        # Simple summary for progress.md
        # In a real-world scenario, we might use a utility LLM to create a better summary
        update_text = f"### Interaction at {timestamp}\n"
        if last_response:
            # Take snippet of response
            snippet = (last_response[:200] + '...') if len(last_response) > 200 else last_response
            update_text += f"- **Outcome**: {snippet}\n"
        
        progress_path = os.path.join(mb_dir, "progress.md")
        
        try:
            with open(progress_path, "a", encoding="utf-8") as f:
                f.write(f"\n{update_text}\n")
        except Exception as e:
            logger.warning(f"[MEMORY BANK] Failed to update progress.md: {e}")
