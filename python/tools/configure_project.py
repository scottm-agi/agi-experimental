from __future__ import annotations
"""
Configure Project Tool for AGIX

Provides a tool for agents to update project metadata (title, description, instructions, etc.).
"""

from python.helpers.tool import Tool, Response
from python.helpers import projects

class ConfigureProject(Tool):
    """
    Tool for updating project metadata.
    
    This tool allows agents to refine the project definition:
    - Update title and description
    - Set project-specific instructions
    - Change UI color and memory settings
    - Configure file structure presentation settings
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        Execute project configuration update.
        
        Args (via kwargs):
            name: Project name (required)
            title: Project title (optional)
            description: Project description (optional)
            instructions: Project instructions (optional)
            color: Project UI color (optional)
            memory: Memory type (own/global) (optional)
            file_structure: File structure settings (dict) (optional)
            
        Returns:
            Response with update results
        """
        project_name = kwargs.get("name", "").strip()
        
        if not project_name:
            return Response(
                message="Error: Project name is required",
                break_loop=False,
            )
            
        try:
            # Load existing data to merge
            current = projects.load_edit_project_data(project_name)
            
            # Update fields if provided
            if "title" in kwargs: current["title"] = kwargs["title"]
            if "description" in kwargs: current["description"] = kwargs["description"]
            if "instructions" in kwargs: current["instructions"] = kwargs["instructions"]
            if "color" in kwargs: current["color"] = kwargs["color"]
            if "memory" in kwargs: current["memory"] = kwargs["memory"]
            
            if "file_structure" in kwargs and isinstance(kwargs["file_structure"], dict):
                current["file_structure"].update(kwargs["file_structure"])
                
            # Normalize and save
            projects.update_project(project_name, current)
            
            return Response(
                message=f"## Project Configuration Updated ✓\n\nProject '{project_name}' metadata has been updated successfully.",
                break_loop=False,
            )
        except Exception as e:
            return Response(
                message=f"## Configuration Error ✗\n\nFailed to update project '{project_name}': {e}",
                break_loop=False,
            )
