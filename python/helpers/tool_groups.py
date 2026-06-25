from __future__ import annotations
"""
MultiAgentDev Tool Groups

Maps AGIX tools to groups similar to RooCode's tool group system.
Provides filtering functionality to restrict tool access based on mode.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent


# Tool group definitions
# Maps group names to lists of tool names
TOOL_GROUPS: Dict[str, List[str]] = {
    # Read group - tools for reading/querying information
    "read": [
        "memory_load",
        "document_query",
        "search_engine",
        "vision_load",
        "perplexity_ask.perplexity_ask",
        "mcp_perplexity_ask.perplexity_ask",
        "scrape_url",
    ],
    
    # Edit group - tools for modifying files/code
    "edit": [
        "code_execution_tool",  # Can write files via code
    ],
    
    # Command group - tools for executing system commands
    "command": [
        "code_execution_tool",  # Can execute shell commands
        "input",  # Can send keyboard input to running terminal programs
    ],
    
    # Delegate group - tools for delegating to subordinates
    "delegate": [
        "call_subordinate",
        "call_subordinate_batch",
    ],
    
    # Browser group - tools for web browsing
    "browser": [
        "browser_agent",
        "scrape_url",
    ],
    
    # Communicate group - tools for communication
    "communicate": [
        "response",
        "input",
        "notify_user",
        "a2a_chat",
    ],
    
    # Memory group - tools for memory management
    "memory": [
        "memory_save",
        "memory_load",
        "memory_delete",
        "memory_forget",
    ],
    
    # Scheduler group - tools for scheduling
    "scheduler": [
        "scheduler",
        "wait",
    ],
    
    # Behavior group - tools for behavior adjustment
    "behavior": [
        "behaviour_adjustment",
    ],
    
    # Thinking group - for sequential thinking and planning
    "thinking": [
        "sequential_thinking.sequentialthinking",
        "sequential-thinking.sequentialthinking",
    ],
}

# Reverse mapping: tool name -> groups it belongs to
TOOL_TO_GROUPS: Dict[str, List[str]] = {}
for group, tools in TOOL_GROUPS.items():
    for tool in tools:
        if tool not in TOOL_TO_GROUPS:
            TOOL_TO_GROUPS[tool] = []
        TOOL_TO_GROUPS[tool].append(group)


@dataclass
class ToolGroupConfig:
    """Configuration for tool group filtering."""
    enabled_groups: List[str]
    file_restrictions: Optional[Dict[str, List[str]]] = None  # group -> file patterns
    
    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed based on enabled groups."""
        # Get groups this tool belongs to
        tool_groups = TOOL_TO_GROUPS.get(tool_name, [])
        
        if not tool_groups:
            # Tool not in any group - allow by default
            # This handles tools like "unknown" that aren't categorized
            return True
        
        # Tool is allowed if ANY of its groups is enabled
        return any(group in self.enabled_groups for group in tool_groups)
    
    def get_allowed_tools(self) -> Set[str]:
        """Get set of all allowed tool names."""
        allowed = set()
        for group in self.enabled_groups:
            if group in TOOL_GROUPS:
                allowed.update(TOOL_GROUPS[group])
        return allowed
    
    def get_file_patterns(self, group: str) -> List[str]:
        """Get file patterns for a group."""
        if self.file_restrictions and group in self.file_restrictions:
            return self.file_restrictions[group]
        return ["*"]  # All files allowed by default


