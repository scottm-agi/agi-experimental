"""
Supervisor Tools — Individual Tool Action Methods.
==================================================
Extracted from tools.py during modularization.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

from .base import logger
from python.helpers.lessons_learned import LessonsLearnedEngine, LessonCategory, LessonSeverity


class ActionsMixin:
    """Mixin providing individual supervisor tool action implementations."""

    # =========================================================================
    # Tool Implementations
    # =========================================================================
    
    async def _publish_intervention(self, agent: "Agent", agent_id: str, message: str, intervention_type: str = "guidance", skip_if_local: bool = False) -> None:
        """Helper to publish intervention signals to the event bus.
        
        Args:
            skip_if_local: If True and agent.intervention is already set (direct injection),
                          skip the event bus publish to avoid duplicate messages.
                          The _40_remote_intervention.py extension picks up event bus
                          signals and re-injects them, causing double-injection.
        """
        # Skip event bus if we already set agent.intervention directly (same process)
        if skip_if_local and hasattr(agent, 'intervention') and agent.intervention is not None:
            logger.debug(f"[SUPERVISOR] Skipping event bus publish for {agent_id} — local intervention already set")
            return
        
        try:
            from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
            
            # Get context_id if possible
            context_id = "unknown"
            if hasattr(agent, 'context') and agent.context:
                context_id = getattr(agent.context, 'id', 'unknown')
            
            signal = AgentSignal(
                signal_type=SignalType.INTERVENTION_GUIDANCE,
                agent_id=agent_id,
                context_id=context_id,
                timestamp=datetime.now(timezone.utc),
                severity="high",
                details={
                    "message": message,
                    "intervention_type": intervention_type
                }
            )
            asyncio.create_task(get_event_bus().publish(signal))
        except Exception as e:
            logger.error(f"Failed to publish intervention signal ({intervention_type}): {e}")

    async def _tool_provide_guidance(
        self,
        agent: "Agent",
        args: Dict[str, Any],
    ) -> str:
        """Provide guidance to an agent."""
        message = args.get("message", "")
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        # LLM-based semantic dedup: Use the utility model to judge whether this
        # guidance adds material value vs. repeating what's already been said.
        # Only triggers if there are existing supervisor hints in recent history.
        if hasattr(agent, 'history') and agent.history:
            try:
                recent = agent.history.output()[-8:]
                existing_hints = []
                for m in recent:
                    content_str = str(m.get("content", ""))
                    if "[SUPERVISOR GUIDANCE]" in content_str:
                        hint_text = content_str.split("[SUPERVISOR GUIDANCE]")[-1].strip()[:500]
                        if hint_text:
                            existing_hints.append(hint_text)
                
                if existing_hints and hasattr(agent, 'call_utility_model'):
                    dedup_prompt = (
                        "You are a supervisor deduplication filter. Your job is to determine if NEW guidance "
                        "adds materially different value from EXISTING guidance already given to the agent.\n\n"
                        "EXISTING GUIDANCE (already in agent's context):\n"
                        + "\n---\n".join(existing_hints[-3:])  # Last 3 hints max
                        + "\n\nNEW GUIDANCE (proposed):\n" + message[:500]
                        + "\n\nDoes the NEW guidance provide materially different direction, new information, "
                        "or address a different aspect of the task? Answer ONLY 'NEW' if it adds value, "
                        "or 'DUPLICATE' if it's essentially repeating the same instruction."
                    )
                    verdict = await agent.call_utility_model(
                        system="You are a concise dedup filter. Answer ONLY 'NEW' or 'DUPLICATE'.",
                        message=dedup_prompt,
                    )
                    verdict = verdict.strip().upper()
                    if "DUPLICATE" in verdict:
                        logger.info(f"[SUPERVISOR_DEDUP] LLM judged guidance as duplicate for {agent_id}")
                        return f"Guidance already present for {agent_id} (LLM dedup)"
            except Exception as e:
                logger.debug(f"Supervisor LLM dedup check failed (non-fatal): {e}")
        
        # Create intervention message
        if hasattr(agent, 'intervention') and agent:
            from python.agent import UserMessage
            agent.intervention = UserMessage(
                message=f"[SUPERVISOR GUIDANCE]\n\n{message}",
                system_message=["[SUPERVISOR GUIDANCE]"],
            )
        
        # Also publish via event bus for distributed agents (skip if local to avoid duplication)
        await self._publish_intervention(agent, agent_id, message, "guidance", skip_if_local=True)
        
        # Record intervention
        self._intervention_history.setdefault(agent_id, []).append(datetime.now(timezone.utc))
        
        # Log to agent's context if available
        if hasattr(agent, 'context') and agent.context and hasattr(agent.context, 'log'):
            agent.context.log.log(
                type="info",
                heading="🎯 Supervisor Guidance",
                content=message,
            )
        
        return f"Guidance sent to {agent_id}"
    
    async def _tool_redirect_approach(
        self,
        agent: "Agent",
        args: Dict[str, Any],
    ) -> str:
        """Redirect agent to a different approach."""
        new_approach = args.get("approach", "")
        reason = args.get("reason", "")
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        message = f"""I notice you might be stuck. Let me suggest a different approach:

**New Approach:** {new_approach}

**Why:** {reason}

Please try this approach and let me know if you need more help."""
        
        if hasattr(agent, 'intervention') and agent:
            from python.agent import UserMessage
            agent.intervention = UserMessage(
                message=f"[SUPERVISOR REDIRECT]\n\n{message}",
                system_message=["[SUPERVISOR REDIRECT]"],
            )
        
        # Also publish via event bus for distributed agents (skip if local to avoid duplication)
        await self._publish_intervention(agent, agent_id, message, "redirect", skip_if_local=True)
        
        self._intervention_history.setdefault(agent_id, []).append(datetime.now(timezone.utc))
        
        return f"Redirect sent to {agent_id}"
    
    async def _tool_simplify_task(
        self,
        agent: "Agent",
        args: Dict[str, Any],
    ) -> str:
        """Help agent break down a complex task."""
        breakdown = args.get("breakdown", [])
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        if isinstance(breakdown, list):
            steps = "\n".join(f"{i+1}. {step}" for i, step in enumerate(breakdown))
        else:
            steps = str(breakdown)
        
        message = f"""This task seems complex. Let me help break it down:

{steps}

Focus on step 1 first, then proceed to the next steps."""
        
        if hasattr(agent, 'intervention') and agent:
            from python.agent import UserMessage
            agent.intervention = UserMessage(
                message=f"[SUPERVISOR SIMPLIFY]\n\n{message}",
                system_message=["[SUPERVISOR SIMPLIFY]"],
            )
        
        # Also publish via event bus for distributed agents (skip if local to avoid duplication)
        await self._publish_intervention(agent, agent_id, message, "simplify", skip_if_local=True)
        
        self._intervention_history.setdefault(agent_id, []).append(datetime.now(timezone.utc))
        
        return f"Task breakdown sent to {agent_id}"
    
    async def _tool_inject_hint(
        self,
        agent: "Agent",
        args: Dict[str, Any],
    ) -> str:
        """Inject a specific technical hint."""
        hint = args.get("hint", "")
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        message = f"""💡 **Hint:** {hint}"""
        
        if hasattr(agent, 'intervention') and agent:
            from python.agent import UserMessage
            agent.intervention = UserMessage(
                message=f"[SUPERVISOR HINT]\n\n{message}",
                system_message=["[SUPERVISOR HINT]"],
            )
        
        # Also publish via event bus for distributed agents (skip if local to avoid duplication)
        await self._publish_intervention(agent, agent_id, message, "hint", skip_if_local=True)
        
        self._intervention_history.setdefault(agent_id, []).append(datetime.now(timezone.utc))
        
        return f"Hint sent to {agent_id}"
    
    async def _tool_escalate_human(
        self,
        agent: "Agent",
        args: Dict[str, Any],
    ) -> str:
        """Escalate to human intervention with per-agent dedup guard."""
        reason = args.get("reason", "")
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        # =====================================================================
        # ESCALATION DEDUP GUARD (Issue: Supervisor Escalation Feedback Loop)
        # Prevents the supervisor from spamming escalate_human for the same
        # agent. Each escalation publishes an INTERVENTION_GUIDANCE signal
        # which triggers the supervisor again, creating a feedback loop.
        # Max 2 escalations per agent per 10-minute window.
        # =====================================================================
        now = datetime.now(timezone.utc)
        if agent_id not in ToolsMixin._escalation_history:
            ToolsMixin._escalation_history[agent_id] = []
        
        # Prune old entries outside the window
        cutoff = now - timedelta(seconds=self.ESCALATION_WINDOW_SECONDS)
        ToolsMixin._escalation_history[agent_id] = [
            t for t in ToolsMixin._escalation_history[agent_id] if t > cutoff
        ]
        
        recent_count = len(ToolsMixin._escalation_history[agent_id])
        if recent_count >= self.MAX_ESCALATIONS_PER_AGENT:
            logger.warning(
                f"[SUPERVISOR] Escalation dedup: agent {agent_id} already escalated "
                f"{recent_count} times in last {self.ESCALATION_WINDOW_SECONDS}s. "
                f"Converting to no_intervention to break feedback loop."
            )
            return f"No intervention needed (escalation rate-limited for {agent_id})"
        
        # Record this escalation
        ToolsMixin._escalation_history[agent_id].append(now)
        
        # Pause the agent if possible — BUT ONLY for top-level agents.
        # Subordinates share the parent's context (call_subordinate_batch.py:806),
        # so pausing a subordinate's context kills the parent orchestrator.
        is_subordinate = getattr(agent, 'agent_number', 0) > 0
        if not is_subordinate and hasattr(agent, 'context') and agent.context and agent:
            agent.context.paused = True
        
        # NOTE: We intentionally do NOT publish an INTERVENTION_GUIDANCE signal
        # here. Publishing a signal from escalate_human creates a feedback loop
        # where the supervisor picks up the signal and escalates again.
        
        # Log escalation
        if hasattr(agent, 'context') and agent.context and hasattr(agent.context, 'log') and agent:
            agent.context.log.log(
                type="error",
                heading="🚨 Supervisor Escalation",
                content=f"Agent {agent_id} requires human intervention.\n\nReason: {reason}",
            )
        
        return f"Escalated {agent_id} to human"
    
    async def _tool_nudge_agent(
        self,
        agent: "Agent",
        args: Dict[str, Any],
    ) -> str:
        """Read the agent's chat history, compose targeted advice, and inject it.
        
        Unlike other supervisor tools which relay the supervisor's own message,
        nudge_agent actively reads the agent's conversation to compose the most
        contextually relevant corrective advice.
        """
        reason = args.get("reason", "Agent appears stuck")
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        # Iteration 23: Check if agent's monologue loop is likely alive
        # If _last_llm_call_time is very stale, the intervention may not be read
        monologue_task_alive = True
        if hasattr(agent, 'data') and isinstance(agent.data, dict):
            import time as _time
            last_llm = agent.data.get("_last_llm_call_time", 0)
            # ADR rca_terminal_blocking_stall: Check if agent is blocked in a
            # long-running tool (e.g., get_terminal_output). These agents ARE alive
            # — they just can't make LLM calls because they're in a blocking output
            # loop. The intervention mechanism IS the way to break them out.
            # Setting intervention triggers InterventionException in the loop.
            is_blocked_in_tool = agent.data.get("_blocked_in_tool", False)
            
            if last_llm > 0:
                staleness = _time.time() - last_llm
                if staleness > 600 and not is_blocked_in_tool:  # 10+ minutes with no activity AND not in tool
                    monologue_task_alive = False
                    logger.warning(
                        f"[SUPERVISOR] ⚠️ Agent '{agent_id}' monologue task appears dead "
                        f"(no LLM activity for {staleness:.0f}s). Nudge may not be received."
                    )
                    # Fix 5B (Iter 126): Return DEAD indicator immediately instead
                    # of wasting an LLM call composing advice that will never be
                    # read. The caller (dead agent recovery in monitoring.py) uses
                    # this to fast-track parent escalation instead of waiting for
                    # 3 nudge attempts on a corpse.
                    return (
                        f"DEAD: Agent '{agent_id}' is unreachable — monologue task "
                        f"has been inactive for {staleness:.0f}s. Nudge NOT delivered. "
                        f"Escalate to parent or respawn."
                    )
                elif staleness > 600 and is_blocked_in_tool:
                    logger.info(
                        f"[SUPERVISOR] Agent '{agent_id}' has stale LLM ({staleness:.0f}s) "
                        f"but is BLOCKED IN TOOL — injecting nudge to trigger InterventionException"
                    )
        
        print(f"\n[SUPERVISOR] 🔔 NUDGE_AGENT called for {agent_id}: {reason}", file=sys.stderr)
        
        # 1. Read recent chat history from the agent (Fix 8.3: 10 → 30 messages)
        chat_preview = ""
        tool_failure_summary = ""
        if hasattr(agent, 'history') and agent.history:
            try:
                full_history = agent.history.output()
                recent = full_history[-30:]  # Last 30 messages (was 10)
                for m in recent:
                    role = m.get("role", "unknown")
                    content = str(m.get("content", ""))[:300]
                    chat_preview += f"\n[{role}]: {content}"
                
                # Fix 8.3: Collect deduped tool failure summary from FULL history
                seen_errors = set()
                error_lines = []
                for m in full_history:
                    content = str(m.get("content", ""))
                    # Look for error patterns in tool results
                    for pattern in ["Error:", "ENOENT", "EACCES", "permission denied",
                                    "failed", "404", "500", "connection refused",
                                    "ModuleNotFoundError", "ImportError", "TypeError"]:
                        if pattern.lower() in content.lower():
                            # Extract a short error signature for dedup
                            error_sig = content[:150].strip()
                            if error_sig not in seen_errors:
                                seen_errors.add(error_sig)
                                error_lines.append(f"- {error_sig[:120]}")
                            break
                
                if error_lines:
                    tool_failure_summary = (
                        f"\n\n## Historical Tool Failures (deduped from full {len(full_history)}-message history)\n"
                        + "\n".join(error_lines[-15:])  # Cap at 15 unique errors
                    )
            except Exception as e:
                chat_preview = f"(Unable to read chat history: {e})"
        
        if not chat_preview:
            chat_preview = "(No chat history available)"
        
        # 2. Compose diagnostic prompt for the utility model
        diagnostic_prompt = f"""You are a senior engineering mentor reviewing a stuck agent's conversation.

