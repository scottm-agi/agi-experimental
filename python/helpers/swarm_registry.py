"""
Swarm Registry — configurable orchestrator delegation boundaries.

Loads the _swarm_registry.json from agents/ to determine which profiles each
orchestrator is allowed to delegate to. Orchestrators can always reach other
orchestrators (inter-orchestrator routing). Non-orchestrator profiles with
no registry entry have unrestricted delegation (returns None).

Routing rules:
    - Orchestrators → their swarm members + other orchestrators (always)
    - Default → orchestrators + independent agents (not in any swarm)
    - Unregistered profiles → unrestricted (no swarm defined)

Usage:
    from python.helpers.swarm_registry import is_profile_allowed, get_allowed_profiles
    
    # Check if multiagentdev can call hacker
    is_profile_allowed("multiagentdev", "hacker")  # False
    
    # Orchestrators can always call each other
    is_profile_allowed("alex", "multiagentdev")  # True (inter-orchestrator)
    
    # Filter prompt profiles for an agent
    filter_profiles_for_agent("multiagentdev", all_profiles)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Optional

from python.helpers import files

logger = logging.getLogger("agix.swarm_registry")

# Thread-safe cache for the loaded registry
_registry_cache: dict[str, set[str]] | None = None
_orchestrators_cache: set[str] | None = None
_cache_lock = threading.Lock()

REGISTRY_FILENAME = "_swarm_registry.json"


def _get_registry_path() -> str:
    """Return absolute path to the swarm registry JSON."""
    return files.get_abs_path("agents", REGISTRY_FILENAME)


def load_swarm_registry() -> dict[str, set[str]]:
    """Load the swarm registry from disk.

    Returns:
        Dict mapping profile → set of allowed subordinate profiles.
        Only includes profiles that have explicit swarm definitions.
    """
    global _registry_cache

    with _cache_lock:
        if _registry_cache is not None:
            return _registry_cache

        registry_path = _get_registry_path()
        if not os.path.exists(registry_path):
            logger.warning(
                f"[SWARM REGISTRY] No registry file found at {registry_path}. "
                f"All delegation is unrestricted."
            )
            _registry_cache = {}
            return _registry_cache

        try:
            with open(registry_path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[SWARM REGISTRY] Failed to load {registry_path}: {e}")
            _registry_cache = {}
            return _registry_cache

        swarms = data.get("swarms", {})
        result: dict[str, set[str]] = {}
        for profile, config in swarms.items():
            allowed = config.get("allowed_profiles", [])
            result[profile] = set(allowed)
            logger.info(
                f"[SWARM REGISTRY] Loaded swarm '{profile}': "
                f"{len(allowed)} allowed profiles"
            )

        _registry_cache = result
        return _registry_cache


def get_orchestrators() -> set[str]:
    """Return the set of orchestrator profile names.

    Orchestrators are defined in the top-level 'orchestrators' key in the
    registry JSON. They have a special routing privilege: any orchestrator
    can always delegate to any other orchestrator.
    """
    global _orchestrators_cache

    with _cache_lock:
        if _orchestrators_cache is not None:
            return _orchestrators_cache

    registry_path = _get_registry_path()
    try:
        with open(registry_path, "r") as f:
            data = json.load(f)
        _orchestrators_cache = set(data.get("orchestrators", []))
    except (json.JSONDecodeError, IOError, FileNotFoundError):
        _orchestrators_cache = set()

    return _orchestrators_cache


def invalidate_cache() -> None:
    """Invalidate the in-memory cache. Forces next call to reload from disk."""
    global _registry_cache, _orchestrators_cache
    with _cache_lock:
        _registry_cache = None
        _orchestrators_cache = None


def get_allowed_profiles(orchestrator_profile: str) -> Optional[set[str]]:
    """Get the set of allowed subordinate profiles for an agent.

    Args:
        orchestrator_profile: The profile name of the calling agent.

    Returns:
        A set of allowed profile names, or None if the profile is unrestricted
        (not defined in the registry, meaning any delegation is allowed).
    """
    registry = load_swarm_registry()
    allowed = registry.get(orchestrator_profile)
    if allowed is None:
        return None  # Unrestricted
    return set(allowed)  # Return a copy to prevent mutation


def is_profile_allowed(
    orchestrator_profile: str, target_profile: str
) -> bool:
    """Check if an agent is allowed to delegate to a target profile.

    Special rule: orchestrators can always delegate to other orchestrators
    (inter-orchestrator routing), regardless of swarm config.

    Args:
        orchestrator_profile: The profile of the calling agent.
        target_profile: The profile being delegated to.

    Returns:
        True if allowed (or if the caller is unrestricted).
        False if the caller has a defined swarm and the target is not in it.
    """
    allowed = get_allowed_profiles(orchestrator_profile)
    if allowed is None:
        return True  # Unrestricted — no swarm defined

    # Inter-orchestrator routing: orchestrators can always call each other
    orchestrators = get_orchestrators()
    if orchestrator_profile in orchestrators and target_profile in orchestrators:
        return True

    return target_profile in allowed


def filter_profiles_for_agent(
    agent_profile: str, all_profiles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Filter the list of agent profiles to only those allowed for this agent.

    Used by the prompt plugin to hide unauthorized profiles from the LLM.
    Orchestrators always see other orchestrators in addition to their swarm.

    Args:
        agent_profile: The profile of the calling agent.
        all_profiles: Full list of profile dicts (each with "name" and "context").

    Returns:
        Filtered list — only allowed profiles for registered agents,
        or all profiles for unregistered agents.
    """
    allowed = get_allowed_profiles(agent_profile)
    if allowed is None:
        return all_profiles  # Unrestricted — return all

    # Inter-orchestrator routing: orchestrators also see other orchestrators
    orchestrators = get_orchestrators()
    if agent_profile in orchestrators:
        visible = allowed | orchestrators
    else:
        visible = allowed

    return [p for p in all_profiles if p.get("name") in visible]


