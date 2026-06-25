from __future__ import annotations
from python.helpers.extension import Extension
from python.helpers.event_bus import get_event_bus, SignalType
from python.agent import UserMessage
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

# Max age of signals to consider (prevents stale cross-session leakage)
SIGNAL_MAX_AGE_SECONDS = 120  # 2 minutes


class RemoteIntervention(Extension):
    async def execute(self, **kwargs):
        agent = self.agent
        agent_id = agent.agent_name

        # CRITICAL: Skip supervisor interventions when processing deterministic
        # webhook commands. These commands (build/merge/deploy/monitor) are
        # injected by webhook_handler with "CRITICAL COMMAND DETECTED" and must
        # NOT be overridden by supervisor guidance. Without this guard, the
        # supervisor can redirect the agent to a different task entirely,
        # causing the webhook command to never execute. (Forgejo #900)
        try:
            current_msg = agent.hist_get_last_user_message()
            if current_msg:
                msg_content = str(current_msg.get("content", ""))
                if "CRITICAL COMMAND DETECTED" in msg_content:
                    logger.info(f"[RemoteIntervention] Skipping supervisor guidance — deterministic webhook command active for agent {agent_id}")
                    return
        except Exception as e:
            logger.warning(f"[REMOTE INTERVENTION] Webhook command detection failed: {e}")
            return  # Fail closed — if we can't detect webhooks, don't override with supervisor guidance

        # Get the current context_id to scope signals to THIS conversation only
        context_id = None
        if hasattr(agent, 'context') and agent.context:
            context_id = getattr(agent.context, 'id', None)

        if not context_id:
            # No context = can't scope signals, skip to avoid cross-session leaks
            return

        # Check for recent intervention signals for this agent AND context
        try:
            event_bus = get_event_bus()
            since_cutoff = datetime.now(timezone.utc) - timedelta(seconds=SIGNAL_MAX_AGE_SECONDS)

            signals = event_bus.get_recent_signals(
                agent_id=agent_id,
                signal_type=SignalType.INTERVENTION_GUIDANCE,
                since=since_cutoff,
                limit=5
            )

            if not signals:
                return

            # Filter to signals matching THIS context only
            signals = [s for s in signals if s.context_id == context_id]

            if not signals:
                return

            # Process signals
            processed = agent.data.get("_processed_intervention_signals", set())

            for signal in signals:
                signal_id = signal.id

                if signal_id not in processed:
                    message = signal.details.get("message", "")
                    if message:
                        # Set intervention on agent
                        agent.intervention = UserMessage(
                            message=f"[REMOTE SUPERVISOR GUIDANCE]\n\n{message}",
                            system_message=["[REMOTE SUPERVISOR GUIDANCE]"],
                        )
                        logger.info(f"Applied remote supervisor guidance to agent {agent_id} (context {context_id}): {signal_id}")

                    processed.add(signal_id)

            # Update processed signals (limit size to avoid memory leak)
            if len(processed) > 100:
                processed = set(list(processed)[-100:])
            agent.data["_processed_intervention_signals"] = processed

        except Exception as e:
            logger.error(f"Error checking for remote supervisor interventions: {e}")
            agent.data["_supervisor_unavailable"] = str(e)
