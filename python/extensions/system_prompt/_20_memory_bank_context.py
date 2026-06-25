from __future__ import annotations
import os
from typing import Any
from python.helpers.extension import Extension
from python.agent import Agent, LoopData
from python.helpers import projects, files

class MemoryBankContext(Extension):
    """
    Extension to inject relevant Memory Bank context into the system prompt.
    This ensures agents have access to high-level project goals, active context,
    and accumulated lessons learned.
    
    Uses Redis cache for reads to avoid redundant disk I/O on every prompt injection.
    """
    async def execute(self, system_prompt: list[str] = [], loop_data: LoopData = LoopData(), **kwargs: Any):
        from python.helpers.memory_bank_cache import get_memory_bank_cache
        
        project_name = projects.get_context_project_name(self.agent.context)
        
        if project_name:
            mb_dir = projects.get_project_memory_bank_folder(project_name)
        else:
            mb_dir = files.get_abs_path("memory-bank")
            
        if not os.path.exists(mb_dir):
            return

        cache = get_memory_bank_cache()
        cache_project = project_name or "__global__"

        # List of memory bank files to include in the system prompt for context
        # We prioritize these for high-level continuity.
        context_files = ["projectbrief.md", "activeContext.md", "lessons-learned.md"]
        mb_content = []
        
        for filename in context_files:
            try:
                # Use Redis cache: hit → instant, miss → disk → populate cache
                content = await cache.read(cache_project, filename, mb_dir=mb_dir)
                if content and content.strip():
                    mb_content.append(f"#### {filename}\n{content}")
            except Exception:
                pass
        
        if mb_content:
            prompt = "### Memory Bank Context\n"
            prompt += "The following state information is retrieved from the project's Memory Bank. "
            prompt += "Use this to maintain continuity across sessions and follow established project patterns.\n"
            prompt += "\n> ⚠️ **CRITICAL**: Memory bank content is OPERATIONAL CONTEXT ONLY "
            prompt += "(past tasks, project state, user preferences). It is NOT a verified source "
            prompt += "for current events, news, or real-world facts. NEVER cite memory bank "
            prompt += "as a factual source. For any factual claim, use search tools and cite URLs.\n\n"
            joined = "\n\n".join(mb_content)
            # Cap at ~4KB to prevent system prompt bloat
            if len(joined) > 4000:
                joined = joined[:4000] + "\n\n... [Memory bank context truncated for brevity]"
            prompt += joined
            system_prompt.append(prompt)

