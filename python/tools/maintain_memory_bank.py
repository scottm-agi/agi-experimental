from __future__ import annotations
import os
import time
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle

class MaintainMemoryBank(Tool):
    """
    A tool to maintain the project's Memory Bank (Markdown files in memory-bank/).
    This ensures that high-level project context, active focus, and lessons learned
    are captured in a human-readable format, as per the user's global rules.
    """

    @staticmethod
    def _sanitize_file_name(file_name):
        """
        Clean agent-provided file_name to prevent path-doubling errors.
        Strips 'memory-bank/' prefix and any directory components.
        Returns None for empty/None inputs.
        """
        if not file_name:
            return None
        # Strip surrounding whitespace
        cleaned = file_name.strip()
        if not cleaned:
            return None
        # Extract just the basename — strip ALL directory components
        # This handles: 'memory-bank/progress.md', '/agix/.../memory-bank/progress.md', 'adrs/adr-001.md'
        cleaned = os.path.basename(cleaned)
        return cleaned if cleaned else None

    @staticmethod
    def _get_default_file_name(mode):
        """
        Return a sensible default file_name when the agent forgets to provide one.
        Only applies to write modes (append, overwrite) — read/list require explicit input.
        """
        if mode in ("append", "overwrite"):
            return "progress.md"
        return None

    async def execute(self, file_name: str = None, content: str = None, mode: str = "append", **kwargs):
        """
        Maintains the project's Memory Bank.
        
        Args:
            file_name (str): The name of the file (e.g., 'progress.md'). Required for 'read', 'append', 'overwrite'.
            content (str): The content to add or replace. Required for 'append', 'overwrite'.
            mode (str):
                'list': Lists all memory bank files.
                'read': Returns the content of file_name.
                'append': Adds content to the end of file_name.
                'overwrite': Replaces file_name with content.
                'adr': Generates an Architectural Decision Record.
                'context': Generates/updates a context management file.
                Defaults to 'append'.
        """
        from python.helpers import projects
        from python.helpers.memory_bank_cache import get_memory_bank_cache

        # Depth-based write suppression: subordinate agents at depth >= 2
        # should not write to memory bank (their output is ephemeral and
        # aggregated by the parent). Read/list operations remain allowed.
        _delegation_depth = self.agent.data.get("_delegation_depth", 0)
        if _delegation_depth >= 2 and mode in ("append", "overwrite", "adr", "context"):
            return Response(
                message=(
                    f"Memory bank write skipped (subordinate agent at depth {_delegation_depth}). "
                    f"Your parent agent will persist results. Continue with your task."
                ),
                break_loop=False,
            )
        
        # Detect active project
        project_name = projects.get_context_project_name(self.agent.context)
        
        if project_name:
            memory_bank_dir = projects.get_project_memory_bank_folder(project_name)
            projects.initialize_project_memory_bank(project_name)
        else:
            memory_bank_dir = os.path.join(os.getcwd(), "memory-bank")
            projects.initialize_global_memory_bank()
        
        os.makedirs(memory_bank_dir, exist_ok=True)
        
        # Cache instance (uses Redis when available, falls back to disk)
        cache = get_memory_bank_cache()
        cache_project = project_name or "__global__"

        if mode == "list":
            files = [f for f in os.listdir(memory_bank_dir) if f.endswith(".md")]
            message = f"Memory bank files in {memory_bank_dir}:\n- " + "\n- ".join(files)
            return Response(message=message, break_loop=False)

        # Sanitize file_name to prevent path-doubling (#1113)
        file_name = MaintainMemoryBank._sanitize_file_name(file_name)

        # Default file_name for write modes if agent forgot it (#1113)
        if not file_name:
            file_name = MaintainMemoryBank._get_default_file_name(mode)

        if not file_name:
            valid_modes_needing_file = "read, append, overwrite"
            return Response(
                message=f"⚠️ file_name is required for mode: {mode}. "
                        f"Please specify which memory bank file to {mode} "
                        f"(e.g., 'progress.md', 'activeContext.md', 'techContext.md').",
                break_loop=False,
            )

        # Enforce .md extension
        if not file_name.endswith(".md"):
            file_name += ".md"

        file_path = os.path.join(memory_bank_dir, file_name)
        
        try:
            if mode == "read":
                # Use cache: Redis first → disk fallback → populate cache on miss
                cached_content = await cache.read(cache_project, file_name, mb_dir=memory_bank_dir)
                if cached_content is None:
                    return Response(message=f"File {file_name} not found in {memory_bank_dir}.", break_loop=True)
                return Response(message=f"Content of {file_name}:\n\n{cached_content}", break_loop=False)

            if not content:
                return Response(message="content is required for mode: " + mode, break_loop=True)

            if mode == "append":
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write("\n" + content + "\n")
                await cache.invalidate_file(cache_project, file_name)
                message = f"Successfully appended content to {file_name} in {memory_bank_dir}."
            elif mode == "overwrite":
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                await cache.invalidate_file(cache_project, file_name)
                message = f"Successfully overwrote {file_name} in {memory_bank_dir}."
            elif mode == "adr":
                # ADR Template logic
                adr_content = f"# ADR: {kwargs.get('title', 'Untitled Decision')}\n\n## Status\n{kwargs.get('status', 'Proposed')}\n\n## Context\n{content}\n\n## Decision\n{kwargs.get('decision', 'TBD')}\n\n## Consequences\n{kwargs.get('consequences', 'TBD')}"
                adr_file = f"adr-{int(time.time())}.md"
                with open(os.path.join(memory_bank_dir, adr_file), "w") as f:
                    f.write(adr_content)
                message = f"Created ADR {adr_file} in {memory_bank_dir}."
            elif mode == "context":
                context_file = "activeContext.md"
                with open(os.path.join(memory_bank_dir, context_file), "w") as f:
                    f.write(content)
                await cache.invalidate_file(cache_project, context_file)
                message = f"Updated context in {context_file}."
            else:
                return Response(message=f"Invalid mode: {mode}. Use 'list', 'read', 'append', or 'overwrite'.", break_loop=True)
            
            PrintStyle.hint(message)
            return Response(message=message, break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error updating memory bank: {e}", break_loop=True)

if __name__ == "__main__":
    # Test block
    pass
