"""
Tool Call Tracker — tool_execute_after extension (_21_)

Tracks browser_agent, call_subordinate, and save_deliverable tool calls
for ALL agents (including subordinates). Both the Alex and multiagentdev
completion gates read this tracking data.

Runs at _21_ (before _22_ gates) to ensure data is available when gates check.

Extracted from _22_orchestrator_completion_gate.py monolith (L288-370).
"""

import hashlib
import logging
import re
from typing import Any

from python.helpers.extension import Extension

def has_verification_evidence(text: str) -> bool:
    """Check if delegation response contains verification evidence keywords."""
    _EVIDENCE_KEYWORDS = [
        "all tests pass", "tests passed", "build succeeded",
        "lint clean", "no errors", "verification complete",
        "e2e pass", "curl.*200", "health check.*pass",
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in _EVIDENCE_KEYWORDS)


def detect_quality_audit_evidence(text: str) -> bool:
    """Check if delegation response contains quality audit text evidence."""
    _AUDIT_KEYWORDS = [
        "quality audit", "audit complete", "audit results",
        "code review", "review complete",
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in _AUDIT_KEYWORDS)

from python.helpers.validators.lit import detect_lit_execution_evidence

logger = logging.getLogger("agix.orchestrator_completion_gate")


class ToolCallTracker(Extension):
    """Track tool calls for gate consumption. Handles non-response tools only."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        tool_lower = tool_name.lower()

        # ── Track browser_agent invocations (RC-10) ──
        if tool_lower == "browser_agent":
            count = self.agent.data.get("_browser_agent_calls", 0) + 1
            self.agent.data["_browser_agent_calls"] = count
            logger.info(
                f"[TOOL TRACKER] {self.agent.agent_name} browser_agent call #{count}"
            )
            # RC-30 v3: Do NOT set _quality_audit_done from text evidence.
            # Only VLM-based screenshot evaluation (CHECK 1.17) can set this.
            return

        # ── Track subordinate delegations ──
        if tool_lower in ("call_subordinate", "call_subordinate_batch", "fan_out_subordinates"):
            count = self.agent.data.get("_subordinate_call_count", 0) + 1
            self.agent.data["_subordinate_call_count"] = count
            logger.info(
                f"[TOOL TRACKER] {self.agent.agent_name} delegation #{count}"
            )

            tool_args = kwargs.get("tool_args", {})


            # Profiles that indicate verification/testing delegation
            VERIFICATION_PROFILES = {"e2e", "browser_agent", "qa", "tester"}

            # Track delegated profile names for business gate
            tool_args = kwargs.get("tool_args", {})
            if isinstance(tool_args, dict):
                delegated_profile = tool_args.get("profile", "")
                if delegated_profile:
                    profiles = self.agent.data.get("_delegation_profiles", set())
                    if not isinstance(profiles, set):
                        profiles = set(profiles) if profiles else set()
                    profiles.add(delegated_profile)
                    self.agent.data["_delegation_profiles"] = profiles
                    # Track content-writer specifically
                    if delegated_profile == "content-writer":
                        self.agent.data["_content_writer_delegated"] = True
                    # Proactive verification detection from profile name
                    if delegated_profile.lower() in VERIFICATION_PROFILES:
                        self.agent.data["_verification_delegated"] = True
                        logger.info(
                            f"[TOOL TRACKER] Verification profile detected: "
                            f"{delegated_profile} → set _verification_delegated"
                        )
                    logger.info(
                        f"[TOOL TRACKER] Delegated to profile={delegated_profile} "
                        f"(total unique: {len(profiles)})"
                    )

                # Track batch task profiles (for call_subordinate_batch)
                tasks = tool_args.get("tasks", [])
                if isinstance(tasks, list) and tasks:
                    profiles = self.agent.data.get("_delegation_profiles", set())
                    if not isinstance(profiles, set):
                        profiles = set(profiles) if profiles else set()
                    for task in tasks:
                        p = task.get("profile", "") if isinstance(task, dict) else ""
                        if p:
                            profiles.add(p)
                            if p == "content-writer":
                                self.agent.data["_content_writer_delegated"] = True
                            # Proactive verification detection from batch task profile
                            if p.lower() in VERIFICATION_PROFILES:
                                self.agent.data["_verification_delegated"] = True
                                logger.info(
                                    f"[TOOL TRACKER] Verification profile in batch: "
                                    f"{p} → set _verification_delegated"
                                )
                    self.agent.data["_delegation_profiles"] = profiles

            # ── Requirements Traceability Ledger ──
            # Records structured delegation entries linked to prompt requirements.
            # _requirements_ledger is the canonical source of truth.
            # RCA-451: Removed migrate_legacy_ledger call and _delegation_task_ledger
            # writeback. The old backward-compat bridge created a feedback loop:
            # writeback → re-migrate → double entries per restart → 1M+ entries → OOM.
            from python.helpers.requirements_ledger import (
                record_delegation as _record_delegation,
                mark_delegation_complete as _mark_complete,
                validate_requirement_ids,
            )

            tool_args = kwargs.get("tool_args", {}) if not isinstance(tool_args, dict) else tool_args

            if tool_lower == "call_subordinate":
                profile = tool_args.get("profile", "unknown")
                message = tool_args.get("message", "")
                req_ids = tool_args.get("requirement_ids", [])
                bdd_specs = tool_args.get("bdd_specs", [])
                # F-1 fix: Deterministic delegation_id from profile + sorted req_ids.
                # Prevents inflation when LLM paraphrases messages during context
                # reconstruction — the F-3 idempotent update path in record_delegation
                # matches on this stable ID instead of relying on content-hash dedup.
                #
                # SS-1 (ITR-355): When req_ids is empty, use profile + message hash
                # as the deterministic ID instead of falling back to None (which
                # caused sequential delegation-N IDs that collide across context
                # reconstructions, inflating 6 unique delegations to 47 entries).
                req_ids_key = ','.join(sorted(req_ids)) if isinstance(req_ids, list) and req_ids else ''
                if req_ids_key:
                    det_hash = hashlib.md5(f"{profile}:{req_ids_key}".encode()).hexdigest()[:8]
                    det_delegation_id = f"del-{profile}-{det_hash}"
                else:
                    # SS-1 fix: Use message content hash as stable ID
                    msg_key = message[:200] if message else 'empty'
                    det_hash = hashlib.md5(f"{profile}:{msg_key}".encode()).hexdigest()[:8]
                    det_delegation_id = f"del-{profile}-{det_hash}"
                delegation_id = _record_delegation(
                    self.agent.data, profile, message,
                    requirement_ids=req_ids if isinstance(req_ids, list) else [],
                    bdd_specs=bdd_specs if isinstance(bdd_specs, list) else [],
                    delegation_id=det_delegation_id,
                )
                # Stash delegation_id for completion tracking on response
                self.agent.data["_last_delegation_id"] = delegation_id

                # ── Critical enforcement: BLOCK when requirement_ids missing ──
                # When requirements exist in the ledger and this delegation
                # omits requirement_ids, inject a blocking message that forces
                # the orchestrator to use the `requirements` tool and retry.
                req_warning = validate_requirement_ids(
                    self.agent.data,
                    requirement_ids=req_ids if isinstance(req_ids, list) else [],
                )
                if req_warning and response and hasattr(response, "message"):
                    # Escalate to critical block — not just advisory
                    block_msg = (
                        f"🚫 BLOCKED — MISSING requirement_ids\n\n"
                        f"{req_warning}\n\n"
                        f"**Action required:** Call the `requirements` tool with "
                        f'action "suggest" to get unassigned requirement IDs, '
                        f"then re-delegate with `requirement_ids` included.\n\n"
                        f"This delegation was recorded but CANNOT be verified "
                        f"without requirement_ids linkage."
                    )
                    response.message = f"{block_msg}\n\n{response.message}"
                    logger.warning(
                        f"[TOOL TRACKER] BLOCKED delegation #{count}: "
                        f"missing requirement_ids (requirements exist in ledger)"
                    )
            elif tool_lower in ("call_subordinate_batch", "fan_out_subordinates"):
                tasks = tool_args.get("tasks", [])
                # RCA-362 Fix R-1: Collect ALL batch delegation IDs
                # so we can mark all of them complete, not just the last one.
                batch_delegation_ids = []
                if isinstance(tasks, list):
                    for task in tasks:
                        if isinstance(task, dict):
                            req_ids = task.get("requirement_ids", [])
                            bdd_specs = task.get("bdd_specs", [])
                            # F-1 fix: Deterministic delegation_id for batch tasks
                            # SS-1 (ITR-355): Always generate deterministic IDs,
                            # even when req_ids is empty (use message hash).
                            batch_profile = task.get("profile", "unknown")
                            batch_req_key = ','.join(sorted(req_ids)) if isinstance(req_ids, list) and req_ids else ''
                            batch_msg = task.get("message", "")
                            if batch_req_key:
                                batch_det_hash = hashlib.md5(f"{batch_profile}:{batch_req_key}".encode()).hexdigest()[:8]
                                batch_det_id = f"del-{batch_profile}-{batch_det_hash}"
                            else:
                                batch_msg_key = batch_msg[:200] if batch_msg else 'empty'
                                batch_det_hash = hashlib.md5(f"{batch_profile}:{batch_msg_key}".encode()).hexdigest()[:8]
                                batch_det_id = f"del-{batch_profile}-{batch_det_hash}"
                            delegation_id = _record_delegation(
                                self.agent.data,
                                batch_profile,
                                task.get("message", ""),
                                requirement_ids=req_ids if isinstance(req_ids, list) else [],
                                bdd_specs=bdd_specs if isinstance(bdd_specs, list) else [],
                                delegation_id=batch_det_id,
                            )
                            batch_delegation_ids.append(delegation_id)
                if batch_delegation_ids:
                    self.agent.data["_last_delegation_id"] = batch_delegation_ids[-1]
                    # Store all IDs for batch completion marking
                    self.agent.data["_batch_delegation_ids"] = batch_delegation_ids

            # Mark delegation(s) as completed when we have a response
            response_msg = getattr(response, "message", "") if response else ""
            if not response_msg and isinstance(response, str):
                response_msg = response

            # RCA-362: Resolve project_dir for disk persistence.
            # The tracker is the primary caller of mark_delegation_complete.
            # Without this, the disk ledger stays stale at "all pending".
            _project_dir = None
            try:
                from python.helpers import projects
                _pname = projects.get_context_project_name(
                    self.agent.context
                ) if hasattr(self.agent, 'context') else None
                if _pname:
                    _project_dir = projects.get_project_folder(_pname)
            except Exception:
                pass  # Non-fatal — mark_complete still works in memory

            # RCA-362 Fix R-1: Mark ALL batch delegation IDs complete,
            # not just _last_delegation_id. For single delegations, this
            # falls through to the _last_delegation_id path.
            batch_ids = self.agent.data.pop("_batch_delegation_ids", [])
            if batch_ids and response_msg:
                for bid in batch_ids:
                    _mark_complete(
                        self.agent.data, bid,
                        response_summary=response_msg[:300],
                        project_dir=_project_dir,
                    )
                # Response-based linking for the last batch delegation
                last_bid = batch_ids[-1]
                from python.helpers.requirements_ledger import _ensure_ledger as _el
                _ledger = _el(self.agent.data)
                _delegation = next(
                    (d for d in _ledger["delegations"] if d["id"] == last_bid),
                    None,
                )
                if _delegation and _delegation.get("status") != "failed":
                    try_response_based_linking(
                        self.agent.data, last_bid, response_msg
                    )
            else:
                # Single delegation path (call_subordinate)
                last_did = self.agent.data.get("_last_delegation_id")
                if last_did and response_msg:
                    resolved = _mark_complete(
                        self.agent.data, last_did,
                        response_summary=response_msg[:300],
                        project_dir=_project_dir,
                    )
                    # RCA-271 Fix 2: If mark_complete resolved 0 requirements
                    # but response contains REQ-IDs, try response-based linking.
                    # RCA-259 Fix G: Skip linking on failed delegations — they
                    # should NOT resolve requirements through any code path.
                    if resolved:
                        from python.helpers.requirements_ledger import _ensure_ledger as _el
                        _ledger = _el(self.agent.data)
                        _delegation = next(
                            (d for d in _ledger["delegations"] if d["id"] == last_did),
                            None,
                        )
                        if _delegation and _delegation.get("status") != "failed":
                            try_response_based_linking(
                                self.agent.data, last_did, response_msg
                            )

            # RCA-451: Removed backward-compat writeback of _delegation_task_ledger.
            # This was the SOURCE of the exponential inflation loop.
            # All gate consumers now read from _requirements_ledger directly
            # via get_delegation_ledger_for_gate().

            # Track verification evidence in delegation response
            response_msg = getattr(response, "message", "") if response else ""
            if not response_msg and isinstance(response, str):
                response_msg = response
            if response_msg and has_verification_evidence(response_msg):
                self.agent.data["_verification_delegated"] = True
                logger.info(
                    f"[TOOL TRACKER] Verification evidence detected in "
                    f"delegation #{count} response"
                )

            # RC-30 v3: Log quality audit text evidence but do NOT set flag
            if response_msg and detect_quality_audit_evidence(response_msg):
                logger.info(
                    f"[TOOL TRACKER] Quality audit TEXT evidence detected in "
                    f"delegation #{count} response (NOT setting _quality_audit_done "
                    f"— VLM evaluation required)"
                )

            # LIT: Check if delegation response contains LIT execution evidence
            if response_msg and detect_lit_execution_evidence(response_msg):
                self.agent.data["_lit_tests_executed"] = True
                logger.info(
                    f"[TOOL TRACKER] LIT execution evidence detected in "
                    f"delegation #{count} response"
                )
            return

        # ── Track save_deliverable calls (RC-21.4) ──
        if tool_lower == "save_deliverable":
            count = self.agent.data.get("_deliverable_save_count", 0) + 1
            self.agent.data["_deliverable_save_count"] = count

            # Track deliverable titles for title validation gate check
            tool_args = kwargs.get("tool_args", {})
            if isinstance(tool_args, dict):
                title = tool_args.get("title", "")
                titles = self.agent.data.get("_deliverable_titles", [])
                if not isinstance(titles, list):
                    titles = []
                titles.append(title)
                self.agent.data["_deliverable_titles"] = titles

            logger.info(
                f"[TOOL TRACKER] {self.agent.agent_name} save_deliverable #{count}"
            )
            return

        # All other tools: no-op (gates handle 'response' tool)


def try_response_based_linking(
    agent_data: dict,
    delegation_id: str,
    response_text: str,
) -> int:
    """RCA-271 Fix 2: Extract REQ-IDs from delegation response and link them.

    When mark_delegation_complete() resolves 0 requirements because the
    delegation had no requirement_ids, we scan the response text for
    REQ-XXX patterns and resolve any matching requirements in the ledger.

    This is defense-in-depth for delegations that slipped through the
    circuit breaker without requirement_ids but still completed the work.

    Args:
        agent_data: The agent.data dict
        delegation_id: ID of the completed delegation
        response_text: Full text of the delegation's response

    Returns:
        Number of requirements linked from response text
    """
    import re

    if not response_text:
        return 0

    try:
        from python.helpers.requirements_ledger import _ensure_ledger
        ledger = _ensure_ledger(agent_data)

        # Find the delegation
        delegation = None
        for d in ledger["delegations"]:
            if d["id"] == delegation_id:
                delegation = d
                break

        if delegation is None:
            return 0

        # RCA-ITR49 Fix 2: Don't skip when already_linked is non-empty.
        # The old code returned 0 here, meaning response-based linking ONLY
        # helped delegations with NO requirement_ids. Now we link ADDITIONAL
        # requirements found in the response while skipping already-linked ones.
        already_linked = set(delegation.get("requirement_ids", []))

        # Extract all REQ-XXX patterns from response
        req_pattern = re.compile(r'REQ-[a-zA-Z0-9]{3,10}')
        found_ids = set(req_pattern.findall(response_text))

        if not found_ids:
            return 0

        # Build lookup of known requirement IDs
        from python.helpers.req_id_normalizer import build_normalized_req_map
        req_map = build_normalized_req_map(ledger.get("requirements", []))

        # Link only IDs that exist in the ledger AND are not already linked
        from python.helpers.requirements_proof import mark_requirement_complete
        linked_count = 0
        linked_ids = []
        for req_id in found_ids:
            if req_id in req_map and req_id not in already_linked:
                try:
                    mark_requirement_complete(agent_data, req_id, force=True)
                    linked_ids.append(req_id)
                    linked_count += 1
                except Exception as e:
                    logger.debug(f"[TOOL TRACKER] Auto-promote error for {req_id}: {e}")

        # Update delegation with discovered IDs (merge with existing)
        if linked_ids:
            delegation["requirement_ids"] = list(already_linked | set(linked_ids))
            logger.info(
                f"[TOOL TRACKER] RCA-271: Response-based linking resolved "
                f"{linked_count} requirements from delegation {delegation_id}: "
                f"{', '.join(linked_ids[:5])}"
            )

        return linked_count

    except Exception as e:
        logger.debug(f"[TOOL TRACKER] Response-based linking failed: {e}")
        return 0
