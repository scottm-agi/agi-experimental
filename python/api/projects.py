from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response
from python.helpers import projects


class Projects(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        action = input.get("action", "")
        ctxid = input.get("context_id", None)

        if ctxid:
            _context = await self.use_context(ctxid)

        try:
            if action == "list":
                data = self.get_active_projects_list()
            elif action == "load":
                data = self.load_project(input.get("name", None))
            elif action == "create":
                data = self.create_project(input.get("project", None))
            elif action == "update":
                data = self.update_project(input.get("project", None))
            elif action == "delete":
                data = await self.delete_project(input.get("name", None))
            elif action == "activate":
                data = await self.activate_project(ctxid, input.get("name", None))
            elif action == "deactivate":
                data = await self.deactivate_project(ctxid)
            elif action == "file_structure":
                data = self.get_file_structure(input.get("name", None), input.get("settings"))
            else:
                raise Exception("Invalid action")

            return {
                "ok": True,
                "success": True,
                "data": data,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
            }

    def get_active_projects_list(self):
        return projects.get_active_projects_list()

    def create_project(self, project: dict|None):
        if project is None:
            raise Exception("Project data is required")
        
        # We use EditProjectData to ensure we capture secrets and parameters if provided
        data = projects.EditProjectData(**project)
        name = projects.create_project(project["name"], data)
        
        # If additional fields are present, we should call update_project logic to save them
        # since basic projects.create_project only handles the header fields.
        if project.get("secrets") or project.get("parameters"):
            projects.update_project(name, data)
            
        return projects.load_edit_project_data(name)

    def load_project(self, name: str|None):
        if name is None:
            raise Exception("Project name is required")
        return projects.load_edit_project_data(name)

    def update_project(self, project: dict|None):
        if project is None:
            raise Exception("Project data is required")
        data = projects.EditProjectData(**project)
        name = projects.update_project(project["name"], data)
        return projects.load_edit_project_data(name)

    async def delete_project(self, name: str|None):
        if name is None:
            raise Exception("Project name is required")
        result = await projects.delete_project(name)
        # Surface filesystem deletion warning to the API response
        if result.get("warning"):
            return {"name": result["name"], "deleted": result["deleted"], "warning": result["warning"]}
        return result

    async def activate_project(self, context_id: str|None, name: str|None):
        if not context_id:
            raise Exception("Context ID is required")
        if not name:
            raise Exception("Project name is required") 
        return await projects.activate_project(context_id, name)

    async def deactivate_project(self, context_id: str|None):
        if not context_id:
            raise Exception("Context ID is required")
        return await projects.deactivate_project(context_id)

    def get_file_structure(self, name: str|None, settings: dict|None):
        if not name:
            raise Exception("Project name is required")
        # project data
        basic_data = projects.load_basic_project_data(name)
        # override file structure settings
        if settings:
            basic_data["file_structure"] = settings # type: ignore
        # get structure
        return projects.get_file_structure(name, basic_data)