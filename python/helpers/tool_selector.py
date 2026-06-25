from __future__ import annotations
import json
import os
import logging
from typing import List, Dict, Any, Optional, Set
from python.helpers import files

logger = logging.getLogger(__name__)

ONTOLOGY_PATH = "python/tools/ontology.json"

class ToolSelector:
    _instance = None
    _ontology: Dict[str, Any] = {}
    _initialized = False
    _last_mtime: float = 0.0  # RCA-1 (2026-04-25): mtime-based cache invalidation

    @classmethod
    def get_instance(cls) -> ToolSelector:
        if not cls._instance:
            cls._instance = ToolSelector()
        return cls._instance

    def __init__(self):
        if ToolSelector._initialized:
            return
        self._load_ontology()
        ToolSelector._initialized = True

    def _maybe_reload(self):
        """Check if ontology.json was modified since last load; reload if so.
        
        RCA-1 (2026-04-25): The original Singleton loaded ontology once and
        cached forever. Changes made during runtime (e.g., category splits,
        profile updates) were invisible until process restart. This mtime
        check adds ~1 stat() call per tool check — negligible cost.
        """
        abs_path = files.get_abs_path(ONTOLOGY_PATH)
        try:
            current_mtime = os.path.getmtime(abs_path) if os.path.exists(abs_path) else 0.0
        except OSError:
            current_mtime = 0.0
        
        if current_mtime != self._last_mtime:
            logger.info(
                f"Ontology file changed (mtime {self._last_mtime} → {current_mtime}), reloading"
            )
            self._load_ontology()

    def _load_ontology(self):
        """Load the tool ontology from ontology.json."""
        abs_path = files.get_abs_path(ONTOLOGY_PATH)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    self._ontology = json.load(f)
                # Track mtime for invalidation
                try:
                    ToolSelector._last_mtime = os.path.getmtime(abs_path)
                except OSError:
                    ToolSelector._last_mtime = 0.0
            except Exception as e:
                logger.error(f"Failed to load tool ontology from {abs_path}: {e}")
                self._ontology = {"categories": {}, "profiles": {}}
        else:
            logger.warning(f"Ontology file not found at {abs_path}. Using empty ontology.")
            self._ontology = {"categories": {}, "profiles": {}}
            ToolSelector._last_mtime = 0.0

    def get_allowed_tools(self, profile: str = "default") -> Set[str]:
        """
        Get the set of allowed tool names for a given profile.
        If profile is not found, it falls back to 'default'.
        If 'default' is not found, it returns an empty set (or could return all).
        """
        self._maybe_reload()
        profiles = self._ontology.get("profiles", {})
        categories = self._ontology.get("categories", {})
        
        allowed_categories = profiles.get(profile)
        if allowed_categories is None and profile != "default":
            allowed_categories = profiles.get("default")
            
        if not allowed_categories:
            return set()
            
        allowed_tools = set()
        for cat in allowed_categories:
            cat_tools = categories.get(cat, [])
            allowed_tools.update(cat_tools)
            
        return allowed_tools

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize a tool name for matching.

        RCA Iteration 158, Issue D: MCP tool names use hyphens
        (e.g., 'sequential-thinking') but the ontology uses underscores
        ('sequential_thinking'). This caused false PROFILE_ENFORCEMENT
        blocks on frontend and code profiles.

        Fix: Replace hyphens with underscores before matching.
        """
        return name.replace("-", "_")

    def filter_tools(self, tool_names: List[str], profile: str = "default") -> List[str]:
        """Filter a list of tool names based on the profile."""
        allowed = self.get_allowed_tools(profile)
        if not allowed:
             # If no ontology/profile defined, allow all as fallback to prevent breakage
             return tool_names

        # Normalize both sides for matching (RCA Iteration 158, Fix F2)
        allowed_norm = {self._normalize(t) for t in allowed}
        return [
            t for t in tool_names
            if self._normalize(t) in allowed_norm
            or self._normalize(t.split(".")[-1]) in allowed_norm
        ]

    def should_include_tool(self, tool_name: str, profile: str = "default") -> bool:
        """Check if a specific tool should be included for the profile."""
        # _maybe_reload is called via get_allowed_tools
        allowed = self.get_allowed_tools(profile)
        if not allowed:
            return True # Fallback

        # Build normalized allowed set for matching (RCA Iteration 158, Fix F2)
        allowed_norm = {self._normalize(t) for t in allowed}

        # Check for exact name, basename (for MCP tools), or server name prefix (Issue #645)
        parts = tool_name.split(".")
        basename = parts[-1]
        server_name = parts[0] if len(parts) > 1 else None

        # Normalize the query names
        norm_name = self._normalize(tool_name)
        norm_base = self._normalize(basename)
        norm_server = self._normalize(server_name) if server_name else None

        # 1. Exact match (normalized)
        if norm_name in allowed_norm or norm_base in allowed_norm:
            return True

        # 2. Server name match (allows all tools from an allowed MCP server)
        if norm_server and norm_server in allowed_norm:
            return True

        # 3. Prefix match for grouped tools (e.g. google_chat -> google_chat_list_spaces)
        for pattern in allowed_norm:
            if norm_base.startswith(pattern + "_"):
                return True

        return False

    @staticmethod
    def is_natural_only(agent_config: Any) -> bool:
        """Helper to check if 'Natural-only' mode is active."""
        # This is a placeholder for the actual logic to detect Natural-only mode
        # which might come from agent.config or a specific flag.
        return getattr(agent_config, "natural_only", False)
