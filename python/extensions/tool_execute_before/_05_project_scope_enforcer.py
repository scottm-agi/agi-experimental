from __future__ import annotations
"""
Project Scope Enforcer Extension (FIX-3, Iteration 11b)

Intercepts ALL file-writing tools and validates that output paths are within
the active project sandbox. Complements _15_project_path_enforcer.py which
handles code_execution_tool shell commands.

Root cause: FileGuard was opt-in per-tool (only 3 of 100+ tools imported it).
This extension provides universal enforcement at the framework level via the
tool_execute_before hook.

Coverage:
- write_to_file: validates 'path' arg
- replace_in_file: validates 'path' arg  
- generate_image: validates 'output_path' arg (when added)
- Any future tool with path/file arguments

Exemptions:
- call_subordinate: orchestration tool, no file paths
- services_mgt: scavenger needs cross-project access by design
- code_execution_tool: already covered by _15_project_path_enforcer.py
- response: terminal tool, no file paths
- knowledge_tool: reads from knowledge base, not project-scoped
"""

import os
import logging
from typing import Any, Optional
from python.helpers.extension import Extension
from python.helpers.tool import Response


logger = logging.getLogger("agix.project_scope_enforcer")

# Tools exempted from project-scope validation.
# Each exemption has a documented reason.
EXEMPTED_TOOLS = {
    "call_subordinate",       # Orchestration — no file paths
    "call_subordinate_batch", # Orchestration — no file paths
    "services_mgt",           # Scavenger needs cross-project access
    "code_execution_tool",    # Handled by _15_project_path_enforcer.py
    "code_execution",         # Alias for code_execution_tool
    "response",               # Terminal tool — no file paths
    "knowledge_tool",         # Reads KB, not project-scoped
    "task_done",              # Control flow, no file paths
    "online_knowledge_tool",  # Web search, no file paths
    "docs_lookup",            # Documentation lookup, no file paths
    "web_search",             # Web search, no file paths
    "setup_project",          # Creates projects — needs unrestricted access
    "recall_memories",        # Memory system, not project-scoped
    "save_memory",            # Memory system, not project-scoped
    "maintain_memory_bank",   # Memory bank — not project-scoped
    "read_file",              # Read-only, non-destructive — scope is for writes (RCA-324)
    "save_deliverable",       # Framework-managed deliverable path — inherently safe
    "generate_guid",          # No file paths at all
    "sequential_thinking",    # MCP reasoning tool — no file paths
    "sequentialthinking",     # Alias for sequential_thinking
}

# Tool argument names that contain file paths to validate
PATH_ARG_NAMES = {"path", "file_path", "output_path", "target_path", "filepath"}

# Paths that are always allowed regardless of project scope
ALWAYS_ALLOWED_PREFIXES = (
    "/tmp/",         # Scratch space (sandboxed by Docker)
    "/tmp",          # Exact /tmp
)


