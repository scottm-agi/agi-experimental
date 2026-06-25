from __future__ import annotations

import base64
import hashlib
import time
from python.helpers.parameters import get_parameters_manager
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal, TypedDict, cast


from python.helpers import runtime, whisper, defer, git_helper
from python.helpers import files, dotenv_manager as dotenv, feature_flags
from python.helpers.print_style import PrintStyle
from python.helpers.providers import get_providers
from python.helpers import env_sync

from python.helpers.secrets_helper import get_default_secrets_manager
from python.helpers import dirty_json


# Model constants & defaults extracted to settings_defaults.py
from python.helpers.settings_defaults import (
    MODELS_DEFAULT_CORE,
    MODELS_DEFAULT_UTIL,
    MODELS_DEFAULT_IMAGE_OPENROUTER,
    MODELS_DEFAULT_IMAGE_GEMINI,
    MODELS_DEFAULT_EMBED,
    MODELS_DEFAULT_EMBED_VENICE,
    MODELS_DEFAULT_CLAUDE,
    MODELS_DEFAULT_UNCENSORED,
    MODELS_DEFAULT_GROK,
    get_default_settings,
    get_preset_template,
)



class Settings(TypedDict):
    version: str

    chat_model_provider: str
    chat_model_name: str
    chat_model_api_base: str
    chat_model_kwargs: dict[str, Any]
    chat_model_ctx_length: int
    chat_model_ctx_history: float
    chat_model_vision: bool
    chat_model_rl_requests: int
    chat_model_rl_input: int
    chat_model_rl_output: int
    chat_model_max_tokens: int
    chat_model_thinking: bool
    chat_model_thinking_tokens: int

    util_model_provider: str
    util_model_name: str
    util_model_api_base: str
    util_model_kwargs: dict[str, Any]
    util_model_ctx_length: int
    util_model_ctx_input: float
    util_model_rl_requests: int
    util_model_rl_input: int
    util_model_rl_output: int
    util_model_max_tokens: int
    util_model_thinking: bool
    util_model_thinking_tokens: int

    embed_model_provider: str
    embed_model_name: str
    embed_model_api_base: str
    embed_model_kwargs: dict[str, Any]
    embed_model_rl_requests: int
    embed_model_rl_input: int

    browser_model_provider: str
    browser_model_name: str
    browser_model_api_base: str
    browser_model_vision: bool
    browser_model_ctx_length: int
    browser_model_rl_requests: int
    browser_model_rl_input: int
    browser_model_rl_output: int
    browser_model_max_tokens: int
    browser_model_thinking: bool
    browser_model_thinking_tokens: int
    browser_model_kwargs: dict[str, Any]
    browser_model_template: bool
    browser_http_headers: dict[str, Any]
    browser_agent_max_steps: int
    browser_agent_timeout_seconds: int
    browser_agent_screenshot_timeout: int

    supervisor_model_provider: str
    supervisor_model_name: str
    supervisor_model_api_base: str
    supervisor_model_ctx_length: int
    supervisor_model_kwargs: dict[str, Any]
    supervisor_model_rl_requests: int
    supervisor_model_rl_input: int
    supervisor_model_rl_output: int
    supervisor_model_max_tokens: int
    supervisor_model_thinking: bool
    supervisor_model_thinking_tokens: int
    simple_chat: bool
    prompt_enhancement: bool
    llm_cache_enabled: bool
    token_tracking_enabled: bool
    budget_max_tokens_per_day: int
    budget_reset_interval: str
    grok_fallback_enabled: bool
    ollama_fallback_enabled: bool
    agent_history_max_turns: int

    agent_profile: str
    agent_profile_to_edit: str
    agent_profiles_enabled: bool
    agent_memory_subdir: str
    agent_knowledge_subdir: str
    agent_skills: list[str]

    memory_recall_enabled: bool
    memory_recall_delayed: bool
    memory_recall_interval: int
    memory_recall_history_len: int
    memory_recall_memories_max_search: int
    memory_recall_solutions_max_search: int
    memory_recall_memories_max_result: int
    memory_recall_solutions_max_result: int
    memory_recall_similarity_threshold: float
    memory_recall_query_prep: bool
    memory_recall_post_filter: bool
    memory_memorize_enabled: bool
    memory_memorize_consolidation: bool
    memory_memorize_replace_threshold: float

    context_condense_threshold: float
    supervisor_intervention_timeout_seconds: int

    tasks_enabled: bool
    supervisor_ignore_task_contexts: bool

    personalization_enabled: bool
    personalized_reply: bool
    personalization_analysis_interval: int

    api_keys: dict[str, str]
    perplexity_api_key: str
    context7_api_key: str

    auth_login: str
    auth_password: str
    root_password: str

    rfc_auto_docker: bool
    rfc_url: str
    rfc_password: str
    rfc_port_http: int
    rfc_port_ssh: int

    shell_interface: Literal['local','ssh']

    stt_model_size: str
    stt_language: str
    stt_silence_threshold: float
    stt_silence_duration: int
    stt_waiting_timeout: int

    tts_kokoro: bool
    parameters: dict[str, Any]

    mcp_servers: str
    mcp_client_init_timeout: int
    mcp_client_tool_timeout: int
    mcp_server_enabled: bool
    mcp_server_token: str

    a2a_server_enabled: bool

    secrets: str

    litellm_global_kwargs: dict[str, Any]

    update_check_enabled: bool
    update_repo_url: str
    privacy_mode: bool

    model_configurations: list[dict[str, Any]]
    role_configurations: dict[str, dict[str, Any] | list[dict[str, Any]]]
    routing_rules: dict[str, str]
    model_metadata_cache: dict[str, Any]
    mcp_sequential_thinking_enabled: bool
    
    global_model_enabled: bool
    global_model_provider: str
    global_model_name: str
    global_model_ctx_length: int
    global_model_max_tokens: int
    global_model_thinking: bool
    global_model_thinking_tokens: int
    ui_tooltips_enabled: bool
    task_vector_memory_disabled: bool
    show_background_updates: bool
    agent_trace_to_context: bool
    browser_agent_profile: str

    image_gen_provider: str
    image_gen_model: str

    event_hooks_enabled: bool
    event_hooks_auto_project: bool
    event_hooks_repos: str
    event_hooks_workflows: list[str]
    event_hooks_command_triggers: dict[str, str]
    event_hooks_prompt_templates: dict[str, str]

    file_access_enabled: bool
    hide_file_access: bool
    simple_chat_forced: bool
    simple_chat_default: bool
    hide_sub_agent_tiles: bool
    performance_tier: str


