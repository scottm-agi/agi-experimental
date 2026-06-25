from __future__ import annotations
"""
Extension hook to automatically update the project's memory-bank at the end of each monologue.
Ensures activeContext.md and progress.md are kept current with session activity.
"""

import os
from datetime import datetime
from python.helpers.extension import Extension
from python.helpers.print_style import PrintStyle
from python.helpers import projects, files


class AutoMemoryBankUpdate(Extension):
    """Extension to auto-update memory-bank at monologue end."""

    async def execute(self, loop_data=None, **kwargs):
        """Update memory-bank with session summary."""
        try:
            # Get active project
            project_name = projects.get_context_project_name(self.agent.context)
            
            if not project_name:
                # No active project - skip auto-update (global memory-bank not auto-updated)
                return
            
            # Get session data
            iterations = 0
            final_response = None
            
            if loop_data:
                if hasattr(loop_data, 'iteration'):
                    iterations = loop_data.iteration
                if hasattr(loop_data, 'last_response') and loop_data.last_response:
                    final_response = str(loop_data.last_response)[:500]  # Truncate
            
            # Skip only if literally nothing happened (0 iterations and no response)
            if iterations == 0 and not final_response:
                return
            
            # Get memory-bank directory — create if missing (RCA-289)
            mb_dir = projects.get_project_memory_bank_folder(project_name)
            if not os.path.exists(mb_dir):
                # Auto-initialize the memory bank instead of silently skipping.
                # This ensures every project that runs a monologue gets a memory bank.
                projects.initialize_project_memory_bank(project_name)
                PrintStyle.hint(f"Memory bank auto-initialized for project: {project_name}")
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 1. Update activeContext.md with current session summary
            await self._update_active_context(mb_dir, timestamp, iterations, final_response)
            
            # 2. Update progress.md with session milestone (always log completed sessions)
            await self._update_progress(mb_dir, timestamp, iterations)
            
            PrintStyle.hint(f"Memory bank auto-updated for project: {project_name}")
            
        except Exception as e:
            # Don't fail the monologue if memory-bank update fails
            PrintStyle.error(f"Auto memory-bank update failed: {e}")

    async def _update_active_context(self, mb_dir: str, timestamp: str, iterations: int, response: str | None):
        """Update activeContext.md with current session state."""
        filepath = os.path.join(mb_dir, "activeContext.md")
        
        # Build update content — ONLY metadata, NEVER response content.
        # Saving response content causes a poisoning loop: hallucinated text gets
        # saved → injected into next system prompt → re-hallucinated → saved again.
        update_lines = [
            f"\n## Session Update ({timestamp})",
            f"- **Agent**: {self.agent.agent_name} (iterations: {iterations})",
        ]
        
        update_content = "\n".join(update_lines) + "\n"
        
        # Read current content
        current_content = ""
        if os.path.exists(filepath):
            try:
                current_content = files.read_file(filepath)
            except Exception:
                pass
        
        # Check if it's just the template (2 lines)
        if current_content.count("\n") <= 2:
            # Replace template entirely
            new_content = "# Active Context\n\nTracks current focus, recent changes, and immediate next steps.\n" + update_content
        else:
            # Append to existing content
            new_content = current_content.rstrip() + update_content
        
        files.write_file(filepath, new_content)

    async def _update_progress(self, mb_dir: str, timestamp: str, iterations: int):
        """Update progress.md with session milestone."""
        filepath = os.path.join(mb_dir, "progress.md")
        
        update_content = f"\n- [{timestamp}] Session completed ({iterations} iterations) by {self.agent.agent_name}\n"
        
        # Read current content
        current_content = ""
        if os.path.exists(filepath):
            try:
                current_content = files.read_file(filepath)
            except Exception:
                pass
        
        # Check if it's just the template (2 lines)
        if current_content.count("\n") <= 2:
            new_content = "# Progress\n\nLogs completed work, remaining tasks, and known issues.\n" + update_content
        else:
            new_content = current_content.rstrip() + update_content
        
        files.write_file(filepath, new_content)
