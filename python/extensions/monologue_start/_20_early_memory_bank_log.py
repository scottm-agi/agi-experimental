from __future__ import annotations
"""
Extension hook to log the start of a task in the project's memory-bank.
Ensures activeContext.md is updated at the START of work, not just at the end.
Extrapolates meaningful context from the user prompt and project state.
"""

import os
from datetime import datetime
from python.helpers.extension import Extension
from python.helpers.print_style import PrintStyle
from python.helpers import projects, files


class EarlyMemoryBankLog(Extension):
    """Extension to log task start in memory-bank at monologue_start."""

    async def execute(self, loop_data=None, **kwargs):
        """Log task start to activeContext.md with extrapolated context."""
        try:
            # Get active project
            project_name = projects.get_context_project_name(self.agent.context)
            
            if not project_name:
                # No active project - skip
                return
            
            # Only run for the root agent (agent_0) to avoid duplicate logs from sub-agents
            if self.agent.number != 0:
                return
            
            # Get the user's input message that triggered this monologue
            user_input = self._get_user_message(loop_data)
            
            # Get memory-bank directory
            mb_dir = projects.get_project_memory_bank_folder(project_name)
            if not os.path.exists(mb_dir):
                return
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Get project context for extrapolation
            project_context = self._get_project_context(project_name)
            
            # Update activeContext.md with task start and extrapolated context
            await self._log_task_start(mb_dir, timestamp, user_input, project_context)
            
            PrintStyle.hint(f"Memory bank: logged task start for project: {project_name}")
            
        except Exception as e:
            # Don't fail the monologue if memory-bank update fails
            PrintStyle.error(f"Early memory-bank log failed: {e}")

    def _get_user_message(self, loop_data) -> str:
        """Extract user message from various sources.
        
        Handles both Message objects (with .content attr) and plain strings.
        Root cause fix (MSR_Smoke_1776891952): str(Message) returns repr,
        not content. Must use .content accessor.
        """
        user_input = ""
        
        # Try loop_data first (most reliable)
        if loop_data:
            if hasattr(loop_data, 'user_message') and loop_data.user_message:
                user_input = self._extract_text(loop_data.user_message)
            elif hasattr(loop_data, 'message') and loop_data.message:
                user_input = self._extract_text(loop_data.message)
        
        # Fallback to context
        if not user_input and self.agent.context:
            if hasattr(self.agent.context, 'last_user_message') and self.agent.context.last_user_message:
                user_input = self._extract_text(self.agent.context.last_user_message)
        
        # Fallback to history
        if not user_input and hasattr(self.agent, 'history') and self.agent.history:
            # Get last user message from history
            # Use output() to get a flat list of OutputMessage dicts
            for msg in reversed(self.agent.history.output()):
                if msg.get('ai') is False:
                    user_input = str(msg.get('content', ""))
                    break
        
        return user_input[:500] if user_input else ""

    @staticmethod
    def _extract_text(obj) -> str:
        """Extract text content from a Message object or string.
        
        Message objects have a .content attribute containing the actual text.
        str() on a Message returns its repr (e.g. '<python.history.Message object>'),
        so we must use .content instead.
        """
        if obj is None:
            return ""
        # Message objects have .content attribute
        if hasattr(obj, 'content') and not isinstance(obj, dict):
            content = obj.content
            if isinstance(content, str):
                return content
            # content could be a list of dicts (multimodal) — extract text parts
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and item.get('type') == 'text':
                        parts.append(item.get('text', ''))
                return ' '.join(parts)
            return str(content)
        # Dict messages (e.g. {'user_message': 'Build...'})
        if isinstance(obj, dict):
            return str(obj.get('user_message', obj.get('content', obj.get('text', ''))))
        # Plain string
        if isinstance(obj, str):
            return obj
        # Last resort
        return str(obj)

    def _get_project_context(self, project_name: str) -> dict:
        """Get relevant project context for extrapolation."""
        context = {
            "project_name": project_name,
            "has_files": False,
            "recent_focus": "",
            "key_files": []
        }
        
        try:
            # Check project folder for files
            project_folder = projects.get_project_folder(project_name)
            if project_folder and os.path.exists(project_folder):
                files_in_project = os.listdir(project_folder)
                context["has_files"] = len(files_in_project) > 0
                # Note key files (not memory-bank or hidden)
                context["key_files"] = [f for f in files_in_project 
                                        if not f.startswith('.') and f != 'memory-bank'][:5]
            
            # Check for existing activeContext.md content
            mb_dir = projects.get_project_memory_bank_folder(project_name)
            active_context_path = os.path.join(mb_dir, "activeContext.md")
            if os.path.exists(active_context_path):
                content = files.read_file(active_context_path)
                # Extract last meaningful section (skip template lines)
                lines = content.strip().split('\n')
                if len(lines) > 3:  # More than just template
                    # Find last "## " section header
                    for i, line in enumerate(reversed(lines)):
                        if line.startswith('## '):
                            context["recent_focus"] = line[3:].strip()[:100]
                            break
                            
        except Exception:
            pass  # Non-critical, continue with partial context
        
        return context

    def _extrapolate_task_summary(self, user_input: str) -> str:
        """Extrapolate a brief task summary from the user's prompt."""
        if not user_input:
            return "(Task in progress)"
        
        # Lowercase for analysis
        lower_input = user_input.lower()
        
        # Detect common task patterns
        task_type = "Working on"
        
        if any(kw in lower_input for kw in ['audit', 'test', 'verify', 'check']):
            task_type = "Auditing/Testing"
        elif any(kw in lower_input for kw in ['fix', 'debug', 'solve', 'error', 'bug']):
            task_type = "Debugging/Fixing"
        elif any(kw in lower_input for kw in ['create', 'build', 'implement', 'add', 'new']):
            task_type = "Building/Creating"
        elif any(kw in lower_input for kw in ['update', 'modify', 'change', 'refactor']):
            task_type = "Updating/Refactoring"
        elif any(kw in lower_input for kw in ['analyze', 'research', 'investigate', 'find']):
            task_type = "Analyzing/Researching"
        elif any(kw in lower_input for kw in ['deploy', 'release', 'publish']):
            task_type = "Deploying"
        
        # Extract first meaningful sentence or truncate
        first_line = user_input.split('\n')[0].strip()
        if len(first_line) > 150:
            first_line = first_line[:147] + "..."
        
        return f"{task_type}: {first_line}"

    async def _log_task_start(self, mb_dir: str, timestamp: str, user_input: str, context: dict):
        """Log task start to activeContext.md with extrapolated context."""
        filepath = os.path.join(mb_dir, "activeContext.md")
        
        # Extrapolate task summary
        task_summary = self._extrapolate_task_summary(user_input)
        
        # Build update content
        update_lines = [
            f"\n## Task Started ({timestamp})",
            f"- **Agent**: {self.agent.agent_name}",
            f"- **Objective**: {task_summary}",
        ]
        
        # Add context about the project state
        if context.get("recent_focus"):
            update_lines.append(f"- **Previous Focus**: {context['recent_focus']}")
        
        if context.get("key_files"):
            files_str = ", ".join(context["key_files"][:3])
            update_lines.append(f"- **Project Files**: {files_str}")
        
        # Add the raw user prompt (truncated) for reference
        if user_input:
            preview = user_input[:200].replace("\n", " ").strip()
            if len(user_input) > 200:
                preview += "..."
            update_lines.append(f"- **User Request**: {preview}")
        
        update_content = "\n".join(update_lines) + "\n"
        
        # Read current content
        current_content = ""
        if os.path.exists(filepath):
            try:
                current_content = files.read_file(filepath)
            except Exception:
                pass
        
        # Check if it's just the template (2-3 lines)
        if current_content.count("\n") <= 3:
            # Replace template entirely
            new_content = "# Active Context\n\nTracks current focus, recent changes, and immediate next steps.\n" + update_content
        else:
            # Append to existing content
            new_content = current_content.rstrip() + update_content
        
        files.write_file(filepath, new_content)