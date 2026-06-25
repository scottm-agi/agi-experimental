import os
from typing import Any, Optional
from python.helpers.files import VariablesPlugin
from python.helpers import files
from python.helpers.print_style import PrintStyle


class CallSubordinate(VariablesPlugin):
    def get_variables(self, file: str, backup_dirs: Optional[list[str]] = None, **kwargs) -> dict[str, Any]:
        agent = kwargs.get("agent")

        # collect all prompt folders in order of their priority
        folder = files.get_abs_path(os.path.dirname(file))
        folders = [folder]
        if backup_dirs:
            for backup_dir in backup_dirs:
                folders.append(files.get_abs_path(backup_dir))

        # collect all tool instruction files
        prompt_files = files.get_unique_filenames_in_dirs(folders, "agent.system.tool.*.md")
        
        # load tool instructions
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        
        # Determine active profile (this is tricky as we don't have agent here directly, but we can look for it in kwargs or context)
        # However, the generic VariablesPlugin doesn't always pass agent.
        # Let's see if we can get it from kwargs passed to read_prompt_file in agent.py
        agent = kwargs.get("agent")
        profile = "default"
        if agent and hasattr(agent, "config"):
            profile = agent.config.profile or "default"

        tools = []
        for prompt_file in prompt_files:
            # Extract tool name from filename: agent.system.tool.toolname.md
            tool_name = os.path.basename(prompt_file).replace("agent.system.tool.", "").replace(".md", "")
            
            if not selector.should_include_tool(tool_name, profile):
                continue

            try:
                tool = files.read_prompt_file(prompt_file)
                tools.append(tool)
            except Exception as e:
                PrintStyle().error(f"Error loading tool '{prompt_file}': {e}")

        return {"tools": "\n\n".join(tools)}