class PartialSettings(Settings, total=False):
    pass


class FieldOption(TypedDict):
    value: str
    label: str


class SettingsField(TypedDict, total=False):
    id: str
    title: str
    description: str
    type: Literal[
        "text",
        "number",
        "select",
        "range",
        "textarea",
        "password",
        "switch",
        "button",
        "html",
    ]
    value: Any
    min: float
    max: float
    step: float
    hidden: bool
    options: list[FieldOption]
    style: str


class SettingsSection(TypedDict, total=False):
    id: str
    title: str
    description: str
    fields: list[SettingsField]
    tab: str


class SettingsOutput(TypedDict):
    sections: list[SettingsSection]


PASSWORD_PLACEHOLDER = "****PSWD****"
API_KEY_PLACEHOLDER = "************"

SETTINGS_FILE = files.get_abs_path("data/settings.json")
TENANT_DEFAULTS_FILE = files.get_abs_path("data/tenant_defaults.json")
_settings: Settings | None = None
_settings_cache = defer.MemoryCache(default_ttl=5.0)


def convert_out(settings: Settings) -> SettingsOutput:
    """Convert settings to UI output format using modular section builders."""
    from python.helpers.settings_sections import (
        SectionBuilderContext,
        # Model section builders
        build_chat_model_section,
        build_global_model_section,
        build_util_model_section,
        build_embed_model_section,
        build_browser_model_section,
        build_supervisor_model_section,
        # Agent section builders
        build_agent_section,
        build_memory_section,
        build_context_section,
        build_brain_section,
        build_profiles_section,
        build_tasks_section,
        build_ui_general_section,
        build_management_section,
        build_token_budget_section,
        # External services section builders
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
        # MCP section builders
        build_mcp_client_section,
        build_perplexity_section,
        build_context7_section,
        build_mcp_server_section,
        build_a2a_section,
        # System section builders
        build_dev_section,
        build_speech_section,
        # OAuth section builders
        build_oauth_section,
        # Privacy section builder
        build_privacy_section,
        # Personalization section builder
        build_personalization_section,
    )
    
    default_settings = get_default_settings()

    # Determine if file access is enabled via feature flags
    file_access_enabled = not feature_flags.is_file_access_disabled()
    hide_file_access = not file_access_enabled
    simple_chat_forced = feature_flags.is_simple_chat_forced()
    simple_chat_default = feature_flags.is_simple_chat_enabled_default()
    hide_sub_agent_tiles = feature_flags.is_sub_agent_tiles_hidden()

    # Get available profiles from agents directory
    available_profiles = files.get_subdirectories("agents")
    role_configs = settings.get("role_configurations", {})
    all_profiles = sorted(list(set(available_profiles)))
    all_profiles = [p for p in all_profiles if p not in ("_example", "architecture")]

    # Create shared context for all section builders
    ctx = SectionBuilderContext(
        settings=settings,
        default_settings=default_settings,
        available_profiles=all_profiles,
        role_configs=role_configs,
        file_access_enabled=file_access_enabled,
    )

    # Build all sections using modular builders
    all_sections = [
            build_ui_general_section(ctx),
            build_management_section(ctx),
            build_tasks_section(ctx),
            build_token_budget_section(ctx),
            build_global_model_section(ctx),
            build_privacy_section(ctx),
            build_personalization_section(ctx),
            build_agent_section(ctx),
            build_profiles_section(ctx),
            build_chat_model_section(ctx),
            build_util_model_section(ctx),
            build_browser_model_section(ctx),
            build_embed_model_section(ctx),
            build_supervisor_model_section(ctx),
            build_memory_section(ctx),
            build_context_section(ctx),
            build_brain_section(ctx),
            build_speech_section(ctx),
            build_image_gen_section(ctx),
            build_api_keys_section(ctx),
            build_litellm_section(ctx),
            build_parameters_section(ctx),
            build_secrets_section(ctx),
            build_oauth_section(ctx),
            build_auth_section(ctx),
            build_mcp_client_section(ctx),
            build_perplexity_section(ctx),
            build_mcp_server_section(ctx),
            build_context7_section(ctx),
            build_a2a_section(ctx),
            build_external_api_section(ctx),
            build_event_hooks_section(ctx),
            build_update_checker_section(ctx),
            build_backup_section(ctx),
            build_dev_section(ctx),
    ]

    # In production SaaS mode, filter to only user-facing sections
    is_production = feature_flags.is_production_env()
    if is_production:
        # Only these section IDs are allowed for SaaS users
        PRODUCTION_ALLOWED_SECTIONS = {
            "ui_general",
            "privacy",
            "personalization",
            "speech",
            "tasks",
            "oauth",
            "parameters",
            "secrets",
            "event_hooks",
        }
        all_sections = [
            s for s in all_sections
            if s.get("id") in PRODUCTION_ALLOWED_SECTIONS
        ]

    result: SettingsOutput = {
        "file_access_enabled": file_access_enabled,
        "hide_file_access": hide_file_access,
        "simple_chat_forced": simple_chat_forced,
        "simple_chat_default": simple_chat_default,
        "hide_sub_agent_tiles": hide_sub_agent_tiles,
        "is_production": is_production,
        # Per-tab UI visibility flags
        "history_enabled": feature_flags.is_history_enabled(),
        "scheduler_enabled": feature_flags.is_scheduler_enabled(),
        "oauth_enabled": feature_flags.is_oauth_enabled(),
        "backup_enabled": feature_flags.is_backup_enabled(),
        "projects_enabled": feature_flags.is_projects_enabled(),
        "mcp_enabled": feature_flags.is_mcp_enabled(),
        "developer_tab_enabled": feature_flags.is_developer_tab_enabled(),
        "external_enabled": feature_flags.is_external_enabled(),
        "sections": all_sections,
    }
    return result


