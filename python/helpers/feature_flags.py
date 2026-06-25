from __future__ import annotations
import json
import logging
import os

log = logging.getLogger(__name__)


def is_production_env() -> bool:
    """Check if running in production environment.
    
    Production mode enables all security guardrails:
    - Input attack detection for ALL messages (not just external)
    - Output content filtering on ALL responses
    - Tool sandboxing (block system file access)
    - API error sanitization
    - UI restrictions (hide file browser, sub-agent tiles)
    
    Override: Setting AGIX_DEV_MODE=true forces dev mode regardless
    of AGIX_ENV. This allows Railway admins to unlock all UI settings
    by setting a single env var without changing the environment name.
    """
    # Dev mode override: if explicitly set to true, always return False (non-production)
    if os.getenv("AGIX_DEV_MODE", "false").lower() == "true":
        return False
    return os.getenv("AGIX_ENV", "").lower() == "production"


def is_download_enabled() -> bool:
    """Check if file downloads are enabled.
    
    Downloads remain available even when file access (browser) is disabled.
    This allows agents to stage files for user download in production.
    Only explicitly disabled via AGIX_DISABLE_DOWNLOADS.
    """
    return os.getenv("AGIX_DISABLE_DOWNLOADS", "false").lower() != "true"


def is_simple_chat_forced() -> bool:
    """Check if simple chat mode is strictly forced via environment variable (UI toggle disabled)."""
    # Only AGIX_FORCE_SIMPLE_CHAT should truly disable the toggle.
    return os.getenv("AGIX_FORCE_SIMPLE_CHAT", "false").lower() == "true"


def is_simple_chat_enabled_default() -> bool:
    """Check if simple chat should be enabled by default (but can be toggled by user)."""
    # AGIX_SIMPLE_CHAT acts as a suggested default, especially in production.
    # Also enabled if forced.
    return (
        os.getenv("AGIX_SIMPLE_CHAT", "false").lower() == "true" or
        is_simple_chat_forced()
    )


def is_sub_agent_tiles_hidden() -> bool:
    """Check if sub-agent tiles should be hidden in chat."""
    # Default to true if simple chat is forced or in production, but can be overridden
    if is_simple_chat_forced():
        return True
    if is_production_env():
        return True
    return os.getenv("AGIX_HIDE_SUB_AGENT_TILES", "false").lower() == "true"


def is_file_access_disabled() -> bool:
    """Check if file access features are disabled via environment variable.
    
    Default: disabled in production (hides Files + Import knowledge UI buttons
    and blocks file browser API for users). Agents still access filesystems
    via their tools. Can be explicitly overridden.
    """
    explicit = os.getenv("AGIX_DISABLE_FILE_ACCESS")
    if explicit is not None:
        return explicit.lower() == "true"
    # Default: disabled in production, enabled otherwise
    return is_production_env()


# ---------------------------------------------------------------------------
# Per-tab / per-feature flags
# ---------------------------------------------------------------------------


def is_history_enabled() -> bool:
    """Check if the History tab is enabled. Default: true (even in production)."""
    return os.getenv("AGIX_ENABLE_HISTORY", "true").lower() == "true"


def is_scheduler_enabled() -> bool:
    """Check if the Task Scheduler tab is enabled. Default: true."""
    return os.getenv("AGIX_ENABLE_SCHEDULER", "true").lower() == "true"


def is_oauth_enabled() -> bool:
    """Check if the OAuth tab is enabled. Default: true."""
    return os.getenv("AGIX_ENABLE_OAUTH", "true").lower() == "true"


def is_backup_enabled() -> bool:
    """Check if the Backup & Restore tab is enabled.
    
    Default: true in development, false in production (unless explicitly enabled).
    """
    explicit = os.getenv("AGIX_ENABLE_BACKUP")
    if explicit is not None:
        return explicit.lower() == "true"
    # Default: disabled in production, enabled otherwise
    return not is_production_env()


def is_projects_enabled() -> bool:
    """Check if the Projects tab is enabled. Default: true (all environments)."""
    return os.getenv("AGIX_ENABLE_PROJECTS", "true").lower() == "true"


def is_mcp_enabled() -> bool:
    """Check if the MCP/A2A tab is enabled.
    
    Default: true in development, false in production (unless explicitly enabled).
    """
    explicit = os.getenv("AGIX_ENABLE_MCP")
    if explicit is not None:
        return explicit.lower() == "true"
    return not is_production_env()


def is_developer_tab_enabled() -> bool:
    """Check if the Developer tab is enabled.
    
    Default: true in development, false in production (unless explicitly enabled).
    """
    explicit = os.getenv("AGIX_ENABLE_DEVELOPER")
    if explicit is not None:
        return explicit.lower() == "true"
    return not is_production_env()


def is_external_enabled() -> bool:
    """Check if the External Services tab is enabled. Default: true."""
    return os.getenv("AGIX_ENABLE_EXTERNAL", "true").lower() == "true"


# ---------------------------------------------------------------------------
# Config file loader
# ---------------------------------------------------------------------------


def load_from_config(config_path: str = "") -> None:
    """Load feature flag defaults from a JSON config file.
    
    Existing environment variables take precedence and are NOT overridden.
    Keys starting with '_' (e.g. _comment) are ignored.
    
    Args:
        config_path: Absolute path to the JSON config file.
                     If the file does not exist, this is a no-op.
    """
    if not config_path or not os.path.isfile(config_path):
        return

    try:
        with open(config_path, "r") as f:
            config: dict = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load feature flags config %s: %s", config_path, exc)
        return

    for key, value in config.items():
        # Skip comment keys
        if key.startswith("_"):
            continue
        # Only set if not already in environment (env vars take precedence)
        if key not in os.environ:
            os.environ[key] = str(value)


# ---------------------------------------------------------------------------
# Aggregate export
# ---------------------------------------------------------------------------


def get_all_flags() -> dict[str, bool]:
    """Get all feature flags as a dictionary for frontend export."""
    return {
        "simple_chat_forced": is_simple_chat_forced(),
        "simple_chat_enabled_default": is_simple_chat_enabled_default(),
        "file_access_disabled": is_file_access_disabled(),
        "hide_sub_agent_tiles": is_sub_agent_tiles_hidden(),
        "production_env": is_production_env(),
        "download_enabled": is_download_enabled(),
        "history_enabled": is_history_enabled(),
        "scheduler_enabled": is_scheduler_enabled(),
        "oauth_enabled": is_oauth_enabled(),
        "backup_enabled": is_backup_enabled(),
        "projects_enabled": is_projects_enabled(),
        "mcp_enabled": is_mcp_enabled(),
        "developer_tab_enabled": is_developer_tab_enabled(),
        "external_enabled": is_external_enabled(),
    }
