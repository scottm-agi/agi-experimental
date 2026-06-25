"""
Utility functions for model wrappers.
"""
import logging
from python.helpers import dotenv_manager as dotenv

logger = logging.getLogger(__name__)

api_keys_round_robin: dict[str, int] = {}

def get_api_key(service: str) -> str:
    """Get API key for the service, supporting round-robin for multiple keys."""
    import os
    from python.helpers.secrets_helper import get_default_secrets_manager
    
    # 1. Prioritize SecretsManager (authoritative DB source)
    secrets_mgr = get_default_secrets_manager()
    # Check both standard and API_KEY prefix formats
    # 1. Primary Source: Strictly SecretsManager (DB/Persistent)
    # We check all variants against the DB before falling back to any Env/File source
    # This prevents a system key in OS_ENV[API_KEY_X] from preempting a user key in DB[X_API_KEY]
    variants = [
        f"API_KEY_{service.upper()}",
        f"{service.upper()}_API_KEY",
        f"{service.upper()}_API_TOKEN"
    ]
    
    for v in variants:
        key = secrets_mgr.get_secret(v, include_external=False)
        if key and key != "None":
            return key

    # 2. Secondary Source: OS environment or cached dotenv
    for v in variants:
        key = os.environ.get(v) \
              or dotenv.get_dotenv_value(v)
        if key and key != "None":
            return key
    
    return "None"
    if "," in key:
        api_keys = [k.strip() for k in key.split(",") if k.strip()]
        api_keys_round_robin[service] = api_keys_round_robin.get(service, -1) + 1
        key = api_keys[api_keys_round_robin[service] % len(api_keys)]
    return key