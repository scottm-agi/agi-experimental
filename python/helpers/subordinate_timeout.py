"""
RCA-330: Activity-aware subordinate timeout and graceful work preservation.

This module provides:
1. _run_with_activity_timeout() — replaces asyncio.wait_for() with an
   activity-aware wrapper that only fires after `idle_timeout` seconds
   of NO tool activity. A hard wall-clock cap remains as safety net.
   
2. stamp_tool_activity_heartbeat() — stamps agent.data["_last_tool_activity"]
   with the current time. Called from agent_process_tools.py after each
   successful tool execution.

3. _extract_completed_work() — extracts the subordinate's command history
   for inclusion in the timeout message.

4. _build_timeout_message() — generates a work-preserving timeout message
   that tells the orchestrator what was accomplished and how to re-delegate.
"""
import asyncio
import logging
import time

logger = logging.getLogger("agix.subordinate")

# RCA-ITR37 FIX-2: Grace window before hard timeout.
# 30 seconds before the hard timeout, inject a "WRAP UP NOW" warning
# and set _budget_expiring flag so the agent can deliver its work summary.
GRACE_WINDOW_SECONDS = 30


async def _run_with_activity_timeout(
    subordinate,
    timeout_seconds: float,
    idle_timeout: float = 120,
) -> str:
    """Run subordinate monologue with activity-aware timeout.

    Instead of a single wall-clock deadline (asyncio.wait_for), this monitors
    the subordinate's last tool activity timestamp. The timeout only fires
    after `idle_timeout` seconds of NO tool execution — not from total
    elapsed time.

    This means an agent running a 90s npm build doesn't lose 90s of its
    budget, because the build completion resets the activity clock.

    Args:
        subordinate: The subordinate Agent (must have .data dict and .monologue())
        timeout_seconds: Maximum TOTAL wall-clock time (hard cap safety net)
        idle_timeout: Max seconds with no tool activity before killing

    Raises:
        asyncio.TimeoutError: With message indicating hard cap or idle timeout
    """
    # Initialize the activity timestamp so the idle check has a baseline
    subordinate.data["_last_tool_activity"] = time.time()

    task = asyncio.ensure_future(subordinate.monologue())
    start = time.time()

    try:
        while not task.done():
            await asyncio.sleep(2)  # poll every 2s — lightweight
            now = time.time()
            last_activity = subordinate.data.get("_last_tool_activity", start)

            # ── RCA-ITR37 FIX-2: GRACEFUL TIMEOUT WITH 30s GRACE WINDOW ──
            # Instead of immediately cancelling at the hard cap, inject a
            # "WRAP UP NOW" warning 30s before the deadline. This gives the
            # agent one final LLM iteration to call response with a summary.
            # The _budget_expiring flag tells response.py and the completion
            # gate to force-accept the response immediately.
            elapsed = now - start
            grace_deadline = timeout_seconds - GRACE_WINDOW_SECONDS

            # Phase 1: Grace warning — inject wrap-up message
            if elapsed > grace_deadline and not subordinate.data.get("_budget_expiring"):
                subordinate.data["_budget_expiring"] = True
                subordinate.data["_last_tool_activity"] = now  # Reset idle timer
                logger.warning(
                    f"[TIMEOUT GRACE] {getattr(subordinate, 'agent_name', '?')}: "
                    f"Budget expiring in {GRACE_WINDOW_SECONDS}s — "
                    f"injecting wrap-up warning (elapsed={elapsed:.0f}s/"
                    f"{timeout_seconds:.0f}s)"
                )
                # Inject wrap-up warning into agent's conversation history
                # so the LLM sees it on its next iteration
                try:
                    _wrap_up_msg = (
                        "⏰ **TIME BUDGET EXPIRING — WRAP UP NOW**\n\n"
                        "You have ~30 seconds before hard timeout. You MUST "
                        "immediately call the `response` tool with:\n"
                        "1. What you completed (files created/modified)\n"
                        "2. What remains unfinished\n"
                        "3. Any build/test status\n\n"
                        "Do NOT start new work. Do NOT run new commands. "
                        "Call `response` RIGHT NOW with your work summary."
                    )
                    # Use asyncio.ensure_future to inject without blocking
                    # the monitoring loop. hist_add_tool_result is the most
                    # reliable injection point — it appears as a tool result
                    # which the LLM processes immediately.
                    asyncio.ensure_future(
                        subordinate.hist_add_tool_result(
                            "system_budget_warning",
                            _wrap_up_msg,
                            success=False,
                        )
                    )
                except Exception as e:
                    logger.warning(f"[TIMEOUT GRACE] Failed to inject wrap-up: {e}")

            # Phase 2: Hard cap — cancel after grace window expires
            if elapsed > timeout_seconds:
                task.cancel()
                # Give the task a moment to handle CancelledError and run finally blocks
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.TimeoutError(
                    f"Hard timeout after {timeout_seconds:.0f}s"
                )


            # Idle check: no tool activity for idle_timeout seconds
            # RCA-355: Skip idle check when agent is in an LLM call.
            # LLM inference (60-300s) is legitimate work, not idleness.
            # Without this, the 120s idle timer fires during model thinking,
            # killing subordinates that are actively working.
            # RCA-357: Also skip when agent is blocked in a long-running tool
            # (npm install, git clone, builds). code_execution.py:L504 sets
            # _blocked_in_tool=True during get_terminal_output() which polls
            # the subprocess. Without this, the idle timer kills subordinates
            # that are waiting for npm install to finish.
            #
            # RCA-480: CAP on llm_in_progress trust. When a streaming LLM
            # connection hangs (no data, no timeout, no error), the flag
            # stays True forever and the idle checker never fires — the
            # subordinate is stuck permanently. Fix: if llm_in_progress has
            # been True for > MAX_LLM_CALL_SECONDS, treat it as stalled and
            # let the idle timeout fire. The monologue retry will handle it.
            MAX_LLM_CALL_SECONDS = 180  # 3 minutes — generous cap for large responses
            llm_in_progress = subordinate.data.get("_llm_call_in_progress", False)
            blocked_in_tool = subordinate.data.get("_blocked_in_tool", False)

            # RCA-480: Check if LLM call has exceeded max duration
            llm_stalled = False
            if llm_in_progress:
                llm_started = subordinate.data.get("_llm_call_started_at", 0)
                if llm_started and (now - llm_started) > MAX_LLM_CALL_SECONDS:
                    llm_stalled = True
                    import logging
                    logging.getLogger("agix.subordinate_timeout").warning(
                        f"[RCA-480] {getattr(subordinate, 'agent_name', '?')}: "
                        f"LLM call stalled for {now - llm_started:.0f}s "
                        f"(cap={MAX_LLM_CALL_SECONDS}s) — treating as idle"
                    )
                    # Reset the flag so the agent can retry
                    subordinate.data["_llm_call_in_progress"] = False

            agent_is_working = (llm_in_progress and not llm_stalled) or blocked_in_tool
            idle_elapsed = now - last_activity
            if idle_elapsed > idle_timeout * 0.8:  # Log when approaching threshold
                import logging
                logging.getLogger("agix.subordinate_timeout").warning(
                    f"[IDLE_CHECK_DIAG] {getattr(subordinate, 'agent_name', '?')}: "
                    f"idle={idle_elapsed:.0f}s/{idle_timeout:.0f}s "
                    f"llm_in_progress={llm_in_progress} blocked_in_tool={blocked_in_tool} "
                    f"llm_stalled={llm_stalled} "
                    f"total_elapsed={now - start:.0f}s "
                    f"→ {'SKIP (agent working)' if agent_is_working else ('TIMEOUT' if idle_elapsed > idle_timeout else 'OK')}"
                )
            if not agent_is_working and now - last_activity > idle_timeout:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.TimeoutError(
                    f"Idle timeout: no tool activity for {idle_timeout:.0f}s "
                    f"(total elapsed: {now - start:.0f}s)"
                    + (f" [LLM call stalled after {MAX_LLM_CALL_SECONDS}s]" if llm_stalled else "")
                )

        return task.result()
    except asyncio.CancelledError:
        # If the outer task is cancelled, propagate
        task.cancel()
        raise


