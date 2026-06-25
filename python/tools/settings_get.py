from __future__ import annotations
from typing import Optional, List, Any
import json
from python.helpers.tool import Tool, Response
from python.helpers.settings import get_settings, PASSWORD_PLACEHOLDER

class SettingsGet(Tool):
    """
    Retrieves global configuration settings from settings.json.
    Settings include model providers, model names, UI preferences, and system-wide defaults.
    """

    async def execute(self, key: Optional[str] = None, keys: Optional[List[str]] = None, **kwargs) -> Response:
        """
        Gets one or more settings.
        
        Args:
            key (str, optional): A specific setting key to retrieve (e.g., 'chat_model_provider').
                               If omitted, returns all settings.
            keys (list, optional): Multiple keys to retrieve at once.
        
        Returns:
            The setting value(s) or all settings.
        """
        try:
            settings = get_settings()
            
            # Sensitive data that should be filtered out
            SENSITIVE_KEYS = ["auth_login", "auth_password", "root_password", "rfc_password", "mcp_server_token"]
            
            def filter_sensitive(data: dict) -> dict:
                return {k: (PASSWORD_PLACEHOLDER if k in SENSITIVE_KEYS else v) for k, v in data.items()}

            if keys:
                results = {}
                for k in keys:
                    if k in settings:
                        results[k] = PASSWORD_PLACEHOLDER if k in SENSITIVE_KEYS else settings[k]
                    else:
                        results[k] = None
                return Response(message=json.dumps(results, indent=2), break_loop=False)
            
            if key:
                if key in settings:
                    value = PASSWORD_PLACEHOLDER if key in SENSITIVE_KEYS else settings[key]
                    return Response(message=f"{key}: {json.dumps(value)}", break_loop=False)
                else:
                    return Response(message=f"Setting '{key}' not found.", break_loop=False)
            
            # Return all (filtered)
            filtered = filter_sensitive(settings)
            return Response(message=json.dumps(filtered, indent=2), break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error retrieving settings: {e}", break_loop=True)

if __name__ == "__main__":
    pass
