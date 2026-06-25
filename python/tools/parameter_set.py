from __future__ import annotations
from typing import Any
import re
from python.helpers.tool import Tool, Response
from python.helpers.parameters import get_parameters_manager, get_project_parameters_manager
from python.helpers import projects
from python.helpers.print_style import PrintStyle

# Patterns that indicate a value should be stored as a secret, not a parameter
SECRET_KEY_PATTERNS = [
    r'.*api[_-]?key.*',
    r'.*secret.*',
    r'.*token.*',
    r'.*password.*',
    r'.*passwd.*',
    r'.*credential.*',
    r'.*auth.*key.*',
    r'.*private[_-]?key.*',
    r'.*access[_-]?key.*',
    r'.*bearer.*',
    r'.*oauth.*',
]

# Valid key naming pattern: UPPER_SNAKE_CASE with optional hyphens for project prefix
# e.g., BITDRAMA-GITHUB-REPO, MAX_RETRIES, FORGEJO_URL
VALID_KEY_PATTERN = re.compile(r'^[A-Z][A-Z0-9]*([_-][A-Z0-9]+)*$')

def is_secret_key(key: str) -> bool:
    """Check if a key name suggests it should be stored as a secret."""
    key_lower = key.lower()
    for pattern in SECRET_KEY_PATTERNS:
        if re.match(pattern, key_lower):
            return True
    return False

def normalize_key(key: str) -> str:
    """Normalize a key to UPPER_SNAKE_CASE with hyphens for project prefixes.
    
    Examples:
        'my_setting' -> 'MY_SETTING'
        'bitdrama-github-repo' -> 'BITDRAMA-GITHUB-REPO'
        'max retries' -> 'MAX_RETRIES'
    """
    key = key.strip().upper()
    # Replace spaces with underscores
    key = key.replace(' ', '_')
    # Collapse multiple consecutive underscores
    key = re.sub(r'_{2,}', '_', key)
    # Collapse multiple consecutive hyphens
    key = re.sub(r'-{2,}', '-', key)
    return key


class ParameterSet(Tool):
    """
    Sets or updates a deterministic parameter in the JSON parameter store.
    Use this for reliable configuration, state management, or key-value storage.
    
    Supports both project-scoped and global parameters:
    - **project** (default): Stored per-project, takes precedence over global
    - **global**: Shared across all projects
    
    Deduplication: If setting a global key that already exists at the project level,
    the tool will warn and prefer the project-level value.
    
    Key Naming Convention:
    - Use UPPER_SNAKE_CASE: `MAX_RETRIES`, `FORGEJO_URL`
    - For project-specific keys, prefix with project name: `BITDRAMA-GITHUB-REPO`
    - Structure: `<PROJECT>-<SERVICE>-<TYPE>` for clarity
    
    IMPORTANT: Secrets (API keys, tokens, passwords) MUST use secret_set instead.
    """

    async def execute(self, key: str = None, value: Any = None, scope: str = "project", **kwargs) -> Response:
        """
        Sets a parameter value with verification.
        
        Args:
            key (str): The unique identifier for the parameter. Auto-normalized to UPPER_SNAKE_CASE.
            value (Any): The value to store (must be JSON serializable).
            scope (str): Where to store: "project" (default) or "global".
        """
        try:
            # Defensive: handle missing key gracefully instead of crashing
            if key is None:
                return Response(
                    message="Error: Missing required 'key' argument. "
                           "parameter_set requires a 'key' and 'value'. "
                           "Call once per parameter: parameter_set(key=\"MY_KEY\", value=\"my_value\"). "
                           "Do NOT pass a dict of multiple keys as value — call separately for each.",
                    break_loop=False
                )
            
            if value is None:
                return Response(
                    message=f"Error: Missing required 'value' argument for key '{key}'.",
                    break_loop=False
                )
            
            # Normalize key
            original_key = key
            key = normalize_key(key)
            
            if not key:
                return Response(message="Error: Key cannot be empty.", break_loop=False)
            
            # Validate key format
            if not VALID_KEY_PATTERN.match(key):
                return Response(
                    message=f"Error: Key '{key}' does not follow naming convention. "
                           f"Use UPPER_SNAKE_CASE with optional hyphens for prefixes. "
                           f"Examples: MAX_RETRIES, BITDRAMA-GITHUB-REPO, FORGEJO_URL",
                    break_loop=False
                )
            
            # Reject secret-like keys
            if is_secret_key(key):
                return Response(
                    message=f"ERROR: '{key}' appears to be a secret (matches secret pattern). "
                           f"Use secret_set(key='{key}', value='<value>') instead.",
                    break_loop=False
                )
            
            # Normalization notice
            norm_msg = ""
            if key != original_key.strip():
                norm_msg = f"(key normalized: '{original_key.strip()}' → '{key}') "
            
            project_name = projects.get_context_project_name(self.agent.context)
            
            if scope == "global":
                manager = get_parameters_manager(self.agent.context)
                scope_display = "global"
                
                # Deduplication check: warn if project already has this key
                if project_name:
                    proj_manager = get_project_parameters_manager(project_name)
                    proj_value = proj_manager.get_parameter(key)
                    if proj_value is not None:
                        dedup_msg = (
                            f"⚠️ DEDUP WARNING: '{key}' already exists at project level "
                            f"(project '{project_name}', value='{proj_value}'). "
                            f"Project-level takes precedence over global. "
                            f"Consider removing the global value or updating the project value instead."
                        )
                        PrintStyle(font_color="orange", bold=True).print(dedup_msg)
                        # Still proceed to set globally, but include warning
                        norm_msg += dedup_msg + " "
            else:
                # Project scope
                if not project_name:
                    # No project active — fall back to global with a notice
                    manager = get_parameters_manager(self.agent.context)
                    scope_display = "global (no active project)"
                    norm_msg += "⚠️ No active project — storing in global scope. "
                else:
                    manager = get_project_parameters_manager(project_name)
                    scope_display = f"project '{project_name}'"
                    
                    # Deduplication: if global also has this key, inform
                    global_manager = get_parameters_manager(self.agent.context)
                    global_value = global_manager.get_parameter(key)
                    if global_value is not None:
                        norm_msg += (
                            f"ℹ️ Note: '{key}' also exists globally (value='{global_value}'). "
                            f"Project value takes precedence. "
                        )
            
            # Set the parameter
            manager.set_parameter(key, value)
            
            # POST-SET VERIFICATION: Read back and confirm
            readback = manager.get_parameter(key)
            if readback != value:
                error_msg = (
                    f"⚠️ VERIFICATION FAILED: Set '{key}'='{value}' but readback got '{readback}'. "
                    f"Parameter may not have persisted correctly."
                )
                PrintStyle(font_color="red", bold=True).print(error_msg)
                return Response(message=error_msg, break_loop=False)
            
            message = f"{norm_msg}✅ Set parameter '{key}' = '{value}' in {scope_display} scope. [Verified]"
            PrintStyle.hint(message)
            return Response(message=message, break_loop=False)
            
        except Exception as e:
            return Response(message=f"Error setting parameter: {e}", break_loop=True)

if __name__ == "__main__":
    pass
