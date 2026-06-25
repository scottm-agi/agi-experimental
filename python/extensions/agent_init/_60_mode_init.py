from __future__ import annotations
"""
Mode Initialization Extension

Initializes mode settings for MultiAgentDev profile agents.
Applies tool filtering and mode-specific configuration on agent creation.
"""

from python.agent import Agent
from python.helpers.print_style import PrintStyle


async def extension(agent: Agent):
    """
    Initialize mode settings for the agent.
    
    This extension runs during agent initialization and:
    1. Checks if agent is using multiagentdev profile
    2. Loads mode configuration
    3. Applies tool filtering based on mode
    4. Sets mode-specific supervisor settings
    """
    # Only apply to multiagentdev profile
    if not _is_multiagentdev_profile(agent):
        return
    
    try:
        from python.helpers.mode_manager import get_mode_manager, ModeConfig
        from python.helpers.tool_groups import apply_tool_filter_to_agent
        
        manager = get_mode_manager()
        mode_config = manager.current_mode_config
        
        if not mode_config:
            PrintStyle(
                font_color="yellow",
                padding=True
            ).print(f"[Mode Init] No mode config found, using defaults")
            return
        
        # Store mode info on agent for reference
        agent.set_data("multiagentdev_mode", manager.current_mode)
        agent.set_data("multiagentdev_mode_config", mode_config)
        
        # Apply tool filtering based on mode
        apply_tool_filter_to_agent(agent, manager.current_mode)
        
        # Log mode initialization
        PrintStyle(
            font_color="cyan",
            padding=True
        ).print(f"[Mode Init] Agent {agent.agent_name} initialized in {mode_config.name} mode")
        
        # Store supervisor settings for later use
        supervisor_settings = manager.get_supervisor_settings()
        agent.set_data("multiagentdev_supervisor_settings", supervisor_settings)
        
    except ImportError as e:
        PrintStyle(
            font_color="yellow",
            padding=True
        ).print(f"[Mode Init] Mode manager not available: {e}")
    except Exception as e:
        PrintStyle(
            font_color="red",
            padding=True
        ).print(f"[Mode Init] Error initializing mode: {e}")


def _is_multiagentdev_profile(agent: Agent) -> bool:
    """Check if agent is using multiagentdev profile."""
    # Check agent config for profile
    if hasattr(agent, 'config') and hasattr(agent.config, 'prompts_subdir'):
        return 'multiagentdev' in str(agent.config.prompts_subdir)
    
    # Check agent name
    if hasattr(agent, 'agent_name'):
        return 'multiagentdev' in str(agent.agent_name).lower()
    
    # Check context for profile setting
    if hasattr(agent, 'context') and agent.context:
        ctx = agent.context
        if hasattr(ctx, 'config') and hasattr(ctx.config, 'prompts_subdir'):
            return 'multiagentdev' in str(ctx.config.prompts_subdir)
    
    return False


def get_agent_mode(agent: Agent) -> str:
    """Get the current mode for an agent."""
    mode = agent.get_data("multiagentdev_mode")
    if mode:
        return mode
    
    # Fallback to global mode
    try:
        from python.helpers.mode_manager import get_current_mode
        return get_current_mode()
    except ImportError:
        return "code"  # Default


def set_agent_mode(agent: Agent, mode_slug: str) -> bool:
    """
    Set the mode for an agent.
    
    Args:
        agent: Agent instance
        mode_slug: Mode slug to set
        
    Returns:
        True if mode was set successfully
    """
    try:
        from python.helpers.mode_manager import get_mode_manager
        from python.helpers.tool_groups import apply_tool_filter_to_agent
        
        manager = get_mode_manager()
        
        # Validate mode exists
        mode_config = manager.get_mode(mode_slug)
        if not mode_config:
            PrintStyle(
                font_color="red",
                padding=True
            ).print(f"[Mode Init] Unknown mode: {mode_slug}")
            return False
        
        # Update agent mode
        agent.set_data("multiagentdev_mode", mode_slug)
        agent.set_data("multiagentdev_mode_config", mode_config)
        
        # Update model using priority resolution (Settings > Profiles > Mode Defaults)
        resolved_model = manager.get_model_for_mode(mode_slug, agent.config)
        if resolved_model:
            from python.models import get_model_by_name
            new_model = get_model_by_name(resolved_model)
            if new_model:
                agent.config.chat_model = new_model
                PrintStyle(
                    font_color="cyan",
                    padding=True
                ).print(f"[Mode Init] Agent {agent.agent_name} model updated to {resolved_model} (Source: Dynamic)")
        
        # Re-apply tool filtering
        apply_tool_filter_to_agent(agent, mode_slug)
        
        # Update supervisor settings
        supervisor_settings = manager.get_supervisor_settings(mode_slug)
        agent.set_data("multiagentdev_supervisor_settings", supervisor_settings)
        
        PrintStyle(
            font_color="cyan",
            padding=True
        ).print(f"[Mode Init] Agent {agent.agent_name} switched to {mode_config.name} mode")
        
        return True
        
    except Exception as e:
        import traceback
        PrintStyle(
            font_color="red",
            padding=True
        ).print(f"[Mode Init] Error setting mode: {e}\n{traceback.format_exc()}")
        return False
