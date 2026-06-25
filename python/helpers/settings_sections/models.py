"""
Model section builders for settings UI.

Extracts the model configuration sections from the main convert_out() function:
- Chat Model
- Global Model Override
- Utility Model
- Embedding Model
- Browser Model
- Supervisor Model
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .base import (
    SettingsField,
    SettingsSection,
    FieldOption,
    SectionBuilderContext,
    dict_to_env,
    get_providers,
    get_subdirectories,
)

if TYPE_CHECKING:
    from python.helpers.settings import Settings


def build_chat_model_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the chat model configuration section."""
    s_dict = ctx.settings
    
    fields: list[SettingsField] = []
    
    fields.append({
        "id": "chat_model_provider",
        "title": "Chat model provider",
        "description": "Select provider for main chat model used by AGIX",
        "type": "select",
        "value": s_dict["chat_model_provider"],
        "options": cast(list[FieldOption], get_providers("chat")),
    })
    
    fields.append({
        "id": "chat_model_name",
        "title": "Chat model name",
        "description": "Exact name of model from selected provider",
        "type": "text",
        "value": s_dict["chat_model_name"],
    })

    fields.append({
        "id": "chat_model_api_base",
        "title": "Chat model API base URL",
        "description": "API base URL for main chat model. Leave empty for default. Only relevant for Azure, local and custom (other) providers.",
        "type": "text",
        "value": s_dict["chat_model_api_base"],
    })

    fields.append({
        "id": "chat_model_ctx_length",
        "title": "Chat model context length",
        "description": "Maximum number of tokens in the context window for LLM. System prompt, chat history, RAG and response all count towards this limit.",
        "type": "number",
        "value": s_dict["chat_model_ctx_length"],
    })

    fields.append({
        "id": "chat_model_ctx_history",
        "title": "Context window space for chat history",
        "description": "Portion of context window dedicated to chat history visible to the agent. Chat history will automatically be optimized to fit. Smaller size will result in shorter and more summarized history. The remaining space will be used for system prompt, RAG and response.",
        "type": "range",
        "min": 0.01,
        "max": 1,
        "step": 0.01,
        "value": s_dict["chat_model_ctx_history"],
    })

    fields.append({
        "id": "chat_model_vision",
        "title": "Supports Vision",
        "description": "Models capable of Vision can for example natively see the content of image attachments.",
        "type": "switch",
        "value": s_dict["chat_model_vision"],
    })

    fields.append({
        "id": "chat_model_rl_requests",
        "title": "Requests per minute limit",
        "description": "Limits the number of requests per minute to the chat model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["chat_model_rl_requests"],
    })

    fields.append({
        "id": "chat_model_rl_input",
        "title": "Input tokens per minute limit",
        "description": "Limits the number of input tokens per minute to the chat model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["chat_model_rl_input"],
    })

    fields.append({
        "id": "chat_model_rl_output",
        "title": "Output tokens per minute limit",
        "description": "Limits the number of output tokens per minute to the chat model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["chat_model_rl_output"],
    })

    fields.append({
        "id": "chat_model_max_tokens",
        "title": "Max output tokens per request",
        "description": "Maximum number of tokens the chat model can generate in a single response. Use 0 for model default.",
        "type": "number",
        "value": s_dict.get("chat_model_max_tokens", 0),
    })

    fields.append({
        "id": "agent_history_max_turns",
        "title": "Max chat history turns",
        "description": "Maximum number of conversational turns to keep in active memory before pruning older messages. Higher values improve long-term context but increase cost and may lead to context window saturation. Default: 150",
        "type": "number",
        "value": s_dict.get("agent_history_max_turns", 150),
    })

    fields.append({
        "id": "ollama_fallback_enabled",
        "title": "Enable Ollama Fallback",
        "description": "When enabled, Ollama (Qwen) will be used as a last-resort fallback if all other models fail and after a 3 minute delay.",
        "type": "switch",
        "value": s_dict.get("ollama_fallback_enabled", False),
    })

    fields.append({
        "id": "chat_model_thinking",
        "title": "Enable Thinking",
        "description": "Enable reasoning/thinking features for supported models (e.g. Anthropic 3.7+, OpenAI o1/o3).",
        "type": "switch",
        "value": s_dict.get("chat_model_thinking", False),
    })
    
    fields.append({
        "id": "chat_model_thinking_tokens",
        "title": "Thinking Token Budget",
        "description": "Maximum number of tokens dedicated to internal thinking/reasoning. Only used if Thinking is enabled.",
        "type": "number",
        "value": s_dict.get("chat_model_thinking_tokens", 1024),
    })
    
    fields.append({
        "id": "chat_model_kwargs",
        "title": "Chat model additional parameters",
        "description": "Any other parameters supported by <a href='https://docs.litellm.ai/docs/set_keys' target='_blank'>LiteLLM</a>. Format is KEY=VALUE on individual lines, like .env file. Value can also contain JSON objects - when unquoted, it is treated as object, number etc., when quoted, it is treated as string.",
        "type": "textarea",
        "value": dict_to_env(s_dict["chat_model_kwargs"]),
    })

    return {
        "id": "chat_model",
        "title": "Chat Model",
        "description": "Selection and settings for main chat model used by AGIX",
        "fields": fields,
        "tab": "agent",
    }


