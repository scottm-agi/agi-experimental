from __future__ import annotations
from typing import Any
from python.helpers.extension import Extension
from python.helpers.mcp_handler import MCPConfig
from python.agent import Agent, LoopData
from python.helpers.settings import get_settings
from python.helpers import projects
from python.helpers.tool_selector import ToolSelector



class SystemPrompt(Extension):

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs: Any
    ):
        # append main system prompt and tools
        main = get_main_prompt(self.agent)
        tools = get_tools_prompt(self.agent)
        mcp_tools = await get_mcp_tools_prompt(self.agent)
        secrets_prompt = get_secrets_prompt(self.agent)
        parameter_prompt = get_parameters_prompt(self.agent)
        project_prompt = get_project_prompt(self.agent)

        system_prompt.append(main)
        system_prompt.append(tools)
        if mcp_tools:
            system_prompt.append(mcp_tools)
        if secrets_prompt:
            system_prompt.append(secrets_prompt)
        if parameter_prompt:
            system_prompt.append(parameter_prompt)
        if project_prompt:
            system_prompt.append(project_prompt)

        # Inject user custom instructions from mode_manager (if any)
        custom_instructions_prompt = get_custom_instructions_prompt(self.agent)
        if custom_instructions_prompt:
            system_prompt.append(custom_instructions_prompt)


def get_main_prompt(agent: Agent):
    return agent.read_prompt("agent.system.main.md")


def get_tools_prompt(agent: Agent):
    prompt = agent.read_prompt("agent.system.tools.md")
    if agent.config.chat_model.vision:
        prompt += "\n\n" + agent.read_prompt("agent.system.tools_vision.md")
    return prompt


async def get_mcp_tools_prompt(agent: Agent):
    mcp_config = MCPConfig.get_instance()
    
    # Wait for initialization to be ready (instead of just checking .servers)
    # This prevents the race condition where agents start before tools are loaded.
    await mcp_config.wait_until_ready()
    
    if mcp_config.servers:
        pre_progress = agent.context.log.progress
        profile = agent.config.profile or "default"
        tools = await MCPConfig.get_tools_prompt(filter_by_profile=profile)
        
        agent.context.log.set_progress(pre_progress)  # return original progress
        return tools
    return ""


def get_secrets_prompt(agent: Agent):
    try:
        # Use lazy import to avoid circular dependencies
        from python.helpers.secrets_helper import get_secrets_manager

        secrets_manager = get_secrets_manager(agent.context)
        secrets = secrets_manager.get_secrets_for_prompt()
        return agent.read_prompt("agent.system.secrets.md", secrets=secrets)
    except Exception as e:
        # If secrets module is not available or has issues, return empty string
        return ""


def get_parameters_prompt(agent: Agent):
    try:
        from python.helpers.parameters import get_parameters_manager

        pm = get_parameters_manager(agent.context)
        params_dict = pm.load_parameters()
        # Only list keys for prompt to avoid bloating context with values
        # formatting as a bulleted list of keys
        params_str = "\n".join([f"- {k}" for k in params_dict.keys()]) if params_dict else ""
        if params_str:
            return agent.read_prompt("agent.system.parameters.md", parameters=params_str)
        return ""
    except Exception as e:
        return ""


def get_project_prompt(agent: Agent):
    result = agent.read_prompt("agent.system.projects.main.md")
    project_name = agent.context.get_data(projects.CONTEXT_DATA_KEY_PROJECT)
    if project_name:
        project_vars = projects.build_system_prompt_vars(project_name)
        result += "\n\n" + agent.read_prompt(
            "agent.system.projects.active.md", **project_vars
        )
    else:
        result += "\n\n" + agent.read_prompt("agent.system.projects.inactive.md")
    return result


def get_custom_instructions_prompt(agent: Agent):
    """Retrieve user custom instructions from mode_manager and format for system prompt.
    
    Falls back to global custom instructions from prompts/agent.system.custom_instructions.md
    when the current mode has no custom instructions configured.
    """
    instructions = ""
    
    # 1. Try mode-specific custom instructions
    try:
        from python.helpers.mode_manager import get_mode_manager
        mm = get_mode_manager()
        mode_instructions = mm.get_custom_instructions()
        if mode_instructions and mode_instructions.strip():
            instructions = mode_instructions
    except Exception:
        pass
    
    # 2. Always append global custom instructions as baseline
    global_custom_instructions = ""
    try:
        global_custom_instructions = agent.read_prompt("agent.system.custom_instructions.md")
    except Exception:
        pass
    
    # Combine: mode-specific + global baseline
    parts = []
    if instructions:
        parts.append(instructions)
    if global_custom_instructions and global_custom_instructions.strip():
        parts.append(global_custom_instructions)
    
    if parts:
        return "## User Custom Instructions\n\n" + "\n\n---\n\n".join(parts)
    return ""

