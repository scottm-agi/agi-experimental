"""
Layer 2: Intelligent Supervisor — message_loop_start Extension

Dual-activation LLM observer that monitors agent health:
  1. EVENT-TRIGGERED: Activated immediately by Layer 1 structural guard signals
  2. TIMER-TRIGGERED: Periodic self-check every L2_PERIODIC_TURNS turns,
     even when no L1 signals exist (catches stuck-mid-turn agents)
  3. WALL-CLOCK CHECK: Detects agents whose _last_turn_timestamp is stale,
     indicating a hung LLM call or orphaned subordinate

Design Principles:
- Dual activation: both event-driven (L1 signals) AND timer-driven (periodic)
- Wall-clock stall detection via _last_turn_timestamp from L1 structural guards
- LLM returns ONE of: CONTINUE / REDIRECT / STOP_AND_DELIVER / ESCALATE_TO_USER
- Cooldown prevents rapid-fire LLM calls (minimum 5 turns between calls)
- Decoupled from completion gates (L2 is an observer, not a blocker)

Actions:
- CONTINUE: No intervention needed. Agent is making valid progress.
- REDIRECT: Agent is stuck/looping. Inject corrective guidance that
  refocuses the agent on the original mission.
- STOP_AND_DELIVER: Agent is in a death spiral. Force-exit the loop
  and deliver best-effort results to the user.
- ESCALATE_TO_USER: Agent needs non-credential human input (design
  preferences, ambiguous requirements). For missing credentials/API keys,
  REDIRECT the agent to use `request_secret` instead.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from python.helpers.extension import Extension
from python.helpers.agent_core.base import AgentContextType
from python.helpers.requirements_ledger import get_delegation_ledger_for_gate
from python.helpers.signal_quality import score_signal, ScoredSignal
from python.helpers.nudge_tracker import BurstLimiter, NudgeRecord, evaluate_nudge_effectiveness
from python.helpers.output_truncation import truncate_output_middle_out


if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.extensions.intelligent_supervisor")

# Minimum turns between L2 LLM calls to prevent spam
L2_COOLDOWN_TURNS = 5

# Periodic check interval: L2 self-activates every N turns even without L1 signals.
# This catches agents that appear healthy to L1 but are actually stalled.
L2_PERIODIC_TURNS = 10

# Wall-clock stall threshold: if an agent's _last_turn_timestamp is older than
# this many seconds, it's considered stuck mid-turn (hung LLM, orphaned sub).
STALL_THRESHOLD_SECONDS = 300  # 5 minutes

# Supervisor system prompt — instructs the LLM to make a decision
SUPERVISOR_SYSTEM_PROMPT = """You are the AGIX Intelligent Supervisor — an observer that monitors agent execution quality.

You receive:
1. The user's ORIGINAL MISSION (what they asked for)
2. A STATE SNAPSHOT of the agent's current situation
3. ESCALATION SIGNALS from Layer 1 structural guards (deterministic detectors)

Your job: Decide the single best action to take.

RESPONSE FORMAT — You MUST respond with valid JSON only, no markdown:
{
  "action": "CONTINUE" | "REDIRECT" | "STOP_AND_DELIVER" | "ESCALATE_TO_USER",
  "reason": "Brief explanation of your decision",
  "guidance": "Only for REDIRECT: Specific corrective instructions for the agent"
}

DECISION RULES:
- CONTINUE: Agent is making valid progress even if slow. Data processing, long builds, multi-file edits — these are VALID work, not loops. ALWAYS default to CONTINUE unless there's strong evidence of a real problem.
- REDIRECT: Agent is genuinely stuck — repeating the same failing action, fixated on an approach that won't work. Provide specific alternative guidance.
- STOP_AND_DELIVER: Agent is in a death spiral — escalating errors, burning tokens with zero progress. A partial answer is better than no answer.
- ESCALATE_TO_USER: Agent needs information only the user can provide (design preferences, ambiguous requirements). NEVER use ESCALATE_TO_USER for missing credentials, API keys, or tokens — instead use REDIRECT and instruct the agent to call the `request_secret` tool, which sends a non-blocking notification to the user while the agent can continue with other work.

SIGNAL-SPECIFIC REDIRECT GUIDANCE (Iteration 149 RCA — mandatory for each detector type):
When you issue a REDIRECT, your guidance MUST be specific to the detector signal. Generic advice like "try a different approach" is FORBIDDEN — it gets ignored 84% of the time. Use these rules:

1. **tool_call_repetition** (code_execution_tool + grep/rg/search pattern):
   Guidance MUST say: "STOP using grep/rg/search/find entirely. The matches you are finding are likely in build cache files (tsconfig.tsbuildinfo) or binary artifacts (.agix.proj/memory/index.pkl), NOT in source code. Instead: (a) read the actual source files directly with `cat src/path/to/file.ts`, (b) list the src/ directory to find the correct files, (c) if looking for imports, use `rg 'pattern' src/ -t ts -t tsx` to search ONLY source files."

2. **tool_call_repetition** (code_execution_tool + curl/HTTP pattern):
   Guidance MUST say: "STOP curling routes. The dev server is likely dead after a dependency change. You MUST: (1) kill the old server process, (2) run `npm install`, (3) restart the dev server using the services_mgt tool, (4) THEN retry curl. A dead server returns 500 for ALL routes — this is NOT a code bug."

3. **tool_call_repetition** (write_to_file or replace_in_file, same path):
   Guidance MUST say: "You are rewriting the same file repeatedly. STOP and verify: (a) read the file back to confirm your changes actually persisted, (b) if the file looks correct, MOVE ON to the next task, (c) do not optimize or tweak a file that is already working."

4. **error_cascade**:
   Guidance MUST say: "Multiple consecutive errors indicate a fundamental approach problem. STOP your current strategy. Read the actual error messages carefully and address the ROOT CAUSE, not the symptoms. If the errors are about missing modules, check package.json and node_modules. If about TypeScript types, check tsconfig.json."

5. **monologue_loop**:
   Guidance MUST say: "You have been reasoning without executing any tool. You MUST call a tool NOW. If you are unsure what to do, use the response tool to deliver your current progress."

6. **tool_call_repetition** (any other tool):
   Guidance MUST say: "You have called the same tool {N} times consecutively. This is not productive. Change your approach: try a different tool, break the problem into smaller pieces, or deliver what you have so far."

7. **DEATH SPIRAL**: When circuit breaker is active AND consecutive mistakes >= 12, the agent is
   in an unrecoverable state. Your decision MUST be STOP_AND_DELIVER. Never allow more tool calls.

8. **infra_build_loop** (agent editing source files then checking dev server, seeing same error, editing again):
   Guidance MUST say: "Read the FULL build error output. If it says 'prerendering page' or 'useContext', this is a CODE BUG — add `export const dynamic = 'force-dynamic'` or `'use client'` directive to the affected pages. If it says '<Html> should not be imported outside of pages/_document', remove `next/document` imports and use App Router patterns (`not-found.tsx`, `error.tsx`). Only clear `.next/cache` (NOT the entire .next directory) if the error specifically references stale cache files. Restart the dev server via `services_mgt` tool after fixing."

