"""
Decomposition Coverage Gate (Prompt Fidelity Pipeline — Phase 4)

Pre-execution gate that intercepts call_subordinate/call_subordinate_batch
and enforces requirements linkage.

TWO enforcement levels:
  1. ADVISORY: Injects a requirements manifest when unassigned requirements
     exist (unchanged from Phase 4).
  2. HARD ENFORCEMENT: Blocks the delegation tool when requirements exist,
     some are already assigned (proving the agent knows the system), AND
     the new delegation has no requirement_ids.

Prevention Architecture Design:
  - Fires on EVERY delegation (not one-shot)
  - Throttled: only re-injects advisory when coverage % changes
  - Hard block returns a Response that prevents tool execution
  - Non-code profiles (researcher, architect) are exempted

Priority: 16 (before delegation hooks at 20+)
"""

import logging
from python.helpers.extension import Extension
from python.helpers.universal_gate_budget import gate_check


logger = logging.getLogger("agix.decomposition_coverage_gate")

_DELEGATION_TOOLS = {"call_subordinate", "call_subordinate_batch"}
# FIX-020: Use centralized profile registry instead of hardcoded names
from python.helpers.profile_registry import ORCHESTRATOR_PROFILES as _ORCHESTRATOR_NAMES

# Profiles that produce code and should be required to link requirements.
# Non-code profiles (researcher, architect, planner) are exempted.
_NON_CODE_PROFILES = {"researcher", "architect", "planner", "research", "planning", "frontend"}