def stamp_tool_activity_heartbeat(agent) -> None:
    """Stamp the agent's last tool activity time.

    Called from agent_process_tools.py after each successful tool execution.
    This heartbeat signal tells _run_with_activity_timeout that the agent
    is productively working, resetting the idle timer.

    Args:
        agent: The Agent instance (must have .data dict)
    """
    try:
        agent.data["_last_tool_activity"] = time.time()
    except Exception:
        # Never crash on heartbeat — it's observational, not critical
        pass


def stamp_llm_heartbeat(agent, in_progress: bool = True) -> None:
    """Stamp the agent's activity time and set LLM-in-progress flag.

    RCA-355: Called from agent.py before and after each LLM call to prevent
    the idle timeout from firing during model inference. LLM calls commonly
    take 60-300s, which exceeds the 120s idle timeout. Without this heartbeat,
    agents get killed during legitimate thinking.

    Call with in_progress=True BEFORE the LLM call starts.
    Call with in_progress=False AFTER the LLM call completes.

    Args:
        agent: The Agent instance (must have .data dict)
        in_progress: True when LLM call is starting, False when it completes
    """
    try:
        agent.data["_last_tool_activity"] = time.time()
        agent.data["_llm_call_in_progress"] = in_progress
        # RCA-480: Track when the LLM call started so the idle checker
        # can detect stalled connections (hung for > MAX_LLM_CALL_SECONDS)
        if in_progress:
            agent.data["_llm_call_started_at"] = time.time()
        else:
            agent.data["_llm_call_started_at"] = 0
    except Exception:
        # Never crash on heartbeat — it's observational, not critical
        pass