def validate_tool_paths(
    tool_name: str,
    tool_args: dict,
    project_dir: Optional[str],
) -> Optional[str]:
    """Validate that all file paths in tool args are within the active project.

    This is the core validation logic, extracted for testability.

    Args:
        tool_name: Name of the tool being executed.
        tool_args: Arguments passed to the tool.
        project_dir: Active project directory (absolute path), or None.

    Returns:
        None if all paths are valid (allowed).
        Error message string if a path violation is detected (blocked).
    """
    # Exempted tools bypass validation
    if tool_name in EXEMPTED_TOOLS:
        return None

    # No active project → graceful degradation (allow everything)
    if not project_dir:
        return None

    # Normalize project dir (remove trailing slash)
    project_dir = project_dir.rstrip("/")

    # Check each path argument
    for arg_name in PATH_ARG_NAMES:
        path_value = tool_args.get(arg_name)
        if not path_value or not isinstance(path_value, str):
            continue

        # Always-allowed prefixes
        if path_value.startswith(ALWAYS_ALLOWED_PREFIXES):
            continue

        # ADR-012 / ISS-4: Resolve relative paths against the PROJECT directory.
        # Previously used files.get_abs_path() which resolves to FRAMEWORK root
        # (/agix/), causing false-positive blocks for legitimate project paths.
        # The project_dir is already available — resolve directly against it.
        resolved = path_value
        if not path_value.startswith("/"):
            if project_dir:
                resolved = os.path.join(project_dir, path_value)
            else:
                # No project_dir + relative path → can't resolve, skip validation
                continue

        # Resolve symlinks and normalize
        resolved = os.path.normpath(resolved)

        # Check if path is within the project
        if not resolved.startswith(project_dir + "/") and resolved != project_dir:
            # WORKTREE EXCEPTION: Build tasks use worktree directories (build-*)
            # that are separate from the base clone (repo-*). The worktree is a
            # valid write target because it's a git worktree of the base clone.
            # Allow writes to build-* dirs under the same projects parent.
            projects_parent = os.path.dirname(project_dir)
            resolved_parent = os.path.dirname(resolved)
            worktree_dir_name = os.path.basename(resolved_parent) if resolved_parent != resolved else os.path.basename(resolved)
            # Walk up to find the top-level project dir in the resolved path
            rel_to_projects = os.path.relpath(resolved, projects_parent)
            top_dir = rel_to_projects.split(os.sep)[0] if rel_to_projects else ""
            if top_dir.startswith("build-") and resolved.startswith(projects_parent + "/"):
                # This is a worktree directory under the same projects parent — allow it
                continue

            return (
                f"⛔ Path OUTSIDE active project sandbox: '{path_value}' "
                f"(resolved to '{resolved}') "
                f"is not within '{project_dir}/'. "
                f"Tool '{tool_name}' must only write within the active project. "
                f"Fix: use a path relative to the project root, or an absolute "
                f"path starting with '{project_dir}/'."
            )

    return None


class ProjectScopeEnforcer(Extension):
    """Framework-level extension that enforces project-scope file writing.

    Runs as tool_execute_before — intercepts every tool call and validates
    that file paths are within the active project sandbox.
    """

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        """Validate tool paths before execution.

        Returns:
            None if allowed (tool proceeds normally).
            Response with error if path violation detected (tool is blocked).
        """
        if not tool_args or not tool_name:
            return None

        # RCA-324: Re-resolve active project from context EVERY time.
        # The cached _active_project_dir in agent.data can become stale
        # when the project changes mid-session (e.g., old project deleted,
        # new project created). This caused the orchestrator to have
        # _active_project_dir='default' when the real project was different,
        # blocking legitimate reads of subordinate deliverables.
        project_dir = self._resolve_project_dir()

        result = validate_tool_paths(tool_name, tool_args, project_dir)
        if result:
            logger.warning(
                f"[SCOPE_ENFORCER] BLOCKED {tool_name}: {result}"
            )



            return Response(
                message=result,
                break_loop=False,
            )

        return None

    def _resolve_project_dir(self) -> Optional[str]:
        """Resolve the active project directory, refreshing from context.

        Priority:
        1. Resolve from AgentContext (freshest, canonical source)
        2. Fall back to agent.data cache (for subordinates without context)
        """
        # Try resolving from context (freshest source)
        try:
            from python.helpers import projects
            ctx = getattr(self.agent, 'context', None)
            if ctx:
                project_name = projects.get_context_project_name(ctx)
                if project_name:
                    import os
                    project_dir = projects.get_project_folder(project_name)
                    if os.path.isdir(project_dir):
                        # Update the cache so other extensions also benefit
                        if self.agent.data.get("_active_project_dir") != project_dir:
                            # System 7 (ITR-44): Clear project-scoped state on change
                            old_pd = self.agent.data.get("_active_project_dir", "")
                            if old_pd and old_pd != project_dir:
                                from python.helpers.agent_data_keys import invalidate_project_scoped_keys
                                cleared = invalidate_project_scoped_keys(self.agent.data, project_dir)
                                if cleared:
                                    logger.debug(
                                        f"[SCOPE_ENFORCER] System 7: Cleared {len(cleared)} "
                                        f"project-scoped keys: {old_pd} → {project_dir}"
                                    )
                            else:
                                self.agent.data["_active_project_dir"] = project_dir
                            logger.debug(
                                f"[SCOPE_ENFORCER] Refreshed _active_project_dir "
                                f"from context: {old_pd} → {project_dir}"
                            )
                        return project_dir
        except Exception:
            pass

        # Fall back to cached value
        project_dir = (
            self.agent.data.get("_active_project_dir")
            or self.agent.data.get("active_project", {}).get("dir")
        )
        if isinstance(project_dir, dict):
            project_dir = project_dir.get("dir")
        return project_dir
