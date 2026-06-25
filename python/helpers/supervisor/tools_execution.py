"""
Supervisor Tools — Tool Definitions, Intervention Tracking, Execution.
=====================================================================
Extracted from tools.py during modularization.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.event_bus import AgentSignal

from .base import logger


class ExecutionMixin:
    """Mixin providing tool definitions, tracking, and execution dispatch."""

    def _setup_tools(self) -> None:
        """Set up tools for LLM tool calling."""
        self.tools = [
            {
                "name": "provide_guidance",
                "description": "Send a helpful guidance message to an agent. Use this for gentle nudges and encouragement.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The guidance message to send"
                        }
                    },
                    "required": ["message"]
                }
            },
            {
                "name": "redirect_approach",
                "description": "Suggest a different approach to the agent. Use when current approach is clearly not working.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "approach": {
                            "type": "string",
                            "description": "The new approach to suggest"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this approach is better"
                        }
                    },
                    "required": ["approach", "reason"]
                }
            },
            {
                "name": "simplify_task",
                "description": "Break down a complex task into simpler steps. Use when agent is overwhelmed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "breakdown": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of simpler steps"
                        }
                    },
                    "required": ["breakdown"]
                }
            },
            {
                "name": "inject_hint",
                "description": "Provide a specific technical hint. Use for targeted help with specific issues.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "hint": {
                            "type": "string",
                            "description": "The technical hint"
                        }
                    },
                    "required": ["hint"]
                }
            },
            {
                "name": "escalate_human",
                "description": "Escalate to human intervention. Use only as last resort when all else fails.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why human intervention is needed"
                        }
                    },
                    "required": ["reason"]
                }
            },
            {
                "name": "record_lesson",
                "description": "Record a lesson learned from this intervention for future reference.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Brief title for the lesson"
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed description"
                        },
                        "pattern": {
                            "type": "string",
                            "description": "The pattern that was detected"
                        },
                        "solution": {
                            "type": "string",
                            "description": "What worked to resolve it"
                        }
                    },
                    "required": ["title", "description"]
                }
            },
            # RCA-471: nudge_agent REMOVED from L1 tools.
            # L1 should only perform deterministic interventions.
            # nudge_agent is retained by L2 (Intelligent Supervisor) for
            # genuinely stuck agents. The dead agent nudge path in
            # monitoring.py._check_dead_agents() calls _tool_nudge_agent()
            # directly — it does NOT go through L1 tool selection.
            # Docker restart nudge (crash_recovery.py:587) is unaffected.
            {
                "name": "no_intervention",
                "description": "Explicitly choose not to intervene. Use when agent is making progress.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why no intervention is needed"
                        }
                    },
                    "required": []
                }
            },
        ]
    
    # =========================================================================
    # Intervention Tracking
    # =========================================================================
    
    def _is_first_prompt(self, agent: Optional["Agent"]) -> bool:
        """Check if this is the first user prompt in the conversation (Issue #403)."""
        if not agent or not hasattr(agent, 'history') or not agent.history:
            return True  # Assume first if we can't check
        
        try:
            history = agent.history.output() if hasattr(agent.history, 'output') else []
            user_msg_count = 0
            
            for m in history:
                role = m.get("role")
                msg_content = m.get("content", {})
                
                # Count real user messages (not interventions)
                is_real_user = False
                if role == "user":
                    is_real_user = True
                elif role is None and not m.get("ai"):
                    is_real_user = True
                
                # Filter out intervention messages
                if is_real_user:
                    c = msg_content if isinstance(msg_content, dict) else {}
                    if "user_intervention" in c or "[SUPERVISOR GUIDANCE]" in str(msg_content):
                        is_real_user = False
                
                if is_real_user:
                    user_msg_count += 1
            
            return user_msg_count <= 1
        except Exception as e:
            logger.debug(f"Error checking first prompt: {e}")
            return True  # Assume first on error (safer to not intervene)

    def _can_intervene(self, agent_id: str, signal_fingerprint: str = None) -> bool:
        """
        Check if we can intervene with this agent.
        
        Gap 6: Uses fingerprinted cooldown to allow novel issues while debouncing repeated ones.
        """
        history = self._intervention_history.get(agent_id, [])
        
        # Check max interventions (hard limit)
        if len(history) >= self.config.max_interventions_per_agent:
            return False
        
        # Gap 6: Check fingerprinted cooldown if we have a fingerprint
        if signal_fingerprint:
            fingerprint_key = f"{agent_id}:{signal_fingerprint}"
            if fingerprint_key in self._fingerprinted_cooldowns:
                last = self._fingerprinted_cooldowns[fingerprint_key]
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                if elapsed < self.config.intervention_cooldown_seconds:
                    logger.debug(f"Fingerprinted cooldown active for {fingerprint_key}")
                    return False
                # Cooldown expired - remove entry
                del self._fingerprinted_cooldowns[fingerprint_key]
        else:
            # Legacy: Check per-agent cooldown
            if history:
                last = history[-1]
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                if elapsed < self.config.intervention_cooldown_seconds:
                    return False
        
        # Issue #403: Never intervene on first prompt
        agent = self._agent_refs.get(agent_id)
        if agent and self._is_first_prompt(agent):
            logger.info(f"Skipping intervention for first prompt of agent {agent_id}")
            return False
        
        return True
    
    async def _generate_signal_fingerprint(self, signals: List["AgentSignal"]) -> str:
        """
        Generate a fingerprint for a set of signals to enable issue-specific cooldowns.
        Gap 6: Issue-fingerprinted cooldown.
        """
        if not signals:
            return "no_signals"
        
        import hashlib
        # Build fingerprint from signal types and tool names (stable fields)
        parts = []
        for s in signals[-3:]:  # Last 3 signals
            type_str = s.signal_type.value
            tool_str = s.tool_name or "no_tool"
            parts.append(f"{type_str}:{tool_str}")
        
        fingerprint = "|".join(parts)
        # Hash to keep it short
        from python.helpers.hashing import content_hash_short
        return content_hash_short(fingerprint, length=12)
    
    def _record_intervention(self, agent_id: str, signal_fingerprint: str = None) -> None:
        """Record an intervention for cooldown tracking (Gap 6) and escalation ramp (#366)."""
        now = datetime.now(timezone.utc)
        
        # Record in legacy history
        if agent_id not in self._intervention_history:
            self._intervention_history[agent_id] = []
        self._intervention_history[agent_id].append(now)
        
        # Record fingerprinted cooldown
        if signal_fingerprint:
            fingerprint_key = f"{agent_id}:{signal_fingerprint}"
            self._fingerprinted_cooldowns[fingerprint_key] = now
        
        # Escalation ramp (#366): increment on every intervention
        self.increment_escalation(agent_id)
        
        # Record in goal state if available
        try:
            from python.helpers.goal_state_manager import GoalStateManager
            agent = self._agent_refs.get(agent_id)
            if agent and hasattr(agent, 'context') and agent.context:
                gsm = GoalStateManager.get_instance()
                gsm.record_intervention(agent.context.id, "Supervisor intervention")
        except Exception as e:
            logger.debug(f"Could not record intervention in goal state: {e}")
    
    # =========================================================================
    # Response Hash Helper (Fix 8.1)
    # =========================================================================

    @staticmethod
    def _get_agent_response_hash(agent: "Agent") -> str:
        """
        Get a normalized hash of the agent's most recent response tool output.
        
        Used by Fix 8.1 to capture response state at intervention time and compare
        later to detect false progress (iteration increases with identical responses).
        
        Returns empty string if no response history is available.
        """
        import hashlib
        import re
        
        try:
            if hasattr(agent, 'history') and agent.history:
                messages = agent.history.output()
                # Walk backwards to find the last assistant message
                for msg in reversed(messages):
                    if msg.get("role") == "assistant":
                        content = str(msg.get("content", ""))
                        if content:
                            normalized = re.sub(r'\s+', ' ', content.strip().lower())
                            from python.helpers.hashing import content_hash_short
                            return content_hash_short(normalized, length=16)
        except Exception:
            pass
        return ""

    # =========================================================================
    # Tool Execution
    # =========================================================================
    
    async def _execute_tool_calls(
        self,
        agent: "Agent",
        tool_calls: List[Dict[str, Any]],
    ) -> None:
        """Execute tool calls from LLM response."""
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        for call in tool_calls:
            tool_name = call.get("name", "unknown")
            tool_args = call.get("arguments", {})
            
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"[SUPERVISOR] 🔧 EXECUTING INTERVENTION TOOL", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            print(f"  Tool: {tool_name}", file=sys.stderr)
            print(f"  Agent: {agent_id}", file=sys.stderr)
            print(f"  Arguments: {json.dumps(tool_args)[:200]}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            
            try:
                result = await self._execute_tool(agent, tool_name, tool_args)
                
                print(f"\n{'='*60}", file=sys.stderr)
                print(f"[SUPERVISOR] ✅ INTERVENTION EXECUTED", file=sys.stderr)
                print(f"{'='*60}", file=sys.stderr)
                print(f"  Tool: {tool_name}", file=sys.stderr)
                print(f"  Result: {result}", file=sys.stderr)
                print(f"{'='*60}\n", file=sys.stderr)
                
                logger.info(f"[SUPERVISOR] Tool {tool_name} executed: {result}")
                self._stats["interventions_executed"] += 1
                
            except Exception as e:
                print(f"\n[SUPERVISOR] ❌ Tool {tool_name} failed: {e}", file=sys.stderr)
                logger.error(f"Tool {tool_name} failed: {e}")
    
    async def _verify_interventions(self) -> None:
        """
        Gap 5: Verification Loop.
        Check if previous interventions actually resolved the issue.
        """
        if not self._pending_verifications:
            return

        now = datetime.now(timezone.utc)
        to_remove = []

        for context_id, verification in self._pending_verifications.items():
            # Only verify if scheduled time has passed
            if now < verification.get("scheduled_at", now):
                continue

            agent = self._agent_refs.get(context_id)
            if not agent:
                # Try to find agent by context_id in composite IDs
                for aid, ref in self._agent_refs.items():
                    if "@" in aid and aid.split("@")[1] == context_id:
                        agent = ref
                        break
            
            if not agent:
                to_remove.append(context_id)
                continue

            # Check if issue is still present (heuristic)
            issue_resolved = True
            
            # Extract agent state for verification (Issue #1080)
            iteration = 0
            truncation_retries = 0
            last_successful_llm_ts = 0.0
            is_retrying = False
            
            if hasattr(agent, 'loop_data') and agent.loop_data:
                iteration = getattr(agent.loop_data, 'iteration', 0)
                last_successful_llm_ts = getattr(agent.loop_data, 'last_successful_llm_ts', 0.0)
            
            if hasattr(agent, 'data') and isinstance(agent.data, dict):
                is_retrying = agent.data.get("is_retrying", False)
                # Use persistent counter (survives monologue re-entries)
                truncation_retries = agent.data.get("_truncation_retries", 0)
            
            # Stale threshold: if last successful LLM call was > 2 min ago, agent may be stuck
            import time as _time
            stale_threshold_s = 120  # 2 minutes
            llm_is_stale = (last_successful_llm_ts > 0 and 
                           (_time.time() - last_successful_llm_ts) > stale_threshold_s)
            
            # 0. Check if agent has completed its task (highest priority - avoid false escalations)
            # BUT: only trust "completed" if agent is NOT in an error state (Issue #1080)
            agent_completed = False
            if hasattr(agent, 'context') and agent.context:
                agent_id = getattr(agent, 'agent_name', str(id(agent)))
                
                # ── DEBUGGING: Log the agent registry state for this check ──
                registered_ids = list(self._agent_refs.keys())
                logger.info(
                    f"[SUPERVISOR DEBUG] agent_completed check: "
                    f"agent_id='{agent_id}', context_id='{context_id}', "
                    f"registered_agents={registered_ids}, "
                    f"agent_in_refs={agent_id in self._agent_refs}"
                )
                
                # Check if agent has been unregistered (task truly finished)
                if agent_id not in self._agent_refs:
                    # ── FIX Iteration 14: Only consider ACTIVE agents ──
                    # Old code checked ALL agent_refs for matching context_id,
                    # including paused/completed subordinates. This caused
                    # false "still tracked by another agent" verdicts.
                    active_agents = _filter_active_context_agents(
                        self._agent_refs, context_id
                    )
                    if not active_agents:
                        agent_completed = True
                        logger.info(
                            f"[SUPERVISOR DEBUG] ✅ Agent '{agent_id}' truly completed — "
                            f"not in refs AND no ACTIVE agents tracking context_id '{context_id}'"
                        )
                    else:
                        active_names = [getattr(a, 'agent_name', '?') for a in active_agents]
                        logger.warning(
                            f"[SUPERVISOR DEBUG] ⚠️ Agent '{agent_id}' not in refs but "
                            f"context_id '{context_id}' still tracked by ACTIVE agents: "
                            f"{active_names} — NOT declaring completion"
                        )
                # Check if agent is paused — but ONLY count as completed if NOT in error state
                elif hasattr(agent.context, 'paused') and agent.context.paused:
                    # Paused + retrying/truncating = STUCK, not done (Issue #1080)
                    if is_retrying or truncation_retries > 0:
                        agent_completed = False
                        logger.warning(
                            f"[SUPERVISOR] Agent paused but in error state "
                            f"(is_retrying={is_retrying}, truncation_retries={truncation_retries}) "
                            f"for {context_id} — NOT declaring success"
                        )
                    elif llm_is_stale:
                        agent_completed = False
                        logger.warning(
                            f"[SUPERVISOR] Agent paused with stale LLM timestamp "
                            f"(last success {_time.time() - last_successful_llm_ts:.0f}s ago) "
                            f"for {context_id} — NOT declaring success"
                        )
                    else:
                        agent_completed = True
                        logger.info(
                            f"[SUPERVISOR DEBUG] ✅ Agent '{agent_id}' paused (not error state) — "
                            f"declaring completed for {context_id}"
                        )
            
            # If agent truly completed, consider intervention successful
            if agent_completed:
                issue_resolved = True
                logger.info(
                    f"[SUPERVISOR] Agent completed task, marking intervention as resolved "
                    f"for {context_id} (agent_id='{agent_id}')"
                )
            else:
                # 1. Check for new UNRESOLVED signals from this agent
                # Only count signals that haven't been processed and are recent
                signal_cutoff = verification.get("scheduled_at", now) - timedelta(minutes=self.config.check_interval_minutes)
                recent_signals = [
                    s for s in self._pending_signals 
                    if s.context_id == context_id and s.timestamp > signal_cutoff
                ]
                if recent_signals:
                    issue_resolved = False
                
                # 2. Check iteration progress — but NOT if truncation retries are active (Issue #1080)
                # Only mark as unresolved if iteration DECREASED or stalled AND we had signals
                if iteration <= verification.get("last_iteration", 0) and recent_signals:
                    issue_resolved = False
                elif iteration > verification.get("last_iteration", 0):
                    # Iteration increased — but is it REAL progress?
                    if truncation_retries > 0:
                        # Iterations increased due to truncation retry loop, NOT real progress
                        issue_resolved = False
                        logger.warning(
                            f"[SUPERVISOR] Iteration increased ({verification.get('last_iteration', 0)} → {iteration}) "
                            f"but truncation_retries={truncation_retries} — NOT counting as progress for {context_id}"
                        )
                    elif llm_is_stale:
                        # Iteration increased but no recent successful LLM call
                        issue_resolved = False
                        logger.warning(
                            f"[SUPERVISOR] Iteration increased but LLM timestamp is stale "
                            f"({_time.time() - last_successful_llm_ts:.0f}s ago) for {context_id}"
                        )
                    else:
                        # Fix 8.1: Real progress requires NOVEL response, not just iteration increase
                        # Check if the agent's latest response differs from response at intervention time
                        current_hash = self._get_agent_response_hash(
                            self._agent_refs.get(context_id) or agent
                        )
                        intervention_hash = verification.get("response_hash", "")
                        if intervention_hash and current_hash == intervention_hash:
                            # Iteration increased but response is IDENTICAL — false progress
                            issue_resolved = False
                            logger.warning(
                                f"[SUPERVISOR] Iteration increased but response hash unchanged "
                                f"(hash={current_hash[:8]}) for {context_id} — NOT counting as progress"
                            )
                        else:
                            # Real progress: iteration increased + no truncation + fresh LLM + novel response
                            issue_resolved = True

            # Increment attempts before checking resolution
            attempts = verification.get("attempts", 0) + 1
            verification["attempts"] = attempts

            if issue_resolved:
                logger.info(f"[SUPERVISOR] Intervention verified as SUCCESSFUL for {context_id}")
                self._stats["interventions_successful"] += 1
                to_remove.append(context_id)
            else:
                # Issue persists
                if attempts >= 6:
                    # ABSOLUTE LAST RESORT: escalate only after 6 failed verifications
                    logger.warning(f"[SUPERVISOR] Intervention FAILED after 6 attempts for {context_id}. Escalating to human as last resort.")
                    await self._tool_escalate_human(agent, {"reason": f"Intervention '{verification.get('intervention_type')}' failed to resolve issue after 6 verification cycles. All nudge attempts exhausted."})
                    to_remove.append(context_id)
                elif attempts >= 3:
                    # Progressive nudge: after 3 failed verifications, nudge with strong guidance
                    logger.warning(f"[SUPERVISOR] Intervention verification failed {attempts}x for {context_id}. Nudging with stronger guidance.")
                    try:
                        await self._tool_nudge_agent(agent, {
                            "reason": (
                                f"VERIFICATION FAILURE #{attempts}: Previous intervention "
                                f"'{verification.get('intervention_type')}' has not resolved "
                                f"the issue after {attempts} verification cycles. "
                                f"The agent needs a MORE SPECIFIC and DIRECTIVE nudge. "
                                f"Read the agent's latest chat history and provide an "
                                f"exact, actionable fix — not general advice."
                            ),
                        })
                    except Exception as e:
                        logger.error(f"[SUPERVISOR] Nudge failed during verification for {context_id}: {e}")
                else:
                    # Reschedule next verification
                    verification["scheduled_at"] = now + timedelta(minutes=self.config.check_interval_minutes)
                    verification["last_iteration"] = iteration
                    logger.info(f"[SUPERVISOR] Intervention verification PENDING for {context_id} (attempt {attempts}/3)")

        for cid in to_remove:
            self._pending_verifications.pop(cid, None)

    async def _execute_tool(
        self,
        agent: "Agent",
        tool_name: str,
        args: Dict[str, Any],
    ) -> Any:
        """Execute a single tool."""
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        
        # Gap 5: Schedule verification for intervention tools
        intervention_tools = ["provide_guidance", "redirect_approach", "simplify_task", "inject_hint"]  # RCA-471: nudge_agent removed from L1
        if tool_name in intervention_tools and hasattr(agent, 'context') and agent.context:
            context_id = agent.context.id
            iteration = 0
            if hasattr(agent, 'loop_data') and agent.loop_data:
                iteration = getattr(agent.loop_data, 'iteration', 0)
                
            self._pending_verifications[context_id] = {
                "intervention_type": tool_name,
                "scheduled_at": datetime.now(timezone.utc) + timedelta(minutes=self.config.check_interval_minutes),
                "attempts": 0,
                "last_iteration": iteration,
                # Fix 8.1: Capture response hash at intervention time for novelty check
                "response_hash": self._get_agent_response_hash(agent),
            }

        # Tool implementations
        if tool_name == "provide_guidance":
            return await self._tool_provide_guidance(agent, args)
        elif tool_name == "redirect_approach":
            return await self._tool_redirect_approach(agent, args)
        elif tool_name == "simplify_task":
            return await self._tool_simplify_task(agent, args)
        elif tool_name == "inject_hint":
            return await self._tool_inject_hint(agent, args)
        elif tool_name == "escalate_human":
            return await self._tool_escalate_human(agent, args)
        elif tool_name == "record_lesson":
            return await self._tool_record_lesson(args)
        elif tool_name == "nudge_agent":
            return await self._tool_nudge_agent(agent, args)
        elif tool_name == "no_intervention":
            return "No intervention needed"
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
