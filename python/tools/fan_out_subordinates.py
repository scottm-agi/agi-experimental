"""
Fan-Out Subordinates Tool (Issue #825)

Redis-backed parallel fan-out: spawns N subordinate agents concurrently,
coordinated through Redis Streams for task dispatch and result aggregation.
Scales to 50 agents by default (configurable via parallel_config.yaml).

Architecture (hybrid pattern per research):
- Redis Streams for durable task dispatch + result collection (fan-in)
- asyncio.gather for in-process concurrent execution of agent monologues
- asyncio.Semaphore for concurrency limiting
- Distributed rate limit coordination via existing RateLimiter

Use cases:
- Reading 50 git issues in parallel
- Scraping 10+ websites concurrently
- Fan-out research across multiple topics
- Per-space Google Chat crawling
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from python.agent import Agent, UserMessage
from python.helpers.tool import Tool, Response
from python.helpers.agent_tracer import AgentTracer
from python.helpers.rate_limiter import RateLimiter, coordinate_agent_wait
from python.initialize import initialize_agent

logger = logging.getLogger("agix.fan_out")

# Default max concurrent agents (configurable via parallel_config.yaml)
DEFAULT_MAX_CONCURRENT = 10

# Hard timeout ceiling for fan-out execution. Prevents infinite hangs when
# subordinate agents stall (e.g., stuck in paused state, dead LLM connection).
# See rca_asyncio_blocking_coe.md (Category A blocker B-2).
FAN_OUT_TIMEOUT = 600.0  # 10 minutes — same as batch timeout

# Try to import mode manager for MultiAgentDev support
try:
    from python.helpers.mode_manager import get_mode_manager
    from python.extensions.agent_init._60_mode_init import set_agent_mode
    MODE_SUPPORT = True
except ImportError:
    MODE_SUPPORT = False


def _get_max_concurrent_from_settings() -> int:
    """Load max_concurrent from parallel_settings if available."""
    try:
        from python.helpers.parallel_settings import get_parallel_settings
        settings = get_parallel_settings()
        return settings.parallel.max_concurrent_workers or DEFAULT_MAX_CONCURRENT
    except Exception:
        return DEFAULT_MAX_CONCURRENT


class FanOutSubordinates(Tool):
    """
    Launch multiple subordinate agents in parallel using Redis-coordinated
    asyncio.gather fan-out/fan-in pattern.

    Architecture:
    1. Enqueue tasks to Redis Stream (swarm:fanout:tasks:<job_id>)
    2. Launch asyncio workers that consume from stream
    3. Workers run Agent.monologue() concurrently
    4. Results written to Redis Stream (swarm:fanout:results:<job_id>)
    5. Aggregator collects all results and returns to parent

    Redis coordination provides:
    - Durable task queue with at-least-once delivery
    - Progress tracking via Redis hash
    - Dead-letter handling for failed tasks
    - Distributed rate limit coordination
    """

    async def execute(self, tasks=None, max_concurrent=None, **kwargs):
        """
        Execute parallel fan-out of subordinate agents.

        Args:
            tasks: List of task dicts, each with 'message' and optional 'mode'/'profile'
                   e.g. [{"message": "Read issue #1"}, {"message": "Read issue #2"}]
            max_concurrent: Max simultaneous agents (default from config, cap 50)
        """
        # Robust parsing: handle string JSON, single dict, list of strings
        if isinstance(tasks, str):
            try:
                tasks = json.loads(tasks)
            except (json.JSONDecodeError, TypeError):
                # Treat plain string as a single task
                tasks = [{"message": tasks}]

        if isinstance(tasks, dict):
            tasks = [tasks]

        if not tasks or not isinstance(tasks, list):
            return Response(
                message="Error: 'tasks' must be a non-empty list of task objects with 'message' field.",
                break_loop=False,
            )

        # Normalize: wrap plain strings in dict
        normalized = []
        for t in tasks:
            if isinstance(t, str):
                normalized.append({"message": t})
            elif isinstance(t, dict):
                normalized.append(t)
            else:
                normalized.append({"message": str(t)})
        tasks = normalized

        # Parse max_concurrent
        config_max = _get_max_concurrent_from_settings()
        if max_concurrent is None:
            max_concurrent = min(config_max, DEFAULT_MAX_CONCURRENT)
        else:
            max_concurrent = min(int(max_concurrent), config_max, DEFAULT_MAX_CONCURRENT)

        num_tasks = len(tasks)
        job_id = str(uuid.uuid4())[:8]

        # Log fan-out start
        self.agent.context.log.log(
            type="info",
            heading=f"icon://fork_right Fan-Out: {num_tasks} tasks (max {max_concurrent} concurrent)",
            content=f"Job {job_id}: launching parallel subordinates",
        )

        # Coordinate rate limiting
        profile_model = getattr(self.agent.config, "chat_model", None)
        provider = getattr(profile_model, "provider", "unknown") if profile_model else "unknown"
        model_name = getattr(profile_model, "name", "unknown") if profile_model else "unknown"
        provider_key = f"{provider}\\{model_name}"

        wait_time = await coordinate_agent_wait(provider, provider_key)
        if wait_time > 0:
            logger.info(f"Fan-out waited {wait_time:.1f}s for rate limit backoff")

        # Try Redis-backed coordination, fallback to pure asyncio
        redis_client = await self._get_redis_client()
        if redis_client:
            all_results = await self._redis_fan_out(
                tasks, max_concurrent, job_id, provider_key, redis_client
            )
        else:
            logger.info("Redis unavailable, using pure asyncio fan-out")
            all_results = await self._asyncio_fan_out(
                tasks, max_concurrent, provider_key
            )

        # ── N-ATTEMPT FAILURE TRACKER for fan-out tasks ──
        # Mirror the pattern from call_subordinate.py lines 812-857.
        # For each failed result (starts with "Error:"), record the failure
        # in the delegation loop detector so the supervisor's
        # REPEATED_TASK_FAILURE redirect can fire for fan-out delegations.
        # Root cause fix: Same gap as call_subordinate_batch — fan-out
        # failures were never recorded in the delegation loop detector.
        try:
            from python.extensions.tool_execute_before._27_delegation_loop_hook import _global_detector
            agent_id = getattr(self.agent, "agent_name", "") or str(id(self.agent))

            for i, result in enumerate(all_results):
                if result and result.startswith("Error:"):
                    task_spec = tasks[i] if i < len(tasks) else {}
                    msg = (
                        task_spec.get("message", str(task_spec))
                        if isinstance(task_spec, dict) else str(task_spec)
                    )
                    errors = [result[:500]]

                    redirect_diag = _global_detector.record_failure(
                        agent_id, msg, errors=errors
                    )
                    if redirect_diag:
                        task_hash = _global_detector.get_task_hash(msg)
                        failure_count = _global_detector.get_failure_count(agent_id, msg)
                        all_errors = []
                        for detail in _global_detector.get_failure_details(agent_id, msg):
                            all_errors.extend(detail.get("errors", []))

                        try:
                            from python.helpers.event_bus import emit_repeated_task_failure
                            context_id = (
                                getattr(self.agent.context, "id", "unknown")
                                if self.agent.context else "unknown"
                            )
                            asyncio.ensure_future(emit_repeated_task_failure(
                                agent_id=agent_id,
                                context_id=context_id,
                                task_hash=task_hash,
                                failure_count=failure_count,
                                error_summary=all_errors,
                                task_preview=msg[:200],
                                iteration=0,
                            ))
                            logger.warning(
                                f"[FAN-OUT] N-ATTEMPT TRACKER: Task hash={task_hash} "
                                f"failed {failure_count}x — REPEATED_TASK_FAILURE "
                                f"signal emitted for supervisor redirect"
                            )
                        except Exception as sig_err:
                            logger.warning(
                                f"[FAN-OUT] Failed to emit REPEATED_TASK_FAILURE: {sig_err}"
                            )

                        await self.agent.hist_add_warning(redirect_diag)
        except Exception as e:
            logger.warning(f"[FAN-OUT] N-attempt failure tracking failed (non-fatal): {e}")

        # Format combined results
        output_parts = []
        for i, result in enumerate(all_results):
            task_spec = tasks[i] if i < len(tasks) else {}
            msg = task_spec.get("message", str(task_spec)) if isinstance(task_spec, dict) else str(task_spec)
            task_label = msg[:80] + "..." if len(msg) > 80 else msg
            output_parts.append(f"### Task {i + 1}: {task_label}\n{result}\n")

        combined = "\n---\n".join(output_parts)
        elapsed = time.time() - (self._start_time or time.time())
        summary = (
            f"✅ Completed {num_tasks} parallel tasks "
            f"({max_concurrent} concurrent max) in {elapsed:.1f}s\n\n{combined}"
        )

        # ALWAYS break_loop=False: parent must continue its monologue loop
        # to decide next action. SubordinateContinuation was removed (2026-04-18).
        return Response(message=summary, break_loop=False)

    async def _get_redis_client(self):
        """Try to get a Redis client, return None if unavailable."""
        try:
            from python.redis_client import RedisClient
            client = RedisClient.get_instance()
            if await client.connect():
                return client
        except Exception as e:
            logger.debug(f"Redis not available for fan-out: {e}")
        return None

    async def _redis_fan_out(
        self,
        tasks: List[Dict],
        max_concurrent: int,
        job_id: str,
        provider_key: str,
        redis_client,
    ) -> List[str]:
        """
        Redis Streams-backed fan-out/fan-in.

        1. XADD tasks to task stream
        2. Track progress in Redis hash
        3. Run workers with asyncio.gather bounded by semaphore
        4. XADD results to result stream
        5. Aggregate results
        """
        self._start_time = time.time()

        task_stream = f"swarm:fanout:tasks:{job_id}"
        result_stream = f"swarm:fanout:results:{job_id}"
        progress_key = f"swarm:fanout:progress:{job_id}"

        # Enqueue all tasks to Redis Stream
        task_ids = []
        for i, task_spec in enumerate(tasks):
            msg = task_spec.get("message", "") if isinstance(task_spec, dict) else str(task_spec)
            mode = task_spec.get("mode", "") if isinstance(task_spec, dict) else ""
            profile = task_spec.get("profile", "") if isinstance(task_spec, dict) else ""

            stream_id = await redis_client.xadd(task_stream, {
                "task_index": str(i),
                "message": msg,
                "mode": mode,
                "profile": profile,
                "status": "pending",
            }, maxlen=10000)
            task_ids.append(stream_id)

        # Initialize progress tracking
        await redis_client.set_json(progress_key, {
            "total": len(tasks),
            "completed": 0,
            "failed": 0,
            "started_at": time.time(),
        }, ex=3600)  # 1h TTL

        # Concurrency-limited parallel execution
        semaphore = asyncio.Semaphore(max_concurrent)
        results = [None] * len(tasks)

        async def _worker(index: int, task_spec: Dict):
            async with semaphore:
                msg = task_spec.get("message", "") if isinstance(task_spec, dict) else str(task_spec)
                mode = task_spec.get("mode", "") if isinstance(task_spec, dict) else ""
                profile = task_spec.get("profile", "") if isinstance(task_spec, dict) else ""

                try:
                    result = await self._run_single_subordinate(msg, mode, profile, provider_key, task_index=index)

                    # Write result to Redis result stream
                    await redis_client.xadd(result_stream, {
                        "task_index": str(index),
                        "result": result[:10000],  # Cap at 10KB per result
                        "status": "completed",
                    }, maxlen=10000)

                    results[index] = result
                except asyncio.CancelledError:
                    # FIX (Iteration 22 / RCA-22): CancelledError is BaseException in Python 3.9+
                    error_msg = f"Error: Task {index} cancelled (asyncio.CancelledError)"
                    await redis_client.xadd(result_stream, {
                        "task_index": str(index),
                        "result": error_msg,
                        "status": "cancelled",
                    }, maxlen=10000)
                    results[index] = error_msg
                    logger.warning(f"Fan-out task {index} cancelled via CancelledError")
                except Exception as e:
                    error_msg = f"Error: {str(e)}"
                    await redis_client.xadd(result_stream, {
                        "task_index": str(index),
                        "result": error_msg,
                        "status": "failed",
                    }, maxlen=10000)
                    results[index] = error_msg
                    logger.error(f"Fan-out task {index} failed: {e}")

        # asyncio.wait with timeout for bounded parallel execution.
        # REPLACES bare asyncio.gather(return_exceptions=False) which:
        # 1. Had NO timeout — could hang forever
        # 2. Used return_exceptions=False — one exception cascades to cancel ALL
        # See rca_asyncio_blocking_coe.md (5-Whys COE) and debate_round_1.md (Judge 3)
        _fan_out_tasks = [
            asyncio.ensure_future(
                _worker(i, t if isinstance(t, dict) else {"message": str(t)})
            )
            for i, t in enumerate(tasks)
        ]

        # Register tasks in TaskRegistry so the supervisor IO-Breaker
        # can target individual stuck subordinates (Phase 2 hardening)
        try:
            from python.helpers.task_registry import TaskRegistry
            registry = TaskRegistry.instance()
            context_id = self.agent.context.id if hasattr(self.agent, 'context') and self.agent.context else "unknown"
            for i, task in enumerate(_fan_out_tasks):
                composite_id = f"fan_out_{i}@{context_id}"
                registry.register_task(composite_id, task)
            logger.debug(f"[FAN-OUT] Registered {len(_fan_out_tasks)} tasks in TaskRegistry")
        except Exception as e:
            logger.debug(f"[FAN-OUT] TaskRegistry registration skipped: {e}")

        if _fan_out_tasks:
            done, pending = await asyncio.wait(
                _fan_out_tasks,
                timeout=FAN_OUT_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                logger.warning(
                    f"[FAN-OUT] {len(pending)} tasks timed out after "
                    f"{FAN_OUT_TIMEOUT}s — cancelling"
                )
                for p in pending:
                    p.cancel()
                # Brief grace period for cleanup (finally blocks)
                await asyncio.wait(pending, timeout=5.0)
            # Collect any exceptions from completed tasks
            for task in done:
                if task.exception():
                    logger.error(f"[FAN-OUT] Task exception: {task.exception()}")

            # Cleanup completed tasks from TaskRegistry
            try:
                from python.helpers.task_registry import TaskRegistry
                TaskRegistry.instance().cleanup_done()
            except Exception:
                pass

        # Update progress
        completed = sum(1 for r in results if r and not r.startswith("Error:"))
        failed = sum(1 for r in results if r and r.startswith("Error:"))
        await redis_client.set_json(progress_key, {
            "total": len(tasks),
            "completed": completed,
            "failed": failed,
            "elapsed": time.time() - self._start_time,
        }, ex=3600)

        # Cleanup streams after collecting (TTL would also handle this)
        try:
            await redis_client.delete(task_stream, result_stream)
        except Exception:
            pass

        return results

    async def _asyncio_fan_out(
        self,
        tasks: List[Dict],
        max_concurrent: int,
        provider_key: str,
    ) -> List[str]:
        """Pure asyncio fan-out fallback (no Redis)."""
        self._start_time = time.time()
        semaphore = asyncio.Semaphore(max_concurrent)
        results = [None] * len(tasks)

        async def _worker(index: int, task_spec: Dict):
            async with semaphore:
                msg = task_spec.get("message", "") if isinstance(task_spec, dict) else str(task_spec)
                mode = task_spec.get("mode", "") if isinstance(task_spec, dict) else ""
                profile = task_spec.get("profile", "") if isinstance(task_spec, dict) else ""
                try:
                    results[index] = await self._run_single_subordinate(msg, mode, profile, provider_key, task_index=index)
                except asyncio.CancelledError:
                    # FIX (Iteration 22 / RCA-22): CancelledError is BaseException in Python 3.9+
                    results[index] = f"Error: Task {index} cancelled (asyncio.CancelledError)"
                    logger.warning(f"Fan-out task {index} cancelled via CancelledError")
                except Exception as e:
                    results[index] = f"Error: {str(e)}"
                    logger.error(f"Fan-out task {index} failed: {e}")

        # asyncio.wait with timeout for bounded parallel execution.
        # Same fix as _redis_fan_out — see rca_asyncio_blocking_coe.md
        _fan_out_tasks = [
            asyncio.ensure_future(
                _worker(i, t if isinstance(t, dict) else {"message": str(t)})
            )
            for i, t in enumerate(tasks)
        ]

        # Register tasks in TaskRegistry (Phase 2 hardening)
        try:
            from python.helpers.task_registry import TaskRegistry
            registry = TaskRegistry.instance()
            context_id = self.agent.context.id if hasattr(self.agent, 'context') and self.agent.context else "unknown"
            for i, task in enumerate(_fan_out_tasks):
                composite_id = f"fan_out_async_{i}@{context_id}"
                registry.register_task(composite_id, task)
        except Exception:
            pass

        if _fan_out_tasks:
            done, pending = await asyncio.wait(
                _fan_out_tasks,
                timeout=FAN_OUT_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                logger.warning(
                    f"[FAN-OUT] {len(pending)} asyncio tasks timed out after "
                    f"{FAN_OUT_TIMEOUT}s — cancelling"
                )
                for p in pending:
                    p.cancel()
                await asyncio.wait(pending, timeout=5.0)
            for task in done:
                if task.exception():
                    logger.error(f"[FAN-OUT] Task exception: {task.exception()}")

            # Cleanup from TaskRegistry
            try:
                from python.helpers.task_registry import TaskRegistry
                TaskRegistry.instance().cleanup_done()
            except Exception:
                pass

        return results

    async def _run_single_subordinate(
        self,
        message: str,
        mode: str,
        profile: str,
        provider_key: str,
        task_index: int = 0,
    ) -> str:
        """Create and run a single subordinate agent (truly parallel)."""
        # Initialize subordinate in thread pool so it doesn't block event loop
        # This is the key to true parallel spawning
        config = await asyncio.to_thread(initialize_agent)
        if profile:
            config.profile = profile

        sub = Agent(self.agent.number + 1, config, self.agent.context)
        
        # Set numbered name so it shows in UI/logs/thoughts (e.g. "Researcher [3]")
        base_name = sub.agent_name or (profile or 'Sub').replace('-', ' ').title()
        sub.agent_name = f"{base_name} [{task_index}]"
        
        sub.set_data(Agent.DATA_NAME_SUPERIOR, self.agent)

        # FIX-024: Propagate phase cap to subordinate
        phase_cap = self.agent.data.get("_phase_cap")
        if phase_cap is not None:
            sub.data["_phase_cap"] = phase_cap

        # FIX-024: Seed build loop detector from parent's propagated state
        try:
            propagated = self.agent.data.get("_build_failure_propagated")
            if propagated:
                from python.helpers.build_loop_detector import seed_build_loop_detector
                seed_build_loop_detector(sub, propagated)
        except Exception:
            pass  # Non-fatal

        # Apply mode
        if MODE_SUPPORT and mode:
            try:
                set_agent_mode(sub, mode)
            except Exception:
                pass

        await sub.hist_add_user_message(
            UserMessage(message=message, attachments=[]),
            sender_type="agent",
            sender_id=self.agent.agent_name,
        )

        AgentTracer.trace_subordinate_created(
            parent_agent=self.agent,
            subordinate_agent=sub,
            mission=message,
        )

        # Run monologue with retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await sub.monologue()

                AgentTracer.trace_subordinate_completed(
                    parent_agent=self.agent,
                    subordinate_agent=sub,
                    result=result,
                )

                # Conditionally propagate tool_data_anchors
                # PRINCIPLE: "Verify once at the edge, trust the chain."
                sub_anchors = sub.data.get("tool_data_anchors", [])
                sub_fidelity_warned = sub.data.get("_fidelity_warned_this_turn", False)
                if sub_anchors and sub_fidelity_warned:
                    if "tool_data_anchors" not in self.agent.data:
                        self.agent.data["tool_data_anchors"] = []
                    self.agent.data["tool_data_anchors"].extend(sub_anchors)
                    self.agent.data["tool_data_anchors"] = self.agent.data["tool_data_anchors"][-50:]
                elif sub_anchors:
                    logger.info(
                        f"NOT propagating {len(sub_anchors)} anchors from fan-out sub "
                        f"(passed fidelity — data verified at edge)"
                    )

                return result
            except asyncio.CancelledError:
                # FIX (Iteration 22 / RCA-22): Don't retry CancelledError — it's intentional.
                logger.warning(f"Fan-out subordinate cancelled via CancelledError (attempt {attempt + 1})")
                raise
            except Exception as e:
                from python.models import _is_rate_limit_error
                if _is_rate_limit_error(e) and attempt < max_retries - 1:
                    delay = 5 * (attempt + 1)
                    logger.debug(f"Subordinate rate limited, retry {attempt + 1}/{max_retries}, waiting {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise

        return "Error: max retries exceeded"

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://fork_right {self.agent.agent_name}: Fan-Out Subordinates",
            content="",
            kvps=self.args,
        )

# Backward-compat alias: tests import FanOut
FanOut = FanOutSubordinates
