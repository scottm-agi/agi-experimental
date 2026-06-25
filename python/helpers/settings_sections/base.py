"""
Base types and utilities for settings section builders.

This module contains shared TypedDicts and helper functions used across
all section builder modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from python.helpers.settings import Settings


# Constants
PASSWORD_PLACEHOLDER = "****PSWD****"
API_KEY_PLACEHOLDER = "************"


class FieldOption(TypedDict):
    """Option for select/dropdown fields."""
    value: str
    label: str


class SettingsField(TypedDict, total=False):
    """Field definition for settings UI."""
    id: str
    title: str
    description: str
    type: Literal[
        "text",
        "number",
        "select",
        "range",
        "textarea",
        "password",
        "switch",
        "button",
        "html",
        "searchable-select",
        "kvp",
    ]
    value: Any
    min: float
    max: float
    step: float
    hidden: bool
    options: list[FieldOption]
    style: str
    action: str  # For button fields
    format: str  # For kvp fields
    isSecret: bool  # For kvp fields


class SettingsSection(TypedDict, total=False):
    """Section definition for settings UI."""
    id: str
    title: str
    description: str
    fields: list[SettingsField]
    tab: str  # Indicates which tab this section belongs to


@dataclass
class SectionBuilderContext:
    """
    Context object passed to section builders.
    
    Contains all data needed to build sections, avoiding the need
    to pass many individual parameters to each builder function.
    """
    settings: "Settings"
    default_settings: "Settings"
    available_profiles: list[str] = field(default_factory=list)
    role_configs: dict[str, Any] = field(default_factory=dict)
    file_access_enabled: bool = True
    
    def __post_init__(self):
        """Initialize derived fields after dataclass creation."""
        if not self.available_profiles:
            from python.helpers import files
            self.available_profiles = files.get_subdirectories("agents")
            # Exclude architecture legacy name and _example template
            self.available_profiles = [
                p for p in self.available_profiles 
                if p not in ("_example", "architecture")
            ]
            self.available_profiles = sorted(self.available_profiles)
        
        if not self.role_configs:
            self.role_configs = self.settings.get("role_configurations", {})


def dict_to_env(data_dict: dict[str, Any]) -> str:
    """
    Convert a dictionary to .env format string.
    
    Args:
        data_dict: Dictionary to convert
        
    Returns:
        String in KEY=VALUE format, one per line
    """
    import json
    
    lines = []
    for key, value in data_dict.items():
        if isinstance(value, str):
            # Quote strings and escape internal quotes
            escaped_value = value.replace('"', '\\"')
            lines.append(f'{key}="{escaped_value}"')
        elif isinstance(value, (dict, list, bool)) or value is None:
            # Serialize as unquoted JSON
            lines.append(f'{key}={json.dumps(value, separators=(",", ":"))}')
        else:
            # Numbers and other types as unquoted strings
            lines.append(f'{key}={value}')
    
    return "\n".join(lines)


def env_to_dict(data: str) -> dict[str, Any]:
    """
    Convert .env format string to dictionary.
    
    Args:
        data: String in KEY=VALUE format
        
    Returns:
        Dictionary with parsed values
    """
    import json
    
    result = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        if '=' not in line:
            continue
            
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        
        # If quoted, treat as string
        if value.startswith('"') and value.endswith('"'):
            result[key] = value[1:-1].replace('\\"', '"')  # Unescape quotes
        elif value.startswith("'") and value.endswith("'"):
            result[key] = value[1:-1].replace("\\'", "'")  # Unescape quotes
        else:
            # Not quoted, try JSON parse
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[key] = value
    
    return result


def get_providers(provider_type: str) -> list[FieldOption]:
    """
    Get list of providers for a given type.
    
    Args:
        provider_type: Type of provider ("chat" or "embedding")
        
    Returns:
        List of FieldOption dicts with value and label
    """
    from python.helpers.providers import get_providers as _get_providers
    from typing import cast
    return cast(list[FieldOption], _get_providers(provider_type))


def get_subdirectories(path: str, exclude: str | None = None) -> list[str]:
    """
    Get subdirectories from a path.
    
    Args:
        path: Base path to search
        exclude: Directory name to exclude
        
    Returns:
        List of subdirectory names
    """
    from python.helpers import files
    return files.get_subdirectories(path, exclude=exclude)