"""
Batch Execution Engine — Extracted from call_subordinate_batch.py (P1.2)

Contains all execution-phase logic: single-task execution with message
enrichment, parallel/wave/sequential execution modes, rate-limit coordination,
subordinate agent creation, orphan monitoring, and retry logic.

All functions are class methods on BatchDelegation — this module is imported
by the main tool file which delegates to these methods via mixin-style
inheritance or direct delegation.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from python.agent import Agent, UserMessage
from python.helpers.agent_tracer import AgentTracer
from python.helpers.rate_limiter import RateLimiter, coordinate_agent_wait
from python.helpers.batch_types import (
    BatchExecutionMode, BatchResult, BatchTask, TaskStatus,
    PARENT_HEARTBEAT_TIMEOUT, _DEFAULT_BATCH_TIMEOUT,
    _DEFAULT_MAX_TASK_RETRIES, _is_non_retriable,
    build_budget_message, compute_batch_timeout,
    estimate_task_timeout_with_tier, get_batch_task_timeout,
    get_effective_timeout,
)
from python.initialize import initialize_agent

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agix.batch_subordinate")


# ── Standalone execution functions ──
# These were class methods on BatchDelegation, now extracted as module-level
# functions. Each takes `tool` (the BatchDelegation instance) as first arg.
# The class delegates to these via thin wrapper methods.

async def _execute_sequential(
    tool,
    tasks: List[BatchTask],
    result: BatchResult
) -> None:
    """Execute tasks one at a time."""
    for task in tasks:
        if task.status == TaskStatus.CANCELLED:
            continue
        await _execute_single_task(tool, task, all_tasks=tasks)


async def _retry_failed_tasks(
    tool,
    tasks: List[BatchTask],
    max_retries: int = _DEFAULT_MAX_TASK_RETRIES,
) -> int:
    """Retry FAILED/TIMEOUT/CANCELLED tasks with fresh subordinates.
    
    Iteration 23 (Tier 1): Automatic recovery for transient failures.
    
    When a task fails due to CancelledError, timeout, or exception, this
    method re-executes it with a FRESH subordinate (the old monologue loop
    is dead). Tasks that hit structural limits (ITERATION_LIMIT, CHAIN_LIMIT)
    are NOT retried — the root cause won't change.
    
    Args:
        tasks: All batch tasks (will filter to retriable failures).
        max_retries: Maximum retry attempts per task. Default 1.
    
    Returns:
        Number of tasks successfully recovered.
    """
    # Collect retriable failures
    retriable = [
        t for t in tasks
        if t.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED)
        and t.metadata.get("_retry_attempt", 0) < max_retries
        and not _is_non_retriable(t)
    ]
    
    if not retriable:
        return 0
    
    profiles = [t.profile or "default" for t in retriable]
    logger.warning(
        f"[BATCH-RETRY] Retrying {len(retriable)} failed/timed-out tasks "
        f"(profiles: {', '.join(profiles)}, attempt "
        f"{retriable[0].metadata.get('_retry_attempt', 0) + 1}/{max_retries})"
    )
    
    recovered = 0
    for task in retriable:
        # Track retry attempt
        task.metadata["_retry_attempt"] = task.metadata.get("_retry_attempt", 0) + 1
        
        # Reset task state for re-execution
        old_status = task.status.value
        old_error = task.error
        task.status = TaskStatus.PENDING
        task.error = None
        task.result = None
        task.start_time = None
        task.end_time = None
        task.last_activity_time = 0.0
        
        logger.info(
            f"[BATCH-RETRY] Retrying task '{task.id}' "
            f"(was {old_status}: {str(old_error)[:100]})"
        )
        
        # Execute with a FRESH subordinate (the old one's task is dead)
        await _execute_single_task(tool, task, all_tasks=tasks)
        
        if task.status == TaskStatus.COMPLETED:
            recovered += 1
            logger.info(
                f"[BATCH-RETRY] ✅ Task '{task.id}' recovered on retry "
                f"(attempt {task.metadata['_retry_attempt']})"
            )
        else:
            logger.warning(
                f"[BATCH-RETRY] ❌ Task '{task.id}' still {task.status.value} "
                f"after retry {task.metadata['_retry_attempt']}/{max_retries}"
            )
    
    if recovered > 0:
        logger.warning(
            f"[BATCH-RETRY] Recovered {recovered}/{len(retriable)} tasks"
        )
    
    return recovered


async def _execute_parallel(
    tool,
    tasks: List[BatchTask],
    result: BatchResult,
    max_concurrent: int
) -> None:
    """Execute tasks in parallel with concurrency limit and batch timeout.
    
    Fix #3 (RCA-2026-04-20): Replaced bare asyncio.gather() with
    asyncio.wait(timeout=) to prevent infinite hangs when subordinates
    get stuck on dead HTTP connections. nest_asyncio prevents wait_for
    from cancelling tasks, so we use asyncio.wait which returns
    (done, pending) sets for explicit cleanup.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def execute_with_semaphore(task: BatchTask):
        async with semaphore:
            if task.status != TaskStatus.CANCELLED:
                await _execute_single_task(tool, task, all_tasks=tasks)
    
    # Compute batch-level timeout from per-task timeouts
    task_timeouts = [get_effective_timeout(t.timeout) for t in tasks]
    batch_timeout = compute_batch_timeout(task_timeouts)
    logger.info(
        f"[BATCH] Parallel execution: {len(tasks)} tasks, "
        f"batch_timeout={batch_timeout:.0f}s (max_task={max(task_timeouts):.0f}s)"
    )
    
    # Create asyncio tasks
    async_tasks = [
        asyncio.ensure_future(execute_with_semaphore(task))
        for task in tasks
    ]

    # Register tasks in TaskRegistry for supervisor IO-Breaker (Phase 2)
    try:
        from python.helpers.task_registry import TaskRegistry
        registry = TaskRegistry.instance()
        context_id = tool.agent.context.id if hasattr(tool.agent, 'context') and tool.agent.context else "unknown"
        for i, at in enumerate(async_tasks):
            task_id = getattr(tasks[i], 'id', f'batch_{i}') if i < len(tasks) else f'batch_{i}'
            composite_id = f"{task_id}@{context_id}"
            registry.register_task(composite_id, at)
        logger.debug(f"[BATCH] Registered {len(async_tasks)} tasks in TaskRegistry")
    except Exception as e:
        logger.debug(f"[BATCH] TaskRegistry registration skipped: {e}")
    
    # Execute with batch-level timeout
    done, pending = await asyncio.wait(
        async_tasks,
        timeout=batch_timeout,
        return_when=asyncio.ALL_COMPLETED
    )
    
    # Cancel and cleanup any tasks that exceeded the batch timeout
    if pending:
        logger.warning(
            f"[BATCH] Batch timeout ({batch_timeout:.0f}s) hit! "
            f"{len(pending)} tasks still pending, {len(done)} completed. "
            f"Cancelling hung tasks."
        )
        for p_task in pending:
            p_task.cancel()
        # Wait briefly for cancellations to propagate
        if pending:
            await asyncio.wait(pending, timeout=5.0)
        
        # Mark the corresponding BatchTasks as TIMEOUT
        # Map asyncio tasks back to BatchTasks by index
        for i, async_task in enumerate(async_tasks):
            if async_task in pending and i < len(tasks):
                if tasks[i].status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    tasks[i].status = TaskStatus.TIMEOUT
                    tasks[i].error = (
                        f"Batch timeout ({batch_timeout:.0f}s) exceeded. "
                        f"Task cancelled to prevent infinite hang."
                    )
                    tasks[i].end_time = time.time()
                    logger.warning(
                        f"[BATCH] Task {tasks[i].id} marked TIMEOUT "
                        f"(batch-level, not per-task)"
                    )
    
    # Collect any exceptions or cancellations from completed tasks
    for d_task in done:
        if d_task.cancelled():
            # FIX (Iteration 22 / RCA-22): CancelledError tasks report
            # .cancelled()=True but .exception()=None. Without this check,
            # cancelled tasks were silently ignored.
            logger.warning(
                f"[BATCH] Task was cancelled (asyncio.CancelledError). "
                f"In Python 3.9+, CancelledError is BaseException and "
                f"bypasses except Exception handlers."
            )
        elif d_task.exception() is not None:
            logger.warning(
                f"[BATCH] Task exception: {d_task.exception()}"
            )

    # Cleanup completed tasks from TaskRegistry (Phase 2)
    try:
        from python.helpers.task_registry import TaskRegistry
        TaskRegistry.instance().cleanup_done()
    except Exception:
        pass
