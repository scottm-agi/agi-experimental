from __future__ import annotations
from python.helpers.tool import Tool, Response
from python.helpers.parameters import get_parameters_manager, get_project_parameters_manager
from python.helpers import projects
from python.helpers.print_style import PrintStyle
import json
import difflib
from typing import Union, List


def fuzzy_suggest(key: str, available_keys: list, n: int = 3) -> list:
    """Find the closest matching keys using difflib."""
    if not available_keys:
        return []
    # Try case-insensitive matching first
    key_upper = key.upper().replace(' ', '_')
    exact_upper = [k for k in available_keys if k.upper() == key_upper]
    if exact_upper:
        return exact_upper
    # Fuzzy match
    matches = difflib.get_close_matches(key_upper, [k.upper() for k in available_keys], n=n, cutoff=0.4)
    # Map back to original case
    result = []
    for match in matches:
        for k in available_keys:
            if k.upper() == match and k not in result:
                result.append(k)
    return result



class ParameterGet(Tool):
    """
    Retrieves a deterministic parameter or all parameters from the JSON store.
    
    Supports both project-scoped and global parameters:
    - **auto** (default): Check project first, then global (most useful)
    - **project**: Only check current project
    - **global**: Only check global scope
    
    Project-scoped parameters always take precedence over global ones.
    """

    async def execute(self, key: str = None, keys: List[str] = None, scope: str = "auto", **kwargs) -> Response:
        """
        Gets parameter(s) with scope resolution.
        
        Args:
            key (str, optional): The key to retrieve. If omitted, returns all parameters.
            keys (list, optional): Multiple keys to retrieve at once.
            scope (str): Where to look: "auto" (default), "project", or "global".
                        "auto" checks project first, then global.
        """
        try:
            project_name = projects.get_context_project_name(self.agent.context)
            
            if scope == "auto":
                # Project first, then global fallback
                proj_manager = None
                global_manager = get_parameters_manager(self.agent.context)
                
                if project_name:
                    proj_manager = get_project_parameters_manager(project_name)
                
                if key:
                    key = str(key).strip()
                    # Check project first
                    value = None
                    found_scope = "global"
                    if proj_manager:
                        value = proj_manager.get_parameter(key)
                        if value is not None:
                            found_scope = f"project '{project_name}'"
                    # Fallback to global
                    if value is None:
                        value = global_manager.get_parameter(key)
                        found_scope = "global"
                    
                    if value is None:
                        # Fuzzy matching: gather all available keys
                        all_keys = list(global_manager.load_parameters().keys())
                        if proj_manager:
                            all_keys += list(proj_manager.load_parameters().keys())
                        all_keys = list(set(all_keys))  # deduplicate
                        suggestions = fuzzy_suggest(key, all_keys)
                        
                        hint = f"Parameter '{key}' not found in any scope."
                        if suggestions:
                            hint += f" Did you mean: {', '.join(suggestions)}?"
                        else:
                            available = ', '.join(sorted(all_keys)[:10]) if all_keys else 'none'
                            hint += f" Available keys: {available}"
                        hint += " \n💡 Tip: Use UPPER_SNAKE_CASE keys (e.g. MAX_RETRIES). Let agents set values via parameter_set for consistency."
                        return Response(message=hint, break_loop=False)
                    return Response(
                        message=f"{key}: {value} (scope: {found_scope})",
                        break_loop=False
                    )
                
                if keys and isinstance(keys, list):
                    results = {}
                    for k in keys:
                        k_str = str(k).strip()
                        value = None
                        if proj_manager:
                            value = proj_manager.get_parameter(k_str)
                        if value is None:
                            value = global_manager.get_parameter(k_str)
                        results[k_str] = value
                    return Response(message=json.dumps(results, indent=2), break_loop=False)
                
                # All parameters — merge global + project (project wins)
                all_params = global_manager.load_parameters()
                if proj_manager:
                    proj_params = proj_manager.load_parameters()
                    all_params.update(proj_params)  # Project overrides global
                return Response(message=json.dumps(all_params, indent=4), break_loop=False)
                
            elif scope == "project":
                if not project_name:
                    return Response(
                        message="Error: No active project. Use scope='auto' or scope='global'.",
                        break_loop=False
                    )
                manager = get_project_parameters_manager(project_name)
                scope_display = f"project '{project_name}'"
                
            else:  # global
                manager = get_parameters_manager(self.agent.context)
                scope_display = "global"
            
            # Handle single/multi key for explicit scope
            if keys and isinstance(keys, list):
                results = {}
                for k in keys:
                    k_str = str(k).strip()
                    results[k_str] = manager.get_parameter(k_str)
                return Response(message=json.dumps(results, indent=2), break_loop=False)
            
            if key:
                if isinstance(key, list):
                    results = {}
                    for k in key:
                        k_str = str(k).strip()
                        results[k_str] = manager.get_parameter(k_str)
                    return Response(message=json.dumps(results, indent=2), break_loop=False)
                    
                key_str = str(key).strip()
                value = manager.get_parameter(key_str)
                if value is None:
                    # Fuzzy matching for explicit scope
                    all_keys = list(manager.load_parameters().keys())
                    suggestions = fuzzy_suggest(key_str, all_keys)
                    
                    hint = f"Parameter '{key_str}' not found in {scope_display} scope."
                    if suggestions:
                        hint += f" Did you mean: {', '.join(suggestions)}?"
                    else:
                        available = ', '.join(sorted(all_keys)[:10]) if all_keys else 'none'
                        hint += f" Available keys: {available}"
                    hint += " \n💡 Tip: Use UPPER_SNAKE_CASE keys."
                    return Response(message=hint, break_loop=False)
                return Response(message=f"{key_str}: {value}", break_loop=False)
            else:
                params = manager.load_parameters()
                return Response(message=json.dumps(params, indent=4), break_loop=False)
                
        except Exception as e:
            return Response(message=f"Error getting parameter: {e}", break_loop=True)

if __name__ == "__main__":
    pass
