from __future__ import annotations
from typing import Optional
import difflib
from python.helpers.tool import Tool, Response
from python.helpers.secrets_helper import get_secrets_manager, get_default_secrets_manager, get_project_secrets_manager
from python.helpers import projects
from python.helpers.print_style import PrintStyle


def fuzzy_suggest_keys(key: str, available_keys: list, n: int = 3) -> list:
    """Find the closest matching keys using difflib."""
    if not available_keys:
        return []
    key_upper = key.upper().replace(' ', '_')
    exact = [k for k in available_keys if k.upper() == key_upper]
    if exact:
        return exact
    matches = difflib.get_close_matches(key_upper, [k.upper() for k in available_keys], n=n, cutoff=0.4)
    result = []
    for match in matches:
        for k in available_keys:
            if k.upper() == match and k not in result:
                result.append(k)
    return result


class SecretGet(Tool):
    """
    Retrieves a secret value from the secure secrets store.
    Secrets are stored encrypted and can be scoped globally or per-project.
    
    IMPORTANT: Secrets are masked in logs and chat history for security.
    The actual value is only used during tool execution.
    """

    async def execute(
        self, 
        key: Optional[str] = None, 
        scope: str = "auto",
        **kwargs
    ) -> Response:
        """
        Gets a secret value by key.
        
        Args:
            key (Optional[str]): The unique identifier for the secret (e.g., 'GITHUB_TOKEN'). 
                                If omitted, returns available secret names (masked).
            scope (str): Where to look for the secret:
                - "auto" (default): Check project scope first, then global
                - "project": Only check project-scoped secrets  
                - "global": Only check global secrets
        
        Returns:
            The secret value or an error message if not found.
        """
        try:
            if scope == "auto":
                # Try project first if in a project context
                manager = get_secrets_manager(self.agent.context)
            elif scope == "project":
                project_name = projects.get_context_project_name(self.agent.context)
                if not project_name:
                    return Response(
                        message="Error: No active project. Use scope='global' or activate a project.",
                        break_loop=False
                    )
                manager = get_project_secrets_manager(project_name)
            else:  # global
                manager = get_default_secrets_manager()

            if not key:
                keys = manager.get_keys()
                return Response(
                    message=f"Available secret keys in {scope} scope: {', '.join(keys) if keys else 'None'}. "
                           f"Call secret_get with a specific 'key' to retrieve the value.",
                    break_loop=False
                )

            key = key.upper().strip()
            
            # Use get_secret for unified retrieval (includes external fallback)
            value = manager.get_secret(key)
            
            if value is None:
                # Fuzzy matching: suggest closest keys
                available_keys = list(manager.get_keys()) if hasattr(manager, 'get_keys') else []
                suggestions = fuzzy_suggest_keys(key, available_keys)
                
                hint = f"Secret '{key}' not found in {scope} scope (including environment fallback)."
                if suggestions:
                    hint += f" Did you mean: {', '.join(suggestions)}?"
                elif available_keys:
                    hint += f" Available keys: {', '.join(sorted(available_keys)[:10])}"
                hint += " \n💡 Tip: Use UPPER_SNAKE_CASE keys (e.g. GITHUB_TOKEN). Use request_secret to request from user, or let agents use secret_set."
                return Response(message=hint, break_loop=False)
            
            # Phase 4 (#1185): Reject unresolved template placeholders.
            # If a secret value is literally "{{SECRET_FOO}}", the config is broken
            # and the agent must not use it — it will pollute downstream tools.
            import re
            if value and re.match(r'\{\{.*\}\}', str(value)):
                return Response(
                    message=(
                        f"Error: Secret '{key}' contains an unresolved template placeholder "
                        f"('{value}'). This means the configuration has not been properly set. "
                        f"Use `secret_set` to set a real value, or `request_secret` to ask the user."
                    ),
                    break_loop=False,
                )

            # NOTE: The actual value is returned in the message so the agent can 'read' it.
            # It will be automatically masked in logs and chat history by the StreamingSecretsFilter.
            PrintStyle.hint(f"Retrieved unified secret '{key}' (scope={scope}).")
            return Response(message=f"Secret '{key}' value: {value}", break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error retrieving secret: {e}", break_loop=True)


if __name__ == "__main__":
    pass