async def _execute_wave(
    tool,
    tasks: List[BatchTask],
    result: BatchResult,
    max_concurrent: int
) -> None:
    """Execute tasks in dependency-ordered waves."""
    # Build dependency graph
    task_map = {t.id: t for t in tasks}
    completed_ids: set = set()
    
    while True:
        # Find tasks ready to execute (all dependencies satisfied)
        ready_tasks = [
            t for t in tasks
            if t.status == TaskStatus.PENDING
            and all(dep in completed_ids for dep in t.dependencies)
        ]
        
        if not ready_tasks:
            # Check if we're stuck (circular dependencies or all done)
            pending = [t for t in tasks if t.status == TaskStatus.PENDING]
            if pending:
                # Mark remaining as failed due to unmet dependencies
                for t in pending:
                    t.status = TaskStatus.FAILED
                    t.error = f"Unmet dependencies: {t.dependencies}"
            break
        
        # Execute this wave in parallel
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def execute_with_semaphore(task: BatchTask):
            async with semaphore:
                await _execute_single_task(tool, task, all_tasks=tasks)
                if task.status == TaskStatus.COMPLETED:
                    completed_ids.add(task.id)
        
        # Fix #3 (RCA-2026-04-20): Use asyncio.wait with timeout for wave execution
        wave_timeouts = [get_effective_timeout(t.timeout) for t in ready_tasks]
        wave_timeout = compute_batch_timeout(wave_timeouts)
        
        wave_async_tasks = [
            asyncio.ensure_future(execute_with_semaphore(task))
            for task in ready_tasks
        ]

        # Register wave tasks in TaskRegistry (Phase 2)
        try:
            from python.helpers.task_registry import TaskRegistry
            registry = TaskRegistry.instance()
            context_id = tool.agent.context.id if hasattr(tool.agent, 'context') and tool.agent.context else "unknown"
            for i, at in enumerate(wave_async_tasks):
                task_id = getattr(ready_tasks[i], 'id', f'wave_{i}') if i < len(ready_tasks) else f'wave_{i}'
                composite_id = f"{task_id}@{context_id}"
                registry.register_task(composite_id, at)
        except Exception:
            pass
        
        w_done, w_pending = await asyncio.wait(
            wave_async_tasks,
            timeout=wave_timeout,
            return_when=asyncio.ALL_COMPLETED
        )
        
        if w_pending:
            logger.warning(
                f"[BATCH] Wave timeout ({wave_timeout:.0f}s) hit! "
                f"{len(w_pending)} tasks still pending. Cancelling."
            )
            for p_task in w_pending:
                p_task.cancel()
            if w_pending:
                await asyncio.wait(w_pending, timeout=5.0)
            for i, async_task in enumerate(wave_async_tasks):
                if async_task in w_pending and i < len(ready_tasks):
                    if ready_tasks[i].status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                        ready_tasks[i].status = TaskStatus.TIMEOUT
                        ready_tasks[i].error = f"Wave timeout ({wave_timeout:.0f}s) exceeded."
                        ready_tasks[i].end_time = time.time()

        # Cleanup wave tasks from TaskRegistry
        try:
            from python.helpers.task_registry import TaskRegistry
            TaskRegistry.instance().cleanup_done()
        except Exception:
            pass
        
        # FIX (Iteration 22 / RCA-22): Check for cancelled tasks in wave
        for d_task in w_done:
            if d_task.cancelled():
                logger.warning(
                    f"[BATCH] Wave task was cancelled (asyncio.CancelledError)."
                )


