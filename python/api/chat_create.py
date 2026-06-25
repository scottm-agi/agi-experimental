from __future__ import annotations
from python.helpers.api import ApiHandler, Input, Output, Request, Response


from python.helpers import projects, guids
from python.agent import AgentContext


class CreateChat(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        current_ctxid = input.get("current_context", "") # current context id
        project_name = input.get("project", "") # explicit project name
        new_ctxid = input.get("new_context", guids.generate_id()) # given or new guid

        # get/create new context
        new_context = await self.use_context(new_ctxid)

        # Determine project to activate
        target_project = None
        if "project" in input:
            target_project = input.get("project")
        else:
            from python.helpers import persist_chat
            current_context = await persist_chat.load_chat(current_ctxid)
            if current_context:
                target_project = current_context.get_data(projects.CONTEXT_DATA_KEY_PROJECT)

        # Fallback to default if no project specified or inherited
        if not target_project:
            target_project = "default"

        # Activate the resolved project
        await projects.activate_project(new_context.id, target_project)

        # Ensure context is persisted to disk immediately for external tools/scripts
        from python.helpers import persist_chat
        persist_chat.save_tmp_chat(new_context)

        return {
            "ok": True,
            "ctxid": new_context.id,
            "message": "Context created.",
        }
