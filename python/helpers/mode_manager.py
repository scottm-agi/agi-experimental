from __future__ import annotations
"""
MultiAgentDev Mode Manager

Manages agent modes similar to RooCode's mode system.
Handles mode loading, switching, validation, and application to agents.
"""

import os
import fnmatch
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from pathlib import Path

import yaml

from python.helpers import files
from python.helpers.print_style import PrintStyle


@dataclass
class SupervisorSettings:
    """Mode-specific supervisor settings."""
    max_iterations_without_progress: int = 5
    max_consecutive_tool_failures: int = 2
    response_loop_threshold: int = 3
    context_warning_threshold: float = 0.76


@dataclass
class ModeRestrictions:
    """Tool restrictions for a mode."""
    edit: Optional[Dict[str, Any]] = None
    
    def get_allowed_file_patterns(self) -> List[str]:
        """Get file patterns allowed for editing."""
        if self.edit and "file_patterns" in self.edit:
            return self.edit["file_patterns"]
        return ["*"]  # All files allowed by default
    
    def is_file_allowed(self, filepath: str) -> bool:
        """Check if a file is allowed for editing in this mode."""
        patterns = self.get_allowed_file_patterns()
        filename = os.path.basename(filepath)
        return any(fnmatch.fnmatch(filename, pattern) for pattern in patterns)


@dataclass
class ModeConfig:
    """Configuration for a single mode."""
    slug: str
    name: str
    display_name: str
    description: str
    role_definition: str
    tool_groups: List[str]
    custom_instructions: str = ""
    model: Optional[str] = None
    restrictions: Optional[ModeRestrictions] = None
    supervisor_settings: Optional[SupervisorSettings] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModeConfig":
        """Create ModeConfig from dictionary."""
        restrictions = None
        if "restrictions" in data:
            restrictions = ModeRestrictions(
                edit=data["restrictions"].get("edit")
            )
        
        supervisor_settings = None
        if "supervisor_settings" in data:
            ss = data["supervisor_settings"]
            supervisor_settings = SupervisorSettings(
                max_iterations_without_progress=ss.get("max_iterations_without_progress", 5),
                max_consecutive_tool_failures=ss.get("max_consecutive_tool_failures", 2),
                response_loop_threshold=ss.get("response_loop_threshold", 3),
                context_warning_threshold=ss.get("context_warning_threshold", 0.76),
            )
        
        return cls(
            slug=data["slug"],
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            description=data.get("description", ""),
            role_definition=data.get("role_definition", ""),
            tool_groups=data.get("tool_groups", []),
            custom_instructions=data.get("custom_instructions", ""),
            model=data.get("model"),
            restrictions=restrictions,
            supervisor_settings=supervisor_settings,
        )


@dataclass
class ModeTransitions:
    """Mode transition rules."""
    allowed: Dict[str, List[str]] = field(default_factory=dict)
    auto_suggest: Dict[str, List[str]] = field(default_factory=dict)
    
    def can_transition(self, from_mode: str, to_mode: str) -> bool:
        """Check if transition from one mode to another is allowed."""
        if from_mode not in self.allowed:
            return True  # No restrictions defined
        return to_mode in self.allowed[from_mode]
    
    def suggest_mode(self, task_text: str) -> Optional[str]:
        """Suggest a mode based on task text patterns."""
        task_lower = task_text.lower()
        for mode, patterns in self.auto_suggest.items():
            for pattern in patterns:
                if pattern.lower() in task_lower:
                    return mode
        return None


