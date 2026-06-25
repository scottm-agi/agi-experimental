"""
Tool for configuring webhooks (GitHub, Forgejo, Gitea) via chat prompts.

Allows agents to:
- Set webhook secrets
- Configure allowed repos
- Enable/disable webhooks
- Get current configuration status
"""
from __future__ import annotations
from typing import Optional, List, Dict, Any
from python.helpers.tool import Tool, Response
from python.helpers.secrets_helper import get_default_secrets_manager
from python.helpers.print_style import PrintStyle
import json
import os


class ConfigureWebhook(Tool):
    """
    Configure webhook settings for GitHub, Forgejo, or Gitea integrations.
    
    Use this tool to:
    - Set webhook secrets (GITHUB_WEBHOOK_SECRET, FORGEJO_WEBHOOK_SECRET)
    - Enable or disable webhooks
    - Configure allowed repositories
    - Get current webhook configuration status
    
    All secrets are stored securely in the secrets manager.
    Settings are stored in data/settings.json.
    """

    async def execute(
        self, 
        action: str,
        provider: str = "github",
        secret: str = None,
        enabled: bool = None,
        allowed_repos: List[str] = None,
        **kwargs
    ) -> Response:
        """
        Configure webhooks.
        
        Args:
            action (str): The action to perform:
                - "set_secret": Set the webhook secret for a provider
                - "get_status": Get current webhook status and configuration
                - "enable": Enable webhooks
                - "disable": Disable webhooks
                - "add_repo": Add a repository to allowed list
                - "remove_repo": Remove a repository from allowed list
                - "generate_secret": Generate a new random webhook secret
            
            provider (str): The webhook provider. One of:
                - "github" (default)
                - "forgejo"
                - "gitea" (uses same settings as forgejo)
            
            secret (str): The webhook secret value (for set_secret action)
            enabled (bool): Whether webhooks are enabled
            allowed_repos (list): List of repo patterns like ["owner/repo", "owner/*"]
        
        Returns:
            Status message with configuration details.
        """
        action = action.lower()
        provider = provider.lower()
        
        # Map gitea to forgejo
        if provider == "gitea":
            provider = "forgejo"
        
        if action == "set_secret":
            return await self._set_secret(provider, secret)
        elif action == "get_status":
            return await self._get_status()
        elif action == "enable":
            return await self._set_enabled(True)
        elif action == "disable":
            return await self._set_enabled(False)
        elif action == "add_repo":
            return await self._manage_repos("add", allowed_repos)
        elif action == "remove_repo":
            return await self._manage_repos("remove", allowed_repos)
        elif action == "generate_secret":
            return await self._generate_secret(provider)
        else:
            return Response(
                message=f"Unknown action: {action}. Valid actions: set_secret, get_status, enable, disable, add_repo, remove_repo, generate_secret",
                break_loop=False
            )
    
    async def _set_secret(self, provider: str, secret: str) -> Response:
        """Set webhook secret for a provider."""
        if not secret:
            return Response(
                message="Error: secret parameter is required for set_secret action.",
                break_loop=False
            )
        
        # Determine key name
        key = f"{provider.upper()}_WEBHOOK_SECRET"
        
        try:
            sm = get_default_secrets_manager()
            sm.set_secret(key, secret)
            
            PrintStyle.hint(f"Stored webhook secret '{key}' in global secrets.")
            return Response(
                message=f"✅ Successfully stored {provider.title()} webhook secret.\n\n"
                        f"Key: `{key}`\n"
                        f"Scope: Global\n\n"
                        f"**Next Steps:**\n"
                        f"1. Configure your {provider.title()} repository settings\n"
                        f"2. Add a webhook pointing to: `https://your-domain/webhook/github`\n"
                        f"3. Set Content-Type to `application/json`\n"
                        f"4. Use the same secret value in your {provider.title()} webhook settings",
                break_loop=False
            )
        except Exception as e:
            return Response(message=f"Error storing secret: {e}", break_loop=False)
    
    async def _get_status(self) -> Response:
        """Get current webhook configuration status."""
        try:
            # Load secrets
            sm = get_default_secrets_manager()
            secrets = sm.load_secrets()
            
            github_secret = "✅ Configured" if secrets.get("GITHUB_WEBHOOK_SECRET") else "❌ Not set"
            forgejo_secret = "✅ Configured" if secrets.get("FORGEJO_WEBHOOK_SECRET") else "❌ Not set"
            
            # Load settings
            settings = self._load_settings()
            event_hooks = settings.get("event_hooks", {})
            
            enabled = "✅ Enabled" if event_hooks.get("enabled", True) else "❌ Disabled"
            allowed_repos = event_hooks.get("allowed_repos", [])
            allowed_str = ", ".join(allowed_repos) if allowed_repos else "All repos (no restrictions)"
            
            return Response(
                message=f"## Webhook Configuration Status\n\n"
                        f"| Setting | Status |\n"
                        f"|---------|--------|\n"
                        f"| Webhooks | {enabled} |\n"
                        f"| GitHub Secret | {github_secret} |\n"
                        f"| Forgejo Secret | {forgejo_secret} |\n"
                        f"| Allowed Repos | {allowed_str} |\n\n"
                        f"**Webhook Endpoint:** `/webhook/github` (supports GitHub, Forgejo, Gitea)\n\n"
                        f"Use `configure_webhook` with action='set_secret' to configure secrets.",
                break_loop=False
            )
        except Exception as e:
            return Response(message=f"Error getting status: {e}", break_loop=False)
    
    async def _set_enabled(self, enabled: bool) -> Response:
        """Enable or disable webhooks."""
        try:
            settings = self._load_settings()
            
            if "event_hooks" not in settings:
                settings["event_hooks"] = {}
            
            settings["event_hooks"]["enabled"] = enabled
            self._save_settings(settings)
            
            status = "enabled" if enabled else "disabled"
            emoji = "✅" if enabled else "🔴"
            
            return Response(
                message=f"{emoji} Webhooks have been **{status}**.\n\n"
                        f"Changes take effect immediately. "
                        f"{'New webhook events will now be processed.' if enabled else 'All webhook events will be rejected until re-enabled.'}",
                break_loop=False
            )
        except Exception as e:
            return Response(message=f"Error updating settings: {e}", break_loop=False)
    
    async def _manage_repos(self, operation: str, repos: List[str]) -> Response:
        """Add or remove repos from allowed list."""
        if not repos:
            return Response(
                message="Error: allowed_repos parameter is required. Provide a list like ['owner/repo', 'owner/*']",
                break_loop=False
            )
        
        try:
            settings = self._load_settings()
            
            if "event_hooks" not in settings:
                settings["event_hooks"] = {}
            
            current_repos = set(settings["event_hooks"].get("allowed_repos", []))
            
            if operation == "add":
                current_repos.update(repos)
                action_word = "added to"
            else:  # remove
                current_repos -= set(repos)
                action_word = "removed from"
            
            settings["event_hooks"]["allowed_repos"] = list(current_repos)
            self._save_settings(settings)
            
            return Response(
                message=f"✅ Repositories {action_word} allowed list.\n\n"
                        f"**Current allowed repos:** {', '.join(current_repos) if current_repos else 'All repos (no restrictions)'}",
                break_loop=False
            )
        except Exception as e:
            return Response(message=f"Error updating repos: {e}", break_loop=False)
    
    async def _generate_secret(self, provider: str) -> Response:
        """Generate a new random webhook secret."""
        import secrets
        
        # Generate a 64-character hex secret (32 bytes)
        new_secret = secrets.token_hex(32)
        key = f"{provider.upper()}_WEBHOOK_SECRET"
        
        try:
            sm = get_default_secrets_manager()
            sm.set_secret(key, new_secret)
            
            return Response(
                message=f"✅ Generated and stored new {provider.title()} webhook secret.\n\n"
                        f"**Key:** `{key}`\n"
                        f"**Secret:** `{new_secret}`\n\n"
                        f"⚠️ **IMPORTANT:** Copy this secret now and configure it in your {provider.title()} webhook settings. "
                        f"This is the only time the secret will be displayed in plaintext.\n\n"
                        f"**Webhook URL:** `https://your-domain/webhook/github`",
                break_loop=False
            )
        except Exception as e:
            return Response(message=f"Error generating secret: {e}", break_loop=False)
    
    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from settings.json."""
        settings_path = "data/settings.json"
        try:
            with open(settings_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
    
    def _save_settings(self, settings: Dict[str, Any]):
        """Save settings to settings.json."""
        settings_path = "data/settings.json"
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)


if __name__ == "__main__":
    pass
