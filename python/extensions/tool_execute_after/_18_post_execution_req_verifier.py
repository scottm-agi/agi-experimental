"""
Extension: Post-Execution REQ Verifier — tool_execute_after

After every call_subordinate return, scans the project codebase to
verify that the requirements assigned to that delegation were actually
implemented (not just claimed). For unverified requirements, creates
remediation tasks with n.n.n sequencing and injects a manifest into
the tool result.

Architecture:
    1. Get the delegation's requirement_ids from the ledger
    2. Get the requirement texts and categories
    3. Scan the project directory for evidence of each requirement
    4. For unverified requirements:
       a. Mark them as "unverified" in the ledger
       b. Create remediation tasks (n.n.n format)
       c. Add remediation tasks to the ledger
       d. Inject manifest into tool result for orchestrator

This is deterministic regex scanning, NOT LLM-based verification.

Hook: tool_execute_after for call_subordinate
Order: _18_ (after _16 deliverable verifier)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

from python.helpers.extension import Extension
from python.helpers.requirements_ledger import get_delegation_ledger_for_gate

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.extensions.post_execution_req_verifier")


class PostExecutionReqVerifier(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"})

    """Verify requirements after subordinate returns and create remediation tasks."""

    async def execute(self, loop_data: Optional["LoopData"] = None, **kwargs) -> None:
        # Only activate for call_subordinate tool
        tool_name = kwargs.get("tool_name", "")
        if tool_name != "call_subordinate":
            return

        tool_result = kwargs.get("tool_result", "")
        if not tool_result or not isinstance(tool_result, str):
            return

        agent = self.agent
        if not agent:
            return

        # ── Step 1: Get last delegation result and its requirement_ids ──
        last_result = agent.data.get("_last_delegation_result", {})
        if not last_result:
            logger.debug("[REQ VERIFIER] No _last_delegation_result — skipping")
            return

        task_hash = last_result.get("task_hash", "")
        delegation_status = last_result.get("status", "")

        # Only verify on successful or partial delegations
        # Failed delegations already have error tracking
        if delegation_status == "failed":
            logger.debug("[REQ VERIFIER] Delegation status=failed — skipping verification")
            return

        # ── Step 2: Find the delegation in the ledger ──
        from python.helpers.requirements_ledger import _ensure_ledger

        ledger = _ensure_ledger(agent.data)
        delegations = ledger.get("delegations", [])
        requirements = ledger.get("requirements", [])

        if not delegations or not requirements:
            logger.debug("[REQ VERIFIER] No delegations or requirements in ledger — skipping")
            return

        # Find the most recent delegation (last one in list)
        latest_delegation = delegations[-1] if delegations else None
        if not latest_delegation:
            return

        delegation_id = latest_delegation.get("id", "")
        req_ids = latest_delegation.get("requirement_ids", [])

        if not req_ids:
            logger.debug(
                f"[REQ VERIFIER] Delegation {delegation_id} has no requirement_ids — skipping"
            )
            return

        # ── Step 3: Gather requirement details ──
        from python.helpers.req_id_normalizer import build_normalized_req_map
        req_map = build_normalized_req_map(requirements)
        reqs_to_verify = []
        for req_id in req_ids:
            if req_id in req_map:
                req = req_map[req_id]
                reqs_to_verify.append({
                    "id": req_id,
                    "text": req.get("text", ""),
                    "category": req.get("category", "general"),
                })

        if not reqs_to_verify:
            logger.debug(
                f"[REQ VERIFIER] No requirements found for IDs {req_ids} — skipping"
            )
            return

        # ── Step 4a: Circuit breaker — skip REQs verified 2+ times ──
        from python.helpers.post_execution_req_verifier import (
            should_verify_req,
            increment_verification_count,
        )
        filtered_reqs = []
        for req in reqs_to_verify:
            if should_verify_req(agent.data, req["id"]):
                filtered_reqs.append(req)
                increment_verification_count(agent.data, req["id"])
            else:
                logger.debug(
                    f"[REQ VERIFIER] Circuit breaker: skipping {req['id']} "
                    f"(max verification attempts reached)"
                )
        if not filtered_reqs:
            logger.info("[REQ VERIFIER] All REQs hit circuit breaker — skipping")
            return
        reqs_to_verify = filtered_reqs

        # ── Step 4: Get project directory ──
        project_dir = self._get_project_dir(agent)
        if not project_dir:
            logger.warning("[REQ VERIFIER] Could not determine project directory — skipping")
            return

        # ── Step 5: Verify requirements against codebase ──
        from python.helpers.post_execution_req_verifier import (
            verify_requirements_batch,
            build_remediation_task,
            add_remediation_to_ledger,
            mark_reqs_unverified,
            format_remediation_manifest,
            verify_file_spec,
        )
        from python.helpers.blueprint_req_bridge import (
            generate_file_specs,
            link_specs_to_requirements,
        )

        # Try blueprint-based verification first (spec-first path)
        spec_unverified_reqs: set = set()
        file_specs = generate_file_specs(project_dir)
        if file_specs:
            logger.info(
                f"[REQ VERIFIER] Using blueprint file_specs ({len(file_specs)} specs)"
            )

            # Link specs to REQ-IDs via fuzzy text matching
            file_specs = link_specs_to_requirements(file_specs, agent.data)

            # Verify each file spec
            spec_results = []
            for spec in file_specs:
                spec_result = verify_file_spec(project_dir, spec)
                spec_results.append((spec, spec_result))

            verified_specs = [(s, r) for s, r in spec_results if r["verified"]]
            unverified_specs = [(s, r) for s, r in spec_results if not r["verified"]]

            logger.info(
                f"[REQ VERIFIER] Blueprint verification: "
                f"✅ {len(verified_specs)} / ❌ {len(unverified_specs)}"
            )

            # Map unverified file specs back to requirement IDs
            for spec, result in unverified_specs:
                linked_reqs = spec.get("linked_reqs", [])
                if linked_reqs:
                    spec_unverified_reqs.update(linked_reqs)
                    logger.info(
                        f"[REQ VERIFIER] Spec '{spec.get('path')}' failed → "
                        f"linked REQs marked unverified: {linked_reqs}"
                    )


        report = verify_requirements_batch(project_dir, reqs_to_verify)

        # ── Step 5b: Merge spec-linked unverified REQs into report ──
        # If a REQ-ID is linked to a failing file spec, it overrides the
        # regex-based verification (spec is more precise than regex)
        if spec_unverified_reqs:
            existing_unverified_ids = {r["id"] for r in report["unverified"]}
            for req_id in spec_unverified_reqs:
                if req_id not in existing_unverified_ids and req_id in req_map:
                    # Move from verified to unverified
                    report["verified"] = [
                        v for v in report["verified"] if v["id"] != req_id
                    ]
                    report["unverified"].append({
                        "id": req_id,
                        "reason": f"File spec verification failed for linked requirement",
                    })
                    logger.info(
                        f"[REQ VERIFIER] Spec override: {req_id} moved from "
                        f"verified → unverified (linked file spec failed)"
                    )

        verified_count = len(report["verified"])
        unverified_count = len(report["unverified"])

        logger.info(
            f"[REQ VERIFIER] Delegation {delegation_id}: "
            f"✅ {verified_count} verified, ❌ {unverified_count} unverified "
            f"out of {len(reqs_to_verify)} total"
        )

        # ── Step 6: Handle unverified requirements ──
        if not report["unverified"]:
            # All verified — update ledger statuses to "verified"
            for v in report["verified"]:
                if v["id"] in req_map:
                    req_map[v["id"]]["status"] = "verified"
            logger.info(
                f"[REQ VERIFIER] ✅ All {verified_count} requirements VERIFIED for {delegation_id}"
            )
            # Store verification pass on agent data
            agent.data["_last_req_verification"] = {
                "delegation_id": delegation_id,
                "verified": verified_count,
                "unverified": 0,
                "remediation_tasks": [],
            }
            return

        # ── Step 7: Create remediation tasks (n.n.n sequencing) ──
        # Extract the delegation sequence number from delegation_id
        delegation_seq = self._extract_delegation_seq(delegation_id)
        profile = latest_delegation.get("profile", "code")

        remediation_tasks = []
        unverified_ids = []
        for idx, unv in enumerate(report["unverified"], start=1):
            task = build_remediation_task(
                delegation_seq=delegation_seq,
                sub_seq=idx,
                req_id=unv["id"],
                req_text=unv.get("reason", req_map.get(unv["id"], {}).get("text", "")),
                profile=profile,
            )
            remediation_tasks.append(task)
            unverified_ids.append(unv["id"])

        # ── Step 8: Update ledger ──
        mark_reqs_unverified(agent.data, unverified_ids)
        added = add_remediation_to_ledger(agent.data, remediation_tasks)

        logger.warning(
            f"[REQ VERIFIER] ❌ {unverified_count} requirements UNVERIFIED for {delegation_id}. "
            f"Created {added} remediation tasks ({delegation_seq}.1 → {delegation_seq}.{len(remediation_tasks)})"
        )

        # ── Step 9: Inject manifest into tool result (guard: only when added > 0) ──
        if added > 0:
            manifest = format_remediation_manifest(remediation_tasks, delegation_id=delegation_id)
            if manifest and loop_data is not None and hasattr(loop_data, "tool_result"):
                loop_data.tool_result = f"{tool_result}\n\n{manifest}"
            elif manifest and kwargs.get("_response_ref"):
                kwargs["_response_ref"]["result"] = f"{tool_result}\n\n{manifest}"
        else:
            logger.info(
                f"[REQ VERIFIER] Skipping manifest injection — "
                f"all {len(remediation_tasks)} remediation tasks were deduped"
            )

        # Store verification result on agent data for supervisor visibility
        agent.data["_last_req_verification"] = {
            "delegation_id": delegation_id,
            "verified": verified_count,
            "unverified": unverified_count,
            "remediation_tasks": [t["task_id"] for t in remediation_tasks],
        }

    def _extract_delegation_seq(self, delegation_id: str) -> int:
        """Extract the numeric sequence from 'delegation-N' format."""
        match = re.search(r'delegation-(\d+)', delegation_id)
        if match:
            return int(match.group(1))
        return 0

    def _get_project_dir(self, agent: "Agent") -> Optional[str]:
        """Determine the active project directory from agent context.

        Reuses the same logic as SubordinateDeliverableVerifier for consistency.
        """
        import os

        # Source 1: Explicit project dir
        project_dir = agent.data.get("_active_project_dir")
        if project_dir and os.path.isdir(project_dir):
            return project_dir

        # Source 2: Sandbox dir from FileGuard
        sandbox_dir = agent.data.get("_sandbox_dir")
        if sandbox_dir and os.path.isdir(sandbox_dir):
            return sandbox_dir

        # Source 3: Derive from delegation messages
        ledger = get_delegation_ledger_for_gate(agent.data)
        if ledger:
            for entry in reversed(ledger):
                msg = entry.get("message_summary", "")
                if "/agix/usr/projects/" in msg:
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
