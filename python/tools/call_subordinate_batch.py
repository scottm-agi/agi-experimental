from __future__ import annotations
"""
Batch Subordinate Delegation Tool for Parallel Swarm Execution

This tool enables parallel delegation of multiple tasks to subordinate agents,
integrating with the orchestrator for wave-based execution and shared memory
for cross-agent communication.

Usage:
    {
        "tool_name": "call_subordinate_batch",
        "tool_args": {
            "tasks": [
                {"message": "Research topic A", "profile": "researcher"},
                {"message": "Analyze data B", "profile": "analyst"},
                {"message": "Review code C", "profile": "developer"}
            ],
            "execution_mode": "parallel",  // "parallel", "wave", "sequential"
            "max_concurrent": 5,
            "timeout": 600,
            "aggregate_results": true
        }
    }
"""

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.agent import Agent, AgentContext, UserMessage
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle
from python.helpers.agent_tracer import AgentTracer
from python.helpers.rate_limiter import RateLimiter, coordinate_agent_wait
from python.initialize import initialize_agent
import logging

# ── Domain module imports (P1.2 modularization) ──
from python.helpers.batch_types import (
    BatchExecutionMode, TaskStatus, BatchTask, BatchResult,
    PARENT_HEARTBEAT_TIMEOUT, _MIN_BATCH_TIMEOUT, _DEFAULT_BATCH_TIMEOUT,
    _MAX_PARALLEL_SUBORDINATES, _SEQUENTIAL_THREAD_THRESHOLD,
    TASK_STALL_THRESHOLD, BATCH_ABSOLUTE_TIMEOUT, _BATCH_TIMEOUT_MULTIPLIER,
    _DEFAULT_MAX_TASK_RETRIES,
    _is_non_retriable, compute_batch_timeout, get_effective_timeout,
    detect_stalled_tasks, get_batch_task_timeout, estimate_task_timeout,
    estimate_task_timeout_with_tier, build_budget_message,
    enforce_lifecycle_ordering,
)
from python.helpers import batch_execution as _bexec

logger = logging.getLogger("agix.batch_subordinate")

# Lazy import to avoid circular dependency
def _get_boomerang_context(agent, calling_agent_name: str = "", all_tasks_succeeded: bool = True):
    from python.helpers.boomerang_context import get_boomerang_context
    return get_boomerang_context(agent, calling_agent_name=calling_agent_name, all_tasks_succeeded=all_tasks_succeeded)


