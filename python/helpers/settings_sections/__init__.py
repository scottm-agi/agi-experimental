"""
Settings Sections Module - Complete Modularization

This module provides modular section builders for the settings UI.
All section builders are organized by category:

- models.py: Model configuration sections (chat, util, embed, browser, supervisor)
- agents.py: Agent-related sections (agent config, memory, context, brain, profiles, tasks, UI)
- external.py: External services sections (auth, API keys, LiteLLM, secrets, parameters, webhooks)
- mcp.py: MCP protocol sections (client, server, Perplexity, Context7, A2A)
- system.py: System sections (development, speech/TTS)
- oauth.py: OAuth integration sections (Google Chat, dynamic vendors)

Usage:
    from python.helpers.settings_sections import (
        SectionBuilderContext,
        build_chat_model_section,
        build_agent_section,
        build_oauth_section,
    )
    
    ctx = SectionBuilderContext(settings, default_settings, available_profiles, role_configs, file_access_enabled)
    section = build_chat_model_section(ctx)
"""

# Base types and utilities
from .base import (
    SettingsField,
    SettingsSection,
    FieldOption,
    SectionBuilderContext,
    PASSWORD_PLACEHOLDER,
    API_KEY_PLACEHOLDER,
    dict_to_env,
    env_to_dict,
)

# Phase 1: Model section builders
from .models import (
    build_chat_model_section,
    build_global_model_section,
    build_util_model_section,
    build_embed_model_section,
    build_browser_model_section,
    build_supervisor_model_section,
)

# Phase 2: Agent section builders
from .agents import (
    build_agent_section,
    build_memory_section,
    build_context_section,
    build_brain_section,
    build_profiles_section,
    build_tasks_section,
    build_ui_general_section,
    build_management_section,
    build_token_budget_section,
)

# Phase 3: External services section builders
from .external import (
    build_auth_section,
    build_api_keys_section,
    build_litellm_section,
    build_secrets_section,
    build_parameters_section,
    build_external_api_section,
    build_update_checker_section,
    build_event_hooks_section,
    build_backup_section,
    build_image_gen_section,
)

# Phase 4: MCP section builders
from .mcp import (
    build_mcp_client_section,
    build_perplexity_section,
    build_context7_section,
    build_mcp_server_section,
    build_a2a_section,
)

# Phase 5: System section builders
from .system import (
    build_dev_section,
    build_speech_section,
)

# Phase 6: OAuth section builders
from .oauth import (
    build_oauth_section,
)

# Phase 7: Privacy section builder
from .privacy import (
    build_privacy_section,
)

# Phase 8: Personalization section builder
from .personalization import (
    build_personalization_section,
)

__all__ = [
    # Types from base
    "SettingsField",
    "SettingsSection", 
    "FieldOption",
    "SectionBuilderContext",
    "PASSWORD_PLACEHOLDER",
    "API_KEY_PLACEHOLDER",
    "dict_to_env",
    "env_to_dict",
    
    # Phase 1: Model section builders
    "build_chat_model_section",
    "build_global_model_section",
    "build_util_model_section",
    "build_embed_model_section",
    "build_browser_model_section",
    "build_supervisor_model_section",
    
    # Phase 2: Agent section builders
    "build_agent_section",
    "build_memory_section",
    "build_context_section",
    "build_brain_section",
    "build_profiles_section",
    "build_tasks_section",
    "build_ui_general_section",
    "build_management_section",
    "build_token_budget_section",
    
    # Phase 3: External services section builders
    "build_auth_section",
    "build_api_keys_section",
    "build_litellm_section",
    "build_secrets_section",
    "build_parameters_section",
    "build_external_api_section",
    "build_update_checker_section",
    "build_event_hooks_section",
    "build_backup_section",
    "build_image_gen_section",
    
    # Phase 4: MCP section builders
    "build_mcp_client_section",
    "build_perplexity_section",
    "build_context7_section",
    "build_mcp_server_section",
    "build_a2a_section",
    
    # Phase 5: System section builders
    "build_dev_section",
    "build_speech_section",
    
    # Phase 6: OAuth section builders
    "build_oauth_section",
    
    # Phase 7: Privacy section builder
    "build_privacy_section",

    # Phase 8: Personalization section builder
    "build_personalization_section",
]