from typing import Any, Dict, List, Union, Optional
from python.helpers import runtime, settings, defer
from python.helpers.print_style import PrintStyle
from python.helpers.agent_tracer import AgentTracer


def initialize_agent(override_settings: Optional[dict] = None, context: Optional['AgentContext'] = None, context_id: Optional[str] = None):
    from python.agent import AgentConfig
    import python.models as models
    from python.helpers.secrets_helper import get_secrets_manager
    from python.helpers.parameters import get_parameters_manager
    
    # Sync core secrets to os.environ for sub-process/MCP accessibility
    # Use context_id if provided (for initialization before full context creation)
    ctx_ref = context if context is not None else context_id
    get_secrets_manager(ctx_ref).sync_to_environ()

    current_settings = settings.get_settings().copy()
    privacy_mode = current_settings.get("privacy_mode", True)
    
    # Merge parameters (prioritize project if context provided)
    params_mgr = get_parameters_manager(context)
    params = params_mgr.load_parameters()
    if params:
        current_settings.update(params)


    if override_settings:
        current_settings.update(override_settings)

    def _normalize_model_kwargs(kwargs: dict) -> dict:
        # convert string values that represent valid Python numbers to numeric types
        result = {}
        for key, value in kwargs.items():
            if isinstance(value, str):
                # try to convert string to number if it's a valid Python number
                try:
                    # try int first, then float
                    result[key] = int(value)
                except ValueError:
                    try:
                        result[key] = float(value)
                    except ValueError:
                        result[key] = value
            else:
                result[key] = value
        return result

    profile = current_settings.get("agent_profile", "alex")
    profiles_enabled = current_settings.get("agent_profiles_enabled", True)

    # Handle fallback and migration
    if not profiles_enabled:
        profile = "alex"  # Primary orchestrator
    elif profile == "developer":
        profile = "code"  # Migration path: developer -> code

    # agent configuration
    config = AgentConfig(
        chat_model=models.resolve_model_config(
            models.ModelType.CHAT, 
            current_settings.get("chat_model_provider", "openrouter"),
            current_settings.get("chat_model_name", "anthropic/claude-3.5-sonnet"),
            base_config=models.ModelConfig(
                type=models.ModelType.CHAT,
                provider=current_settings.get("chat_model_provider", "openrouter"),
                name=current_settings.get("chat_model_name", "anthropic/claude-3.5-sonnet"),
                api_base=current_settings.get("chat_model_api_base", ""),
                ctx_length=current_settings.get("chat_model_ctx_length", 0),
                vision=current_settings.get("chat_model_vision", True),
                limit_requests=current_settings.get("chat_model_rl_requests", 0),
                limit_input=current_settings.get("chat_model_rl_input", 0),
                limit_output=current_settings.get("chat_model_rl_output", 0),
                max_tokens=current_settings.get("chat_model_max_tokens", 0),
                thinking=current_settings.get("chat_model_thinking", False),
                thinking_tokens=current_settings.get("chat_model_thinking_tokens", 0),
                kwargs=_normalize_model_kwargs(current_settings.get("chat_model_kwargs", {})),
                privacy=privacy_mode,
            )
        ),
        utility_model=models.resolve_model_config(
            models.ModelType.CHAT,
            current_settings.get("util_model_provider", "openrouter"),
            current_settings.get("util_model_name", "anthropic/claude-3.5-sonnet"),
            base_config=models.ModelConfig(
                type=models.ModelType.CHAT,
                provider=current_settings.get("util_model_provider", "openrouter"),
                name=current_settings.get("util_model_name", "anthropic/claude-3.5-sonnet"),
                api_base=current_settings.get("util_model_api_base", ""),
                ctx_length=current_settings.get("util_model_ctx_length", 0),
                limit_requests=current_settings.get("util_model_rl_requests", 0),
                limit_input=current_settings.get("util_model_rl_input", 0),
                limit_output=current_settings.get("util_model_rl_output", 0),
                max_tokens=current_settings.get("util_model_max_tokens", 0),
                thinking=current_settings.get("util_model_thinking", False),
                thinking_tokens=current_settings.get("util_model_thinking_tokens", 0),
                kwargs=_normalize_model_kwargs(current_settings.get("util_model_kwargs", {})),
                privacy=privacy_mode,
            )
        ),
        embeddings_model=models.resolve_model_config(
            models.ModelType.EMBEDDING,
            current_settings.get("embed_model_provider", "huggingface"),
            current_settings.get("embed_model_name", "all-MiniLM-L6-v2"),
            base_config=models.ModelConfig(
                type=models.ModelType.EMBEDDING,
                provider=current_settings.get("embed_model_provider", "huggingface"),
                name=current_settings.get("embed_model_name", "all-MiniLM-L6-v2"),
                api_base=current_settings.get("embed_model_api_base", ""),
                limit_requests=current_settings.get("embed_model_rl_requests", 0),
                kwargs=_normalize_model_kwargs(current_settings.get("embed_model_kwargs", {})),
                privacy=privacy_mode,
            )
        ),
        browser_model=models.resolve_model_config(
            models.ModelType.CHAT,
            current_settings.get("browser_model_provider", "openrouter"),
            current_settings.get("browser_model_name", "anthropic/claude-3.5-sonnet"),
            base_config=models.ModelConfig(
                type=models.ModelType.CHAT,
                provider=current_settings.get("browser_model_provider", "openrouter"),
                name=current_settings.get("browser_model_name", "anthropic/claude-3.5-sonnet"),
                api_base=current_settings.get("browser_model_api_base", ""),
                vision=current_settings.get("browser_model_vision", True),
                max_tokens=current_settings.get("browser_model_max_tokens", 0),
                thinking=current_settings.get("browser_model_thinking", False),
                thinking_tokens=current_settings.get("browser_model_thinking_tokens", 0),
                kwargs=_normalize_model_kwargs(current_settings.get("browser_model_kwargs", {})),
                privacy=privacy_mode,
            )
        ),
        profile=profile,
        memory_subdir=current_settings.get("agent_memory_subdir", "default"),
        knowledge_subdirs=[current_settings.get("agent_knowledge_subdir", "default"), "default"],
        mcp_servers=current_settings.get("mcp_servers", ""),
        browser_http_headers=current_settings.get("browser_http_headers", {}),
        skills=current_settings.get("agent_skills", []),
    )

    # [DEBUG] Log the resolved configurations
    print(f"[DEBUG_INIT] Agent initialized with profile: {profile}")
    print(f"[DEBUG_INIT] Chat Model: {config.chat_model.provider}/{config.chat_model.name}")
    print(f"[DEBUG_INIT] Util Model: {config.utility_model.provider}/{config.utility_model.name}")
    print(f"[DEBUG_INIT] Embed Model: {config.embeddings_model.provider}/{config.embeddings_model.name}")

    # update SSH and docker settings
    _set_runtime_config(config, current_settings)

    # update config with runtime args
    _args_override(config)

    # initialize MCP in deferred task to prevent blocking the main thread
    from python.helpers.mcp_handler import initialize_mcp
    initialize_mcp(config.mcp_servers, force=context is not None)

    # return config object
    return config