async def _execute_single_task(tool, task: BatchTask, all_tasks: List[BatchTask] = None) -> None:
    """Execute a single task using a subordinate agent."""
    task.status = TaskStatus.RUNNING
    task.start_time = time.time()
    
    # Get provider info for rate limit coordination
    provider = tool.agent.config.chat_model.provider
    model_name = tool.agent.config.chat_model.name
    provider_key = f"{provider}\\{model_name}"
    
    # Check for global rate limit backoff before creating/running subordinate
    wait_time = await coordinate_agent_wait(provider, provider_key)
    if wait_time > 0:
        logger.warning(
            f"[BATCH_BACKOFF] Task {task.id} waited {wait_time:.1f}s for "
            f"provider={provider_key} rate limit backoff"
        )
    
    subordinate = None
    try:
        # Create or get subordinate agent with numbered name
        # Extract index from task ID (e.g. "task_3" -> 3)
        # Fallback to 0 if the suffix isn't numeric (e.g. "browser_task")
        try:
            task_idx = int(task.id.split("_")[-1]) if "_" in task.id else 0
        except (ValueError, IndexError):
            task_idx = 0
        subordinate = await _get_or_create_subordinate(tool, profile=task.profile, task_index=task_idx)
        task.agent_number = subordinate.number
        
        # Iteration 23 (Tier 2): Store parent reference for supervisor context hierarchy
        subordinate.data["_parent_agent_number"] = tool.agent.number
        subordinate.data["_batch_task_id"] = task.id
        subordinate.data["_batch_task_message"] = task.message[:200]  # Truncated for memory
        
        # Log subordinate identity for tracing (RC-20)
        logger.info(
            f"[BATCH] Spawned {subordinate.display_name} "
            f"(uid={subordinate.session_uid[:8]}) for task {task.id} "
            f"(profile={task.profile or 'NONE — inherits parent'})"
        )
        
        # Warn on no-profile tasks — these inherit the parent's profile
        # which can cause same-profile delegation chains (RC-20b)
        if not task.profile:
            logger.warning(
                f"[RC-20b] Task '{task.id}' has NO profile specified. "
                f"It will inherit parent profile '{getattr(tool.agent.config, 'profile', 'default')}'. "
                f"This may cause same-profile delegation chains."
            )
        
        # ── SERVICE CONTEXT BRIDGE: Downward propagation (RCA-2) ──
        # Propagate _dev_server_port from parent → subordinate so browser
        # agents know which port to test on. Without this, browser tasks
        # default to port 3000 while the actual server is on e.g. 5139.
        try:
            from python.helpers.service_context_bridge import propagate_service_context_down
            propagate_service_context_down(tool.agent, subordinate)
        except Exception as e:
            logger.debug(f"Service context downward propagation skipped for task {task.id}: {e}")

        # ── DATA PROPAGATION BRIDGE: Parity with call_subordinate.py (RCA-315c) ──
        # Propagate _active_project_dir, _dev_server_*, _research_depth from
        # parent.data → subordinate.data. This was MISSING — only call_subordinate.py
        # called this, leaving batch subordinates without project context.
        try:
            from python.helpers.delegation_message import propagate_data_to_subordinate
            propagate_data_to_subordinate(tool.agent, subordinate, {})
        except Exception as e:
            logger.debug(f"Data propagation to subordinate skipped for task {task.id}: {e}")

        # ── PRE-DELEGATION SECRET EXTRACTION (Gap-4: Parity with call_subordinate.py) ──
        # Extract API keys from the original user prompt and store them in the
        # vault BEFORE the env bridge runs. Without this, batch subordinates
        # never get secrets from inline prompts (e.g., "OPENROUTER_API_KEY=sk-or-' + 'v1-xxx").
        try:
            project_dir = tool.agent.data.get("_active_project_dir", "")
            project_name = os.path.basename(project_dir) if project_dir else ""
            if project_name and not tool.agent.data.get("_prompt_secrets_extracted"):
                from python.helpers.boomerang_context import get_original_user_message
                from python.helpers.prompt_secret_extractor import (
                    extract_secrets_from_text,
                    store_extracted_secrets,
                )
                original_msg = get_original_user_message(tool.agent)
                if original_msg:
                    secrets = extract_secrets_from_text(original_msg)
                    if secrets:
                        count = store_extracted_secrets(project_name, secrets)
                        if count > 0:
                            logger.info(
                                f"[PROMPT SECRET EXTRACTOR] Extracted {count} secrets "
                                f"from user prompt for project '{project_name}' (batch task {task.id})"
                            )
                tool.agent.data["_prompt_secrets_extracted"] = True
        except Exception as e:
            logger.warning(f"[PROMPT SECRET EXTRACTOR] Failed for batch task {task.id} (non-fatal): {e}")

        # Initialise task_message BEFORE any block that references it
        # (SS-6 / ITR-344: was previously assigned AFTER the env bridge,
        # causing UnboundLocalError when the bridge path executed).
        task_message = task.message

        # ── PRE-DELEGATION ENV BRIDGE (Gap-4: Parity with call_subordinate.py) ──
        # Bridge vault secrets to .env.local before batch subordinate starts.
        # This was MISSING — only call_subordinate.py called this, leaving
        # batch subordinates without env vars, causing runtime API failures.
        try:
            project_dir = tool.agent.data.get("_active_project_dir", "")
            project_name = os.path.basename(project_dir) if project_dir else ""
            if project_dir and project_name:
                from python.helpers.pre_delegation_env_bridge import ensure_env_before_delegation
                bridged = ensure_env_before_delegation(project_dir, project_name)
                if bridged:
                    logger.info(f"[ENV BRIDGE] Bridged secrets to .env.local for {project_name} (batch task {task.id})")
                if bridged.missing_keys:
                    logger.warning(
                        f"[ENV BRIDGE] Missing secrets for {project_name} (batch task {task.id}): "
                        f"{bridged.missing_keys}"
                    )
                # Inject written_keys into task message so subordinate knows
                # which env vars are available (parity with SS-5 ITR-23).
                if bridged and bridged.written_keys:
                    from python.helpers.pre_delegation_env_bridge import build_env_var_section
                    env_section = build_env_var_section(bridged)
                    if env_section:
                        task_message = task_message + "\n\n" + env_section
        except Exception as e:
            logger.warning(f"[ENV BRIDGE] Failed for batch task {task.id} (non-fatal): {e}")

        # Trace subordinate creation
        AgentTracer.trace_subordinate_created(
            parent_agent=tool.agent,
            subordinate_agent=subordinate,
            mission=task.message
        )
        
        # Add task message to subordinate (with swarm instructions if available)
        
        # ── RC-20c: SIBLING TASK MANIFEST INJECTION ──
        # When Default (parent orchestrator) spawns sub-orchestrators like
        # multiagentdev/alex, they need to know what work has ALREADY been
        # scheduled to other siblings in this batch. Without this, the sub-
        # orchestrator re-decomposes its focused task into duplicate sub-tasks
        # (image gen, browser, etc.) that overlap with first-level siblings.
        #
        # Flow: Default decomposes → [researcher, browser, multiagentdev, frontend]
        #       multiagentdev receives manifest: "researcher is doing X, browser
        #       is doing Y, frontend is doing Z — YOU only handle repo_and_code"
        if all_tasks and task.profile:
            try:
                from python.helpers.boomerang_context import ORCHESTRATOR_PROFILES
                if task.profile.lower() in ORCHESTRATOR_PROFILES:
                    import json as _json
                    sibling_manifest = []
                    for sibling in all_tasks:
                        if sibling.id == task.id:
                            continue  # Skip self
                        sibling_manifest.append({
                            "task_id": sibling.id,
                            "profile": sibling.profile or "default",
                            "summary": (sibling.message or "")[:120]
                        })
                    if sibling_manifest:
                        manifest_json = _json.dumps(sibling_manifest, indent=2)
                        task_message = (
                            f"## ⚠️ ALREADY-SCHEDULED SIBLING TASKS (DO NOT DUPLICATE)\n"
                            f"The parent orchestrator has ALREADY delegated the following tasks to "
                            f"other specialized agents in this same batch. You MUST NOT re-create, "
                            f"re-schedule, or duplicate any of this work. Focus ONLY on YOUR "
                            f"assigned task below.\n\n"
                            f"```json\n{manifest_json}\n```\n\n---\n\n"
                            f"## YOUR ASSIGNED TASK (Focus exclusively on this)\n{task_message}"
                        )
                        logger.info(
                            f"[RC-20c] Injected sibling manifest ({len(sibling_manifest)} tasks) "
                            f"into orchestrator subordinate {task.id} (profile={task.profile})"
                        )
            except Exception as e:
                logger.debug(f"Sibling manifest injection skipped for batch task {task.id}: {e}")
        
        try:
            from python.tools.call_subordinate import _swarm_instructions_cache
            # GUARD: Only inject swarm instructions for code-related profiles
            # to prevent contaminating non-code tasks (e.g., legal doc analysis)
            # Iteration 109: Added 'researcher' for Context7 workflow guidance
            CODE_PROFILES = {"code", "frontend", "debug", "architect", "e2e", "review", "ask", "researcher"}
            should_inject_swarm = (task.profile or "").lower() in CODE_PROFILES
            # Reuse the swarm instruction loader from call_subordinate
            swarm_instructions = _load_swarm_instructions(tool) if should_inject_swarm else None
            if swarm_instructions:
                task_message = f"## Swarm Instructions (Mandatory)\n{swarm_instructions}\n\n---\n\n## Your Task\n{task_message}"
        except Exception as e:
            logger.debug(f"Swarm instruction injection skipped for batch task {task.id}: {e}")
        
        # ── PROJECT SCOPE INJECTION (MSR-3): inherit parent project scope ──
        try:
            from python.helpers import projects
            project_name = projects.get_context_project_name(tool.agent.context)
            if project_name:
                project_path = projects.get_project_folder(project_name)
                task_message = (
                    f"## Active Project Scope\n"
                    f"**Project:** `{project_name}`\n"
                    f"**Path:** `{project_path}`\n"
                    f"All work, secrets, and parameters MUST be scoped to this project.\n"
                    f"**Temp Dir:** `{project_path}/tmp/` — NEVER use system `/tmp/`. "
                    f"Run `mkdir -p {project_path}/tmp/` before use.\n\n---\n\n"
                    + task_message
                )
                # RCA-315c: Also SET _active_project_dir on subordinate.data
                # The text injection above is informational only — tools like
                # code_execution.get_cwd() need the actual data flag set.
                if not subordinate.data.get("_active_project_dir"):
                    subordinate.data["_active_project_dir"] = project_path
                    logger.info(
                        f"[RCA-315c] Set _active_project_dir='{project_path}' on "
                        f"batch subordinate for task {task.id}"
                    )
        except Exception as e:
            logger.debug(f"Project scope injection skipped for batch task {task.id}: {e}")

        # ── DELEGATION PACKAGE: Universal context injection (RCA-Context-Loss) ──
        # Same consolidated package builder as call_subordinate.py — curated,
        # profile-aware package with all 3 tiers of context.
        # ISS-B: Now includes phase detection for fix-mode awareness.
        # Scope guard is integrated as first section inside build_delegation_package.
        try:
            project_dir = tool.agent.data.get("_active_project_dir", "")
            if project_dir:
                from python.helpers.delegation_brief import build_delegation_package, detect_delegation_phase
                batch_phase = detect_delegation_phase(task_message, tool.agent.data)
                task_message = build_delegation_package(
                    profile=task.profile or "",
                    message=task_message,
                    kwargs=task.metadata,
                    project_dir=project_dir,
                    agent=tool.agent,
                    subordinate=subordinate,
                    is_batch=True,
                    phase=batch_phase,
                )
        except Exception as e:
            logger.debug(f"Delegation package injection skipped for batch task {task.id}: {e}")

        # ── ORCHESTRATOR CONTEXT INJECTION: Forward FULL original user message ──
        # When batch-delegating to an orchestrator profile, the parent LLM may
        # summarize the task narrowly. Inject the full original user message.
        #
        # RC-20 DEPTH GUARD: Only inject when the CALLING agent is top-level
        # (has no superior). If this agent was itself spawned as a subordinate,
        # its children must get FOCUSED tasks only — not the full original message.
        # Without this guard, subordinate-orchestrators (e.g. multiagentdev) would
        # re-decompose the entire request, spawning duplicate tasks for image gen,
        # browser checks, etc. that were already assigned to other first-level subs.
        has_superior = tool.agent.get_data(Agent.DATA_NAME_SUPERIOR) is not None
        if task.profile and not has_superior:
            try:
                from python.helpers.boomerang_context import ORCHESTRATOR_PROFILES, get_original_user_message
                if task.profile.lower() in ORCHESTRATOR_PROFILES:
                    original_msg = get_original_user_message(tool.agent)
                    if original_msg and original_msg not in task_message:
                        task_message = (
                            f"## FULL ORIGINAL USER REQUEST\n"
                            f"You are an orchestrator. Below is the COMPLETE original user request. "
                            f"You MUST use this as your source of truth, not the summary above.\n\n"
                            f"{original_msg}\n\n---\n\n"
                            f"## Parent Agent's Delegation Notes\n{task_message}"
                        )
            except Exception as e:
                logger.debug(f"Orchestrator context injection skipped for batch task {task.id}: {e}")
        elif has_superior and task.profile:
            try:
                from python.helpers.boomerang_context import ORCHESTRATOR_PROFILES
                if task.profile.lower() in ORCHESTRATOR_PROFILES:
                    logger.info(
                        f"[RC-20] Skipping full-context injection for task {task.id} "
                        f"(profile={task.profile}): parent is already a subordinate. "
                        f"Child will receive focused task only."
                    )
            except Exception:
                pass

        # ── RESEARCH DEPTH PROPAGATION (Iteration 111) ──
        # Each batch task can specify research_depth. Propagate to the
        # subordinate's agent.data so the research quality gate uses it
        # directly without heuristic inference.
        research_depth = task.metadata.get("research_depth", "")
        if research_depth in ("shallow", "deep"):
            subordinate.data["_research_depth"] = research_depth
            logger.info(
                f"[BATCH] Propagated research_depth='{research_depth}' to "
                f"subordinate for task {task.id}"
            )

        # ── BATCH METADATA PROPAGATION (RCA-264 Part 2) ──
        # Store batch task metadata on subordinate.data so the supervisor
        # can see what task this agent is working on, what timeout it has,
        # and who its parent is. This enables:
        # 1. Supervisor timeout awareness (knows when a task is near limit)
        # 2. Parent re-delegation (knows what message to re-delegate)
        # 3. Better logging/diagnostics
        timeout_s = task.timeout or get_batch_task_timeout()
        subordinate.data["_batch_task_id"] = task.id
        subordinate.data["_batch_task_message"] = (task.message or "")[:500]  # Truncate for memory
        subordinate.data["_batch_task_timeout"] = timeout_s
        subordinate.data["_batch_task_start_time"] = time.time()
        subordinate.data["_parent_agent_number"] = tool.agent.number

        # Get timeout tier label for budget awareness
        _, timeout_tier = estimate_task_timeout_with_tier(task.message or "")

        # ── SERVICE CONTEXT BRIDGE: Message injection for browser tasks (RCA-2) ──
        # Inject active dev server port into browser/e2e task messages so the
        # agent knows exactly which port to navigate to.
        try:
            from python.helpers.service_context_bridge import inject_service_context_message
            task_message = inject_service_context_message(
                task_message, tool.agent, task.profile
            )
        except Exception as e:
            logger.debug(f"Service context message injection skipped for task {task.id}: {e}")

        # ── BUDGET AWARENESS INJECTION (Forgejo #370, RCA-264 Part 2) ──
        # Tell subordinates their iteration & time limits so they can plan
        # within constraints rather than hitting hard limits silently.
        # Now includes tier label so agents know their complexity classification.
        try:
            sub_max_iters = getattr(subordinate.config, 'max_tool_response_length', 75)  # fallback
            if hasattr(subordinate.config, 'chat_model_kwargs'):
                # Profile-specific iteration limits are set via chain_limit
                pass
            budget_msg = build_budget_message(
                profile=task.profile or "default",
                timeout_seconds=timeout_s,
                max_iterations=sub_max_iters,
                timeout_tier=timeout_tier,
            )
            task_message += budget_msg
        except Exception as e:
            logger.debug(f"Budget injection skipped for batch task {task.id}: {e}")

        await subordinate.hist_add_user_message(UserMessage(
            message=task_message,
            attachments=[]
        ))
        
        # Execute with timeout and rate limit coordination
        result = await _run_with_rate_limit_coordination(
            tool, subordinate, task, provider_key
        )
        
        task.result = result
        
        # Strip boomerang context and completion markers from subordinate results
        # (#873: boomerang context leak, #874: completion marker persistence, #866)
        # and detect error-pattern results (#868)
        try:
            from python.helpers.boomerang_context import strip_boomerang, strip_completion_markers, is_error_result
            if isinstance(result, str):
                task.result = strip_boomerang(result)
                task.result = strip_completion_markers(task.result)
            
            # RCA-228: Detect agent lifecycle sentinels BEFORE generic error check.
            # monologue() catches CancelledError and returns "[CANCELLED] Agent X..."
            # as a normal string. Without this, cancelled tasks are marked COMPLETED
            # and _retry_failed_tasks() never fires → work is silently lost.
            result_str = str(task.result) if task.result else ""
            if "[CANCELLED]" in result_str:
                task.status = TaskStatus.CANCELLED
                task.error = f"Agent returned cancellation sentinel: {result_str[:200]}"
                logger.warning(
                    f"[RCA-228] Batch task {task.id} detected as CANCELLED via sentinel. "
                    f"Retry will be attempted with fresh subordinate."
                )
            elif any(tag in result_str for tag in ["[ITERATION_LIMIT]", "[CHAIN_LIMIT]", "[RESTART_LIMIT]"]):
                # Structural limits — these are NOT retriable (same prompt → same result)
                task.status = TaskStatus.FAILED
                task.error = f"Agent hit structural limit: {result_str[:200]}"
                logger.warning(f"Batch task {task.id} hit structural limit (non-retriable): {task.error}")
            elif is_error_result(task.result):
                task.status = TaskStatus.FAILED
                task.error = f"Subordinate returned error-like result: {str(task.result)[:200]}"
                logger.warning(f"Batch task {task.id} detected as error result: {task.error}")
            else:
                task.status = TaskStatus.COMPLETED
        except Exception as e:
            logger.warning(f"Post-processing failed for task {task.id}: {e}")
            task.status = TaskStatus.COMPLETED
        
        # ── SERVICE CONTEXT BRIDGE: Upward propagation (RCA-2) ──
        # Propagate _dev_server_port from subordinate → parent so later
        # sibling tasks (e.g., browser after code) can see the port.
        try:
            from python.helpers.service_context_bridge import propagate_service_context_up
            propagate_service_context_up(subordinate, tool.agent)
        except Exception as e:
            logger.debug(f"Service context upward propagation skipped for task {task.id}: {e}")

        # Trace subordinate completion
        AgentTracer.trace_subordinate_completed(
            parent_agent=tool.agent,
            subordinate_agent=subordinate,
            result=result
        )
            
    except asyncio.CancelledError:
        # FIX (Iteration 22 / RCA-22): In Python 3.9+, CancelledError is
        # BaseException, NOT Exception. Without this handler, cancelled tasks
        # silently vanish — the task status stays RUNNING and the supervisor
        # detects the agent as "dead" with no recovery path.
        task.status = TaskStatus.CANCELLED
        task.error = "Task was cancelled (asyncio.CancelledError)"
        logger.warning(
            f"[BATCH] Task {task.id} cancelled via asyncio.CancelledError. "
            f"Marked as CANCELLED instead of silently dying."
        )
        if subordinate:
            AgentTracer.trace_subordinate_completed(
                parent_agent=tool.agent,
                subordinate_agent=subordinate,
                result=f"CANCELLED: {task.error}"
            )
    except asyncio.TimeoutError:
        task.status = TaskStatus.TIMEOUT
        task.error = f"Task timed out after {task.timeout}s"
        if subordinate:
            AgentTracer.trace_subordinate_completed(
                parent_agent=tool.agent,
                subordinate_agent=subordinate,
                result=f"TIMEOUT: {task.error}"
            )
            
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)
        if subordinate:
            AgentTracer.trace_subordinate_completed(
                parent_agent=tool.agent,
                subordinate_agent=subordinate,
                result=f"FAILED: {task.error}"
            )
        
    finally:
        task.end_time = time.time()
        tool._log_task_complete(task)


