"""
External services settings section builders.

Phase 3 of settings.py modularization.
Extracts auth, API keys, LiteLLM, secrets, parameters, external API, update checker, and webhooks.
"""

import json
import os
from typing import Any, cast

from python.helpers import runtime, dotenv_manager as dotenv
from python.helpers.providers import get_providers
from python.helpers.secrets_helper import get_default_secrets_manager
from python.helpers.parameters import get_parameters_manager

from .base import (
    SettingsField,
    SettingsSection,
    FieldOption,
    SectionBuilderContext,
    PASSWORD_PLACEHOLDER,
    API_KEY_PLACEHOLDER,
    dict_to_env,
)


def build_auth_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Authentication settings section."""
    
    auth_fields: list[SettingsField] = []

    auth_fields.append(
        {
            "id": "auth_login",
            "title": "UI Login",
            "description": "Set user name for web UI",
            "type": "text",
            "value": dotenv.get_dotenv_value(dotenv.KEY_AUTH_LOGIN) or "",
        }
    )

    auth_fields.append(
        {
            "id": "auth_password",
            "title": "UI Password",
            "description": "Set user password for web UI",
            "type": "password",
            "value": (
                PASSWORD_PLACEHOLDER
                if dotenv.get_dotenv_value(dotenv.KEY_AUTH_PASSWORD)
                else ""
            ),
        }
    )

    if runtime.is_dockerized():
        auth_fields.append(
            {
                "id": "root_password",
                "title": "root Password",
                "description": "Change linux root password in docker container. This password can be used for SSH access. Original password was randomly generated during setup.",
                "type": "password",
                "value": "",
            }
        )

    return {
        "id": "auth",
        "title": "Authentication",
        "description": "Settings for authentication to use AGIX Web UI.",
        "fields": auth_fields,
        "tab": "external",
    }


def _get_api_key_field(settings: dict, provider: str, title: str) -> SettingsField:
    """Helper to create an API key field for a provider."""
    import python.models as models
    key = settings.get("api_keys", {}).get(provider, models.get_api_key(provider))
    return {
        "id": f"api_key_{provider}",
        "title": title,
        "type": "password",
        "isSecret": True,
        "value": (API_KEY_PLACEHOLDER if key and key != "None" else ""),
    }


def build_api_keys_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the API Keys settings section."""
    settings = ctx.settings
    
    api_keys_fields: list[SettingsField] = []

    # Collect unique providers from both chat and embedding sections
    providers_seen: set[str] = set()
    for p_type in ("chat", "embedding"):
        for provider in get_providers(p_type):
            pid_lower = provider["value"].lower()
            if pid_lower in providers_seen:
                continue
            providers_seen.add(pid_lower)
            api_keys_fields.append(
                _get_api_key_field(settings, pid_lower, provider["label"])
            )

    return {
        "id": "api_keys",
        "title": "API Keys",
        "description": "API keys for model providers and services used by AGIX. You can set multiple API keys separated by a comma (,). They will be used in round-robin fashion.<br>For more information about AGIX Venice provider, see <a href='http://example.com/?community/api-dashboard/about' target='_blank'>AGIX Venice</a>.",
        "fields": api_keys_fields,
        "tab": "external",
    }


