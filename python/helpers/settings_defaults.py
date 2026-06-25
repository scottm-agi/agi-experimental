"""
Settings Defaults and Presets for AGIX

Extracted from settings.py per Issue #778 (line audit).
Contains get_default_settings() and get_preset_template() functions
which together account for ~440 lines.
"""
from __future__ import annotations

import json
import os
from typing import Any

from python.helpers import runtime, files
from python.helpers import git_helper


# =============================================================================
# Default Model IDs (Centralized constants to avoid hardcoding)
# =============================================================================
MODELS_DEFAULT_CORE = "google/gemini-3-flash-preview"
MODELS_DEFAULT_UTIL = "google/gemini-3-flash-preview"
MODELS_DEFAULT_IMAGE_OPENROUTER = "google/gemini-3.1-flash-image-preview"
MODELS_DEFAULT_IMAGE_GEMINI = "gemini-3.1-flash-image-preview"
MODELS_DEFAULT_EMBED = "sentence-transformers/all-MiniLM-L6-v2"
MODELS_DEFAULT_EMBED_VENICE = "openai-text-embedding-bge-m3"
MODELS_DEFAULT_CLAUDE = "anthropic/claude-sonnet-4.6"
MODELS_DEFAULT_UNCENSORED = "venice-uncensored"
MODELS_DEFAULT_GROK = "openai/grok-41-fast"


def _detect_bedrock_available() -> bool:
    try:
        import boto3
    except Exception:
        return False

    aws_bedrock_indicators = (
        "API_KEY_BEDROCK",
        "AWS_ACCESS_KEY_ID",
        "AWS_ACCESS_KEY",
    )
    return any(os.getenv(v) for v in aws_bedrock_indicators)


def _get_default_model_providers() -> dict[str, str]:
    if _detect_bedrock_available():
        return {
            "chat": "bedrock",
            "util": "bedrock",
            "browser": "bedrock",
            "supervisor": "bedrock",
            "embed": "bedrock",
        }

    return {
        "chat": "openrouter",
        "util": "openrouter",
        "browser": "openrouter",
        "supervisor": "openrouter",
        "embed": "huggingface",
    }


def _get_version():
    return git_helper.get_version()


