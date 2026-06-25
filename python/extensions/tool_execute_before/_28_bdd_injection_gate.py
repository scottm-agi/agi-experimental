"""
BDD Spec Injection Gate — tool_execute_before extension.

L-2 Fix: BDD specs exist in the ledger but injection into call_subordinate
delegations is purely LLM-voluntary. The existing _21_tool_call_tracker.py
only RECORDS BDD specs (observability), and _22_multiagentdev_completion_gate.py
only REPORTS BDD coverage stats. Neither blocks delegations missing BDD specs.

This gate automatically injects BDD specs from the ledger when a
call_subordinate delegation has requirement_ids but no bdd_specs.
"""
from __future__ import annotations

import logging
from python.helpers.extension import Extension
from python.helpers.requirements_ledger import get_delegation_ledger_for_gate

logger = logging.getLogger("agix.bdd_injection_gate")


class BDDInjectionGate(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"})

    """Auto-inject BDD specs from ledger into call_subordinate delegations.

    When call_subordinate has requirement_ids referencing ledger entries
    with BDD specs, but bdd_specs is empty/missing in the tool_args,
    this gate injects them automatically.
    """

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        # FIX-026 (G-7): Handle batch and fan-out delegations, not just single
        if not tool_name or tool_name.lower() not in (
            "call_subordinate", "call_subordinate_batch", "fan_out_subordinates"
        ):
            return

        if not tool_args or not isinstance(tool_args, dict):
            return

        # Only act when requirement_ids are present but bdd_specs are missing
        requirement_ids = tool_args.get("requirement_ids", [])
        if not requirement_ids:
            return

        existing_bdd = tool_args.get("bdd_specs", [])
        if existing_bdd:
            return  # Already has BDD specs — don't override

        # Look up BDD specs from the delegation task ledger
        ledger = get_delegation_ledger_for_gate(self.agent.data)
        if not ledger:
            return

        injected_specs = []
        for req_id in requirement_ids:
            for entry in ledger:
                entry_id = entry.get("requirement_id", "")
                if entry_id == req_id:
                    specs = entry.get("bdd_specs", [])
                    if specs:
                        injected_specs.extend(specs)

        if not injected_specs:
            return

        # Inject BDD specs into the delegation
        tool_args["bdd_specs"] = injected_specs
        logger.info(
            f"[BDD INJECTION GATE] Injected {len(injected_specs)} BDD specs "
            f"from ledger into call_subordinate delegation "
            f"(requirement_ids={requirement_ids})"
        )

        # Log warning to agent history for visibility
        try:
            await self.agent.hist_add_warning(
                f"📋 BDD specs auto-injected: {len(injected_specs)} test scenarios "
                f"from the ledger were added to this delegation. "
                f"The subordinate agent should implement tests matching these specs."
            )
        except Exception:
            pass