async def _run_with_rate_limit_coordination(
    tool,
    subordinate: Agent,
    task: BatchTask,
    provider_key: str
) -> str:
    """
    Run subordinate monologue with rate limit coordination.
    
    If the subordinate hits a rate limit, coordinates with other agents
    and retries with exponential backoff. Handles rate limits gracefully
    without showing errors in UI - just retries silently.
    """
    # Increased max retries for better resilience
    max_rate_limit_retries = 10
    retry_count = 0
    total_wait_time = 0.0
    
    # Track rate limit stats for summary
    rate_limit_stats = task.metadata.get("_rate_limit_stats", {
        "retries": 0,
        "total_wait": 0.0
    })
    
    while retry_count < max_rate_limit_retries:
        try:
            # Check for global backoff before each attempt
            wait_time = await RateLimiter.get_global_wait_time(provider_key)
            if wait_time > 0:
                logger.warning(
                    f"[BATCH_BACKOFF] Task {task.id} waiting {wait_time:.1f}s for "
                    f"provider={provider_key} coordination backoff"
                )
                await asyncio.sleep(wait_time)
                total_wait_time += wait_time
            
            # ── Isolate subordinate's chain counter from parent ──
            saved_chain_count = subordinate.context._chain_monologue_iterations

            # Run the subordinate monologue with timeout
            # RCA Fix #1: ALWAYS use wait_for — never allow unguarded monologue.
            # The original code had a gap: if task.timeout was None/0,
            # subordinate.monologue() ran without any timeout, causing infinite hangs.
            effective_timeout = get_effective_timeout(task.timeout)
            result = await asyncio.wait_for(
                subordinate.monologue(),
                timeout=effective_timeout
            )
            
            # Restore parent's chain counter — subordinate iterations shouldn't count
            subordinate.context._chain_monologue_iterations = saved_chain_count

            # Handle subordinate failure gracefully
            if result is None:
                result = "Subordinate agent failed to produce a response."
                logger.warning(f"Batch task {task.id}: subordinate returned None")
            elif isinstance(result, str) and any(tag in result for tag in ['[ITERATION_LIMIT]', '[CHAIN_LIMIT]', '[RESTART_LIMIT]']):
                logger.warning(f"Batch task {task.id}: subordinate hit limit: {result[:120]}")
            
            # Store rate limit stats in metadata for summary
            if retry_count > 0:
                rate_limit_stats["retries"] = retry_count
                rate_limit_stats["total_wait"] = total_wait_time
                task.metadata["_rate_limit_stats"] = rate_limit_stats
            
            return result
            
        except asyncio.CancelledError:
            # FIX (Iteration 22 / RCA-22): Re-raise CancelledError — don't retry.
            # In Python 3.9+, this is BaseException and bypasses except Exception.
            raise
        except asyncio.TimeoutError:
            # Re-raise timeout errors - don't retry these
            raise
            
        except Exception as e:
            # Check if this is a rate limit error
            from python.models import _is_rate_limit_error, _extract_retry_after, calculate_retry_delay
            
            if _is_rate_limit_error(e):
                retry_count += 1
                
                # Extract retry-after or calculate backoff with more aggressive delays
                retry_after = _extract_retry_after(e)
                if retry_after:
                    delay = retry_after
                else:
                    # More aggressive backoff: start at 5s, max 180s
                    delay = calculate_retry_delay(
                        retry_count, base_delay=5.0, max_delay=180.0
                    )
                
                total_wait_time += delay
                
                # Update global backoff state for coordination
                from python.helpers.rate_limiter import RateLimitState
                await RateLimiter._set_global_state(
                    provider_key, RateLimitState.BACKING_OFF, delay
                )
                
                logger.warning(
                    f"[BATCH_BACKOFF] Task {task.id} rate limited | "
                    f"retry={retry_count}/{max_rate_limit_retries} | "
                    f"delay={delay:.1f}s | provider={provider_key}"
                )
                
                if retry_count >= max_rate_limit_retries:
                    # Store final stats before raising
                    rate_limit_stats["retries"] = retry_count
                    rate_limit_stats["total_wait"] = total_wait_time
                    rate_limit_stats["exhausted"] = True
                    task.metadata["_rate_limit_stats"] = rate_limit_stats
                    
                    # Raise with a cleaner error message
                    raise Exception(
                        f"Rate limit: waited {total_wait_time:.0f}s over {retry_count} retries"
                    )
                
                await asyncio.sleep(delay)
            else:
                # Re-raise non-rate-limit errors
                raise
    
    # Should not reach here, but just in case
    rate_limit_stats["retries"] = retry_count
    rate_limit_stats["total_wait"] = total_wait_time
    task.metadata["_rate_limit_stats"] = rate_limit_stats
    raise Exception(f"Rate limit: waited {total_wait_time:.0f}s over {retry_count} retries")


