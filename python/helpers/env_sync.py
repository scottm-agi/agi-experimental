from __future__ import annotations
import os
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from python.helpers.settings import Settings

from python.helpers.print_style import PrintStyle

VENICE_API_BASE_DEFAULT = "https://api.venice.ai/api/v1"

def sync_to_environ(settings: Settings):
    """Sync critical settings to OS environment variables for immediate effect."""
    
    # 1. API Keys from dict
    api_keys = settings.get("api_keys", {})
    if isinstance(api_keys, dict):
        for provider, key in api_keys.items():
            if key and not key.startswith("******"): # Don't sync placeholders or empty
                provider_up = provider.upper().replace("API_KEY_", "").replace("_API_KEY", "")
                # Ensure we set both variants and OVERWRITE any existing OS env values
                # specifically to override Railway/OS defaults
                os.environ[f"{provider_up}_API_KEY"] = key
                os.environ[f"API_KEY_{provider_up}"] = key
                
                # Also ensure we propagate this change to the dotenv manager cache
                from python.helpers import dotenv_manager as dotenv
                dotenv.save_dotenv_value(f"{provider_up}_API_KEY", key)
                dotenv.save_dotenv_value(f"API_KEY_{provider_up}", key)

    
    # 2. Individual API Keys — sync ALL env var variants + dotenv
    for key_name in ["perplexity_api_key", "context7_api_key"]:
        val = settings.get(key_name)
        if val and not val.startswith("******"):
            env_key_upper = key_name.upper()  # e.g. PERPLEXITY_API_KEY
            # Extract provider name: "perplexity_api_key" -> "PERPLEXITY"
            provider = key_name.replace("_api_key", "").upper()
            
            # Set all 3 variants in os.environ
            os.environ[env_key_upper] = val                    # PERPLEXITY_API_KEY
            os.environ[f"API_KEY_{provider}"] = val            # API_KEY_PERPLEXITY
            os.environ[provider] = val                          # PERPLEXITY
            
            # Also persist to dotenv for container restart survival
            from python.helpers import dotenv_manager as dotenv
            dotenv.save_dotenv_value(env_key_upper, val)
            dotenv.save_dotenv_value(f"API_KEY_{provider}", val)
            dotenv.save_dotenv_value(provider, val)

    # 3. Model settings (Global Overrides)
    # These help tools or external scripts that might rely on environment variables
    env_mapping = {
        "global_model_enabled": "GLOBAL_MODEL_ENABLED",
        "global_model_provider": "GLOBAL_MODEL_PROVIDER",
        "global_model_name": "GLOBAL_MODEL_NAME",
        "global_model_ctx_length": "GLOBAL_MODEL_CTX_LENGTH",
        "global_model_max_tokens": "GLOBAL_MODEL_MAX_TOKENS",
        "global_model_thinking": "GLOBAL_MODEL_THINKING",
        "global_model_thinking_tokens": "GLOBAL_MODEL_THINKING_TOKENS",
        "agent_profile": "AGENT_PROFILE",
    }
    
    for settings_key, env_key in env_mapping.items():
        val = settings.get(settings_key)
        if val is not None:
            # Convert booleans to lowercase string for consistent truthiness checks elsewhere
            if isinstance(val, bool):
                os.environ[env_key] = str(val).lower()
            else:
                os.environ[env_key] = str(val)

    # 4. Special case for Venice (since we've been debugging it)
    # Ensure VENICE_API_KEY is available in os.environ even if not the active provider
    v_key = api_keys.get("venice") or os.environ.get("VENICE_API_KEY") or os.environ.get("API_KEY_VENICE")
    if v_key and not v_key.startswith("******"):
        if "VENICE_API_KEY" not in os.environ:
            os.environ["VENICE_API_KEY"] = v_key
        
        # IMPORTANT: Venice uses OpenAI provider in LiteLLM, so it NEEDS OPENAI_API_KEY.
        # We set it here if OPENAI_API_KEY is missing or looks like a placeholder.
        current_openai_key = os.environ.get("OPENAI_API_KEY")
        if not current_openai_key or current_openai_key.lower() in ("none", "", "na") or current_openai_key.startswith("{{"):
            os.environ["OPENAI_API_KEY"] = v_key
    
    # Also ensure VENICE_API_BASE is set if Venice is involved
    active_provider = settings.get("global_model_provider") if settings.get("global_model_enabled") else settings.get("chat_model_provider")
    if active_provider == "venice" or v_key:
        venice_base = settings.get("global_model_api_base") if settings.get("global_model_enabled") else settings.get("chat_model_api_base")
        venice_base = venice_base or VENICE_API_BASE_DEFAULT
        os.environ["VENICE_API_BASE"] = venice_base

    # 5. Sync Forgejo settings to environment for tools/webhooks
    mcp_servers_json = settings.get("mcp_servers")
    if mcp_servers_json:
        try:
            mcp_config = json.loads(mcp_servers_json)
            forgejo_config = mcp_config.get("mcpServers", {}).get("forgejo", {})
            if forgejo_config:
                env_vars = forgejo_config.get("env", {})
                if env_vars.get("FORGEJO_URL"):
                    os.environ["FORGEJO_URL"] = env_vars["FORGEJO_URL"]
                if env_vars.get("FORGEJO_TOKEN"):
                    os.environ["FORGEJO_TOKEN"] = env_vars["FORGEJO_TOKEN"]
        except Exception:
            pass
    # 6. Coordinate with SecretsManager for database-only secrets
    from python.helpers.secrets_helper import get_default_secrets_manager
    try:
        manager = get_default_secrets_manager()
        manager.sync_to_environ(force=True) # Force sync when setting is saved
    except Exception:
        pass

    # 7. Sync Global Parameters to environment
    # Useful for tools that might need these directly
    from python.helpers.parameters import get_parameters_manager
    try:
        params_manager = get_parameters_manager()
        params = params_manager.load_parameters()
        for p_key, p_val in params.items():
            if isinstance(p_val, (str, int, float, bool)):
                os.environ[f"PARAM_{p_key.upper()}"] = str(p_val)
    except Exception:
        pass