def get_default_settings():
    """Return the full default settings dict."""
    providers = _get_default_model_providers()

    chat_model_name = os.getenv("CHAT_MODEL_NAME", MODELS_DEFAULT_CORE)
    util_model_name = os.getenv("UTIL_MODEL_NAME", MODELS_DEFAULT_UTIL)
    
    chat_ctx_length = int(os.getenv("CHAT_MODEL_CTX_LENGTH", "1000000"))
    util_ctx_length = int(os.getenv("UTIL_MODEL_CTX_LENGTH", "1000000"))
    global_ctx_length = int(os.getenv("GLOBAL_MODEL_CTX_LENGTH", "1000000"))

    embed_provider = providers["embed"]
    embed_model_name = MODELS_DEFAULT_EMBED
    if embed_provider == "venice":
        embed_model_name = MODELS_DEFAULT_EMBED_VENICE

    # SUPERVISOR CONFIG
    supervisor_ignore_task_contexts = os.getenv("SUPERVISOR_IGNORE_TASK_CONTEXTS", "false").lower() in ("true", "1", "yes")

    # Import Settings type locally to avoid circular imports
    from python.helpers.settings import Settings

    return Settings(
        version=_get_version(),
        chat_model_provider=providers["chat"],
        chat_model_name=chat_model_name,
        chat_model_api_base="",
        chat_model_kwargs={"temperature": "0"},
        chat_model_ctx_length=chat_ctx_length,
        chat_model_ctx_history=0.7,
        chat_model_vision=True,
        chat_model_rl_requests=0,
        chat_model_rl_input=0,
        chat_model_rl_output=0,
        chat_model_max_tokens=4096,
        chat_model_thinking=False,
        chat_model_thinking_tokens=1024,
        util_model_provider=providers["util"],
        util_model_name=util_model_name,
        util_model_api_base="",
        util_model_ctx_length=util_ctx_length,
        util_model_ctx_input=0.7,
        util_model_kwargs={"temperature": "0"},
        util_model_rl_requests=0,
        util_model_rl_input=0,
        util_model_rl_output=0,
        util_model_max_tokens=4096,
        util_model_thinking=False,
        util_model_thinking_tokens=1024,
        embed_model_provider=embed_provider,
        embed_model_name=embed_model_name,
        embed_model_api_base="",
        embed_model_kwargs={},
        embed_model_rl_requests=0,
        embed_model_rl_input=0,
        browser_model_provider=providers["browser"],
        browser_model_name=MODELS_DEFAULT_CORE,
        browser_model_api_base="",
        browser_model_vision=True,
        browser_model_ctx_length=0,
        browser_model_rl_requests=0,
        browser_model_rl_input=0,
        browser_model_rl_output=0,
        browser_model_max_tokens=4096,
        browser_model_thinking=False,
        browser_model_thinking_tokens=1024,
        browser_agent_max_steps=50,
        browser_agent_timeout_seconds=300,
        browser_agent_screenshot_timeout=25000,
        browser_model_kwargs={"temperature": "0"},
        browser_http_headers={},
        supervisor_model_provider=providers["supervisor"],
        supervisor_model_name=MODELS_DEFAULT_CORE,
        supervisor_model_api_base="",
        supervisor_model_ctx_length=0,
        supervisor_model_kwargs={"temperature": "0"},
        supervisor_model_rl_requests=0,
        supervisor_model_rl_input=0,
        supervisor_model_rl_output=0,
        supervisor_model_max_tokens=4096,
        supervisor_model_thinking=False,
        supervisor_model_thinking_tokens=1024,
        agent_history_max_turns=20,
        memory_recall_enabled=True,
        memory_recall_delayed=False,
        memory_recall_interval=3,
        memory_recall_history_len=10000,
        memory_recall_memories_max_search=12,
        memory_recall_solutions_max_search=8,
        memory_recall_memories_max_result=5,
        memory_recall_solutions_max_result=3,
        memory_recall_similarity_threshold=0.7,
        memory_recall_query_prep=True,
        memory_recall_post_filter=True,
        memory_memorize_enabled=True,
        memory_memorize_consolidation=True,
        memory_memorize_replace_threshold=0.9,
        context_condense_threshold=0.72,
        supervisor_intervention_timeout_seconds=90,
        api_keys={},
        perplexity_api_key="",
        context7_api_key="",
        auth_login="",
        auth_password="",
        root_password="",
        privacy_mode=True,
        simple_chat=False,
        prompt_enhancement=False,
        llm_cache_enabled=True,
        token_tracking_enabled=True,
        budget_max_tokens_per_day=0,
        budget_reset_interval="day",
        grok_fallback_enabled=False,
        ollama_fallback_enabled=False,  # Disabled by default per code-level mandate
        agent_profile="default",
        browser_agent_profile="browser",
        agent_profile_to_edit="alex",
        agent_profiles_enabled=True,
        agent_memory_subdir="default",
        agent_knowledge_subdir="custom",
        rfc_auto_docker=True,
        rfc_url="localhost",
        rfc_password="",
        tasks_enabled=False,
        supervisor_ignore_task_contexts=supervisor_ignore_task_contexts,
        personalization_enabled=True,
        personalized_reply=True,
        personalization_analysis_interval=3,
        global_model_enabled=True,
        global_model_provider="openrouter",
        global_model_name=MODELS_DEFAULT_CORE,
        global_model_ctx_length=global_ctx_length,
        global_model_max_tokens=32768,
        global_model_thinking=False,
        global_model_thinking_tokens=1024,
        ui_tooltips_enabled=True,
        show_background_updates=False,
        agent_trace_to_context=False,
        image_gen_provider="openrouter",
        image_gen_model="google/gemini-3.1-flash-image-preview",
        event_hooks_enabled=True,
        event_hooks_auto_project=True,
        event_hooks_repos="",
        event_hooks_workflows=["new_issue_analysis", "comment_response", "build_branch", "integration_merge", "health_monitoring", "expert_analysis", "deploy_to_cloud"],
        event_hooks_command_triggers={
            r'@?with(?:ai|agi)\s+build\s+branch': "build_branch",
            r'@?with(?:ai|agi)\s+merge': "integration_merge",
            r'@?with(?:ai|agi)\s+health\s+check': "health_monitoring",
            r'@?with(?:ai|agi)\s+expert\s+analysis': "expert_analysis",
            r'@?with(?:ai|agi)\s+deploy\s+railway': "deploy_to_cloud"
        },
        event_hooks_prompt_templates={
            "new_issue_analysis": "A new technical issue has been opened on {platform_name} as issue #{issue_number}.\n[METADATA] {metadata_str}\n\nTitle: {title}\nBody: {body}\n\nPlease use repository_automation with action='analyze_issue' and the EXACT parameters below:\n```json\n{{\n  \"action\": \"analyze_issue\",\n  \"provider\": \"{provider}\",\n  \"owner\": \"{owner}\",\n  \"repo\": \"{repo}\",\n  \"issue_number\": {issue_number},\n  \"auto_comment\": true\n}}\n```\n\nCRITICAL: You MUST include owner='{owner}' and repo='{repo}' in your tool call. Do NOT use default values.",
            "comment_response": "A new comment was posted on {platform_name} issue #{issue_number} that may need a response.\n[METADATA] {metadata_str}\n\nRepository: {owner}/{repo}\nPlatform: {platform_name}\nComment excerpt: {comment_body}\n\nPlease use repository_automation with action='answer_comment' and the EXACT parameters below:\n```json\n{{\n  \"action\": \"answer_comment\",\n  \"provider\": \"{provider}\",\n  \"owner\": \"{owner}\",\n  \"repo\": \"{repo}\",\n  \"issue_number\": {issue_number}\n}}\n```\n\nCRITICAL: ONLY use the provided tool call. Do NOT perform any additional research, searches, or follow-ups unless the tool fails. Your goal is to respond to the comment efficiently.\nCRITICAL: You MUST include owner='{owner}' and repo='{repo}' in your tool call. Do NOT use default values.",
            "build_branch": "A build request has been detected for {platform_name} issue #{issue_number}.\n[METADATA] {metadata_str}\n\nRepository: {owner}/{repo}\nPlatform: {platform_name}\n\nPlease use repository_automation with action='trigger_build_task' to initiate the build process.",
            "integration_merge": "An integration merge request has been detected for {platform_name} issue #{issue_number}.\n[METADATA] {metadata_str}\n\nRepository: {owner}/{repo}\nPlatform: {platform_name}\nTrigger: {comment_body}\n\nPlease use repository_automation with action='start_batch' to consolidate approved branches.",
            "expert_analysis": "A request for expert issue analysis has been detected for {platform_name} issue #{issue_number}.\n[METADATA] {metadata_str}\n\nRepository: {owner}/{repo}\nPlatform: {platform_name}\n\nPlease use repository_automation with action='analyze_issue' to provide a professional-grade technical assessment.",
            "deploy_to_cloud": "A cloud deployment request has been detected for {platform_name} issue #{issue_number}.\n[METADATA] {metadata_str}\n\nRepository: {owner}/{repo}\nPlatform: {platform_name}\n\nPlease use repository_automation with action='deploy_to_cloud' to initiate the Railway deployment process.",
            "health_monitoring": "A health monitoring check has been requested for {platform_name} issue #{issue_number}.\n[METADATA] {metadata_str}\n\nRepository: {owner}/{repo}\nPlatform: {platform_name}\n\nPlease use repository_automation with action='monitor_deployment_health' to verify system stability."
        },
        rfc_port_http=80,
        rfc_port_ssh=55022,
        shell_interface="local" if runtime.is_dockerized() else "ssh",
        stt_model_size="base",
        stt_language="en",
        stt_silence_threshold=0.3,
        stt_silence_duration=1000,
        stt_waiting_timeout=2000,
        tts_kokoro=True,
        mcp_sequential_thinking_enabled=True,
        parameters="{}",
        mcp_servers=json.dumps({
            "mcpServers": {
                "sequential-thinking": {
                    "type": "stdio",
                    "command": "mcp-server-sequential-thinking",
                    "args": [],
                    "disabled": False
                },
                "github": {
                    "type": "stdio",
                    "command": "mcp-server-github",
                    "args": [],
                    "env": {
                        "GITHUB_PERSONAL_ACCESS_TOKEN": "§§secret(GITHUB_TOKEN)"
                    },
                    "disabled": False
                },
                "forgejo": {
                    "type": "stdio",
                    "command": "/opt/venv-agix/bin/python3",
                    "args": ["-m", "python.helpers.mcp.forgejo_mcp_server"],
                    "env": {
                        "PYTHONPATH": "/agix",
                        "FORGEJO_TOKEN": "§§secret(FORGEJO_TOKEN)",
                        "FORGEJO_URL": "§§parameter(FORGEJO_URL)"
                    },
                    "disabled": False
                },
                "perplexity-ask": {
                    "type": "stdio",
                    "command": "perplexity-mcp",
                    "args": [],
                    "env": {
                        "PERPLEXITY_API_KEY": "§§secret(PERPLEXITY_API_KEY)"
                    },
                    "disabled": False
                },
                "context7": {
                    "type": "stdio",
                    "command": "context7-mcp",
                    "args": [],
                    "disabled": False
                },
                "google_drive": {
                    "type": "stdio",
                    "command": "google-drive-mcp",
                    "args": [],
                    "env": {
                        "GOOGLE_CHAT_TOKEN": "§secret(GOOGLE_CHAT_TOKEN)"
                    },
                    "disabled": False
                },
                "google-chat": {
                    "type": "stdio",
                    "command": "/opt/venv-agix/bin/python3",
                    "args": ["-m", "python.helpers.mcp.google_chat_mcp_server"],
                    "env": {
                        "PYTHONPATH": "/agix",
                        "GOOGLE_CHAT_TOKEN": "§§secret(GOOGLE_CHAT_TOKEN)"
                    },
                    "disabled": False
                },
                "mcp_server": {
                    "type": "stdio",
                    "command": "/opt/venv-agix/bin/python3",
                    "args": ["-m", "python.helpers.mcp_server"],
                    "env": {
                        "PYTHONPATH": "/agix"
                    },
                    "disabled": False
                },
                "tavily-mcp": {
                    "type": "stdio",
                    "command": "tavily-mcp",
                    "args": [],
                    "env": {
                        "TAVILY_API_KEY": "§§secret(TAVILY_API_KEY)"
                    },
                    "disabled": False
                },
                "alphaxiv": {
                    "disabled": False,
                    "description": "alphaXiv MCP server for academic paper search and research analysis",
                    "url": "https://api.alphaxiv.org/mcp/v1"
                }
            }
        }, indent=4),
        mcp_client_init_timeout=30,
        mcp_client_tool_timeout=600,
        mcp_server_enabled=False, # Disabled by default (Expose AGIX as MCP)
        mcp_server_token="",
        a2a_server_enabled=False, # Disabled by default (Agent-to-Agent)
        variables="",
        secrets="",
        litellm_global_kwargs={},
        update_check_enabled=False,
        update_repo_url=os.getenv("UPDATE_REPO_URL", ""),

        model_configurations=[],
        role_configurations={
            "architect": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CLAUDE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 8096}
            },
            "orchestrator": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CORE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 8096}
            },
            "alex": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CORE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 8096}
            },
            "code": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CORE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 8096}
            },
            "debug": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CLAUDE,
                "ctx_length": 1000000,
                "kwargs": {
                    "temperature": 0,
                    "max_tokens": 8096
                }
            },
            "multiagentdev": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CLAUDE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 8096}
            },
            "frontend": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CORE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 8096}
            },
            "content": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CORE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0.7, "max_tokens": 8096}
            },
            "content-writer": {
                "provider": "openrouter",
                "name": MODELS_DEFAULT_CORE,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0.7, "max_tokens": 32768}
            },
            "simple": {
                "use_chat_model": True
            }
        },
        routing_rules={
            "simple": "simple",
            "code": "code",
            "architecture": "architect",
            "architect": "architect",
            "debug": "debug",
            "frontend": "frontend",
            "content": "content",
            "content-writer": "content-writer",
            "documentation": "content",
            "researcher": "content",
            "hacker": "code",
            "multiagentdev": "multiagentdev",
        },
        model_metadata_cache={},
        performance_tier="standard"
    )


