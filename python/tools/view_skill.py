"""
view_skill — Tool for agents to lazily load full skill instructions on demand.

This is part of the Progressive Discovery architecture (Phase 2A):
- System prompt contains only a compact skill index (name + description)
- Agents use this tool to load full instructions when they need a skill
- Saves ~24K tokens of context by not eagerly injecting all skill bodies

This replaces the old pattern where all skill instructions were baked
into the system prompt at agent init time.
"""
from __future__ import annotations

from python.helpers.tool import Tool, Response
from python.helpers.skills_manager import SkillContent


class ViewSkill(Tool):
    """
    Load the full instructions of a skill by name.
    
    The agent's system prompt contains a compact index of available skills.
    Use this tool to load the full SKILL.md instructions when you need to
    execute a specific skill's workflow.
    """

    async def execute(self, **kwargs) -> Response:
        skill_name = self.args.get("skill_name")

        if not skill_name:
            return Response(
                message="Error: Missing 'skill_name' argument. Provide the name of the skill to view.",
                break_loop=False
            )

        # Look up skill from the agent's SkillsManager
        if not hasattr(self.agent, 'skills_manager'):
            return Response(
                message="Error: Skills manager not available on this agent.",
                break_loop=False
            )

        skill = self.agent.skills_manager.get_skill_content(skill_name)

        if not skill or not isinstance(skill, SkillContent):
            # List available skills to help the agent
            available = list(self.agent.skills_manager.skills.keys())
            available_str = ", ".join(available) if available else "(none)"
            return Response(
                message=f"Skill '{skill_name}' not found. Available skills: {available_str}",
                break_loop=False
            )

        return Response(
            message=f"## Skill: {skill.name}\n\n**Description**: {skill.description}\n\n{skill.instructions}",
            break_loop=False
        )
