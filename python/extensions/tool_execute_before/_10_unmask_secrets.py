from __future__ import annotations
from python.helpers.extension import Extension
from python.helpers.secrets_helper import get_secrets_manager
from python.helpers.notification import NotificationType, NotificationPriority


class UnmaskToolSecrets(Extension):

    async def execute(self, **kwargs):
        # Get tool args from kwargs
        tool_args = kwargs.get("tool_args")
        if not tool_args:
            return

        secrets_mgr = get_secrets_manager(self.agent.context)

        # Scan for missing secrets
        missing_placeholders = []
        for k, v in tool_args.items():
            if isinstance(v, str):
                missing = secrets_mgr.get_missing_placeholders(v)
                if missing:
                    missing_placeholders.extend(missing)

        if missing_placeholders:
            missing_placeholders = list(set(missing_placeholders))
            missing_str = ", ".join(missing_placeholders)
            
            # 1. Notify the user
            msg = f"Missing required secrets: {missing_str}. Please provide them (e.g., in .env or settings) to proceed."
            self.agent.context.get_notification_manager().add_notification(
                message=msg,
                title="Missing Secrets",
                type=NotificationType.WARNING,
                priority=NotificationPriority.HIGH,
                display_time=120  # 2 minute timeout for the notification itself
            )
            
            # 2. Log to history — NON-BLOCKING: notify but roll forward immediately
            # User can add secrets anytime via UI and agent picks them up on next code execution
            await self.agent.hist_add_warning(f"Missing secrets: {missing_str}. Rolling forward — you can provide them anytime via settings.")
            self.agent.context.log.log(type="warning", content=f"Rolling forward without secrets: {missing_str}. Add them via .env or settings when ready.")

        # Unmask placeholders in args for actual tool execution
        # If still missing after wait, replace_placeholders will throw MissingSecretException
        # but we want to roll forward if it's a timeout.
        for k, v in tool_args.items():
            if isinstance(v, str):
                try:
                    tool_args[k] = secrets_mgr.replace_placeholders(v)
                except Exception:
                    # Roll forward logic: Replace §§secret(KEY) with SAMPLE_DATA_FOR_KEY
                    import re
                    def roll_forward_replacer(match):
                        key = match.group(1).upper()
                        return f"SAMPLE_DATA_FOR_{key}"
                    
                    tool_args[k] = re.sub(secrets_mgr.PLACEHOLDER_PATTERN, roll_forward_replacer, v)
