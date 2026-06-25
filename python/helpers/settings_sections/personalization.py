"""
Personalization settings section builder.
"""

import os

from .base import (
    SettingsField,
    SettingsSection,
    SectionBuilderContext,
)


def build_personalization_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Personalization settings section."""
    settings = ctx.settings

    fields: list[SettingsField] = []

    fields.append({
        "id": "personalization_enabled",
        "title": "Enable Personalization",
        "description": "When enabled, the system automatically builds a personality profile from your conversations to adapt communication style, detail level, and proactivity. Profile is created after 3 turns and refined on each subsequent turn.",
        "type": "switch",
        "value": settings.get("personalization_enabled", True),
    })

    fields.append({
        "id": "personalized_reply",
        "title": "Personalized Replies",
        "description": "When enabled, the agent adapts its response tone and style to match your communication preferences (e.g. formality, detail level, directness). Disable to use the agent's default voice regardless of your profile.",
        "type": "switch",
        "value": settings.get("personalized_reply", True),
    })

    fields.append({
        "id": "personalization_analysis_interval",
        "title": "Analysis Trigger Interval",
        "description": "Number of conversation turns before the initial personality profile is created. After creation, the profile is updated on every turn. Lower = faster initial profile, higher = more data before first analysis.",
        "type": "range",
        "min": 2,
        "max": 10,
        "step": 1,
        "value": settings.get("personalization_analysis_interval", 3),
    })

    fields.append({
        "id": "personalization_analysis_cooldown",
        "title": "Profile Analysis Cooldown",
        "description": "Minimum time (in seconds) between personality profile analyses. Prevents repeated 'Profile updated' notifications from flashing. Default: 300 seconds (5 minutes).",
        "type": "range",
        "min": 30,
        "max": 900,
        "step": 30,
        "value": settings.get("personalization_analysis_cooldown", 300),
    })

    # Hide the dashboard button in production (Railway) but keep all other settings
    is_production = os.environ.get("RAILWAY_ENVIRONMENT") is not None

    fields.append({
        "id": "personalization_dashboard",
        "title": "Personalization Dashboard",
        "description": "View your personality profile, confidence scores, communication style analysis, and signal history.",
        "type": "button",
        "value": "Open Dashboard",
        "hidden": is_production,
    })

    fields.append({
        "id": "personalization_reset",
        "title": "⚠️ Reset Personalization",
        "description": "Delete your personality profile and all collected signals. The system will start learning from scratch.",
        "type": "button",
        "value": "Reset Profile",
    })

    return {
        "id": "personalization",
        "title": "Personalization",
        "description": "Personality profiling that adapts the agent's communication style based on your interaction patterns. Uses a 10-dimension psychological analysis inspired by Big Five personality traits.",
        "fields": fields,
        "tab": "agent",
    }
