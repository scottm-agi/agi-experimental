from __future__ import annotations
"""
List Projects Tool for AGIX

Provides agents with a way to discover existing projects before creating new ones.
This prevents duplicate project creation (MSR-13).
"""

import logging
from python.helpers.tool import Tool, Response
from python.helpers import projects

logger = logging.getLogger("agix.tools.list_projects")


class ListProjects(Tool):
    """
    Tool for listing all existing projects in the workspace.
    
    Agents MUST use this tool before calling setup_project to:
    1. Avoid creating duplicate projects
    2. Find the correct project to activate
    3. Understand what already exists in the workspace
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        List all projects or search for a specific one.
        
        Args (via kwargs):
            search: Optional search term to filter projects by name/title/description
            
        Returns:
            Response with formatted project list
        """
        search = kwargs.get("search", "").strip().lower()
        
        try:
            project_list = projects.get_active_projects_list()
            
            if search:
                # Filter by search term matching name, title, or description
                filtered = [
                    p for p in project_list
                    if search in p.get("name", "").lower()
                    or search in p.get("title", "").lower()
                    or search in p.get("description", "").lower()
                ]
            else:
                filtered = project_list
            
            if not filtered:
                if search:
                    return Response(
                        message=f"No projects found matching '{search}'. "
                                f"Total projects: {len(project_list)}. "
                                f"Use setup_project to create a new one.",
                        break_loop=False,
                    )
                return Response(
                    message="No projects found. Use setup_project to create one.",
                    break_loop=False,
                )
            
            # Format output
            output = f"## Existing Projects ({len(filtered)}"
            if search:
                output += f" matching '{search}'"
            output += f" of {len(project_list)} total)\n\n"
            
            for p in filtered:
                name = p.get("name", "unknown")
                title = p.get("title", "")
                desc = p.get("description", "")
                color = p.get("color", "")
                created = p.get("created_at", "")
                updated = p.get("updated_at", "")
                
                output += f"### {title or name}\n"
                output += f"- **Name:** `{name}`\n"
                if desc:
                    output += f"- **Description:** {desc[:200]}\n"
                if color:
                    output += f"- **Color:** {color}\n"
                if created:
                    output += f"- **Created:** {created[:10]}\n"
                if updated:
                    output += f"- **Updated:** {updated[:10]}\n"
                
                # Check git remote
                remote = projects.get_project_git_remote(name)
                if remote:
                    output += f"- **Git Remote:** {remote}\n"
                
                output += "\n"
            
            output += "---\n"
            output += "**To activate a project:** Use `activate_project` with the project name.\n"
            output += "**To create a new project:** Use `setup_project` (but ONLY if no existing project matches).\n"
            
            return Response(
                message=output,
                break_loop=False,
            )
            
        except Exception as e:
            logger.exception(f"Failed to list projects: {e}")
            return Response(
                message=f"Error listing projects: {e}",
                break_loop=False,
            )