def get_preset_template(name: str) -> dict | None:
    """Return a pre-defined configuration bundle for the given template name."""
    # Import here to avoid circular dependency
    from python.helpers.settings import get_settings_bundle
    
    default = get_default_settings()
    
    if name == "venice_optimized":
        bundle = get_settings_bundle()
        s = bundle["settings"]

        # Ensure ZDR (Zero Data Retention / Privacy Mode) is always enabled
        s["privacy_mode"] = True
        # Default agent profile — system fallback
        s["agent_profile"] = "default"

        # Global models — all on OpenRouter with Gemini Flash 3 Preview
        _flash = "google/gemini-3-flash-preview"
        _opus = "anthropic/claude-opus-4.7"
        _glm = "z-ai/glm-5.1"

        s["chat_model_provider"] = "openrouter"
        s["chat_model_name"] = _flash
        s["chat_model_kwargs"] = {"temperature": "0", "max_tokens": 65536}
        s["util_model_provider"] = "openrouter"
        s["util_model_name"] = _flash
        s["util_model_kwargs"] = {"temperature": "0", "max_tokens": 65536}
        s["browser_model_provider"] = "openrouter"
        s["browser_model_name"] = _flash
        s["browser_model_kwargs"] = {"temperature": "0", "max_tokens": 65536}
        s["supervisor_model_provider"] = "openrouter"
        s["supervisor_model_name"] = _flash
        s["supervisor_model_kwargs"] = {"temperature": "0", "max_tokens": 65536}

        s["role_configurations"] = s.get("role_configurations") or {}

        # Opus 4.7 roles — high-reasoning tasks
        opus_roles = ["multiagentdev", "architect", "debug", "content-writer", "alex", "account-leader"]
        for role in opus_roles:
            s["role_configurations"][role] = {
                "provider": "openrouter",
                "name": _opus,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 65536}
            }

        # Flash 3 Preview roles — high-throughput tasks
        flash_roles = [
            "researcher", "ask", "code", "chat", "simple",
            "default", "dashboard", "e2e", "frontend",
            "marketing-lead", "mcp_builder", "review",
            "sales-enabler", "orchestrator", "content",
            "browser",
        ]
        for role in flash_roles:
            s["role_configurations"][role] = {
                "provider": "openrouter",
                "name": _flash,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 65536}
            }

        # GLM 5.1 roles — security-focused tasks (200k context limit)
        glm_roles = ["hacker", "security_auditor"]
        for role in glm_roles:
            s["role_configurations"][role] = {
                "provider": "openrouter",
                "name": _glm,
                "ctx_length": 200000,
                "kwargs": {"temperature": 0, "max_tokens": 65536}
            }

        if "architecture" in s["role_configurations"]:
            del s["role_configurations"]["architecture"]

        return bundle
    
    elif name == "default_standard":
        bundle = get_settings_bundle()
        bundle["settings"] = get_default_settings()
        return bundle
        
    elif name == "minimal_local":
        bundle = get_settings_bundle()
        s = bundle["settings"]
        # Default agent profile — system fallback
        s["agent_profile"] = "default"
        s["chat_model_provider"] = "ollama"
        s["chat_model_name"] = "llama3.2:3b"
        s["chat_model_kwargs"] = {"temperature": "0", "max_tokens": 2048}
        s["util_model_provider"] = "ollama"
        s["util_model_name"] = "llama3.2:1b"
        s["util_model_kwargs"] = {"temperature": "0", "max_tokens": 1024}
        bundle["settings"]["embed_model_provider"] = "local"
        return bundle
        
    elif name == "single_llm_override":
        bundle = get_settings_bundle()
        s = bundle["settings"]
        # Default agent profile — system fallback
        s["agent_profile"] = "default"
        s["global_model_enabled"] = True
        s["chat_model_kwargs"] = s.get("chat_model_kwargs", {})
        s["chat_model_kwargs"]["max_tokens"] = 16384
        s["util_model_kwargs"] = s.get("util_model_kwargs", {})
        s["util_model_kwargs"]["max_tokens"] = 8192
        s["browser_model_kwargs"] = s.get("browser_model_kwargs", {})
        s["browser_model_kwargs"]["max_tokens"] = 16384
        s["supervisor_model_kwargs"] = s.get("supervisor_model_kwargs", {})
        s["supervisor_model_kwargs"]["max_tokens"] = 8192
        return bundle
        
    elif name == "max_performance":
        bundle = get_settings_bundle()
        s = bundle["settings"]
        s["performance_tier"] = "max"
        s["agent_profiles_enabled"] = True
        # Default agent profile — system fallback (multiagentdev is invoked explicitly, not as default)
        s["agent_profile"] = "default"
        
        # Ensure key agents are on OpenRouter for max performance
        s["role_configurations"] = s.get("role_configurations") or {}
        
        _opus = "anthropic/claude-opus-4.6"
        _gemini = "google/gemini-3-pro-preview"
        _flash = "google/gemini-3-flash-preview"
        
        # Tier 1 — Opus for high-reasoning dev roles
        for role in ["architect", "code", "debug", "multiagentdev", "orchestrator",
                     "alex", "account-leader", "content-writer"]:
            s["role_configurations"][role] = {
                "provider": "openrouter",
                "name": _opus,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 65536}
            }
        
        # Tier 2 — Gemini Pro for visual/frontend work
        for role in ["frontend", "e2e", "browser", "dashboard"]:
            s["role_configurations"][role] = {
                "provider": "openrouter",
                "name": _gemini,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 65536}
            }
        
        # Tier 3 — Flash for standard throughput roles
        for role in ["researcher", "ask", "review", "marketing-lead",
                     "sales-enabler", "mcp_builder", "hacker",
                     "security_auditor", "default", "chat", "simple", "content"]:
            s["role_configurations"][role] = {
                "provider": "openrouter",
                "name": _flash,
                "ctx_length": 1000000,
                "kwargs": {"temperature": 0, "max_tokens": 65536}
            }
        
        # Routing rules cleanup (ensure they point to the right roles)
        s["routing_rules"] = s.get("routing_rules") or {}
        s["routing_rules"]["frontend"] = "frontend"
        s["routing_rules"]["multiagentdev"] = "multiagentdev"
        
        return bundle
        
    return None
