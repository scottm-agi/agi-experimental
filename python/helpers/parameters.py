from __future__ import annotations
import os
import json
import hashlib
import threading
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from python.helpers import files
from python.helpers import config_db

if TYPE_CHECKING:
    from python.agent import AgentContext

DEFAULT_PARAMETERS_FILE = "tmp/parameters.json"

class ParametersManager:
    """
    Manages deterministic parameters stored in SQLite database.
    Falls back to JSON files for migration.
    """
    
    _instances: Dict[Tuple[str, ...], "ParametersManager"] = {}
    
    @classmethod
    def get_instance(cls, *parameter_files: str) -> "ParametersManager":
        if not parameter_files:
            parameter_files = (DEFAULT_PARAMETERS_FILE,)
        key = tuple(parameter_files)
        if key not in cls._instances:
            cls._instances[key] = cls(*parameter_files)
        return cls._instances[key]

    def __init__(self, *files_list: str):
        self._lock = threading.RLock()
        self._files: Tuple[str, ...] = tuple(files_list) if files_list else (DEFAULT_PARAMETERS_FILE,)
        self._cache: Optional[Dict[str, Any]] = None

    def _get_scope_from_path(self, path: str) -> str:
        """Determine scope from a specific file path."""
        if path == DEFAULT_PARAMETERS_FILE or (os.path.basename(path) == "parameters.json" and "tmp" not in path and ".agix.proj" not in path and ".agix.proj" not in path):
            return "global"
        
        # Project-specific: extracts project name (supports both .agix.proj and legacy .agix.proj)
        for meta_dir in (".agix.proj", ".agix.proj"):
            if meta_dir in path:
                parts = path.split(meta_dir)
                if len(parts) > 0:
                    project_path = parts[0].rstrip("/")
                    return os.path.basename(project_path) or "global"
        
        # Chat-specific: extracts chat ID
        # Expected path: .../tmp/chats/{chat_id}/parameters.json
        if "tmp/chats" in path:
            norm_path = path.replace("\\", "/")
            parts = norm_path.split("/")
            # Find the part after 'chats'
            try:
                chats_idx = parts.index("chats")
                if len(parts) > chats_idx + 1:
                    return parts[chats_idx + 1]
            except ValueError:
                pass
                
        # Unknown path: generate a deterministic scope from the path hash
        # to prevent test/unknown paths from polluting the real global scope.
        # Empty paths still fall through to "global" for backward compatibility.
        if path:
            return f"_path_{hashlib.md5(path.encode()).hexdigest()[:12]}"
        return "global"

    def _get_scope(self) -> str:
        """Determine primarily active scope (first in list)."""
        if not self._files:
            return "global"
        return self._get_scope_from_path(self._files[0])

    def load_parameters(self) -> Dict[str, Any]:
        """
        Loads parameters from DB (with file fallback for migration) across all configured scopes.
        Higher priority scopes override lower ones.
        """
        with self._lock:
            if self._cache is not None:
                return self._cache
            
            final_params: Dict[str, Any] = {}
            
            # Iterate through files in reverse order (lowest priority first)
            for path in reversed(self._files):
                scope = self._get_scope_from_path(path)
                
                # Try DB first
                scope_params = config_db.get_parameters(scope)
                
                if not scope_params:
                    # Fallback to file for migration
                    try:
                        if os.path.exists(path):
                            content = files.read_file(path)
                            data = json.loads(content)
                            if isinstance(data, dict):
                                scope_params = data
                                # Migrate to DB
                                config_db.set_parameters(scope_params, scope)
                    except Exception:
                        scope_params = {}
                
                if scope_params:
                    final_params.update(scope_params)
            
            self._cache = final_params
            return final_params

    def set_parameter(self, key: str, value: Any):
        """
        Sets a parameter in the database.
        Updates the cache immediately.
        """
        with self._lock:
            scope = self._get_scope()
            config_db.set_parameter(key, value, scope)
            # Update cache
            if self._cache is not None:
                self._cache[key] = value

    def save_parameters(self, data: Dict[str, Any]):
        """
        Saves all parameters atomically to the primary scope.
        """
        if not data:
            return  # Don't save empty data
            
        scope = self._get_scope()
        
        with self._lock:
            # Save to DB atomically
            config_db.set_parameters(data, scope, replace=True)
            
            # Also write to file for backward compatibility if it's the primary scope
            if self._files:
                primary_file = self._files[0]
                os.makedirs(os.path.dirname(primary_file), exist_ok=True)
                files.write_file(primary_file, json.dumps(data, indent=4))
            
            # Update cache
            self._cache = data.copy()

    def get_parameter(self, key: str, default: Any = None) -> Any:
        """
        Retrieves a parameter by key with copy-on-first-access from global.
        
        If the key is NOT in the primary scope but IS in a lower-priority scope (global),
        it will be copied to the primary scope automatically. This ensures projects
        inherit global values once but then operate independently.
        """
        with self._lock:
            primary_scope = self._get_scope()
            
            # Check primary scope first (project or chat)
            primary_value = config_db.get_parameter(key, primary_scope)
            
            if primary_value is not None:
                return primary_value
            
            # Not in primary scope - check if we have multiple scopes (fallback chain)
            if len(self._files) > 1:
                # Check lower-priority scopes (e.g., global)
                for path in self._files[1:]:  # Skip primary (index 0)
                    fallback_scope = self._get_scope_from_path(path)
                    fallback_value = config_db.get_parameter(key, fallback_scope)
                    
                    if fallback_value is not None:
                        # Copy-on-first-access: copy to primary scope
                        config_db.set_parameter(key, fallback_value, primary_scope)
                        
                        # Update cache if exists
                        if self._cache is not None:
                            self._cache[key] = fallback_value
                        
                        # Log the copy operation
                        from python.helpers.print_style import PrintStyle
                        PrintStyle(font_color="cyan").print(
                            f"[PARAM INIT] Copied '{key}' from {fallback_scope} -> {primary_scope}"
                        )
                        
                        return fallback_value
            
            return default

    def clear_cache(self):
        with self._lock:
            self._cache = None