def _extract_completed_work(subordinate) -> str:
    """Extract what the subordinate accomplished before timeout.

    Scans subordinate.data for command execution history and returns
    a human-readable bullet list.

    Args:
        subordinate: The subordinate Agent

    Returns:
        Bullet-list string of completed work, or a fallback message
    """
    lines = []

    # Check for command execution history
    commands = subordinate.data.get("_code_execution_commands", [])
    if commands:
        for cmd in commands:
            # Truncate long commands
            cmd_str = str(cmd)[:120]
            lines.append(f"- `{cmd_str}`")

    # Check for files written
    files_written = subordinate.data.get("_files_written", [])
    if files_written:
        for f in files_written[:20]:  # Cap at 20
            lines.append(f"- Wrote: `{f}`")

    if not lines:
        return (
            "No detailed command history available. "
            "Check the project directory for files the subordinate may have created."
        )

    return "\n".join(lines)


def _build_timeout_message(
    subordinate_name: str,
    timeout_seconds: float,
    completed_work: str,
) -> str:
    """Build a work-preserving timeout message for the orchestrator.

    This message:
    1. Says "budget exceeded" (NOT "stuck" or "loop")
    2. Lists what was accomplished
    3. Instructs the orchestrator to re-delegate ONLY remaining work
    4. Reminds about scope cap (≤3-5 features per delegation)

    Args:
        subordinate_name: Name of the timed-out subordinate
        timeout_seconds: The timeout limit that was exceeded
        completed_work: Bullet list of accomplished work

    Returns:
        Formatted timeout message string
    """
    return (
        f"⏰ SUBORDINATE BUDGET EXCEEDED after {timeout_seconds:.0f}s.\n\n"
        f"Agent '{subordinate_name}' ran out of time — this is NOT an error or "
        f"agent failure. The agent was making productive progress but the task scope "
        f"exceeded the delegation time budget.\n\n"
        f"## Work Completed Before Timeout\n{completed_work}\n\n"
        f"## Required Action\n"
        f"1. Check what files the subordinate created/modified in the project directory\n"
        f"2. Re-delegate ONLY the remaining unfinished work (not the whole task)\n"
        f"3. Keep each follow-up delegation scoped to 3-5 small/medium features "
        f"to prevent repeat timeouts\n"
        f"4. Do NOT re-do work that was already completed above"
    )