def _get_api_key_field(settings: Settings, provider: str, title: str) -> SettingsField:
    import python.models as models
    key = settings["api_keys"].get(provider, models.get_api_key(provider))
    return {
        "id": f"api_key_{provider}",
        "title": title,
        "type": "text",
        "value": (API_KEY_PLACEHOLDER if key and key != "None" else ""),
    }


def _env_to_dict(data: str):
    result = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        if '=' not in line:
            continue
            
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        
        if value.startswith('"') and value.endswith('"'):
            result[key] = value[1:-1].replace('\\"', '"')
        elif value.startswith("'") and value.endswith("'"):
            result[key] = value[1:-1].replace("\\'", "'")
        else:
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[key] = value
    
    return result


def _dict_to_env(data_dict):
    lines = []
    for key, value in data_dict.items():
        if isinstance(value, str):
            escaped_value = value.replace('"', '\\"')
            lines.append(f'{key}="{escaped_value}"')
        elif isinstance(value, (dict, list, bool)) or value is None:
            lines.append(f'{key}={json.dumps(value, separators=(",", ":"))}')
        else:
            lines.append(f'{key}={value}')
    
    return "\n".join(lines)


# ── Production settings write filters ──────────────────────────────
# These allow SaaS users to modify their own user-facing settings
# while protecting operator-managed fields (API keys, model configs, auth).

# Section IDs that SaaS users may modify in production.
# Must stay in sync with PRODUCTION_ALLOWED_SECTIONS in convert_out().
PRODUCTION_WRITABLE_SECTIONS = {
    "ui_general",
    "privacy",
    "personalization",
    "speech",
    "tasks",
    "oauth",
    "parameters",
    "secrets",
    "event_hooks",
}

# Individual field IDs that are NEVER writable by users in production,
# even if they appear in an allowed section (defense-in-depth).
PRODUCTION_PROTECTED_FIELDS = {
    "auth_login", "auth_password", "root_password",
    "rfc_url", "rfc_password", "rfc_port_http", "rfc_port_ssh",
    "shell_interface",
}


def filter_production_writable(input: dict) -> dict:
    """Filter a full settings save payload to only production-writable sections.
    
    Used by settings_set API in production mode. Strips out sections
    that SaaS users should not be able to modify.
    """
    if "sections" not in input:
        return input
    filtered_sections = []
    for section in input["sections"]:
        section_id = section.get("id", "")
        if section_id in PRODUCTION_WRITABLE_SECTIONS:
            # Strip any protected fields from within allowed sections
            if "fields" in section:
                section = {**section, "fields": [
                    f for f in section["fields"]
                    if f.get("id", "") not in PRODUCTION_PROTECTED_FIELDS
                ]}
            filtered_sections.append(section)
    return {**input, "sections": filtered_sections}


def filter_production_writable_delta(delta: dict) -> dict:
    """Filter a settings delta payload to only production-writable keys.
    
    Used by settings_set_delta API in production mode. Builds the set of
    allowed field IDs dynamically from the current settings output sections.
    """
    # Build the set of writable field IDs from the sections the user can see
    allowed_keys: set[str] = set()
    try:
        current_output = convert_out(get_settings())
        for section in current_output.get("sections", []):
            if section.get("id", "") in PRODUCTION_WRITABLE_SECTIONS:
                for field in section.get("fields", []):
                    field_id = field.get("id", "")
                    if field_id and field_id not in PRODUCTION_PROTECTED_FIELDS:
                        allowed_keys.add(field_id)
    except Exception:
        pass  # If we can't build the list, return empty (deny all)

    return {k: v for k, v in delta.items() if k in allowed_keys}


def convert_in(settings: dict) -> Settings:
    current = get_settings()
    for section in settings["sections"]:
        if "fields" in section:
            for field in section["fields"]:
                should_skip = (
                    field["value"] == PASSWORD_PLACEHOLDER or
                    field["value"] == API_KEY_PLACEHOLDER
                )

                if not should_skip:
                    if field["id"] == "browser_http_headers" or (field["id"].endswith("_kwargs") and not field["id"].startswith("profile_")):
                        current[field["id"]] = _env_to_dict(field["value"])
                    elif field["id"].startswith("api_key_"):
                        current["api_keys"][field["id"]] = field["value"]
                    elif field["id"].startswith("profile_"):
                        suffixes = ["provider", "name", "ctx_length", "max_tokens", "kwargs"]
                        profile_name = ""
                        field_type = ""
                        
                        full_id = field["id"][len("profile_"):]
                        
                        for s in suffixes:
                            if full_id.endswith("_" + s):
                                field_type = s
                                profile_name = full_id[:-len("_" + s)]
                                break
                        
                        if not profile_name or not field_type:
                            continue
                        
                        if "role_configurations" not in current:
                            current["role_configurations"] = {}
                        
                        if profile_name not in current["role_configurations"]:
                            current["role_configurations"][profile_name] = {}
                        
                        target = current["role_configurations"][profile_name]
                        if isinstance(target, list):
                            if not target or not isinstance(target[0], dict):
                                target = {}
                                current["role_configurations"][profile_name] = target
                            else:
                                target = target[0]
                        
                        if field_type == "provider":
                            target["provider"] = field["value"]
                        elif field_type == "name":
                            target["name"] = field["value"]
                        elif field_type == "ctx_length":
                            try:
                                ctx_val = int(str(field["value"]).strip()) if field["value"] else 0
                            except (ValueError, TypeError):
                                ctx_val = 0
                                
                            if ctx_val > 0:
                                target["ctx_length"] = ctx_val
                            elif "ctx_length" in target:
                                del target["ctx_length"]
                        elif field_type == "max_tokens":
                            if "kwargs" not in target: target["kwargs"] = {}
                            try: 
                                max_tokens_val = int(str(field["value"]).strip()) if field["value"] else 0
                            except (ValueError, TypeError):
                                max_tokens_val = 0
                                
                            if max_tokens_val > 0:
                                target["kwargs"]["max_tokens"] = max_tokens_val
                            elif "max_tokens" in target.get("kwargs", {}):
                                del target["kwargs"]["max_tokens"]
                        elif field_type == "kwargs":
                            new_kwargs = _env_to_dict(field["value"])
                            current_max_tokens = target.get("kwargs", {}).get("max_tokens")
                            target["kwargs"] = new_kwargs
                            if current_max_tokens is not None:
                                target["kwargs"]["max_tokens"] = current_max_tokens
                    else:
                        current[field["id"]] = field["value"]
            
    if "event_hooks_workflows" in current and isinstance(current["event_hooks_workflows"], str):
        workflows = [w.strip() for w in current["event_hooks_workflows"].split(",") if w.strip()]
        current["event_hooks_workflows"] = workflows

    return current