def add_profile_to_swarm(orchestrator_profile: str, new_profile: str) -> None:
    """Add a profile to an orchestrator's allowed swarm.

    Persists the change to disk and invalidates the cache.

    Args:
        orchestrator_profile: The orchestrator to modify.
        new_profile: The profile to add to the swarm.
    """
    registry_path = _get_registry_path()

    with open(registry_path, "r") as f:
        data = json.load(f)

    swarms = data.setdefault("swarms", {})
    swarm = swarms.setdefault(orchestrator_profile, {
        "description": f"Auto-created swarm for {orchestrator_profile}",
        "allowed_profiles": [],
    })

    profiles = swarm.setdefault("allowed_profiles", [])
    if new_profile not in profiles:
        profiles.append(new_profile)

    with open(registry_path, "w") as f:
        json.dump(data, f, indent=4)

    invalidate_cache()
    logger.info(
        f"[SWARM REGISTRY] Added '{new_profile}' to swarm '{orchestrator_profile}'"
    )


def remove_profile_from_swarm(orchestrator_profile: str, profile: str) -> None:
    """Remove a profile from an orchestrator's allowed swarm.

    Persists the change to disk and invalidates the cache.

    Args:
        orchestrator_profile: The orchestrator to modify.
        profile: The profile to remove from the swarm.
    """
    registry_path = _get_registry_path()

    with open(registry_path, "r") as f:
        data = json.load(f)

    swarms = data.get("swarms", {})
    swarm = swarms.get(orchestrator_profile)
    if swarm is None:
        return

    profiles = swarm.get("allowed_profiles", [])
    if profile in profiles:
        profiles.remove(profile)

    with open(registry_path, "w") as f:
        json.dump(data, f, indent=4)

    invalidate_cache()
    logger.info(
        f"[SWARM REGISTRY] Removed '{profile}' from swarm '{orchestrator_profile}'"
    )


def validate_registry_canary() -> dict:
    """Boot-time canary: validate the swarm registry is loaded and sane.

    Called at startup to verify the bind-mounted _swarm_registry.json is
    present and contains expected orchestrators and swarms. This catches
    container/host desync early — before delegations start failing silently.

    Returns:
        Dict with keys:
        - valid: bool — True if registry is healthy
        - orchestrators_count: int — number of orchestrators found
        - swarms_count: int — number of swarms found
        - issues: list[str] — any problems found
    """
    issues: list[str] = []

    registry = load_swarm_registry()
    orchestrators = get_orchestrators()

    swarms_count = len(registry)
    orchestrators_count = len(orchestrators)

    if orchestrators_count == 0:
        issues.append("No orchestrators defined in registry")
    if swarms_count == 0:
        issues.append("No swarms defined in registry")

    # Verify each orchestrator has a matching swarm entry
    for orch in orchestrators:
        if orch not in registry:
            issues.append(f"Orchestrator '{orch}' has no swarm entry")

    # Verify each swarm has at least one allowed profile
    for swarm_name, allowed in registry.items():
        if not allowed:
            issues.append(f"Swarm '{swarm_name}' has no allowed profiles")

    valid = len(issues) == 0

    if valid:
        logger.info(
            f"[SWARM CANARY] Registry healthy: {orchestrators_count} orchestrators, "
            f"{swarms_count} swarms"
        )
    else:
        logger.error(
            f"[SWARM CANARY] Registry INVALID: {issues}"
        )

    return {
        "valid": valid,
        "orchestrators_count": orchestrators_count,
        "swarms_count": swarms_count,
        "issues": issues,
    }