async def _get_or_create_subordinate(
    tool,
    profile: Optional[str] = None,
    task_index: int = 0
) -> Agent:
    """Get or create a subordinate agent with numbered name."""
    # Initialize agent config in thread pool for true parallel spawning
    config = await asyncio.to_thread(initialize_agent)
    
    # Set profile if provided — validate it exists first (MSR-2)
    if profile:
        from python.helpers import files
        profile_dir = files.get_abs_path("agents", profile)
        if not os.path.isdir(profile_dir):
            valid_profiles = sorted([
                d for d in os.listdir(files.get_abs_path("agents"))
                if os.path.isdir(files.get_abs_path("agents", d))
                and not d.startswith(".") and d != "_example"
            ])
            raise ValueError(
                f"INVALID PROFILE: '{profile}' does not exist. "
                f"Valid profiles: {', '.join(valid_profiles)}"
            )
        # ── SWARM BOUNDARY ENFORCEMENT ──
        from python.helpers.swarm_registry import is_profile_allowed, get_allowed_profiles
        current_profile = getattr(tool.agent.config, "profile", "default")
        if not is_profile_allowed(current_profile, profile):
            allowed = get_allowed_profiles(current_profile) or set()
            raise ValueError(
                f"⛔ SWARM BOUNDARY VIOLATION: Profile '{current_profile}' cannot "
                f"delegate to '{profile}'. "
                f"Allowed profiles: {', '.join(sorted(allowed))}."
            )
        config.profile = profile
    
    # Create new subordinate agent
    # Use incrementing numbers based on parent agent
    base_number = tool.agent.number + 1
    pool = tool.agent.get_data("_batch_agent_pool") or []
    agent_number = base_number + len(pool)
    
    subordinate = Agent(agent_number, config, tool.agent.context)
    
    # Set numbered name with UID for deterministic log correlation (RC-20)
    base_name = subordinate.agent_name or (profile or 'Sub').replace('-', ' ').title()
    subordinate.agent_name = f"{base_name} [{task_index}]"
    subordinate.display_name = f"{base_name} #{agent_number} ({subordinate.session_uid[:8]})"
    
    # Register relationships
    subordinate.set_data(Agent.DATA_NAME_SUPERIOR, tool.agent)

    # FIX-024: Propagate phase cap to subordinate
    phase_cap = tool.agent.data.get("_phase_cap")
    if phase_cap is not None:
        subordinate.data["_phase_cap"] = phase_cap

    # FIX-024: Seed build loop detector from parent's propagated state
    try:
        propagated = tool.agent.data.get("_build_failure_propagated")
        if propagated:
            from python.helpers.build_loop_detector import seed_build_loop_detector
            seed_build_loop_detector(subordinate, propagated)
    except Exception:
        pass  # Non-fatal

    # Add to pool for tracking
    pool.append(subordinate)
    tool.agent.set_data("_batch_agent_pool", pool)
    
    return subordinate


