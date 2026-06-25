from __future__ import annotations
import os
import logging
from python.helpers.extension import Extension
from python.agent import LoopData
from python.helpers import settings

logger = logging.getLogger(__name__)

PERSONALIZATION_DATA_DIR = os.environ.get(
    "PERSONALIZATION_DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "personalization"),
)

# Minimum signals with dimensions before creating a basic profile
PROFILE_THRESHOLD = 3


class SignalCollectorExtension(Extension):
    """
    Collects implicit personality signals from user messages at the end
    of each message loop iteration.

    Two-tier storage:
      Tier 1 (Memory): Basic profile summary written to vector store after 3
          turns and updated every turn. Available via RAG during agent responses
          and visible in the Memory Dashboard under "personalization".
      Tier 2 (Files): Raw signals persisted to JSONL for full history. Used by
          the personal automation agent (APA) for deep, comprehensive analysis.
    """

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        set = settings.get_settings()

        if not set.get("personalization_enabled", True):
            return

        try:
            from python.helpers.personalization_signals import SignalCollector
            from python.helpers.memory import Memory

            data_dir = os.path.abspath(PERSONALIZATION_DATA_DIR)
            user_id = "default"

            collector = SignalCollector(user_id=user_id, data_dir=data_dir)

            # Get the last user message from history
            msgs = self.agent.history.messages
            if not msgs:
                return

            # Find the most recent user message (ai=False means user)
            # Skip tool outputs, supervisor injections, and system messages
            last_user_msg = None
            for msg in reversed(msgs):
                # We only want user messages that are NOT system-generated or supervisor-injected
                if not msg.ai:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    stripped = content.strip()
                    if not stripped:
                        continue
                        
                    # Skip common non-user patterns
                    if any(p in stripped for p in [
                        "{'tool_name':", '{"tool_name":',
                        "{'tool_result':", '{"tool_result":',
                        "{'system_warning':", '{"system_warning":',
                        "'system_message'", '"system_message"',
                        "'user_intervention'", '"user_intervention"',
                        "[REMOTE SUPERVISOR]", "[SUPERVISOR]"
                    ]):
                        continue
                        
                    # Skip very short generic messages that usually aren't user prompts
                    if len(stripped) < 3:
                        continue
                        
                    last_user_msg = content
                    break

            if not last_user_msg:
                return

            logger.info("Signal collector: processing message (%d chars): %.80s...", len(last_user_msg.strip()), last_user_msg.strip())

            # --- TIER 2: Collect and persist signal to JSONL ---
            context = f"chat:{self.agent.context.id if hasattr(self.agent, 'context') and self.agent.context else 'unknown'}"
            signal = collector.collect_implicit_signal(
                message=last_user_msg,
                context=context,
            )

            if signal and signal.get("detected_dimensions"):
                logger.debug(
                    "Personalization signal: %d dimensions from %d chars",
                    len(signal["detected_dimensions"]),
                    len(last_user_msg),
                )

            # --- TIER 1: Build/update basic profile in Memory ---
            # After PROFILE_THRESHOLD signals with dimensions, aggregate all
            # signals into a basic personality profile and write to Memory.
            # On every subsequent turn, re-aggregate and update.
            await self._update_memory_profile(collector, Memory)

        except Exception as e:
            logger.warning("Signal collector extension error: %s", e)

    async def _update_memory_profile(self, collector, Memory):
        """Aggregate all signals into a basic profile and upsert to Memory."""
        try:
            signals = collector.get_signal_history()

            # Only count signals that have detected dimensions
            signals_with_dims = [
                s for s in signals
                if s.get("detected_dimensions")
            ]

            if len(signals_with_dims) < PROFILE_THRESHOLD:
                return  # Not enough data yet

            # Aggregate dimension scores
            dim_scores: dict[str, dict] = {}
            for sig in signals_with_dims:
                weight = collector.calculate_decayed_weight(sig)
                for d in sig.get("detected_dimensions", []):
                    dim = d["dimension"]
                    direction = d["direction"]
                    if dim not in dim_scores:
                        dim_scores[dim] = {"increase": 0.0, "decrease": 0.0, "count": 0}
                    dim_scores[dim][direction] += weight
                    dim_scores[dim]["count"] += 1

            if not dim_scores:
                return

            # Build basic profile text for Memory
            profile_lines = ["User personality profile (auto-detected from conversation patterns):"]
            for dim in sorted(dim_scores.keys()):
                scores = dim_scores[dim]
                net = scores["increase"] - scores["decrease"]
                strength = "strong" if abs(net) > 3 else "moderate" if abs(net) > 1 else "slight"
                direction = "high" if net > 0 else "low" if net < 0 else "neutral"
                profile_lines.append(
                    f"- {dim}: {direction} ({strength}, {scores['count']} signals, "
                    f"net={net:+.1f})"
                )

            # Add behavioral summary
            top_dims = sorted(dim_scores.keys(), key=lambda d: abs(dim_scores[d]["increase"] - dim_scores[d]["decrease"]), reverse=True)
            if top_dims:
                top = top_dims[0]
                net = dim_scores[top]["increase"] - dim_scores[top]["decrease"]
                profile_lines.append(
                    f"\nStrongest trait: {top} ({'high' if net > 0 else 'low'}). "
                    f"Based on {len(signals_with_dims)} behavioral signals "
                    f"across {len(set(s.get('context', '') for s in signals_with_dims))} conversations."
                )

            profile_text = "\n".join(profile_lines)

            # Upsert to Memory: remove old profile entry, insert updated one
            db = await Memory.get(self.agent)

            # Delete previous profile entries (high threshold = exact match area)
            try:
                await db.delete_documents_by_query(
                    query="User personality profile",
                    threshold=0.7,
                    filter=f"area=='{Memory.Area.PERSONALIZATION.value}'",
                )
            except Exception as e:
                logger.warning(f"[SIGNAL COLLECTOR] Failed to delete old personality profile: {e}")

            # Insert updated profile
            await db.insert_text(
                text=profile_text,
                metadata={"area": Memory.Area.PERSONALIZATION.value},
            )
            logger.debug(
                "Basic profile updated in Memory: %d dimensions, %d signals",
                len(dim_scores),
                len(signals_with_dims),
            )

        except Exception as e:
            logger.warning("Failed to update Memory profile: %s", e)
