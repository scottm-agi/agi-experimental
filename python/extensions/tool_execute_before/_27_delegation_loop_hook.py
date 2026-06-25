"""
Delegation Loop Detector — tool_execute_before extension.

Runs BEFORE call_subordinate invocations and detects when a parent agent
is stuck delegating the same task repeatedly to different subordinates.

After N identical delegations (default 3), injects a structured diagnostic.
After 2N (default 6), hard-blocks the delegation.

Root cause (ADR-019, Iteration 151):
    Parent orchestrator entered 50-minute death spiral delegating identical
    "Phase 5: verify build" task to Ask, E2e, Researcher, Frontend agents.
    Each completed but parent re-delegated. No circuit breaker existed.

Hooks into: tool_execute_before (order 27)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from python.helpers.extension import Extension
from python.helpers.delegation_loop_detector import DelegationLoopDetector, get_detector_key
from python.helpers.delegation_topic_dedup import TopicDedupTracker
from python.helpers.phase_category import PhaseCategory
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.delegation_loop_hook")

# Tool names that delegate to subordinates
DELEGATION_TOOLS = {"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"}

# Singleton detector — shared across all agents in the process
# RCA-316c: hard_limit lowered from 6→5 per user requirement.
# At 5 identical delegations, the escape hatch fires and the agent
# must report a PARTIAL completion status.
_global_detector = DelegationLoopDetector(threshold=3, hard_limit=5)

# ITR-29: Topic-based dedup (Layer 2) — catches semantically-identical
# delegations where the orchestrator rewrites the message each time.
# Threshold=3 means: 3 delegations about the same topic = diagnostic.
# C-4: hard_limit=5 means: 5 delegations = HARD BLOCK (return Response).
_global_topic_tracker = TopicDedupTracker(threshold=3, hard_limit=5)


# ── T4: Phase detection helper ────────────────────────────────────────────
def _detect_phase_from_message(message: str) -> Optional[str]:
    """Extract phase seq number from a delegation message.

    Looks for patterns like "Phase 3.1", "Phase 3.2.1", "seq: 3.1",
    'seq: "3.1"' in the message text.

    Args:
        message: The delegation message text.

    Returns:
        Phase seq string (e.g. "3.1"), or None if no phase detected.
    """
    if not message:
        return None
    # Pattern 1: "Phase 3.1" or "Phase 3.2.1"
    match = re.search(r'Phase\s+(\d+\.\d+(?:\.\d+)?)', message, re.IGNORECASE)
    if match:
        return match.group(1)
    # Pattern 2: "seq: 3.1" or 'seq: "3.1"' or "seq: '3.1'"
    match = re.search(r'seq[:\s]+["\']?(\d+\.\d+(?:\.\d+)?)', message, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def interceptor_replace_if_remediation_available(
    agent_data: dict,
    message: str,
    tool_args: dict,
) -> bool:
    """Check if a remediation brief should replace the delegation message.

    Core T4 logic: when a delegation targets a phase that has prior attempt
    history with unresolved issues, replace the LLM's message with a
    programmatic remediation brief that scopes the fix.

    Args:
        agent_data: The agent's data dictionary.
        message: The delegation message text.
        tool_args: Mutable tool arguments dict. If replacement happens,
            tool_args["message"] is overwritten.

    Returns:
        True if message was replaced, False otherwise.
    """
    detected_phase = _detect_phase_from_message(message)
    if not detected_phase:
        return False

    from python.helpers.phase_attempt_ledger import get_attempt_history

    attempt_history = get_attempt_history(agent_data, detected_phase)
    if not attempt_history or attempt_history.get("total_attempts", 0) < 1:
        return False

    # There IS attempt history — build a remediation brief
    from python.helpers.delegation_brief import build_remediation_brief

    project_dir = agent_data.get("_active_project_dir", "")
    remediation = build_remediation_brief(
        phase_seq=detected_phase,
        attempt_history=attempt_history,
        project_dir=project_dir,
        agent_data=agent_data,
    )

    if not remediation:
        # No issues to remediate (all clean) — don't replace
        return False

    tool_args["message"] = remediation
    logger.info(
        f"[DELEGATION LOOP HOOK] Replaced re-delegation for phase "
        f"{detected_phase} with remediation brief "
        f"(attempt #{attempt_history['total_attempts'] + 1})"
    )
    return True


def _try_autocomplete_blocked_phase(
    agent_data: dict,
    message: str,
    agent_id: str,
) -> bool:
    """RCA-473: Check if a HARD-BLOCKED phase's deliverable already exists.

    When the DelegationLoopDetector HARD BLOCKS a delegation, this function
    checks if the phase's expected deliverable file already exists on disk.
    If so, it runs _reconcile_decomp_statuses to auto-complete the phase
    in decomposition_index.json, preventing the deadlock between the
    Phase Order gate (which blocks later phases when this one is "pending")
    and the DelegationLoopDetector (which blocks re-delegation of this phase).

    Root cause (MSR Phase 3, 2026-06-24):
        1. Phase Order gate blocked Phase 1 ("Phase 0.5 still pending")
        2. DelegationLoopDetector blocked Phase 0.5 (7x same topic)
        3. Orchestrator stuck in requirements management loop for 30+ iterations
        4. fix: when HARD BLOCK fires, check if deliverable exists → auto-complete

    Args:
        agent_data: The agent's data dictionary (contains _active_project_dir).
        message: The delegation message text (used to detect phase).
        agent_id: The agent identifier string.

    Returns:
        True if the phase was auto-completed (deliverable exists), False otherwise.
    """
    import json
    import os

    project_dir = agent_data.get("_active_project_dir", "")
    if not project_dir or not os.path.isdir(project_dir):
        return False

    # Detect which phase this delegation targets
    phase_seq = _detect_phase_from_message(message)
    if not phase_seq:
        return False

    # Read decomposition_index.json
    decomp_path = None
    for candidate in [
        os.path.join(project_dir, "decomposition_index.json"),
        os.path.join(project_dir, "docs", "decomposition-index.json"),
    ]:
        if os.path.isfile(candidate):
            decomp_path = candidate
            break

    if not decomp_path:
        return False

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False

    # Normalize to list
    if isinstance(decomp, dict):
        decomp = (
            decomp.get("tasks")
            or decomp.get("milestones")
            or decomp.get("phases")
            or []
        )

    if not isinstance(decomp, list):
        return False

    # Find the target phase
    target_phase = None
    for phase in decomp:
        if str(phase.get("seq", "")) == str(phase_seq):
            target_phase = phase
            break

    if not target_phase:
        return False

    # If already completed, no need to auto-complete
    if target_phase.get("status", "pending") in {"completed", "verified", "done"}:
        return True  # Already done, tell caller it's complete

    # Run _reconcile_decomp_statuses — this checks for deliverable files
    # and auto-completes phases whose output artifacts exist
    try:
        from python.tools.requirements_sync import _reconcile_decomp_statuses
        _reconcile_decomp_statuses(decomp, project_dir, agent_data=agent_data)
    except Exception as e:
        logger.debug(f"[RCA-473] Reconcile failed: {e}")
        return False

    # Check if the phase was auto-completed by reconciliation
    if target_phase.get("status", "pending") in {"completed", "verified", "done"}:
        # Save the updated decomposition back to disk
        try:
            with open(decomp_path, "w", encoding="utf-8") as f:
                json.dump(decomp, f, indent=2, ensure_ascii=False)
            logger.info(
                f"[RCA-473] Auto-completed Phase {phase_seq} in decomposition "
                f"(deliverable exists on disk). Saved to {decomp_path}"
            )
        except Exception as e:
            logger.warning(f"[RCA-473] Failed to save decomposition: {e}")
        return True

    return False


class DelegationLoopHook(Extension):
    """Detect same-delegation spirals and inject tracking metadata.

    Context-aware: only fire during phases where delegation loops are harmful.
    PLANNING/DESIGN delegations are structurally different and less loop-prone.
    """

    CATEGORIES = {
        PhaseCategory.IMPLEMENTATION,
        PhaseCategory.INTEGRATION,
        PhaseCategory.VERIFICATION,
    }

    """Detect same-delegation spirals and inject tracking metadata.
    
    Two responsibilities:
    1. Loop detection — check() for soft/hard warnings (existing)
    2. Tracking injection — record_attempt() to assign hash + seq_id (new)
    
    Enforcement behavior (RCA-281):
    - At threshold (3): inject warning into chat history, allow tool execution
    - At hard_limit (6): BLOCK tool execution by returning a Response object
      that replaces call_subordinate output. The LLM receives the block message
      instead of executing the delegation. This is the structural difference
      from prompt-only fixes — the tool literally cannot execute.
    
    After this hook runs (if not blocked), tool_args will contain:
        _task_hash: str   — 12-char canonical hash of the task message
        _task_seq_id: int  — attempt number for this task (1-based)
    
    call_subordinate.py reads these and injects them into the subordinate.
    """

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        if not tool_name:
            return

        if tool_name.lower() not in DELEGATION_TOOLS:
            return

        if not tool_args or not isinstance(tool_args, dict):
            return

        # Extract the task message from tool args
        message = tool_args.get("message", "") or tool_args.get("task", "")
        if not message:
            return

        # U-15 Fix: Use centralized get_detector_key for context-scoped isolation.
        # Prevents cross-context poisoning where Agent A's failures in one
        # conversation incorrectly block Agent B in a different conversation.
        agent_id = get_detector_key(self.agent)

        # F-6: Extract phase_id for phase-aware loop detection
        phase_id = _detect_phase_from_message(message)

        # 1. Loop detection — check for soft warning or hard block
        diagnostic = _global_detector.check(agent_id, message, phase_id=phase_id)

        if diagnostic:
            # Determine severity: HARD BLOCK vs soft warning
            count = _global_detector.get_delegation_count(agent_id, message)
            is_hard_block = count >= _global_detector.hard_limit

            if is_hard_block:
                # ╔═══════════════════════════════════════════════════════════╗
                # ║ HARD BLOCK: Return Response to PREVENT tool execution    ║
                # ║ RCA-281: This is code-level enforcement. The LLM cannot  ║
                # ║ ignore this — the tool call is replaced with the block   ║
                # ║ message. Previous prompt-only fixes ("NEVER re-delegate  ║
                # ║ verbatim") were ignored by the orchestrator.             ║
                # ║                                                          ║
                # ║ RCA-316c: At 5 identical delegations, the escape hatch   ║
                # ║ fires. The orchestrator MUST call `response` with a      ║
                # ║ PARTIAL completion status, not re-delegate again.        ║
                # ╚═══════════════════════════════════════════════════════════╝

                # Escape hatch: after 3 hard blocks, allow through (ADVISORY).
                # The underlying detector already has its own escape, but this
                # covers the hook's Response-level blocking separately.
                if gate_check(self.agent.data, "delegation_loop_hook_hard"):
                    logger.warning(
                        f"[DELEGATION LOOP HOOK] {agent_id}: "
                        f"Escape hatch — hard blocked, "
                        f"allowing through (ADVISORY)"
                    )
                    # Fall through to tracking (section 3) instead of blocking
                else:
                    # ── RCA-473: Check deliverable before Layer 1 HARD BLOCK ──
                    deliverable_found = False
                    try:
                        deliverable_found = _try_autocomplete_blocked_phase(
                            self.agent.data, message, agent_id
                        )
                    except Exception as e:
                        logger.debug(
                            f"[DELEGATION LOOP HOOK] RCA-473 L1 autocomplete check failed: {e}"
                        )

                    if deliverable_found:
                        logger.info(
                            f"[DELEGATION LOOP HOOK] {agent_id}: "
                            f"RCA-473: Phase deliverable exists — auto-completed (L1). "
                            f"Returning proceed guidance instead of HARD BLOCK."
                        )
                        return Response(
                            message=(
                                "✅ PHASE ALREADY COMPLETE — The deliverable for this "
                                "phase already exists on disk. The phase has been "
                                "auto-completed in the decomposition index. "
                                "**Proceed to the NEXT phase immediately.** "
                                "Do NOT re-delegate this phase."
                            ),
                            break_loop=False,
                        )

                    logger.error(
                        f"[DELEGATION LOOP HOOK] {agent_id}: "
                        f"HARD BLOCK — delegation #{count} blocked. "
                        f"Returning Response to prevent tool execution."
                    )
                    self.agent.log(
                        type="error",
                        heading="🛑 Delegation BLOCKED — Escape Hatch",
                        content=(
                            f"Identical delegation blocked after {count} attempts. "
                            f"You MUST call the `response` tool NOW with a PARTIAL "
                            f"completion status. Report what was accomplished across "
                            f"all {count} attempts and what remains incomplete."
                        ),
                    )
                    return Response(message=diagnostic, break_loop=False)

            else:
                # SOFT WARNING: inject into context, allow execution
                await self.agent.hist_add_warning(diagnostic)
                logger.warning(
                    f"[DELEGATION LOOP HOOK] {agent_id}: "
                    f"Delegation loop detected (#{count}) — injected diagnostic"
                )
                # F-5 (ITR-49): Inject structured recovery strategy
                try:
                    from python.helpers.delegation_loop_detector import generate_loop_recovery_strategy
                    profile = tool_args.get("profile", "") if tool_args else ""
                    strategy = generate_loop_recovery_strategy(
                        count=count,
                        message=message,
                        profile=profile,
                        agent_data=self.agent.data,
                        threshold=_global_detector.threshold,
                        hard_limit=_global_detector.hard_limit,
                    )
                    if strategy:
                        await self.agent.hist_add_warning(strategy)
                        logger.info(
                            f"[DELEGATION LOOP HOOK] {agent_id}: "
                            f"Injected loop recovery strategy (count={count})"
                        )
                except Exception as e:
                    logger.warning(
                        f"[DELEGATION LOOP HOOK] {agent_id}: "
                        f"Failed to generate recovery strategy: {e}"
                    )

        # 2. ITR-29: Topic-based dedup (Layer 2)
        # Catches re-delegations where the orchestrator rewrites the message
        # each time but the underlying topic (API name + error code) is the same.
        # Restore persisted state on first invocation after restart.
        # F-2 (ITR-45): Guard is per-tracker-per-agent, NOT per-extension-instance.
        # Multiple extension instances share _global_topic_tracker; the old
        # `hasattr(self, '_topic_restored')` guard was per-instance, so each
        # new instance re-restored, doubling clusters every monologue cycle.
        if agent_id not in _global_topic_tracker._restored_agents:
            _global_topic_tracker.restore_from_agent_data(self.agent.data, agent_id)
            _global_topic_tracker._restored_agents.add(agent_id)

        if not diagnostic:  # Only check if Layer 1 didn't already fire
            profile = tool_args.get('profile', '') if tool_args else ''

            # Fix 3.3: Universal task-type extraction (not phase-number dependent).
            # Scopes topic clusters so that semantically similar messages with
            # different delegation types don't falsely collide.
            task_type = ""
            # 1. Profile is always available and universally meaningful
            if profile:
                task_type = profile
            # 2. If decomposition metadata has a category, append for finer scope
            if tool_args:
                task_type_from_args = (
                    tool_args.get("category", "") or tool_args.get("task_type", "")
                )
                if task_type_from_args:
                    task_type = (
                        f"{task_type}:{task_type_from_args}" if task_type
                        else task_type_from_args
                    )

            topic_diagnostic = await _global_topic_tracker.check_async(
                agent_id, message, profile=profile, task_type=task_type
            )
            if topic_diagnostic:
                # C-4: Check if this is a HARD BLOCK (topic count >= hard_limit)
                if "HARD_BLOCK" in topic_diagnostic:
                    # Escape hatch: after 3 topic hard blocks, allow through.
                    if gate_check(self.agent.data, "topic_dedup_hook_hard"):
                        logger.warning(
                            f"[DELEGATION LOOP HOOK] {agent_id}: "
                            f"Topic escape hatch — hard blocked, "
                            f"allowing through (ADVISORY)"
                        )
                        # Fall through to tracking instead of blocking
                    else:
                        # ── RCA-473: Deliverable-exists auto-complete ──────────
                        # When a phase delegation is HARD BLOCKED, check if
                        # the phase's deliverable already exists on disk.
                        # If so, auto-complete the phase and tell the
                        # orchestrator to proceed — preventing the deadlock
                        # between Phase Order gate and DelegationLoopDetector.
                        deliverable_found = False
                        try:
                            deliverable_found = _try_autocomplete_blocked_phase(
                                self.agent.data, message, agent_id
                            )
                        except Exception as e:
                            logger.debug(
                                f"[DELEGATION LOOP HOOK] RCA-473 autocomplete check failed: {e}"
                            )

                        if deliverable_found:
                            logger.info(
                                f"[DELEGATION LOOP HOOK] {agent_id}: "
                                f"RCA-473: Phase deliverable exists — auto-completed. "
                                f"Returning proceed guidance instead of HARD BLOCK."
                            )
                            _global_topic_tracker.save_to_agent_data(self.agent.data, agent_id)
                            return Response(
                                message=(
                                    "✅ PHASE ALREADY COMPLETE — The deliverable for this "
                                    "phase already exists on disk. The phase has been "
                                    "auto-completed in the decomposition index. "
                                    "**Proceed to the NEXT phase immediately.** "
                                    "Do NOT re-delegate this phase."
                                ),
                                break_loop=False,
                            )

                        logger.error(
                            f"[DELEGATION LOOP HOOK] {agent_id}: "
                            f"Topic-based HARD BLOCK — returning Response to prevent execution"
                        )
                        self.agent.log(
                            type="error",
                            heading="🛑 Topic Delegation BLOCKED — Escape Hatch",
                            content=(
                                f"Same topic delegated too many times. "
                                f"You MUST call the `response` tool NOW with a PARTIAL "
                                f"completion status."
                            ),
                        )
                        # Persist before blocking
                        _global_topic_tracker.save_to_agent_data(self.agent.data, agent_id)
                        return Response(message=topic_diagnostic, break_loop=False)

                else:
                    # ── T4: SOFT WARNING — intercept with remediation brief ──
                    # Before injecting the diagnostic, check if we can replace
                    # the LLM's message with a scoped remediation brief.
                    try:
                        replaced = interceptor_replace_if_remediation_available(
                            agent_data=self.agent.data,
                            message=message,
                            tool_args=tool_args,
                        )
                        if replaced:
                            # Message was replaced with remediation brief.
                            # Skip the topic diagnostic — the remediation brief
                            # is structurally different, so topic dedup won't
                            # fire on the next check.
                            _global_topic_tracker.save_to_agent_data(self.agent.data, agent_id)
                            # Fall through to tracking (section 3) — allow delegation
                        else:
                            # No remediation available — inject diagnostic as before
                            logger.warning(
                                f"[DELEGATION LOOP HOOK] {agent_id}: "
                                f"Topic-based loop detected — injecting diagnostic"
                            )
                            await self.agent.hist_add_warning(topic_diagnostic)
                    except Exception as _t4_err:
                        logger.warning(
                            f"[DELEGATION LOOP HOOK] {agent_id}: "
                            f"T4 interceptor failed (non-fatal): {_t4_err}"
                        )
                        # Fall back to original behavior
                        logger.warning(
                            f"[DELEGATION LOOP HOOK] {agent_id}: "
                            f"Topic-based loop detected — injecting diagnostic"
                        )
                        await self.agent.hist_add_warning(topic_diagnostic)

        # Persist topic counts to agent.data so they survive restarts
        _global_topic_tracker.save_to_agent_data(self.agent.data, agent_id)

        # 3. Tracking metadata injection
        # record_attempt returns (task_hash, sequence_id) for this delegation
        task_hash, seq_id = _global_detector.record_attempt(agent_id, message, phase_id=phase_id)
        tool_args["_task_hash"] = task_hash
        tool_args["_task_seq_id"] = seq_id

        logger.info(
            f"[DELEGATION LOOP HOOK] {agent_id}: "
            f"Tracking: hash={task_hash}, seq={seq_id}"
        )

        # Progress tracking (RCA-352)
        try:
            from python.helpers.delegation_progress_tracker import record_delegation, check_progress
            record_delegation(self.agent.data, message, tool_args.get("profile", ""))
            count = self.agent.data.get("_delegation_progress", {}).get("total_count", 0)
            if count > 0 and count % 10 == 0:
                stall_signal = check_progress(self.agent.data)
                if stall_signal:
                    # Emit as L1 escalation signal for supervisor
                    signals = self.agent.data.get("_l2_escalation_signals", [])
                    if not isinstance(signals, list):
                        signals = []
                    signals.append(stall_signal)
                    self.agent.data["_l2_escalation_signals"] = signals
                    logger.warning(
                        f"[DELEGATION LOOP HOOK] {agent_id}: "
                        f"Progress stall detected (stall_count={stall_signal.get('stall_count', 0)})"
                    )
        except Exception as e:
            logger.warning(f"[DELEGATION LOOP HOOK] Progress tracking error: {e}")