def _is_parent_healthy(tool) -> bool:
    """
    Check if the parent context is still healthy and active.
    """
    if not tool.agent or not tool.agent.context:
        return False
        
    current_time = datetime.now(timezone.utc).timestamp()
    heartbeat = getattr(tool.agent.context, 'last_heartbeat', None)
    
    if heartbeat is None:
        # If heartbeat not initialized, assume it's just started
        return True
        
    age = current_time - heartbeat
    if age > PARENT_HEARTBEAT_TIMEOUT:
        logger.warning(f"Parent context heartbeat stale: {age:.1f}s. Subordinate terminating.")
        return False
        
    return True
async def _monitor_orphans(tool, tasks: List[BatchTask]):
    """
    Periodically check for parent health and cancel tasks if orphaned.
    
    CRITICAL FIX (iter 62): If ANY task is still RUNNING, refresh the parent
    heartbeat. The parent agent is blocked on asyncio.gather waiting for us,
    so its own heartbeat loop can't run. Without this refresh, the monitor
    kills active subordinates after PARENT_HEARTBEAT_TIMEOUT.
    """
    try:
        while True:
            # Check if any tasks are still actively running
            has_running_tasks = any(
                t.status == TaskStatus.RUNNING for t in tasks
            )
            
            if has_running_tasks:
                # Subordinates are working — keep parent heartbeat alive
                # The parent IS active (waiting on us), so refresh its heartbeat
                if tool.agent and tool.agent.context:
                    tool.agent.context.last_heartbeat = datetime.now(timezone.utc).timestamp()
                    logger.debug(
                        f"[BATCH MONITOR] Refreshed parent heartbeat — "
                        f"{sum(1 for t in tasks if t.status == TaskStatus.RUNNING)} tasks still running"
                    )
            elif not _is_parent_healthy(tool):
                # No running tasks AND heartbeat is stale → truly orphaned
                logger.error("Orphaned batch detected - cancelling all tasks")
                for task in tasks:
                    if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                        task.status = TaskStatus.CANCELLED
                        task.error = "Cancelled due to parent context inactivity (heartbeat timeout)"
                break
            
            await asyncio.sleep(60)  # Check every minute
    except asyncio.CancelledError:
        logger.debug("[BatchMonitor] Orphan monitor cancelled — shutting down gracefully")


