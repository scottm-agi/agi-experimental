"""
Canonical Project-Aware Path Resolution — RCA-318

Single source of truth for resolving file paths in agent tool operations.

Design principles:
1. Relative paths ALWAYS resolve against the active project directory
2. Absolute paths are used as-is
3. Framework root is NEVER a resolution target — agents operate in their project
4. Missing project context → warn user in chat + return error (never silent)
5. Self-correction: if _active_project_dir missing, look up from context
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from python.helpers import projects

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.resolve_agent_path")


class ProjectContextError(RuntimeError):
    """Raised when an agent has no project context — this is always a bug.
    
    Every chat and task is guaranteed to have a project (at minimum 'default').
    If this error fires, something upstream failed to bind the project.
    
    The error message includes diagnostic details so the agent can report
    the issue and attempt to self-correct.
    """
    pass


def resolve_agent_path(path: str | None, agent: "Agent") -> str:
    """Resolve a file path in the context of the agent's active project.
    
    Resolution logic:
    1. None path → ValueError
    2. Empty path → returns the project directory itself
    3. Absolute path → returned as-is
    4. Relative path → joined with the active project directory
    
    Project directory resolution:
    - PRIMARY: agent.data["_active_project_dir"] (set at agent init by _05_project_context_init)
    - SELF-CORRECTION: if missing, look up via get_context_project_name() and SET it
    - FAILURE: raise ProjectContextError with full diagnostics
    
    Args:
        path: The file path to resolve. Can be relative or absolute.
        agent: The agent instance with context and data.
    
    Returns:
        The resolved absolute path.
    
    Raises:
        ValueError: If path is None.
        ProjectContextError: If no project context can be determined (bug).
    """
    if path is None:
        raise ValueError("File path cannot be None")

    # Absolute paths are used as-is — FileGuard enforces boundaries for writes
    if path and os.path.isabs(path):
        return path

    # Get the project directory — passes path for error diagnostics
    project_dir = _resolve_project_dir(agent, requested_path=path)

    # Empty path → return project dir itself
    if not path:
        return project_dir

    # Normalize ./prefix
    if path.startswith("./"):
        path = path[2:]

    # Resolve relative path against project directory — the ONLY resolution target
    return os.path.join(project_dir, path)


def _resolve_project_dir(agent: "Agent", requested_path: str = "") -> str:
    """Get the active project directory, with self-correction.
    
    1. Check agent.data["_active_project_dir"] (fastest, set at init)
    2. If missing, look up from context → project → folder (self-correction)
    3. If both fail, raise ProjectContextError with full diagnostics
    """
    # Fast path: already set at init by _05_project_context_init
    project_dir = agent.data.get("_active_project_dir")
    if project_dir and os.path.isdir(project_dir):
        return project_dir

    # Self-correction: look up from context
    try:
        project_name = projects.get_context_project_name(agent.context)
        if project_name:
            project_dir = projects.get_project_folder(project_name)
            if os.path.isdir(project_dir):
                # System 7 (ITR-44): Clear project-scoped state on change
                old_pd = agent.data.get("_active_project_dir", "")
                if old_pd and old_pd != project_dir:
                    from python.helpers.agent_data_keys import invalidate_project_scoped_keys
                    invalidate_project_scoped_keys(agent.data, project_dir)
                else:
                    # Cache it for future calls
                    agent.data["_active_project_dir"] = project_dir
                logger.warning(
                    f"[RESOLVE PATH] Self-corrected: _active_project_dir was missing "
                    f"for {agent.agent_name}, resolved from context project "
                    f"'{project_name}' → '{project_dir}'. This indicates "
                    f"_05_project_context_init may not have run."
                )
                return project_dir
    except Exception as e:
        logger.error(
            f"[RESOLVE PATH] Failed to self-correct project context "
            f"for {agent.agent_name}: {e}"
        )

    # Total failure — this should NEVER happen in production
    agent_name = getattr(agent, "agent_name", "unknown")
    context_id = getattr(agent.context, "id", "unknown") if agent.context else "no_context"

    error_msg = (
        f"No project context available for agent '{agent_name}' "
        f"(context_id='{context_id}', requested_path='{requested_path}'). "
        f"Every chat/task must have a project bound. "
        f"This is a bug — _05_project_context_init should set "
        f"_active_project_dir at agent creation. "
        f"Check that the chat has a project assigned and that "
        f"the agent_init extension chain ran successfully."
    )
    logger.error(f"[RESOLVE PATH] {error_msg}")

    raise ProjectContextError(error_msg)
