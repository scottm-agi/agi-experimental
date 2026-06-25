"""State Persistence — Critical agent.data keys that survive Docker restarts.

FIX-030: All counters are LOST on Docker restart, causing the agent to
repeat its failure spiral from scratch. This module persists the critical
subset of agent.data to disk and restores it on restart.

Architecture:
    - CRITICAL_KEYS: list of agent.data keys that MUST survive
    - persist_critical_state(agent_data, project_dir): writes to .agix.proj/critical_state.json
    - restore_critical_state(agent_data, project_dir): reads and merges
    - should_persist(agent_data): returns True if any critical key has non-default value
    - Uses atomic write (write to .tmp, then os.rename) for crash safety
    - Handles RetryBudgetManager serialization via to_dict()/from_dict()
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("agix.state_persistence")

# Keys that MUST survive Docker restarts
CRITICAL_KEYS = [
    "_build_attempt_count",
    "_build_failure_propagated",
    "_rework_cycle_count",
    "_quality_degraded",
    "build_fix_exhausted",
    "_phase_delegation_attempts",
    "_same_message_cumulative_count",
    "_retry_budget",
]

# State file location within the project
_STATE_FILENAME = "critical_state.json"
_STATE_DIR = ".agix.proj"

# Default values for critical keys (used by should_persist)
_DEFAULTS: dict[str, Any] = {
    "_build_attempt_count": 0,
    "_build_failure_propagated": None,
    "_rework_cycle_count": 0,
    "_quality_degraded": False,
    "build_fix_exhausted": False,
    "_phase_delegation_attempts": {},
    "_same_message_cumulative_count": 0,
    "_retry_budget": None,
}


def _get_state_path(project_dir: str) -> str:
    """Get the absolute path to the critical state file."""
    return os.path.join(project_dir, _STATE_DIR, _STATE_FILENAME)


def should_persist(agent_data: dict) -> bool:
    """Check if any critical key has a non-default value worth persisting.

    Args:
        agent_data: The agent's data dict.

    Returns:
        True if at least one critical key has a non-default value.
    """
    for key in CRITICAL_KEYS:
        val = agent_data.get(key)
        default = _DEFAULTS.get(key)
        if val is not None and val != default:
            # Special case: empty containers are default
            if isinstance(val, (dict, list)) and len(val) == 0:
                continue
            return True
    return False


def persist_critical_state(agent_data: dict, project_dir: str) -> None:
    """Persist critical agent.data keys to disk.

    Writes to .agix.proj/critical_state.json using atomic write
    (write to .tmp, then os.rename) for crash safety.

    Args:
        agent_data: The agent's data dict.
        project_dir: Absolute path to the project directory.
    """
    if not project_dir:
        return

    state_dir = os.path.join(project_dir, _STATE_DIR)
    os.makedirs(state_dir, exist_ok=True)

    state = {}
    for key in CRITICAL_KEYS:
        val = agent_data.get(key)
        if val is None:
            continue

        # Special handling for RetryBudgetManager
        if key == "_retry_budget":
            try:
                from python.helpers.retry_budget import RetryBudgetManager

                if isinstance(val, RetryBudgetManager):
                    state[key] = {"__type__": "RetryBudgetManager", "data": val.to_dict()}
                elif isinstance(val, dict):
                    state[key] = {"__type__": "RetryBudgetManager", "data": val}
                continue
            except Exception as e:
                logger.debug(f"RetryBudgetManager serialization failed: {e}")
                continue

        # Standard JSON-serializable values
        try:
            json.dumps(val)  # Verify serializable
            state[key] = val
        except (TypeError, ValueError) as e:
            logger.debug(f"Skipping non-serializable key {key}: {e}")

    if not state:
        return

    state_path = _get_state_path(project_dir)
    tmp_path = state_path + ".tmp"

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)

        os.rename(tmp_path, state_path)
        logger.info(
            f"Persisted {len(state)} critical state keys to {state_path}"
        )
    except Exception as e:
        logger.warning(f"Failed to persist critical state: {e}")
        # Clean up tmp file if rename failed
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def restore_critical_state(agent_data: dict, project_dir: str) -> None:
    """Restore critical state from disk into agent_data.

    Reads from .agix.proj/critical_state.json and merges into agent_data.
    Only restores keys that are NOT already set in agent_data (no overwrite).

    Args:
        agent_data: The agent's data dict (will be mutated).
        project_dir: Absolute path to the project directory.
    """
    if not project_dir:
        return

    state_path = _get_state_path(project_dir)
    if not os.path.isfile(state_path):
        return

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read critical state: {e}")
        return

    if not isinstance(state, dict):
        return

    restored = 0
    for key in CRITICAL_KEYS:
        if key not in state:
            continue

        val = state[key]

        # Special handling for RetryBudgetManager
        if key == "_retry_budget" and isinstance(val, dict):
            if val.get("__type__") == "RetryBudgetManager":
                try:
                    from python.helpers.retry_budget import RetryBudgetManager

                    agent_data[key] = RetryBudgetManager.from_dict(val["data"])
                    restored += 1
                except Exception as e:
                    logger.debug(f"RetryBudgetManager deserialization failed: {e}")
                continue

        agent_data[key] = val
        restored += 1

    if restored > 0:
        logger.info(
            f"Restored {restored} critical state keys from {state_path}"
        )