def _load_swarm_instructions(tool) -> str | None:
    """
    Load swarm instructions using the shared loader from call_subordinate.
    Delegates to the cached implementation to avoid code duplication.
    """
    try:
        from python.tools.call_subordinate import Delegation
        # Create a temporary Delegation instance to use its method
        # This shares the same cache (_swarm_instructions_cache)
        temp = Delegation.__new__(Delegation)
        temp.agent = tool.agent
        temp.args = {}
        return temp._load_swarm_instructions()
    except Exception as e:
        logger.debug(f"Failed to load swarm instructions via shared loader: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Wire 7: Budget-Based Wave Planning
# ═══════════════════════════════════════════════════════════════════════════

# Imports for budget-based planning — used by plan_with_budget() below.
from python.helpers.budget_cost_model import (
    estimate_feature_cost,
    plan_delegation_waves,
    FeatureCostEstimate,
)


def plan_with_budget(
    requirements: list[dict],
    dep_graph: dict | None,
    budget_iterations: int,
) -> list[list[FeatureCostEstimate]] | None:
    """Plan delegation waves using budget-based cost estimation.

    This is the integration point between the budget cost model
    (budget_cost_model.py) and the batch execution engine. When a valid
    dependency graph is available, it replaces the static hard-cap
    (MAX_PARALLEL_SUBORDINATES) with cost-aware wave planning.

    Args:
        requirements: List of requirement dicts with id, text, category.
        dep_graph: Structured dependency graph from the architect.
                   If None or empty, returns None (fall back to hard-cap).
        budget_iterations: Maximum iteration budget per wave.

    Returns:
        List of waves (each wave is a list of FeatureCostEstimate),
        or None if budget planning cannot be applied (caller should
        fall back to existing hard-cap behavior).
    """
    # ── Guard: no dep graph → fall back to hard-cap ──
    if not dep_graph or not isinstance(dep_graph, dict):
        return None

    if "modules" not in dep_graph or not dep_graph["modules"]:
        return None

    # ── Guard: no requirements → nothing to plan ──
    if not requirements:
        return None

    # ── Budget planning with error resilience ──
    try:
        waves = plan_delegation_waves(requirements, dep_graph, budget_iterations)
        return waves if waves else None
    except Exception as e:
        logger.warning(
            f"[BATCH] Budget-based wave planning failed (falling back to hard-cap): {e}"
        )
        return None