def get_settings() -> Settings:
    global _settings
    
    cache_key = "global_settings"
    cached = _settings_cache.get(cache_key) if _settings_cache else None
    if cached:
        return cached

    disk_settings = _read_settings_file()
    if disk_settings:
        _settings = disk_settings
    elif not _settings:
        _settings = get_default_settings()
        # First-boot: apply tenant defaults if available and persist
        tenant_defaults = _read_tenant_defaults()
        if tenant_defaults:
            _settings.update(tenant_defaults)
            norm_first_boot = normalize_settings(_settings)
            _write_settings_file(norm_first_boot)
            PrintStyle.info("Applied tenant defaults on first boot and persisted to data/settings.json.")
        
    norm = normalize_settings(_settings)
    if _settings_cache:
        _settings_cache.set(cache_key, norm)
    return norm


def set_settings(settings: Settings, apply: bool = True):
    global _settings
    if _settings_cache:
        _settings_cache.invalidate()
    previous = _settings
    _settings = normalize_settings(settings)
    _write_settings_file(_settings)
    if apply:
        _apply_settings(previous)


def set_settings_delta(delta: dict, apply: bool = True):
    current = get_settings()
    new = {**current, **delta}
    set_settings(new, apply)


def get_settings_bundle() -> dict:
    """Aggregate all configuration sources into a single bundle."""
    base_settings = _read_settings_file() or get_default_settings()
    
    api_keys = {}
    providers_seen: set[str] = set()
    for p_type in ("chat", "embedding"):
        for provider in get_providers(p_type):
            pid_lower = provider["value"].lower()
            if pid_lower in providers_seen:
                continue
            providers_seen.add(pid_lower)
            import python.models as models
            val = models.get_api_key(pid_lower)
            if val:
                api_keys[pid_lower] = val
    
    api_keys["perplexity"] = os.getenv("PERPLEXITY_API_KEY") or ""
    api_keys["context7"] = os.getenv("CONTEXT7_API_KEY") or ""
    
    secrets_content = get_default_secrets_manager().get_formatted_secrets(masked=False)
    
    try:
        parameters = get_parameters_manager().load_parameters()
    except Exception:
        parameters = {}

    return {
        "settings": base_settings,
        "api_keys": api_keys,
        "secrets": secrets_content,
        "parameters": parameters,
        "timestamp": time.time(),
        "version": base_settings.get("version", "1.0.0")
    }


def apply_settings_bundle(bundle: dict):
    """Apply an aggregated configuration bundle to the system."""
    if "settings" in bundle:
        new_settings = bundle["settings"]
        if "api_keys" in bundle:
            new_settings["api_keys"] = bundle["api_keys"]
            if "perplexity" in bundle["api_keys"]:
                new_settings["perplexity_api_key"] = bundle["api_keys"]["perplexity"]
            if "context7" in bundle["api_keys"]:
                new_settings["context7_api_key"] = bundle["api_keys"]["context7"]
        
        if "secrets" in bundle:
            new_settings["secrets"] = bundle["secrets"]
        if "parameters" in bundle:
            new_settings["parameters"] = json.dumps(bundle["parameters"])
            
        set_settings(new_settings, apply=True)

    replace_secrets = bundle.get("replace_secrets", False)

    if "secrets" in bundle and bundle["secrets"]:
        get_default_secrets_manager().save_secrets_with_merge(bundle["secrets"], replace=replace_secrets)

    if "parameters" in bundle and bundle["parameters"]:
        get_parameters_manager().save_parameters(bundle["parameters"])


def merge_settings(original: Settings, delta: dict) -> Settings:
    merged = original.copy()
    merged.update(delta)
    return merged


# get_preset_template extracted to python.helpers.settings_defaults