def get_parameters_manager(context: "AgentContext|None" = None) -> ParametersManager:
    from python.helpers import projects
    
    # Base global parameters
    parameter_files = [DEFAULT_PARAMETERS_FILE]
    
    if not context:
        from python.agent import AgentContext
        context = AgentContext.current()
    elif isinstance(context, str):
        from python.agent import AgentContext
        context = AgentContext.get(context)
        
    if context:
        chat_id = context if isinstance(context, str) else context.id
        from python.helpers import projects
        project_name = projects.get_context_project_name(context)
        if project_name:
            # Project parameters override global ones
            project_file = files.get_abs_path(projects.get_project_meta_folder(project_name), "parameters.json")
            parameter_files.insert(0, project_file)
            
        # Chat parameters override everything
        if chat_id:
            chat_file = files.get_abs_path("tmp/chats", chat_id, "parameters.json")
            if chat_file not in parameter_files:
                parameter_files.insert(0, chat_file)
            
    return ParametersManager.get_instance(*parameter_files)

def get_global_parameters_manager() -> ParametersManager:
    """
    DEPRECATED: Global scope has been removed to prevent state confusion.
    
    This function now logs a deprecation warning and returns a manager
    that will use the DEFAULT_PARAMETERS_FILE for backward compatibility,
    but all new code should use get_parameters_manager(context) instead.
    """
    from python.helpers.print_style import PrintStyle
    PrintStyle(font_color="orange", bold=True).print(
        "[DEPRECATION WARNING] get_global_parameters_manager() is deprecated. "
        "Use get_parameters_manager(context) for project-scoped parameters. "
        "Global scope has been removed to prevent state confusion between tasks."
    )
    # Return a manager for the default file for backward compatibility
    # but callers should migrate to project-scoped parameters
    return ParametersManager.get_instance(DEFAULT_PARAMETERS_FILE)

def get_project_parameters_manager(project_name: str, merge_with_global: bool = False) -> ParametersManager:
    from python.helpers import projects
    project_file = files.get_abs_path(projects.get_project_meta_folder(project_name), "parameters.json")
    
    parameter_files = [project_file]
    if merge_with_global:
        parameter_files.append(DEFAULT_PARAMETERS_FILE)
        
    return ParametersManager.get_instance(*parameter_files)