def build_litellm_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the LiteLLM Global Settings section."""
    settings = ctx.settings
    
    litellm_fields: list[SettingsField] = []

    litellm_fields.append(
        {
            "id": "litellm_global_kwargs",
            "title": "LiteLLM global parameters",
            "description": "Global LiteLLM params (e.g. timeout, stream_timeout) in .env format: one KEY=VALUE per line. Example: <code>stream_timeout=30</code>. Applied to all LiteLLM calls unless overridden. See <a href='https://docs.litellm.ai/docs/set_keys' target='_blank'>LiteLLM</a> and <a href='https://docs.litellm.ai/docs/proxy/timeout' target='_blank'>timeouts</a>.",
            "type": "textarea",
            "value": dict_to_env(settings["litellm_global_kwargs"]),
            "style": "height: 12em",
        }
    )

    return {
        "id": "litellm",
        "title": "LiteLLM Global Settings",
        "description": "Configure global parameters passed to LiteLLM for all providers.",
        "fields": litellm_fields,
        "tab": "external",
    }


def build_secrets_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Secrets Management settings section."""
    
    secrets_fields: list[SettingsField] = []

    secrets_manager = get_default_secrets_manager()
    try:
        secrets = secrets_manager.get_masked_secrets()
    except Exception:
        secrets = ""

    secrets_fields.append({
        "id": "secrets",
        "title": "Secrets Store",
        "description": "Store secrets and credentials in .env format e.g. EMAIL_PASSWORD=\"s3cret-p4$$w0rd\", one item per line. You can use comments starting with # to add descriptions for the agent. See <a href=\"javascript:openModal('settings/secrets/example-secrets.html')\">example</a>.<br>These variables are not visile to LLMs and in chat history, they are being masked. ⚠️ only values with length >= 4 are being masked to prevent false positives. ",
        "type": "kvp",
        "format": "env",
        "isSecret": True,
        "value": secrets,
        "style": "height: 20em",
    })

    return {
        "id": "secrets",
        "title": "Secrets Management",
        "description": "Manage secrets and credentials that agents can use without exposing values to LLMs, chat history or logs. Placeholders are automatically replaced with values just before tool calls. If bare passwords occur in tool results, they are masked back to placeholders.",
        "fields": secrets_fields,
        "tab": "external",
    }


def build_parameters_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Global Parameters settings section."""
    
    parameters_fields: list[SettingsField] = []
    
    try:
        current_params = get_parameters_manager().load_parameters()
        params_json = json.dumps(current_params, indent=4)
    except Exception:
        params_json = "{}"

    parameters_fields.append({
        "id": "parameters",
        "title": "Global Parameters",
        "description": "Store global deterministic parameters in JSON format. These are accessible to agents via <code>parameter_get</code> and <code>parameter_set</code> tools. Project-specific parameters will override these values.",
        "type": "kvp",
        "format": "json",
        "isSecret": False,
        "value": params_json,
        "style": "height: 20em",
    })

    return {
        "id": "parameters",
        "title": "Global Parameters",
        "description": "Manage global configuration and state parameters that agents can read and write deterministically.",
        "fields": parameters_fields,
        "tab": "external",
    }


def build_external_api_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the External API settings section."""
    
    external_api_fields: list[SettingsField] = []

    external_api_fields.append(
        {
            "id": "external_api_examples",
            "title": "API Examples",
            "description": "View examples for using AGIX's external API endpoints with API key authentication.",
            "type": "button",
            "value": "Show API Examples",
        }
    )

    return {
        "id": "external_api",
        "title": "External API",
        "description": "AGIX provides external API endpoints for integration with other applications. "
                       "These endpoints use API key authentication and support text messages and file attachments.",
        "fields": external_api_fields,
        "tab": "external",
    }


