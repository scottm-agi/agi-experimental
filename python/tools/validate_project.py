from __future__ import annotations
"""
Validate Project Tool for AGIX

Provides a tool for agents to validate that a project is ready for development.
"""

from python.helpers.tool import Tool, Response
from python.helpers.project_setup import validate_project_ready


class ValidateProject(Tool):
    """
    Tool for validating that a project is ready for development.
    
    Checks:
    - Directory exists
    - Git initialized
    - MISE config present
    - Gitignore present
    - README present
    - AGIX project metadata present
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        Validate project readiness.
        
        Args (via kwargs):
            path: Project path (required)
            
        Returns:
            Response with validation results
        """
        project_path = kwargs.get("path", "").strip()
        
        if not project_path:
            return Response(
                message="Error: Project path is required",
                break_loop=False,
            )
        
        try:
            checks = validate_project_ready(project_path)
            all_passed = all(checks.values())
            
            output = f"""## Project Validation {'✓' if all_passed else '✗'}

**Path:** {project_path}

### Checks:
"""
            for check, passed in checks.items():
                status = "✓" if passed else "✗"
                output += f"- [{status}] {check.replace('_', ' ').title()}\n"
            
            if not all_passed:
                failed = [k for k, v in checks.items() if not v]
                output += f"\n**Missing:** {', '.join(failed)}\n"
                output += "\nRun `setup_project` to complete the setup.\n"
            else:
                output += "\nProject is ready for development!\n"
            
            return Response(
                message=output,
                break_loop=False,
            )
        except Exception as e:
            return Response(
                message=f"Validation error: {e}",
                break_loop=False,
            )
