from __future__ import annotations
from python.helpers.tool import Tool, Response
from python.agent import AgentContext
from python.helpers.secrets_helper import get_secrets_manager, alias_for_key
from python.helpers.notification import NotificationPriority, NotificationType

class RequestSecretTool(Tool):
    """
    Tool for agents to explicitly request missing secrets/credentials from the user.
    This triggers a high-priority notification in the WebUI.
    """

    async def execute(self, keys: list[str], reason: str = "", **kwargs):
        if not keys:
            return Response(message="Error: No secret keys provided.", break_loop=False)

        sm = get_secrets_manager(self.agent.context)
        missing_keys = []
        
        # Normalize and check keys
        for key in keys:
            key = key.upper()
            if key not in sm.load_secrets():
                missing_keys.append(key)
        
        if not missing_keys:
            return Response(message="All requested secrets are already available in the secrets store.", break_loop=False)

        # Build notification message
        keys_str = ", ".join([f"'{k}'" for k in missing_keys])
        message = f"Agent '{self.agent.agent_name}' is requesting the following missing secrets: {keys_str}."
        if reason:
            message += f" Reason: {reason}"
        
        # Get placeholders for the response
        placeholders = [alias_for_key(k) for k in missing_keys]
        
        # Send Notification
        AgentContext.get_notification_manager().add_notification(
            message=message,
            title="Action Required: Missing Secrets",
            detail=f"The agent requires these secrets to proceed with your request. Please add them to your settings or provide them in the chat. <br><br>Missing keys: {', '.join(missing_keys)}",
            type=NotificationType.WARNING,
            priority=NotificationPriority.HIGH,
            display_time=60, # Keep visible for 1 minute
        )

        response_msg = (
            f"The following secrets have been requested from the user: {', '.join(missing_keys)}.\n"
            f"Please wait for the user to provide them. You can use them in your files/commands using placeholders: {', '.join(placeholders)}.\n"
            "If the user provides them in the chat, you will need to reload your secrets or wait for the system to synchronize."
        )

        return Response(message=response_msg, break_loop=False)
