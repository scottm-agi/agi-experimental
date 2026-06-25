from __future__ import annotations
"""
Setup Project Tool for AGIX

Provides a tool for agents to set up new projects with mise-en-place.
This tool creates project directories, initializes git, sets up MISE, etc.
"""

import json
import logging
from python.helpers.tool import Tool, Response
from python.helpers.project_setup import mise_en_place, MiseEnPlaceResult
from python.helpers.mise_manager import Framework

logger = logging.getLogger("agix.tools.setup_project")


class SetupProject(Tool):
    """
    Tool for setting up new projects with mise-en-place.
    
    This tool orchestrates the complete project setup process:
    - Creates project directory at /projects/<name>
    - Initializes git repository
    - Detects or uses specified framework
    - Creates .mise.toml configuration
    - Creates .gitignore
    - Creates README.md
    - Initializes AGIX project metadata with full definition (BasicProjectData)
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        Execute project setup.
        
        Args (via kwargs):
            name: Project name (required)
            description: Project description (optional)
            framework: Framework type - python, nodejs, rust, go, ruby, java, fullstack, generic (optional)
            auto_install: Whether to auto-install MISE tools (optional, default False)
            
        Returns:
            Response with setup results
        """
        # Extract parameters
        project_name = kwargs.get("name", "").strip()
        description = kwargs.get("description", "")
        framework = kwargs.get("framework", None)
        auto_install = kwargs.get("auto_install", False)
        
        # Complete project data fields
        title = kwargs.get("title", project_name.replace("-", " ").title())
        instructions = kwargs.get("instructions", "")
        color = kwargs.get("color", "")
        memory = kwargs.get("memory", "own") # own or global
        
        # File structure settings
        fs_settings = kwargs.get("file_structure", {})
        if not isinstance(fs_settings, dict): fs_settings = {}
        
        from python.helpers import projects
        project_data = projects.BasicProjectData(
            title=title,
            description=description,
            instructions=instructions,
            color=color,
            memory=memory, # type: ignore
            file_structure=projects._normalizeFileStructure(fs_settings)
        )
        
        # Validate required parameters
        if not project_name:
            return Response(
                message=self._format_error("Project name is required"),
                break_loop=False,
            )
        
        if not description:
            return Response(
                message=self._format_error("Project description is MANDATORY. Please provide a clear summary of what the project is about."),
                break_loop=False,
            )
        # Validate framework if provided
        valid_frameworks = [f.value for f in Framework]
        if framework and framework.lower() not in valid_frameworks:
            return Response(
                message=self._format_error(
                    f"Invalid framework '{framework}'. Valid options: {', '.join(valid_frameworks)}"
                ),
                break_loop=False,
            )
        
        # Execute project setup
        try:
            result = mise_en_place(
                project_name=project_name,
                description=description,
                framework=framework,
                auto_install_tools=auto_install,
                project_data=project_data,
            )
            
            # Associate current chat with this project if requested
            associate_chat = kwargs.get("associate_chat", True)
            if result.success and associate_chat:
                try:
                    # Use result.project_name (actual folder name) instead of input project_name
                    # as projects might have been renamed during creation (e.g., myproject_1)
                    actual_name = result.project_name
                    projects.activate_project(self.agent.context.id, actual_name)
                    logger.info(f"Chat {self.agent.context.id} associated with new project: {actual_name}")
                except Exception as ae:
                    logger.warning(f"Failed to associate chat with new project: {ae}")

            return Response(
                message=self._format_result(result, associate_chat and result.success),
                break_loop=False,
            )
        except Exception as e:
            return Response(
                message=self._format_error(f"Project setup failed: {e}"),
                break_loop=False,
            )
    
    def _format_result(self, result: MiseEnPlaceResult, chat_associated: bool = False) -> str:
        """Format the setup result for display."""
        if result.success:
            output = f"""## Project Setup Complete ✓

**Project:** {result.project_name}
**Path:** {result.project_path}
**Framework:** {result.framework.value}
**Chat Associated:** {"YES" if chat_associated else "NO"} (Reflected in UI sidebar)
**Duration:** {result.duration_seconds:.2f}s

### Completed Steps:
"""
            for step_result in result.steps:
                status = "✓" if step_result.success else "✗"
                output += f"- [{status}] {step_result.step.value}: {step_result.message}\n"
            
            output += f"""
### Next Steps:
1. Navigate to the project: `cd {result.project_path}`
2. Install MISE tools: `mise install && mise trust`
3. Install dependencies: `mise run install`
4. Start development: `mise run dev`

The project is ready for development!
"""
        else:
            output = f"""## Project Setup Failed ✗

**Project:** {result.project_name}
**Error:** {result.error}

### Step Results:
"""
            for step_result in result.steps:
                status = "✓" if step_result.success else "✗"
                output += f"- [{status}] {step_result.step.value}: {step_result.message}\n"
            
            if result.failed_steps:
                output += f"\n**Failed Steps:** {', '.join(s.value for s in result.failed_steps)}\n"
        
        return output
    
    def _format_error(self, message: str) -> str:
        """Format an error message."""
        return f"""## Project Setup Error

{message}

### Usage:
```
setup_project:
  name: my-project
  title: "My Project Title" # Optional, defaults to name
  description: "A comprehensive description is MANDATORY for good project initialization"
  instructions: "Specify any high-level project-specific instructions here"
  framework: python  # Optional: python, nodejs, rust, go, ruby, java, fullstack, generic
  color: "#4A90E2" # Optional: UI color for the project
  memory: own # Optional: 'own' (isolated) or 'global' (shared)
  file_structure: # Optional: nested settings for file structure injection
    enabled: true
    max_depth: 5
    max_lines: 250
  auto_install: false  # Optional: whether to auto-install MISE tools
  associate_chat: true # Optional: whether to move the current chat to this project (default: true)
```
"""
