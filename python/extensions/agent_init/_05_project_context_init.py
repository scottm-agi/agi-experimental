"""
Project Context Init Extension (RCA-300)

Sets _active_project_dir on agent.data at agent creation time by resolving
from the shared AgentContext's project binding.

This is the canonical top-down approach: project context flows from the
chat → context → agent.data at init. All agents (top-level AND subordinates)
get project context automatically because they share the same AgentContext.

Replaces the old _01_context_project_cleanup.py which aggressively cleared
project state at every message loop start. With init-time resolution:
- Each new agent gets project dir from the CURRENT context state
- If the project changes between prompts, the context itself changes
- No need to retroactively clear stale state
"""
from __future__ import annotations
import os
import logging

from python.helpers.extension import Extension

logger = logging.getLogger("agix.project_context_init")


class ProjectContextInit(Extension):
    """Set _active_project_dir on agent.data from AgentContext at init."""

    async def execute(self, **kwargs):
        await _execute_init(self.agent)


async def _execute_init(agent):
    """Core logic — extracted for testability."""
    await execute(agent)


async def execute(agent, **kwargs):
    """Resolve project dir from AgentContext and set on agent.data.

    Uses the same context→project binding that resolve_project_dir_from_context
    uses, but runs at init time so _active_project_dir is available from the
    very first tool call.
    """
    from python.helpers import projects

    try:
        project_name = projects.get_context_project_name(agent.context)
        if not project_name:
            logger.debug(
                f"[PROJECT INIT] No project in context for {agent.agent_name}"
            )
            return

        project_dir = projects.get_project_folder(project_name)
        if os.path.isdir(project_dir):
            # System 7 (ITR-44): Clear project-scoped state if project changed
            old_project = agent.data.get("_active_project_dir", "")
            if old_project and old_project != project_dir:
                from python.helpers.agent_data_keys import invalidate_project_scoped_keys
                cleared = invalidate_project_scoped_keys(agent.data, project_dir)
                if cleared:
                    logger.info(
                        f"[PROJECT INIT] System 7: Cleared {len(cleared)} project-scoped "
                        f"keys on project change: {old_project} → {project_dir}"
                    )
            else:
                agent.data["_active_project_dir"] = project_dir
            logger.info(
                f"[PROJECT INIT] Set _active_project_dir='{project_dir}' "
                f"for {agent.agent_name} (from context project '{project_name}')"
            )
        else:
            logger.debug(
                f"[PROJECT INIT] Project dir '{project_dir}' does not exist "
                f"for project '{project_name}' — skipping"
            )
    except Exception as e:
        logger.debug(f"[PROJECT INIT] Could not resolve project context: {e}")
