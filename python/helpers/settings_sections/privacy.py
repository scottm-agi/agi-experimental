"""
Privacy settings section builder.
"""

from .base import (
    SettingsField,
    SettingsSection,
    SectionBuilderContext,
)

def build_privacy_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Privacy settings section."""
    settings = ctx.settings
    
    fields: list[SettingsField] = []

    fields.append({
        "id": "privacy_mode",
        "title": "Privacy Mode (ZDR)",
        "description": "Enable Zero Data Retention (ZDR) and privacy headers for all model requests. When enabled, models will be called with provider-specific privacy flags (e.g. OpenRouter 'HTTP-Referer' and 'X-Title' set to null, and 'extra_body': {'transforms': []} for zero data retention).",
        "type": "switch",
        "value": settings.get("privacy_mode", True),
    })

    return {
        "id": "privacy",
        "title": "Privacy",
        "description": "Control how your data is handled by external model providers. AGIX follows the 'Private by Default' principle.",
        "fields": fields,
        "tab": "agent",
    }