class ModeManager:
    """
    Manages MultiAgentDev modes.
    
    Singleton pattern - use ModeManager.get_instance() to get the manager.
    """
    
    _instance: Optional["ModeManager"] = None
    CONFIG_FILE = "conf/multiagentdev_modes.yaml"
    
    def __init__(self):
        self.modes: Dict[str, ModeConfig] = {}
        self.default_mode: str = "code"
        self.model_mappings: Dict[str, str] = {}
        self.transitions: ModeTransitions = ModeTransitions()
        self._current_mode: str = "code"
        self._loaded = False
    
    @classmethod
    def get_instance(cls) -> "ModeManager":
        """Get singleton instance of ModeManager."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load_config()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (for testing)."""
        cls._instance = None
    
    def load_config(self, config_path: Optional[str] = None) -> bool:
        """
        Load mode configuration from YAML file.
        
        Args:
            config_path: Optional path to config file. Uses default if not provided.
            
        Returns:
            True if loaded successfully, False otherwise.
        """
        if config_path is None:
            config_path = files.get_abs_path(self.CONFIG_FILE)
        
        try:
            if not os.path.exists(config_path):
                PrintStyle(
                    font_color="yellow",
                    padding=True
                ).print(f"Mode config not found at {config_path}, using defaults")
                self._create_default_modes()
                return False
            
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            
            # Load default mode
            self.default_mode = config.get("default_mode", "code")
            self._current_mode = self.default_mode
            
            # Load modes
            self.modes = {}
            for slug, mode_data in config.get("modes", {}).items():
                mode_data["slug"] = slug  # Ensure slug is set
                self.modes[slug] = ModeConfig.from_dict(mode_data)
            
            # Load transitions
            transitions_data = config.get("transitions", {})
            self.transitions = ModeTransitions(
                allowed=transitions_data.get("allowed", {}),
                auto_suggest=transitions_data.get("auto_suggest", {}),
            )
            
            # Load global model mappings if any
            self.model_mappings = config.get("model_mappings", {})
            
            # Apply global mappings to modes that don't have an explicit model
            for slug, mode in self.modes.items():
                if not mode.model and slug in self.model_mappings:
                    mode.model = self.model_mappings[slug]
            
            self._loaded = True
            PrintStyle(
                font_color="green",
                padding=True
            ).print(f"Loaded {len(self.modes)} modes from {config_path}")
            
            return True
            
        except Exception as e:
            PrintStyle(
                font_color="red",
                padding=True
            ).print(f"Error loading mode config: {e}")
            self._create_default_modes()
            return False
            
    def get_model_for_mode(self, mode_slug: str, agent_config: Optional[Any] = None) -> Optional[str]:
        """
        Determine the best model for a given mode based on priority:
        1. Explicit user override in settings.json (multiagentdev_overrides)
        2. Role configuration from settings.json (set by templates like Max Performance or user UI config)
        3. Mode-specific model mapping in multiagentdev_modes.yaml (fallback/defaults)
        4. Global model mapping in multiagentdev_modes.yaml
        5. Current agent default model
        
        IMPORTANT: role_configurations in settings.json is the PRIMARY dynamic source.
        Templates (e.g. Max Performance) and the UI agent profile editor both update
        role_configurations, so this must be checked BEFORE the static YAML model_mappings.
        """
        from python.helpers import settings
        s = settings.get_settings()
        
        # 1. Check for explicit user override in settings.json
        # Format: {"multiagentdev_overrides": {"modes": {"code": {"model": "..."}}}}
        overrides = s.get("multiagentdev_overrides", {})
        if isinstance(overrides, dict):
            mode_overrides = overrides.get("modes", {}).get(mode_slug, {})
            if isinstance(mode_overrides, dict) and mode_overrides.get("model"):
                return mode_overrides.get("model")
        
        # 2. Check role_configurations from settings (dynamic - set by templates/UI)
        # This is the primary path for model resolution. Templates like Max Performance
        # and the per-agent profile UI both write to role_configurations.
        role_configs = s.get("role_configurations", {})
        if mode_slug in role_configs:
            role_cfg = role_configs[mode_slug]
            if isinstance(role_cfg, dict) and role_cfg.get("provider") and role_cfg.get("name"):
                # Return as a role reference so resolve_model_config handles it dynamically
                return f"role/{mode_slug}"
        
        # 3. Check mode config model from yaml (static fallback)
        mode_config = self.get_mode(mode_slug)
        if mode_config and mode_config.model:
            return mode_config.model
            
        # 4. Check global mappings from yaml (static fallback)
        if mode_slug in self.model_mappings:
            return self.model_mappings[mode_slug]
            
        return None
    
    def _create_default_modes(self):
        """Create default modes if config loading fails."""
        self.modes = {
            "code": ModeConfig(
                slug="code",
                name="💻 Code",
                display_name="Code Mode",
                description="Full-featured development mode",
                role_definition="You are a skilled software developer.",
                tool_groups=["read", "edit", "command", "browser", "communicate", "memory"],
                custom_instructions="Write clean, tested code.",
                supervisor_settings=SupervisorSettings(),
            ),
            "ask": ModeConfig(
                slug="ask",
                name="❓ Ask",
                display_name="Ask Mode",
                description="Question-answering mode",
                role_definition="You are a helpful assistant.",
                tool_groups=["read", "browser", "communicate", "memory"],
                custom_instructions="Answer questions clearly.",
                supervisor_settings=SupervisorSettings(
                    max_iterations_without_progress=15,
                    max_consecutive_tool_failures=5,
                ),
            ),
        }
        self.default_mode = "code"
        self._current_mode = "code"
        self._loaded = True
    
    @property
    def current_mode(self) -> str:
        """Get current mode slug."""
        return self._current_mode
    
    @property
    def current_mode_config(self) -> Optional[ModeConfig]:
        """Get current mode configuration."""
        return self.modes.get(self._current_mode)
    
    def get_mode(self, slug: str) -> Optional[ModeConfig]:
        """Get mode configuration by slug."""
        return self.modes.get(slug)
    
    def list_modes(self) -> List[ModeConfig]:
        """List all available modes."""
        return list(self.modes.values())
    
    def list_mode_slugs(self) -> List[str]:
        """List all available mode slugs."""
        return list(self.modes.keys())
    
    def switch_mode(self, new_mode: str, force: bool = False) -> bool:
        """
        Switch to a new mode.
        
        Args:
            new_mode: Slug of the mode to switch to.
            force: If True, ignore transition rules.
            
        Returns:
            True if switch was successful, False otherwise.
        """
        if new_mode not in self.modes:
            PrintStyle(
                font_color="red",
                padding=True
            ).print(f"Unknown mode: {new_mode}")
            return False
        
        if not force and not self.transitions.can_transition(self._current_mode, new_mode):
            PrintStyle(
                font_color="yellow",
                padding=True
            ).print(f"Transition from {self._current_mode} to {new_mode} not allowed")
            return False
        
        old_mode = self._current_mode
        self._current_mode = new_mode
        
        PrintStyle(
            font_color="cyan",
            padding=True
        ).print(f"Mode switched: {old_mode} → {new_mode}")
        
        return True
    
    def suggest_mode_for_task(self, task_text: str) -> Optional[str]:
        """
        Suggest a mode based on task text.
        
        Args:
            task_text: The task description.
            
        Returns:
            Suggested mode slug, or None if no suggestion.
        """
        return self.transitions.suggest_mode(task_text)
    
    def get_tool_groups_for_mode(self, mode_slug: Optional[str] = None) -> List[str]:
        """
        Get tool groups for a mode.
        
        Args:
            mode_slug: Mode slug. Uses current mode if not provided.
            
        Returns:
            List of tool group names.
        """
        slug = mode_slug or self._current_mode
        mode = self.modes.get(slug)
        if mode:
            return mode.tool_groups
        return []
    
    def get_supervisor_settings(self, mode_slug: Optional[str] = None) -> SupervisorSettings:
        """
        Get supervisor settings for a mode.
        
        Args:
            mode_slug: Mode slug. Uses current mode if not provided.
            
        Returns:
            SupervisorSettings for the mode.
        """
        slug = mode_slug or self._current_mode
        mode = self.modes.get(slug)
        if mode and mode.supervisor_settings:
            return mode.supervisor_settings
        return SupervisorSettings()  # Default settings
    
    def is_file_edit_allowed(self, filepath: str, mode_slug: Optional[str] = None) -> bool:
        """
        Check if editing a file is allowed in the current mode.
        
        Args:
            filepath: Path to the file.
            mode_slug: Mode slug. Uses current mode if not provided.
            
        Returns:
            True if editing is allowed, False otherwise.
        """
        slug = mode_slug or self._current_mode
        mode = self.modes.get(slug)
        
        if not mode:
            return True  # No mode = no restrictions
        
        # Check if edit is in tool groups
        if "edit" not in mode.tool_groups:
            return False
        
        # Check file pattern restrictions
        if mode.restrictions:
            return mode.restrictions.is_file_allowed(filepath)
        
        return True
    
    def get_role_definition(self, mode_slug: Optional[str] = None) -> str:
        """
        Get role definition for a mode.
        
        Args:
            mode_slug: Mode slug. Uses current mode if not provided.
            
        Returns:
            Role definition string.
        """
        slug = mode_slug or self._current_mode
        mode = self.modes.get(slug)
        if mode:
            return mode.role_definition
        return ""
    
    def get_custom_instructions(self, mode_slug: Optional[str] = None) -> str:
        """
        Get custom instructions for a mode.
        
        Args:
            mode_slug: Mode slug. Uses current mode if not provided.
            
        Returns:
            Custom instructions string.
        """
        slug = mode_slug or self._current_mode
        mode = self.modes.get(slug)
        if mode:
            return mode.custom_instructions
        return ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert manager state to dictionary for serialization."""
        return {
            "current_mode": self._current_mode,
            "default_mode": self.default_mode,
            "available_modes": self.list_mode_slugs(),
        }


# Convenience functions for global access
def get_mode_manager() -> ModeManager:
    """Get the global ModeManager instance."""
    return ModeManager.get_instance()


def get_current_mode() -> str:
    """Get the current mode slug."""
    return get_mode_manager().current_mode


def get_current_mode_config() -> Optional[ModeConfig]:
    """Get the current mode configuration."""
    return get_mode_manager().current_mode_config


def switch_mode(new_mode: str, force: bool = False) -> bool:
    """Switch to a new mode."""
    return get_mode_manager().switch_mode(new_mode, force)


def suggest_mode(task_text: str) -> Optional[str]:
    """Suggest a mode for a task."""
    return get_mode_manager().suggest_mode_for_task(task_text)
