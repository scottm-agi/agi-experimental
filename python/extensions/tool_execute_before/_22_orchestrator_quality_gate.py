"""
Orchestrator Quality Gate — 3-gate stage-based routing.

Fires 3 gates at pipeline boundaries:
  BDD Gate  → "Is the spec complete & faithful to prompt?" (after Phase 2)
  TDD Gate  → "Are all tests green & code meets spec?"    (after Phase 3)
  Done Gate → "Is this ready to deliver?"                  (after Phase 4/5)

Each gate runs ONLY the checks tagged with its gate name (via gate= param
on @register_check). Checks with gate="" run in all gates (backward compat).

Escape hatch: per-requirement partial status via mark_partial(). After 3
failed attempts on the same requirement, it's accepted as partial and the
gate allows through. No blanket force-allow — the partial system tracks
PER-REQUIREMENT, not per-gate.

Extension point: tool_execute_before
Order: 22 (after tool tracking, before build manager)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from python.helpers.extension import Extension

logger = logging.getLogger("agix.orchestrator_quality_gate")


class OrchestratorQualityGate(Extension):
    """Orchestrator-level quality gate — intercepts response tool and runs
    stage-based integration checks before allowing delivery."""

    # Context-aware: only fire for orchestrator profiles on response tool
    PROFILES = {"multiagentdev", "orchestrator", "alex"}
    TOOLS = frozenset({"response"})

    async def execute(
        self,
        tool_name: str = "",
        tool_args: Optional[dict] = None,
        **kwargs: Any,
    ) -> Optional[Any]:
        """Run stage-based quality gate checks before allowing the response tool."""
        if tool_name != "response":
            return None

        # Per-requirement partial system in run_gate_checks() is the
        # sole escape mechanism — no blanket budget needed.

        # Lazy imports to avoid circular dependencies at module load time
        from python.helpers.gate_router import (
            get_current_gate,
            mark_gate_passed,
        )
        from python.helpers.orchestrator_gate_integration_checks import (
            run_gate_checks,
        )

        # Determine which gate should fire based on pipeline state
        current_gate = get_current_gate(self.agent.data)
        if current_gate is None:
            # All 3 gates passed — allow response through
            logger.info("[ORCH GATE] All gates (bdd/tdd/done) passed — allowing response")
            return None

        block_count = self.agent.data.get("_orchestrator_gate_blocks", 0)
        blocked = await run_gate_checks(
            self.agent, current_gate, block_count, response=None
        )

        if blocked:
            self.agent.data["_orchestrator_gate_blocks"] = block_count + 1
            logger.warning(
                "[ORCH GATE:%s] Quality checks BLOCKED response "
                "(block #%d). Agent must fix issues before responding.",
                current_gate, block_count + 1,
            )
            # Retrieve the structured block message stored by run_gate_checks
            block_details = self.agent.data.get(
                "_last_gate_block_details", {}
            )
            block_msg = block_details.get(
                "message",
                f"⛔ {current_gate.upper()} gate checks failed. Fix the issues listed above.",
            )

            # Return a Response that blocks the tool but keeps the loop going
            # (break_loop=False → agent continues working to fix issues)
            from python.extensions.tool_execute_before._19_pre_response_self_check import (
                Response,
            )
            return Response(message=block_msg, break_loop=False)

        # Current gate passed — mark it and allow response through
        # (next response attempt will hit the next gate)
        mark_gate_passed(self.agent.data, current_gate)
        logger.info(
            "[ORCH GATE:%s] All checks PASSED — gate marked complete",
            current_gate,
        )
        return None  # Allow response

