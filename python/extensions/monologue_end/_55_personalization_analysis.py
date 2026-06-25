from __future__ import annotations
import asyncio
import os
import json
import logging
from datetime import datetime, timezone
from python.helpers.extension import Extension
from python.agent import LoopData
from python.helpers import settings

logger = logging.getLogger(__name__)

PERSONALIZATION_DATA_DIR = os.environ.get(
    "PERSONALIZATION_DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "personalization"),
)

# agix-saas 10-dimension analysis prompt (ported from personalization.ts)
ANALYSIS_SYSTEM_PROMPT = """You are a psychological analysis assistant focused on creating accurate personalization profiles. When analyzing chat history, prioritize the user's own messages over AI responses - the AI responses should only be considered as context to understand the user better, not as direct indicators of the user's style or preferences. Focus on the user's original text, reactions, and follow-ups to determine their true communication style and preferences. Provide your response in valid JSON format without any additional text."""

ANALYSIS_USER_PROMPT = """You are a psychological analysis assistant tasked with creating a personalized profile for an individual based on their behavioral signals. The goal is to generate context that will refine AI responses and enable decision-making as if they were the individual.

Your output will consist of two main components:

Tenets: 10 concise statements reflecting the individual's psychological characteristics across 10 dimensions.
Communication Style Profile: A structured description of the individual's typical communication tone, language complexity, structure, and other aspects.

IMPORTANT ANALYSIS INSTRUCTIONS:
- FOCUS PRIMARILY ON THE USER'S OWN MESSAGES when determining their psychological profile
- Consider AI responses only as context to understand what the user is responding to
- DO NOT use AI response styles to determine the user's communication style
- Pay special attention to:
  * The user's original prompts and questions
  * How the user responds to AI suggestions
  * The user's follow-up questions and clarifications
  * Topics and themes the user introduces

Psychological Dimensions — Assess across these 10 dimensions:

1. Openness to Experience: High = creative, curious, prefers novelty; Low = practical, prefers routine.
2. Conscientiousness: High = organized, dependable, plans ahead; Low = spontaneous, flexible.
3. Extraversion: High = sociable, assertive, seeks stimulation; Low = reserved, prefers solitude.
4. Agreeableness: High = compassionate, cooperative, values harmony; Low = competitive, direct.
5. Neuroticism: High = emotionally unstable, anxious; Low = calm, resilient.
6. Risk Tolerance: High = embraces risk, thrives in uncertainty; Low = cautious, seeks security.
7. Need for Cognition: High = enjoys complex thinking; Low = prefers simplicity, intuition.
8. Autonomy vs. Interdependence: Autonomy = independent, self-reliant; Interdependence = collaborative, group-focused.
9. Time Orientation: Short-term = focuses on present, immediate rewards; Long-term = plans for future, delayed gratification.
10. Sensory Sensitivity: High = sensitive to stimuli; Low = tolerates high stimulation.

Behavioral signals to analyze:
{signals_text}

Previous analysis (if available):
{previous_profile}

Output format (JSON only, no extra text):
{{
  "tenets": [
    {{"name": "Openness", "score": 0.8, "description": "Highly open to innovation, actively championing the exploration of novel architectures."}},
    {{"name": "Conscientiousness", "score": 0.9, "description": "Exceptionally conscientious, enforcing rigorous standards for TDD and modularity."}},
    {{"name": "Extraversion", "score": 0.3, "description": "Reserved and highly task-oriented, focusing on technical precision."}},
    {{"name": "Agreeableness", "score": 0.4, "description": "Highly direct and assertive, prioritizing adherence to strict standards."}},
    {{"name": "Neuroticism", "score": 0.2, "description": "Emotionally resilient, managing potential project instability through planning."}},
    {{"name": "Risk Tolerance", "score": 0.7, "description": "Balanced risk profile: eagerly takes risks in conceptual design."}},
    {{"name": "Need for Cognition", "score": 0.9, "description": "High need for cognition, showing preference for complex architectural problems."}},
    {{"name": "Autonomy", "score": 0.8, "description": "Strongly autonomous, taking an authoritative lead in defining workflows."}},
    {{"name": "Time Orientation", "score": 0.9, "description": "Future-oriented, prioritizing long-term maintainability."}},
    {{"name": "Sensory Sensitivity", "score": 0.2, "description": "Low sensory sensitivity, focuses entirely on abstract logical structures."}}
  ],
  "communication_style": {{
    "tone": "[e.g., enthusiastic, calm, direct]",
    "language_complexity": "[e.g., simple, moderate, complex]",
    "structure": "[e.g., organized, free-flowing]",
    "perspective": "[e.g., individual-focused, group-focused]",
    "time_focus": "[e.g., present-oriented, future-oriented]",
    "creativity": "[e.g., conventional, innovative]",
    "risk_appetite": "[e.g., conservative, adventurous]",
    "detail_level": "[e.g., high-level, detailed]",
    "sensory_language": "[e.g., minimal, rich]"
  }}
}}

Important: If previous analysis exists, refine it based on new signals rather than starting from scratch. If evidence for a dimension is unclear, maintain the previous assessment."""


