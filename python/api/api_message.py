from __future__ import annotations
import base64
import os
from datetime import datetime, timedelta
from python.agent import AgentContext, UserMessage, AgentContextType
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import files
from python.helpers.print_style import PrintStyle
from werkzeug.utils import secure_filename
from python.initialize import initialize_agent
import threading


class ApiMessage(ApiHandler):
    # Track chat lifetimes for cleanup
    _chat_lifetimes = {}
    _cleanup_lock = threading.Lock()

    @classmethod
    def requires_auth(cls) -> bool:
        return False  # No web auth required

    @classmethod
    def requires_csrf(cls) -> bool:
        return False  # No CSRF required

    @classmethod
    def requires_api_key(cls) -> bool:
        return True  # Require API key

    async def process(self, input: dict, request: Request) -> dict | Response:
        # Extract parameters
        context_id = input.get("context_id", "")
        message = input.get("message", "")
        attachments = input.get("attachments", [])
        lifetime_hours = input.get("lifetime_hours", 24)  # Default 24 hours
        integration = input.get("integration", False)  # Integration flag for guardrails

        if not message:
            return Response('{"error": "Message is required"}', status=400, mimetype="application/json")

        # Handle attachments (base64 encoded)
        attachment_paths = []
        if attachments:
            upload_folder_int = "/agix/tmp/uploads" if os.path.exists("/agix/tmp") else "/agix/tmp/uploads"
            upload_folder_ext = files.get_abs_path("tmp/uploads")
            os.makedirs(upload_folder_ext, exist_ok=True)

            for attachment in attachments:
                if not isinstance(attachment, dict) or "filename" not in attachment or "base64" not in attachment:
                    continue

                try:
                    filename = secure_filename(attachment["filename"])
                    if not filename:
                        continue

                    # Decode base64 content
                    file_content = base64.b64decode(attachment["base64"])

                    # Save to temp file
                    save_path = os.path.join(upload_folder_ext, filename)
                    with open(save_path, "wb") as f:
                        f.write(file_content)

                    attachment_paths.append(os.path.join(upload_folder_int, filename))
                except Exception as e:
                    PrintStyle.error(f"Failed to process attachment {attachment.get('filename', 'unknown')}: {e}")
                    continue

        # Get or create context
        if context_id:
            context = AgentContext.use(context_id)
            if not context:
                # Context doesn't exist - create new one and return its actual ID
                # This allows multi-turn conversations to work even with custom context_ids
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                AgentContext.use(context.id)
                context_id = context.id  # Return the actual new context_id
                PrintStyle(font_color="yellow", padding=False).print(
                    f"Context not found, created new context: {context_id}"
                )
        else:
            config = initialize_agent()
            context = AgentContext(config=config, type=AgentContextType.USER)
            AgentContext.use(context.id)
            context_id = context.id

        # Set source_type for integration requests to trigger guardrails
        if integration:
            context.set_data("source_type", "integration")
            PrintStyle(font_color="yellow", padding=False).print(
                f"Integration mode enabled for context {context_id} — guardrails active"
            )

        # Update chat lifetime
        with self._cleanup_lock:
            self._chat_lifetimes[context_id] = datetime.now() + timedelta(hours=lifetime_hours)

        # Process message
        try:
            # Log the message
            attachment_filenames = [os.path.basename(path) for path in attachment_paths] if attachment_paths else []

            PrintStyle(
                background_color="#6C3483", font_color="white", bold=True, padding=True
            ).print("External API message:")
            PrintStyle(font_color="white", padding=False).print(f"> {message}")
            if attachment_filenames:
                PrintStyle(font_color="white", padding=False).print("Attachments:")
                for filename in attachment_filenames:
                    PrintStyle(font_color="white", padding=False).print(f"- {filename}")

            # Add user message to chat history so it's visible in the UI
            context.log.log(
                type="user",
                heading="User message",
                content=message,
                kvps={"attachments": attachment_filenames},
            )

            # Send message to agent
            task = context.communicate(UserMessage(message, attachment_paths))
            result = await task.result()

            # Clean up expired chats
            self._cleanup_expired_chats()

            # Apply output content filter for integration requests
            if integration and result:
                try:
                    from python.helpers.content_filter import ContentFilter
                    filter_result = ContentFilter.scan(str(result))
                    if filter_result.has_violations:
                        PrintStyle(font_color="yellow", padding=False).print(
                            f"Content filter: {len(filter_result.violations)} violations found"
                        )
                    if filter_result.blocked:
                        result = (
                            "I cannot provide this response due to security policies. "
                            "Please rephrase your request."
                        )
                    else:
                        result = filter_result.filtered
                except Exception as e:
                    PrintStyle.error(f"Content filter error (non-blocking): {e}")

            # RCA-347b: monologue() may return Response objects (post F-1 fix).
            # Coerce to string before putting in JSON-serializable dict.
            from python.helpers.tool import Response as ToolResponse
            if isinstance(result, ToolResponse):
                result = result.message or ""

            return {
                "context_id": context_id,
                "response": result
            }

        except Exception as e:
            PrintStyle.error(f"External API error: {e}")
            return Response(f'{{"error": "{str(e)}"}}', status=500, mimetype="application/json")

    @classmethod
    def _cleanup_expired_chats(cls):
        """Clean up expired chats"""
        with cls._cleanup_lock:
            now = datetime.now()
            expired_contexts = [
                context_id for context_id, expiry in cls._chat_lifetimes.items()
                if now > expiry
            ]

            for context_id in expired_contexts:
                try:
                    context = AgentContext.get(context_id)
                    if context:
                        context.reset()
                        AgentContext.remove(context_id)
                    del cls._chat_lifetimes[context_id]
                    PrintStyle().print(f"Cleaned up expired chat: {context_id}")
                except Exception as e:
                    PrintStyle.error(f"Failed to cleanup chat {context_id}: {e}")
