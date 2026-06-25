from __future__ import annotations
"""
Create Chat Tool for AGIX
"""
from python.helpers.tool import Tool, Response
from python.agent import AgentContext, UserMessage
from python.helpers import projects, persist_chat, guids

class CreateChat(Tool):
    """
    Tool for programmatically creating a new chat session.
    
    This tool allows an agent to spawn a new conversation session. 
    A new chat can be optionally associated with a project and can include
    an initial message to start the conversation automatically.
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        Execute creating a new chat.
        
        Args (via kwargs):
            name: (optional) A descriptive name for the new chat.
            project_name: (optional) The name of the project to associate the new chat with.
            reason: (optional) The rationale for branching this chat.
            mission: (optional) A clear, concise mission statement for the new chat.
            initial_message: (optional) An initial message to start the conversation.
            
        Returns:
            Response with the result of the chat creation, including the new chat ID.
        """
        name = kwargs.get("name", "New Project Chat" if kwargs.get("project_name") else "New Chat")
        project_name = kwargs.get("project_name", "").strip()
        reason = kwargs.get("reason", "").strip()
        mission = kwargs.get("mission", "").strip()
        initial_message = kwargs.get("initial_message", "").strip()
        
        try:
            # 0. Check safety limits
            parent_ctx = self.agent.context
            spawned_count = parent_ctx.get_data("spawned_count") or 0
            if spawned_count >= 10:
                return Response(
                    message=f"Branching limit reached: You have already spawned {spawned_count} chats from this context. "
                            f"To prevent resource exhaustion, the hard limit is 10 branched chats per parent.",
                    break_loop=False,
                )

            # 1. Create a new context
            new_ctxid = guids.generate_id()
            new_context = AgentContext(config=self.agent.config, id=new_ctxid, name=name)
            
            # Update spawned count in parent
            parent_ctx.set_data("spawned_count", spawned_count + 1)
            
            # Record parent linkage and branching metadata
            parent_ctxid = self.agent.context.id
            new_context.set_data("parent_ctxid", parent_ctxid)
            if reason:
                new_context.set_data("reason", reason)
            if mission:
                new_context.set_data("mission", mission)
            
            # 2. Associate with project if requested
            if project_name:
                projects.activate_project(new_ctxid, project_name)
                
            # 3. Add initial message and trigger agent if provided
            if initial_message:
                from python.agent import UserMessage
                new_context.communicate(UserMessage(message=initial_message, attachments=[]))
            
            # 4. Persist to disk
            persist_chat.save_tmp_chat(new_context)
            
            # Get project title if applicable
            project_info = ""
            if project_name:
                try:
                    p_data = projects.load_basic_project_data(project_name)
                    project_info = f" associated with project **{p_data.get('title', project_name)}**"
                except Exception:
                    project_info = f" associated with project **{project_name}**"

            mission_info = f"\n**Mission**: {mission}" if mission else ""

            return Response(
                message=f"New chat session branched successfully: **{name}** (ID: `{new_ctxid}`){project_info}.{mission_info}\n"
                        f"Parent Chat ID: `{parent_ctxid}`. The new standalone session is now available in the UI and ready for work.",
                break_loop=False,
                additional={"ctxid": new_ctxid, "parent_ctxid": parent_ctxid, "reason": reason, "mission": mission}
            )
            
        except Exception as e:
            return Response(
                message=f"Failed to create new chat session: {str(e)}",
                break_loop=False,
            )
