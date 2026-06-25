"""Code Self-Check Tool — explicit structural quality check for code agents.

Calls run_self_check_suite() which uses the SAME raw validators as
the orchestrator completion gate. Registered in code_audit category.
"""
from __future__ import annotations

from python.helpers.tool import Tool, Response
from python.helpers.self_check_suite import run_self_check_suite


class CodeSelfCheck(Tool):
    """Tool for running structural self-checks on the current project.

    Calls the same raw validators used by the orchestrator completion gate
    (check_fetch_route_completeness, check_nav_link_consistency, etc.)
    and reports results in a structured format.
    """

    async def execute(self, **kwargs) -> Response:
        project_path = kwargs.get("path", "").strip()
        if not project_path:
            project_path = self.agent.data.get("project_dir", "")
            if not project_path:
                project_path = self.agent.data.get("_active_project_dir", "")

        if not project_path:
            return Response(
                message="Error: No project path. Use path argument or set project_dir.",
                break_loop=False,
            )

        report = run_self_check_suite(project_path, agent_data=self.agent.data)
        return Response(
            message=report.format_markdown(),
            break_loop=False,
            additional={"success": report.all_passed},
        )