class DecompositionCoverageGate(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"})

    """Pre-execution gate: inject requirements manifest on every delegation
    when unassigned requirements exist. Hard-blocks when requirement_ids
    are missing and enforcement conditions are met."""

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        """Intercept delegation calls and enforce requirement linkage.

        Enforcement conditions for HARD BLOCK:
          1. Tool is call_subordinate or call_subordinate_batch
          2. Agent is an orchestrator (multiagentdev, alex)
          3. Requirements ledger has requirements
          4. At least one requirement is already assigned (proving agent
             has used the system before — not in seeding phase)
          5. The delegation has no requirement_ids
          6. The delegation profile is code-producing (not research/architect)

        If all conditions are met, returns a Response that blocks execution.
        Otherwise, falls through to advisory manifest injection.
        """
        # Only intercept delegation tools
        if tool_name not in _DELEGATION_TOOLS:
            return

        # Only applies to orchestrator agents
        agent_name = getattr(self.agent, "agent_name", "").lower()
        if agent_name not in _ORCHESTRATOR_NAMES:
            return

        tool_args = tool_args or {}

        # ──────────────────────────────────────────────────────────
        # FULLSTACK BLOB DETECTION: Block mixed frontend+backend delegations
        # CIRCUIT BREAKER: After MAX_BLOB_BLOCKS hard blocks, downgrade
        # to advisory log so forward progress is never permanently stalled.
        # ──────────────────────────────────────────────────────────
        MAX_BLOB_BLOCKS = 3
        try:
            from python.helpers.validators.common import detect_fullstack_blob
            profile = tool_args.get("profile", "").lower()
            message = tool_args.get("message", "")

            blob_blocks = self.agent.data.get("_decomp_blob_blocks", 0)

            # For batch, check each task's message
            if tool_name == "call_subordinate_batch":
                tasks = tool_args.get("tasks", [])
                for task in (tasks if isinstance(tasks, list) else []):
                    if isinstance(task, dict):
                        t_profile = task.get("profile", "").lower()
                        t_message = task.get("message", "")
                        blob_result = detect_fullstack_blob(t_message, t_profile)
                        if blob_result and blob_result.get("is_blob"):
                            if not gate_check(self.agent.data, "decomp_blob"):
                                from python.helpers.tool import Response
                                agent_label = _get_agent_label(self.agent)

                                logger.warning(
                                    f"[DECOMPOSITION GATE] FULLSTACK BLOB BLOCKED: "
                                    f"{agent_label} batch task to profile={t_profile} mixes "
                                    f"frontend ({blob_result['frontend_signals']}) "
                                    f"and backend ({blob_result['backend_signals']}) signals"
                                )
                                # Wire: Use enhanced blob decomposition messages
                                try:
                                    from python.helpers.blob_decomposition import build_blob_decomposition
                                    decomp_msg = build_blob_decomposition(
                                        blob_result['frontend_signals'],
                                        blob_result['backend_signals'],
                                    )
                                except Exception:
                                    decomp_msg = (
                                        f"**FIX**: Decompose into separate delegations:\n"
                                        f"1. A `code` delegation for backend (API routes, DB, integrations)\n"
                                        f"2. A `frontend` delegation for UI (pages, components, styling)\n\n"
                                        f"Each delegation must have its own `requirement_ids`."
                                    )
                                return Response(
                                    message=(
                                        f"⛔ FULLSTACK BLOB BLOCKED (caller: {agent_label})\n\n"
                                        f"❌ **NOTHING WAS EXECUTED.** No subordinates were created. "
                                        f"Your `{tool_name}` call was intercepted and did NOT run.\n\n"
                                        f"This batch delegation mixes frontend and backend concerns:\n"
                                        f"- Frontend signals: {', '.join(blob_result['frontend_signals'])}\n"
                                        f"- Backend signals: {', '.join(blob_result['backend_signals'])}\n\n"
                                        f"{decomp_msg}\n\n"
                                        f"⚠️ **You MUST call `call_subordinate` or `call_subordinate_batch` again** "
                                        f"with decomposed tasks. Do NOT call `wait()` — nothing is running yet."
                                    ),
                                    break_loop=False,
                                )
                            else:
                                logger.warning(
                                    f"[DECOMPOSITION GATE] Blob circuit breaker: "
                                    f"{blob_blocks} blocks exhausted — allowing mixed "
                                    f"delegation through (forward progress over perfection)"
                                )
            else:
                blob_result = detect_fullstack_blob(message, profile)
                if blob_result and blob_result.get("is_blob"):
                    if not gate_check(self.agent.data, "decomp_blob"):
                        from python.helpers.tool import Response
                        agent_label = _get_agent_label(self.agent)

                        logger.warning(
                            f"[DECOMPOSITION GATE] FULLSTACK BLOB BLOCKED: "
                            f"{agent_label} profile={profile} mixes frontend ({blob_result['frontend_signals']}) "
                            f"and backend ({blob_result['backend_signals']}) signals"
                        )
                        # Wire: Use enhanced blob decomposition messages
                        try:
                            from python.helpers.blob_decomposition import build_blob_decomposition
                            decomp_msg = build_blob_decomposition(
                                blob_result['frontend_signals'],
                                blob_result['backend_signals'],
                            )
                        except Exception:
                            decomp_msg = (
                                f"**FIX**: Decompose into separate delegations:\n"
                                f"1. A `code` delegation for backend (API routes, DB, integrations)\n"
                                f"2. A `frontend` delegation for UI (pages, components, styling)\n\n"
                                f"Each delegation must have its own `requirement_ids`."
                            )
                        return Response(
                            message=(
                                f"⛔ FULLSTACK BLOB BLOCKED (caller: {agent_label})\n\n"
                                f"❌ **NOTHING WAS EXECUTED.** No subordinates were created. "
                                f"Your `{tool_name}` call was intercepted and did NOT run.\n\n"
                                f"This delegation mixes frontend and backend concerns:\n"
                                f"- Frontend signals: {', '.join(blob_result['frontend_signals'])}\n"
                                f"- Backend signals: {', '.join(blob_result['backend_signals'])}\n\n"
                                f"{decomp_msg}\n\n"
                                f"⚠️ **You MUST call `call_subordinate` or `call_subordinate_batch` again** "
                                f"with decomposed tasks. Do NOT call `wait()` — nothing is running yet."
                            ),
                            break_loop=False,
                        )
                    else:
                        logger.warning(
                            f"[DECOMPOSITION GATE] Blob circuit breaker: "
                            f"{blob_blocks} blocks exhausted — allowing mixed "
                            f"delegation through (forward progress over perfection)"
                        )
        except Exception as blob_err:
            logger.debug(f"[DECOMPOSITION GATE] Fullstack blob check failed: {blob_err}")

        # ──────────────────────────────────────────────────────────
        # U-295-5: DECOMPOSITION-AWARE DISPATCH
        # Block tasks whose predecessors are not yet complete.
        # ──────────────────────────────────────────────────────────
        try:
            from python.helpers.validators.decomposition_dispatch import (
                check_dependency_completion,
                extract_seq_ids_from_message,
            )
            decomp_index = self.agent.data.get("_decomposition_index", {})
            if decomp_index:
                # Extract seq IDs from the delegation
                if tool_name == "call_subordinate_batch":
                    all_seq_ids = []
                    for task in (tool_args.get("tasks", []) if isinstance(tool_args.get("tasks"), list) else []):
                        if isinstance(task, dict):
                            msg = task.get("message", "")
                            req_ids = task.get("requirement_ids", [])
                            seq_ids = extract_seq_ids_from_message(
                                msg, requirement_ids=req_ids, decomp_index=decomp_index
                            )
                            all_seq_ids.extend(seq_ids)
                else:
                    msg = tool_args.get("message", "")
                    req_ids = tool_args.get("requirement_ids", [])
                    all_seq_ids = extract_seq_ids_from_message(
                        msg, requirement_ids=req_ids, decomp_index=decomp_index
                    )

                if all_seq_ids:
                    dep_check = check_dependency_completion(decomp_index, all_seq_ids)
                    if dep_check and not dep_check["all_satisfied"]:
                        # RCA-306c: Circuit breaker — escape after MAX_DEP_BLOCKS
                        MAX_DEP_BLOCKS = 3
                        dep_blocks = self.agent.data.get("_decomp_dep_blocks", 0)
                        if not gate_check(self.agent.data, "decomp_dep"):
                            from python.helpers.tool import Response

                            blocked_info = "\n".join(
                                f"  - **{bt['seq']}** depends on: {', '.join(bt['unmet_deps'])}"
                                for bt in dep_check["blocked_tasks"]
                            )
                            logger.warning(
                                f"[DECOMPOSITION GATE] U-295-5 DEPENDENCY BLOCK: "
                                f"{len(dep_check['blocked_tasks'])} tasks have unmet dependencies "
                                f"(block {dep_blocks + 1}/{MAX_DEP_BLOCKS})"
                            )
                            return Response(
                                message=(
                                    f"⛔ DEPENDENCY BLOCK — Prerequisites not complete\n\n"
                                    f"❌ **NOTHING WAS EXECUTED.** The following tasks have "
                                    f"unmet dependencies that must complete first:\n\n"
                                    f"{blocked_info}\n\n"
                                    f"**FIX**: Complete the prerequisite tasks first, then "
                                    f"re-dispatch. Check `decomposition_index.json` status.\n\n"
                                    f"⚠️ Do NOT call `wait()` — nothing is running yet."
                                ),
                                break_loop=False,
                            )
                        else:
                            # Circuit breaker tripped — allow through
                            logger.warning(
                                f"[DECOMPOSITION GATE] Dependency circuit breaker: "
                                f"{dep_blocks} blocks exhausted — allowing delegation "
                                f"through despite unmet dependencies (forward progress "
                                f"over perfection)"
                            )
        except Exception as dep_err:
            logger.debug(f"[DECOMPOSITION GATE] Dependency check failed: {dep_err}")

        # Check if we have requirements at all
        try:
            from python.helpers.requirements_ledger import (
                get_coverage,
                get_unassigned_requirements,
            )
            coverage = get_coverage(self.agent.data)
        except Exception as e:
            logger.debug(f"[DECOMPOSITION GATE] Coverage check failed: {e}")
            return

        if coverage["total_requirements"] == 0:
            return  # No requirements extracted yet — allow

        # ──────────────────────────────────────────────────────────
        # HARD ENFORCEMENT: Block when requirement_ids missing
        # ──────────────────────────────────────────────────────────
        # FIX F3-B (cold-start gap): Previously used `assigned_count > 0`
        # which meant the FIRST delegations could slip through without
        # requirement_ids. Now enforce from the very first delegation
        # when requirements exist (total_requirements > 0).
        #
        # CIRCUIT BREAKER: Hard-block at most MAX_REQ_ID_BLOCKS times,
        # then downgrade to advisory warning so forward progress is
        # never permanently stalled ("forward progress over perfection").
        MAX_REQ_ID_BLOCKS = 3
        if coverage["total_requirements"] > 0:
            # Requirements exist — enforce requirement_ids on ALL delegations.
            req_ids = tool_args.get("requirement_ids", [])

            # For batch delegations, check the outer level
            if tool_name == "call_subordinate_batch":
                # Batch: check if ANY tasks have requirement_ids
                tasks = tool_args.get("tasks", [])
                has_any_ids = any(
                    t.get("requirement_ids") for t in tasks if isinstance(t, dict)
                )
                if not has_any_ids:
                    req_ids = []  # No task has IDs → enforce
                else:
                    # RCA-306b FIX: Tasks have requirement_ids — mark as covered
                    # so the `if not req_ids` check below skips the block.
                    # Previously req_ids stayed empty ([]) from the top-level
                    # get() because batch IDs are per-task, not top-level.
                    req_ids = ["_batch_tasks_have_ids"]

            if not req_ids:
                # F-14 (RCA-357): Auto-infer requirement_ids from delegation
                # message text before blocking. If the orchestrator mentions
                # REQ-xxx patterns in the message but forgot to put them in
                # the requirement_ids field, extract and attach them.
                inferred_ids = _extract_req_ids_from_text(tool_args, tool_name)
                if inferred_ids:
                    # Auto-attach the inferred IDs
                    if tool_name == "call_subordinate_batch":
                        # For batch, attach to each task
                        tasks = tool_args.get("tasks", [])
                        for task in (tasks if isinstance(tasks, list) else []):
                            if isinstance(task, dict) and not task.get("requirement_ids"):
                                task_ids = _extract_req_ids_from_single_text(
                                    task.get("message", "")
                                )
                                if task_ids:
                                    task["requirement_ids"] = task_ids
                    else:
                        tool_args["requirement_ids"] = inferred_ids
                    logger.info(
                        f"[DECOMPOSITION GATE] F-14: Auto-inferred "
                        f"{len(inferred_ids)} requirement_ids from delegation "
                        f"message text: {', '.join(inferred_ids[:5])}"
                    )
                    req_ids = inferred_ids  # Skip the block

            if not req_ids:
                # Check if this is a non-code profile (exempt from enforcement)
                profile = tool_args.get("profile", "").lower()
                if profile not in _NON_CODE_PROFILES:
                    req_id_blocks = self.agent.data.get("_decomp_req_blocks", 0)
                    if not gate_check(self.agent.data, "decomp_req"):
                        # HARD BLOCK: Return a Response that prevents execution

                        return _build_enforcement_block(
                            self.agent.data, coverage, get_unassigned_requirements,
                            tool_name=tool_name,
                        )
                    else:
                        # Circuit breaker tripped — downgrade to advisory
                        # RCA-271 FIX: Auto-assign unassigned requirements
                        # so mark_delegation_complete() can still resolve them.
                        # Without this, delegations go through untrackable.
                        auto_link_on_circuit_breaker(
                            self.agent.data, tool_args
                        )
                        logger.warning(
                            f"[DECOMPOSITION GATE] Requirement-ID circuit breaker: "
                            f"{req_id_blocks} hard blocks exhausted — allowing "
                            f"delegation through (auto-linked unassigned reqs)"
                        )

        # ──────────────────────────────────────────────────────────
        # ADVISORY: Inject manifest for unassigned requirements
        # ──────────────────────────────────────────────────────────
        unassigned = get_unassigned_requirements(self.agent.data)
        if not unassigned:
            return  # All assigned — no manifest needed

        # Throttle: skip if coverage snapshot hasn't changed since last injection
        current_snapshot = (
            coverage["total_requirements"],
            coverage["assigned"],
            len(unassigned),
        )
        last_snapshot = self.agent.data.get("_coverage_gate_last_snapshot")
        if last_snapshot == current_snapshot:
            return  # Coverage unchanged — skip redundant injection

        # Build and inject manifest
        manifest = _build_requirements_manifest(unassigned, coverage)
        await self.agent.hist_add_warning(manifest)
        self.agent.data["_coverage_gate_last_snapshot"] = current_snapshot

        logger.info(
            f"[DECOMPOSITION GATE] Injected requirements manifest: "
            f"{len(unassigned)} unassigned out of {coverage['total_requirements']} total "
            f"(coverage: {coverage['assigned']}/{coverage['total_requirements']})"
        )


def _build_enforcement_block(agent_data, coverage, get_unassigned_fn, tool_name="call_subordinate"):
    """Build a Response that blocks the delegation for missing requirement_ids.

    Args:
        agent_data: The agent.data dict
        coverage: Coverage stats dict with total_requirements and assigned
        get_unassigned_fn: Function to get unassigned requirements
        tool_name: The tool being blocked (affects format guidance)

    Returns a Response object (from python.helpers.tool) so the extension
    framework replaces the tool's execution with this message.
    """
    from python.helpers.tool import Response

    unassigned = get_unassigned_fn(agent_data)
    unassigned_list = "\n".join(
        f"  - **{r['id']}** [{r.get('category', 'general')}]: {r.get('text', '')[:80]}"
        for r in unassigned[:8]
    )

    total = coverage["total_requirements"]
    assigned = coverage["assigned"]

    # RCA-259 Fix B: Format-specific guidance for batch vs singular calls
    is_batch = tool_name in ("call_subordinate_batch", "fan_out_subordinates")

    if is_batch:
        fix_guidance = (
            f"**FIX**: Re-call `{tool_name}` with `requirement_ids` on **each task** "
            f"in the tasks array. Each task object must include its own requirement_ids.\n\n"
            f"```json\n"
            f'{{"tasks": [\n'
            f'  {{"profile": "code", "message": "...", "requirement_ids": ["REQ-XXX", "REQ-YYY"]}},\n'
            f'  {{"profile": "code", "message": "...", "requirement_ids": ["REQ-ZZZ"]}}\n'
            f"]}}\n"
            f"```\n\n"
            f"Research/architect/frontend profiles are exempt from this requirement."
        )
    else:
        fix_guidance = (
            f"**FIX**: Re-call `call_subordinate` with `requirement_ids` specifying "
            f"which REQ-IDs this delegation will implement. "
            f"Research/architect/frontend profiles are exempt from this requirement."
        )

    message = (
        f"⛔ DELEGATION BLOCKED — MISSING requirement_ids\n\n"
        f"❌ **NOTHING WAS EXECUTED.** No subordinates were created. "
        f"Your delegation call was intercepted and did NOT run.\n\n"
        f"You have {total} tracked requirements ({assigned} already assigned). "
        f"This delegation MUST include `requirement_ids=[\"REQ-XXX\", ...]` to "
        f"link it to specific requirements from the ledger.\n\n"
        f"### Unassigned Requirements\n"
        f"{unassigned_list}\n\n"
        f"{fix_guidance}\n\n"
        f"⚠️ Do NOT call `wait()` — nothing is running yet."
    )

    logger.warning(
        f"[DECOMPOSITION GATE] HARD BLOCK: Delegation missing requirement_ids "
        f"({assigned}/{total} assigned, {len(unassigned)} unassigned)"
    )

    return Response(message=message, break_loop=False)


def _build_requirements_manifest(
    unassigned: list,
    coverage: dict,
) -> str:
    """Build a structured requirements manifest for injection.

    Args:
        unassigned: List of unassigned requirement dicts
        coverage: Coverage statistics dict

    Returns:
        Formatted manifest string
    """
    total = coverage["total_requirements"]
    assigned = coverage["assigned"]
    pct = int(assigned / max(total, 1) * 100)

    lines = [
        "## ⚠️ REQUIREMENTS COVERAGE ALERT",
        "",
        f"**Coverage: {assigned}/{total} ({pct}%)** — "
        f"**{len(unassigned)}** requirements are NOT YET assigned to any delegation.",
        "",
        "### Unassigned Requirements (MUST be covered)",
        "",
    ]

    for req in unassigned:
        lines.append(f"- **{req['id']}** [{req.get('category', 'general')}]: {req.get('text', 'Unknown')}")

    lines.extend([
        "",
        "**ACTION REQUIRED**: Ensure your delegation plan covers ALL unassigned "
        "requirements above. Include `requirement_ids=[\"REQ-XXX\", ...]` in each "
        "`call_subordinate` call to link delegations to specific requirements.",
        "",
        "---",
    ])

    return "\n".join(lines)


def _get_agent_label(agent) -> str:
    """Build a human-readable label for the calling agent."""
    name = getattr(agent, "agent_name", "unknown")
    number = getattr(agent, "number", "?")
    return f"Agent #{number} ({name})"


def auto_link_on_circuit_breaker(agent_data: dict, tool_args: dict) -> None:
    """RCA-271: Auto-assign unassigned requirements when circuit breaker fires.

    When the decomposition gate's MAX_REQ_ID_BLOCKS circuit breaker lets a
    delegation through without requirement_ids, this function assigns ALL
    unassigned requirements to the delegation's tool_args. This ensures
    mark_delegation_complete() in _21_tool_call_tracker can resolve them.

    Without this, the ledger never learns what was completed, causing the
    orchestrator to re-delegate the same scope infinitely.

    Args:
        agent_data: The agent.data dict containing the requirements ledger
        tool_args: The delegation's tool arguments (mutated in place)
    """
    # Don't overwrite existing requirement_ids
    if tool_args.get("requirement_ids"):
        return

    try:
        from python.helpers.requirements_ledger import get_unassigned_requirements
        unassigned = get_unassigned_requirements(agent_data)
        if unassigned:
            ids = [r["id"] for r in unassigned]
            tool_args["requirement_ids"] = ids
            logger.info(
                f"[DECOMPOSITION GATE] RCA-271: Auto-linked {len(ids)} "
                f"unassigned requirements to circuit-breaker delegation: "
                f"{', '.join(ids[:5])}{'...' if len(ids) > 5 else ''}"
            )
    except Exception as e:
        logger.debug(f"[DECOMPOSITION GATE] Auto-link failed: {e}")


def _extract_req_ids_from_single_text(text: str) -> list:
    """Extract REQ-xxx patterns from a single text string.

    Matches patterns like REQ-a1b2c3d4, REQ-12345678, etc.
    The GUID format is REQ- followed by 8 hex characters.

    Args:
        text: Text to search for REQ-xxx patterns

    Returns:
        List of unique REQ-xxx strings found
    """
    import re
    pattern = r'\bREQ-[a-f0-9]{8}\b'
    matches = re.findall(pattern, text, re.IGNORECASE)
    # Normalize to uppercase and deduplicate preserving order
    seen = set()
    result = []
    for m in matches:
        normalized = m.upper()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _extract_req_ids_from_text(tool_args: dict, tool_name: str) -> list:
    """Extract REQ-xxx patterns from delegation message(s).

    For single delegations, extracts from the message field.
    For batch delegations, extracts from all task messages.

    Args:
        tool_args: The tool call arguments
        tool_name: The tool being called

    Returns:
        List of unique REQ-xxx strings found across all messages
    """
    all_ids = []

    if tool_name == "call_subordinate_batch":
        tasks = tool_args.get("tasks", [])
        for task in (tasks if isinstance(tasks, list) else []):
            if isinstance(task, dict):
                msg = task.get("message", "")
                all_ids.extend(_extract_req_ids_from_single_text(msg))
    else:
        msg = tool_args.get("message", "")
        all_ids = _extract_req_ids_from_single_text(msg)

    # Deduplicate preserving order
    seen = set()
    result = []
    for req_id in all_ids:
        if req_id not in seen:
            seen.add(req_id)
            result.append(req_id)
    return result