CRITICAL: Err on the side of CONTINUE. Long-running data processing is NOT a loop. Agents doing thorough work with many tool calls is VALID. Only intervene when you see clear evidence of stuck behavior.

DECOMPOSITION PLAN AWARENESS:
If the state snapshot shows an active DECOMPOSITION PLAN with in-progress or pending tasks, the agent is executing a structured plan. This is VALID WORK — do NOT redirect or suggest alternatives unless the agent is genuinely stuck on a specific task within the plan. A planned decomposition with pending tasks is NOT a loop.

5-WHY DIAGNOSTIC METHODOLOGY:
When you detect a potential problem, apply root cause analysis BEFORE deciding to REDIRECT:
1. What is the symptom? (e.g., "agent called the same tool 5 times")
2. WHY is the agent doing this? (e.g., "because the build keeps failing")
3. WHY is the build failing? (e.g., "because a dependency is missing")
4. WHY is the dependency missing? (e.g., "because npm install wasn't run after package.json change")
5. ROOT CAUSE → Your REDIRECT guidance must address THIS level, not the symptom.

Your guidance must target the root cause. "Try a different approach" is FORBIDDEN — it's symptom-level advice that gets ignored 84% of the time.

FILE WRITE CONFLICT AWARENESS:
If the state snapshot shows FILE WRITE CONFLICTS, it means multiple different agents wrote the same file(s) during batch execution. This is a high-risk situation — the last writer's content survives but earlier agents' work may be silently lost. When you see file write conflicts:
- If the conflicting file is a shared coordination file (e.g., decomposition_index.json), this is usually benign — CONTINUE.
- If the conflicting file is a source code file (e.g., .tsx, .ts, .py, .css), this likely indicates a task decomposition overlap — consider REDIRECT with guidance to re-check the conflicting file's content for completeness.
- If 3+ agents wrote the same source file, this is a critical decomposition failure — consider STOP_AND_DELIVER with a warning about potential data loss."""


class IntelligentSupervisor(Extension):
    """
    Layer 2: Intelligent Supervisor — LLM-powered observer.

    Dual activation:
      - Event-triggered: L1 escalation signals → immediate LLM check
      - Timer-triggered: Every L2_PERIODIC_TURNS → periodic health check
      - Wall-clock: _last_turn_timestamp stale → stall detection

    When triggered:
    1. Extracts the user's original mission
    2. Builds a structured state snapshot
    3. Calls a utility LLM for a decision
    4. Executes the decision (CONTINUE/REDIRECT/STOP/ESCALATE)
    """

    async def execute(self, loop_data: Optional["LoopData"] = None, **kwargs) -> None:
        if not loop_data:
            return

        agent = self.agent
        if not agent:
            return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ TRIPLE ACTIVATION: L1 + external signals OR periodic/stall║
        # ╚═══════════════════════════════════════════════════════════╝
        # RCA-249: L2 now consumes signals from TWO sources:
        #   1. _l2_escalation_signals — L1 structural guards (in-loop)
        #   2. _l2_external_signals — background monitoring (check-ins, loops)
        # Two separate keys prevent race conditions between the background
        # monitoring thread and the in-loop extension.
        l1_signals = agent.data.get("_l2_escalation_signals")
        external_signals = agent.data.get("_l2_external_signals")
        signals = None
        activation_reason = None

        if l1_signals:
            signals = l1_signals
            activation_reason = "l1_escalation"
        elif external_signals:
            # External signals from monitoring (health checks, loop detection)
            signals = external_signals
            activation_reason = "external_monitoring"
            logger.info(
                f"[INTELLIGENT SUPERVISOR] External monitoring signals received: "
                f"{len(external_signals)} signal(s)"
            )
        else:
            # No signals from either source — check periodic timer and wall-clock stall
            activation_reason = self._check_periodic_or_stall(agent)

        if not activation_reason:
            # Truly nothing to do — fast path (95% case)
            return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ COOLDOWN: Don't call LLM too frequently                  ║
        # ╚═══════════════════════════════════════════════════════════╝
        last_call_turn = agent.data.get("_last_l2_llm_call_turn", 0)
        if (agent._absolute_turns - last_call_turn) < L2_COOLDOWN_TURNS:
            # RCA-256: Critical signals MUST bypass cooldown.
            # The same_message_bridge escalates to severity=critical when
            # cumulative repeats reach threshold. If L2 was called recently
            # for a routine check, the critical signal would be consumed
            # (popped) and never acted on — leaving the agent stuck.
            has_critical = False
            for sig_list in (l1_signals, external_signals):
                if sig_list:
                    for sig in sig_list:
                        sev = sig.get("severity", "") if isinstance(sig, dict) else getattr(sig, "severity", "")
                        if sev == "critical":
                            has_critical = True
                            break
                if has_critical:
                    break

            if has_critical:
                logger.warning(
                    f"[INTELLIGENT SUPERVISOR] Critical signal detected — "
                    f"BYPASSING cooldown (last call turn {last_call_turn}, "
                    f"current turn {agent._absolute_turns})"
                )
            else:
                logger.debug(
                    f"[INTELLIGENT SUPERVISOR] Cooldown active — skipping LLM call "
                    f"(last call at turn {last_call_turn}, current turn {agent._absolute_turns})"
                )
                # Consume signals so they don't accumulate
                agent.data.pop("_l2_escalation_signals", None)
                agent.data.pop("_l2_external_signals", None)
                return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ RCA-352 F-2: Stage 3 delegation stall — force stop       ║
        # ╚═══════════════════════════════════════════════════════════╝
        # If a delegation_stall signal has stall_count >= 3, skip the LLM
        # call entirely and force-stop immediately. This prevents burning
        # additional tokens on an agent that has made zero progress in 30+
        # delegations.
        if signals:
            for sig in signals:
                if (isinstance(sig, dict)
                        and sig.get('detector') == 'delegation_stall'
                        and sig.get('stall_count', 0) >= 3):
                    stall_count = sig['stall_count']
                    loop_data.is_done = True
                    loop_data.stop_reason = (
                        f"Supervisor: delegation stall stage 3 — no progress "
                        f"in {stall_count * 10}+ delegations"
                    )
                    logger.warning(
                        f"[INTELLIGENT SUPERVISOR] Delegation stall stage 3 — "
                        f"force-stopping agent (stall_count={stall_count})"
                    )
                    # Consume signals
                    agent.data.pop("_l2_escalation_signals", None)
                    agent.data.pop("_l2_external_signals", None)
                    return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ P1-3: QUALITY SCORING — filter signals before LLM call   ║
        # ╚═══════════════════════════════════════════════════════════╝
        # Score each signal through the quality scorer. Only signals that
        # pass the quality threshold (or are critical) proceed to the LLM.
        # This replaces 6 overlapping noise suppressors with a single layer.
        nudge_history: list = agent.data.get("_nudge_history", [])
        if signals:
            scored = [score_signal(s, agent.data, nudge_history) for s in signals]
            suppressed = [s for s in scored if not s.should_fire]
            signals = [s.original for s in scored if s.should_fire]
            if suppressed:
                logger.info(
                    f"[INTELLIGENT SUPERVISOR] P1-3 Quality filter suppressed "
                    f"{len(suppressed)}/{len(scored)} signals: "
                    f"{[s.suppression_reason for s in suppressed]}"
                )
            if not signals:
                logger.info(
                    "[INTELLIGENT SUPERVISOR] All signals suppressed by quality filter — skipping LLM call"
                )
                agent.data.pop("_l2_escalation_signals", None)
                agent.data.pop("_l2_external_signals", None)
                return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ P1-3: BURST LIMITER — prevent nudge storms               ║
        # ╚═══════════════════════════════════════════════════════════╝
        burst_limiter: BurstLimiter = agent.data.get("_nudge_burst_limiter")
        if burst_limiter is None:
            burst_limiter = BurstLimiter()
            agent.data["_nudge_burst_limiter"] = burst_limiter

        current_turn = getattr(agent, "_absolute_turns", 0)
        if not burst_limiter.can_nudge(current_turn):
            logger.info(
                f"[INTELLIGENT SUPERVISOR] P1-3 Burst limiter blocked nudge "
                f"at turn {current_turn} — rate limited"
            )
            agent.data.pop("_l2_escalation_signals", None)
            agent.data.pop("_l2_external_signals", None)
            return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ P1-3: EVALUATE PAST NUDGE EFFECTIVENESS                  ║
        # ╚═══════════════════════════════════════════════════════════╝
        # Update was_effective on recent nudge records so future scoring
        # can suppress repeat signals for detectors that ignored nudges.
        for record in nudge_history:
            if isinstance(record, NudgeRecord) and not record.was_effective:
                record.was_effective = evaluate_nudge_effectiveness(
                    record, current_turn, agent.data
                )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ CALL LLM: Build context and get decision                 ║
        # ╚═══════════════════════════════════════════════════════════╝
        # For timer/stall activation, build synthetic health-check signals
        if not signals:
            signals = self._build_periodic_check_signals(agent, activation_reason)

        logger.info(
            f"[INTELLIGENT SUPERVISOR] Activated via {activation_reason} — "
            f"calling LLM with {len(signals) if signals else 0} signals"
        )

        decision = await self._call_supervisor_llm(agent, signals)

        # Record this call
        agent.data["_last_l2_llm_call_turn"] = agent._absolute_turns
        # Consume ALL signal sources
        agent.data.pop("_l2_escalation_signals", None)
        agent.data.pop("_l2_external_signals", None)

        if not decision:
            logger.warning("[INTELLIGENT SUPERVISOR] LLM returned no decision — defaulting to CONTINUE")
            return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ EXECUTE DECISION                                         ║
        # ╚═══════════════════════════════════════════════════════════╝
        action = decision.get("action", "CONTINUE").upper()
        reason = decision.get("reason", "No reason provided")

        logger.info(
            f"[INTELLIGENT SUPERVISOR] Decision: {action} — {reason}"
        )

        if action == "CONTINUE":
            # H-22 / Systems Audit: Suppress CONTINUE when budget > 90% used.
            # The supervisor LLM has no visibility into budget state and will
            # keep saying CONTINUE while _budget_expiring says STOP — deadlock.
            if agent.data.get("_budget_expiring"):
                logger.warning(
                    f"[INTELLIGENT SUPERVISOR] Overriding CONTINUE → STOP_AND_DELIVER: "
                    f"_budget_expiring is set. Agent must deliver, not continue."
                )
                decision["action"] = "STOP_AND_DELIVER"
                decision["reason"] = (
                    f"Budget is expiring. Original supervisor decision was CONTINUE "
                    f"({reason}), but budget constraints require delivery now."
                )
                action = "STOP_AND_DELIVER"
            else:
                # No intervention — agent is making valid progress
                return

        # Context-aware decision filtering: TASK agents cannot be killed
        decision = self._filter_decision_for_context(agent, decision)
        action = decision.get("action", "CONTINUE").upper()

        if action == "CONTINUE":
            return

        elif action == "REDIRECT":
            # Fix Iter10b: Cap consecutive supervisor redirects to prevent
            # infinite loops. Root cause: gate escape → dup detector blocks →
            # supervisor redirects → agent produces duplicate → dup detector
            # blocks again → supervisor redirects again → loop forever.
            from python.helpers.supervisor_redirect_cap import (
                increment_redirect_counter,
                should_suppress_redirect,
            )
            if should_suppress_redirect(agent.data):
                # 5-Why RCA (2026-04-22): Previously just suppressed the redirect,
                # letting the agent continue looping forever. Now escalates to
                # STOP_AND_DELIVER — force-kills the stuck agent so the parent
                # orchestrator can re-batch pending work with a fresh subordinate.
                logger.warning(
                    f"[INTELLIGENT SUPERVISOR] REDIRECT cap reached — "
                    f"escalating to STOP_AND_DELIVER (force-kill stuck agent)"
                )
                loop_data = agent.loop_data
                loop_data.is_done = True
                loop_data.stop_reason = (
                    "Supervisor: redirect cap reached — agent ignored "
                    f"{agent.data.get('_consecutive_supervisor_redirects', 0)} "
                    f"consecutive redirects, force-killing"
                )
                agent.context._execution_status = "FAILED"
                agent.context._failure_reason = "tool_repetition_loop"
                # Also clear duplicate detector state for clean restart
                agent.data.pop("_last_response_content", None)
                return
            increment_redirect_counter(agent.data)

            guidance = decision.get("guidance", "")
            # Enrich with deterministic signal-specific guidance (Iteration 149 RCA)
            # The LLM-generated guidance is often too generic (84% ignore rate).
            # Append concrete corrective actions based on which detectors fired.
            signal_enrichment = self._get_signal_specific_guidance(agent)
            if signal_enrichment:
                guidance = f"{guidance}\n\n⚠️ MANDATORY CORRECTIVE ACTIONS:\n{signal_enrichment}"

            redirect_msg = (
                f"🔄 **SUPERVISOR REDIRECT**: {reason}\n\n"
                f"**Guidance**: {guidance}\n\n"
                f"Refocus on the original mission and try the suggested approach."
            )
            await agent.hist_add_warning(message=redirect_msg)
            agent.log(
                type="warning",
                heading="🔄 Supervisor Redirect",
                content=reason,
            )

            # ── P1-3: Record NudgeRecord for effectiveness tracking ──
            # Identify the primary detector that triggered this redirect
            primary_detector = "unknown"
            if signals:
                for sig in signals:
                    if isinstance(sig, dict) and sig.get("detector"):
                        primary_detector = sig["detector"]
                        break
            nudge_record = NudgeRecord(
                detector=primary_detector,
                nudge_text=redirect_msg[:200],  # Truncate for memory
                timestamp=time.time(),
                agent_turn_at_nudge=getattr(agent, "_absolute_turns", 0),
                was_effective=False,  # Will be evaluated on next cycle
            )
            nudge_history = agent.data.get("_nudge_history", [])
            nudge_history.append(nudge_record)
            # Bound history to 20 entries
            if len(nudge_history) > 20:
                nudge_history = nudge_history[-20:]
            agent.data["_nudge_history"] = nudge_history

            # Record in burst limiter
            burst_limiter = agent.data.get("_nudge_burst_limiter")
            if burst_limiter:
                burst_limiter.record_nudge(
                    getattr(agent, "_absolute_turns", 0),
                    was_effective=False,
                )

        elif action == "STOP_AND_DELIVER":
            loop_data.is_done = True
            loop_data.stop_reason = f"Intelligent Supervisor: {reason}"
            agent.log(
                type="error",
                heading="🛑 Supervisor Stop",
                content=(
                    f"The supervisor has stopped this agent: {reason}\n\n"
                    f"Delivering best-effort results."
                ),
            )

        elif action == "ESCALATE_TO_USER":
            agent.context.paused = True
            escalation_msg = (
                f"❓ **SUPERVISOR ESCALATION**: {reason}\n\n"
                f"The agent needs your input to continue. "
                f"Please provide guidance and the agent will resume."
            )
            await agent.hist_add_warning(message=escalation_msg)
            agent.log(
                type="warning",
                heading="❓ Needs Human Input",
                content=reason,
            )

        else:
            logger.warning(f"[INTELLIGENT SUPERVISOR] Unknown action: {action}")

    # ------------------------------------------------------------------
    # Periodic Check & Stall Detection
    # ------------------------------------------------------------------

    def _check_periodic_or_stall(self, agent: "Agent") -> Optional[str]:
        """Check if L2 should activate via periodic timer or wall-clock stall.

        Returns activation reason string or None.
        This runs ONLY when there are no L1 signals — it's the independent
        timer loop that catches agents L1 can't see.
        """
        current_turn = getattr(agent, "_absolute_turns", 0)

        # ── EXEMPTION 1: Routing-agent pattern (ISSUE-4 / MSR audit) ──
        # If agent has exactly 1 tool call and it's call_subordinate,
        # the agent's job is done (it just routed). Don't stall-detect.
        recent_calls = agent.data.get("recent_tool_calls", [])
        if (len(recent_calls) == 1
                and recent_calls[0].get("tool_name") == "call_subordinate"):
            return None

        # ── EXEMPTION 2: Parent-waiting-for-child pattern (ISSUE-5 / MSR audit) ──
        # If agent has active (non-completed/non-failed) subordinates in the
        # delegation health ledger, it's legitimately waiting. Don't stall-detect.
        _DONE_STATUSES = {"completed", "failed", "error", "killed"}
        health_ledger = agent.data.get("_delegation_health_ledger", [])
        if health_ledger:
            has_active_child = any(
                entry.get("status", "").lower() not in _DONE_STATUSES
                for entry in health_ledger
            )
            if has_active_child:
                logger.info(
                    f"[INTELLIGENT SUPERVISOR] Exemption: {agent.agent_name} has "
                    f"active subordinate(s) — suppressing stall detection"
                )
                return None

        # --- Timer check: periodic self-activation every L2_PERIODIC_TURNS ---
        last_periodic_turn = agent.data.get("_l2_last_periodic_turn", 0)
        turns_since_periodic = current_turn - last_periodic_turn

        if turns_since_periodic >= L2_PERIODIC_TURNS:
            agent.data["_l2_last_periodic_turn"] = current_turn
            logger.info(
                f"[INTELLIGENT SUPERVISOR] Periodic timer check activated "
                f"(turn {current_turn}, last periodic at {last_periodic_turn})"
            )
            return "periodic_timer"

        # --- Wall-clock check: detect stale _last_turn_timestamp ---
        stall_reason = self._check_agent_health(agent)
        if stall_reason:
            return stall_reason

        return None

    def _check_agent_health(self, agent: "Agent") -> Optional[str]:
        """Check wall-clock staleness of agent's last turn timestamp.

        If _last_turn_timestamp is older than STALL_THRESHOLD_SECONDS,
        the agent is likely stuck mid-turn (hung LLM call, orphaned
        subordinate, etc).

        Returns activation reason string or None.
        """
        last_timestamp = agent.data.get("_last_turn_timestamp")
        if not last_timestamp:
            return None

        elapsed = time.time() - last_timestamp
        if elapsed > STALL_THRESHOLD_SECONDS:
            logger.warning(
                f"[INTELLIGENT SUPERVISOR] Wall-clock stall detected! "
                f"Agent {getattr(agent, 'agent_name', '?')} last turn "
                f"was {elapsed:.0f}s ago (threshold: {STALL_THRESHOLD_SECONDS}s)"
            )
            return "wall_clock_stall"

        return None

    def _build_periodic_check_signals(
        self, agent: "Agent", activation_reason: str
    ) -> List[Dict[str, Any]]:
        """Build synthetic health-check signals for periodic/stall activation.

        When the supervisor activates via timer or stall detection (no L1
        signals exist), we still need to provide the LLM with context
        about why it was called.
        """
        current_turn = getattr(agent, "_absolute_turns", 0)
        last_timestamp = agent.data.get("_last_turn_timestamp", 0)
        elapsed = time.time() - last_timestamp if last_timestamp else -1

        signals = []

        if activation_reason == "periodic_timer":
            signals.append({
                "source": "supervisor_periodic_check",
                "severity": "info",
                "message": (
                    f"Periodic health check at turn {current_turn}. "
                    f"Last turn was {elapsed:.0f}s ago. "
                    f"No L1 escalation signals — checking overall health."
                ),
            })
        elif activation_reason == "wall_clock_stall":
            signals.append({
                "source": "supervisor_wall_clock_stall",
                "severity": "critical",
                "message": (
                    f"WALL-CLOCK STALL DETECTED: Agent has not completed a turn "
                    f"in {elapsed:.0f}s (threshold: {STALL_THRESHOLD_SECONDS}s). "
                    f"Likely stuck on hung LLM call or orphaned subordinate. "
                    f"Current turn: {current_turn}."
                ),
            })

        return signals

    # ------------------------------------------------------------------
    # Mission Extraction
    # ------------------------------------------------------------------

    def _extract_mission(self, agent: "Agent") -> str:
        """Extract the user's original mission from chat history.

        Looks for the first user message in the conversation history.
        This is the foundational context for all supervisor decisions.
        """
        try:
            messages = getattr(agent.history, "messages", [])
            for msg in messages:
                role = getattr(msg, "role", "")
                if role == "user":
                    content = getattr(msg, "content", "")
                    if callable(getattr(msg, "output_text", None)):
                        content = msg.output_text()
                    if content:
                        # Truncate very long missions
                        return truncate_output_middle_out(content, max_chars=2000, head_ratio=0.3)
        except Exception as e:
            logger.debug(f"[INTELLIGENT SUPERVISOR] Error extracting mission: {e}")

        # Fallback: use last_user_message
        if agent.last_user_message:
            content = getattr(agent.last_user_message, "content", "Unknown mission")
            if callable(getattr(agent.last_user_message, "output_text", None)):
                content = agent.last_user_message.output_text()
            return truncate_output_middle_out(str(content), max_chars=2000, head_ratio=0.3)

        return "Unknown mission — no user messages found in history"

    # ------------------------------------------------------------------
    # State Snapshot
    # ------------------------------------------------------------------

    def _build_state_snapshot(self, agent: "Agent") -> str:
        """Build a structured text snapshot of the agent's state.

        This is the context the LLM supervisor receives to make decisions.
        Designed to be compact but informative.

        5-Why RCA (2026-04-18): Added delegation ledger, tool failure context,
        and completion status. Without these, the supervisor had no visibility
        into actual work progress and would mark CONTINUE based solely on
        recent tool fingerprints — even when the agent was in a death spiral.
        """
        signals = agent.data.get("_l2_escalation_signals", [])
        signal_lines = "\n".join(
            f"  - [{s.get('severity', '?')}] {s.get('detector', '?')}: {s.get('detail', '')}"
            for s in signals
        )

        max_turns = agent.get_max_turns()
        turn_pct = (agent._absolute_turns / max_turns * 100) if max_turns > 0 else 0

        # Recent tool call summary from live recent_tool_calls data
        # 5-Why RCA (2026-04-22): Previously read from _md5_action_log which
        # was ALWAYS EMPTY because fingerprint_action() was never called.
        # Now reads from recent_tool_calls which is populated by agent_process_tools.py.
        recent_tools = []
        recent_calls = agent.data.get("recent_tool_calls", [])
        for entry in recent_calls[-10:]:
            tool = entry.get("tool_name", "?")
            args_preview = str(entry.get("tool_args", {}))[:80]
            recent_tools.append(f"  - {tool}: {args_preview}")


        # Delegation ledger — what subordinates have been dispatched and their status
        ledger = get_delegation_ledger_for_gate(agent.data)
        ledger_lines = ""
        if ledger:
            ledger_entries = []
            for entry in ledger[-8:]:  # Last 8 entries
                profile = entry.get("profile", "?")
                status = entry.get("status", "?")
                summary = entry.get("message_summary", "")[:80]
                ledger_entries.append(f"  - [{status}] {profile}: {summary}")
            ledger_lines = "\n".join(ledger_entries)

        # Tool failure context — what's actually breaking
        # 5-Why RCA (2026-04-24, Iteration 152): Previously truncated error
        # context to 100 chars, making supervisor guidance generic ("try different
        # approach"). Expanded to 400 chars so the LLM can see actual error messages
        # (e.g., "Prisma 7.8.0 requires Node.js >= 22.0.0") and give specific fixes.
        failure_counts = agent.data.get("_tool_failure_counts", {})
        error_ctx = agent.data.get("_tool_failure_error_context", {})
        failure_lines = ""
        if failure_counts:
            entries = []
            for tool, count in failure_counts.items():
                last_err = truncate_output_middle_out(error_ctx.get(tool, ""), max_chars=400, head_ratio=0.3)
                entries.append(f"  - {tool}: {count} consecutive failures — {last_err}")
            failure_lines = "\n".join(entries)

        # Dedicated error context section — expanded details for supervisor LLM
        # Shows the last error output for each failing tool with 400-char excerpts.
        # This is the key diagnostic info the supervisor needs to give specific guidance.
        error_detail_lines = ""
        if error_ctx:
            detail_entries = []
            for tool, err_text in error_ctx.items():
                if err_text:
                    excerpt = truncate_output_middle_out(err_text, max_chars=400, head_ratio=0.3)
                    detail_entries.append(f"  [{tool}]:\n    {excerpt}")
            error_detail_lines = "\n".join(detail_entries)

        # Consecutive mistake count
        consecutive_mistakes = agent.data.get("_consecutive_mistake_count", 0)

        # Tool failed this turn flag (wired from _12_tool_failure_tracker)
        tool_failed_this_turn = agent.data.get("_tool_failed_in_current_turn", False)

        # Circuit breaker status (wired from _12_tool_failure_tracker, RCA-289 wiring fix)
        cb_triggered = agent.data.get("_circuit_breaker_triggered", False)
        cb_tool = agent.data.get("_circuit_breaker_tool", "")
        cb_count = agent.data.get("_circuit_breaker_count", 0)
        circuit_breaker_lines = ""
        if cb_triggered:
            circuit_breaker_lines = (
                f"  🚨 CIRCUIT BREAKER ACTIVE: Tool `{cb_tool}` has failed "
                f"{cb_count} consecutive times. Agent should STOP retrying "
                f"this tool and use a completely different approach."
            )

        # Completion gate block count
        from python.helpers.universal_gate_budget import get_block_count
        gate_blocks = get_block_count(getattr(getattr(self, 'agent', None), 'data', {}), "supervisor_gate")

        # Decomposition plan status — suppress supervisor nudges during planned work
        # RCA-237 RC-7: Supervisor issued "explore alternatives" during active decomposition
        decomp_plan = agent.data.get("_decomposition_plan", {})
        decomp_lines = ""
        if decomp_plan:
            total = decomp_plan.get("total_tasks", 0)
            completed = decomp_plan.get("completed_tasks", 0)
            in_progress = decomp_plan.get("in_progress_tasks", 0)
            pending = decomp_plan.get("pending_tasks", 0)
            decomp_lines = (
                f"  Total: {total}, Completed: {completed}, "
                f"In-Progress: {in_progress}, Pending: {pending}"
            )
            if total > 0:
                pct = completed / total * 100
                decomp_lines += f" ({pct:.0f}% done)"

        # ── §10.1 Gate Status — integration blocks, fidelity, contract results ──
        from python.helpers.orchestrator_gate_common import MAX_INTEGRATION_BLOCKS
        integration_blocks = agent.data.get("_integration_block_count", 0)
        critical_blocks = agent.data.get("_critical_check_blocks", {})
        fidelity_violations = agent.data.get("_pending_fidelity_violations", [])
        contract_result = agent.data.get("_last_contract_result", {})
        bypass_report = agent.data.get("_gate_bypass_report", {})

        # Pipeline gap fix: Read blocking gate details so supervisor can
        # diagnose WHY a gate is blocking, not just that it blocked.
        last_gate_block = agent.data.get("_last_gate_block_details", {})

        gate_lines = []
        if integration_blocks > 0:
            gate_lines.append(
                f"  Integration blocks: {integration_blocks}/{MAX_INTEGRATION_BLOCKS}"
                + (" (⚠️ approaching escape hatch)" if integration_blocks >= MAX_INTEGRATION_BLOCKS - 1 else "")
            )
        if critical_blocks:
            cb_parts = [f"{k}: {v}/{MAX_CRITICAL_CHECK_BLOCKS}" for k, v in critical_blocks.items()]
            gate_lines.append(f"  Critical check blocks: {{{', '.join(cb_parts)}}}")
        if fidelity_violations:
            fv_parts = [str(v)[:100] for v in fidelity_violations[:5]]
            gate_lines.append(f"  Fidelity violations: {fv_parts}")
        if contract_result:
            passed = contract_result.get("passed", 0)
            total = contract_result.get("total", 0)
            failed_vals = contract_result.get("failed_values", [])
            gate_lines.append(
                f"  Contract assertions: {passed}/{total} passed"
                + (f" — Missing: {failed_vals[:5]}" if failed_vals else "")
            )
        if bypass_report:
            gate_lines.append(f"  ⚠️ GATE BYPASS ACTIVE — {len(bypass_report.get('incomplete_items', []))} incomplete items")
        if last_gate_block:
            block_gate = last_gate_block.get("gate", "?")
            block_failures = last_gate_block.get("failures", [])
            block_attempt = last_gate_block.get("attempt", 0)
            block_msg = last_gate_block.get("message", "")[:200]
            gate_lines.append(
                f"  ⚠️ LAST GATE BLOCK: gate={block_gate}, "
                f"attempt={block_attempt}, "
                f"failing_checks={block_failures[:3]}"
            )
            if block_msg:
                gate_lines.append(f"    Detail: {block_msg}")
        gate_status = "\n".join(gate_lines) if gate_lines else "  (all gates passing)"

        # ── G-7: File write conflict visibility ──
        file_conflicts = agent.data.get("_file_write_conflicts", [])
        conflict_lines = ""
        if file_conflicts:
            conflict_entries = []
            for c in file_conflicts[:10]:  # Cap at 10 to keep snapshot compact
                path = c.get("path", "?")
                agents = c.get("agents", [])
                conflict_entries.append(
                    f"  - {os.path.basename(path)}: written by {', '.join(agents)}"
                )
            conflict_lines = "\n".join(conflict_entries)

        # ── SS-7: Delegation Health — cross-agent failure patterns ──
        health_ledger = agent.data.get("_delegation_health_ledger", [])
        consec_failures = agent.data.get("_consecutive_failed_delegations", 0)
        health_lines = ""
        if health_ledger:
            recent = health_ledger[-8:]
            entries = []
            for e in recent:
                status_icon = "✅" if e.get("status") == "OK" else "❌"
                reason = e.get("failure_reason", "")[:80]
                entries.append(
                    f"  {status_icon} [{e.get('profile', '?')}] "
                    f"{reason or 'completed'}"
                )
            health_lines = "\n".join(entries)
            if consec_failures >= 3:
                health_lines += f"\n  ⚠️ {consec_failures} CONSECUTIVE FAILURES"

        return (
            f"AGENT STATE SNAPSHOT\n"
            f"====================\n"
            f"Agent: {agent.agent_name} (profile: {agent.config.profile})\n"
            f"Turn: {agent._absolute_turns}/{max_turns} ({turn_pct:.0f}%)\n"
            f"Errors: {agent._error_count}\n"
            f"Failed tool calls: {agent._failed_tool_count}\n"
            f"Consecutive mistakes: {consecutive_mistakes}\n"
            f"Tool failed this turn: {tool_failed_this_turn}\n"
            f"Gate blocks: {gate_blocks}\n"
            f"\n"
            f"CIRCUIT BREAKER STATUS:\n"
            f"{circuit_breaker_lines or '  (not triggered)'}\n"
            f"\n"
            f"GATE STATUS:\n"
            f"{gate_status}\n"
            f"\n"
            f"FILE WRITE CONFLICTS:\n"
            f"{conflict_lines or '  (none)'}\n"
            f"\n"
            f"DECOMPOSITION PLAN STATUS:\n"
            f"{decomp_lines or '  (no active decomposition plan)'}\n"
            f"\n"
            f"ESCALATION SIGNALS:\n"
            f"{signal_lines or '  (none)'}\n"
            f"\n"
            f"DELEGATION LEDGER:\n"
            f"{ledger_lines or '  (no delegations recorded)'}\n"
            f"\n"
            f"DELEGATION HEALTH (subordinate outcomes):\n"
            f"{health_lines or '  (no delegations recorded)'}\n"
            f"\n"
            f"ACTIVE TOOL FAILURES:\n"
            f"{failure_lines or '  (none)'}\n"
            f"\n"
            f"RECENT TOOL ERRORS (full context):\n"
            f"{error_detail_lines or '  (none)'}\n"
            f"\n"
            f"RECENT ACTIONS (last 10):\n"
            f"{chr(10).join(recent_tools) if recent_tools else '  (none)'}\n"
        )

    # ------------------------------------------------------------------
    # Signal-Specific Guidance (Iteration 149 RCA)
    # ------------------------------------------------------------------

    def _get_signal_specific_guidance(self, agent: "Agent") -> str:
        """Generate deterministic corrective guidance based on active signals.

        5-Why RCA (Iteration 149): Supervisor redirect effectiveness was 16%
        because the LLM generated generic guidance. This method appends concrete,
        non-generic corrective actions based on which detectors actually fired.

        RCA-251 §5.4: Now merges BOTH L1 escalation signals AND external
        monitoring signals. Previously only L1 signals were read, causing
        external monitoring detections (loop detection, health checks) to
        produce REDIRECT decisions with zero actionable guidance.
        """
        # Merge L1 + external signal sources (§5.4 fix)
        l1_signals = agent.data.get("_l2_escalation_signals", [])
        external_signals = agent.data.get("_l2_external_signals", [])
        signals = list(l1_signals or []) + list(external_signals or [])
        if not signals:
            return ""

        parts = []
        recent_calls = agent.data.get("recent_tool_calls", [])
        last_tool = recent_calls[-1].get("tool_name", "") if recent_calls else ""
        last_args = str(recent_calls[-1].get("tool_args", {})) if recent_calls else ""

        for signal in signals:
            detector = signal.get("detector", "")

            if detector == "tool_call_repetition":
                if last_tool == "code_execution_tool":
                    if any(kw in last_args.lower() for kw in ["grep", "rg ", "rg\n", "find ", "search"]):
                        parts.append(
                            "🔍 SEARCH LOOP: STOP searching entirely. Matches are "
                            "likely from build caches (tsconfig.tsbuildinfo) or binary "
                            "files (.agix.proj/), NOT source code. Instead: "
                            "(a) `cat src/path/to/file.ts` to read files directly, "
                            "(b) `ls src/` to find correct files, "
                            "(c) if you must search, use `rg 'pattern' src/ -t ts -t tsx` "
                            "to limit to source only."
                        )
                    elif any(kw in last_args.lower() for kw in ["curl", "http://", "localhost"]):
                        parts.append(
                            "🌐 CURL LOOP: STOP curling. The dev server is likely DEAD "
                            "after a dependency change. You MUST: "
                            "(1) kill old server process, "
                            "(2) `npm install --legacy-peer-deps`, "
                            "(3) restart via services_mgt tool, "
                            "(4) THEN retry. 500 on ALL routes = dead server, not code bug."
                        )
                    else:
                        parts.append(
                            f"🔄 TOOL LOOP: You have called {last_tool} too many times "
                            f"consecutively. Change approach: try a different tool, break "
                            f"the problem down, or deliver current progress."
                        )
                elif last_tool in ("write_to_file", "replace_in_file"):
                    parts.append(
                        "📝 FILE WRITE LOOP: You are rewriting the same file. STOP. "
                        "Read the file back with `cat` to verify changes persisted. "
                        "If correct, MOVE ON to the next task."
                    )
                else:
                    # Error-pattern enrichment for generic tool loop
                    from python.helpers.error_pattern_guidance import get_error_specific_guidance
                    last_error = agent.data.get("_last_tool_error", "")
                    tool_loop_hint = get_error_specific_guidance(last_error)
                    base_msg = (
                        f"🔄 TOOL LOOP: {last_tool} called too many times. "
                        f"Change your approach entirely."
                    )
                    if tool_loop_hint:
                        base_msg += f"\n   {tool_loop_hint}"
                    parts.append(base_msg)

            elif detector == "error_cascade":
                parts.append(
                    "❌ ERROR CASCADE: Multiple consecutive errors = fundamental "
                    "approach problem. Read error messages carefully. Address ROOT "
                    "CAUSE, not symptoms. Check package.json, tsconfig.json, or "
                    "missing modules."
                )

            elif detector == "monologue_loop":
                parts.append(
                    "💬 MONOLOGUE: You are reasoning without executing tools. "
                    "Call a tool NOW — use `response` tool if unsure."
                )

            # §5.4: External monitoring detector types
            elif detector == "repetitive_tool_loop":
                details = signal.get("details", {})
                repeated_tool = details.get("repeated_tool", last_tool or "unknown")
                repeat_count = details.get("repeat_count", "many")
                parts.append(
                    f"🔄 EXTERNAL LOOP DETECTION: Background monitor detected "
                    f"'{repeated_tool}' called {repeat_count} times in a loop. "
                    f"You MUST change strategy immediately: "
                    f"(1) Stop calling the same tool, "
                    f"(2) Diagnose WHY it's not working, "
                    f"(3) Try a completely different approach or deliver progress."
                )

            elif detector == "periodic_check_in":
                parts.append(
                    "⏰ HEALTH CHECK: Background monitor flagged a concern during "
                    "periodic check-in. Review your recent progress: Are you making "
                    "forward progress toward the user's goal? If stuck, simplify "
                    "your approach and deliver what you have."
                )

            # §10.6: Gate-to-L2 signal mappings
            # RCA-353b: Death spiral detection — CB + high consecutive mistakes
            elif detector == "gate_circuit_breaker_warning":
                consecutive = agent.data.get("_consecutive_mistake_count", 0)
                if consecutive >= 12:
                    parts.append(
                        f"🛑 DEATH SPIRAL: Circuit breaker fired AND {consecutive}+ consecutive "
                        "mistakes. This agent cannot recover. Decision MUST be STOP_AND_DELIVER. "
                        "Force immediate response with whatever work is completed. "
                        "Do NOT allow further tool calls."
                    )
                else:
                    detail = signal.get("detail", "")
                    parts.append(
                        f"🚧 GATE LIMIT: The agent is approaching gate bypass limits. "
                        f"{detail} Review the GATE STATUS section. If the agent has been "
                        f"repeatedly blocked on the same check, it may be stuck — consider "
                        f"REDIRECT with specific fix instructions or STOP_AND_DELIVER if "
                        f"the issue is unfixable within remaining turns."
                    )

            elif detector == "critical_check_warning":
                detail = signal.get("detail", "")
                parts.append(
                    f"⚠️ CRITICAL CHECK LIMIT: {detail} The next block on this "
                    f"critical check will trigger bypass. If the agent cannot fix the "
                    f"underlying issue, STOP_AND_DELIVER with explicit warnings about "
                    f"the unresolved violations."
                )

            elif detector == "advisory_quality_gap":
                detail = signal.get("detail", "")
                parts.append(
                    f"📋 QUALITY GAP: {detail} Multiple quality checks are failing. "
                    f"The code may lack error handling, test coverage, or CSS integrity. "
                    f"REDIRECT the agent to address these quality gaps before delivery."
                )

            # SS-7: Cross-delegation spiral — same failure from 3+ subordinates
            elif detector == "cross_delegation_spiral":
                detail = signal.get("detail", "")

                # Extract the actual failure reason from the health ledger
                failure_reason = ""
                health_ledger = agent.data.get("_delegation_health_ledger", [])
                if health_ledger:
                    last_failure = next(
                        (e for e in reversed(health_ledger) if e.get("status") == "FAILED"), {}
                    )
                    failure_reason = last_failure.get("failure_reason", "")

                # Get error-pattern-specific guidance
                from python.helpers.error_pattern_guidance import get_error_specific_guidance
                specific_guidance = get_error_specific_guidance(failure_reason or detail)

                guidance_text = (
                    f"🔄 DELEGATION SPIRAL: {detail}\n"
                    f"   STOP re-delegating. The root cause must be fixed at YOUR level:\n"
                )
                if specific_guidance:
                    guidance_text += f"   {specific_guidance}\n"
                guidance_text += (
                    f"   1. Read the failure reason — is it an environment issue (missing dep, wrong version)?\n"
                    f"   2. Fix the issue YOURSELF before delegating again\n"
                    f"   3. If unfixable, use the response tool to deliver partial results\n"
                    f"   4. Do NOT spawn another subordinate for the same failing task"
                )
                parts.append(guidance_text)

        # RCA-352: Delegation progress stall
        stall_signals = [s for s in signals if isinstance(s, dict) and s.get('detector') == 'delegation_stall']
        if stall_signals:
            stall = stall_signals[-1]  # Use latest
            parts.append(self._assess_delegation_progress(agent, stall))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Delegation Progress Assessment (RCA-352 F-2)
    # ------------------------------------------------------------------

    def _assess_delegation_progress(self, agent: 'Agent', stall_signal: dict) -> str:
        """Generate signal-specific guidance for delegation stall signals.

        Called by _get_signal_specific_guidance when a 'delegation_stall' signal
        is present.

        Uses the stall_count from the signal to determine escalation stage:
        - Stage 1 (stall_count=1): Advisory — suggest wrapping up
        - Stage 2 (stall_count=2): Critical — MUST call response tool
        - Stage 3 (stall_count>=3): Force stop — set loop_data.is_done = True

        For stages 1-2, returns guidance text injected into the REDIRECT message.
        For stage 3, directly stops the agent (no LLM call needed).
        """
        stall_count = stall_signal.get('stall_count', 1)
        recent_messages = stall_signal.get('recent_messages', [])
        reqs_completed = stall_signal.get('reqs_completed', '?')
        reqs_total = stall_signal.get('reqs_total', '?')

        if stall_count >= 3:
            # Stage 3: Force stop — set loop_data.is_done directly
            agent.loop_data.is_done = True
            agent.loop_data.stop_reason = (
                f"Supervisor: delegation stall stage 3 — no progress "
                f"in {stall_count * 10}+ delegations"
            )
            logger.warning(
                f"[INTELLIGENT SUPERVISOR] Delegation stall stage 3 — "
                f"force-stopping agent via _assess_delegation_progress "
                f"(stall_count={stall_count})"
            )
            return (
                f"🛑 FORCE STOP: Delegation stall stage 3 — no progress "
                f"in {stall_count * 10}+ delegations. Agent force-stopped."
            )

        elif stall_count == 2:
            # Stage 2: Critical — MUST call response tool
            return (
                f"🔴 CRITICAL: DELEGATION STALL — NO PROGRESS IN "
                f"{stall_count * 10}+ DELEGATIONS\n"
                f"Requirements completed: {reqs_completed}/{reqs_total} "
                f"(unchanged for {stall_count * 10} delegations)\n"
                f"You MUST call the `response` tool NOW with a PARTIAL "
                f"completion status.\n"
                f"Report what was accomplished and what remains incomplete.\n"
                f"Do NOT delegate any more fix attempts."
            )

        else:
            # Stage 1: Advisory — suggest wrapping up
            msg_previews = "\n".join(
                f"  - {msg[:80]}" for msg in recent_messages[:5]
            ) if recent_messages else "  (no recent messages)"
            return (
                f"⚠️ DELEGATION PROGRESS STALL DETECTED\n"
                f"No new requirements completed in the last "
                f"{stall_count * 10} delegations.\n"
                f"Recent delegations:\n{msg_previews}\n"
                f"Consider: Are you making forward progress or retrying "
                f"the same fix?\n"
                f"If verification keeps failing, accept partial completion "
                f"and report results."
            )

    # ------------------------------------------------------------------
    # LLM Call
    # ------------------------------------------------------------------

    async def _call_supervisor_llm(
        self,
        agent: "Agent",
        signals: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Call the utility LLM to get a supervisor decision.

        Returns a dict with 'action', 'reason', and optionally 'guidance'.
        Returns a static REDIRECT nudge after 3+ consecutive failures.
        """
        # --- Self-healing: check if we should skip the LLM and nudge ---
        consecutive_failures = agent.data.get("_l2_consecutive_failures", 0)
        if consecutive_failures >= 3:
            logger.warning(
                f"[INTELLIGENT SUPERVISOR] {consecutive_failures} consecutive LLM "
                f"failures — returning deterministic recovery nudge"
            )
            # Increment counter for tracking
            agent.data["_l2_consecutive_failures"] = consecutive_failures + 1
            return {
                "action": "REDIRECT",
                "reason": (
                    f"Supervisor recovery: LLM has failed {consecutive_failures} "
                    f"consecutive times. Injecting deterministic guidance."
                ),
                "guidance": (
                    "The supervisor LLM is unavailable. Take a step back and "
                    "re-evaluate your current approach. If you are stuck in a loop, "
                    "try a completely different strategy. Focus on delivering the "
                    "user's original request with the simplest working solution."
                ),
            }

        mission = self._extract_mission(agent)
        snapshot = self._build_state_snapshot(agent)

        user_prompt = (
            f"USER'S ORIGINAL MISSION:\n{mission}\n\n"
            f"{snapshot}\n\n"
            f"Based on the above, what action should be taken? "
            f"Respond with JSON only."
        )

        # Try utility model first, then fall back to chat model
        models_to_try = [agent.config.utility_model]
        if hasattr(agent.config, 'chat_model') and agent.config.chat_model:
            models_to_try.append(agent.config.chat_model)

        for model_config in models_to_try:
            try:
                from litellm import acompletion

                # --- Fix 1: Extract string from ModelConfig ---
                model_str = self._extract_model_string(model_config)
                extra_kwargs = {}
                if hasattr(model_config, 'build_kwargs'):
                    extra_kwargs = model_config.build_kwargs() or {}

                # Build context-aware system prompt
                system_prompt = SUPERVISOR_SYSTEM_PROMPT + self._get_context_prompt_addendum(agent)

                # Strip keys we set explicitly to avoid "got multiple values" TypeError
                for key in ("temperature", "max_tokens", "response_format", "model", "messages"):
                    extra_kwargs.pop(key, None)

                response = await acompletion(
                    model=model_str,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=500,
                    response_format={"type": "json_object"},
                    **extra_kwargs,
                )

                content = response.choices[0].message.content
                if content:
                    # --- Self-healing: reset failure counter on success ---
                    agent.data["_l2_consecutive_failures"] = 0
                    return json.loads(content)

            except json.JSONDecodeError as e:
                logger.error(f"[INTELLIGENT SUPERVISOR] Failed to parse LLM response: {e}")
            except Exception as e:
                model_name = getattr(model_config, 'name', str(model_config))
                logger.error(f"[INTELLIGENT SUPERVISOR] LLM call failed with {model_name}: {e}")
                continue  # Try next model in fallback chain

        # --- Self-healing: increment failure counter ---
        agent.data["_l2_consecutive_failures"] = consecutive_failures + 1
        logger.warning(
            f"[INTELLIGENT SUPERVISOR] All models failed. Consecutive failures: "
            f"{agent.data['_l2_consecutive_failures']}"
        )
        return None

    @staticmethod
    def _extract_model_string(model_config) -> str:
        """Extract a 'provider/name' string from a ModelConfig object.

        litellm.acompletion requires the model param to be a string like
        'openrouter/google/gemini-3-flash'. Passing a ModelConfig object
        directly causes AttributeError because litellm calls .split('/').

        Args:
            model_config: Either a string or a ModelConfig with .provider/.name

        Returns:
            A 'provider/name' format string suitable for litellm
        """
        if isinstance(model_config, str):
            return model_config
        provider = getattr(model_config, 'provider', '')
        name = getattr(model_config, 'name', '')
        if provider and name:
            return f"{provider}/{name}"
        if name:
            return name
        # Last resort: str() representation
        return str(model_config)

    # ------------------------------------------------------------------
    # Context-Aware Helpers
    # ------------------------------------------------------------------

    def _is_task_context(self, agent: "Agent") -> bool:
        """Check if agent is running in TASK context (scheduled/automated)."""
        try:
            if hasattr(agent, 'context') and agent.context:
                ctx_type = getattr(agent.context, 'type', None)
                return ctx_type == AgentContextType.TASK
        except Exception as e:
            logger.warning(f"[SUPERVISOR] Task context detection failed: {e}")
        return False

    def _get_context_prompt_addendum(self, agent: "Agent") -> str:
        """Get context-specific addendum for the supervisor system prompt.
        
        For TASK contexts, instructs the LLM that long-running automated work
        is normal and should default to CONTINUE.
        """
        if not self._is_task_context(agent):
            return ""
        
        return (
            "\n\nCONTEXT: SCHEDULED AUTOMATED TASK\n"
            "This agent is executing a SCHEDULED automated task (e.g., outreach processing, "
            "data sync, triage, batch operations). Expected behavior includes:\n"
            "- Long chains of sequential tool calls (10-50+ is normal)\n"
            "- Extended data processing and API interactions\n"
            "- Database operations and batch updates\n"
            "- Periods of reasoning between tool executions\n\n"
            "These are NORMAL for automated tasks. Default to CONTINUE unless you see "
            "a genuine death spiral (5+ identical failed operations with zero progress). "
            "NEVER use STOP_AND_DELIVER for scheduled tasks — use REDIRECT with "
            "guidance to help the agent recover instead."
        )

    def _filter_decision_for_context(self, agent: "Agent", decision: dict) -> dict:
        """Filter supervisor decision based on agent context.
        
        For TASK contexts, STOP_AND_DELIVER is downgraded to REDIRECT because
        the task timeout is the ultimate backstop — the supervisor should guide,
        not kill scheduled tasks.
        """
        if not self._is_task_context(agent):
            return decision
        
        action = decision.get("action", "CONTINUE").upper()
        
        if action == "STOP_AND_DELIVER":
            reason = decision.get("reason", "Agent appears stuck")
            logger.info(
                f"[INTELLIGENT SUPERVISOR] Downgrading STOP_AND_DELIVER to REDIRECT "
                f"for scheduled task agent {agent.agent_name}: {reason}"
            )
            return {
                "action": "REDIRECT",
                "reason": f"Supervisor redirect (scheduled task protection): {reason}",
                "guidance": (
                    f"This is a scheduled task — the timeout will handle hard stops. "
                    f"Try a different approach: break the operation into smaller batches, "
                    f"skip problematic items, or report partial results."
                ),
            }
        
        return decision
