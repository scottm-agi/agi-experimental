"""
Agent-related settings section builders.

Phase 2 of settings.py modularization.
Extracts agent config, memory, context, brain, profiles, tasks, and UI sections.
"""

from typing import Any, cast

from python.helpers import files
from python.helpers.providers import get_providers

from .base import (
    SettingsField,
    SettingsSection,
    FieldOption,
    SectionBuilderContext,
    dict_to_env,
)


def build_agent_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Agent Config settings section."""
    settings = ctx.settings
    
    agent_fields: list[SettingsField] = []

    agent_fields.append(
        {
            "id": "agent_profiles_enabled",
            "title": "Enable Agent Profiles (Persona-based routing)",
            "description": "When enabled, agents can use profiles like 'multiagentdev' to intelligently route requests to different models (e.g. Orchestrator -> Architect -> Coder).",
            "type": "switch",
            "value": settings.get("agent_profiles_enabled", True),
        }
    )

    agent_fields.append(
        {
            "id": "mcp_sequential_thinking_enabled",
            "title": "Enable Sequential Thinking MCP Tool",
            "description": "When enabled, agents can use the Sequential Thinking tool to break down complex problems and plan steps across all modes.",
            "type": "switch",
            "value": settings.get("mcp_sequential_thinking_enabled", True),
        }
    )

    agent_fields.append(
        {
            "id": "agent_profile",
            "title": "Default agent profile",
            "description": "Subdirectory of /agents folder to be used by default agent no. 0. Subordinate agents can be spawned with other profiles, that is on their superior agent to decide. This setting affects the behaviour of the top level agent you communicate with.",
            "type": "select",
            "value": settings["agent_profile"],
            "options": [
                {"value": subdir, "label": subdir}
                for subdir in files.get_subdirectories("agents")
                if subdir != "_example"
            ],
        }
    )

    agent_fields.append(
        {
            "id": "agent_knowledge_subdir",
            "title": "Knowledge subdirectory",
            "description": "Subdirectory of /knowledge folder to use for agent knowledge import. 'default' subfolder is always imported and contains framework knowledge.",
            "type": "select",
            "value": settings["agent_knowledge_subdir"],
            "options": [
                {"value": subdir, "label": subdir}
                for subdir in files.get_subdirectories("knowledge", exclude="default")
            ],
        }
    )

    agent_fields.append(
        {
            "id": "llm_cache_enabled",
            "title": "LLM Response Caching",
            "description": "Cache LLM responses in Redis to reduce latency and API costs for repeated queries. Disable for debugging or when testing prompt changes.",
            "type": "switch",
            "value": settings.get("llm_cache_enabled", True),
        }
    )

    return {
        "id": "agent",
        "title": "Agent Config",
        "description": "Agent parameters.",
        "fields": agent_fields,
        "tab": "agent",
    }


def build_memory_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Memory settings section."""
    settings = ctx.settings
    
    memory_fields: list[SettingsField] = []

    memory_fields.append(
        {
            "id": "agent_memory_subdir",
            "title": "Memory Subdirectory",
            "description": "Subdirectory of /memory folder to use for agent memory storage. Used to separate memory storage between different instances.",
            "type": "text",
            "value": settings["agent_memory_subdir"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_dashboard",
            "title": "Memory Dashboard",
            "description": "View and explore all stored memories in a table format with filtering and search capabilities.",
            "type": "button",
            "value": "Open Dashboard",
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_enabled",
            "title": "Memory auto-recall enabled",
            "description": "AGIX will automatically recall memories based on conversation context.",
            "type": "switch",
            "value": settings["memory_recall_enabled"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_delayed",
            "title": "Memory auto-recall delayed",
            "description": "The agent will not wait for auto memory recall. Memories will be delivered one message later. This speeds up agent's response time but may result in less relevant first step.",
            "type": "switch",
            "value": settings["memory_recall_delayed"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_query_prep",
            "title": "Auto-recall AI query preparation",
            "description": "Enables vector DB query preparation from conversation context by utility LLM for auto-recall. Improves search quality, adds 1 utility LLM call per auto-recall.",
            "type": "switch",
            "value": settings["memory_recall_query_prep"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_post_filter",
            "title": "Auto-recall AI post-filtering",
            "description": "Enables memory relevance filtering by utility LLM for auto-recall. Improves search quality, adds 1 utility LLM call per auto-recall.",
            "type": "switch",
            "value": settings["memory_recall_post_filter"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_interval",
            "title": "Memory auto-recall interval",
            "description": "Memories are recalled after every user or superior agent message. During agent's monologue, memories are recalled every X turns based on this parameter.",
            "type": "range",
            "min": 1,
            "max": 10,
            "step": 1,
            "value": settings["memory_recall_interval"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_history_len",
            "title": "Memory auto-recall history length",
            "description": "The length of conversation history passed to memory recall LLM for context (in characters).",
            "type": "number",
            "value": settings["memory_recall_history_len"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_similarity_threshold",
            "title": "Memory auto-recall similarity threshold",
            "description": "The threshold for similarity search in memory recall (0 = no similarity, 1 = exact match).",
            "type": "range",
            "min": 0,
            "max": 1,
            "step": 0.01,
            "value": settings["memory_recall_similarity_threshold"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_memories_max_search",
            "title": "Memory auto-recall max memories to search",
            "description": "The maximum number of memories returned by vector DB for further processing.",
            "type": "number",
            "value": settings["memory_recall_memories_max_search"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_memories_max_result",
            "title": "Memory auto-recall max memories to use",
            "description": "The maximum number of memories to inject into Alex's context window.",
            "type": "number",
            "value": settings["memory_recall_memories_max_result"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_solutions_max_search",
            "title": "Memory auto-recall max solutions to search",
            "description": "The maximum number of solutions returned by vector DB for further processing.",
            "type": "number",
            "value": settings["memory_recall_solutions_max_search"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_recall_solutions_max_result",
            "title": "Memory auto-recall max solutions to use",
            "description": "The maximum number of solutions to inject into Alex's context window.",
            "type": "number",
            "value": settings["memory_recall_solutions_max_result"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_memorize_enabled",
            "title": "Auto-memorize enabled",
            "description": "Alex will automatically memorize facts and solutions from conversation history.",
            "type": "switch",
            "value": settings["memory_memorize_enabled"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_memorize_consolidation",
            "title": "Auto-memorize AI consolidation",
            "description": "Alex will automatically consolidate similar memories using utility LLM. Improves memory quality over time, adds 2 utility LLM calls per memory.",
            "type": "switch",
            "value": settings["memory_memorize_consolidation"],
        }
    )

    memory_fields.append(
        {
            "id": "memory_memorize_replace_threshold",
            "title": "Auto-memorize replacement threshold",
            "description": "Only applies when AI consolidation is disabled. Replaces previous similar memories with new ones based on this threshold. 0 = replace even if not similar at all, 1 = replace only if exact match.",
            "type": "range",
            "min": 0,
            "max": 1,
            "step": 0.01,
            "value": settings["memory_memorize_replace_threshold"],
        }
    )

    return {
        "id": "memory",
        "title": "Memory",
        "description": "Configuration of Alex's memory system. Alex memorizes and recalls memories automatically to help it's context awareness.",
        "fields": memory_fields,
        "tab": "agent",
    }


def build_context_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Context Management settings section."""
    settings = ctx.settings
    
    context_fields: list[SettingsField] = []

    context_fields.append(
        {
            "id": "context_condense_threshold",
            "title": "Context Condensation Threshold",
            "description": "Percentage of context window usage that triggers intelligent condensation. When context usage exceeds this threshold, the system will use LLM to create a comprehensive summary of the conversation history. Default: 72%",
            "type": "range",
            "min": 0.5,
            "max": 0.95,
            "step": 0.01,
            "value": settings["context_condense_threshold"],
        }
    )

    return {
        "id": "context_management",
        "title": "Context Management",
        "description": "Settings for intelligent context window management. The system proactively condenses conversation history to prevent context overflow.",
        "fields": context_fields,
        "tab": "agent",
    }


def build_brain_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Brain Management settings section."""
    
    brain_fields: list[SettingsField] = []

    brain_fields.append(
        {
            "id": "brain_export_all",
            "title": "Export All Memories",
            "description": "Export all memories from the current memory directory to a JSON file for backup.",
            "type": "button",
            "value": "Export Brain",
        }
    )

    brain_fields.append(
        {
            "id": "brain_import",
            "title": "Import Memories",
            "description": "Import memories from a previously exported JSON file.",
            "type": "button",
            "value": "Import Brain",
        }
    )

    brain_fields.append(
        {
            "id": "brain_reset_conversations",
            "title": "Reset Conversation Memories",
            "description": "Delete all conversation memories while preserving imported knowledge. Useful for starting fresh.",
            "type": "button",
            "value": "Reset Conversations",
        }
    )

    brain_fields.append(
        {
            "id": "brain_reset_all",
            "title": "⚠️ Full Brain Reset",
            "description": "DANGER: Delete ALL memories including knowledge. This cannot be undone!",
            "type": "button",
            "value": "Reset Everything",
        }
    )

    return {
        "id": "brain",
        "title": "Brain Management",
        "description": "Management tools for AGIX's memory system. Export, import, and reset memories without affecting core memory settings.",
        "fields": brain_fields,
        "tab": "agent",
    }


def build_profiles_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Agent Profiles settings section."""
    settings = ctx.settings
    all_profiles = ctx.available_profiles
    role_configs = ctx.role_configs
    
    profile_fields: list[SettingsField] = []
    
    profile_fields.append({
        "id": "agent_profile_to_edit",
        "title": "Profile to Tune",
        "description": "Select agent profile to configure its specific model and parameters below.",
        "type": "searchable-select",
        "value": settings.get("agent_profile_to_edit", "alex"),
        "options": [{"value": p, "label": p} for p in all_profiles],
    })
    
    for profile in all_profiles:
        if profile in ("_example",):
            continue
        
        role_cfg = role_configs.get(profile, {})
        # If it's a list (fallbacks), for simplicity in UI we just edit the first one
        if isinstance(role_cfg, list):
            role_cfg = role_cfg[0] if role_cfg else {}

        # Provider field for this profile
        profile_fields.append({
            "id": f"profile_{profile}_provider",
            "title": f"[{profile}] Provider",
            "description": f"Model provider for '{profile}'. Leave empty to use global default.",
            "type": "select",
            "value": role_cfg.get("provider", ""),
            "options": [{"value": "", "label": "(Use Default)"}] + cast(list[FieldOption], get_providers("chat")),
        })
        
        # Model name field for this profile
        profile_fields.append({
            "id": f"profile_{profile}_name",
            "title": f"[{profile}] Model Name",
            "description": f"Model name for '{profile}'. Leave empty to use global default.",
            "type": "text",
            "value": role_cfg.get("name", ""),
        })
        
        # Context length field for this profile
        profile_fields.append({
            "id": f"profile_{profile}_ctx_length",
            "title": f"[{profile}] Context Length",
            "description": f"Maximum context window size (tokens) for '{profile}'. Set to 0 to auto-detect from model metadata. Leave empty to use global chat model context.",
            "type": "number",
            "value": role_cfg.get("ctx_length", 0),
        })

        # Max output tokens for this profile
        profile_fields.append({
            "id": f"profile_{profile}_max_tokens",
            "title": f"[{profile}] Max Output Tokens",
            "description": f"Maximum output tokens for '{profile}'. Recommended: Venice/Grok=16384, OpenAI=4096-16384, Anthropic=8192, Local=2048. Set to 0 for model default.",
            "type": "number",
            "value": role_cfg.get("kwargs", {}).get("max_tokens", 0),
        })

        # Additional parameters (kwargs) for this profile
        profile_fields.append({
            "id": f"profile_{profile}_kwargs",
            "title": f"[{profile}] Parameters",
            "description": f"Additional parameters for '{profile}' (e.g. temperature=0) in .env format. Leave empty to use global defaults. Note: <code>max_tokens</code> is set separately above.",
            "type": "textarea",
            "value": dict_to_env({k: v for k, v in role_cfg.get("kwargs", {}).items() if k != "max_tokens"}),
            "style": "height: 8em",
        })

    return {
        "id": "profiles",
        "title": "Agent Profiles",
        "description": "Configure specific models and providers for different agent profiles. Profiles are defined by folders in <code>/agents</code>.",
        "fields": profile_fields,
        "tab": "agent",
    }


def build_tasks_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Tasks & Automation settings section."""
    settings = ctx.settings
    
    tasks_fields: list[SettingsField] = []

    tasks_fields.append(
        {
            "id": "tasks_enabled",
            "title": "Enable Automated Tasks",
            "description": "Global master switch to enable or disable all automated task execution (Scheduled, Ad-Hoc, and Planned tasks).",
            "type": "switch",
            "value": settings["tasks_enabled"],
        }
    )

    return {
        "id": "tasks",
        "title": "Tasks & Automation",
        "description": "Configure automated task execution behavior.",
        "fields": tasks_fields,
        "tab": "task",
    }


def build_ui_general_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the General UI Settings section."""
    settings = ctx.settings
    
    ui_general_fields: list[SettingsField] = []
    
    ui_general_fields.append(
        {
            "id": "ui_tooltips_enabled",
            "title": "Enable UI Hover Tooltips",
            "description": "Show descriptive popups/bubbles when hovering over important options in the settings and system UI.",
            "type": "switch",
            "value": settings["ui_tooltips_enabled"],
        }
    )

    ui_general_fields.append(
        {
            "id": "show_background_updates",
            "title": "Show Background Updates",
            "description": "If enabled, noisy background updates (tracing, non-essential utility logs) will be shown in the chat interface.",
            "type": "switch",
            "value": settings["show_background_updates"],
        }
    )

    ui_general_fields.append(
        {
            "id": "agent_trace_to_context",
            "title": "Agent Tracing to Context",
            "description": "If enabled, detailed agent tracing events will be logged to the chat context. Primarily for debugging.",
            "type": "switch",
            "value": settings["agent_trace_to_context"],
        }
    )

    ui_general_fields.append(
        {
            "id": "simple_chat",
            "title": "Simple Chat Mode",
            "description": "If enabled, only user prompts and final agent responses will be shown. Intermediate thoughts and tool calls will be hidden for a cleaner conversation view.",
            "type": "switch",
            "value": settings.get("simple_chat", False),
        }
    )

    ui_general_fields.append(
        {
            "id": "prompt_enhancement",
            "title": "Enhanced Prompt Mode",
            "description": "If enabled, user prompts will be refined and detailed by a utility agent before being sent to the main agent. This helps improve response quality but adds slight latency.",
            "type": "switch",
            "value": settings.get("prompt_enhancement", False),
        }
    )

    return {
        "id": "ui_general",
        "title": "General UI Settings",
        "description": "Configure overall user interface behavior and appearance.",
        "fields": ui_general_fields,
        "tab": "agent",
    }


def build_management_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Configuration Management settings section."""
    
    management_fields: list[SettingsField] = []
    
    management_fields.append({
        "id": "settings_template_apply",
        "title": "Apply Preset Template",
        "description": "Quickly switch between pre-defined configurations for different use cases.",
        "type": "select",
        "value": "",
        "options": [
            {"value": "", "label": "Select a template..."},
            {"value": "venice_optimized", "label": "Optimized (Privacy & Performance)"},
            {"value": "default_standard", "label": "Standard Default (Balanced)"},
            {"value": "minimal_local", "label": "Minimal Local (Low Resource)"},
            {"value": "single_llm_override", "label": "Single LLM Override (Unified)"},
            {"value": "max_performance", "label": "Max Performance (50 Workers)"}
        ]
    })

    management_fields.append({
        "id": "settings_export_bundle",
        "title": "Export Configuration Bundle",
        "description": "Download a complete package of your settings, secrets, and parameters as a JSON file.",
        "type": "button",
        "value": "Export Bundle",
    })

    management_fields.append({
        "id": "settings_import_bundle",
        "title": "Import Configuration Bundle",
        "description": "Upload a previously exported settings bundle (.json) to restore your configuration.",
        "type": "button",
        "value": "Import Bundle",
    })

    return {
        "id": "management",
        "title": "Configuration Management",
        "description": "Backup, restore, and manage your AGIX environment configuration.",
        "fields": management_fields,
        "tab": "agent",
    }


def build_token_budget_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Token Budget settings section."""
    settings = ctx.settings

    budget_fields: list[SettingsField] = []

    budget_fields.append(
        {
            "id": "budget_max_tokens_per_day",
            "title": "Token Limit",
            "description": "Maximum total tokens (input + output) allowed per reset interval across all models. Set to 0 to disable the limit. When exceeded, new agent requests will be blocked until the next reset.",
            "type": "number",
            "value": settings.get("budget_max_tokens_per_day", 0),
        }
    )

    budget_fields.append(
        {
            "id": "budget_reset_interval",
            "title": "Reset Interval",
            "description": "How often the token budget counter resets. 'Day' resets at midnight UTC each day. 'Month' resets on the 1st of each month at midnight UTC.",
            "type": "select",
            "value": settings.get("budget_reset_interval", "day"),
            "options": [
                {"value": "day", "label": "Day"},
                {"value": "month", "label": "Month"},
            ],
        }
    )

    return {
        "id": "token_budget",
        "title": "Token Budget",
        "description": "Set token usage limits to control API consumption. Set the token limit to 0 to disable.",
        "fields": budget_fields,
        "tab": "agent",
    }