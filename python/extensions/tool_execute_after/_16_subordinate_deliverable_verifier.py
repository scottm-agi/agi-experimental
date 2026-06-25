"""
Extension: Subordinate Deliverable Verifier — tool_execute_after

Anti-hallucination layer that automatically verifies file deliverables
after every call_subordinate return. Injects a filesystem verification
report into the tool result so the orchestrator can see undeniable evidence
of which files actually exist vs. which were hallucinated.

5-Why RCA (2026-04-24, Iteration 152):
  Root cause: Orchestrator trusts subordinate return messages verbatim.
  Frontend agent claimed "I created Navbar.tsx, Footer.tsx, AuditCard.tsx"
  but ZERO files existed on disk. No verification happened between the
  subordinate returning and the orchestrator processing the result.

Hook: tool_execute_after for call_subordinate
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

from python.helpers.extension import Extension
from python.helpers.requirements_ledger import get_delegation_ledger_for_gate

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.extensions.subordinate_deliverable_verifier")


class SubordinateDeliverableVerifier(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch"})

    """Verify file deliverables after call_subordinate returns.

    Extracts claimed file paths from the subordinate's return message,
    checks them against the filesystem, and injects a verification
    report into the tool result.
    """

    async def execute(self, loop_data: Optional["LoopData"] = None, **kwargs) -> None:
        # Only activate for call_subordinate tool
        tool_name = kwargs.get("tool_name", "")
        if tool_name not in ("call_subordinate", "call_subordinate_batch"):
            return

        tool_result = kwargs.get("tool_result", "")
        if not tool_result or not isinstance(tool_result, str):
            return

        agent = self.agent
        if not agent:
            return

        # Import the verifier utility
        from python.helpers.subordinate_deliverable_verifier import (
            extract_claimed_files,
            verify_deliverables,
        )

        # Extract claimed files from the subordinate's return message
        claimed_files = extract_claimed_files(tool_result)

        if not claimed_files:
            logger.debug(
                "[DELIVERABLE VERIFIER] No file paths found in subordinate result — skipping"
            )
            return

        # Determine the project directory
        project_dir = self._get_project_dir(agent)
        if not project_dir:
            logger.warning(
                "[DELIVERABLE VERIFIER] Could not determine project directory — skipping"
            )
            return

        # Verify deliverables against filesystem
        report = verify_deliverables(project_dir, claimed_files)

        # Log the results
        if report.missing:
            logger.warning(
                f"[DELIVERABLE VERIFIER] ❌ {len(report.missing)} claimed files MISSING "
                f"out of {len(claimed_files)} total — pass rate: {report.pass_rate:.0%}"
            )
            for f in report.missing:
                logger.warning(f"  MISSING: {f}")
        else:
            logger.info(
                f"[DELIVERABLE VERIFIER] ✅ All {len(claimed_files)} claimed files verified"
            )

        # Inject verification report into the tool result
        verification_text = report.format()
        if hasattr(loop_data, "tool_result") and loop_data is not None:
            # Append to the existing tool result
            loop_data.tool_result = f"{tool_result}\n\n{verification_text}"
        elif kwargs.get("_response_ref"):
            # Some frameworks pass a mutable response ref
            kwargs["_response_ref"]["result"] = f"{tool_result}\n\n{verification_text}"

        # Store verification in agent data for supervisor visibility
        agent.data["_last_deliverable_verification"] = {
            "verified": report.verified,
            "missing": report.missing,
            "pass_rate": report.pass_rate,
        }

    def _get_project_dir(self, agent: "Agent") -> Optional[str]:
        """Determine the active project directory from agent context.

        Checks multiple sources in priority order:
        1. agent.data["_active_project_dir"] — set by project init
        2. agent.data["_sandbox_dir"] — set by FileGuard
        3. /agix/usr/projects/{project_name} — derived from agent name
        """
        # Source 1: Explicit project dir
        project_dir = agent.data.get("_active_project_dir")
        if project_dir and os.path.isdir(project_dir):
            return project_dir

        # Source 2: Sandbox dir from FileGuard
        sandbox_dir = agent.data.get("_sandbox_dir")
        if sandbox_dir and os.path.isdir(sandbox_dir):
            return sandbox_dir

        # Source 3: Try to derive from project name in delegation context
        ledger = get_delegation_ledger_for_gate(agent.data)
        if ledger:
            for entry in reversed(ledger):
                msg = entry.get("message_summary", "")
                # Look for project directory references in delegation messages
                if "/agix/usr/projects/" in msg:
                    import re
                    match = re.search(r"/agix/usr/projects/[\w\-]+", msg)
                    if match and os.path.isdir(match.group()):
                        return match.group()

        # Source 4: Check parent agent's project dir
        if hasattr(agent, "context") and agent.context:
            parent = getattr(agent.context, "parent_agent", None)
            if parent:
                parent_dir = getattr(parent, "data", {}).get("_active_project_dir")
                if parent_dir and os.path.isdir(parent_dir):
                    return parent_dir

        return None