def build_global_model_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the unified/global model override section."""
    from python.helpers import settings as settings_module
    s_dict = ctx.settings
    
    fields: list[SettingsField] = []
    
    fields.append({
        "id": "global_model_enabled",
        "title": "Enable Global Model Override",
        "description": "When enabled, all agents (Chat, Utility, Browser, Supervisor) will use this single model configuration. Embeddings are NOT affected.",
        "type": "switch",
        "value": s_dict.get("global_model_enabled", False),
    })
    
    fields.append({
        "id": "global_model_provider",
        "title": "Global model provider",
        "description": "Select provider for the global model override",
        "type": "select",
        "value": s_dict.get("global_model_provider", "venice"),
        "options": cast(list[FieldOption], get_providers("chat")),
    })
    
    fields.append({
        "id": "global_model_name",
        "title": "Global model name",
        "description": "Exact name of model for global override",
        "type": "text",
        "value": s_dict.get("global_model_name", settings_module.MODELS_DEFAULT_GROK),
    })
    
    fields.append({
        "id": "global_model_ctx_length",
        "title": "Global model context length",
        "description": "Maximum tokens in the context window for all models when override is active.",
        "type": "number",
        "value": s_dict.get("global_model_ctx_length", 0),
    })

    fields.append({
        "id": "global_model_max_tokens",
        "title": "Global model max tokens",
        "description": "Maximum tokens in the output for all models when override is active. Prevents truncated responses. Set to 0 for system default.",
        "type": "number",
        "value": s_dict.get("global_model_max_tokens", 16384),
    })
    
    fields.append({
        "id": "global_model_thinking",
        "title": "Global Enable Thinking",
        "description": "Enable thinking for all models when override is active.",
        "type": "switch",
        "value": s_dict.get("global_model_thinking", False),
    })
    
    fields.append({
        "id": "global_model_thinking_tokens",
        "title": "Global Thinking Token Budget",
        "description": "Token budget for internal thinking across all models when override is active.",
        "type": "number",
        "value": s_dict.get("global_model_thinking_tokens", 1024),
    })

    return {
        "id": "global_model_override",
        "title": "Unified Configuration",
        "description": "Force a single model for all roles across the entire system. Helpful for quickly switching everything to a specific model.",
        "fields": fields,
        "tab": "agent",
    }


def build_util_model_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the utility model configuration section."""
    s_dict = ctx.settings
    
    fields: list[SettingsField] = []
    
    fields.append({
        "id": "util_model_provider",
        "title": "Utility model provider",
        "description": "Select provider for utility model used by the framework",
        "type": "select",
        "value": s_dict["util_model_provider"],
        "options": cast(list[FieldOption], get_providers("chat")),
    })
    
    fields.append({
        "id": "util_model_name",
        "title": "Utility model name",
        "description": "Exact name of model from selected provider",
        "type": "text",
        "value": s_dict["util_model_name"],
    })

    fields.append({
        "id": "util_model_api_base",
        "title": "Utility model API base URL",
        "description": "API base URL for utility model. Leave empty for default. Only relevant for Azure, local and custom (other) providers.",
        "type": "text",
        "value": s_dict["util_model_api_base"],
    })

    fields.append({
        "id": "util_model_ctx_length",
        "title": "Utility model context length",
        "description": "Maximum number of tokens in the context window for utility model.",
        "type": "number",
        "value": s_dict["util_model_ctx_length"],
    })

    fields.append({
        "id": "util_model_max_tokens",
        "title": "Utility model max tokens",
        "description": "Maximum tokens in the output for utility model. Prevents truncated responses. Set to 0 for system default.",
        "type": "number",
        "value": s_dict["util_model_max_tokens"],
    })

    fields.append({
        "id": "util_model_ctx_input",
        "title": "Context window space for input",
        "description": "Portion of context window dedicated to input for utility tasks.",
        "type": "range",
        "min": 0.01,
        "max": 1,
        "step": 0.01,
        "value": s_dict["util_model_ctx_input"],
    })

    fields.append({
        "id": "util_model_rl_requests",
        "title": "Requests per minute limit",
        "description": "Limits the number of requests per minute to the utility model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["util_model_rl_requests"],
    })

    fields.append({
        "id": "util_model_rl_input",
        "title": "Input tokens per minute limit",
        "description": "Limits the number of input tokens per minute to the utility model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["util_model_rl_input"],
    })

    fields.append({
        "id": "util_model_rl_output",
        "title": "Output tokens per minute limit",
        "description": "Limits the number of output tokens per minute to the utility model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["util_model_rl_output"],
    })

    fields.append({
        "id": "util_model_thinking",
        "title": "Enable Thinking (Utility)",
        "description": "Enable reasoning for utility model tasks.",
        "type": "switch",
        "value": s_dict.get("util_model_thinking", False),
    })
    
    fields.append({
        "id": "util_model_thinking_tokens",
        "title": "Thinking Tokens (Utility)",
        "description": "Thinking token budget for utility tasks.",
        "type": "number",
        "value": s_dict.get("util_model_thinking_tokens", 512),
    })
    
    fields.append({
        "id": "util_model_kwargs",
        "title": "Utility model additional parameters",
        "description": "Any other parameters supported by <a href='https://docs.litellm.ai/docs/set_keys' target='_blank'>LiteLLM</a>. Format is KEY=VALUE on individual lines, like .env file. Value can also contain JSON objects - when unquoted, it is treated as object, number etc., when quoted, it is treated as string.",
        "type": "textarea",
        "value": dict_to_env(s_dict["util_model_kwargs"]),
    })

    return {
        "id": "util_model",
        "title": "Utility model",
        "description": "Smaller, cheaper, faster model for handling utility tasks like organizing memory, preparing prompts, summarizing.",
        "fields": fields,
        "tab": "agent",
    }


