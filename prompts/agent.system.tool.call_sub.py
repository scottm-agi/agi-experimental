import json
import os
from typing import Any, Optional
from python.helpers.files import VariablesPlugin
from python.helpers import files
from python.helpers.print_style import PrintStyle


def _load_ontology() -> dict:
    """Load the tool ontology for capability matrix injection."""
    try:
        ontology_path = files.get_abs_path("python", "tools", "ontology.json")
        with open(ontology_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


class CallSubordinate(VariablesPlugin):
    def get_variables(self, file: str, backup_dirs: Optional[list[str]] = None, **kwargs) -> dict[str, Any]:

        # Load ontology for capability matrix injection
        ontology = _load_ontology()
        ontology_profiles = ontology.get("profiles", {})

        # collect all prompt profiles from subdirectories (_context.md file)
        profiles = []
        agent_subdirs = files.get_subdirectories("agents", exclude=["_example"])
        for agent_subdir in agent_subdirs:
            # Skip internal files (e.g., _swarm_registry.json)
            if agent_subdir.startswith("_"):
                continue
            try:
                context = files.read_prompt_file(
                    "_context.md",
                    [files.get_abs_path("agents", agent_subdir)]
                )
                profile_data = {"name": agent_subdir, "context": context}

                # Inject capability categories from ontology
                if agent_subdir in ontology_profiles:
                    profile_data["capabilities"] = ontology_profiles[agent_subdir]

                profiles.append(profile_data)
            except Exception as e:
                PrintStyle().error(f"Error loading agent profile '{agent_subdir}': {e}")

        # in case of no profiles
        if not profiles:
            # PrintStyle().error("No agent profiles found")
            profiles = [
                {"name": "default", "context": "Default AGIX AI Assistant"}
            ]

        # ── SWARM FILTERING (Layer 1): hide unauthorized profiles from the LLM ──
        # If the calling agent is an orchestrator with a defined swarm,
        # filter the profiles list to only show authorized subordinates.
        agent = kwargs.get("agent")
        if agent is not None:
            agent_profile = getattr(agent.config, "profile", None) or "default"
            try:
                from python.helpers.swarm_registry import filter_profiles_for_agent
                profiles = filter_profiles_for_agent(agent_profile, profiles)
            except ImportError:
                pass  # swarm_registry not available — show all profiles

        return {"agent_profiles": profiles}

