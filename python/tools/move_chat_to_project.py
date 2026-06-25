from __future__ import annotations
"""
Move Chat to Project Tool for AGIX
"""
from python.helpers.tool import Tool, Response
from python.helpers import projects

class MoveChatToProject(Tool):
    """
    Tool for moving the current chat session into a specific project.
    
    This tool associates the current conversation with an existing project,
    enabling project-aware behavior, such as using project-specific instructions,
    accessing project memory, and performing repository automation within the project context.
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        Execute moving the chat to a project.
        
        Args (via kwargs):
            project_name: The name of the project to move the chat to (required).
            
        Returns:
            Response with the result of the move operation.
        """
        project_name = kwargs.get("project_name", "").strip()
        
        if not project_name:
            # Provide a list of active projects if no name is given
            active_projects = projects.get_active_projects_list()
            project_names = [p["name"] for p in active_projects]
            
            error_msg = "Project name is required. "
            if project_names:
                error_msg += f"Available projects: {', '.join(project_names)}"
            else:
                error_msg += "No active projects found. You may need to create one first using 'setup_project'."
                
            return Response(
                message=error_msg,
                break_loop=False,
            )
            
        try:
            # Activate the project for the current context
            projects.activate_project(self.agent.context.id, project_name)
            
            # Get project details for the response
            project_data = projects.load_basic_project_data(project_name)
            title = project_data.get("title", project_name)
            
            return Response(
                message=f"Chat successfully moved to project: **{title}** ({project_name}).\n"
                        f"Project-specific instructions and memory are now active for this session.",
                break_loop=False,
            )
        except Exception as e:
            return Response(
                message=f"Failed to move chat to project '{project_name}': {str(e)}",
                break_loop=False,
            )
