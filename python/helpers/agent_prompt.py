"""
Agent prompt construction — extracted from agent.py.

Contains the implementation for get_system_prompt, parse_prompt, and read_prompt.
Delegated from Agent methods via the _impl pattern.
"""
import logging

from python.helpers.agent_core import AgentContextType
from python.helpers import files

logger = logging.getLogger(__name__)


async def get_system_prompt_impl(agent, loop_data) -> list[str]:
    """Build the system prompt list — delegated from Agent.get_system_prompt()."""
    system_prompt: list[str] = []
    await agent.call_extensions(
        "system_prompt", system_prompt=system_prompt, loop_data=loop_data
    )

    # Add task hardening instructions for scheduled tasks (Item #4)
    if agent.context.type == AgentContextType.TASK:
        hardening_prompt = agent.read_prompt("agent.system.task_hardening.md")
        if hardening_prompt:
            system_prompt.append(hardening_prompt)

    # Add skills compact index (Phase 2A: Progressive Discovery)
    # Instead of injecting full skill instruction bodies (~24K tokens),
    # we inject a compact name+description index and agents use the
    # view_skill tool to load full instructions on demand.
    if hasattr(agent, 'skills_manager'):
        mode = agent.config.profile or "default"

        # Determine available tools for conditional filtering (Phase 2B)
        available_tool_names = []
        if hasattr(agent.config, 'tools') and agent.config.tools:
            available_tool_names = [
                getattr(t, 'name', str(t)) for t in agent.config.tools
            ]

        context_type_str = agent.context.type.value if agent.context and agent.context.type else "user"

        compact_prompt = agent.skills_manager.get_compact_index_prompt(
            mode=mode,
            available_tools=available_tool_names if available_tool_names else None,
            context_type=context_type_str
        )
        if compact_prompt:
            system_prompt.append(compact_prompt)

    return system_prompt


def parse_prompt_impl(agent, _prompt_file: str, **kwargs):
    """Parse a prompt template — delegated from Agent.parse_prompt()."""
    dirs = [files.get_abs_path("prompts")]
    if (
        agent.config.profile
    ):  # if agent has custom folder, use it and use default as backup
        prompt_dir = files.get_abs_path("agents", agent.config.profile, "prompts")
        dirs.insert(0, prompt_dir)
    prompt = files.parse_file(
        _prompt_file, _directories=dirs, **kwargs
    )
    return prompt


def read_prompt_impl(agent, file: str, **kwargs) -> str:
    """Read and render a prompt file — delegated from Agent.read_prompt()."""
    dirs = [files.get_abs_path("prompts")]
    if (
        agent.config.profile
    ):  # if agent has custom folder, use it and use default as backup
        prompt_dir = files.get_abs_path("agents", agent.config.profile, "prompts")
        dirs.insert(0, prompt_dir)
    prompt = files.read_prompt_file(
        file, _directories=dirs, agent=agent, **kwargs
    )
    prompt = files.remove_code_fences(prompt)
    return prompt
