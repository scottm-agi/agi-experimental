"""
Agent intervention handling — extracted from agent.py.

Contains the implementation for handle_intervention and wait_if_paused.
These are delegated from Agent methods via the _impl pattern.
"""
import asyncio
import logging
import time

from python.helpers.errors import InterventionException

logger = logging.getLogger(__name__)


async def handle_intervention_impl(agent, progress: str = ""):
    """Handle user intervention and pause logic.

    Delegated from Agent.handle_intervention().
    """
    # C-5 audit fix: Event-based pause wait with supervisor escalation.
    # Replaces asyncio.sleep(0.5) spin loop (600 iterations) with Event.wait.
    # Architecture: 60s → escalate to supervisor, 300s → hard safety net.
    _pause_start = time.time()
    _PAUSE_TIMEOUT = 300  # 5 minutes max — hard safety net
    _PAUSE_ESCALATION_TIMEOUT = 60  # Escalate to supervisor after 60s
    _pause_event = asyncio.Event()  # Set externally when pause is cleared
    _escalated = False

    while agent.context.paused:
        elapsed = time.time() - _pause_start

        # Hard safety net: 300s max
        if elapsed > _PAUSE_TIMEOUT:
            # RCA-ITR41: Check _user_stop_directive BEFORE auto-resuming.
            # If the user said "stop all work", the pause was intentional —
            # auto-resuming defeats the purpose of the stop directive.
            user_stop = (
                agent.data.get("_user_stop_directive", False)
                or (agent.context and agent.context.data.get("_user_stop_directive", False))
            )
            if user_stop:
                logger.warning(
                    f"[AGENT] Pause timeout ({_PAUSE_TIMEOUT}s) hit in "
                    f"handle_intervention — _user_stop_directive is set, "
                    f"TERMINATING agent {agent.agent_name} (not auto-resuming)"
                )
                agent.context.paused = False
                # Synthesize stop intervention so the agent terminates cleanly
                from python.helpers.agent_core.config import UserMessage
                stop_msg = UserMessage(
                    "🛑 User stop directive: terminate immediately.", []
                )
                agent.intervention = stop_msg
                break
            logger.warning(
                f"[AGENT] Pause timeout ({_PAUSE_TIMEOUT}s) hit in "
                f"handle_intervention — auto-resuming agent {agent.agent_name}"
            )
            agent.context.paused = False
            break

        # Supervisor escalation: after 60s, escalate with context
        if elapsed > _PAUSE_ESCALATION_TIMEOUT and not _escalated:
            _escalated = True
            logger.warning(
                f"[AGENT] Pause escalation timeout ({_PAUSE_ESCALATION_TIMEOUT}s) hit — "
                f"escalating to supervisor for agent {agent.agent_name}"
            )
            try:
                escape_context = {
                    "reason": "pause_timeout_escalation",
                    "agent_name": agent.agent_name,
                    "paused_duration_s": int(elapsed),
                    "progress": progress,
                    "message": (
                        f"Agent {agent.agent_name} has been paused for {int(elapsed)}s "
                        f"with no resolution. Supervisor should decide: redirect agent "
                        f"to different approach, force-deliver partial work, or terminate."
                    ),
                }
                redirect_prompt = await agent._attempt_supervisor_redirect(escape_context)
                if redirect_prompt:
                    # Supervisor provided a redirect — unpause and inject
                    logger.info(
                        f"[AGENT] Supervisor resolved pause for {agent.agent_name} — "
                        f"injecting redirect"
                    )
                    agent.context.paused = False
                    from python.helpers.agent_core.config import UserMessage
                    agent.intervention = UserMessage(redirect_prompt, [])
                    break
            except Exception as e:
                logger.error(
                    f"[AGENT] Supervisor escalation failed for {agent.agent_name}: {e}"
                )

        # Event-based wait instead of sleep spin — yields CPU properly
        try:
            await asyncio.wait_for(_pause_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass  # Check loop conditions again
    if (
        agent.intervention
    ):  # if there is an intervention message, but not yet processed
        msg = agent.intervention

        agent.intervention = None  # reset the intervention message
        agent.log(type="debug", content=f"Processing intervention: {str(msg)[:100]}...")

        # === USER MESSAGE PRECEDENCE (Gap A Fix) ===
        # The user's intervention IS the new direction. Reset ALL stale gate state
        # so the agent can respond to new requirements without gates blocking
        # based on criteria from the OLD direction.


        # Check if intervention IS a stop directive — 2-Layer detection
        # Layer 1: Regex produces confidence signals
        # Only auto-set flag for HIGH confidence (>=0.7)
        # Lower confidence gets injected for LLM decision via the extension
        try:
            from python.helpers.user_intent_patterns import get_stop_signals
            signals = get_stop_signals(msg_text)
            if signals["confidence"] >= 0.7:
                agent.data["_user_stop_directive"] = True
                if agent.context:
                    agent.context.data["_user_stop_directive"] = True
                logger.warning(
                    f"[INTERVENTION] High-confidence stop directive "
                    f"(conf={signals['confidence']:.0%}) in intervention "
                    f"for {agent.agent_name} — flag set for immediate exit"
                )
            elif signals["confidence"] >= 0.3:
                logger.info(
                    f"[INTERVENTION] Medium-confidence stop signal "
                    f"(conf={signals['confidence']:.0%}) in intervention "
                    f"for {agent.agent_name} — LLM will decide via extension"
                )
        except Exception as _stop_err:
            logger.debug(f"[INTERVENTION] Stop directive detection failed: {_stop_err}")

        # Update last_user_message so monologue restart uses the NEW direction
        agent.last_user_message = msg

        # If a tool was running, save its progress to history
        last_tool = agent.loop_data.current_tool
        if last_tool:
            tool_progress = last_tool.progress.strip()
            if tool_progress:
                # Log progress as a message with a specific ID if possible
                log_item = agent.log(type="info", content=f"Partial progress: {tool_progress}", verbose=True)
                await agent.hist_add_tool_result(last_tool.name, tool_progress)  # Tool result doesn't have ID in history yet, but that's fine for now
                try:
                    if hasattr(last_tool, "set_progress"):
                        last_tool.set_progress(None)
                except Exception as _prog_err:
                    logger.debug(f"[INTERVENTION] set_progress(None) failed: {_prog_err}")
        if progress.strip():
            log_id = ""
            if "log_item_response" in agent.loop_data.params_temporary:
                log_id = agent.loop_data.params_temporary["log_item_response"].id or ""
            await agent.hist_add_ai_response(progress, id=log_id)
        # append the intervention message
        await agent.hist_add_user_message(msg, intervention=True)
        raise InterventionException(msg)


async def wait_if_paused_impl(agent, timeout: float = 300):
    """Wait while agent is paused, with a hard timeout ceiling.

    Args:
        agent: The Agent instance.
        timeout: Maximum seconds to wait before auto-resuming (default 300s).
                 Prevents infinite hang if context.paused is never cleared.
                 See rca_asyncio_blocking_coe.md.
    """
    _pause_start = time.time()
    # P0-3 (second location): Use Event.wait instead of sleep(0.5) polling
    _resume_event = getattr(agent.context, '_resume_event', None)
    while agent.context.paused:
        if time.time() - _pause_start > timeout:
            # RCA-ITR41: Check _user_stop_directive BEFORE auto-resuming.
            user_stop = (
                agent.data.get("_user_stop_directive", False)
                or (agent.context and agent.context.data.get("_user_stop_directive", False))
            )
            if user_stop:
                logger.warning(
                    f"[AGENT] Pause timeout ({timeout}s) hit in "
                    f"wait_if_paused — _user_stop_directive is set, "
                    f"TERMINATING agent {agent.agent_name} (not auto-resuming)"
                )
                agent.context.paused = False
                raise InterventionException(
                    "🛑 User stop directive: terminate immediately."
                )
            logger.warning(
                f"[AGENT] Pause timeout ({timeout}s) hit in "
                f"wait_if_paused — auto-resuming agent {agent.agent_name}"
            )
            agent.context.paused = False
            break
        # Efficient wait: blocks up to 1.0s or until event is set
        if _resume_event:
            try:
                await asyncio.wait_for(_resume_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass  # Check loop condition again
        else:
            await asyncio.sleep(1.0)  # Fallback: less aggressive than 0.5