def normalize_settings(settings: Settings) -> Settings:
    copy = settings.copy()
    default = get_default_settings()

    if "version" not in copy or copy["version"] != default["version"]:
        _adjust_to_version(copy, default)
        copy["version"] = default["version"]

    keys_to_remove = [key for key in copy if key not in default and not key.startswith("profile_")]
    for key in keys_to_remove:
        del copy[key]

    for key, value in default.items():
        if key not in copy:
            copy[key] = value
        else:
            try:
                target_type = type(value)
                source_value = copy[key]
                
                if target_type is bool:
                    if isinstance(source_value, str):
                        copy[key] = source_value.lower() in ("true", "yes", "1", "on")
                    else:
                        copy[key] = bool(source_value)
                elif target_type is list:
                    if isinstance(source_value, str):
                        copy[key] = [v.strip() for v in source_value.split(",") if v.strip()]
                    else:
                        copy[key] = list(source_value)
                elif target_type is dict:
                    if isinstance(source_value, str):
                        copy[key] = json.loads(source_value)
                    elif isinstance(source_value, dict):
                        copy[key] = source_value
                    else:
                        copy[key] = value
                else:
                    copy[key] = target_type(source_value)

                if isinstance(copy[key], str):
                    copy[key] = copy[key].strip()
            except (ValueError, TypeError):
                copy[key] = value

    env_token = os.getenv("MCP_SERVER_TOKEN")
    if env_token:
        copy["mcp_server_token"] = env_token
    elif not copy.get("mcp_server_token"):
        # Stable fallback: Try global settings first to avoid unnecessary regeneration
        # This breaks the loop where "cleaned" settings trigger a new token.
        # We only call create_auth_token() if we truly have no token yet.
        global _settings
        if _settings and _settings.get("mcp_server_token"):
            copy["mcp_server_token"] = _settings["mcp_server_token"]
        else:
            copy["mcp_server_token"] = create_auth_token()

    if "role_configurations" not in copy or not isinstance(copy["role_configurations"], dict):
        copy["role_configurations"] = {}
    
    profile_pattern = re.compile(r'^profile_(.+)_(provider|name|ctx_length|max_tokens|kwargs)$')
    profiles_found: dict[str, dict[str, Any]] = {}
    
    for key, value in list(copy.items()):
        match = profile_pattern.match(key)
        if match and value:
            profile_name = match.group(1)
            field_type = match.group(2)
            
            if profile_name not in profiles_found:
                profiles_found[profile_name] = {}
            profiles_found[profile_name][field_type] = value
            del copy[key]
    
    for profile_name, config in profiles_found.items():
        if any(config.get(k) for k in ("provider", "name", "ctx_length", "max_tokens", "kwargs")):
            if profile_name not in copy["role_configurations"]:
                copy["role_configurations"][profile_name] = {}
            
            target = copy["role_configurations"][profile_name]
            if isinstance(target, list):
                if not target:
                    target = {}
                    copy["role_configurations"][profile_name] = target
                else:
                    target = target[0] if isinstance(target[0], dict) else {}
            
            if config.get("provider"):
                target["provider"] = config["provider"]
            if config.get("name"):
                target["name"] = config["name"]
            if config.get("ctx_length"):
                try: target["ctx_length"] = int(config["ctx_length"])
                except (ValueError, TypeError): pass
            if config.get("max_tokens"):
                try: 
                    if "kwargs" not in target: target["kwargs"] = {}
                    target["kwargs"]["max_tokens"] = int(config["max_tokens"])
                except (ValueError, TypeError): pass
            if config.get("kwargs"):
                if "kwargs" not in target: target["kwargs"] = {}
                extra_kwargs = _env_to_dict(config["kwargs"]) if isinstance(config["kwargs"], str) else config["kwargs"]
                
                current_max_tokens = target.get("kwargs", {}).get("max_tokens")
                target["kwargs"].update(extra_kwargs)
                if current_max_tokens is not None:
                    target["kwargs"]["max_tokens"] = current_max_tokens

    try:
        available_profiles = files.get_subdirectories("agents")
        for profile in available_profiles:
            profile_settings_path = files.get_abs_path("agents", profile, "settings.json")
            if files.exists(profile_settings_path):
                try:
                    profile_settings_str = files.read_file(profile_settings_path)
                    profile_settings = json.loads(profile_settings_str)
                    if isinstance(profile_settings, dict):
                        role_cfg: dict[str, Any] = {}
                        
                        prefix = "browser_model_" if profile == "browser" else "chat_model_"
                        
                        role_cfg["provider"] = profile_settings.get(f"{prefix}provider") or profile_settings.get("chat_model_provider")
                        role_cfg["name"] = profile_settings.get(f"{prefix}name") or profile_settings.get("chat_model_name")
                        role_cfg["ctx_length"] = profile_settings.get(f"{prefix}ctx_length") or profile_settings.get("chat_model_ctx_length", 0)
                        role_cfg["kwargs"] = profile_settings.get(f"{prefix}kwargs") or profile_settings.get("chat_model_kwargs", {})
                        
                        if role_cfg.get("provider") and role_cfg.get("name"):
                            if profile not in copy["role_configurations"]:
                                copy["role_configurations"][profile] = role_cfg
                            else:
                                target = copy["role_configurations"][profile]
                                if isinstance(target, list): target = target[0] if target else {}
                                if role_cfg.get("provider"): target["provider"] = role_cfg["provider"]
                                if role_cfg.get("name"): target["name"] = role_cfg["name"]
                                if role_cfg.get("ctx_length"): target["ctx_length"] = role_cfg["ctx_length"]
                                if role_cfg.get("kwargs"): target["kwargs"] = role_cfg["kwargs"]
                except Exception:
                    pass
    except Exception:
        pass

    # Migration from agent0 to default (router/orchestrator)
    if copy.get("agent_profile") == "agent0":
        copy["agent_profile"] = "default"
    
    if "agent0" in copy.get("role_configurations", {}):
        if "alex" not in copy["role_configurations"]:
            copy["role_configurations"]["alex"] = copy["role_configurations"].pop("agent0")
        else:
            del copy["role_configurations"]["agent0"]

    # Hydrate MCP server env vars from environment
    if copy.get("mcp_servers"):
        copy["mcp_servers"] = _update_mcp_env_vars(copy["mcp_servers"])

    return copy


def _adjust_to_version(settings: Settings, default: Settings):
    if "version" not in settings or settings["version"].startswith("v0.8"):
        if "agent_profile" not in settings or settings["agent_profile"] == "agent0":
            settings["agent_profile"] = "default"
    elif settings.get("agent_profile") == "agent0":
        settings["agent_profile"] = "default"


def _read_settings_file() -> Settings | None:
    if os.path.exists(SETTINGS_FILE):
        content = files.read_file(SETTINGS_FILE)
        parsed = json.loads(content)
        return normalize_settings(parsed)


