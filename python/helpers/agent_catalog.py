"""AgentCatalog — In-memory cache of agent profiles.

Scans the /agents/ directory on init (or refresh), extracts profile names
and descriptions from each profile's agent.system.main.role.md file.

Used by:
- prompt_router.py: validates LLM-returned profiles against real agents
- agent.system.main.role.md.py: injects dynamic agent catalog into system prompt

Singleton: use AgentCatalog.get_instance() for the shared instance.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class AgentProfile:
    """Cached metadata for a single agent profile."""
    name: str
    description: str
    profile_dir: str


class AgentCatalog:
    """In-memory cache of agent profiles, built from disk on boot.
    
    Provides:
    - get_valid_profiles()    → set of profile name strings
    - get_description(name)  → description string or None
    - get_catalog_prompt()   → formatted table for system prompt injection
    - refresh()              → rebuild cache (e.g., after user adds agent)
    """

    _instance: Optional[AgentCatalog] = None

    def __init__(self, agents_dir: Optional[str] = None):
        self._agents_dir = agents_dir or self._default_agents_dir()
        self._profiles: Dict[str, AgentProfile] = {}

    @classmethod
    def get_instance(cls, agents_dir: Optional[str] = None) -> AgentCatalog:
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(agents_dir=agents_dir)
            cls._instance.build_catalog()
        return cls._instance

    @staticmethod
    def _default_agents_dir() -> str:
        """Resolve the default agents directory relative to the project root."""
        try:
            from python.helpers import files
            return files.get_abs_path("agents")
        except Exception:
            # Fallback: resolve relative to this file
            return os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "agents"
            )

    def build_catalog(self) -> None:
        """Scan the agents directory and populate the cache."""
        self._profiles.clear()

        if not os.path.isdir(self._agents_dir):
            logger.warning(f"AgentCatalog: agents directory not found: {self._agents_dir}")
            return

        for entry in sorted(os.listdir(self._agents_dir)):
            # Skip dotfiles, _example, and non-directories
            if entry.startswith(".") or entry.startswith("_"):
                continue

            profile_dir = os.path.join(self._agents_dir, entry)
            if not os.path.isdir(profile_dir):
                continue

            # Read the role file for description
            role_path = os.path.join(profile_dir, "prompts", "agent.system.main.role.md")
            if not os.path.isfile(role_path):
                continue

            description = self._extract_description(role_path)
            if description:
                self._profiles[entry] = AgentProfile(
                    name=entry,
                    description=description,
                    profile_dir=profile_dir,
                )

        logger.info(f"AgentCatalog: loaded {len(self._profiles)} profiles: {sorted(self._profiles.keys())}")

    def refresh(self) -> None:
        """Rebuild the catalog from disk. Call when agents are dynamically added."""
        self.build_catalog()

    def get_valid_profiles(self) -> Set[str]:
        """Return set of all valid profile names."""
        return set(self._profiles.keys())

    def get_description(self, profile: str) -> Optional[str]:
        """Return the description for a profile, or None if not found."""
        p = self._profiles.get(profile)
        return p.description if p else None

    def get_catalog_prompt(self) -> str:
        """Generate a formatted agent catalog for system prompt injection.
        
        Returns a markdown table listing all available agents with descriptions.
        """
        if not self._profiles:
            return "No agents available."

        lines = [
            "| Profile | Description |",
            "|---|---|",
        ]
        for name in sorted(self._profiles.keys()):
            profile = self._profiles[name]
            # Escape pipes in description
            desc = profile.description.replace("|", "\\|")
            lines.append(f"| `{name}` | {desc} |")

        return "\n".join(lines)

    @staticmethod
    def _extract_description(role_path: str) -> Optional[str]:
        """Extract a short description from the agent's role file.
        
        Reads the first meaningful lines (skipping markdown headers) to build
        a concise one-line description.
        """
        try:
            with open(role_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning(f"AgentCatalog: failed to read {role_path}: {e}")
            return None

        # Collect first 2 non-header, non-empty lines as description
        description_parts = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip markdown headers
            if stripped.startswith("#"):
                continue
            description_parts.append(stripped)
            if len(description_parts) >= 2:
                break

        if not description_parts:
            return None

        # Join and truncate to reasonable length
        desc = " ".join(description_parts)
        if len(desc) > 200:
            desc = desc[:197] + "..."
        return desc
