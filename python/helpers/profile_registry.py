"""Centralized Profile Registry (FIX-020 / G-8).

Replaces 13+ hardcoded ``{"multiagentdev", "alex"}`` and
``{"multiagentdev", "alex", "default"}`` sets scattered across extensions
with a single source of truth.

All modules that need to check whether an agent is an orchestrator MUST
import ``is_orchestrator`` (or ``ORCHESTRATOR_PROFILES``) from here.

Also provides ``get_project_dir()`` (FIX-021 / G-9) for standardized
project directory key access with fallback chain.

Usage::

    from python.helpers.profile_registry import is_orchestrator, get_project_dir

    if is_orchestrator(agent.agent_name):
        ...

    project_dir = get_project_dir(agent.data)
"""

import logging
from typing import Optional

logger = logging.getLogger("agix.profile_registry")

# ═══════════════════════════════════════════════════════════════
# FIX-020: Centralized Orchestrator Profiles (G-8)
# ═══════════════════════════════════════════════════════════════
# Single source of truth for ALL orchestrator profile checks.
# Add new orchestrator profiles HERE — never hardcode in extensions.

ORCHESTRATOR_PROFILES: frozenset[str] = frozenset({
    "multiagentdev",
    "alex",
    "default",
})

# Subset: orchestrator profiles that run web-dev workflows
# (used by completion gate, build pass gate, etc.)
WEB_ORCHESTRATOR_PROFILES: frozenset[str] = frozenset({
    "multiagentdev",
})

# Subset: orchestrator profiles that can delegate to subordinates
# (includes supervisor for requirements manifest gate)
DELEGATION_ORCHESTRATOR_PROFILES: frozenset[str] = frozenset({
    "multiagentdev",
    "alex",
    "supervisor",
})


def is_orchestrator(agent_name_or_profile: str) -> bool:
    """Check if the given agent name or profile is an orchestrator.

    This is the canonical check — use this instead of hardcoded sets.

    Args:
        agent_name_or_profile: Agent name string (e.g. "multiagentdev", "alex").

    Returns:
        True if the agent is an orchestrator profile.
    """
    if not agent_name_or_profile:
        return False
    name = agent_name_or_profile.lower().strip()
    return name in ORCHESTRATOR_PROFILES


def is_web_orchestrator(agent_name_or_profile: str) -> bool:
    """Check if the given agent is a web-dev orchestrator.

    Args:
        agent_name_or_profile: Agent name string.

    Returns:
        True if the agent is a web-dev orchestrator.
    """
    if not agent_name_or_profile:
        return False
    name = agent_name_or_profile.lower().strip()
    return name in WEB_ORCHESTRATOR_PROFILES


def is_delegation_orchestrator(agent_name_or_profile: str) -> bool:
    """Check if the given agent can delegate to subordinates.

    Args:
        agent_name_or_profile: Agent name string.

    Returns:
        True if the agent can delegate.
    """
    if not agent_name_or_profile:
        return False
    name = agent_name_or_profile.lower().strip()
    return name in DELEGATION_ORCHESTRATOR_PROFILES


# ═══════════════════════════════════════════════════════════════
# FIX-021: Standardized Project Dir Key (G-9)
# ═══════════════════════════════════════════════════════════════
# Canonical key is ``_active_project_dir``. Legacy key ``_project_dir``
# is supported as a fallback for backward compatibility.

_PRIMARY_PROJECT_DIR_KEY = "_active_project_dir"
_LEGACY_PROJECT_DIR_KEY = "_project_dir"


def get_project_dir(agent_data: dict) -> str:
    """Get the active project directory from agent data.

    Implements a fallback chain:
    1. ``_active_project_dir`` (canonical, preferred)
    2. ``_project_dir`` (legacy fallback)

    If the legacy key is used, it also sets the canonical key for
    future lookups (auto-migration).

    Args:
        agent_data: The agent.data dict.

    Returns:
        Project directory path, or empty string if not set.
    """
    # Primary: canonical key
    primary = agent_data.get(_PRIMARY_PROJECT_DIR_KEY, "")
    if primary:
        return primary

    # Fallback: legacy key
    legacy = agent_data.get(_LEGACY_PROJECT_DIR_KEY, "")
    if legacy:
        # Auto-migrate: set canonical key for future lookups
        agent_data[_PRIMARY_PROJECT_DIR_KEY] = legacy
        logger.debug(
            f"[PROFILE REGISTRY] Auto-migrated _project_dir → _active_project_dir: {legacy}"
        )
        return legacy

    return ""


def set_project_dir(agent_data: dict, project_dir: str) -> None:
    """Set the active project directory in agent data.

    Sets both canonical and legacy keys for backward compatibility.

    Args:
        agent_data: The agent.data dict.
        project_dir: The project directory path to set.
    """
    agent_data[_PRIMARY_PROJECT_DIR_KEY] = project_dir
    agent_data[_LEGACY_PROJECT_DIR_KEY] = project_dir
