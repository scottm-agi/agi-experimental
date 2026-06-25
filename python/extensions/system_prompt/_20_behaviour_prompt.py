from __future__ import annotations
from datetime import datetime
from python.helpers.extension import Extension
from python.agent import Agent, LoopData
from python.helpers import files, memory

# Profiles that receive orchestrator-specific prompts (delegation rules, memory bank ownership)
# FIX-020: Use centralized profile registry instead of hardcoded names
from python.helpers.profile_registry import ORCHESTRATOR_PROFILES


class BehaviourPrompt(Extension):

    async def execute(self, system_prompt: list[str]=[], loop_data: LoopData = LoopData(), **kwargs):
        prompt = read_rules(self.agent)
        system_prompt.insert(0, prompt) #.append(prompt)

        # Fix 5 (Iteration 209): Role-based prompt loading.
        # Orchestrator-specific prompts (delegation rules, memory bank ownership)
        # are ONLY loaded for orchestrator profiles. Code/frontend/browser agents
        # skip ~2K tokens of irrelevant orchestration instructions.
        profile = (self.agent.config.profile or "default").lower()
        if profile in ORCHESTRATOR_PROFILES:
            orchestrator_prompt = self.agent.read_prompt("agent.system.behaviour_orchestrator.md")
            if orchestrator_prompt:
                system_prompt.append(orchestrator_prompt)

def get_custom_rules_file(agent: Agent):
    return files.get_abs_path(memory.get_memory_subdir_abs(agent), "behaviour.md")

def read_rules(agent: Agent):
    rules_file = get_custom_rules_file(agent)
    if files.exists(rules_file):
        rules = files.read_file(rules_file) # no includes and vars here, that could crash
        return agent.read_prompt("agent.system.behaviour.md", rules=rules)
    else:
        rules = agent.read_prompt("agent.system.behaviour_default.md")
        return agent.read_prompt("agent.system.behaviour.md", rules=rules)

  