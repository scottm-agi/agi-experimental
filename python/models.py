"""
models.py - Thin facade for model wrappers (modularized).

This file re-exports all components from python.helpers.model_wrappers
for backwards compatibility. Factory functions (get_chat_model, etc.)
and profile resolution logic remain here as they depend on settings.

Module Structure:
- python.helpers.model_wrappers.base - Core types (ModelType, ModelConfig, etc.)
- python.helpers.model_wrappers.rate_limiting - Rate limiting utilities
- python.helpers.model_wrappers.litellm_chat - LiteLLM chat wrapper
- python.helpers.model_wrappers.litellm_embed - LiteLLM embedding wrapper
- python.helpers.model_wrappers.browser_wrapper - Browser-compatible wrapper
- python.helpers.model_wrappers.fallback - Fallback wrappers
- python.helpers.model_wrappers.local_embed - Local sentence transformer
- python.helpers.model_wrappers.metadata - Model metadata and context windows
- python.helpers.model_wrappers.utils - API key utilities
- python.helpers.model_wrappers.errors - Provider errors
"""

import logging
import os
import socket
import sys
import json
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Set PyTorch/ARM environment variables before other imports to prevent meta tensor bugs
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
os.environ['PYTORCH_MPS_HIGH_WATERMARK_RATIO'] = '0.0'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

# =============================================================================
# Re-exports from modular packages (for backwards compatibility)
# =============================================================================

# Base types and data structures
from python.helpers.model_wrappers.base import (
    ModelType,
    ModelConfig,
    ChatChunk,
    ChatGenerationResult,
    BaseMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ChatGenerationChunk,
    SimpleChatModel,
    Embeddings,
    _parse_chunk,
)

# Rate limiting utilities
from python.helpers.model_wrappers.rate_limiting import (
    RateLimiter,
    get_rate_limiter,
    calculate_retry_delay,
    apply_rate_limiter,
    apply_rate_limiter_sync,
    handle_rate_limit_error,
    _notify_llm_failure,
    _is_transient_litellm_error,
    _is_rate_limit_error,
    _extract_retry_after,
)

# Also export without underscore for new code
is_transient_litellm_error = _is_transient_litellm_error
is_rate_limit_error = _is_rate_limit_error
extract_retry_after = _extract_retry_after

# Chat wrappers
from python.helpers.model_wrappers.litellm_chat import (
    LiteLLMChatWrapper,
    BrowserCompatibleChatWrapper,
)

# Embedding wrappers
from python.helpers.model_wrappers.litellm_embed import (
    LiteLLMEmbeddingWrapper,
    LocalSentenceTransformerWrapper,
)

# Fallback wrappers
from python.helpers.model_wrappers.fallback import (
    FallbackChatWrapper,
    FallbackEmbeddingWrapper,
)

# Model metadata
from python.helpers.model_wrappers.metadata import (
    MODEL_METADATA,
    get_model_context_window,
    resolve_largest_context_model,
    get_safe_max_tokens,
)

def resolve_model_config(
    model_type: ModelType,
    provider: str,
    name: str,
    base_config: Optional[ModelConfig] = None,
    **kwargs: Any
) -> ModelConfig:
    """
    Resolve model configuration parameters without instantiating a wrapper.
    Applies profiles and global overrides.
    """
    s = settings.get_settings()
    
    # Apply Global Override if applicable
    global_model_enabled = s.get("global_model_enabled") and not kwargs.get("bypass_global_override")
    
    # 1. Resolve Profile/Role
    configs = s.get("model_configurations", [])
    role_configs = s.get("role_configurations", {})
    configured_providers = get_configured_providers()

    def _resolve(p: str, n: str):
        routing_rules = s.get("routing_rules", {})
        role_key = n if p == "role" else p
        if role_key in routing_rules:
            role_key = routing_rules[role_key]
            p = "role"
            n = role_key
        
        if role_key in role_configs:
            role = role_configs[role_key]
            if isinstance(role, dict):
                p_id = role.get("provider")
                m_name = role.get("name")
                if p_id and m_name and p_id in configured_providers:
                    return p_id, m_name, {**role.get("kwargs", {}), **kwargs}
        return p, n, kwargs

    p_res, n_res, k_res = _resolve(provider, name)
    
    # 2. Apply Global Override
    if global_model_enabled and model_type == ModelType.CHAT:
        g_provider = s.get("global_model_provider")
        g_name = s.get("global_model_name")
        g_ctx = s.get("global_model_ctx_length", 0)
        
        p_res = g_provider or p_res
        n_res = g_name or n_res
        if g_ctx > 0:
            k_res["_profile_ctx_length"] = g_ctx
        if g_ctx <= 0 and not k_res.get("_profile_ctx_length"):
            k_res["_profile_ctx_length"] = get_model_context_window(n_res)

    # 3. Build ModelConfig
    ctx = k_res.get("_profile_ctx_length", 0) or (base_config.ctx_length if base_config else 0)
    if not ctx:
        ctx = get_model_context_window(n_res)
    
    return ModelConfig(
        type=model_type,
        provider=p_res,
        name=n_res,
        ctx_length=ctx,
        api_base=base_config.api_base if base_config else "",
        max_tokens=get_safe_max_tokens(k_res.get("_profile_max_tokens", 0), n_res),
        vision=base_config.vision if base_config else True,
        limit_requests=base_config.limit_requests if base_config else 0,
        limit_input=base_config.limit_input if base_config else 0,
        limit_output=base_config.limit_output if base_config else 0,
        thinking=k_res.get("_profile_thinking", base_config.thinking if base_config else False),
        thinking_tokens=k_res.get("_profile_thinking_tokens", base_config.thinking_tokens if base_config else 0),
        kwargs={**k_res, **(base_config.kwargs if base_config else {})},
        privacy=s.get("privacy_mode", True)
    )

