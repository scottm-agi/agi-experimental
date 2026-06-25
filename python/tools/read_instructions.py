"""
read_instructions — Tool for agents to dynamically discover and load instructions.

Consolidates ALL instruction sources into a single tool call:
- Global custom instructions (prompts/agent.system.custom_instructions.md)
- Mode-specific custom instructions (from mode_manager)
- Per-directory .rules files (walked up from project working directory)
- agents.md files (project-level agent configuration)
- .agents/ directory configuration

This ensures ALL agents — regardless of profile or delegation depth —
can access the full instruction context via a tool call.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from python.helpers.tool import Tool, Response
from python.helpers import files

logger = logging.getLogger("agix.read_instructions")


class ReadInstructions(Tool):
    """
    Dynamically discover and load all instruction sources for the current agent context.
    
    Sources loaded (in priority order):
    1. Global custom_instructions from prompts/
    2. Mode-specific custom instructions from mode_manager
    3. Per-directory .rules files from the project working directory tree
    4. agents.md from the project root
    5. .agents/ directory configuration
    """

    async def execute(self, scope: str = "all", directory: str = None, **kwargs) -> Response:
        scope = scope or self.args.get("scope", "all")
        directory = directory or self.args.get("directory", None)
        
        sections = []
        
        if scope in ("all", "global"):
            sections.append(self._load_global_custom_instructions())
        
        if scope in ("all", "mode"):
            sections.append(self._load_mode_instructions())
        
        if scope in ("all", "rules"):
            sections.append(self._load_rules_files(directory))
        
        if scope in ("all", "agents"):
            sections.append(self._load_agents_md(directory))
        
        # Filter empty sections
        sections = [s for s in sections if s and s.strip()]
        
        if sections:
            result = "\n\n---\n\n".join(sections)
            return Response(message=result, break_loop=False)
        
        return Response(
            message="No instructions found for the current context.",
            break_loop=False
        )

    def _load_global_custom_instructions(self) -> str:
        """Load global custom instructions from prompts/agent.system.custom_instructions.md."""
        try:
            content = self.agent.read_prompt("agent.system.custom_instructions.md")
            if content and content.strip():
                return f"## Global Custom Instructions\n\n{content}"
        except Exception as e:
            logger.debug(f"No global custom instructions found: {e}")
        return ""

    def _load_mode_instructions(self) -> str:
        """Load mode-specific custom instructions from mode_manager."""
        try:
            from python.helpers.mode_manager import get_mode_manager
            mm = get_mode_manager()
            instructions = mm.get_custom_instructions()
            if instructions and instructions.strip():
                mode_name = mm.get_current_mode_name() if hasattr(mm, 'get_current_mode_name') else "current"
                return f"## Mode-Specific Instructions ({mode_name})\n\n{instructions}"
        except Exception as e:
            logger.debug(f"No mode instructions available: {e}")
        return ""

    def _load_rules_files(self, directory: Optional[str] = None) -> str:
        """Walk up from directory to find and load .rules files."""
        search_dir = directory or self._get_project_dir()
        if not search_dir:
            return ""
        
        rules_content = []
        current = search_dir
        visited = set()
        
        # Walk up to 5 levels to find .rules files
        for _ in range(5):
            if current in visited or not current or current == "/":
                break
            visited.add(current)
            
            rules_path = os.path.join(current, ".rules")
            if os.path.isfile(rules_path):
                try:
                    content = files.read_file(rules_path)
                    if content and content.strip():
                        rules_content.append(
                            f"### Rules from `{rules_path}`\n\n{content}"
                        )
                except Exception as e:
                    logger.debug(f"Error reading {rules_path}: {e}")
            
            current = os.path.dirname(current)
        
        if rules_content:
            return "## Per-Directory Rules\n\n" + "\n\n".join(rules_content)
        return ""

    def _load_agents_md(self, directory: Optional[str] = None) -> str:
        """Load agents.md from project root or specified directory."""
        search_dir = directory or self._get_project_dir()
        if not search_dir:
            return ""
        
        # Check for agents.md in current dir and parent dirs
        current = search_dir
        for _ in range(5):
            if not current or current == "/":
                break
            
            agents_path = os.path.join(current, "agents.md")
            if os.path.isfile(agents_path):
                try:
                    content = files.read_file(agents_path)
                    if content and content.strip():
                        return f"## Agent Configuration (agents.md)\n\n{content}"
                except Exception as e:
                    logger.debug(f"Error reading {agents_path}: {e}")
            
            # Also check .agents/ directory
            agents_dir = os.path.join(current, ".agents")
            if os.path.isdir(agents_dir):
                agents_entries = []
                for fname in os.listdir(agents_dir):
                    fpath = os.path.join(agents_dir, fname)
                    if os.path.isfile(fpath) and fname.endswith(".md"):
                        try:
                            content = files.read_file(fpath)
                            if content and content.strip():
                                agents_entries.append(f"### {fname}\n\n{content}")
                        except Exception:
                            pass
                if agents_entries:
                    return "## Agent Configuration (.agents/)\n\n" + "\n\n".join(agents_entries)
            
            current = os.path.dirname(current)
        
        return ""

    def _get_project_dir(self) -> Optional[str]:
        """Get the current project working directory from agent context."""
        try:
            # Try active project path
            if self.agent:
                active_project = getattr(self.agent, 'data', {}).get('active_project')
                if isinstance(active_project, dict) and active_project.get('path'):
                    return active_project['path']
                
                # Try context-based resolution
                from python.helpers import projects as projects_helper
                context = getattr(self.agent, 'context', None)
                if context:
                    project_name = projects_helper.get_context_project_name(context)
                    if project_name:
                        project_dir = projects_helper.get_project_dir(project_name)
                        if project_dir:
                            return project_dir
        except Exception:
            pass
        
        # Fallback to cwd
        return os.getcwd()