def build_embed_model_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the embedding model configuration section."""
    from python.helpers import settings as settings_module
    s_dict = ctx.settings
    default_settings = settings_module.get_default_settings()
    
    fields: list[SettingsField] = []
    
    fields.append({
        "id": "embed_model_provider",
        "title": "Embedding model provider",
        "description": "Select provider for embedding model used by AGIX",
        "type": "select",
        "value": s_dict["embed_model_provider"],
        "options": cast(list[FieldOption], get_providers("embed")),
    })
    
    fields.append({
        "id": "embed_model_name",
        "title": "Embedding model name",
        "description": "Exact name of model from selected provider",
        "type": "text",
        "value": s_dict["embed_model_name"],
    })

    fields.append({
        "id": "embed_model_api_base",
        "title": "Embedding model API base URL",
        "description": "API base URL for embedding model. Leave empty for default. Only relevant for Azure, local and custom (other) providers.",
        "type": "text",
        "value": s_dict["embed_model_api_base"],
    })

    fields.append({
        "id": "embed_model_rl_requests",
        "title": "Requests per minute limit",
        "description": "Limits the number of requests per minute to the embedding model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["embed_model_rl_requests"],
    })

    fields.append({
        "id": "embed_model_rl_input",
        "title": "Input tokens per minute limit",
        "description": "Limits the number of input tokens per minute to the embedding model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["embed_model_rl_input"],
    })

    fields.append({
        "id": "embed_model_kwargs",
        "title": "Embedding model additional parameters",
        "description": "Any other parameters supported by <a href='https://docs.litellm.ai/docs/set_keys' target='_blank'>LiteLLM</a>. Format is KEY=VALUE on individual lines, like .env file. Value can also contain JSON objects - when unquoted, it is treated as object, number etc., when quoted, it is treated as string.",
        "type": "textarea",
        "value": dict_to_env(s_dict["embed_model_kwargs"]),
    })

    return {
        "id": "embed_model",
        "title": "Embedding Model",
        "description": f"Settings for the embedding model used by AGIX.<br><h4>⚠️ No need to change</h4>The default HuggingFace model {default_settings['embed_model_name']} is preloaded and runs locally within the docker container and there's no need to change it unless you have a specific requirements for embedding.",
        "fields": fields,
        "tab": "agent",
    }


def build_browser_model_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the browser/web model configuration section."""
    s_dict = ctx.settings
    
    fields: list[SettingsField] = []
    
    fields.append({
        "id": "browser_model_provider",
        "title": "Web Browser model provider",
        "description": "Select provider for web browser model used by <a href='https://github.com/browser-use/browser-use' target='_blank'>browser-use</a> framework",
        "type": "select",
        "value": s_dict["browser_model_provider"],
        "options": [{"value": "role", "label": "Profile (Role)"}] + cast(list[FieldOption], get_providers("chat")),
    })

    fields.append({
        "id": "browser_model_name",
        "title": "Web Browser model name",
        "description": "Exact name of model from selected provider, or agent profile name if provider is 'Profile (Role)'.",
        "type": "text",
        "value": s_dict["browser_model_name"],
    })

    fields.append({
        "id": "browser_model_api_base",
        "title": "Web Browser model API base URL",
        "description": "API base URL for web browser model. Leave empty for default. Only relevant for Azure, local and custom (other) providers.",
        "type": "text",
        "value": s_dict["browser_model_api_base"],
    })

    fields.append({
        "id": "browser_model_vision",
        "title": "Use Vision",
        "description": "Models capable of Vision can use it to analyze web pages from screenshots. Increases quality but also token usage.",
        "type": "switch",
        "value": s_dict["browser_model_vision"],
    })

    fields.append({
        "id": "browser_model_ctx_length",
        "title": "Browser model context length",
        "description": "Maximum number of tokens in the context window for browser model. Used for web page analysis and navigation.",
        "type": "number",
        "value": s_dict.get("browser_model_ctx_length", 0),
    })

    fields.append({
        "id": "browser_model_rl_requests",
        "title": "Web Browser model rate limit requests",
        "description": "Rate limit requests for web browser model.",
        "type": "number",
        "value": s_dict["browser_model_rl_requests"],
    })

    fields.append({
        "id": "browser_model_rl_input",
        "title": "Web Browser model rate limit input",
        "description": "Rate limit input for web browser model.",
        "type": "number",
        "value": s_dict["browser_model_rl_input"],
    })

    fields.append({
        "id": "browser_model_rl_output",
        "title": "Web Browser model rate limit output",
        "description": "Rate limit output for web browser model.",
        "type": "number",
        "value": s_dict["browser_model_rl_output"],
    })

    fields.append({
        "id": "browser_model_max_tokens",
        "title": "Max output tokens per request",
        "description": "Maximum number of tokens the browser model can generate in a single response.",
        "type": "number",
        "value": s_dict.get("browser_model_max_tokens", 0),
    })

    fields.append({
        "id": "browser_agent_max_steps",
        "title": "Browser: Max steps per task",
        "description": "Maximum number of autonomous steps the browser agent can take to complete a task. Default: 50. Increasing this allows for more complex research tasks.",
        "type": "number",
        "value": s_dict.get("browser_agent_max_steps", 50),
    })

    fields.append({
        "id": "browser_agent_timeout_seconds",
        "title": "Browser: Task timeout (seconds)",
        "description": "Overall timeout for a single browser task. Default: 300 (5 minutes).",
        "type": "number",
        "value": s_dict.get("browser_agent_timeout_seconds", 300),
    })

    fields.append({
        "id": "browser_agent_screenshot_timeout",
        "title": "Browser: Screenshot timeout (ms)",
        "description": "Timeout for capturing a screenshot of the web page. Default: 25000 (25 seconds). Helpful for slow-loading pages.",
        "type": "number",
        "value": s_dict.get("browser_agent_screenshot_timeout", 25000),
    })

    fields.append({
        "id": "browser_model_thinking",
        "title": "Enable Thinking (Browser)",
        "description": "Enable reasoning for browser model tasks.",
        "type": "switch",
        "value": s_dict.get("browser_model_thinking", False),
    })
    
    fields.append({
        "id": "browser_model_thinking_tokens",
        "title": "Thinking Tokens (Browser)",
        "description": "Thinking token budget for browser tasks.",
        "type": "number",
        "value": s_dict.get("browser_model_thinking_tokens", 1024),
    })
    
    fields.append({
        "id": "browser_model_kwargs",
        "title": "Web Browser model additional parameters",
        "description": "Any other parameters supported by <a href='https://docs.litellm.ai/docs/set_keys' target='_blank'>LiteLLM</a>. Format is KEY=VALUE on individual lines, like .env file. Value can also contain JSON objects - when unquoted, it is treated as object, number etc., when quoted, it is treated as string.",
        "type": "textarea",
        "value": dict_to_env(s_dict["browser_model_kwargs"]),
    })

    fields.append({
        "id": "browser_http_headers",
        "title": "HTTP Headers",
        "description": "HTTP headers to include with all browser requests. Format is KEY=VALUE on individual lines, like .env file. Value can also contain JSON objects - when unquoted, it is treated as object, number etc., when quoted, it is treated as string. Example: Authorization=Bearer token123",
        "type": "textarea",
        "value": dict_to_env(s_dict.get("browser_http_headers", {})),
    })

    fields.append({
        "id": "browser_agent_profile",
        "title": "Web Browser Agent Profile",
        "description": "Select the agent profile to be used for web browser tasks. Default is 'browser'.",
        "type": "select",
        "value": s_dict.get("browser_agent_profile", "browser"),
        "options": [
            {"value": subdir, "label": subdir}
            for subdir in get_subdirectories("agents")
            if subdir != "_example"
        ],
    })

    return {
        "id": "browser_model",
        "title": "Web Browser Model",
        "description": "Settings for the web browser model. AGIX uses <a href='https://github.com/browser-use/browser-use' target='_blank'>browser-use</a> agentic framework to handle web interactions.",
        "fields": fields,
        "tab": "agent",
    }