def get_model_by_name(model_id: str) -> Optional[ModelConfig]:
    """
    Resolve a model ID string (e.g. 'anthropic/claude-opus-4.6' or 'role/code') to a ModelConfig.
    Supports both explicit provider/name format and role-based resolution.
    """
    if not model_id:
        return None
    
    parts = model_id.split('/', 1)
    if len(parts) == 2:
        provider, name = parts
    else:
        # Check if it's a known role/profile name in settings
        s = settings.get_settings()
        role_configs = s.get("role_configurations", {})
        if model_id in role_configs:
            provider = "role"
            name = model_id
        else:
            provider = s.get("chat_model_provider", "openrouter")
            name = model_id
        
    return resolve_model_config(ModelType.CHAT, provider, name)

# Utilities
from python.helpers.model_wrappers.utils import (
    get_api_key,
    api_keys_round_robin,
)

# Errors
from python.helpers.model_wrappers.errors import (
    ProviderConfigurationError,
    is_bedrock_missing_dependency_error,
)

# =============================================================================
# LiteLLM and external imports
# =============================================================================

import litellm
from python.helpers.litellm_shim import completion, acompletion, embedding

from python.helpers import dotenv_manager as dotenv
from python.helpers import settings, dirty_json, browser_use_monkeypatch
from python.helpers.dotenv_manager import load_dotenv
from python.helpers.providers import get_provider_config, get_raw_providers
from python.helpers.rate_limiter import (
    BackoffConfig,
    get_or_create_rate_limiter,
)

# =============================================================================
# Initialization
# =============================================================================

def turn_off_logging():
    """Disable extra LiteLLM logging."""
    os.environ["LITELLM_LOG"] = "ERROR"
    litellm.suppress_debug_info = True
    for name in logging.Logger.manager.loggerDict:
        if name.lower().startswith("litellm"):
            logging.getLogger(name).setLevel(logging.ERROR)

# Initialize
load_dotenv()
turn_off_logging()
browser_use_monkeypatch.apply()

litellm.modify_params = True
litellm.success_callback = []
litellm.failure_callback = []
litellm._disable_cost_calc = True

# Register global crash recovery handler
from python.helpers.crash_recovery import register_crash_handler
register_crash_handler()

# =============================================================================
# Global state
# =============================================================================

rate_limiters: dict[str, RateLimiter] = {}

# SentenceTransformer model caching (used by LocalSentenceTransformerWrapper)
_ST_MODEL_CACHE: dict = {}
_ST_MODEL_LOCK = threading.Lock()

# Import sentence_transformers for local embeddings (no API key needed)
try:
    import sentence_transformers as sentence_transformers_lib
    print(f"[EMBEDDINGS] sentence_transformers loaded successfully: {sentence_transformers_lib.__version__}", file=sys.stderr)
