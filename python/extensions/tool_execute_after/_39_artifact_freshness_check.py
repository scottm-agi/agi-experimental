"""Artifact Freshness Check — tool_execute_after extension.

Before delegation proceeds to later phases, checks that upstream phase
artifacts are not stale (e.g., Phase 2.3 artifacts should be fresher
than Phase 2.0 artifacts).

Hooks into: tool_execute_after (order 39)
"""
from __future__ import annotations

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.artifact_freshness import get_stale_artifacts

logger = logging.getLogger("agix.artifact_freshness_check")


class ArtifactFreshnessCheck(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"})


    async def execute(self, tool_name: str = "", tool_args: dict = None, response: Any = None, **kwargs):
        if not tool_name or tool_name.lower() not in ('call_subordinate', 'call_subordinate_batch', 'fan_out_subordinates'):
            return

        try:
            project_dir = getattr(self.agent, 'project_dir', None) or self.agent.data.get('_project_dir', '')
            if not project_dir:
                return

            stale = get_stale_artifacts(project_dir)
            if stale:
                details = [f"  • {s['artifact']} (stale because of: {', '.join(s.get('stale_because_of', []))})" for s in stale[:3]]
                warning = (
                    f"⚠️ STALE ARTIFACTS: {len(stale)} phase artifact(s) are outdated:\n"
                    + "\n".join(details)
                    + "\n\nRegenerate upstream artifacts before proceeding."
                )
                logger.warning(warning)
                if hasattr(self.agent, 'hist_add_event'):
                    self.agent.hist_add_event("warning", warning, importance=60)

        except Exception as e:
            logger.debug(f"Artifact freshness check skipped: {e}")
