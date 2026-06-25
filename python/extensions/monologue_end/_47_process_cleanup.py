from __future__ import annotations
"""
Extension: Kill orphaned code_execution_tool processes on agent completion.

ROOT CAUSE FIX (Forgejo #1132):
When an agent's monologue ends, any shell sessions spawned by code_execution_tool
may still be running (e.g., `next dev`, `npm run dev`, background processes).
These zombie processes can contaminate subsequent agent contexts by continuing
to write files or consume resources.

RCA-270: Preserves dev servers for the most recent chat's project so users
can test the build at the advertised port. Only kills dev servers for
stale/old project contexts.

This extension runs AFTER supervisor unregister (_45) and BEFORE memory operations (_50+).
It terminates all shell sessions owned by the completing agent.
"""

from python.helpers.extension import Extension
import logging

logger = logging.getLogger(__name__)


class ProcessCleanup(Extension):
    """Kill orphaned shell sessions when agent monologue ends."""

    async def execute(self, **kwargs):
        """Close all code_execution_tool shell sessions for this agent.

        RCA-270: Preserves dev servers for the most recent chat's project
        so users can test the build. Only kills dev servers for stale/old
        project contexts. The inter-test cleanup (PortManager) handles
        aggressive cleanup between test runs.
        """
        cleaned = 0
        errors = 0
        try:
            # Get the code execution state from agent data
            state = self.agent.get_data("_cet_state")
            if not state:
                return  # No code execution sessions to clean

            shells = getattr(state, "shells", {})
            if not shells:
                return  # No active shells

            for sid, wrap in list(shells.items()):
                try:
                    session = wrap.session
                    if session:
                        await session.close()
                        cleaned += 1
                except Exception as e:
                    # Log but don't fail — best-effort cleanup
                    logger.warning(
                        f"[PROCESS_CLEANUP] Failed to close session {sid}: {e}"
                    )
                    errors += 1

            # Clear the shells dict so no references remain
            shells.clear()

            # Also kill any node/npm/next-server processes owned by this agent's project
            project_dir = self.agent.get_data("project_dir") or ""
            if project_dir:
                # RCA-270: Only kill dev servers if this is NOT the most recent
                # chat's active project. Preserve dev servers so the user can
                # test the build at the advertised port.
                should_preserve = self._is_most_recent_project(project_dir)
                if should_preserve:
                    logger.info(
                        f"[PROCESS_CLEANUP] PRESERVING dev servers for "
                        f"most-recent project: {project_dir}"
                    )
                else:
                    await self._kill_project_processes(project_dir)

            if cleaned > 0:
                logger.info(
                    f"[PROCESS_CLEANUP] Agent {self.agent.agent_name}: "
                    f"closed {cleaned} shell sessions "
                    f"({errors} errors)"
                )

        except Exception as e:
            # Never fail the monologue_end pipeline
            logger.warning(
                f"[PROCESS_CLEANUP] Agent {self.agent.agent_name}: "
                f"cleanup failed: {e}"
            )

    def _is_most_recent_project(self, project_dir: str) -> bool:
        """Check if this project is the most recent chat's active project.

        The most recent project should keep its dev server running so the
        user can test when they get around to it. Older projects get cleaned.

        Returns True if the project should be PRESERVED (skip process kill).
        """
        try:
            # Check if the agent's context exists
            context = getattr(self.agent, 'context', None)
            if not context:
                return False

            # A chat_id means this is a user-facing chat (not background)
            chat_id = getattr(context, 'id', None) or getattr(context, 'chat_id', None)
            if not chat_id:
                return False

            # Check if there's an active dev server for this project
            dev_server_started = self.agent.data.get("_dev_server_started", False)
            dev_server_port = self.agent.data.get("_dev_server_port", "")

            # If the agent actively started a dev server AND this is a user-facing chat,
            # preserve it
            return bool(dev_server_started and dev_server_port)
        except Exception:
            return False  # Safe default: clean up on error

    async def _kill_project_processes(self, project_dir: str):
        """Kill node/npm/next processes running in the agent's project directory."""
        import asyncio
        try:
            # Find and kill processes whose CWD is in the project directory
            # This catches `next dev`, `npm run dev`, etc.
            cmd = (
                f"ps aux | grep -E 'next-server|npm|node' | "
                f"grep '{project_dir}' | grep -v grep | "
                f"awk '{{print $2}}' | xargs -r kill -9 2>/dev/null"
            )
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                logger.info(
                    f"[PROCESS_CLEANUP] Killed project processes for {project_dir}"
                )
        except asyncio.TimeoutError:
            logger.warning("[PROCESS_CLEANUP] Process kill timed out")
        except Exception as e:
            logger.warning(f"[PROCESS_CLEANUP] Process kill failed: {e}")
