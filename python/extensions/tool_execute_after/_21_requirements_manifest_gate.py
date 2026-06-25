"""
Requirements Manifest Gate — tool_execute_after extension.

Runs AFTER the 'response' tool fires on orchestrator agents.
Checks the project's `requirements_manifest.md` for unchecked items
and blocks delivery if requirements are incomplete.

This is complementary to _22_multiagentdev_completion_gate.py:
- _22 checks the in-memory requirements LEDGER (delegation coverage)
- _21 checks the on-disk requirements MANIFEST (deliverable completeness)

Root cause (Iteration 211 RCA):
    Agent delivered "complete" responses with fabricated content and
    missing features because no gate checked the requirements manifest
    for unchecked items before allowing the response to pass.

Hooks into: tool_execute_after (order 21 — before completion gate)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from python.helpers.extension import Extension
from python.helpers.requirements_manifest import (
    check_manifest_completeness,
    format_manifest_warning,
)
from python.helpers.requirements_ledger import get_delegation_ledger_for_gate

from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.requirements_manifest_gate")

# Agents that use this gate (orchestrators with project context)
# FIX-020: Use centralized profile registry instead of hardcoded names
from python.helpers.profile_registry import DELEGATION_ORCHESTRATOR_PROFILES as ORCHESTRATOR_AGENTS

# Maximum blocks before circuit-breaker allows through
MAX_MANIFEST_BLOCKS = 3

# S3 (RCA MSR_1777396305 RC-3): Minimum completion percentage before allowing
# delivery. At ≥80%, the gate passes (good enough for review). Below 80%,
# the gate blocks with specific missing items listed.
MIN_COMPLETION_THRESHOLD = 80


class RequirementsManifestGate(Extension):
    # Context-aware: orchestrator only, response tool
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"response"})

    """Block premature delivery when requirements_manifest.md has unchecked items."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        # Only intercept the 'response' tool
        if tool_name.lower() != "response":
            return

        # Only handle orchestrator agents
        agent_name = getattr(self.agent, "agent_name", "").lower()
        if agent_name not in ORCHESTRATOR_AGENTS:
            return



        # Circuit breaker: max N blocks to prevent loops
        if gate_check(self.agent.data, "manifest_gate"):
            return

        # Find project directory
        project_dir = self._find_project_dir()
        if not project_dir:
            return

        # Check manifest — pass project_dir for source-code auto-verification
        # RCA-258: Without this, the gate reports 0% because no agent updates
        # the manifest checkboxes. Auto-verification greps source code for
        # the literal values (URLs, env vars, entities) listed in the manifest.
        manifest_path = os.path.join(project_dir, "requirements_manifest.md")
        result = check_manifest_completeness(
            manifest_path, project_dir=project_dir, agent_data=self.agent.data
        )

        if result is None:
            # No manifest or no checkboxes — pass through
            return

        if result["complete"]:
            logger.info(
                f"[MANIFEST GATE] All {result['total']} requirements complete "
                f"in {project_dir}"
            )
            return

        # S3: Check if completion percentage meets threshold (80%)
        # At ≥80%, allow through — close enough for delivery review
        completion_pct = result.get("completion_pct", 0)
        if completion_pct >= MIN_COMPLETION_THRESHOLD:
            logger.info(
                f"[MANIFEST GATE] {result['done']}/{result['total']} "
                f"({completion_pct}%) — above {MIN_COMPLETION_THRESHOLD}% "
                f"threshold, allowing through"
            )
            return

        # Below threshold — block delivery with specific missing items
        warning = format_manifest_warning(result)
        if warning and isinstance(response, Response):
            pass  # gate_block_counters stub removed — increment was no-op
            response.message = warning
            if hasattr(response, "break_loop"):
                response.break_loop = False

            logger.warning(
                f"[MANIFEST GATE] BLOCKING delivery: "
                f"{result['done']}/{result['total']} complete "
                f"({completion_pct}%) — below {MIN_COMPLETION_THRESHOLD}% — "
                f"missing: {', '.join(result['missing'][:5])}"
            )

    def _find_project_dir(self) -> str:
        """Find the active project directory from agent context.

        Checks multiple sources in priority order:
        1. agent.project_dir (set by delegation context)
        2. _delegation_task_ledger entries (most recent project path)
        3. data store keys with project path patterns
        """
        # Direct attribute
        project_dir = getattr(self.agent, "project_dir", "")
        if project_dir and os.path.isdir(project_dir):
            return project_dir

        # From delegation ledger — find most recent project reference
        ledger = get_delegation_ledger_for_gate(self.agent.data)
        for entry in reversed(ledger):
            if isinstance(entry, dict):
                path = entry.get("project_dir", "")
                if path and os.path.isdir(path):
                    return path

        # From agent data store — look for project directory keys
        for key in ["_active_project_dir", "_project_dir", "project_dir"]:
            val = self.agent.data.get(key, "")
            if val and os.path.isdir(val):
                return val

        return ""
