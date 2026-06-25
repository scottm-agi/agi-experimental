from __future__ import annotations
import os
import json
import logging
import re
import ast
from typing import Set, List, Optional, Dict, Any
import threading

logger = logging.getLogger(__name__)

REGISTRY_PATH = "python/tools/registry.json"

class ToolRegistry:
    _instance = None
    _lock = threading.Lock()
    _cache: Set[str] = set()
    _metadata: Dict[str, Dict[str, str]] = {}
    _initialized = False

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = ToolRegistry()
        return cls._instance

    def __init__(self):
        if ToolRegistry._initialized:
            return
        self._load_registry()
        ToolRegistry._initialized = True

    def _get_tool_description(self, file_path: str) -> str:
        """Extract tool class docstring using AST without importing the module."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content.strip():
                    return ""
                tree = ast.parse(content)
                
                # 1. Look for Class definitions first
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        # Use the docstring of the class if it has one
                        doc = ast.get_docstring(node)
                        if doc:
                            return doc.strip()
                
                # 2. Fallback to module level docstring
                return (ast.get_docstring(tree) or "").strip()
        except Exception as e:
            logger.debug(f"Failed to extract docstring from {file_path}: {e}")
        return ""

    def _load_registry(self):
        """Load registry from file and scan core tools directory."""
        # 1. Load from file if exists
        file_tools = set()
        if os.path.exists(REGISTRY_PATH):
            try:
                with open(REGISTRY_PATH, 'r') as f:
                    file_tools = set(json.load(f))
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to read tool registry: {e}")

        # 2. Scan core tools directory
        core_tools = set()
        tools_dirs = ["python/tools"]
        
        # Scan agents directory for profile-specific tools
        agents_dir = "agents"
        if os.path.exists(agents_dir):
            for profile in os.listdir(agents_dir):
                profile_tools_dir = os.path.join(agents_dir, profile, "tools")
                if os.path.isdir(profile_tools_dir):
                    tools_dirs.append(profile_tools_dir)

        self._metadata = {}
        for d in tools_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if f.endswith(".py") and not f.startswith("__"):
                        name = f[:-3]
                        core_tools.add(name)
                        # Extract metadata
                        full_path = os.path.join(d, f)
                        desc = self._get_tool_description(full_path)
                        if desc:
                            self._metadata[name] = {"description": desc}
        
        # Merge: If a tool exists as a file, it's "known"
        self._cache = file_tools.union(core_tools)
        
        # Save if there were new discoveries from scanning
        if self._cache != file_tools:
            self._save_registry()

    def _save_registry(self):
        """Save cache to file."""
        try:
            os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
            with open(REGISTRY_PATH, 'w') as f:
                json.dump(list(self._cache), f)
        except IOError as e:
            logger.error(f"Failed to update tool registry: {e}")

    def get_known_tools(self) -> Set[str]:
        """Get the set of known tool names from the registry."""
        with self._lock:
            return self._cache.copy()

    def get_tool_metadata(self, name: str) -> Optional[Dict[str, str]]:
        """Get metadata for a tool."""
        with self._lock:
            return self._metadata.get(name)

    def register_tool(self, name: str) -> bool:
        """
        Register a tool name.
        Returns True if it's a new tool (not in registry.json), False otherwise.
        Raises ValueError if the name is invalid.
        """
        valid, error = self.validate_tool_name(name)
        if not valid:
            raise ValueError(f"Invalid tool name '{name}': {error}")

        with self._lock:
            if name not in self._cache:
                self._cache.add(name)
                # If it's new, we should try to refresh metadata if it's a file
                # But usually register_tool is called AFTER the file is written
                self._save_registry()
                return True # New tool discovered or created
            return False # Already known

    def validate_tool_name(self, name: str) -> tuple[bool, Optional[str]]:
        """Validates tool name for safety and conventions."""
        if not name:
            return False, "Name cannot be empty"
        if len(name) > 64:
            return False, "Name too long (max 64 chars)"
        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            return False, "Name must start with a letter and contain only lowercase alphanumeric characters and underscores"
        
        # Reserved names or patterns
        reserved = ["unknown", "registry", "__init__"]
        if name in reserved:
            return False, f"'{name}' is a reserved name"
            
        return True, None

    def find_similar_tools(self, name: str, threshold: float = 0.6) -> List[Dict[str, Any]]:
        """
        Finds tools with similar names or purposes. 
        Returns list of dicts with name, ratio, and description.
        """
        from difflib import SequenceMatcher
        
        similar = []
        known_tools = self.get_known_tools()
        
        for known in known_tools:
            name_ratio = SequenceMatcher(None, name, known).ratio()
            
            # Basic keyword overlap check if we have descriptions
            desc_ratio = 0.0
            metadata = self._metadata.get(known, {})
            desc = metadata.get("description", "")
            
            best_ratio = name_ratio
            if name_ratio >= threshold:
                similar.append({
                    "name": known,
                    "ratio": name_ratio,
                    "description": desc,
                    "match_type": "name"
                })
                
        return sorted(similar, key=lambda x: x["ratio"], reverse=True)

# Global instance methods for backward compatibility
def get_known_tools() -> Set[str]:
    return ToolRegistry.get_instance().get_known_tools()

def register_tool(name: str) -> bool:
    return ToolRegistry.get_instance().register_tool(name)

def get_tool_metadata(name: str) -> Optional[Dict[str, str]]:
    return ToolRegistry.get_instance().get_tool_metadata(name)

def find_similar_tools(name: str, threshold: float = 0.6) -> List[Dict[str, Any]]:
    return ToolRegistry.get_instance().find_similar_tools(name, threshold)

def get_tool(name: str) -> Optional[Dict[str, Any]]:
    """
    Standardized tool lookup facade. 
    Returns metadata for now to satisfy existing calls.
    """
    return get_tool_metadata(name)