def build_update_checker_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Update Checker settings section."""
    settings = ctx.settings
    
    update_checker_fields: list[SettingsField] = []

    update_checker_fields.append(
        {
            "id": "update_check_enabled",
            "title": "Enable Update Checker",
            "description": "Enable update checker to notify about newer versions of AGIX.",
            "type": "switch",
            "value": settings["update_check_enabled"],
        }
    )

    update_checker_fields.append(
        {
            "id": "update_repo_url",
            "title": "Update Repository URL",
            "description": "Git repository URL for application updates.",
            "type": "text",
            "value": settings["update_repo_url"],
        }
    )

    update_checker_fields.append(
        {
            "id": "system_update",
            "title": "Update System",
            "description": "Click to pull latest changes from the Forgejo repository (main branch) and restart the application.<br><b>Warning: This will overwrite any local changes to core files!</b>",
            "type": "button",
            "value": "Update",
        }
    )

    return {
        "id": "update_checker",
        "title": "Update Checker",
        "description": "Update checker periodically checks for new releases of AGIX and will notify when an update is recommended.<br>No personal data is sent to the update server, only randomized+anonymized unique ID and current version number, which help us evaluate the importance of the update in case of critical bug fixes etc.",
        "fields": update_checker_fields,
        "tab": "external",
    }


def build_event_hooks_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Event Hooks (GitHub/Forgejo webhooks) settings section."""
    settings = ctx.settings
    
    event_hooks_fields: list[SettingsField] = []

    # Pre-configuration steps (expandable guide)
    event_hooks_fields.append({
        "id": "event_hooks_setup_guide",
        "title": "Pre-Configuration Steps",
        "description": "Expand to view setup instructions for webhooks",
        "type": "html",
        "value": """
<details style="margin-top: 8px; padding: 12px; background: rgba(255,255,255,0.05); border-radius: 8px; cursor: pointer;">
<summary style="font-weight: 600; margin-bottom: 8px;">📋 Setup Guide (click to expand)</summary>
<div style="margin-top: 12px; font-size: 13px; line-height: 1.6;">

<h4 style="margin: 12px 0 8px 0; color: #9ca3af;">Step 1: Expose Webhook Endpoint</h4>
<p>AGIX listens on <code>/webhook/github</code>. You need a public URL:</p>
<ul style="margin: 8px 0; padding-left: 20px;">
<li><b>Development (ngrok):</b> <code>ngrok http https://localhost:8880 --host-header=localhost</code></li>
</ul>

<h4 style="margin: 12px 0 8px 0; color: #9ca3af;">Step 2: Generate Webhook Secret</h4>
<p>Create a secure secret: <code>openssl rand -hex 32</code></p>
<p>Add to <b>Secrets Management</b>: <code>GITHUB_WEBHOOK_SECRET=your_secret_here</code></p>

<h4 style="margin: 12px 0 8px 0; color: #9ca3af;">Step 3: Configure GitHub/Forgejo</h4>
<ol style="margin: 8px 0; padding-left: 20px;">
<li>Go to Repository → Settings → Webhooks → Add webhook</li>
<li><b>Payload URL:</b> <code>https://your-domain.com/webhook/github</code></li>
<li><b>Content type:</b> application/json</li>
<li><b>Secret:</b> Same secret from Step 2</li>
<li><b>Events:</b> Issues, Issue comments (or "Send me everything")</li>
</ol>

<h4 style="margin: 12px 0 8px 0; color: #9ca3af;">Step 4: Test</h4>
<p>Create a test issue → check Docker logs for <code>[WEBHOOK]</code> messages</p>

</div>
</details>
""",
    })

    event_hooks_fields.append({
        "id": "event_hooks_enabled",
        "title": "Enable Event Hooks",
        "description": "Enable processing of GitHub/Forgejo webhook events. When enabled, webhooks trigger agent workflows automatically.",
        "type": "switch",
        "value": settings.get("event_hooks_enabled", True),
    })

    event_hooks_fields.append({
        "id": "event_hooks_auto_project",
        "title": "Auto-Create Projects",
        "description": "Automatically discover or create agix projects for repositories that trigger webhooks.",
        "type": "switch",
        "value": settings.get("event_hooks_auto_project", True),
    })

    event_hooks_fields.append({
        "id": "event_hooks_repos",
        "title": "Allowed Repositories",
        "description": "List of allowed repositories (owner/repo format, one per line). Leave empty to allow all repositories.",
        "type": "textarea",
        "value": settings.get("event_hooks_repos", ""),
        "style": "height: 8em",
    })

    # Workflow checkboxes as comma-separated text
    current_workflows = settings.get("event_hooks_workflows", ["new_issue_analysis", "comment_response", "build_branch"])
    event_hooks_fields.append({
        "id": "event_hooks_workflows",
        "title": "Enabled Workflows",
        "description": "Comma-separated list of enabled workflows.",
        "type": "text",
        "value": ",".join(current_workflows) if isinstance(current_workflows, list) else current_workflows,
    })

    event_hooks_fields.append({
        "id": "event_hooks_command_triggers",
        "title": "Command Triggers (JSON)",
        "description": "Map of regex patterns to workflow names (e.g., <code>{\"deploy\": \"deploy_to_cloud\"}</code>). Regex matches against comment text.",
        "type": "kvp",
        "format": "json",
        "value": json.dumps(settings.get("event_hooks_command_triggers", {}), indent=4),
        "style": "height: 12em",
    })

    event_hooks_fields.append({
        "id": "event_hooks_prompt_templates",
        "title": "Prompt Templates (JSON)",
        "description": "Map of workflow IDs to specialized prompt templates. Supports format placeholders: <code>{platform_name}, {issue_number}, {owner}, {repo}, {metadata_str}</code>, etc.",
        "type": "kvp",
        "format": "json",
        "value": json.dumps(settings.get("event_hooks_prompt_templates", {}), indent=4),
        "style": "height: 20em",
    })

    return {
        "id": "event_hooks",
        "title": "Event Hooks",
        "description": "Configure GitHub/Forgejo webhook integrations for automated agent triggers. Webhook secret is stored in Secrets Management.",
        "fields": event_hooks_fields,
        "tab": "external",
    }