def build_supervisor_model_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the supervisor model configuration section."""
    s_dict = ctx.settings
    
    fields: list[SettingsField] = []
    
    fields.append({
        "id": "supervisor_model_provider",
        "title": "Supervisor model provider",
        "description": "Select provider for the LLM supervisor that monitors agent behavior and detects problematic patterns",
        "type": "select",
        "value": s_dict["supervisor_model_provider"],
        "options": cast(list[FieldOption], get_providers("chat")),
    })
    
    fields.append({
        "id": "supervisor_model_name",
        "title": "Supervisor model name",
        "description": "Exact name of model from selected provider. A smaller, faster model is recommended for real-time supervision.",
        "type": "text",
        "value": s_dict["supervisor_model_name"],
    })

    fields.append({
        "id": "supervisor_model_api_base",
        "title": "Supervisor model API base URL",
        "description": "API base URL for supervisor model. Leave empty for default. Only relevant for Azure, local and custom (other) providers.",
        "type": "text",
        "value": s_dict["supervisor_model_api_base"],
    })

    fields.append({
        "id": "supervisor_model_ctx_length",
        "title": "Supervisor model context length",
        "description": "Maximum number of tokens in the context window for supervisor model. Used for pattern detection and intervention analysis.",
        "type": "number",
        "value": s_dict.get("supervisor_model_ctx_length", 0),
    })

    fields.append({
        "id": "supervisor_model_rl_requests",
        "title": "Requests per minute limit",
        "description": "Limits the number of requests per minute to the supervisor model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["supervisor_model_rl_requests"],
    })

    fields.append({
        "id": "supervisor_model_rl_input",
        "title": "Input tokens per minute limit",
        "description": "Limits the number of input tokens per minute to the supervisor model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["supervisor_model_rl_input"],
    })

    fields.append({
        "id": "supervisor_model_rl_output",
        "title": "Output tokens per minute limit",
        "description": "Limits the number of output tokens per minute to the supervisor model. Waits if the limit is exceeded. Set to 0 to disable rate limiting.",
        "type": "number",
        "value": s_dict["supervisor_model_rl_output"],
    })

    fields.append({
        "id": "supervisor_model_max_tokens",
        "title": "Max output tokens per request",
        "description": "Maximum number of tokens the supervisor model can generate in a single response.",
        "type": "number",
        "value": s_dict.get("supervisor_model_max_tokens", 0),
    })

    fields.append({
        "id": "supervisor_model_thinking",
        "title": "Enable Thinking (Supervisor)",
        "description": "Enable reasoning for supervisor model tasks.",
        "type": "switch",
        "value": s_dict.get("supervisor_model_thinking", False),
    })
    
    fields.append({
        "id": "supervisor_model_thinking_tokens",
        "title": "Thinking Tokens (Supervisor)",
        "description": "Thinking token budget for supervisor tasks.",
        "type": "number",
        "value": s_dict.get("supervisor_model_thinking_tokens", 1024),
    })
    
    fields.append({
        "id": "supervisor_model_kwargs",
        "title": "Supervisor model additional parameters",
        "description": "Any other parameters supported by <a href='https://docs.litellm.ai/docs/set_keys' target='_blank'>LiteLLM</a>. Format is KEY=VALUE on individual lines, like .env file.",
        "type": "textarea",
        "value": dict_to_env(s_dict["supervisor_model_kwargs"]),
    })

    fields.append({
        "id": "supervisor_intervention_timeout_seconds",
        "title": "Supervisor Intervention Timeout (seconds)",
        "description": "Time in seconds without progress before the supervisor automatically reads the chat and intervenes to help the agent. Set to 0 to disable time-based intervention. Default: 90 seconds",
        "type": "number",
        "value": s_dict["supervisor_intervention_timeout_seconds"],
    })

    return {
        "id": "supervisor_model",
        "title": "Supervisor Model",
        "description": "LLM used by the supervisor system to monitor agent behavior, detect problematic patterns (loops, errors, stuck states), and provide intelligent interventions. A smaller, faster model is recommended.",
        "fields": fields,
        "tab": "agent",
    }