def _read_tenant_defaults() -> dict | None:
    """Read tenant_defaults.json if it exists. Returns a partial dict of overrides, NOT a full Settings."""
    if os.path.exists(TENANT_DEFAULTS_FILE):
        try:
            content = files.read_file(TENANT_DEFAULTS_FILE)
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                PrintStyle.debug(f"Loaded tenant defaults from {TENANT_DEFAULTS_FILE}: {list(parsed.keys())}")
                return parsed
        except (json.JSONDecodeError, Exception) as e:
            PrintStyle.error(f"Failed to read tenant defaults from {TENANT_DEFAULTS_FILE}: {e}")
    return None


def _write_settings_file(settings: Settings):
    settings = settings.copy()
    _write_sensitive_settings(settings)
    settings["mcp_servers"] = _update_mcp_env_vars(settings["mcp_servers"])
    _remove_sensitive_settings(settings)
    content = json.dumps(settings, indent=4)
    files.write_file(SETTINGS_FILE, content)


def _remove_sensitive_settings(settings: Settings):
    settings["api_keys"] = {}
    settings["perplexity_api_key"] = ""
    settings["context7_api_key"] = ""
    settings["auth_login"] = ""
    settings["auth_password"] = ""
    settings["rfc_password"] = ""
    settings["root_password"] = ""
    settings["mcp_server_token"] = ""
    settings["secrets"] = ""
    settings["parameters"] = ""


def _update_mcp_env_vars(mcp_servers_json: str) -> str:
    """Update MCP server env vars with current values from environment."""
    try:
        mcp_config = json.loads(mcp_servers_json)
    except (json.JSONDecodeError, TypeError):
        return mcp_servers_json
    
    try:
        from python.helpers.secrets_helper import get_secrets_manager
        get_secrets_manager().sync_to_environ()
    except Exception:
        pass

    def _is_placeholder(val: str | None) -> bool:
        if not val: return True
        placeholders = ["gh" + "p_verification_token", "PLACEHOLDER", "***"]
        return any(p in val for p in placeholders)

    def _get_prioritized_token(primary: str, fallbacks: list[str]) -> str:
        def _normalize(token: str | None) -> str:
            if not token: return ""
            if token.startswith("aagh" + "p_"):
                return token[2:]
            return token

        for key in [primary] + fallbacks:
            val = os.getenv(key)
            if val and not _is_placeholder(val):
                return _normalize(val)

        try:
            from python.helpers import config_db
            for key in [primary] + fallbacks:
                conn = config_db.get_connection()
                cursor = conn.execute(
                    "SELECT scope, key, value FROM secrets WHERE key = ? ORDER BY (CASE WHEN scope = 'global' THEN 1 ELSE 0 END) ASC LIMIT 1",
                    (key,)
                )
                row = cursor.fetchone()
                if row:
                    if not _is_placeholder(row['value']):
                        return _normalize(row['value'])
        except Exception:
            pass

        return ""

    sensitive_env_vars = {
        "PERPLEXITY_API_KEY": os.getenv("PERPLEXITY_API_KEY") or "",
        "CONTEXT7_API_KEY": os.getenv("CONTEXT7_API_KEY") or "",
        "FORGEJO_TOKEN": os.getenv("FORGEJO_TOKEN") or os.getenv("FORGEJO_API_KEY") or "",
        "FORGEJO_URL": os.getenv("FORGEJO_URL") or "",
        "REPO_OWNER": os.getenv("REPO_OWNER") or "",
        "REPO_NAME": os.getenv("REPO_NAME") or "",
        "GITHUB_PERSONAL_ACCESS_TOKEN": _get_prioritized_token(
            "GITHUB_PERSONAL_ACCESS_TOKEN", 
            ["GFIN_GITHUB_TOKEN", "GITHUB_TOKEN"]
        ),
    }
    
    servers_dict = mcp_config.get("mcpServers", mcp_config)
    
    if not isinstance(servers_dict, dict):
        return mcp_servers_json

    for server_name, server_config in servers_dict.items():
        if isinstance(server_config, dict) and "env" in server_config:
            env_dict = server_config["env"]
            if not isinstance(env_dict, dict): continue
            
            for env_key, current_value in sensitive_env_vars.items():
                if env_key in env_dict and current_value:
                    env_dict[env_key] = current_value
    
    return json.dumps(mcp_config, indent=4)


