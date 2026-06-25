"""
Personalization Engine — Core data model, analysis, and prompt integration.

This module provides:
- PersonalizationProfile: 10-dimension psychological profile with persistence
- PersonalizationAnalyzer: LLM-based signal analysis pipeline
- format_prompt_context: System prompt injection based on established profile

Existing record_personalization() function preserved for backwards compatibility.
"""
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)

PERSONALIZATION_FILE = "memory-bank/personalization.md"

# ═══════════════════════════════════════════════════════════════════
#  Legacy Function — Preserved for Backwards Compatibility
# ═══════════════════════════════════════════════════════════════════

def record_personalization(content: str, source: str = "UI Feedback"):
    """
    Records positive user feedback as a personalization preference.
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"""
## Feedback: {source} ({timestamp})
- **Content**: {content.strip()}
- **Action**: User gave THUMBS UP. Prioritize this style or approach in future interactions.

---
"""
        os.makedirs(os.path.dirname(PERSONALIZATION_FILE), exist_ok=True)
        with open(PERSONALIZATION_FILE, "a") as f:
            f.write(entry)

        logger.info(f"Recorded personalization feedback from {source}")
        return True
    except Exception as e:
        logger.error(f"Failed to record personalization: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

DEFAULT_TENETS = [
    "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism", "risk_tolerance",
    "need_for_cognition", "autonomy", "time_orientation",
    "sensory_sensitivity"
]

# Communication style mapping: tenet scores → style dimensions
STYLE_MAPPING = {
    "tone": {
        "drivers": ["extraversion", "agreeableness"],
        "low": "formal_reserved",
        "mid": "professional",
        "high": "warm_conversational"
    },
    "language_complexity": {
        "drivers": ["need_for_cognition", "openness"],
        "low": "simple",
        "mid": "moderate",
        "high": "technical"
    },
    "structure_preference": {
        "drivers": ["conscientiousness"],
        "low": "freeform",
        "mid": "moderately_structured",
        "high": "bullet_points_and_tables"
    },
    "detail_level": {
        "drivers": ["need_for_cognition", "conscientiousness"],
        "low": "concise",
        "mid": "balanced",
        "high": "comprehensive"
    },
    "proactivity": {
        "drivers": ["autonomy", "openness"],
        "low": "reactive",
        "mid": "moderate",
        "high": "high"
    }
}


# ═══════════════════════════════════════════════════════════════════
#  PersonalizationProfile
# ═══════════════════════════════════════════════════════════════════

class PersonalizationProfile:
    """
    10-dimension psychological profile for adaptive personalization.

    Each tenet has a score (0.0-1.0) and confidence (0.0-1.0).
    Scores start neutral (0.5) and adapt based on collected signals.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.confidence = 0.0
        self.analysis_count = 0
        self.last_updated: Optional[str] = None
        self.tenets: dict[str, dict[str, Any]] = {
            name: {"score": 0.5, "confidence": 0.0, "description": ""}
            for name in DEFAULT_TENETS
        }
        self.communication_style: dict[str, str] = {}

    def get_communication_style(self) -> dict[str, str]:
        """Derive communication style preferences from tenet scores."""
        style = {}
        for style_dim, mapping in STYLE_MAPPING.items():
            drivers = mapping["drivers"]
            # Average the driver tenet scores
            driver_scores = [self.tenets[d]["score"] for d in drivers if d in self.tenets]
            if not driver_scores:
                avg = 0.5
            else:
                avg = sum(driver_scores) / len(driver_scores)

            # Map to level
            if avg < 0.4:
                style[style_dim] = mapping["low"]
            elif avg < 0.7:
                style[style_dim] = mapping["mid"]
            else:
                style[style_dim] = mapping["high"]
        return style

    def to_dict(self) -> dict[str, Any]:
        """Serialize profile to dictionary."""
        return {
            "user_id": self.user_id,
            "confidence": self.confidence,
            "analysis_count": self.analysis_count,
            "last_updated": self.last_updated,
            "tenets": self.tenets,
            "communication_style": self.communication_style,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_user_id: str = "default") -> "PersonalizationProfile":
        """Deserialize profile from dictionary. Handles both structured and raw LLM formats."""
        profile = cls(user_id=data.get("user_id", fallback_user_id))
        profile.confidence = data.get("confidence", data.get("confidence_score", 0.0))
        profile.analysis_count = data.get("analysis_count", 0)
        profile.last_updated = data.get("last_updated")
        profile.communication_style = data.get("communication_style", {})
        
        raw_tenets = data.get("tenets", {})
        if isinstance(raw_tenets, dict):
            # Structured format: {"openness": {"score": 0.7, "description": "..."}, ...}
            profile.tenets = raw_tenets
        elif isinstance(raw_tenets, list):
            for item in raw_tenets:
                if isinstance(item, dict):
                    name = item.get("name", "").lower().replace(" ", "_")
                    if not name:
                        name = f"trait_{len(profile.tenets)}"
                    profile.tenets[name] = {
                        "score": item.get("score", 0.5),
                        "confidence": item.get("confidence", 0.75),
                        "description": item.get("description", "")
                    }
                else:
                    profile.tenets[f"trait_{len(profile.tenets)}"] = {
                        "score": 0.5,
                        "confidence": 0.75,
                        "description": str(item)
                    }
        return profile

    def save(self, data_dir: str) -> None:
        """Save profile to JSON file under data_dir/{user_id}/profile.json."""
        user_dir = os.path.join(data_dir, self.user_id)
        os.makedirs(user_dir, exist_ok=True)

        self.last_updated = datetime.now(timezone.utc).isoformat()
        filepath = os.path.join(user_dir, "profile.json")
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, user_id: str, data_dir: str) -> Optional["PersonalizationProfile"]:
        """Load profile from disk. Returns None if not found."""
        filepath = os.path.join(data_dir, user_id, "profile.json")
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls.from_dict(data, fallback_user_id=user_id)

    @classmethod
    def delete(cls, user_id: str, data_dir: str) -> None:
        """Delete all profile data for a user."""
        user_dir = os.path.join(data_dir, user_id)
        if os.path.exists(user_dir):
            from python.helpers import files
            files.delete_dir(user_dir)

    def record_evolution(self, data_dir: str) -> None:
        """Append a snapshot of current scores to evolution history."""
        user_dir = os.path.join(data_dir, self.user_id)
        os.makedirs(user_dir, exist_ok=True)

        evolution_path = os.path.join(user_dir, "evolution.jsonl")
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "analysis_count": self.analysis_count,
            "confidence": self.confidence,
            "tenets": {k: {"score": v["score"], "confidence": v["confidence"]} for k, v in self.tenets.items()}
        }
        with open(evolution_path, "a") as f:
            f.write(json.dumps(snapshot) + "\n")

    def get_evolution_history(self, data_dir: str) -> list[dict]:
        """Read evolution history as list of snapshots."""
        evolution_path = os.path.join(data_dir, self.user_id, "evolution.jsonl")
        if not os.path.exists(evolution_path):
            return []
        history = []
        with open(evolution_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    history.append(json.loads(line))
        return history


# ═══════════════════════════════════════════════════════════════════
#  PersonalizationAnalyzer
# ═══════════════════════════════════════════════════════════════════

class PersonalizationAnalyzer:
    """LLM-based analysis pipeline for updating personality tenets."""

    async def analyze_signals(
        self,
        profile: PersonalizationProfile,
        signals: list[dict],
    ) -> PersonalizationProfile:
        """
        Analyze accumulated signals and update profile tenets.

        Args:
            profile: Current profile to update
            signals: List of signal dicts (from SignalCollector or raw messages)

        Returns:
            Updated PersonalizationProfile
        """
        analysis = await self._call_llm(signals, profile.to_dict())

        # Merge LLM analysis results into profile
        if "tenets" in analysis:
            for tenet_name, values in analysis["tenets"].items():
                if tenet_name in profile.tenets:
                    if "score" in values:
                        profile.tenets[tenet_name]["score"] = values["score"]
                    if "confidence" in values:
                        profile.tenets[tenet_name]["confidence"] = values["confidence"]

        profile.analysis_count += 1

        # Update overall confidence as average of tenet confidences
        tenet_confs = [t["confidence"] for t in profile.tenets.values()]
        profile.confidence = sum(tenet_confs) / len(tenet_confs) if tenet_confs else 0.0

        return profile

    async def _call_llm(self, signals: list[dict], current_profile: dict) -> dict:
        """
        Call the utility LLM for tenets analysis.
        Override or mock this in tests.
        """
        # In production, this calls the configured utility model
        # with the tenets analysis prompt
        raise NotImplementedError("Must be overridden or mocked in tests")


# ═══════════════════════════════════════════════════════════════════
#  System Prompt Injection
# ═══════════════════════════════════════════════════════════════════

def format_prompt_context(
    profile: PersonalizationProfile,
    confidence_threshold: float = 0.3,
) -> str:
    """
    Format personalization profile into system prompt context.

    Returns an empty string if the profile confidence is below threshold.
    """
    if profile.confidence < confidence_threshold:
        return ""

    style = profile.get_communication_style()

    # Build natural language context block
    lines = ["## User Communication Preferences (Personalization)"]
    lines.append("")
    lines.append(f"Based on {profile.analysis_count} interactions (confidence: {profile.confidence:.0%}):")
    lines.append("")

    # Style dimensions
    style_descriptions = {
        "tone": {
            "formal_reserved": "Use a formal, reserved tone",
            "professional": "Use a professional, balanced tone",
            "warm_conversational": "Use a warm, conversational tone"
        },
        "language_complexity": {
            "simple": "Use simple, accessible language",
            "moderate": "Use moderately technical language",
            "technical": "Use precise technical language with domain terminology"
        },
        "structure_preference": {
            "freeform": "Use flowing prose format",
            "moderately_structured": "Use moderate formatting with some structure",
            "bullet_points_and_tables": "Use structured formatting with bullet points and tables"
        },
        "detail_level": {
            "concise": "Be concise and brief",
            "balanced": "Provide balanced detail",
            "comprehensive": "Provide comprehensive, detailed explanations"
        },
        "proactivity": {
            "reactive": "Wait for explicit instructions before acting",
            "moderate": "Be moderately proactive with suggestions",
            "high": "Be highly proactive — anticipate needs and suggest next steps"
        }
    }

    for dim, value in style.items():
        desc_map = style_descriptions.get(dim, {})
        description = desc_map.get(value)
        if description:
            lines.append(f"- {description}")

    # Add high-confidence tenet insights
    lines.append("")
    lines.append("### Key Preferences:")
    high_conf_tenets = [
        (name, data) for name, data in profile.tenets.items()
        if data["confidence"] >= 0.6
    ]
    if high_conf_tenets:
        for name, data in high_conf_tenets:
            direction = "high" if data["score"] >= 0.7 else "low" if data["score"] <= 0.3 else "moderate"
            lines.append(f"- {name.replace('_', ' ').title()}: {direction} ({data['confidence']:.0%} confident)")
    else:
        lines.append("- Still learning user preferences...")

    return "\n".join(lines)