class PersonalizationAnalysis(Extension):
    """
    LLM-based personality analysis using agix-saas 10-dimension system.

    Behavior (matching agix-saas pattern):
      - After 3 signals: create initial profile using LLM
      - Every turn after: quickly reassess, update if changed
      - Writes tenets + communication style to Memory (Tier 1)
      - Saves full profile JSON to disk (Tier 2)
    """

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        set = settings.get_settings()

        if not set.get("personalization_enabled", True):
            return

        try:
            from python.helpers.personalization_signals import SignalCollector

            data_dir = os.path.abspath(PERSONALIZATION_DATA_DIR)
            user_id = "default"
            threshold = set.get("personalization_analysis_interval", 3)

            collector = SignalCollector(user_id=user_id, data_dir=data_dir)
            signals = collector.get_signal_history()

            # Only count signals with actual dimensions (skip noise)
            signals_with_dims = [
                s for s in signals if s.get("detected_dimensions")
            ]

            if len(signals_with_dims) < threshold:
                return  # Not enough data yet

            # Cooldown: skip analysis if profile was updated recently (Issue #830)
            cooldown_seconds = set.get("personalization_analysis_cooldown", 300)
            profile_path = os.path.join(data_dir, user_id, "profile.json")
            if os.path.exists(profile_path):
                try:
                    with open(profile_path, "r") as f:
                        existing_profile = json.load(f)
                    last_updated = existing_profile.get("last_updated")
                    if last_updated:
                        last_dt = datetime.fromisoformat(last_updated)
                        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                        if elapsed < cooldown_seconds:
                            logger.debug(
                                "Personalization analysis skipped: cooldown %.0fs remaining",
                                cooldown_seconds - elapsed,
                            )
                            return  # Within cooldown, skip analysis
                except Exception as cd_err:
                    logger.debug("Cooldown check error (proceeding): %s", cd_err)

            # Run analysis in background (non-blocking)
            task = asyncio.create_task(
                self._run_analysis(signals_with_dims, data_dir, user_id)
            )
            return task

        except Exception as e:
            logger.warning("Personalization analysis extension error: %s", e)

    async def _run_analysis(
        self,
        signals: list[dict],
        data_dir: str,
        user_id: str,
    ):
        """Run 10-dimension LLM analysis (agix-saas pattern)."""
        try:
            from python.helpers.memory import Memory

            log_item = self.agent.context.log.log(
                type="util",
                heading="Analyzing personality profile...",
            )

            # Load existing profile if any
            profile_path = os.path.join(data_dir, user_id, "profile.json")
            existing_profile = {}
            if os.path.exists(profile_path):
                try:
                    with open(profile_path, "r") as f:
                        existing_profile = json.load(f)
                except Exception:
                    pass

            # Format signals for LLM
            signals_text = "\n".join(
                f"- [{s.get('timestamp', '?')[:19]}] "
                f"Context: {s.get('context', '?')} | "
                f"Dimensions: {', '.join(d['dimension'] + ':' + d['direction'] for d in s.get('detected_dimensions', []))} | "
                f"Message: {s.get('content', '')[:150]}"
                for s in signals[-20:]  # Last 20 signals max
            )
            
            # --- NEW: Get recent chat history for context only ---
            chat_context = ""
            try:
                # Last 5 user/ai turns
                recent_msgs = self.agent.history.messages[-10:]
                chat_context_lines = ["RECENT CHAT HISTORY (FOR TOPIC CONTEXT ONLY):"]
                for m in recent_msgs:
                    role = "Assistant" if m.ai else "User"
                    text = m.content if isinstance(m.content, str) else str(m.content)
                    # Truncate long messages
                    text = text[:300] + "..." if len(text) > 300 else text
                    chat_context_lines.append(f"{role}: {text}")
                chat_context = "\n".join(chat_context_lines)
            except Exception as context_err:
                logger.warning("Failed to get chat context for analysis: %s", context_err)

            previous_profile = json.dumps(existing_profile, indent=2) if existing_profile else "None"

            # Build the analysis prompt
            prompt = ANALYSIS_USER_PROMPT.format(
                signals_text=signals_text,
                previous_profile=previous_profile,
            )
            
            if chat_context:
                prompt = chat_context + "\n\n" + prompt

            # Call utility model
            async def log_callback(content):
                log_item.stream(content=content)

            response = await self.agent.call_utility_model(
                system=ANALYSIS_SYSTEM_PROMPT,
                message=prompt,
                callback=log_callback,
                background=True,
            )

            if not response or not isinstance(response, str):
                log_item.update(heading="No response from utility model.")
                return

            # Parse JSON response
            response = response.strip()
            result = None
            try:
                result = json.loads(response)
            except json.JSONDecodeError:
                # Try to extract JSON from response
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    try:
                        result = json.loads(response[start:end])
                    except Exception:
                        pass

            if not result or "tenets" not in result:
                log_item.update(heading="Failed to parse analysis response.")
                return

            # Calculate confidence score (agix-saas formula)
            analysis_count = existing_profile.get("analysis_count", 0) + 1
            confidence_score = min(0.5 + (analysis_count * 0.05), 0.95)

            # Build updated profile
            updated_profile = {
                "tenets": result["tenets"],
                "communication_style": result.get("communication_style", {}),
                "confidence": confidence_score,
                "analysis_count": analysis_count,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "signal_count": len(signals),
            }

            # --- TIER 2: Save full profile to disk ---
            os.makedirs(os.path.join(data_dir, user_id), exist_ok=True)
            with open(profile_path, "w") as f:
                json.dump(updated_profile, f, indent=2)

            # --- TIER 1: Write tenets + comm style to Memory vector store ---
            try:
                profile_text = self._format_memory_profile(updated_profile)
                logger.info("Writing personalization profile to Memory vector store (%d chars)", len(profile_text))
                db = await Memory.get(self.agent)

                # Remove old profile entry
                try:
                    removed = await db.delete_documents_by_query(
                        query="Personalization tenets profile",
                        threshold=0.7,
                        filter=f"area=='{Memory.Area.PERSONALIZATION.value}'",
                    )
                    if removed:
                        logger.info("Removed %d old personalization entries from Memory", len(removed))
                except Exception as del_err:
                    logger.info("No existing personalization entries to remove: %s", del_err)

                # Insert updated profile
                await db.insert_text(
                    text=profile_text,
                    metadata={"area": Memory.Area.PERSONALIZATION.value},
                )
                logger.info("✅ Personalization profile written to Memory vector store")
            except Exception as mem_err:
                logger.warning("❌ Failed to write profile to Memory: %s", mem_err)

            # Count tenet changes
            old_tenets = existing_profile.get("tenets", [])
            changes = sum(
                1
                for old, new in zip(old_tenets, result["tenets"])
                if old != new
            )
            if not old_tenets:
                changes = len(result["tenets"])

            # Only show visible notification when there are actual changes (Issue #830)
            if changes > 0:
                log_item.update(
                    heading=f"Profile updated (confidence: {confidence_score:.0%}, {changes} tenet changes)",
                )
            else:
                # No changes — close log silently to avoid UI flash
                log_item.update(
                    heading="Profile unchanged — no tenet changes detected",
                )
            logger.info(
                "Personalization analysis #%d: %d signals → %d tenet changes, confidence %.0f%%",
                analysis_count,
                len(signals),
                changes,
                confidence_score * 100,
            )

        except Exception as e:
            logger.warning("Personalization analysis error: %s", e)

    @staticmethod
    def _format_memory_profile(profile: dict) -> str:
        """Format profile as text for Memory vector store (RAG-retrievable)."""
        lines = [
            "Personalization tenets profile "
            f"(confidence: {profile.get('confidence_score', 0):.0%}, "
            f"analysis #{profile.get('analysis_count', 0)}):"
        ]

        for tenet in profile.get("tenets", []):
            lines.append(f"  {tenet}")

        comm = profile.get("communication_style", {})
        if comm:
            lines.append("\nCommunication style:")
            for key, val in comm.items():
                label = key.replace("_", " ").title()
                lines.append(f"  {label}: {val}")

        return "\n".join(lines)
