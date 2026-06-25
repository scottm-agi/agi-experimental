"""
Monitoring module for supervisor.

Contains monitoring loop, signal handling, and check-in logic.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import logger

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.event_bus import AgentSignal, SignalType


class MonitoringMixin:
    """
    Mixin class providing monitoring functionality for SupervisorAgent.
    
    This mixin handles:
    - Main monitoring loop
    - Signal reception and processing
    - Periodic agent check-ins
    - Tiered task supervision
    """
    
    # =========================================================================
    # Monitoring Loop
    # =========================================================================
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop with periodic check-ins.

        RCA-249: This loop no longer calls _process_signals() or any LLM
        methods. It flushes pending signals into agent.data for L2
        (IntelligentSupervisor) to consume on the agent's next turn.
        The ONLY direct intervention path is _check_dead_agents(), which
        must nudge directly because dead agents have no running L2 loop.
        """
        check_interval = self.config.check_interval_minutes * 60  # Convert to seconds
        
        while self._running:
            try:
                # RCA-249: Flush pending signals into agent.data for L2 consumption
                if self._pending_signals:
                    self._flush_pending_signals_to_l2()
                
                # Periodic check-in on all agents (stores signals for L2)
                await self._perform_check_in()
                self._stats["check_ins_performed"] += 1

                # Gap 5: Verification Loop
                await self._verify_interventions()

                # Dead-agent heartbeat detection (DIRECT nudge — dead agents can't trigger L2)
                await self._check_dead_agents()
                
                # Wait for next check-in
                await asyncio.sleep(check_interval)
                
            except asyncio.CancelledError:
                logger.debug("[SupervisorMonitoring] Monitoring loop cancelled — shutting down gracefully")
                break
            except Exception as e:
                logger.error(f"Error in supervisor monitoring loop: {e}")
                await asyncio.sleep(10)  # Brief pause on error
    
    async def _on_signal(self, signal: "AgentSignal") -> None:
        """Handle incoming signal from event bus."""
        # MODIFIED: Tiered task supervision instead of blanket ignore (Gap 4)
        from python.helpers.task_definitions import (
            get_task_supervision_level, 
            SupervisionLevel,
            should_skip_supervision,
            should_log_only
        )
        from python.helpers.event_bus import SignalType
        
        supervision_level = SupervisionLevel.STANDARD  # Default
        
        # Check if this is a TASK context signal
        if hasattr(signal, 'context_type') and signal.context_type:
            ctx_type_str = str(signal.context_type).upper()
            if ctx_type_str == "TASK" or "TASK" in ctx_type_str:
                # Determine supervision level for this task
                task_name = getattr(signal, 'task_name', '') or signal.agent_id
                supervision_level = get_task_supervision_level(task_name)
                
                if supervision_level == SupervisionLevel.NONE:
                    logger.debug(f"[SUPERVISOR] Ignoring signal from unsupervised task: {signal.agent_id}")
                    return
                
                if supervision_level == SupervisionLevel.MINIMAL:
                    # Log but don't process
                    logger.info(f"[SUPERVISOR] Task signal logged (minimal supervision): {signal.signal_type.value} from {signal.agent_id}")
                    self._stats["signals_logged_minimal"] = self._stats.get("signals_logged_minimal", 0) + 1
                    return
                
                # ENHANCED supervision: Boost signal severity for faster response
                if supervision_level == SupervisionLevel.ENHANCED:
                    if signal.severity == "warning":
                        signal.severity = "high"
                    logger.info(f"[SUPERVISOR] Enhanced task supervision active for {signal.agent_id}")
        
        # Fallback: check if agent is registered and has TASK context (legacy behavior)
        elif getattr(self, '_ignore_task_contexts', False):
            try:
                from python.agent import Agent, AgentContextType
                looked_up_agent = Agent.get(signal.agent_id, None)
                if looked_up_agent and looked_up_agent.context:
                    if getattr(looked_up_agent.context, 'type', None) == AgentContextType.TASK:
                        # Apply tiered supervision even for legacy lookup
                        task_name = getattr(looked_up_agent.context, 'name', '') or signal.agent_id
                        supervision_level = get_task_supervision_level(task_name)
                        if supervision_level in [SupervisionLevel.NONE, SupervisionLevel.MINIMAL]:
                            logger.info(f"[SUPERVISOR] Ignoring/logging TASK context (via agent lookup): {signal.agent_id}")
                            return
            except Exception as e:
                logger.debug(f"Could not check context type for signal {signal.agent_id}: {e}")
        
        self._pending_signals.append(signal)
        self._stats["signals_received"] += 1
        
        # ENHANCED: Visible logging for all signals
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[SUPERVISOR] 📡 SIGNAL RECEIVED", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"  Type: {signal.signal_type.value}", file=sys.stderr)
        print(f"  Agent: {signal.agent_id}", file=sys.stderr)
        print(f"  Severity: {signal.severity}", file=sys.stderr)
        print(f"  Context: {signal.context_id}", file=sys.stderr)
        if signal.error_message:
            # Better labeling logic to avoid false positive "Error" tags for non-error signals
            error_types = [
                SignalType.AUTH_ERROR, 
                SignalType.RATE_LIMITED, 
                SignalType.PERMISSION_ERROR, 
                SignalType.AGENT_ERROR,
                SignalType.TOOL_FAILURE_LOOP,
                SignalType.AGENT_STUCK
            ]
            
            if signal.signal_type in error_types:
                label = "Error"
            elif signal.severity in ["critical", "high"]:
                label = "Alert"
            else:
                label = "Message"
                
            print(f"  {label}: {signal.error_message[:200]}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        
        logger.info(f"[SUPERVISOR] Signal received: {signal.signal_type.value} from {signal.agent_id} (severity: {signal.severity})")
        
        # RCA-249: Store signals for L2 (IntelligentSupervisor) instead of
        # calling _process_signals()/LLM directly. L2 reads these signals on
        # the agent's next monologue turn and makes the LLM-based decision.
        if signal.severity in ["critical", "high", "warning"]:
            agent_key = f"{signal.agent_id}@{signal.context_id}" if signal.context_id else signal.agent_id
            pending_for_agent = [
                s for s in self._pending_signals if
                (f"{s.agent_id}@{s.context_id}" if s.context_id else s.agent_id) == agent_key
            ]
            if self._should_intervene(agent_key, pending_for_agent):
                print(f"[SUPERVISOR] ⚡ Signal stored for L2 (severity: {signal.severity})", file=sys.stderr)
                # Flush pending signals to agent.data for L2 consumption
                self._flush_pending_signals_to_l2()
            else:
                print(f"[SUPERVISOR] ⏭️ Signal queued but intervention suppressed (cooldown/quality)", file=sys.stderr)
                logger.info(f"[SUPERVISOR] Signal suppressed for {agent_key} by intelligent evaluation")
    
    # =========================================================================
    # Intelligent Supervisor Evaluation (RCA-249)
    # =========================================================================
    # Root cause of MSR_Smoke_1777658113 nudge inflation (399 nudges):
    # The supervisor reacted to EVERY warning+ signal without assessing
    # agent progress, signal quality, or intervention effectiveness.
    #
    # This gate ensures nudges are rare, high-quality, and impactful.
    # =========================================================================

    # Tools whose output is source code — never a genuine tool failure
    _WRITE_TOOLS = frozenset({"write_to_file", "replace_in_file", "save_to_file"})

    def _should_intervene(self, agent_id: str, signals: list) -> bool:
        """Intelligent evaluation: should the supervisor actually intervene?

        Checks (in order):
        1. Critical override — critical signals always pass
        2. Signal quality — discard write-tool false positives
        3. Cooldown — was a nudge sent recently with no effect?
        4. Deduplication — collapse identical signal types within 30s

        NOTE: No nudge budget. The root cause of 399 nudges in MSR_Smoke was
        8,500 false-positive tool failure signals from write tools scanning
        source code. That's fixed at the source (_12_tool_failure_tracker.py
        excludes write tools). A budget cap would mask whether the root cause
        fix actually works. If nudge counts spike again, fix the SIGNAL SOURCE,
        don't cap the supervisor.

        Returns:
            True if intervention is warranted, False to suppress.
        """
        # Initialize tracking structures if needed
        if not hasattr(self, '_intervention_history'):
            self._intervention_history = {}

        cooldown_seconds = getattr(
            self.config, 'intervention_cooldown_seconds', 60.0
        )

        # ── 1. Critical override: always intervene ──
        has_critical = any(
            getattr(s, 'severity', '') == 'critical' for s in signals
        )
        if has_critical:
            self._record_nudge(agent_id)
            return True

        # ── 2. Signal quality: filter out write-tool false positives ──
        # Defense-in-depth: the tracker already excludes write tools,
        # but if stale signals leak through, catch them here too.
        quality_signals = []
        for s in signals:
            tool_name = ""
            if hasattr(s, 'details') and isinstance(s.details, dict):
                tool_name = s.details.get('tool_name', '')
            signal_type = getattr(s.signal_type, 'value', str(s.signal_type)) if hasattr(s, 'signal_type') else ''
            # Skip tool_failure signals from write tools
            if signal_type == 'tool_failure' and tool_name in self._WRITE_TOOLS:
                logger.debug(
                    f"[SUPERVISOR] Filtering write-tool false positive: "
                    f"{tool_name} for {agent_id}"
                )
                continue
            quality_signals.append(s)

        if not quality_signals:
            return False  # All signals were false positives

        # ── 3. Cooldown: skip if nudge was sent < cooldown_seconds ago ──
        history = self._intervention_history.get(agent_id, [])
        if history:
            last_nudge = history[-1]
            elapsed = (datetime.now(timezone.utc) - last_nudge).total_seconds()
            if elapsed < cooldown_seconds:
                logger.info(
                    f"[SUPERVISOR] Cooldown active for {agent_id}: "
                    f"{elapsed:.0f}s < {cooldown_seconds}s"
                )
                return False

        # ── 4. Deduplication: collapse identical signal types in 30s window ──
        now = datetime.now(timezone.utc)
        seen_types = set()
        deduped = []
        for s in quality_signals:
            sig_type = getattr(s.signal_type, 'value', str(s.signal_type)) if hasattr(s, 'signal_type') else 'unknown'
            ts = getattr(s, 'timestamp', now)
            age = (now - ts).total_seconds() if ts else 0
            if age <= 30 and sig_type in seen_types:
                continue  # Duplicate within 30s
            seen_types.add(sig_type)
            deduped.append(s)

        if not deduped:
            return False

        # All checks passed — record and allow
        self._record_nudge(agent_id)
        return True

    def _record_nudge(self, agent_id: str) -> None:
        """Record a nudge for budget and cooldown tracking."""
        if "_nudge_counts" not in self._stats:
            self._stats["_nudge_counts"] = {}
        self._stats["_nudge_counts"][agent_id] = (
            self._stats["_nudge_counts"].get(agent_id, 0) + 1
        )
        if not hasattr(self, '_intervention_history'):
            self._intervention_history = {}
        if agent_id not in self._intervention_history:
            self._intervention_history[agent_id] = []
        self._intervention_history[agent_id].append(datetime.now(timezone.utc))

    # =========================================================================
    # Signal Storage for L2 (RCA-249)
    # =========================================================================
    # MonitoringMixin is now a PURE SIGNAL STORAGE layer. It detects issues
    # and stores them in agent.data["_l2_external_signals"] for the L2
    # IntelligentSupervisor to consume on the agent's next monologue turn.
    # The ONLY exception is dead-agent nudging — dead agents have no running
    # monologue loop, so L2 can never fire for them.
    # =========================================================================

    def _flush_pending_signals_to_l2(self) -> None:
        """Flush pending signals into agent.data for L2 consumption.

        Groups signals by agent composite ID and stores them in each
        agent's data["_l2_external_signals"] list. L2 (IntelligentSupervisor)
        reads and clears this key on each monologue turn.

        Uses a SEPARATE key from L1 structural guards (_l2_escalation_signals)
        to prevent race conditions between the background monitoring thread
        and the in-loop extension.
        """
        if not self._pending_signals:
            return

        # Group signals by composite ID (agent_id@context_id)
        signals_by_agent: Dict[str, List["AgentSignal"]] = {}
        for signal in self._pending_signals:
            key = f"{signal.agent_id}@{signal.context_id}" if signal.context_id else signal.agent_id
            if key not in signals_by_agent:
                signals_by_agent[key] = []
            signals_by_agent[key].append(signal)

        self._pending_signals.clear()

        # Store normalized signals in each agent's data for L2
        for target_id, signals in signals_by_agent.items():
            self._store_signals_for_l2(target_id, signals)

    def _store_signals_for_l2(self, target_id: str, signals: List["AgentSignal"]) -> None:
        """Store normalized signals in agent.data for L2 consumption.

        Signals are stored as dicts with consistent schema so L2 can
        merge them with L1 escalation signals.

        Args:
            target_id: Composite agent ID (agent_id@context_id).
            signals: List of AgentSignal objects from the event bus.
        """
        agent = self.get_agent(target_id)
        if not agent or not hasattr(agent, 'data'):
            logger.debug(f"[SUPERVISOR] Cannot store signals for {target_id}: agent not found")
            return

        # Initialize the external signals list if absent
        if "_l2_external_signals" not in agent.data:
            agent.data["_l2_external_signals"] = []

        for sig in signals:
            normalized = {
                "source": "monitoring_signal",
                "detector": getattr(sig.signal_type, 'value', str(sig.signal_type)) if hasattr(sig, 'signal_type') else 'unknown',
                "severity": getattr(sig, 'severity', 'warning'),
                "message": getattr(sig, 'error_message', '') or '',
                "details": getattr(sig, 'details', {}) or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            agent.data["_l2_external_signals"].append(normalized)

        logger.info(
            f"[SUPERVISOR] Stored {len(signals)} signal(s) for L2 consumption "
            f"(agent: {target_id}, key: _l2_external_signals)"
        )

    def _store_loop_signal_for_l2(self, agent, loop_info: Dict[str, Any]) -> None:
        """Store a repetitive-loop detection signal for L2 consumption.

        Args:
            agent: The agent instance stuck in a loop.
            loop_info: Dict with repeated_tool, repeat_count, etc.
        """
        if not hasattr(agent, 'data'):
            return

        if "_l2_external_signals" not in agent.data:
            agent.data["_l2_external_signals"] = []

        repeat_tool = loop_info.get("repeated_tool", "?")
        repeat_count = loop_info.get("repeat_count", 0)

        agent.data["_l2_external_signals"].append({
            "source": "monitoring_loop_detector",
            "detector": "repetitive_tool_loop",
            "severity": "high",
            "message": (
                f"REPETITIVE LOOP DETECTED: Tool '{repeat_tool}' called "
                f"{repeat_count} times consecutively with no progress."
            ),
            "details": loop_info,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.warning(
            f"[SUPERVISOR] Stored loop signal for L2: '{repeat_tool}' x{repeat_count} "
            f"(agent: {getattr(agent, 'agent_name', '?')})"
        )
    
    async def _perform_check_in(self) -> None:
        """Perform periodic check-in on all agents.

        RCA-249: This method no longer calls _handle_agent_signals() or
        _tool_nudge_agent() directly. It stores diagnostic signals in
        agent.data["_l2_external_signals"] for L2 to evaluate.
        """
        # Get all registered IDs (base and composite)
        all_ids = self.get_registered_agents()
        if not all_ids:
            return

        # We process all registered instances.
        for target_id in all_ids:
            # Check if this agent instance needs assessment
            agent = self.get_agent(target_id)
            if not agent:
                continue

            # Skip TASK context agents if configured to avoid noise in short-lived tasks
            if getattr(self, '_ignore_task_contexts', False):
                try:
                    if hasattr(agent, 'context') and agent.context:
                        from python.agent import AgentContextType
                        if getattr(agent.context, 'type', None) == AgentContextType.TASK:
                            logger.debug(f"Skipping TASK context agent in check-in: {target_id}")
                            continue
                except Exception as e:
                    logger.debug(f"Could not check context type for {target_id}: {e}")

            try:
                # Get agent summary
                summary = self._get_agent_summary(agent)

                # Check if agent needs help based on summary
                needs_help = self._quick_assess_agent(summary)

                if needs_help:
                    # RCA-249: Store health signal for L2 instead of calling LLM directly
                    self._store_health_signal_for_l2(agent, summary)

                # Detect repetitive tool-call loops on active agents.
                # RCA-249: Store loop signal for L2 instead of nudging directly
                loop_info = self._detect_repetitive_loop(agent)
                if loop_info:
                    self._store_loop_signal_for_l2(agent, loop_info)
                    self._stats["repetitive_loop_signals"] = (
                        self._stats.get("repetitive_loop_signals", 0) + 1
                    )

            except Exception as e:
                logger.error(f"Error checking agent {target_id}: {e}")

    def _store_health_signal_for_l2(self, agent, summary: str) -> None:
        """Store a health-check signal for L2 consumption.

        Called when _quick_assess_agent detects potential issues
        (high context usage, high iteration count, gate rejection loops).

        Args:
            agent: The agent that may need help.
            summary: The agent summary string from _get_agent_summary.
        """
        if not hasattr(agent, 'data'):
            return

        if "_l2_external_signals" not in agent.data:
            agent.data["_l2_external_signals"] = []

        agent.data["_l2_external_signals"].append({
            "source": "monitoring_health_check",
            "detector": "periodic_check_in",
            "severity": "warning",
            "message": f"Agent health concern detected during periodic check-in",
            "details": {"summary": summary[:500]},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"[SUPERVISOR] Stored health signal for L2: "
            f"{getattr(agent, 'agent_name', '?')}"
        )
    
    async def _check_dead_agents(self) -> None:
        """Detect dead agents — nudge with progressively stronger guidance.

        Progressive response (nudge-first, parent escalation, then human):
          Detection 1 → nudge dead agent: general guidance ("assess and resume")
          Detection 2 → nudge dead agent: stronger guidance ("try different approach")
          Detection 3 → nudge PARENT: re-delegate the dead agent's task (Iteration 23)
          Detection 4+ → escalate_human (absolute last resort)

        Iteration 23 (Tier 2): After 2 failed nudges to the dead agent, we
        escalate to the PARENT orchestrator instead of continuing to inject
        messages into a dead asyncio task. The parent has a live monologue
        loop and can re-delegate the failed task to a fresh subordinate.

        The 75-iteration LoopLimiter is the real hard safety net.
        """
        from .tools import detect_dead_agents

        dead_agents = detect_dead_agents(self._agent_refs)

        # Initialize tracking dict: agent_id → nudge count
        if "_dead_nudge_counts" not in self._stats:
            self._stats["_dead_nudge_counts"] = {}

        # Clear nudge counts for agents that recovered (no longer in dead list)
        current_dead_ids = {info["agent_id"] for info in dead_agents} if dead_agents else set()
        recovered_ids = [
            aid for aid in list(self._stats["_dead_nudge_counts"].keys())
            if aid not in current_dead_ids
        ]
        for aid in recovered_ids:
            logger.info(
                f"[SUPERVISOR] ✅ Agent '{aid}' recovered — resetting dead nudge count "
                f"(was {self._stats['_dead_nudge_counts'][aid]})"
            )
            del self._stats["_dead_nudge_counts"][aid]

        if not dead_agents:
            return

        for info in dead_agents:
            agent_id = info["agent_id"]
            agent = self._agent_refs.get(agent_id)
            if not agent:
                continue

            # Increment nudge count for this agent
            nudge_count = self._stats["_dead_nudge_counts"].get(agent_id, 0) + 1
            self._stats["_dead_nudge_counts"][agent_id] = nudge_count
            self._stats["dead_agents_detected"] = self._stats.get("dead_agents_detected", 0) + 1

            age_s = info["last_llm_age_s"]
            iteration = info["iteration"]
            ctx_id = info["context_id"]

            if nudge_count <= 2:
                # ── STEP 1-2: NUDGE DEAD AGENT (soft, message-based) ──
                nudge_messages = {
                    1: (
                        f"STALL DETECTED: '{agent_id}' has had no LLM activity "
                        f"for {age_s:.0f}s (iteration {iteration}, context {ctx_id}). "
                        f"Assess your current situation: Are you waiting for something? "
                        f"Is a tool execution hanging? Resume work or return partial "
                        f"results to the orchestrator."
                    ),
                    2: (
                        f"STILL STALLED: '{agent_id}' remains inactive after previous nudge. "
                        f"No LLM activity for {age_s:.0f}s. Try a DIFFERENT approach: "
                        f"if a tool is failing, skip it. If a dependency is broken, "
                        f"work around it. If git needs authentication, use `secret_get` to retrieve "
                        f"credentials, then run git commands directly. "
                        f"Make ANY forward progress."
                    ),
                }

                nudge_msg = nudge_messages.get(nudge_count, nudge_messages[2])

                logger.warning(
                    f"[SUPERVISOR] 💀 Dead agent '{agent_id}' — nudge #{nudge_count}: "
                    f"last LLM {age_s:.0f}s ago, iteration={iteration}"
                )
                try:
                    await self._tool_nudge_agent(agent, {"reason": nudge_msg})
                except Exception as e:
                    logger.error(f"[SUPERVISOR] Nudge #{nudge_count} failed for '{agent_id}': {e}")

            elif nudge_count == 3:
                # ── STEP 3: IO-BREAKER: break_pause + reset_state (deterministic) ──
                # If nudges didn't work, the agent may be stuck in a paused state
                # or blocked by error/gate counters. Clear those deterministically.
                from python.helpers.supervisor.io_breaker import IOBreaker

                logger.warning(
                    f"[SUPERVISOR] 🔧 IO-Breaker step 3: break_pause + reset for '{agent_id}' "
                    f"(nudges 1-2 failed, last LLM {age_s:.0f}s ago)"
                )

                # Diagnostic read FIRST (deterministic, no LLM)
                state = IOBreaker.read_agent_state(agent)
                logger.info(
                    f"[SUPERVISOR] Diagnostic for '{agent_id}': "
                    f"paused={state['is_paused']}, errors={state['error_count']}, "
                    f"gate_rejections={state['gate_rejections']}, "
                    f"turns={state['absolute_turns']}"
                )

                # Break pause if set
                if state["is_paused"]:
                    IOBreaker.break_pause(agent)

                # Reset state to give agent clean slate
                IOBreaker.reset_state(agent)

                # One more nudge with clean state
                try:
                    nudge_msg = (
                        f"SUPERVISOR RECOVERY: '{agent_id}' — your error counters and gate "
                        f"state have been reset by the supervisor. You have a clean slate. "
                        f"Resume your current task immediately. If you were blocked by tool "
                        f"failures or gate rejections, those are now cleared."
                    )
                    await self._tool_nudge_agent(agent, {"reason": nudge_msg})
                except Exception as e:
                    logger.error(f"[SUPERVISOR] IO-Breaker nudge failed for '{agent_id}': {e}")

            elif nudge_count == 4:
                # ── STEP 4: IO-BREAKER: cancel_task (kill stuck asyncio task) ──
                from python.helpers.supervisor.io_breaker import IOBreaker

                logger.warning(
                    f"[SUPERVISOR] 🔧 IO-Breaker step 4: cancel_task for '{agent_id}' "
                    f"(break_pause+reset didn't help)"
                )

                # Try to cancel via TaskRegistry
                composite_id = f"{agent_id}@{ctx_id}"
                cancelled = IOBreaker.cancel_task(composite_id)

                if not cancelled:
                    # Also try just the agent_id without context
                    cancelled = IOBreaker.cancel_task(agent_id)

                if cancelled:
                    logger.warning(
                        f"[SUPERVISOR] Cancelled asyncio task for '{agent_id}' — "
                        f"parent should see this as a failed subordinate"
                    )
                else:
                    logger.info(
                        f"[SUPERVISOR] No task found in registry for '{agent_id}' — "
                        f"escalating to parent redelegate"
                    )

            elif nudge_count == 5:
                # ── STEP 5: PARENT ESCALATION: Nudge parent to redelegate (Iteration 23) ──
                logger.warning(
                    f"[SUPERVISOR] 💀 Agent '{agent_id}' unresponsive after 4 attempts "
                    f"(2 nudges + IO-breaker) — escalating to PARENT for re-delegation."
                )
                parent = self._find_parent_agent(agent)
                if parent:
                    await self._nudge_parent_to_redelegate(parent, agent, agent_id, info)
                else:
                    # No parent found — fall back to force_return
                    logger.warning(
                        f"[SUPERVISOR] No parent found for '{agent_id}' — "
                        f"using force_return instead."
                    )
                    from python.helpers.supervisor.io_breaker import IOBreaker
                    await IOBreaker.force_return(
                        agent,
                        f"No parent agent available for re-delegation. "
                        f"Agent stalled for {age_s:.0f}s after 4 recovery attempts."
                    )

            elif nudge_count == 6:
                # ── STEP 6: FORCE RETURN (last programmatic option) ──
                from python.helpers.supervisor.io_breaker import IOBreaker

                logger.warning(
                    f"[SUPERVISOR] 🛑 Force-returning '{agent_id}' — "
                    f"all recovery attempts exhausted before human escalation."
                )
                await IOBreaker.force_return(
                    agent,
                    f"Agent '{agent_id}' has been unresponsive for {age_s:.0f}s "
                    f"across 5 recovery attempts. Delivering partial results."
                )

            else:
                # ── STEP 7+: ESCALATE TO HUMAN (absolute last resort) ──
                logger.warning(
                    f"[SUPERVISOR] 💀 Agent '{agent_id}' unrecoverable after "
                    f"{nudge_count - 1} attempts (nudges + IO-breaker + parent + force) "
                    f"— escalating to human."
                )
                await self._tool_escalate_human(agent, {
                    "reason": (
                        f"Dead agent '{agent_id}' did not recover after "
                        f"{nudge_count - 1} attempts (2 nudges, IO-breaker break_pause, "
                        f"cancel_task, parent re-delegation, force_return). "
                        f"No LLM activity for {age_s:.0f}s. "
                        f"Iteration: {iteration}. Context: {ctx_id}. "
                        f"The agent's asyncio task likely died silently."
                    ),
                })

    def _find_parent_agent(self, dead_agent) -> "Optional[Agent]":
        """Find the parent orchestrator that delegated to a dead agent.
        
        Iteration 23 (Tier 2): Uses the context hierarchy stored by
        call_subordinate_batch when creating subordinates.
        
        Args:
            dead_agent: The dead agent whose parent we need to find.
        
        Returns:
            The parent Agent, or None if not found.
        """
        from python.agent import Agent
        
        # Strategy 1: Check data["_parent_agent_number"] (set by call_subordinate_batch)
        parent_number = None
        if hasattr(dead_agent, 'data') and isinstance(dead_agent.data, dict):
            parent_number = dead_agent.data.get("_parent_agent_number")
        
        if parent_number is not None:
            # Search registered agents for the parent by number
            for ref_id, ref_agent in self._agent_refs.items():
                if hasattr(ref_agent, 'number') and ref_agent.number == parent_number:
                    logger.info(
                        f"[SUPERVISOR] Found parent '{ref_id}' (number={parent_number}) "
                        f"for dead agent (number={getattr(dead_agent, 'number', '?')})"
                    )
                    return ref_agent
        
        # Strategy 2: Find agent 0 (the root orchestrator) as fallback
        for ref_id, ref_agent in self._agent_refs.items():
            if hasattr(ref_agent, 'number') and ref_agent.number == 0:
                logger.info(
                    f"[SUPERVISOR] Using root orchestrator '{ref_id}' as parent "
                    f"fallback for dead agent (number={getattr(dead_agent, 'number', '?')})"
                )
                return ref_agent
        
        logger.warning(
            f"[SUPERVISOR] Could not find parent for dead agent "
            f"(number={getattr(dead_agent, 'number', '?')})"
        )
        return None

    async def _nudge_parent_to_redelegate(
        self,
        parent_agent,
        dead_agent,
        dead_agent_id: str,
        dead_info: dict,
    ) -> None:
        """Nudge the parent orchestrator to re-delegate a dead agent's task.
        
        Iteration 23 (Tier 2): Instead of nudging the dead agent (whose asyncio
        task is already dead), we inject a re-delegation instruction into the
        PARENT orchestrator's intervention slot.
        
        Args:
            parent_agent: The parent orchestrator (live monologue loop).
            dead_agent: The dead subordinate agent.
            dead_agent_id: ID of the dead agent.
            dead_info: Detection info dict with age_s, iteration, etc.
        """
        # Gather task info from dead agent's data
        task_id = "unknown"
        task_message = "unknown"
        if hasattr(dead_agent, 'data') and isinstance(dead_agent.data, dict):
            task_id = dead_agent.data.get("_batch_task_id", "unknown")
            task_message = dead_agent.data.get("_batch_task_message", "unknown")
        
        age_s = dead_info.get("last_llm_age_s", 0)
        
        # Fix 5A (Iter 126): Clear parent's redelegation tracker and gate state.
        # Without this, the parent's exhausted delegation budget from the PREVIOUS
        # failed subordinate prevents it from re-delegating when the supervisor
        # correctly identifies the stall. This was the root cause of unrecoverable
        # death spirals: supervisor detected the problem but the parent couldn't act.
        if hasattr(parent_agent, 'data') and isinstance(parent_agent.data, dict):
            from python.helpers.redelegation_guard import clear_redelegation_tracker
            parent_data = parent_agent.data
            clear_redelegation_tracker(parent_data)
            # Also reset gate block count so parent doesn't hit error-state bypass
            parent_data.pop("_orchestrator_completion_blocks", None)
            parent_data.pop("_error_state_bypassed", None)
            parent_data.pop("_consecutive_duplicate_responses", None)
            parent_data.pop("_last_blocked_response", None)
            logger.info(
                f"[SUPERVISOR] 🔄 Cleared parent '{getattr(parent_agent, 'agent_name', '?')}' "
                f"redelegation tracker + gate blocks for fresh recovery"
            )
        
        redelegate_msg = (
            f"⚠️ SUPERVISOR RE-DELEGATION REQUEST: "
            f"Agent '{dead_agent_id}' has died (no activity for {age_s:.0f}s, "
            f"2 nudge attempts failed). Its task was: \"{task_message}\". "
            f"You MUST re-delegate this task to a fresh agent. The dead agent's "
            f"asyncio task has exited and cannot be recovered. Use call_subordinate "
            f"or call_subordinate_batch to assign the work to a new subordinate."
        )
        
        logger.warning(
            f"[SUPERVISOR] 📩 Nudging PARENT '{getattr(parent_agent, 'agent_name', '?')}' "
            f"to redelegate dead agent '{dead_agent_id}' task='{task_id}'"
        )
        
        try:
            await self._tool_nudge_agent(parent_agent, {"reason": redelegate_msg})
            self._stats["parent_redelegation_nudges"] = self._stats.get("parent_redelegation_nudges", 0) + 1
        except Exception as e:
            logger.error(
                f"[SUPERVISOR] Parent re-delegation nudge failed for "
                f"'{dead_agent_id}' → parent '{getattr(parent_agent, 'agent_name', '?')}': {e}"
            )

    
    def _get_agent_summary(self, agent: Optional["Agent"]) -> str:
        """Get a summary of agent's current state."""
        if agent is None:
            return "**Agent ID:** Unknown/Remote\n**Status:** Distant/Disconnected\n"
            
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        # Get context window info
        ctx_window = getattr(agent, 'get_data', lambda x: None)("ctx_window") or {}
        tokens = ctx_window.get("tokens", 0)
        
        # Get max tokens from config
        max_tokens = 128000  # Default
        if hasattr(agent, 'config') and hasattr(agent.config, 'chat_model'):
            max_tokens = getattr(agent.config.chat_model, 'ctx_length', 128000) or 128000
        
        usage_percent = (tokens / max_tokens * 100) if max_tokens else 0
        
        # Get iteration count
        iteration = 0
        if hasattr(agent, 'loop_data') and agent.loop_data:
            iteration = getattr(agent.loop_data, 'iteration', 0)
        
        # Get history info
        history_count = 0
        if hasattr(agent, 'history'):
            history = agent.history.output() if hasattr(agent.history, 'output') else []
            history_count = len(history)
        
        # Get context info
        context_id = "N/A"
        if hasattr(agent, 'context') and agent.context:
            context_id = getattr(agent.context, 'id', 'N/A')
        
        # Get batch task metadata (RCA-264 Part 2)
        batch_info = ""
        if hasattr(agent, 'data') and isinstance(agent.data, dict):
            batch_task_id = agent.data.get("_batch_task_id", "")
            batch_timeout = agent.data.get("_batch_task_timeout", 0)
            batch_start = agent.data.get("_batch_task_start_time", 0)
            if batch_task_id:
                batch_info += f"**Batch Task:** {batch_task_id}\n"
            if batch_timeout:
                batch_info += f"**Task Timeout:** {int(batch_timeout)}s (~{int(batch_timeout/60)} min)\n"
                if batch_start:
                    import time
                    elapsed = time.time() - batch_start
                    remaining = max(0, batch_timeout - elapsed)
                    pct_used = (elapsed / batch_timeout * 100) if batch_timeout else 0
                    batch_info += (
                        f"**Time Used:** {int(elapsed)}s / {int(batch_timeout)}s "
                        f"({pct_used:.0f}%) — {int(remaining)}s remaining\n"
                    )
        
        return f"""
**Agent ID:** {agent_id}
**Context:** {context_id}
**Iteration:** {iteration}
**Context Usage:** {usage_percent:.1f}% ({tokens:,}/{max_tokens:,} tokens)
**Message History:** {history_count} messages
{batch_info}"""
    
    def _quick_assess_agent(self, summary: str) -> bool:
        """Quick heuristic check if agent needs help.
        
        Also classifies the stall pattern for deep-dive RCA if detected.
        """
        import re
        
        needs_help = False
        stall_reasons = []
        
        # Check for high context usage
        if "90%" in summary or "95%" in summary or "100%" in summary:
            needs_help = True
            stall_reasons.append("context_exhaustion")
        
        # Check for high iteration count (might be stuck)
        # NOTE: 50+ is the threshold because batch delegation with subordinates
        # legitimately uses 20-40 iterations. Only flag truly excessive counts.
        iteration_match = re.search(r'Iteration:\s*(\d+)', summary)
        if iteration_match:
            iteration = int(iteration_match.group(1))
            if iteration > 50:  # High iteration count
                needs_help = True
                stall_reasons.append("high_iteration_count")
        
        # RC-2 FIX: Detect gate rejection loops
        # When an agent has 3+ consecutive gate rejections, it's likely stuck in
        # the response loop (RC-2). This triggers deep-dive RCA to generate a
        # structured recovery plan rather than generic nudges.
        agent_id_match = re.search(r'Agent ID:\s*(\S+)', summary)
        if agent_id_match:
            agent_id = agent_id_match.group(1)
            try:
                agent = self.get_agent(agent_id)
                if agent and hasattr(agent, 'data'):
                    gate_rejections = agent.data.get("_consecutive_gate_rejections", 0)
                    if gate_rejections >= 3:
                        needs_help = True
                        stall_reasons.append(f"gate_rejection_loop({gate_rejections}x)")
                        logger.warning(
                            f"[SUPERVISOR] RC-2 gate rejection loop detected for {agent_id}: "
                            f"{gate_rejections} consecutive rejections. Deep-dive RCA recommended."
                        )
            except Exception as e:
                logger.debug(f"Could not check gate rejections for {agent_id}: {e}")
        
        if stall_reasons:
            logger.info(
                f"[SUPERVISOR] Stall pattern detected: {', '.join(stall_reasons)}"
            )
        
        return needs_help

    # =========================================================================
    # Repetitive Loop Detection (Chat History Semantic Analysis)
    # =========================================================================
    # Root cause: Agents retry the same failing tool call (e.g., npx create-next-app)
    # 7+ times while the supervisor sees them as "active" because the process
    # hasn't died. This method reads chat history to detect that pattern.

    REPETITIVE_LOOP_THRESHOLD = 3  # Flag after 3 consecutive same-tool calls

    def _detect_repetitive_loop(self, agent) -> Optional[Dict[str, Any]]:
        """Detect if an agent is stuck in a repetitive tool-call loop.

        Scans the agent's chat history for consecutive assistant messages
        that invoke the same tool_name. If the same tool appears
        REPETITIVE_LOOP_THRESHOLD or more times in the most recent assistant
        messages, returns a diagnostic dict.

        Args:
            agent: Agent instance with .history.output() → list of messages.

        Returns:
            None if no loop detected.
            Dict with {repeated_tool, repeat_count, sample_args} if looping.
        """
        try:
            messages = agent.history.output()
        except Exception:
            return None

        if not messages:
            return None

        # Extract tool_name from assistant messages (most recent first)
        tool_calls: list = []
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if not content:
                continue

            # Try to extract tool_name from JSON-formatted tool calls
            tool_name = self._extract_tool_name(content)
            if tool_name:
                tool_calls.append(tool_name)
            else:
                # Non-tool assistant message breaks the streak
                # (agent is doing reasoning, not just retrying)
                break

        if len(tool_calls) < self.REPETITIVE_LOOP_THRESHOLD:
            return None

        # Check for consecutive repeats from the most recent
        first_tool = tool_calls[0]
        consecutive = 0
        for t in tool_calls:
            if t == first_tool:
                consecutive += 1
            else:
                break

        if consecutive >= self.REPETITIVE_LOOP_THRESHOLD:
            logger.warning(
                f"[SUPERVISOR] Repetitive loop detected for "
                f"'{getattr(agent, 'agent_name', '?')}': "
                f"tool '{first_tool}' called {consecutive}x consecutively"
            )
            return {
                "repeated_tool": first_tool,
                "repeat_count": consecutive,
                "agent_name": getattr(agent, "agent_name", "unknown"),
            }

        return None

    # =========================================================================
    # Alternating Loop Detection (F-7: A-B-A-B pattern detection)
    # =========================================================================
    # Root cause: _detect_repetitive_loop only catches consecutive same-tool
    # (A-A-A-A). But infra build loops are A-B-A-B (write → check server →
    # write → check server). This method catches those alternating patterns.

    ALTERNATING_LOOP_MIN_REPEATS = 3  # At least 3 full cycle repetitions

    def _detect_alternating_loop(self, agent) -> Optional[Dict[str, Any]]:
        """Detect if an agent is stuck in an alternating tool-call loop.

        Scans the agent's chat history for repeating cycles of 2 or 3 tools
        (e.g., A-B-A-B-A-B or A-B-C-A-B-C-A-B-C). These patterns indicate
        infra build loops where the agent edits code then checks the dev
        server in a cycle.

        A cycle of length 1 (same tool repeated) is NOT detected here — that
        is handled by _detect_repetitive_loop.

        Args:
            agent: Agent instance with .history.output() → list of messages.

        Returns:
            None if no alternating loop detected.
            Dict with {pattern, cycle, repeat_count, agent_name} if looping.
        """
        try:
            messages = agent.history.output()
        except Exception:
            return None

        if not messages:
            return None

        # Extract tool_name from assistant messages (most recent first)
        tool_calls: list = []
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if not content:
                continue

            tool_name = self._extract_tool_name(content)
            if tool_name:
                tool_calls.append(tool_name)
            else:
                # Non-tool message breaks the streak
                break

        # Need at least min_repeats * cycle_len calls
        # For cycle_len=2, min=3 → need 6 calls minimum
        if len(tool_calls) < 2 * self.ALTERNATING_LOOP_MIN_REPEATS:
            return None

        # Check for cycles of length 2 and 3
        for cycle_len in (2, 3):
            if len(tool_calls) < cycle_len * self.ALTERNATING_LOOP_MIN_REPEATS:
                continue

            # Extract candidate cycle from the most recent calls
            candidate = tool_calls[:cycle_len]

            # Skip if all tools in the cycle are the same
            # (that's a same-tool loop, handled by _detect_repetitive_loop)
            if len(set(candidate)) == 1:
                continue

            # Count how many times this cycle repeats
            repeats = 0
            for i in range(0, len(tool_calls) - cycle_len + 1, cycle_len):
                window = tool_calls[i:i + cycle_len]
                if window == candidate:
                    repeats += 1
                else:
                    break

            if repeats >= self.ALTERNATING_LOOP_MIN_REPEATS:
                cycle_str = " → ".join(candidate)
                logger.warning(
                    f"[SUPERVISOR] Alternating loop detected for "
                    f"'{getattr(agent, 'agent_name', '?')}': "
                    f"cycle [{cycle_str}] repeated {repeats}x"
                )
                return {
                    "pattern": "alternating",
                    "cycle": candidate,
                    "repeat_count": repeats,
                    "agent_name": getattr(agent, "agent_name", "unknown"),
                }

        return None

    @staticmethod
    def _extract_tool_name(content: str) -> Optional[str]:
        """Extract tool_name from an assistant message content string.

        Handles JSON-formatted tool calls like:
            {"tool_name": "code_execution_tool", "tool_args": {...}}

        Also handles the content being embedded in markdown code blocks.

        Returns:
            The tool_name string, or None if not a tool-call message.
        """
        import re

        if not content or "tool_name" not in content:
            return None

        # Try JSON parse first (fastest path)
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed.get("tool_name")
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: regex extraction for embedded JSON
        match = re.search(r'"tool_name"\s*:\s*"([^"]+)"', content)
        if match:
            return match.group(1)

        return None
# Backward-compat alias: tests import SupervisorMonitoringMixin
SupervisorMonitoringMixin = MonitoringMixin
