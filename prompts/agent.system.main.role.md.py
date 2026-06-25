"""Dynamic prompt loader for agent.system.main.role.md.

Populates the {{agent_catalog}} placeholder with a live agent catalog
generated from the AgentCatalog in-memory cache.

This ensures the system prompt always has an up-to-date list of available
agent profiles without manual maintenance of static routing tables.
"""
from __future__ import annotations

import logging
from typing import Any

from python.helpers.files import VariablesPlugin

logger = logging.getLogger(__name__)


class Variables(VariablesPlugin):
    """Injects dynamic variables into the default agent's system prompt."""

    def get_variables(self, file: str, backup_dirs=None, **kwargs) -> dict[str, Any]:
        """Return template variables for agent.system.main.role.md."""
        variables: dict[str, Any] = {}

        # Inject the agent catalog table
        try:
            from python.helpers.agent_catalog import AgentCatalog
            catalog = AgentCatalog.get_instance()
            variables["agent_catalog"] = catalog.get_catalog_prompt()
        except Exception as e:
            logger.warning(f"Failed to load agent catalog for prompt: {e}")
            variables["agent_catalog"] = "*(Agent catalog unavailable — using manual routing)*"

        return variables
