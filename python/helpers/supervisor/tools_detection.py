"""
Supervisor Tools — Dead Agent Detection & Context Filtering.
============================================================
Extracted from tools.py during modularization.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.event_bus import AgentSignal

from .base import logger


def _filter_active_context_agents(
    agent_refs: Dict[str, "Agent"],
    context_id: str,
) -> list:
    """Filter agent_refs to only ACTIVE agents tracking a given context_id.

    Iteration 14 FIX: Completed/paused subordinates sharing a context_id
    with the orchestrator were causing false "still tracked by another agent"
    verdicts. This caused the supervisor to never declare task completion
    (the "subordinate confusion" bug).

    An agent is considered ACTIVE if:
    - Its context.id matches the target context_id
    - Its context is NOT paused

    Returns:
        List of active agent objects tracking this context_id.
    """
    active = []
    for aid, agent in agent_refs.items():
        ctx = getattr(agent, 'context', None)
        if ctx is None:
            continue
        agent_ctx_id = getattr(ctx, 'id', None)
        if agent_ctx_id != context_id:
            continue
        # Filter out paused/completed agents
        if getattr(ctx, 'paused', False):
            logger.debug(
                f"[SUPERVISOR] Filtering PAUSED agent '{aid}' from "
                f"context_id '{context_id}' tracking check"
            )
            continue
        active.append(agent)
    return active


# ── Dead-Agent Heartbeat Detection (Iteration 14, BUG-3 + Iteration 15, BUG-5) ──
# ── Iteration 127: Intelligent multi-signal assessment ──

DEAD_AGENT_THRESHOLD_SECONDS = 180  # 3 minutes
BLOCKED_TOOL_CEILING_SECONDS = 600  # 10 minutes — wall-clock ceiling for blocked-in-tool (ITR-31)


def detect_dead_agents(
    agent_refs: Dict[str, "Agent"],
    threshold_seconds: int = DEAD_AGENT_THRESHOLD_SECONDS,
) -> list:
    """Detect agents that have silently died using multi-signal assessment.

    Iteration 14 FIX (BUG-3): Heartbeat detection for silently dead agents.
    Iteration 15 FIX (BUG-5): Skip orchestrators waiting for subordinates.
    Iteration 127 FIX: Intelligent multi-signal assessment to eliminate
    false-positive 💀 alarms on successfully completed agents.

    The supervisor has access to the full Agent object — not just timestamps.
    An agent is only declared DEAD when ALL intelligent signals agree it's stuck:

    Gate 1 — Context idle:       execution_state == "idle" → SKIP (system done)
    Gate 2 — Task liveness:      task.is_alive() == False → SKIP (monologue exited)
    Gate 3 — Response delivery:  _last_response_content set → SKIP (response tool called)
    Gate 4 — Active subordinates (BUG-5): sibling has recent LLM → SKIP (waiting)
    Fallback — Timestamp:        last_llm_ts > threshold → DEAD

    Args:
        agent_refs: Dict mapping agent_id → Agent objects.
        threshold_seconds: How many seconds of silence constitute "dead".
            Default is 180 (3 minutes).

    Returns:
        List of dicts with diagnostic info for each dead agent:
        [{"agent_id": ..., "context_id": ..., "iteration": ..., "last_llm_age_s": ...}]
    """
    import time as _time

    dead: list = []
    now = _time.time()

    for agent_id, agent in agent_refs.items():
        # Get last LLM timestamp
        last_llm_ts = 0.0
        loop_data = getattr(agent, 'loop_data', None)
        if loop_data is not None:
            last_llm_ts = getattr(loop_data, 'last_successful_llm_ts', 0.0)

        # Skip agents that never started (ts == 0)
        if last_llm_ts <= 0:
            continue

        # Skip paused agents (intentionally idle)
        ctx = getattr(agent, 'context', None)
        is_paused = getattr(ctx, 'paused', False) if ctx else False
        if is_paused:
            continue

        # Check if agent exceeds the dead threshold (strictly greater than)
        age_s = now - last_llm_ts
        if age_s > threshold_seconds:
            context_id = getattr(ctx, 'id', 'unknown') if ctx else 'unknown'
            iteration = getattr(loop_data, 'iteration', 0) if loop_data else 0

            # ── GATE 1: Context idle check ──
            # If execution_state == "idle", the entire _process_chain has completed
            # (set in context.py finally block, line 1014). All agents in this
            # context finished their work — none are dead.
            exec_state = getattr(ctx, 'execution_state', 'unknown') if ctx else 'unknown'
            if exec_state == "idle":
                logger.debug(
                    f"[SUPERVISOR] ✅ Agent '{agent_id}' stale ({age_s:.0f}s) but "
                    f"context execution_state='idle' — system COMPLETED, not dead."
                )
                continue

            # ── GATE 2: Task liveness check ──
            # If the context's DeferredTask exists but is NOT alive, the monologue
            # exited normally (returned via response tool or hit a limit). The agent
            # is DONE, not dead. A None task means it was never started or already
            # cleaned up — also not dead.
            task = getattr(ctx, 'task', None) if ctx else None
            if task is not None:
                try:
                    task_alive = task.is_alive()
                except Exception:
                    task_alive = False  # If we can't check, assume exited
                if not task_alive:
                    # Task is dead — but WHY? Two scenarios:
                    # A) Normal exit: agent called response tool → delivered work → done
                    # B) Forced kill: cancel_task (Step 4) killed the task mid-execution
                    #
                    # Only skip (A). For (B), fall through to dead detection so
                    # Step 5 (parent re-delegation) can fire. Without this fix,
                    # cancel_task silently drops work: Gate 2 says "completed"
                    # but the agent never delivered. (MSR_Smoke_1777332729 RCA)
                    _agent_data = getattr(agent, 'data', {}) or {}
                    if _agent_data.get("_last_response_content") is not None:
                        logger.debug(
                            f"[SUPERVISOR] ✅ Agent '{agent_id}' stale ({age_s:.0f}s) but "
                            f"monologue task exited AND response delivered — COMPLETED, not dead."
                        )
                        continue
                    else:
                        logger.warning(
                            f"[SUPERVISOR] ⚠️ Agent '{agent_id}' task is dead "
                            f"(is_alive=False) but NO response was delivered — "
                            f"likely killed by cancel_task. Treating as DEAD for "
                            f"re-delegation."
                        )
                        # Fall through to dead detection — don't continue
            elif task is None:
                # No task at all — agent completed and task was cleaned up,
                # or agent was never started. Either way, not dead.
                logger.debug(
                    f"[SUPERVISOR] ✅ Agent '{agent_id}' stale ({age_s:.0f}s) but "
                    f"no monologue task exists (task=None) — not dead."
                )
                continue

            # ── GATE 3: Response delivery check ──
            # If the agent's data contains _last_response_content, the response tool
            # was called at least once — the agent delivered its work product.
            # (Set by response.py line 117: agent.data["_last_response_content"] = text)
            agent_data = getattr(agent, 'data', {})
            if agent_data.get("_last_response_content") is not None:
                logger.debug(
                    f"[SUPERVISOR] ✅ Agent '{agent_id}' stale ({age_s:.0f}s) but "
                    f"response tool was called — agent DELIVERED response, not dead."
                )
                continue

            # ── GATE 4 (BUG-5 + RCA-471): Skip if subordinates EXIST in same context ──
            # An orchestrator blocking on call_subordinate will have no LLM
            # activity, but its subordinates may. If ANY other agent in the
            # same context has an alive task, the parent is just waiting.
            #
            # RCA-471 FIX: Changed from LLM-recency check to task-liveness
            # check. During rate limits, subordinates are also stale — but
            # their task IS alive. The old check (`now - other_llm_ts <
            # threshold`) falsely flagged parents as dead during rate limits.
            has_alive_subordinates = False
            has_subordinates_in_context = False
            for other_id, other_agent in agent_refs.items():
                if other_id == agent_id:
                    continue
                other_ctx = getattr(other_agent, 'context', None)
                other_ctx_id = getattr(other_ctx, 'id', None) if other_ctx else None
                if other_ctx_id != context_id:
                    continue
                # Skip paused agents
                if getattr(other_ctx, 'paused', False):
                    continue
                has_subordinates_in_context = True
                # Check if subordinate's monologue task is alive
                other_task = getattr(other_ctx, 'task', None)
                other_task_alive = False
                if other_task is not None:
                    try:
                        other_task_alive = other_task.is_alive()
                    except Exception:
                        other_task_alive = False
                # Also check for recent LLM (original fast-path)
                other_loop = getattr(other_agent, 'loop_data', None)
                other_llm_ts = getattr(other_loop, 'last_successful_llm_ts', 0.0) if other_loop else 0.0
                other_has_recent_llm = other_llm_ts > 0 and (now - other_llm_ts) < threshold_seconds

                if other_task_alive or other_has_recent_llm:
                    has_alive_subordinates = True
                    reason = "task alive" if other_task_alive else "recent LLM"
                    logger.info(
                        f"[SUPERVISOR] ⏳ Agent '{agent_id}' is stale ({age_s:.0f}s) "
                        f"but '{other_id}' exists in same context ({reason}) — "
                        f"parent is waiting for subordinate, NOT dead."
                    )
                    break

            if has_alive_subordinates:
                continue

            # ── GATE 4.5 (RCA-471): Router agent detection ──
            # The Default agent is primarily a ROUTER: it receives the user
            # message, delegates to a specialist (Multiagentdev, etc.), and
            # then sits idle permanently. Its job is DONE at iteration=0.
            # Only rare small tasks cause Default to iterate beyond 0.
            #
            # Pattern: iteration == 0 + subordinates EXIST in same context
            #          + agent task is alive → router that completed its job.
            #
            # This gate prevents the false-positive nudge cycle where the
            # supervisor nudges Default → Default "recovers" (does nothing) →
            # goes idle → gets nudged again → 2 wasted LLM calls per cycle.
            if iteration == 0 and has_subordinates_in_context:
                logger.info(
                    f"[SUPERVISOR] ✅ Agent '{agent_id}' is stale ({age_s:.0f}s) "
                    f"but iteration=0 with subordinates in context '{context_id}' — "
                    f"ROUTER agent that completed its routing job. NOT dead."
                )
                continue


            # ── GATE 5: Active work in-flight check (MSR_Smoke_1776891952) ──
            # If last_activity_ts is more recent than last_llm_ts and within
            # threshold, the agent is actively working (tool execution or LLM
            # call in flight), NOT dead. This prevents false-positive deaths
            # during long-running operations like npm install or slow API calls.
            last_activity_ts = getattr(loop_data, 'last_activity_ts', 0.0) if loop_data else 0.0
            if last_activity_ts > last_llm_ts and last_activity_ts > 0:
                activity_age = now - last_activity_ts
                if activity_age < threshold_seconds:
                    logger.info(
                        f"[SUPERVISOR] ⏳ Agent '{agent_id}' LLM stale ({age_s:.0f}s) but "
                        f"last activity only {activity_age:.0f}s ago — "
                        f"agent is working (tool execution or LLM call in flight)."
                    )
                    continue

            # ── GATE 6: Blocked-in-tool check (MSR_Smoke_1777332729 RCA) ──
            # code_execution.py sets agent.data["_blocked_in_tool"] = True
            # during get_terminal_output() (the blocking output read loop).
            # call_subordinate.py sets it to a structured dict with tool context
            # (tool name, subordinate profile, complexity, started_at) so the
            # supervisor knows WHAT is running and expected duration class.
            #
            # ITR-31 FIX: Wall-clock ceiling. If the agent has been blocked
            # for longer than BLOCKED_TOOL_CEILING_SECONDS, Gate 6 no longer
            # protects it — fall through to dead detection. This prevents
            # zombie browser processes from keeping agents "alive" indefinitely.
            agent_data = getattr(agent, 'data', {}) or {}
            blocked_info = agent_data.get("_blocked_in_tool", False)
            if blocked_info:
                # Extract context for intelligent logging
                if isinstance(blocked_info, dict):
                    tool_name = blocked_info.get("tool", "unknown")
                    sub_profile = blocked_info.get("subordinate_profile", "")
                    complexity = blocked_info.get("complexity", "unknown")
                    started = blocked_info.get("started_at", 0) or blocked_info.get("started", 0)
                    wait_s = round(now - started, 0) if started else 0

                    # ITR-31: Enforce wall-clock ceiling
                    if started and wait_s > BLOCKED_TOOL_CEILING_SECONDS:
                        logger.warning(
                            f"[SUPERVISOR] ⏰ WALL-CLOCK CEILING EXCEEDED: Agent '{agent_id}' "
                            f"blocked in {tool_name} for {wait_s:.0f}s "
                            f"(ceiling={BLOCKED_TOOL_CEILING_SECONDS}s). "
                            f"Gate 6 no longer protects — treating as DEAD."
                        )
                        # Fall through to FALLBACK (dead detection)
                    else:
                        logger.info(
                            f"[SUPERVISOR] ⏳ Agent '{agent_id}' LLM stale ({age_s:.0f}s) "
                            f"but _blocked_in_tool={{tool={tool_name}, "
                            f"profile={sub_profile}, complexity={complexity}, "
                            f"waiting={wait_s:.0f}s}} — NOT dead."
                        )
                        continue
                else:
                    # Legacy boolean _blocked_in_tool=True (no started timestamp).
                    # Without a timestamp we cannot compute elapsed time, so we
                    # still skip dead detection for backward compatibility.
                    logger.info(
                        f"[SUPERVISOR] ⏳ Agent '{agent_id}' LLM stale ({age_s:.0f}s) "
                        f"but _blocked_in_tool=True — agent is alive in "
                        f"blocking tool execution. NOT dead."
                    )
                    continue

            # ── FALLBACK: All gates passed — agent is genuinely dead ──
            dead.append({
                "agent_id": agent_id,
                "context_id": context_id,
                "iteration": iteration,
                "last_llm_age_s": round(age_s, 1),
            })
            logger.warning(
                f"[SUPERVISOR] 💀 DEAD AGENT detected: '{agent_id}' — "
                f"last LLM activity {age_s:.0f}s ago (threshold: {threshold_seconds}s), "
                f"iteration={iteration}, context={context_id}, "
                f"exec_state={exec_state}, task_alive=True, no_response=True"
            )

    return dead