def build_image_gen_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Image Generation settings section."""
    settings = ctx.settings

    image_gen_fields: list[SettingsField] = []

    image_gen_fields.append({
        "id": "image_gen_provider",
        "title": "Image Generation Provider",
        "description": "Select the backend provider for image generation. "
                       "<b>OpenRouter</b> uses Gemini Nano Banana via OpenRouter API (recommended). "
                       "<b>Gemini Direct</b> uses the google-genai SDK directly. "
                       "<b>OpenAI/DALL-E</b> uses LiteLLM for DALL-E models.",
        "type": "select",
        "value": settings.get("image_gen_provider", "openrouter"),
        "options": [
            {"value": "openrouter", "label": "OpenRouter (Gemini Nano Banana)"},
            {"value": "gemini", "label": "Gemini Direct (google-genai SDK)"},
            {"value": "openai", "label": "OpenAI / DALL-E (LiteLLM)"},
        ],
    })

    image_gen_fields.append({
        "id": "image_gen_model",
        "title": "Image Generation Model",
        "description": "Model ID for image generation. "
                       "OpenRouter: <code>google/gemini-3.1-flash-image-preview</code>. "
                       "Gemini Direct: <code>gemini-3.1-flash-image-preview</code>. ",
        "type": "text",
        "value": settings.get("image_gen_model", "google/gemini-3.1-flash-image-preview"),
    })

    return {
        "id": "image_gen",
        "title": "Image Generation",
        "description": "Configure the AI image generation provider and model used by the <code>generate_image</code> tool. "
                       "Uses Google Gemini 3.1 Flash (<code>google/gemini-3.1-flash-image-preview</code>) via OpenRouter by default. "
                       "Settings keys: <code>image_gen_provider</code>, <code>image_gen_model</code>.",
        "fields": image_gen_fields,
        "tab": "agent",
    }


def build_backup_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Backup & Restore settings section."""
    
    backup_fields: list[SettingsField] = []

    backup_fields.append(
        {
            "id": "backup_create",
            "title": "Create Backup",
            "description": "Create a backup archive of selected files and configurations "
            "using customizable patterns.",
            "type": "button",
            "value": "Create Backup",
        }
    )

    backup_fields.append(
        {
            "id": "backup_restore",
            "title": "Restore from Backup",
            "description": "Restore files and configurations from a backup archive "
            "with pattern-based selection.",
            "type": "button",
            "value": "Restore Backup",
        }
    )
    
    return {
        "id": "backup_restore",
        "title": "Backup & Restore",
        "description": "Backup and restore AGIX data and configurations "
        "using glob pattern-based file selection.",
        "fields": backup_fields,
        "tab": "backup",
    }