def _write_sensitive_settings(settings: Settings):
    updates = {}
    
    # 1. API Keys - handle masking to prevent erasure
    for key, val in settings["api_keys"].items():
        key_up = key.upper()
        # If masked, try to preserve existing value from env/dotenv
        if val == API_KEY_PLACEHOLDER:
            existing = (os.getenv(f"API_KEY_{key_up}") or 
                        os.getenv(f"{key_up}_API_KEY") or 
                        dotenv.get_dotenv_value(key_up) or
                        dotenv.get_dotenv_value(f"API_KEY_{key_up}") or
                        dotenv.get_dotenv_value(f"{key_up}_API_KEY"))
            if existing:
                updates[key_up] = existing
                PrintStyle.debug(f"[SETTINGS] Preserving masked key for {key_up}")
            else:
                PrintStyle.debug(f"[SETTINGS] Masked key for {key_up} skipped (no existing value found)")
        else:
            updates[key_up] = val

    # 2. Individual API keys — sync ALL env var variants
    for key_name in ["perplexity_api_key", "context7_api_key"]:
        val = settings.get(key_name)
        if val:
            env_key = key_name.upper()  # e.g. PERPLEXITY_API_KEY
            provider = key_name.replace("_api_key", "").upper()  # e.g. PERPLEXITY
            if val == API_KEY_PLACEHOLDER:
                existing = os.getenv(env_key) or dotenv.get_dotenv_value(env_key)
                if existing:
                    updates[env_key] = existing
                    updates[f"API_KEY_{provider}"] = existing
                    updates[provider] = existing
            else:
                updates[env_key] = val
                updates[f"API_KEY_{provider}"] = val
                updates[provider] = val

    # 3. Auth and RFC passwords
    updates[dotenv.KEY_AUTH_LOGIN] = settings.get("auth_login", "")
    
    if settings.get("auth_password"):
        if settings["auth_password"] == PASSWORD_PLACEHOLDER:
             existing = os.getenv(dotenv.KEY_AUTH_PASSWORD) or dotenv.get_dotenv_value(dotenv.KEY_AUTH_PASSWORD)
             if existing:
                 updates[dotenv.KEY_AUTH_PASSWORD] = existing
        else:
            updates[dotenv.KEY_AUTH_PASSWORD] = settings["auth_password"]
    
    if settings.get("rfc_password"):
        if settings["rfc_password"] == PASSWORD_PLACEHOLDER:
             existing = os.getenv(dotenv.KEY_RFC_PASSWORD) or dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD)
             if existing:
                 updates[dotenv.KEY_RFC_PASSWORD] = existing
        else:
            updates[dotenv.KEY_RFC_PASSWORD] = settings["rfc_password"]

    if settings.get("root_password"):
        if settings["root_password"] == PASSWORD_PLACEHOLDER:
             existing = os.getenv(dotenv.KEY_ROOT_PASSWORD) or dotenv.get_dotenv_value(dotenv.KEY_ROOT_PASSWORD)
             if existing:
                 updates[dotenv.KEY_ROOT_PASSWORD] = existing
        else:
            updates[dotenv.KEY_ROOT_PASSWORD] = settings["root_password"]
            try:
                set_root_password(settings["root_password"])
            except Exception:
                pass

    dotenv.save_dotenv_values(updates)
    # Immediately propagate these to the OS environment for the current process
    # AND record timestamps for most-recent-wins resolution
    authoritative_keys: dict = {}
    for key, val in updates.items():
        if val and not val.startswith("******"):
            os.environ[key] = val
            authoritative_keys[key] = val
            # Also ensure both variants are set for API keys
            if key.startswith("API_KEY_"):
                alt = key.replace("API_KEY_", "") + "_API_KEY"
                os.environ[alt] = val
                authoritative_keys[alt] = val
            elif key.endswith("_API_KEY"):
                alt = "API_KEY_" + key.replace("_API_KEY", "")
                os.environ[alt] = val
                authoritative_keys[alt] = val

    # CRITICAL: Run EnvIntegrity repair with these keys as authoritative.
    # This ensures ALL aliases, Secrets DB, and .env are perfectly synced.
    # MD5 loop guard prevents unnecessary repeated writes.
    try:
        from python.helpers.env_integrity import EnvIntegrity
        for k, v in authoritative_keys.items():
            EnvIntegrity.record_write(k, v)
        EnvIntegrity.repair(authoritative=authoritative_keys)
    except Exception as e:
        PrintStyle.debug(f"[SETTINGS] EnvIntegrity repair after save: {e}")

    secrets_manager = get_default_secrets_manager()
    submitted_content = settings["secrets"]
    # Debug logging to trace secrets save (no content preview for security)
    PrintStyle.debug(f"[SETTINGS] _write_sensitive_settings: content_len={len(submitted_content) if submitted_content else 0}")
    secrets_manager.save_secrets_with_merge(submitted_content)

    if "parameters" in settings and settings["parameters"]:
        try:
            params_dict = json.loads(settings["parameters"])
            if params_dict:
                get_parameters_manager().save_parameters(params_dict)
        except Exception as e:
            PrintStyle.error(f"Error saving global parameters: {e}")


# _detect_bedrock_available and _get_default_model_providers extracted to settings_defaults.py


# get_default_settings extracted to python.helpers.settings_defaults

def _sync_to_environ(settings: Settings):
    """Sync critical settings to OS environment variables for immediate effect."""
    env_sync.sync_to_environ(settings)
    
    # Self-heal: detect any remaining drift after sync and auto-repair
    try:
        from python.helpers.env_integrity import EnvIntegrity
        EnvIntegrity.check_and_heal()
    except Exception:
        pass