class BatchDelegation(Tool):
    """
    Tool for parallel batch delegation of tasks to subordinate agents.
    
    Supports multiple execution modes:
    - sequential: Execute tasks one at a time (safe fallback)
    - parallel: Execute all tasks concurrently (up to max_concurrent)
    - wave: Execute in dependency-ordered waves
    - adaptive: Auto-select mode based on task analysis
    
    Execution methods are delegated to batch_execution.py standalone functions.
    Types and constants are imported from batch_types.py.
    """
    
    # Pool of subordinate agents for reuse
    DATA_NAME_AGENT_POOL = "_batch_agent_pool"
    DATA_NAME_BATCH_HISTORY = "_batch_history"
    
    async def execute(
        self,
        tasks: Optional[List[Dict[str, Any]]] = None,
        execution_mode: str = "parallel",
        max_concurrent: int = 5,
        timeout: Optional[float] = None,
        aggregate_results: bool = True,
        **kwargs
    ) -> Response:
        """
        Execute batch delegation of tasks to subordinate agents.
        
        Args:
            tasks: List of task definitions, each with:
                - message: The task message/instruction
                - profile: Optional agent profile to use
                - priority: Task priority (higher = first)
                - dependencies: List of task IDs this depends on
                - timeout: Per-task timeout override
            execution_mode: How to execute tasks (parallel, wave, sequential, adaptive)
            max_concurrent: Maximum concurrent agents
            timeout: Default timeout per task in seconds (default from AGIX_BATCH_TASK_TIMEOUT env or 600)
            aggregate_results: Whether to synthesize results into summary
        
        Returns:
            Response with batch execution results
        """
        # ── RECURSION GUARD: Terminal profiles must NOT re-delegate ──
        # Matches the guard in call_subordinate.py. Browser-profile agents must
        # use their own tools directly, not spawn subordinate chains.
        TERMINAL_PROFILES = {"browser"}
        current_profile = getattr(self.agent.config, "profile", "default")
        if current_profile in TERMINAL_PROFILES:
            return Response(
                message=f"ERROR: You are a '{current_profile}' agent. You must NOT delegate to subordinates. "
                        f"Use your own tools directly (e.g., 'browser_agent' tool for web browsing). "
                        f"Do NOT call call_subordinate_batch.",
                break_loop=False
            )

        # ── CIRCUIT BREAKER: Detect delegation loops ──
        # Walk up the agent hierarchy and check depth. If too deep or looping,
        # break the chain and force direct execution.
        MAX_DELEGATION_DEPTH = 3
        profile_chain = []
        walker = self.agent
        while walker is not None:
            walker_profile = getattr(walker.config, "profile", "default") or "default"
            profile_chain.append(walker_profile)
            walker = walker.get_data(Agent.DATA_NAME_SUPERIOR)
        
        # Lowered from 5→4 to save ~30s of wasted LLM calls hitting depth limits
        if len(profile_chain) >= 4:
            chain_str = " → ".join(reversed(profile_chain))
            logger.warning(f"CIRCUIT BREAKER (batch): max delegation depth reached ({len(profile_chain)}): {chain_str}")
            return Response(
                message=f"⚠️ MAX DELEGATION DEPTH ({len(profile_chain)}) REACHED. "
                        f"Chain: {chain_str}. Handle tasks directly with your own tools.",
                break_loop=False
            )
        
        # Check if any requested profile appears too many times in the chain
        if tasks and isinstance(tasks, list):
            for task_def in tasks:
                requested_profile = task_def.get("profile", "")
                if requested_profile:
                    profile_count = profile_chain.count(requested_profile)
                    if profile_count >= MAX_DELEGATION_DEPTH:
                        chain_str = " → ".join(reversed(profile_chain))
                        logger.warning(
                            f"CIRCUIT BREAKER (batch): delegation loop detected! "
                            f"Profile '{requested_profile}' already appears {profile_count}x in chain: {chain_str}"
                        )
                        return Response(
                            message=f"⚠️ DELEGATION LOOP DETECTED: Profile '{requested_profile}' appears "
                                    f"{profile_count} times in chain: {chain_str}. "
                                    f"Handle tasks directly with your own tools.",
                            break_loop=False
                        )
        
        # Validate inputs
        if not tasks or not isinstance(tasks, list):
            return Response(
                message="Error: 'tasks' parameter must be a non-empty list of task definitions",
                break_loop=False
            )
        
        # Parse execution mode
        try:
            mode = BatchExecutionMode(execution_mode.lower())
        except ValueError:
            mode = BatchExecutionMode.PARALLEL
        
        # ── HARD CAP: prevent thread/resource exhaustion ──
        # Each subordinate spawns MCP servers, DB connections, and threads.
        # 5+ parallel subordinates caused deadlock at 117/150 threads (iter 25).
        if max_concurrent > _MAX_PARALLEL_SUBORDINATES:
            logger.warning(
                f"[BATCH] Capping max_concurrent from {max_concurrent} to "
                f"{_MAX_PARALLEL_SUBORDINATES} (hard ceiling for resource safety)"
            )
            max_concurrent = _MAX_PARALLEL_SUBORDINATES
        
        # ── THREAD SAFETY: force sequential if thread count is high ──
        import threading
        current_threads = threading.active_count()
        if current_threads > _SEQUENTIAL_THREAD_THRESHOLD:
            logger.warning(
                f"[BATCH] Thread count {current_threads} > {_SEQUENTIAL_THREAD_THRESHOLD}, "
                f"forcing SEQUENTIAL mode to prevent deadlock"
            )
            mode = BatchExecutionMode.SEQUENTIAL
        
        # Resolve timeout: use env-configured default if not explicitly provided
        if timeout is None:
            timeout = get_batch_task_timeout()
        
        # Create batch tasks
        batch_id = str(uuid.uuid4())[:8]
        batch_tasks = self._create_batch_tasks(tasks, timeout)
        
        # ── LIFECYCLE ORDERING (Forgejo #371) ──
        # Reorder tasks so verify/test always runs before publish/deploy.
        # Only applied for sequential/wave modes where order matters.
        if mode in (BatchExecutionMode.SEQUENTIAL, BatchExecutionMode.WAVE):
            task_dicts = [{"id": t.id, "message": t.message, "profile": t.profile or ""} for t in batch_tasks]
            ordered_dicts = enforce_lifecycle_ordering(task_dicts)
            if ordered_dicts:
                id_order = [d["id"] for d in ordered_dicts]
                task_map = {t.id: t for t in batch_tasks}
                reordered = [task_map[tid] for tid in id_order if tid in task_map]
                if len(reordered) == len(batch_tasks):
                    batch_tasks = reordered
                    logger.info(f"[BATCH] Lifecycle ordering applied: {[t.id for t in batch_tasks]}")
        
        # ── RCA-232 Fix 4: Bridge secrets → .env.local before delegation ──
        try:
            from python.helpers.pre_delegation_env_bridge import ensure_env_before_delegation
            from python.helpers import projects as proj_helper
            project_name = proj_helper.get_context_project_name(self.agent.context)
            if project_name:
                project_dir = proj_helper.get_project_folder(project_name)
                bridged = ensure_env_before_delegation(project_dir, project_name)
                if bridged:
                    logger.info(f"[BATCH] Pre-delegation env bridge: .env.local updated for {project_name}")

                    # ── U-1 (ITR-29): Pre-delegation API key health check ──
                    if bridged.written_keys:
                        try:
                            from python.helpers.pre_delegation_env_bridge import validate_api_keys
                            env_path = os.path.join(project_dir, ".env.local")
                            env_vars = {}
                            if os.path.isfile(env_path):
                                with open(env_path, "r") as f:
                                    for line in f:
                                        line = line.strip()
                                        if "=" in line and not line.startswith("#"):
                                            k, _, v = line.partition("=")
                                            k = k.strip()
                                            if k in bridged.written_keys:
                                                env_vars[k] = v.strip()
                            if env_vars:
                                health = validate_api_keys(env_vars)
                                invalid_keys = [
                                    k for k, v in health.items()
                                    if v.get("valid") is False
                                ]
                                if invalid_keys:
                                    bridged.invalid_keys = invalid_keys
                                    logger.warning(
                                        f"[BATCH ENV BRIDGE U-1] Invalid API keys for "
                                        f"{project_name}: {invalid_keys}"
                                    )
                        except Exception as health_err:
                            logger.debug(
                                f"[BATCH ENV BRIDGE U-1] Health check failed (non-fatal): {health_err}"
                            )
        except Exception as e:
            logger.debug(f"[BATCH] Pre-delegation env bridge skipped: {e}")

        
        # Log batch start
        self._log_batch_start(batch_id, batch_tasks, mode)
        
        # Execute based on mode
        batch_result = BatchResult(
            batch_id=batch_id,
            total_tasks=len(batch_tasks),
            completed=0,
            failed=0,
            timeout=0,
            cancelled=0,
            tasks=batch_tasks,
            start_time=time.time()
        )
        
        try:
            # Start background orphan monitor
            monitor_task = asyncio.create_task(_bexec._monitor_orphans(self, batch_tasks))
            
            try:
                if mode == BatchExecutionMode.SEQUENTIAL:
                    await _bexec._execute_sequential(self, batch_tasks, batch_result)
                elif mode == BatchExecutionMode.WAVE:
                    await _bexec._execute_wave(self, batch_tasks, batch_result, max_concurrent)
                elif mode == BatchExecutionMode.ADAPTIVE:
                    # Analyze tasks and choose best mode
                    if self._has_dependencies(batch_tasks):
                        await _bexec._execute_wave(self, batch_tasks, batch_result, max_concurrent)
                    elif len(batch_tasks) <= 2:
                        await _bexec._execute_sequential(self, batch_tasks, batch_result)
                    else:
                        await _bexec._execute_parallel(self, batch_tasks, batch_result, max_concurrent)
                else:  # PARALLEL (default)
                    await _bexec._execute_parallel(self, batch_tasks, batch_result, max_concurrent)
            finally:
                # Always kill the monitor when done
                monitor_task.cancel()
            
            batch_result.end_time = time.time()
            
            # ── Iteration 23 (Tier 1): Auto-retry failed/timed-out tasks ──
            # Before counting results, attempt to recover retriable failures
            has_failures = any(
                t.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED)
                for t in batch_tasks
            )
            recovered = 0
            if has_failures:
                recovered = await _bexec._retry_failed_tasks(self, batch_tasks)
                if recovered > 0:
                    batch_result.end_time = time.time()  # Update after retry
            
            # Aggregate results if requested
            if aggregate_results and batch_result.completed > 0:
                batch_result.aggregated_result = await self._aggregate_results(batch_result)
            
            # Update counts (AFTER retry so recovered tasks are counted correctly)
            batch_result.completed = sum(1 for t in batch_tasks if t.status == TaskStatus.COMPLETED)
            batch_result.failed = sum(1 for t in batch_tasks if t.status == TaskStatus.FAILED)
            batch_result.timeout = sum(1 for t in batch_tasks if t.status == TaskStatus.TIMEOUT)
            batch_result.cancelled = sum(1 for t in batch_tasks if t.status == TaskStatus.CANCELLED)
            
            # Store batch history
            self._store_batch_history(batch_result)
            
            # ── N-ATTEMPT FAILURE TRACKER for batch tasks ──
            # Mirror the pattern from call_subordinate.py lines 812-857.
            # For each failed/timeout/cancelled task, record the failure in the
            # global delegation loop detector so the supervisor's
            # REPEATED_TASK_FAILURE redirect can fire for batch delegations.
            # Root cause fix: Iteration 155 — 5hr death spiral because batch
            # failures were never recorded in the delegation loop detector.
            try:
                from python.extensions.tool_execute_before._27_delegation_loop_hook import _global_detector
                agent_id = getattr(self.agent, "agent_name", "") or str(id(self.agent))
                
                for task in batch_tasks:
                    if task.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
                        errors = [str(task.error)] if task.error else [f"Status: {task.status.value}"]
                        redirect_diag = _global_detector.record_failure(
                            agent_id, task.message, errors=errors
                        )
                        
                        if redirect_diag:
                            # Threshold crossed — emit supervisor signal
                            task_hash = _global_detector.get_task_hash(task.message)
                            failure_count = _global_detector.get_failure_count(agent_id, task.message)
                            all_errors = []
                            for detail in _global_detector.get_failure_details(agent_id, task.message):
                                all_errors.extend(detail.get("errors", []))
                            
                            try:
                                from python.helpers.event_bus import emit_repeated_task_failure
                                context_id = (
                                    getattr(self.agent.context, "id", "unknown")
                                    if self.agent.context else "unknown"
                                )
                                iteration = (
                                    getattr(self.agent.loop_data, "iteration", 0)
                                    if hasattr(self.agent, "loop_data") and self.agent.loop_data
                                    else 0
                                )
                                asyncio.ensure_future(emit_repeated_task_failure(
                                    agent_id=agent_id,
                                    context_id=context_id,
                                    task_hash=task_hash,
                                    failure_count=failure_count,
                                    error_summary=all_errors,
                                    task_preview=task.message[:200],
                                    iteration=iteration,
                                ))
                                logger.warning(
                                    f"[BATCH] N-ATTEMPT TRACKER: Task hash={task_hash} "
                                    f"failed {failure_count}x — REPEATED_TASK_FAILURE "
                                    f"signal emitted for supervisor redirect"
                                )
                            except Exception as sig_err:
                                logger.warning(
                                    f"[BATCH] Failed to emit REPEATED_TASK_FAILURE signal: {sig_err}"
                                )
                            
                            await self.agent.hist_add_warning(redirect_diag)
            except Exception as e:
                logger.warning(f"[BATCH] N-attempt failure tracking failed (non-fatal): {e}")
            
            # ── WriteLedger: Post-batch file integrity verification ──
            ledger_report = ""
            try:
                from python.helpers.write_ledger import WriteLedger
                from python.helpers import projects as proj_helper
                project_name = proj_helper.get_context_project_name(self.agent.context)
                if project_name:
                    project_dir = proj_helper.get_project_folder(project_name)
                    ledger = WriteLedger()
                    verification = ledger.verify_all(project_dir)
                    missing_count = len(verification["missing"])
                    corrupted_count = len(verification.get("corrupted", []))
                    total_tracked = (
                        len(verification["present"])
                        + missing_count
                        + corrupted_count
                    )
                    if total_tracked > 0:
                        ledger_report = (
                            f"\n\n## 📋 Write Ledger Verification\n"
                            f"Tracked files: {total_tracked} | "
                            f"Present: {len(verification['present'])} | "
                            f"Missing: {missing_count} | "
                            f"Corrupted: {corrupted_count}\n"
                        )
                        if missing_count > 0:
                            ledger_report += "\n**⚠️ MISSING FILES (lost during batch execution):**\n"
                            for entry in verification["missing"]:
                                ledger_report += f"- `{entry['path']}` (written by agent {entry.get('agent_id', '?')})\n"
                            ledger_report += (
                                "\n**ACTION REQUIRED**: These files were written by subordinates "
                                "but no longer exist on disk. Re-delegate their creation.\n"
                            )
                        if corrupted_count > 0:
                            ledger_report += "\n**⚠️ CORRUPTED FILES (content changed after write):**\n"
                            for entry in verification["corrupted"]:
                                ledger_report += f"- `{entry['path']}` (written by agent {entry.get('agent_id', '?')})\n"

                        # ── G-6: Multi-writer conflict detection ──
                        conflicts = ledger.detect_multi_writer_conflicts(project_dir)
                        if conflicts:
                            ledger_report += f"\n**⚠️ MULTI-WRITER CONFLICTS ({len(conflicts)} files):**\n"
                            for c in conflicts:
                                ledger_report += (
                                    f"- `{c['path']}` — written by agents: "
                                    f"{', '.join(c['agents'])}\n"
                                )
            except Exception as e:
                logger.debug(f"[BATCH] WriteLedger verification skipped: {e}")
            
            # Format response
            response_message = self._format_batch_response(batch_result)
            if ledger_report:
                response_message += ledger_report
            
            # ALWAYS break_loop=False: the parent orchestrator must continue
            # its monologue loop to decide what to do next (delegate more,
            # call response, etc.). SubordinateContinuation was removed in
            # the delegation architecture hardening (2026-04-18).
            return Response(
                message=response_message,
                break_loop=False,
                additional={"batch_result": batch_result.to_dict()}
            )
            
        except Exception as e:
            batch_result.end_time = time.time()
            error_msg = f"Batch execution failed: {str(e)}"
            PrintStyle(font_color="red", padding=True).print(error_msg)
            return Response(
                message=error_msg,
                break_loop=False,
                additional={"error": str(e), "batch_result": batch_result.to_dict()}
            )

    def _create_batch_tasks(
        self,
        task_defs: List[Dict[str, Any]],
        default_timeout: float
    ) -> List[BatchTask]:
        """Create BatchTask objects from task definitions."""
        tasks = []

        # ── RCA-456: Pre-compute file responsibility partitions ──────────
        # When multiple write-capable agents run in the same batch, inject
        # explicit "YOUR files" vs "DO NOT TOUCH" sections to prevent
        # multi-writer conflicts. Without this, agents can clobber each
        # other's files (e.g., scaffold.test.ts written by agents 2 and 5).
        file_responsibilities = self._extract_file_responsibilities(task_defs)

        for i, task_def in enumerate(task_defs):
            task_id = task_def.get("id", f"task_{i}")
            message = task_def.get("message", "")
            
            # ── RCA-232 Fix 5: Inject acceptance criteria from requirement IDs ──
            req_ids = task_def.get("requirement_ids", [])
            if req_ids:
                try:
                    from python.helpers.acceptance_criteria_injector import inject_acceptance_criteria
                    message = inject_acceptance_criteria(
                        message, req_ids, self.agent.data
                    )
                except Exception:
                    pass  # Graceful degradation — don't block task creation

            # ── RCA-456: Inject file responsibility boundaries ──
            if file_responsibilities and task_id in file_responsibilities:
                resp = file_responsibilities[task_id]
                if resp.get("other_files"):
                    boundary = "\n\n### 🔒 FILE RESPONSIBILITY BOUNDARIES\n"
                    if resp.get("own_files"):
                        boundary += "**YOUR files** (you MAY create/modify these):\n"
                        for f in resp["own_files"][:20]:
                            boundary += f"- `{f}`\n"
                    boundary += "\n**⛔ DO NOT TOUCH** (owned by another agent in this batch):\n"
                    for f in resp["other_files"][:20]:
                        boundary += f"- `{f}`\n"
                    boundary += (
                        "\nIf you need to modify a file owned by another agent, "
                        "document the dependency in your response instead of editing it directly.\n"
                    )
                    message += boundary

            tasks.append(BatchTask(
                id=task_id,
                message=message,
                profile=task_def.get("profile"),
                priority=task_def.get("priority", 0),
                dependencies=task_def.get("dependencies", []),
                # RCA-264: If no explicit timeout, use adaptive estimation
                # based on task message keywords instead of flat default.
                timeout=task_def.get("timeout") or estimate_task_timeout(message),
                metadata=task_def.get("metadata", {})
            ))
        
        # Sort by priority (higher first)
        tasks.sort(key=lambda t: t.priority, reverse=True)
        return tasks

    def _extract_file_responsibilities(
        self, task_defs: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, List[str]]]:
        """RCA-456: Extract file references from task messages and partition ownership.

        Scans each task's message for file path patterns (e.g., src/app/page.tsx,
        tests/scaffold.test.ts) and assigns ownership to the task that mentions
        them. When multiple tasks mention the same file, the FIRST task gets
        ownership (earlier tasks have priority).

        Only applies to write-capable profiles (code, hacker).

        Returns:
            Dict mapping task_id -> {own_files: [...], other_files: [...]}
        """
        import re

        WRITE_PROFILES = {"code", "hacker"}

        # Step 1: Extract file references per task
        file_pattern = re.compile(
            r'(?:src/|tests/|prisma/|docs/|public/|app/|components/|lib/|utils/|api/)'
            r'[\w/.@-]+\.(?:tsx?|jsx?|css|json|prisma|md|yaml|yml)',
            re.IGNORECASE
        )

        task_files: Dict[str, List[str]] = {}
        all_files: Dict[str, str] = {}  # file -> first task_id that mentions it

        for i, task_def in enumerate(task_defs):
            task_id = task_def.get("id", f"task_{i}")
            profile = task_def.get("profile", "code")
            if profile not in WRITE_PROFILES:
                continue

            message = task_def.get("message", "")
            found_files = list(set(file_pattern.findall(message)))
            task_files[task_id] = found_files

            for f in found_files:
                if f not in all_files:
                    all_files[f] = task_id

        # Step 2: If only 0-1 write-capable tasks, no partitioning needed
        if len(task_files) <= 1:
            return {}

        # Step 3: Build responsibility map
        result: Dict[str, Dict[str, List[str]]] = {}
        for task_id, files in task_files.items():
            own = [f for f in files if all_files.get(f) == task_id]
            others = []
            for other_id, other_files in task_files.items():
                if other_id != task_id:
                    for f in other_files:
                        if f not in own and f not in others:
                            others.append(f)

            result[task_id] = {
                "own_files": sorted(own),
                "other_files": sorted(others),
            }

        return result
    
    def _has_dependencies(self, tasks: List[BatchTask]) -> bool:
        """Check if any tasks have dependencies."""
        return any(t.dependencies for t in tasks)
    

    async def _aggregate_results(self, batch_result: BatchResult) -> str:
        """Aggregate results from completed tasks into a comprehensive synthesis."""
        completed_tasks = [t for t in batch_result.tasks if t.status == TaskStatus.COMPLETED]
        
        if not completed_tasks:
            return "No tasks completed successfully."
        
        # Build aggregation prompt with full results
        results_text = "\n\n---\n\n".join([
            f"## Agent: {t.profile or 'default'} — {t.message[:150]}\n\n{t.result}"
            for t in completed_tasks
        ])
        
        aggregation_prompt = f"""You have received outputs from multiple specialist agents working in parallel.
Your job is to produce a SINGLE, COMPREHENSIVE, executive-ready document that:

1. Starts with an EXECUTIVE SUMMARY (1 page) synthesizing ALL agent findings
2. Includes FULL DETAILED SECTIONS from each agent's output — DO NOT summarize or truncate their work. Include every detail, table, framework, email template, and data point they produced.
3. Cross-references insights across sections (e.g., research findings inform the account strategy, competitive analysis shapes the campaign positioning)
4. Ends with a unified RECOMMENDATIONS & NEXT STEPS section with a week-by-week action plan

IMPORTANT: This must be a COMPLETE document. Include ALL content from each agent. Do NOT use placeholders like [Content unavailable] or [See above]. Inline everything.

Here are the agent outputs:

{results_text}
"""
        
        try:
            # Use utility model for aggregation
            summary = await self.agent.call_utility_model(
                system="You are a world-class executive strategist. Produce comprehensive, deeply detailed synthesis documents. Never truncate, summarize away, or use placeholders. Include ALL content from each source.",
                message=aggregation_prompt,
                background=True
            )
            return summary
        except Exception as e:
            # Fallback to simple concatenation with headers
            return f"# Unified Results from {len(completed_tasks)} Agents\n\n" + results_text
    

    def _store_batch_history(self, batch_result: BatchResult) -> None:
        """Store batch result in agent's history for reference."""
        history = self.agent.get_data(self.DATA_NAME_BATCH_HISTORY) or []
        history.append({
            "batch_id": batch_result.batch_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_tasks": batch_result.total_tasks,
            "completed": batch_result.completed,
            "failed": batch_result.failed,
            "duration": batch_result.duration,
            "success_rate": batch_result.success_rate,
        })
        # Keep last 100 batch results
        if len(history) > 100:
            history = history[-100:]
        self.agent.set_data(self.DATA_NAME_BATCH_HISTORY, history)

    def _format_batch_response(self, batch_result: BatchResult) -> str:
        """Format batch result as readable response."""
        lines = [
            f"## Batch Execution Complete (ID: {batch_result.batch_id})",
            "",
            f"**Summary:**",
            f"- Total Tasks: {batch_result.total_tasks}",
            f"- Completed: {batch_result.completed}",
            f"- Failed: {batch_result.failed}",
            f"- Timeout: {batch_result.timeout}",
            f"- Success Rate: {batch_result.success_rate:.1%}",
            f"- Duration: {batch_result.duration:.2f}s" if batch_result.duration else "",
            "",
        ]
        
        # Add rate limit summary if any tasks had rate limiting
        rate_limit_summary = self._get_rate_limit_summary(batch_result)
        if rate_limit_summary:
            lines.extend([
                "**Rate Limit Summary:**",
                rate_limit_summary,
                "",
            ])
        
        # Iteration 23: Add retry/recovered summary
        retried_tasks = [t for t in batch_result.tasks if t.metadata.get("_retry_attempt", 0) > 0]
        if retried_tasks:
            recovered_count = sum(1 for t in retried_tasks if t.status == TaskStatus.COMPLETED)
            lines.extend([
                "**Auto-Retry Summary:**",
                f"- Tasks retried: {len(retried_tasks)}",
                f"- Recovered: {recovered_count}",
                f"- Still failed: {len(retried_tasks) - recovered_count}",
                "",
            ])
        
        # Add aggregated result if available
        if batch_result.aggregated_result:
            lines.extend([
                "**Aggregated Results:**",
                batch_result.aggregated_result,
                "",
            ])
        
        # Add individual task details with FULL results
        lines.append("\n---\n")
        lines.append("**Individual Agent Outputs:**")
        for task in batch_result.tasks:
            status_icon = {
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
                TaskStatus.TIMEOUT: "⏱️",
                TaskStatus.CANCELLED: "🚫",
                TaskStatus.PENDING: "⏳",
                TaskStatus.RUNNING: "🔄",
            }.get(task.status, "❓")
            
            duration = f" ({task.end_time - task.start_time:.1f}s)" if task.end_time and task.start_time else ""
            profile_name = (task.profile or 'default').replace('-', ' ').title()
            
            # Add rate limit indicator if task had retries
            rate_stats = task.metadata.get("_rate_limit_stats", {})
            rate_indicator = ""
            if rate_stats.get("retries", 0) > 0:
                rate_indicator = f" 🔄{rate_stats['retries']}"
            
            lines.append(f"\n### {status_icon} {profile_name}{duration}{rate_indicator}")
            
            # Include FULL result for completed tasks — strip any child boomerangs
            if task.status == TaskStatus.COMPLETED and task.result:
                try:
                    from python.helpers.boomerang_context import strip_boomerang
                    lines.append(strip_boomerang(task.result))
                except Exception:
                    lines.append(task.result)
            elif task.error and "Rate limit" not in task.error:
                lines.append(f"Error: {task.error}")
        
        # Append boomerang context — reminds parent of original user's
        # completion requirements (markers, format, sign-off)
        # Only ONE boomerang at the very end of the batch response.
        # #1114: Pass actual success status to prevent redundant re-delegation
        try:
            all_succeeded = all(
                t.status == TaskStatus.COMPLETED
                for t in batch_result.tasks
            )
            boomerang = _get_boomerang_context(
                self.agent,
                calling_agent_name=getattr(self.agent, 'agent_name', ''),
                all_tasks_succeeded=all_succeeded
            )
            if boomerang:
                lines.append(boomerang)
        except Exception as e:
            logger.warning(f"Failed to append boomerang context: {e}")

        return "\n".join(lines)

    def _get_rate_limit_summary(self, batch_result: BatchResult) -> str:
        """Generate a summary of rate limiting across all tasks."""
        total_retries = 0
        total_wait = 0.0
        tasks_affected = 0
        exhausted_count = 0
        
        for task in batch_result.tasks:
            stats = task.metadata.get("_rate_limit_stats", {})
            if stats.get("retries", 0) > 0:
                tasks_affected += 1
                total_retries += stats.get("retries", 0)
                total_wait += stats.get("total_wait", 0.0)
                if stats.get("exhausted"):
                    exhausted_count += 1
        
        if tasks_affected == 0:
            return ""
        
        summary_parts = [
            f"{tasks_affected} task(s) encountered rate limits",
            f"Total retries: {total_retries}",
            f"Total wait time: {total_wait:.1f}s",
        ]
        
        if exhausted_count > 0:
            summary_parts.append(f"⚠️ {exhausted_count} task(s) exhausted retry limit")
        
        return " | ".join(summary_parts)

    def _log_batch_start(
        self,
        batch_id: str,
        tasks: List[BatchTask],
        mode: BatchExecutionMode
    ) -> None:
        """Log batch execution start."""
        PrintStyle(
            font_color="#1B4F72",
            background_color="white",
            padding=True,
            bold=True
        ).print(
            f"{self.agent.agent_name}: Starting batch delegation "
            f"(ID: {batch_id}, Tasks: {len(tasks)}, Mode: {mode.value})"
        )
        
        self.agent.context.log.log(
            type="tool",
            heading=f"icon://group {self.agent.agent_name}: Batch Delegation Started",
            content=f"Batch ID: {batch_id}\nTasks: {len(tasks)}\nMode: {mode.value}",
            kvps={"batch_id": batch_id, "task_count": len(tasks), "mode": mode.value}
        )
    
    def _log_task_complete(self, task: BatchTask) -> None:
        """Log individual task completion."""
        status_color = {
            TaskStatus.COMPLETED: "#27AE60",
            TaskStatus.FAILED: "#E74C3C",
            TaskStatus.TIMEOUT: "#F39C12",
        }.get(task.status, "#95A5A6")
        
        PrintStyle(font_color=status_color).print(
            f"  Task {task.id}: {task.status.value}"
            f" ({task.end_time - task.start_time:.1f}s)" if task.end_time and task.start_time else ""
        )
    
    def get_log_object(self):
        """Create log object for this tool execution."""
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://group {self.agent.agent_name}: Batch Subordinate Delegation",
            content="",
            kvps=self.args,
        )



# Alias for tool discovery
Delegation = BatchDelegation