class ToolGroupFilter:
    """
    Filters tools based on mode configuration.
    
    Integrates with ModeManager to apply tool restrictions.
    """
    
    def __init__(self, enabled_groups: Optional[List[str]] = None):
        """
        Initialize tool group filter.
        
        Args:
            enabled_groups: List of enabled group names. If None, all groups enabled.
        """
        if enabled_groups is None:
            enabled_groups = list(TOOL_GROUPS.keys())
        
        self.config = ToolGroupConfig(enabled_groups=enabled_groups)
    
    @classmethod
    def from_mode(cls, mode_slug: str) -> "ToolGroupFilter":
        """
        Create filter from mode configuration.
        
        Args:
            mode_slug: Mode slug to get groups from.
            
        Returns:
            ToolGroupFilter configured for the mode.
        """
        from python.helpers.mode_manager import get_mode_manager
        
        manager = get_mode_manager()
        mode = manager.get_mode(mode_slug)
        
        if mode:
            filter_instance = cls(enabled_groups=mode.tool_groups)
            
            # Apply file restrictions if any
            if mode.restrictions and mode.restrictions.edit:
                filter_instance.config.file_restrictions = {
                    "edit": mode.restrictions.get_allowed_file_patterns()
                }
            
            return filter_instance
        
        # Default: all groups enabled
        return cls()
    
    @classmethod
    def for_current_mode(cls) -> "ToolGroupFilter":
        """Create filter for the current mode."""
        from python.helpers.mode_manager import get_current_mode
        return cls.from_mode(get_current_mode())
    
    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed."""
        return self.config.is_tool_allowed(tool_name)
    
    def filter_tools(self, tools: List[Any]) -> List[Any]:
        """
        Filter a list of tools based on enabled groups.
        
        Args:
            tools: List of tool instances (must have 'name' attribute).
            
        Returns:
            Filtered list of allowed tools.
        """
        return [t for t in tools if self.is_tool_allowed(getattr(t, 'name', str(t)))]
    
    def get_allowed_tool_names(self) -> Set[str]:
        """Get set of allowed tool names."""
        return self.config.get_allowed_tools()
    
    def get_enabled_groups(self) -> List[str]:
        """Get list of enabled groups."""
        return self.config.enabled_groups
    
    def is_group_enabled(self, group: str) -> bool:
        """Check if a group is enabled."""
        return group in self.config.enabled_groups


def get_tools_for_groups(groups: List[str]) -> List[str]:
    """
    Get all tool names for specified groups.
    
    Args:
        groups: List of group names.
        
    Returns:
        List of unique tool names.
    """
    tools = set()
    for group in groups:
        if group in TOOL_GROUPS:
            tools.update(TOOL_GROUPS[group])
    return list(tools)


def get_groups_for_tool(tool_name: str) -> List[str]:
    """
    Get groups that a tool belongs to.
    
    Args:
        tool_name: Name of the tool.
        
    Returns:
        List of group names.
    """
    return TOOL_TO_GROUPS.get(tool_name, [])


def is_tool_in_group(tool_name: str, group: str) -> bool:
    """
    Check if a tool is in a specific group.
    
    Args:
        tool_name: Name of the tool.
        group: Group name.
        
    Returns:
        True if tool is in group.
    """
    return group in get_groups_for_tool(tool_name)


def list_all_groups() -> List[str]:
    """List all available group names."""
    return list(TOOL_GROUPS.keys())


def list_tools_in_group(group: str) -> List[str]:
    """
    List all tools in a group.
    
    Args:
        group: Group name.
        
    Returns:
        List of tool names in the group.
    """
    return TOOL_GROUPS.get(group, [])


def filter_tools_for_mode(tools: List[Any], mode_slug: Optional[str] = None) -> List[Any]:
    """
    Filter tools based on mode.
    
    Args:
        tools: List of tool instances.
        mode_slug: Mode slug. Uses current mode if not provided.
        
    Returns:
        Filtered list of tools.
    """
    if mode_slug:
        filter_instance = ToolGroupFilter.from_mode(mode_slug)
    else:
        filter_instance = ToolGroupFilter.for_current_mode()
    
    return filter_instance.filter_tools(tools)


def apply_tool_filter_to_agent(agent: "Agent", mode_slug: Optional[str] = None) -> None:
    """
    Apply tool filtering to an agent based on mode.
    
    Args:
        agent: Agent instance to filter tools for.
        mode_slug: Mode slug. Uses current mode if not provided.
    """
    from python.helpers.mode_manager import get_mode_manager
    
    manager = get_mode_manager()
    slug = mode_slug or manager.current_mode
    mode = manager.get_mode(slug)
    
    if not mode:
        return  # No mode = no filtering
    
    # Store original tools if not already stored
    if not hasattr(agent, '_original_tools'):
        agent._original_tools = agent.config.tools.copy() if hasattr(agent.config, 'tools') else []
    
    # Get allowed tools for this mode
    allowed_tools = get_tools_for_groups(mode.tool_groups)
    
    # Check global toggle for Sequential Thinking
    from python.helpers import settings
    if settings.get_settings().get("mcp_sequential_thinking_enabled", True):
        # Ensure thinking tools are included even if not in mode.tool_groups
        thinking_tools = TOOL_GROUPS.get("thinking", [])
        for tool in thinking_tools:
            if tool not in allowed_tools:
                allowed_tools.append(tool)
    else:
        # Remove thinking group tools if disabled globally
        thinking_tools = set(TOOL_GROUPS.get("thinking", []))
        allowed_tools = [t for t in allowed_tools if t not in thinking_tools]
    
    # Filter agent's tools
    if hasattr(agent.config, 'tools'):
        agent.config.tools = [
            t for t in agent._original_tools 
            if getattr(t, 'name', str(t)) in allowed_tools or 
               getattr(t, 'name', str(t)) not in TOOL_TO_GROUPS  # Allow uncategorized tools
        ]


def restore_agent_tools(agent: "Agent") -> None:
    """
    Restore agent's original tools (undo filtering).
    
    Args:
        agent: Agent instance to restore tools for.
    """
    if hasattr(agent, '_original_tools'):
        if hasattr(agent.config, 'tools'):
            agent.config.tools = agent._original_tools.copy()


# Tool metadata decorator for future use
def tool_group(*groups: str) -> Callable:
    """
    Decorator to mark a tool's group membership.
    
    Usage:
        @tool_group("read", "memory")
        class MyTool(Tool):
            ...
    
    Args:
        groups: Group names this tool belongs to.
        
    Returns:
        Decorator function.
    """
    def decorator(cls):
        cls._tool_groups = list(groups)
        return cls
    return decorator


# Summary functions for debugging/display
def get_tool_group_summary() -> Dict[str, Any]:
    """Get summary of tool groups for debugging."""
    return {
        "groups": {
            group: {
                "tools": tools,
                "count": len(tools)
            }
            for group, tools in TOOL_GROUPS.items()
        },
        "total_groups": len(TOOL_GROUPS),
        "total_tools": len(set(t for tools in TOOL_GROUPS.values() for t in tools)),
    }


def print_tool_groups():
    """Print tool groups for debugging."""
    from python.helpers.print_style import PrintStyle
    
    PrintStyle(font_color="cyan", padding=True).print("Tool Groups:")
    for group, tools in TOOL_GROUPS.items():
        PrintStyle(font_color="white").print(f"  {group}: {', '.join(tools)}")