## Supervisor's Observation
Reason for nudge: {reason}

## Agent's Recent Chat History (last 30 messages)
{chat_preview}
{tool_failure_summary}

## Your Task
Based on the chat history above, provide ONE specific, actionable corrective instruction to unstick this agent. 
- Be concrete: reference specific tools, files, or commands
- Be brief: 2-3 sentences max
- Focus on the NEXT action the agent should take
- If the agent is looping, tell it exactly what to do differently
- If the agent has sent the same DONE message multiple times, tell it the task is NOT done and specify what is still missing
"""
        
        # 3. Generate advice using the agent's utility model
        advice = ""
        if hasattr(agent, 'call_utility_model'):
            try:
                advice = await agent.call_utility_model(
                    system="You are a senior engineering mentor. Provide ONE brief, specific corrective instruction.",
                    message=diagnostic_prompt
                )
                if not isinstance(advice, str):
                    advice = str(advice)
            except Exception as e:
                logger.warning(f"[SUPERVISOR] Nudge LLM call failed: {e}")
                advice = f"You appear stuck ({reason}). Try a completely different approach for your current step."
        else:
            advice = f"You appear stuck ({reason}). Try a completely different approach for your current step."
        
        # 4. Inject the nudge as an intervention
        nudge_message = f"[SUPERVISOR NUDGE]\n\n{advice}"
        
        if hasattr(agent, 'intervention'):
            from python.agent import UserMessage
            agent.intervention = UserMessage(
                message=nudge_message,
                system_message=["[SUPERVISOR NUDGE]"],
            )
        
        # Publish via event bus for distributed agents
        await self._publish_intervention(agent, agent_id, nudge_message, "nudge", skip_if_local=True)
        
        # Record intervention
        self._intervention_history.setdefault(agent_id, []).append(datetime.now(timezone.utc))
        
        print(f"[SUPERVISOR] ✅ Nudge injected for {agent_id}: {advice[:100]}...", file=sys.stderr)
        
        return f"Nudge sent to {agent_id}: {advice[:200]}"
    
    async def _tool_record_lesson(self, args: Dict[str, Any]) -> str:
        """Record a lesson learned using the LessonsLearnedEngine."""
        title = args.get("title", "Untitled Lesson")
        description = args.get("description", "")
        pattern = args.get("pattern", "")
        solution = args.get("solution", "")

        try:
            engine = LessonsLearnedEngine(memory_bank_path=self.config.lessons_file_path.rsplit('/', 1)[0] if '/' in self.config.lessons_file_path else "memory-bank/lessons-learned")
            await engine.load_lessons_from_storage()
            lesson = await engine.create_lesson(
                category=LessonCategory.GENERAL,
                severity=LessonSeverity.INFO,
                title=title,
                description=description,
                trigger=pattern,
                observation=description,
                root_cause="Detected by supervisor",
                solution=solution,
                prevention=solution,
                agent_id=args.get("agent_id", ""),
            )
            self._stats["lessons_recorded"] += 1

            # Auto-promote eligible lessons to global rules
            try:
                promoted = await engine.promote_to_rules(
                    min_occurrences=3,
                    min_success_rate=0.8,
                )
                if promoted > 0:
                    logger.info(f"Auto-promoted {promoted} lesson(s) to global rules")
            except Exception as e:
                logger.debug(f"Rule promotion check skipped: {e}")

            return f"Lesson recorded: {title} (id={lesson.id})"
        except Exception as e:
            # Fallback to file append if engine fails
            from python.helpers import files
            lesson_entry = f"\n## {title}\n**Date:** {datetime.now(timezone.utc).isoformat()}\n**Pattern:** {pattern}\n**Description:** {description}\n**Solution:** {solution}\n\n---\n"
            lessons_path = files.get_abs_path(self.config.lessons_file_path)
            os.makedirs(os.path.dirname(lessons_path), exist_ok=True)
            if not os.path.exists(lessons_path):
                with open(lessons_path, 'w') as f:
                    f.write("# Supervisor Lessons Learned\n\nThis file contains lessons learned by the LLM Supervisor.\n\n---\n")
            with open(lessons_path, 'a') as f:
                f.write(lesson_entry)
            self._stats["lessons_recorded"] += 1
            return f"Lesson recorded (fallback): {title}"