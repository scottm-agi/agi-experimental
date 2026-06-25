"""
Context management module for supervisor.

Contains smart file loading, memory bank loading, and content budget management.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import logger
from python.helpers.projects import PROJECT_META_DIR, LEGACY_PROJECT_META_DIR

if TYPE_CHECKING:
    from python.agent import Agent


class ContextManagementMixin:
    """
    Mixin class providing context management functionality for SupervisorAgent.
    
    This mixin handles:
    - Smart file loading with budget awareness
    - Memory bank loading (global, project, agent)
    - Content budget assessment and allocation
    - Content condensation
    """
    
    # =========================================================================
    # Smart Context Management
    # =========================================================================
    
    def _get_file_stats(self, filepath: str) -> Dict[str, Any]:
        """Get file statistics without reading full content (wc -l style)."""
        stats = {
            "exists": False,
            "lines": 0,
            "chars": 0,
            "size_bytes": 0,
            "priority": "normal",
        }
        
        if not os.path.exists(filepath):
            return stats
        
        stats["exists"] = True
        stats["size_bytes"] = os.path.getsize(filepath)
        
        # Quick line count without loading full file
        try:
            line_count = 0
            with open(filepath, 'r', errors='ignore') as f:
                for line_count, _ in enumerate(f, 1):
                    pass
            stats["lines"] = line_count
            # Estimate chars from bytes (rough approximation)
            stats["chars"] = stats["size_bytes"]
        except Exception as e:
            logger.debug(f"Failed to get stats for {filepath}: {e}")
        
        return stats
    
    def _assess_content_budget(self, current_chars: int) -> Dict[str, int]:
        """Assess remaining budget and allocate to different content types."""
        remaining = self.config.max_context_chars - current_chars
        
        # Allocate budget by priority
        # Priority: rules > signals > agent_state > lessons > global_memory > project_memory > agent_memories
        allocations = {
            "rules": min(4000, remaining * 0.10),
            "signals": min(5000, remaining * 0.13),
            "agent_state": min(3000, remaining * 0.08),
            "lessons": min(10000, remaining * 0.22),
            "global_memory": min(8000, remaining * 0.18),
            "project_memory": min(8000, remaining * 0.18),
            "agent_memories": min(5000, remaining * 0.08),
        }
        
        return {k: int(v) for k, v in allocations.items()}
    
    def _smart_load_file(
        self,
        filepath: str,
        max_chars: int,
        max_lines: Optional[int] = None,
        priority_sections: Optional[List[str]] = None,
    ) -> str:
        """Intelligently load file content within budget."""
        if not os.path.exists(filepath):
            return ""
        
        max_lines = max_lines or self.config.max_file_lines
        
        # Get file stats first
        stats = self._get_file_stats(filepath)
        
        if not stats["exists"]:
            return ""
        
        # If file is small enough, load it all
        if stats["chars"] <= max_chars and stats["lines"] <= max_lines:
            try:
                with open(filepath, 'r', errors='ignore') as f:
                    return f.read()
            except Exception:
                return ""
        
        # File is too large - need to condense
        logger.debug(f"[SUPERVISOR] File {filepath} exceeds budget ({stats['chars']} chars, {stats['lines']} lines)")
        
        try:
            with open(filepath, 'r', errors='ignore') as f:
                lines = f.readlines()
        except Exception:
            return ""
        
        # Strategy 1: If priority sections specified, extract those
        if priority_sections:
            extracted = self._extract_priority_sections(lines, priority_sections, max_chars)
            if extracted:
                return extracted
        
        # Strategy 2: Take head + tail (most recent content often at end)
        head_lines = max_lines // 3
        tail_lines = max_lines - head_lines
        
        if len(lines) <= max_lines:
            content = ''.join(lines)
        else:
            head = ''.join(lines[:head_lines])
            tail = ''.join(lines[-tail_lines:])
            content = f"{head}\n\n... [{len(lines) - max_lines} lines omitted] ...\n\n{tail}"
        
        # Truncate by chars if still too long
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...[truncated to fit budget]"
        
        return content
    
    def _extract_priority_sections(
        self,
        lines: List[str],
        sections: List[str],
        max_chars: int,
    ) -> str:
        """Extract priority sections from content (e.g., ## Headers)."""
        content_parts = []
        current_section = None
        current_content = []
        
        for line in lines:
            # Check if this is a section header
            is_header = line.startswith('#') or line.startswith('##')
            
            if is_header:
                # Save previous section if it matches priority
                if current_section and any(s.lower() in current_section.lower() for s in sections):
                    content_parts.append(current_section + '\n' + ''.join(current_content))
                
                current_section = line.strip()
                current_content = []
            else:
                current_content.append(line)
        
        # Don't forget last section
        if current_section and any(s.lower() in current_section.lower() for s in sections):
            content_parts.append(current_section + '\n' + ''.join(current_content))
        
        result = '\n\n'.join(content_parts)
        
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...[truncated]"
        
        return result
    
    def _condense_content(self, content: str, target_chars: int) -> str:
        """Condense content to fit within target character budget."""
        if len(content) <= target_chars:
            return content
        
        lines = content.split('\n')
        
        # Strategy: Keep headers and first line of each section
        condensed_lines = []
        in_section = False
        section_line_count = 0
        max_section_lines = 3  # Keep only first 3 lines per section
        
        for line in lines:
            is_header = line.startswith('#')
            
            if is_header:
                condensed_lines.append(line)
                in_section = True
                section_line_count = 0
            elif in_section and section_line_count < max_section_lines:
                if line.strip():  # Skip empty lines
                    condensed_lines.append(line)
                    section_line_count += 1
        
        result = '\n'.join(condensed_lines)
        
        # If still too long, truncate
        if len(result) > target_chars:
            result = result[:target_chars] + "\n...[condensed]"
        
        return result
    
    def _load_memory_banks_smart(self, agent: "Agent", budget: Dict[str, int]) -> str:
        """Load memory banks with intelligent budget management."""
        memory_context = []
        
        print(f"[SUPERVISOR] 📊 Memory budget allocation: {budget}", file=sys.stderr)
        
        # 0. Load rules (highest priority — shapes agent behavior)
        rules = self._load_rules(agent, budget.get("rules", 4000))
        if rules:
            memory_context.append("## Active Rules\n" + rules)
            print(f"[SUPERVISOR]   Rules: {len(rules)} chars", file=sys.stderr)
        
        # 1. Load global memory bank
        global_memory = self._load_global_memory_bank_smart(budget.get("global_memory", 5000))
        if global_memory:
            memory_context.append("## Global Memory Bank\n" + global_memory)
            print(f"[SUPERVISOR]   Global memory: {len(global_memory)} chars", file=sys.stderr)
        
        # 2. Load project memory bank (if agent has active project)
        project_memory = self._load_project_memory_bank_smart(agent, budget.get("project_memory", 5000))
        if project_memory:
            memory_context.append("## Project Memory Bank\n" + project_memory)
            print(f"[SUPERVISOR]   Project memory: {len(project_memory)} chars", file=sys.stderr)
        
        # 3. Load agent's recent memories
        agent_memories = self._load_agent_memories_smart(agent, budget.get("agent_memories", 3000))
        if agent_memories:
            memory_context.append("## Agent Recent Memories\n" + agent_memories)
            print(f"[SUPERVISOR]   Agent memories: {len(agent_memories)} chars", file=sys.stderr)
        
        if not memory_context:
            return ""
        
        return "\n\n".join(memory_context)
    
    def _load_rules(self, agent: "Agent", max_chars: int) -> str:
        """Load layered rules: global (memory-bank/rules/) + project-level.
        
        Rules hierarchy (later overrides earlier):
        1. Global rules: memory-bank/rules/global.md
        2. Project rules: project/.agix/memory-bank/rules/project.md
        """
        from python.helpers import files
        
        content_parts = []
        chars_used = 0
        
        # 1. Load global rules
        global_rules_path = os.path.join(
            files.get_abs_path("memory-bank"), "rules", "global.md"
        )
        if os.path.exists(global_rules_path):
            remaining = max_chars - chars_used
            content = self._smart_load_file(
                global_rules_path,
                max_chars=min(remaining, max_chars // 2),  # Reserve half for project rules
                priority_sections=["enforce", "TDD", "Content", "Error", "Verification"],
            )
            if content:
                content_parts.append("### Global Rules\n" + content)
                chars_used += len(content)
                logger.debug(f"Loaded global rules: {len(content)} chars")
        
        # 2. Load project-level rules (if agent has active project)
        active_project = None
        if hasattr(agent, 'get_data'):
            active_project = agent.get_data("active_project")
        
        if active_project:
            project_path = active_project.get("path", "") if isinstance(active_project, dict) else str(active_project)
            if project_path:
                project_rules_paths = [
                    os.path.join(project_path, PROJECT_META_DIR, "memory-bank", "rules", "project.md"),
                    os.path.join(project_path, LEGACY_PROJECT_META_DIR, "memory-bank", "rules", "project.md"),
                    os.path.join(project_path, "memory-bank", "rules", "project.md"),
                ]
                
                for rules_path in project_rules_paths:
                    if os.path.exists(rules_path):
                        remaining = max_chars - chars_used
                        if remaining <= 0:
                            break
                        content = self._smart_load_file(rules_path, max_chars=remaining)
                        if content:
                            content_parts.append("### Project Rules\n" + content)
                            chars_used += len(content)
                            logger.debug(f"Loaded project rules: {len(content)} chars from {rules_path}")
                        break  # Only use first found
        
        if not content_parts:
            return ""
        
        return "\n\n".join(content_parts)

    def _load_global_memory_bank_smart(self, max_chars: int) -> str:
        """Load global memory bank with budget awareness."""
        from python.helpers import files
        
        memory_bank_path = files.get_abs_path("memory-bank")
        
        if not os.path.exists(memory_bank_path):
            return ""
        
        content_parts = []
        chars_used = 0
        per_file_budget = max_chars // 4  # Divide among files
        
        # Prioritize files by importance
        priority_files = [
            ("progress.md", ["current", "recent", "status"]),
            ("project-summary.md", ["overview", "goals"]),
        ]
        
        for filename, priority_sections in priority_files:
            if chars_used >= max_chars:
                break
                
            filepath = os.path.join(memory_bank_path, filename)
            remaining_budget = max_chars - chars_used
            file_budget = min(per_file_budget, remaining_budget)
            
            content = self._smart_load_file(
                filepath,
                max_chars=file_budget,
                priority_sections=priority_sections,
            )
            
            if content:
                content_parts.append(f"### {filename}\n{content}")
                chars_used += len(content)
        
        # Load lessons-learned if budget remains
        if chars_used < max_chars:
            lessons_dir = os.path.join(memory_bank_path, "lessons-learned")
            if os.path.exists(lessons_dir):
                lesson_files = sorted(
                    [f for f in os.listdir(lessons_dir) if f.endswith('.md')],
                    key=lambda x: os.path.getmtime(os.path.join(lessons_dir, x)),
                    reverse=True,  # Most recent first
                )[:2]  # Only 2 most recent
                
                for filename in lesson_files:
                    if chars_used >= max_chars:
                        break
                    filepath = os.path.join(lessons_dir, filename)
                    remaining = max_chars - chars_used
                    content = self._smart_load_file(filepath, max_chars=min(1000, remaining))
                    if content:
                        content_parts.append(f"### Lessons: {filename}\n{content}")
                        chars_used += len(content)
        
        return "\n\n".join(content_parts) if content_parts else ""
    
    def _load_project_memory_bank_smart(self, agent: "Agent", max_chars: int) -> str:
        """Load project-specific memory bank with budget awareness."""
        active_project = None
        if hasattr(agent, 'get_data'):
            active_project = agent.get_data("active_project")
        
        if not active_project:
            return ""
        
        project_path = active_project.get("path", "") if isinstance(active_project, dict) else str(active_project)
        if not project_path:
            return ""
        
        # Check for memory bank locations
        memory_paths = [
            os.path.join(project_path, PROJECT_META_DIR, "memory-bank"),
            os.path.join(project_path, LEGACY_PROJECT_META_DIR, "memory-bank"),
            os.path.join(project_path, "memory-bank"),
        ]
        
        for memory_path in memory_paths:
            if os.path.exists(memory_path):
                content_parts = []
                chars_used = 0
                
                # Get all .md files and their stats
                md_files = []
                for filename in os.listdir(memory_path):
                    if filename.endswith('.md'):
                        filepath = os.path.join(memory_path, filename)
                        stats = self._get_file_stats(filepath)
                        md_files.append((filename, filepath, stats))
                
                # Sort by size (smaller files first to fit more)
                md_files.sort(key=lambda x: x[2]["chars"])
                
                per_file_budget = max_chars // max(len(md_files), 1)
                
                for filename, filepath, stats in md_files:
                    if chars_used >= max_chars:
                        break
                    
                    remaining = max_chars - chars_used
                    file_budget = min(per_file_budget, remaining)
                    
                    content = self._smart_load_file(filepath, max_chars=file_budget)
                    if content:
                        content_parts.append(f"### {filename}\n{content}")
                        chars_used += len(content)
                
                return "\n\n".join(content_parts) if content_parts else ""
        
        return ""
    
    def _load_agent_memories_smart(self, agent: "Agent", max_chars: int) -> str:
        """Load agent's recent memories with budget awareness."""
        if not hasattr(agent, 'memory') or getattr(agent, 'memory', None) is None:
            return ""
        
        try:
            memory = getattr(agent, 'memory')
            
            # Try to get recent memories
            if hasattr(memory, 'search'):
                memories = memory.search(
                    query="recent task progress errors solutions",
                    limit=5,
                )
                if memories:
                    memory_texts = []
                    chars_used = 0
                    per_memory_budget = max_chars // 5
                    
                    for mem in memories:
                        if chars_used >= max_chars:
                            break
                        
                        if isinstance(mem, dict):
                            text = mem.get('content', mem.get('text', str(mem)))
                        else:
                            text = str(mem)
                        
                        # Truncate individual memory
                        if len(text) > per_memory_budget:
                            text = text[:per_memory_budget] + "..."
                        
                        memory_texts.append(f"- {text}")
                        chars_used += len(text)
                    
                    return "\n".join(memory_texts)
            
            # Fallback: try to get memory output
            if hasattr(memory, 'output'):
                output = memory.output()
                if output:
                    if len(output) > max_chars:
                        output = output[:max_chars] + "\n...[truncated]"
                    return output
        except Exception as e:
            logger.debug(f"Failed to load agent memories: {e}")
        
        return ""
    
    # =========================================================================
    # Legacy Methods for Backward Compatibility
    # =========================================================================
    
    def _load_memory_banks(self, agent: "Agent") -> str:
        """Load memory banks for context (project, global, agent memories)."""
        # Use smart loading with default budget
        budget = self._assess_content_budget(0)
        return self._load_memory_banks_smart(agent, budget)
    
    def _load_global_memory_bank(self) -> str:
        """Load global memory bank files."""
        from python.helpers import files
        
        memory_bank_path = files.get_abs_path("memory-bank")
        
        if not os.path.exists(memory_bank_path):
            return ""
        
        content_parts = []
        
        # Load key files from memory-bank
        key_files = ["progress.md", "project-summary.md"]
        for filename in key_files:
            filepath = os.path.join(memory_bank_path, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r') as f:
                        file_content = f.read()
                    # Truncate if too long
                    if len(file_content) > 2000:
                        file_content = file_content[:2000] + "\n...[truncated]"
                    content_parts.append(f"### {filename}\n{file_content}")
                except Exception as e:
                    logger.debug(f"Failed to load {filepath}: {e}")
        
        # Load lessons-learned summary
        lessons_dir = os.path.join(memory_bank_path, "lessons-learned")
        if os.path.exists(lessons_dir):
            lesson_files = [f for f in os.listdir(lessons_dir) if f.endswith('.md')]
            for filename in lesson_files[:3]:  # Limit to 3 lesson files
                filepath = os.path.join(lessons_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        file_content = f.read()
                    if len(file_content) > 1000:
                        file_content = file_content[:1000] + "\n...[truncated]"
                    content_parts.append(f"### Lessons: {filename}\n{file_content}")
                except Exception as e:
                    logger.debug(f"Failed to load {filepath}: {e}")
        
        return "\n\n".join(content_parts) if content_parts else ""
    
    def _load_project_memory_bank(self, agent: "Agent") -> str:
        """Load project-specific memory bank if agent has active project."""
        # Check if agent has an active project
        active_project = None
        if hasattr(agent, 'get_data'):
            active_project = agent.get_data("active_project")
        
        if not active_project:
            return ""
        
        # Look for project memory bank
        project_path = active_project.get("path", "") if isinstance(active_project, dict) else str(active_project)
        if not project_path:
            return ""
        
        # Check for project meta dir memory-bank or memory-bank in project
        memory_paths = [
            os.path.join(project_path, PROJECT_META_DIR, "memory-bank"),
            os.path.join(project_path, LEGACY_PROJECT_META_DIR, "memory-bank"),
            os.path.join(project_path, "memory-bank"),
        ]
        
        content_parts = []
        for memory_path in memory_paths:
            if os.path.exists(memory_path):
                # Load key files
                for filename in os.listdir(memory_path):
                    if filename.endswith('.md'):
                        filepath = os.path.join(memory_path, filename)
                        try:
                            with open(filepath, 'r') as f:
                                file_content = f.read()
                            if len(file_content) > 1500:
                                file_content = file_content[:1500] + "\n...[truncated]"
                            content_parts.append(f"### {filename}\n{file_content}")
                        except Exception as e:
                            logger.debug(f"Failed to load {filepath}: {e}")
                break  # Only use first found memory bank
        
        return "\n\n".join(content_parts) if content_parts else ""
    
    def _load_agent_memories(self, agent: "Agent") -> str:
        """Load agent's recent memories from memory system."""
        if not hasattr(agent, 'memory') or agent.memory is None:
            return ""
        
        try:
            # Try to get recent memories
            if hasattr(agent.memory, 'search'):
                # Search for recent relevant memories
                memories = agent.memory.search(
                    query="recent task progress errors solutions",
                    limit=5,
                )
                if memories:
                    memory_texts = []
                    for mem in memories:
                        if isinstance(mem, dict):
                            text = mem.get('content', mem.get('text', str(mem)))
                        else:
                            text = str(mem)
                        if len(text) > 500:
                            text = text[:500] + "..."
                        memory_texts.append(f"- {text}")
                    return "\n".join(memory_texts)
            
            # Fallback: try to get memory output
            if hasattr(agent.memory, 'output'):
                output = agent.memory.output()
                if output:
                    if len(output) > 2000:
                        output = output[:2000] + "\n...[truncated]"
                    return output
        except Exception as e:
            logger.debug(f"Failed to load agent memories: {e}")
        
        return ""