except ImportError as e:
    print(f"[EMBEDDINGS] CRITICAL: sentence_transformers import failed: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sentence_transformers_lib = None
except Exception as e:
    print(f"[EMBEDDINGS] CRITICAL: sentence_transformers import error: {type(e).__name__}: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sentence_transformers_lib = None

DEFAULT_BACKOFF_CONFIG = BackoffConfig(
    initial_delay=1.0,
    max_delay=60.0,
    multiplier=2.0,
    jitter=0.1,
    max_retries=10,
)

# Universal local fallback for all agent profiles (Ollama Qwen)
MODEL_ID_QWEN_FALLBACK = "qwen3:30b-a3b-instruct-2507-q4_K_M"

QWEN_FINAL_FALLBACK = {
    "provider": "ollama",
    "name": MODEL_ID_QWEN_FALLBACK,
    "kwargs": {
        "api_base": "http://host.docker.internal:11434",
        "temperature": 0,
        "bypass_global_override": True,
        "_agix_final_fallback": True,
    }
}

# Cache for Ollama availability (checked once per process)
_ollama_available: Optional[bool] = None

def _check_ollama_available(timeout: float = 1.0) -> bool:
    """Quick TCP check if Ollama is reachable. Cached per process."""
    global _ollama_available
    if _ollama_available is not None:
        return _ollama_available
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        # Check host.docker.internal:11434 (Ollama default port)
        result = sock.connect_ex(("host.docker.internal", 11434))
        sock.close()
        _ollama_available = (result == 0)
    except Exception:
        _ollama_available = False
    
    if not _ollama_available:
        logger.info("[models] Ollama not available at host.docker.internal:11434, skipping as fallback")
    return _ollama_available

# =============================================================================
# Dynamic Profile Resolution (Bundle #82)
# =============================================================================

ROLE_REQUIREMENTS = {
    "orchestrator": ["reasoning", "function_calling", "vision"],
    "agent0": ["reasoning", "function_calling", "vision"],
    "architecture": ["reasoning"],
    "architect": ["reasoning"],
    "code": ["code", "function_calling"],
    "developer": ["code", "function_calling"],
    "multiagentdev": ["code", "reasoning", "function_calling"],
    "debug": ["reasoning", "function_calling"],
    "documentation": ["reasoning"],
    "researcher": ["reasoning"],
    "content": ["reasoning"],
    "frontend": ["vision", "function_calling"],
    "simple": [],
    "hacker": ["uncensored"],
    "review": ["reasoning"],
    "ask": ["reasoning"],
}


def get_configured_providers() -> List[str]:
    """Returns a list of provider IDs that have a configured API key."""
    active = []
    try:
        raw_providers = get_raw_providers("chat")
        candidates = [(p.get("id") or p.get("value", "")).lower() for p in raw_providers]
        candidates = list(set(filter(None, candidates)))
    except Exception:
        candidates = [
            "openai", "anthropic", "google", "groq", "mistral",
            "venice", "xai", "openrouter", "deepseek"
        ]
    
    for c in candidates:
        key = get_api_key(c)
        if key and key not in ("None", "NA"):
            active.append(c)
    return active


def resolve_dynamic_role_configs(role_name: str) -> List[Dict[str, Any]]:
    """Dynamically builds model configurations for a given role based on active providers."""
    active_providers = get_configured_providers()
    requirements = ROLE_REQUIREMENTS.get(role_name, [])
    
    configs = []
    
    if "venice" in active_providers:
        active_providers.remove("venice")
        active_providers.insert(0, "venice")
    
    if "openrouter" in active_providers:
        active_providers.remove("openrouter")
        active_providers.insert(0, "openrouter")

    for p_id in active_providers:
        p_cfg = get_provider_config("chat", p_id)
        if not p_cfg:
            continue
        
        models_meta = p_cfg.get("models", [])
        if not models_meta:
            continue
        
        best_model = None
        best_match_count = -1
        best_context = -1
        
        for m in models_meta:
            m_id = m.get("id")
            m_caps = m.get("capabilities", [])
            
            match_count = len([r for r in requirements if r in m_caps])
            m_context = get_model_context_window(m_id)

            if match_count > best_match_count:
                best_match_count = match_count
                best_model = m_id
                best_context = m_context
            elif match_count == best_match_count and match_count >= 0:
                if m_context > best_context:
                    best_model = m_id
                    best_context = m_context
                elif m_context == best_context:
                    current_best_caps = next(
                        (x.get("capabilities", []) for x in models_meta if x.get("id") == best_model), []
                    )
                    if len(m_caps) < len(current_best_caps):
                        best_model = m_id

        if best_model:
            configs.append({
                "provider": p_id,
                "name": best_model,
                "kwargs": {},
                "context_window": best_context
            })

    configs.sort(key=lambda x: x.get("context_window", -1), reverse=True)
    
    for c in configs:
        c.pop("context_window", None)

    if not configs:
        configs.append({
            "provider": active_providers[0] if active_providers else "openrouter",
            "name": settings.MODELS_DEFAULT_GROK if active_providers and active_providers[0] == "venice" else "anthropic/claude-3.5-sonnet",
            "kwargs": {}
        })

    return configs


# =============================================================================
# Internal factory helpers
# =============================================================================

def _get_litellm_chat(
    cls: type = LiteLLMChatWrapper,
    model_name: str = "",
    provider_name: str = "",
    model_config: Optional[ModelConfig] = None,
    **kwargs: Any,
):
    """Create a LiteLLM chat wrapper instance."""
    # [FIX] Avoid locking in a stale key. If it looks like a placeholder, re-fetch.
    api_key = kwargs.pop("api_key", None)
    if not api_key or str(api_key).lower() in ("none", "", "na") or str(api_key).startswith("{{") or str(api_key).startswith("******"):
        api_key = get_api_key(provider_name)

    if api_key and api_key not in ("None", "NA"):
        kwargs["api_key"] = api_key

    provider_name, model_name, kwargs = _adjust_call_args(provider_name, model_name, kwargs)
    return cls(provider=provider_name, model=model_name, model_config=model_config, **kwargs)


def _get_litellm_embedding(
    model_name: str,
    provider_name: str,
    model_config: Optional[ModelConfig] = None,
    **kwargs: Any,
):
    """Return an embedding wrapper for the given provider/model."""
    original_model_name = model_name
    cleaned_name = original_model_name.strip().strip("\"").strip("'")

    if provider_name == "huggingface":
        if not cleaned_name:
            cleaned_name = "sentence-transformers/all-MiniLM-L6-v2"

        if cleaned_name == "all-MiniLM-L6-v2":
            cleaned_name = "sentence-transformers/all-MiniLM-L6-v2"

        if cleaned_name.startswith("sentence-transformers/"):
            # POLICY: We ONLY use local in-container sentence-transformers.
            # NEVER fall back to HuggingFace Inference API — it requires a paid
            # token we don't configure, and would leak embedding data externally.
            if sentence_transformers_lib is not None:
                provider_name, cleaned_name, kwargs = _adjust_call_args(provider_name, cleaned_name, kwargs)
                return LocalSentenceTransformerWrapper(
                    provider=provider_name,
                    model=cleaned_name,
                    model_config=model_config,
                    **kwargs,
                )
            else:
                logger.error(
                    f"sentence-transformers library not available — cannot create local embedding model for {cleaned_name}. "
                    f"This is likely a numpy/scipy ABI mismatch. Redeploy with the latest Docker image."
                )
                raise ProviderConfigurationError(
                    f"Local embedding model {cleaned_name} requires the sentence-transformers library, "
                    f"which is currently unavailable due to an import error. "
                    f"Redeploy with the latest Docker image to fix the numpy/scipy ABI mismatch."
                )

    api_key = kwargs.pop("api_key", None) or get_api_key(provider_name)

    if api_key and api_key not in ("None", "NA"):
        kwargs["api_key"] = api_key

    provider_name, model_name, kwargs = _adjust_call_args(provider_name, original_model_name, kwargs)
    return LiteLLMEmbeddingWrapper(
        model=model_name, provider=provider_name, model_config=model_config, **kwargs
    )


def _adjust_call_args(provider_name: str, model_name: str, kwargs: dict):
    """Apply provider-specific adjustments to call arguments."""
    if provider_name == "openrouter":
        kwargs["extra_headers"] = {
            "HTTP-Referer": "https://example.com",
            "X-Title": "AGIX",
        }

    if provider_name == "other":
        provider_name = "openai"

    return provider_name, model_name, kwargs


def _merge_provider_defaults(provider_type: str, original_provider: str, kwargs: dict) -> tuple[str, dict]:
    """Merge provider defaults from configuration."""
    def _normalize_values(values: dict) -> dict:
        result: dict[str, Any] = {}
        for k, v in values.items():
            if isinstance(v, str):
                try:
                    result[k] = int(v)
                except ValueError:
                    try:
                        result[k] = float(v)
                    except ValueError:
                        result[k] = v
            else:
                result[k] = v
        return result

    provider_name = original_provider
    env_provider = original_provider
    cfg = get_provider_config(provider_type, original_provider)
    if cfg:
        provider_name = cfg.get("litellm_provider", original_provider).lower()

        extra_kwargs = cfg.get("kwargs") if isinstance(cfg, dict) else None
        if isinstance(extra_kwargs, dict):
            for k, v in extra_kwargs.items():
                kwargs.setdefault(k, v)

    if env_provider.lower() == "agix_venice":
        env_provider = "venice"

    # [FIX] Handle 'venice' as a first-class provider mapping to openai
    if provider_name.lower() == "venice" or env_provider.lower() == "venice":
        provider_name = "openai"
        kwargs.setdefault("api_base", "https://api.venice.ai/api/v1")
        
        # Inject key if missing OR invalid (placeholder/None/Empty)
        current_v_key = kwargs.get("api_key")
        if not current_v_key or str(current_v_key).lower() in ("none", "", "na") or str(current_v_key).startswith("{{") or str(current_v_key).startswith("******"):
            new_v_key = get_api_key("venice")
            if new_v_key and new_v_key not in ("None", "NA"):
                kwargs["api_key"] = new_v_key
                logger.info("Injecting Venice API key")

    if provider_name == "openai" and "api_base" in kwargs and "venice" in kwargs.get("api_base", ""):
        current_o_key = kwargs.get("api_key")
        if not current_o_key or str(current_o_key).lower() in ("none", "", "na") or str(current_o_key).startswith("{{") or str(current_o_key).startswith("******"):
            new_v_key = get_api_key("venice")
            if new_v_key and new_v_key not in ("None", "NA"):
                kwargs["api_key"] = new_v_key
                logger.info("Injecting Venice API key for openai-compatible call")

    if "api_key" not in kwargs:
        key = get_api_key(env_provider)
        if key and key not in ("None", "NA"):
            kwargs["api_key"] = key

    try:
        global_kwargs = settings.get_settings().get("litellm_global_kwargs", {})
    except Exception:
        global_kwargs = {}
    if isinstance(global_kwargs, dict):
        for k, v in _normalize_values(global_kwargs).items():
            kwargs.setdefault(k, v)

    return provider_name, kwargs


# =============================================================================
# Public Factory Functions
# =============================================================================

def get_chat_model(
    provider: str,
    name: str,
    model_config: Optional[ModelConfig] = None,
    wrapper_class: type = LiteLLMChatWrapper,
    **kwargs: Any
) -> Union[LiteLLMChatWrapper, FallbackChatWrapper, BrowserCompatibleChatWrapper]:
    """
    Get a chat model wrapper, resolving profiles and applying fallbacks.
    
    Supports:
    - Named profiles/roles from settings
    - Comma-separated fallback lists
    - Global model override
    - Automatic QWEN local fallback (Issue #267)
    """
    s = settings.get_settings()
    configs = s.get("model_configurations", [])
    role_configs = s.get("role_configurations", {})
    configured_providers = get_configured_providers()
    
    # Determine if Global Model Override is enabled
    env_global_enabled = os.environ.get("GLOBAL_MODEL_ENABLED")
    if env_global_enabled is not None:
        global_model_enabled = str(env_global_enabled).lower() in ("true", "1", "on", "yes")
    else:
        global_model_enabled = bool(s.get("global_model_enabled"))
        
    if kwargs.get("bypass_global_override"):
        global_model_enabled = False
    
    def apply_global_override(p: str, n: str, k: dict):
        if not global_model_enabled:
            return p, n, k
        
        # [FIX] Prioritize settings over os.environ for global overrides to prevent ENV hijacking
        # SecretsManager.get_secret handles the logic of combined sources if needed
        from .helpers import secrets_helper
        sm = secrets_helper.get_default_secrets_manager()
        
        g_provider = sm.get_secret("GLOBAL_MODEL_PROVIDER") or s.get("global_model_provider")
        g_name = sm.get_secret("GLOBAL_MODEL_NAME") or s.get("global_model_name")
        g_ctx_str = sm.get_secret("GLOBAL_MODEL_CTX_LENGTH")
        g_ctx = int(g_ctx_str) if g_ctx_str and g_ctx_str.isdigit() else s.get("global_model_ctx_length", 0)
        
        new_p = g_provider or p
        new_n = g_name or n
        new_k = k.copy()
        
        if g_ctx > 0:
            new_k["_profile_ctx_length"] = g_ctx
        
        # Sync thinking from secrets or settings
        g_thinking_str = sm.get_secret("GLOBAL_MODEL_THINKING")
        if g_thinking_str:
            new_k["_profile_thinking"] = g_thinking_str.lower() in ("true", "1", "yes")
        else:
            new_k["_profile_thinking"] = s.get("global_model_thinking", False)

        g_thinking_tokens_str = sm.get_secret("GLOBAL_MODEL_THINKING_TOKENS")
        if g_thinking_tokens_str and g_thinking_tokens_str.isdigit():
            new_k["_profile_thinking_tokens"] = int(g_thinking_tokens_str)
        else:
            new_k["_profile_thinking_tokens"] = s.get("global_model_thinking_tokens", 1024)
        
        g_max_tokens_str = sm.get_secret("GLOBAL_MODEL_MAX_TOKENS")
        if g_max_tokens_str and g_max_tokens_str.isdigit() and int(g_max_tokens_str) > 0:
            new_k["_profile_max_tokens"] = int(g_max_tokens_str)
        else:
            new_k["_profile_max_tokens"] = s.get("global_model_max_tokens", 0)

        # If we are overriding but g_ctx is 0, ensure we have a valid ctx_length in kwargs or resolved from metadata
        if g_ctx <= 0 and not new_k.get("_profile_ctx_length"):
            new_k["_profile_ctx_length"] = get_model_context_window(new_n)
            
        logger.debug(f"[GLOBAL_OVERRIDE] Applied: {new_p}/{new_n} (original: {p}/{n})")
        return new_p, new_n, new_k
    
    def resolve_profile(p: str, n: str):
        """Resolve named profile to provider/model/kwargs."""
        if p == "huggingface" or n == "huggingface":
            p = "venice"
            n = "largest_context"

        routing_rules = s.get("routing_rules", {})
        role_key = n if p == "role" else p
        
        if role_key in routing_rules:
            role_key = routing_rules[role_key]
            p = "role"
            n = role_key

        if role_key in role_configs:
            role = role_configs[role_key]
            if isinstance(role, list):
                return role
            
            p_id = role.get("provider")
            m_name = role.get("name")
            ctx_len = role.get("ctx_length", 0)
            max_tok = role.get("max_tokens", 0)
            thinking = role.get("thinking", False)
            thinking_tokens = role.get("thinking_tokens", 0)
            
            req_kwargs = kwargs.copy()
            if ctx_len > 0:
                req_kwargs["_profile_ctx_length"] = ctx_len
            if max_tok > 0:
                req_kwargs["_profile_max_tokens"] = max_tok
            if thinking:
                req_kwargs["_profile_thinking"] = True
            if thinking_tokens > 0:
                req_kwargs["_profile_thinking_tokens"] = thinking_tokens

            if p_id and m_name and p_id in configured_providers:
                # Use a copy of kwargs to avoid mutating the caller's state
                merged_kwargs = {**role.get("kwargs", {}), **req_kwargs}
                return p_id, m_name, merged_kwargs
            
            return _apply_profile_fallback_chain(role_key, wrapper_class, req_kwargs)

        for config in configs:
            if config.get("id") == p or config.get("name") == p:
                return config.get("provider"), config.get("model"), {**config.get("kwargs", {}), **kwargs.copy()}
        
        if p == "role":
            return _apply_profile_fallback_chain(n, wrapper_class, kwargs.copy())

        if p == "role" or not n:
            return _apply_profile_fallback_chain("unknown", wrapper_class, kwargs.copy())

        return p, n, kwargs.copy()

    def _apply_profile_fallback_chain(role_key: str, wrapper_cls: type, extra_kwargs: dict):
        """Fallback chain for unconfigured profiles."""
        active_provider = s.get("chat_model_provider")
        if active_provider and active_provider in configured_providers:
            first_provider = active_provider
        else:
            first_provider = configured_providers[0] if configured_providers else "venice"
        
        agent0_cfg = role_configs.get("agent0", {})
        if agent0_cfg and agent0_cfg.get("provider") and agent0_cfg.get("name"):
            return get_chat_model(agent0_cfg.get("provider"), agent0_cfg.get("name"), model_config, wrapper_cls, **extra_kwargs)
        
        return get_chat_model(first_provider, settings.MODELS_DEFAULT_GROK, model_config, wrapper_cls, **extra_kwargs)

    primary_model: Optional[Union[LiteLLMChatWrapper, FallbackChatWrapper, BrowserCompatibleChatWrapper]] = None

    # Handle comma-separated list fail-through
    if (isinstance(provider, str) and "," in provider) or (isinstance(name, str) and "," in name):
        logger.info(f"get_chat_model: creating fallback wrapper for {provider}/{name}")
        p_list = [p.strip() for p in str(provider).split(",") if p.strip()]
        n_list = [n.strip() for n in str(name).split(",") if n.strip()]
        
        internal_kwargs = kwargs.copy()
        internal_kwargs["_agix_final_fallback"] = True
        
        wrappers = []
        max_len = max(len(p_list), len(n_list))
        for i in range(max_len):
            p_val = p_list[i] if i < len(p_list) else p_list[-1]
            n_val = n_list[i] if i < len(n_list) else n_list[-1]
            wrappers.append(get_chat_model(p_val, n_val, model_config, wrapper_class, **internal_kwargs))
        
        primary_model = FallbackChatWrapper(wrappers)

    # Resolve named profile if applicable
    if primary_model is None:
        resolved = resolve_profile(provider, name)
        if isinstance(resolved, list):
            logger.info(f"get_chat_model: creating cascading fallback wrapper for role '{name}'")
            
            internal_kwargs = kwargs.copy()
            internal_kwargs["_agix_final_fallback"] = True
            
            wrappers = []
            for item in resolved:
                wrappers.append(get_chat_model(
                    item.get("provider"),
                    item.get("name"),
                    model_config,
                    wrapper_class,
                    **{**item.get("kwargs", {}), **internal_kwargs}
                ))
            
            primary_model = FallbackChatWrapper(wrappers)
            
        elif not isinstance(resolved, tuple):
            primary_model = resolved
        else:
            p_res, n_res, k_res = resolved
            
            # Apply Global Model Override here, AFTER profile resolution
            p_fin, n_fin, k_fin = apply_global_override(p_res, n_res, k_res)
            
            # [FIX] Apply provider defaults (litellm_provider, api_base, etc.) consistently
            # This ensures 'venice' maps to 'openai' with the correct api_base.
            p_fin, k_fin = _merge_provider_defaults("chat", p_fin, k_fin)
            
            if n_fin == "largest_context" and p_fin != "role":
                largest_model = resolve_largest_context_model(p_fin)
                if largest_model:
                    logger.info(f"get_chat_model: resolved 'largest_context' for '{p_fin}' to '{largest_model}'")
                    n_fin = largest_model
            
            # [FIX] Ensure model name is correctly formatted for LiteLLM.
            # Only strip the provider prefix if it already exists and is followed by the same provider.
            # (e.g., openai/gpt-4o -> gpt-4o, but google/gemini-... -> keep as is for OpenRouter)
            if "/" in n_fin:
                parts = n_fin.split("/", 1)
                p_prefix = parts[0].lower()
                # Only strip if the prefix matches the resolved provider AND doesn't seem to be a cross-provider ID (like openrouter)
                if p_prefix == p_fin.lower() and p_fin.lower() not in ("openrouter"):
                    n_fin = parts[1]
            
            provider, name, kwargs = p_fin, n_fin, k_fin
            primary_model = _get_litellm_chat(wrapper_class, n_fin, p_fin, model_config, **k_fin)

    # Apply Local QWEN safety net (Issue #267)
    qwen_id = QWEN_FINAL_FALLBACK["name"]
    primary_name = ""
    if primary_model and hasattr(primary_model, "model_name"):
        primary_name = str(primary_model.model_name)
    
    # Only add Ollama fallback if it's actually reachable AND enabled in settings
    # (Issue #267 + availability fix + UI toggle)
    ollama_enabled = bool(s.get("ollama_fallback_enabled", False))
    # Allow environment variable override to disable even if setting is True
    disable_ollama_env = os.environ.get("DISABLE_OLLAMA_FALLBACK", "").lower() in ("1", "true", "yes")
    
    if ollama_enabled and not disable_ollama_env and not (kwargs.get("_agix_final_fallback") or (primary_name and qwen_id in primary_name)):
        if _check_ollama_available():
            qwen_kwargs = QWEN_FINAL_FALLBACK["kwargs"].copy()
            qwen_kwargs["agix_fallback_delay"] = 180.0  # 3-minute delay
            
            qwen_wrapper = get_chat_model(
                QWEN_FINAL_FALLBACK["provider"],
                QWEN_FINAL_FALLBACK["name"],
                **qwen_kwargs
            )
            
            if primary_model:
                if isinstance(primary_model, FallbackChatWrapper):
                    primary_model.wrappers.append(qwen_wrapper)
                else:
                    primary_model = FallbackChatWrapper([primary_model, qwen_wrapper])
            else:
                # This handles cases where primary_model failed to resolve but QWEN exists
                primary_model = qwen_wrapper

    # Final Metadata processing
    kwargs.pop("bypass_global_override", None)
    kwargs.pop("_agix_final_fallback", None)
    
    if primary_model is None:
        # Last resort - create a basic model if none exists
        orig = provider.lower()
        provider_name, k_res = _merge_provider_defaults("chat", orig, kwargs.copy())
        return _get_litellm_chat(wrapper_class, name, provider_name, model_config, **k_res)
         
    profile_ctx_length = kwargs.pop("_profile_ctx_length", 0)
    profile_max_tokens = kwargs.pop("_profile_max_tokens", 0)
    profile_thinking = kwargs.pop("_profile_thinking", kwargs.pop("thinking", False))
    profile_thinking_tokens = kwargs.pop("_profile_thinking_tokens", kwargs.pop("thinking_tokens", 0))

    # If we already have a primary_model (e.g. from recursive resolution), 
    # use its existing metadata if none was explicitly provided in the current call's kwargs.
    if primary_model:
        if not profile_ctx_length:
            profile_ctx_length = getattr(primary_model, "ctx_length", 0)
        if hasattr(primary_model, "agix_model_conf") and primary_model.agix_model_conf:
            mc = primary_model.agix_model_conf
            if not profile_max_tokens: profile_max_tokens = mc.max_tokens
            if not profile_thinking: profile_thinking = mc.thinking
            if not profile_thinking_tokens: profile_thinking_tokens = mc.thinking_tokens
    
    if not profile_ctx_length:
        resolved_ctx = get_model_context_window(name)
        if resolved_ctx > 0:
            profile_ctx_length = resolved_ctx
        elif not profile_ctx_length:
            profile_ctx_length = 128000
    
    
    safe_max_tokens = get_safe_max_tokens(profile_max_tokens, name)
    if model_config:
        # Carry over all fields from the existing model_config, then override specific ones
        model_config = ModelConfig(
            type=model_config.type,
            provider=model_config.provider,
            name=model_config.name,
            api_base=model_config.api_base,
            ctx_length=profile_ctx_length or model_config.ctx_length,
            limit_requests=model_config.limit_requests,
            limit_input=model_config.limit_input,
            limit_output=model_config.limit_output,
            max_tokens=safe_max_tokens if profile_max_tokens > 0 else model_config.max_tokens or safe_max_tokens,
            vision=model_config.vision,
            privacy=model_config.privacy,
            thinking=profile_thinking or model_config.thinking,
            thinking_tokens=profile_thinking_tokens or model_config.thinking_tokens,
            kwargs=model_config.kwargs,
        )
    else:
        model_config = ModelConfig(
            type=ModelType.CHAT,
            provider=provider,
            name=name,
            ctx_length=profile_ctx_length,
            max_tokens=safe_max_tokens,
            thinking=profile_thinking,
            thinking_tokens=profile_thinking_tokens,
            privacy=settings.get_settings().get("privacy_mode", True)
        )
    logger.info(f"get_chat_model: ctx={profile_ctx_length}, max_tokens={model_config.max_tokens}, thinking={model_config.thinking}")

    # Attach the final resolved ModelConfig to the primary model (or its constituent wrappers)
    if hasattr(primary_model, "agix_model_conf") or hasattr(primary_model, "model_config_data"):
         # For Single wrappers
         if hasattr(primary_model, "agix_model_conf"):
             primary_model.agix_model_conf = model_config # type: ignore
         elif hasattr(primary_model, "model_config_data"):
             primary_model.model_config_data = model_config # type: ignore
    elif isinstance(primary_model, FallbackChatWrapper):
        # For Fallback wrappers, update the FIRST one (the primary)
        if primary_model.wrappers and hasattr(primary_model.wrappers[0], "agix_model_conf"):
            primary_model.wrappers[0].agix_model_conf = model_config # type: ignore

    return primary_model


def get_browser_model(
    provider: str, name: str, model_config: Optional[ModelConfig] = None, **kwargs: Any
) -> BrowserCompatibleChatWrapper:
    """Get a browser-compatible chat model wrapper."""
    litellm_wrapper = get_chat_model(provider, name, model_config, **kwargs)

    if isinstance(litellm_wrapper, FallbackChatWrapper):
        litellm_wrapper = litellm_wrapper.wrappers[0]

    if hasattr(litellm_wrapper, '_wrapped') and litellm_wrapper._wrapped is not None:
        litellm_wrapper = litellm_wrapper._wrapped

    return BrowserCompatibleChatWrapper(wrapper=litellm_wrapper)


def get_embedding_model(
    provider: str, name: str, model_config: Optional[ModelConfig] = None, **kwargs: Any
) -> Union[LiteLLMEmbeddingWrapper, LocalSentenceTransformerWrapper, FallbackEmbeddingWrapper]:
    """Get an embedding model wrapper, resolving profiles and applying fallbacks."""
    s = settings.get_settings()
    configs = s.get("model_configurations", [])
    
    def resolve_profile(p: str, n: str):
        for config in configs:
            if config.get("id") == p or config.get("name") == p:
                return config.get("provider"), config.get("model"), {**config.get("kwargs", {}), **kwargs}
        return p, n, kwargs

    # Support comma-separated lists for fail-through
    if "," in provider or "," in name:
        logger.info(f"get_embedding_model: creating fallback wrapper for {provider}/{name}")
        p_list = [p.strip() for p in str(provider).split(",") if p.strip()]
        n_list = [n.strip() for n in str(name).split(",") if n.strip()]
        
        if not p_list or not n_list:
            raise ValueError("Invalid provider or name list for embedding fallback")

        wrappers = []
        max_len = max(len(p_list), len(n_list))
        for i in range(max_len):
            p = p_list[i] if i < len(p_list) else p_list[-1]
            n = n_list[i] if i < len(n_list) else n_list[-1]
            wrappers.append(get_embedding_model(p, n, model_config, **kwargs))
        return FallbackEmbeddingWrapper(wrappers)

    # Resolve named profile
    provider, name, kwargs = resolve_profile(provider, name)

    orig = provider.lower()
    provider_name, kwargs = _merge_provider_defaults("embedding", orig, kwargs)
    logger.info(f"get_embedding_model: resolving {orig}/{name} -> {provider_name}/{name}")
    return _get_litellm_embedding(name, provider_name, model_config, **kwargs)