def _apply_settings(previous: Settings | None):
    import time
    global _settings
    if _settings:
        _sync_to_environ(_settings)
        if _settings.get("secrets"):
            get_default_secrets_manager().save_secrets_with_merge(_settings["secrets"], replace=False)
        
        if _settings.get("parameters"):
            from python.helpers.parameters import get_parameters_manager
            try:
                params_data = _settings["parameters"]
                if isinstance(params_data, str) and params_data.strip():
                    params_dict = json.loads(params_data)
                elif isinstance(params_data, dict):
                    params_dict = params_data
                else:
                    params_dict = {}
                
                if params_dict:
                    get_parameters_manager().save_parameters(params_dict)
            except Exception as e:
                PrintStyle.error(f"Failed to save parameters from settings: {e}")

        from python.agent import AgentContext
        from python.initialize import initialize_agent

        config = initialize_agent()
        
        model_settings_changed = (
            previous is None or
            _settings.get("chat_model_provider") != previous.get("chat_model_provider") or
            _settings.get("chat_model_name") != previous.get("chat_model_name") or
            _settings.get("util_model_provider") != previous.get("util_model_provider") or
            _settings.get("util_model_name") != previous.get("util_model_name") or
            _settings.get("embed_model_provider") != previous.get("embed_model_provider") or
            _settings.get("embed_model_name") != previous.get("embed_model_name") or
            _settings.get("browser_model_provider") != previous.get("browser_model_provider") or
            _settings.get("browser_model_name") != previous.get("browser_model_name") or
            _settings.get("supervisor_model_provider") != previous.get("supervisor_model_provider") or
            _settings.get("supervisor_model_name") != previous.get("supervisor_model_name") or
            _settings.get("role_configurations") != previous.get("role_configurations") or
            _settings.get("api_keys") != previous.get("api_keys") or
            _settings.get("perplexity_api_key") != previous.get("perplexity_api_key") or
            _settings.get("context7_api_key") != previous.get("context7_api_key") or
            _settings.get("secrets") != previous.get("secrets") or
            _settings.get("parameters") != previous.get("parameters") or
            _settings.get("global_model_enabled") != previous.get("global_model_enabled") or
            _settings.get("global_model_provider") != previous.get("global_model_provider") or
            _settings.get("global_model_name") != previous.get("global_model_name") or
            _settings.get("agent_profile") != previous.get("agent_profile")
        )
        
        async def refresh_all_agents():
            refresh_count = 0
            for ctx in list(AgentContext._contexts.values()):
                ctx.config = config
                agent = ctx.agent0
                while agent:
                    agent.config = ctx.config
                    if model_settings_changed and hasattr(agent, "refresh_models"):
                        agent.refresh_models()
                        refresh_count += 1
                    agent = agent.get_data(agent.DATA_NAME_SUBORDINATE)
            if refresh_count > 0:
                PrintStyle.debug(f"Refreshed model wrappers for {refresh_count} agents in background.")

        defer.DeferredTask().start_task(refresh_all_agents)
        
        if not previous or _settings["stt_model_size"] != previous["stt_model_size"]:
            defer.DeferredTask().start_task(whisper.preload, _settings["stt_model_size"])

        if not previous or _settings.get("agent_trace_to_context") != previous.get("agent_trace_to_context"):
            from python import initialize
            initialize.initialize_tracing(log_to_context=_settings.get("agent_trace_to_context", False))

        if not previous or (
            _settings["embed_model_name"] != previous["embed_model_name"]
            or _settings["embed_model_provider"] != previous["embed_model_provider"]
            or _settings["embed_model_kwargs"] != previous["embed_model_kwargs"]
        ):
            from python.helpers.memory import reload as memory_reload
            memory_reload()

        if not previous or _settings["mcp_servers"] != previous["mcp_servers"]:
            from python.helpers.mcp_handler import MCPConfig

            async def update_mcp_settings(mcp_servers: str):
                PrintStyle(background_color="black", font_color="white", padding=True).print("Updating MCP config...")
                AgentContext.log_to_all(type="info", content="Updating MCP settings...", temp=True)

                mcp_config = MCPConfig.get_instance()
                try:
                    await MCPConfig.update(mcp_servers)
                except Exception as e:
                    AgentContext.log_to_all(type="error", content=f"Failed to initialize Alex project: {e}", temp=False)
                    PrintStyle(background_color="red", font_color="black", padding=True).print("Failed to update MCP settings")
                    PrintStyle(background_color="black", font_color="red", padding=True).print(f"{e}")

                PrintStyle(background_color="#6734C3", font_color="white", padding=True).print("Parsed MCP config:")
                PrintStyle(background_color="#334455", font_color="white", padding=False).print(mcp_config.model_dump_json())
                AgentContext.log_to_all(type="info", content="Finished updating MCP settings.", temp=True)

            defer.DeferredTask().start_task(update_mcp_settings, config.mcp_servers)

        current_token = create_auth_token()
        if not previous or current_token != previous["mcp_server_token"]:
            async def update_mcp_token(token: str):
                try:
                    from python.helpers.mcp_server import DynamicMcpProxy
                    DynamicMcpProxy.get_instance().reconfigure(token=token)
                except (ImportError, AttributeError):
                    pass  # DynamicMcpProxy not yet implemented

            defer.DeferredTask().start_task(update_mcp_token, current_token)

        if not previous or current_token != previous["mcp_server_token"]:
            async def update_a2a_token(token: str):
                from python.helpers.fasta2a_server import DynamicA2AProxy
                DynamicA2AProxy.get_instance().reconfigure(token=token)

            defer.DeferredTask().start_task(update_a2a_token, current_token)


def set_root_password(password: str):
    if not runtime.is_dockerized():
        raise Exception("root password can only be set in dockerized environments")
    subprocess.run(["chpasswd"], input=f"root:{password}".encode(), capture_output=True, check=True)
    
    success = False
    possible_paths = [Path("/agix/data/root_password"), Path("/agix/data/root_password"), Path("data/root_password")]
    for p in possible_paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(password)
            success = True
            break
        except OSError:
            pass
            
    try:
        dotenv.save_dotenv_value(dotenv.KEY_ROOT_PASSWORD, password)
    except Exception:
        if not success:
            print(f"Warning: Failed to persist root password to any location.")


def set_rfc_password(password: str):
    success = False
    possible_paths = [Path("/agix/data/rfc_password"), Path("/agix/data/rfc_password"), Path("data/rfc_password")]
    for p in possible_paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(password)
            success = True
            break
        except OSError:
            pass
            
    try:
        dotenv.save_dotenv_value(dotenv.KEY_RFC_PASSWORD, password)
    except Exception:
        if not success:
            print(f"Warning: Failed to persist RFC password to any location.")


def get_runtime_config(set: Settings):
    if runtime.is_dockerized():
        return {
            "code_exec_ssh_enabled": set["shell_interface"] == "ssh",
            "code_exec_ssh_addr": "localhost",
            "code_exec_ssh_port": 22,
            "code_exec_ssh_user": "root",
        }
    else:
        host = set["rfc_url"]
        if "//" in host:
            host = host.split("//")[1]
        if ":" in host:
            host, port = host.split(":")
        if host.endswith("/"):
            host = host[:-1]
        return {
            "code_exec_ssh_enabled": set["shell_interface"] == "ssh",
            "code_exec_ssh_addr": host,
            "code_exec_ssh_port": set["rfc_port_ssh"],
            "code_exec_ssh_user": "root",
        }


def create_auth_token() -> str:
    runtime_id = runtime.get_persistent_id()
    username = dotenv.get_dotenv_value(dotenv.KEY_AUTH_LOGIN) or ""
    password = dotenv.get_dotenv_value(dotenv.KEY_AUTH_PASSWORD) or ""
    hash_bytes = hashlib.sha256(f"{runtime_id}:{username}:{password}".encode()).digest()
    b64_token = base64.urlsafe_b64encode(hash_bytes).decode().replace("=", "")
    return b64_token[:16]


# _get_version extracted to python.helpers.settings_defaults