def initialize_chats(config: 'models.AgentConfig' = None):
    from python.helpers import persist_chat
    async def initialize_chats_async():
        persist_chat.load_tmp_chats(config=config)
    return defer.DeferredTask().start_task(initialize_chats_async)

def initialize_mcp():
    set = settings.get_settings()
    mcp_config = set.get("mcp_servers", "")
    async def initialize_mcp_async():
        from python.helpers.mcp_handler import initialize_mcp as _initialize_mcp
        return _initialize_mcp(mcp_config)
    return defer.DeferredTask().start_task(initialize_mcp_async)

def initialize_job_loop():
    from python.helpers.job_loop import run_loop
    return defer.DeferredTask("JobLoop").start_task(run_loop)

def initialize_preload():
    import preload
    return defer.DeferredTask().start_task(preload.preload)


def initialize_tracing(enabled: bool = True, console_output: bool = True, log_to_file: bool = True, log_to_context: bool = False):
    """
    Initialize agent tracing system.
    
    Args:
        enabled: Whether to enable tracing
        console_output: Whether to print trace events to console
        log_to_file: Whether to write trace events to logs/agent_trace_*.log
        log_to_context: Whether to log trace events to agent context
    """
    if enabled:
        AgentTracer.enable(
            trace_file=None,  # JSON trace file is optional
            console_output=console_output,
            log_to_context=log_to_context,
            log_to_file=log_to_file
        )
        PrintStyle().print("Agent tracing enabled - logs will be written to logs/agent_trace_*.log")
    return AgentTracer.is_enabled()


def _args_override(config):
    # update config with runtime args
    for key, value in runtime.args.items():
        if hasattr(config, key):
            # conversion based on type of config[key]
            if isinstance(getattr(config, key), bool):
                value = value.lower().strip() == "true"
            elif isinstance(getattr(config, key), int):
                value = int(value)
            elif isinstance(getattr(config, key), float):
                value = float(value)
            elif isinstance(getattr(config, key), str):
                value = str(value)
            else:
                raise Exception(
                    f"Unsupported argument type of '{key}': {type(getattr(config, key))}"
                )

            setattr(config, key, value)


def _set_runtime_config(config: "AgentConfig", set: settings.Settings):
    ssh_conf = settings.get_runtime_config(set)
    for key, value in ssh_conf.items():
        if hasattr(config, key):
            setattr(config, key, value)
