from __future__ import annotations
import os
import json
import time
import httpx
from typing import Dict, Optional, Any
from python.helpers import secrets_helper

class OAuthManager:
    """
    Generic OAuth 2.0 manager for handling authorization flows and token management.
    """
    
    def __init__(self, service_name: str, context=None):
        self.service_name = service_name.upper()
        self.secrets_mgr = secrets_helper.get_secrets_manager(context)
        self.config_key = f"OAUTH_{self.service_name}_CONFIG"
        self.token_key = f"OAUTH_{self.service_name}_TOKEN"

    def get_config(self) -> Dict[str, Any]:
        """Retrieve OAuth configuration from secrets."""
        config_str = self.secrets_mgr.load_secrets().get(self.config_key)
        if not config_str:
            raise ValueError(f"OAuth configuration for {self.service_name} not found in secrets.")
        return json.loads(config_str)

    def get_token(self) -> Dict[str, Any]:
        """Retrieve OAuth token from secrets."""
        token_str = self.secrets_mgr.load_secrets().get(self.token_key)
        if not token_str:
            return {}
        return json.loads(token_str)

    async def get_access_token(self) -> str:
        """Get a valid access token, refreshing it if necessary."""
        token = self.get_token()
        if not token:
            raise ValueError(f"No OAuth token found for {self.service_name}. Please authorize first.")
        
        # Check if expired (with 1-minute buffer)
        expires_at = token.get("expires_at", 0)
        if time.time() > (expires_at - 60):
            try:
                token = await self.refresh_token(token)
            except Exception as e:
                # Mark token as broken if refresh fails
                self.mark_token_broken(str(e))
                raise ValueError(f"Failed to refresh OAuth token for {self.service_name}: {e}")
            
        return token.get("access_token", "")

    def mark_token_broken(self, reason: str):
        """Mark the token as broken in secrets."""
        token = self.get_token()
        token["broken"] = True
        token["broken_reason"] = reason
        token["broken_at"] = time.time()
        self.secrets_mgr.set_secret(self.token_key, json.dumps(token))

    def is_token_broken(self) -> bool:
        """Check if the token is marked as broken."""
        token = self.get_token()
        return token.get("broken", False)

    async def refresh_token(self, current_token: Dict[str, Any]) -> Dict[str, Any]:
        """Refresh the OAuth access token using the refresh token."""
        config = self.get_config()
        refresh_token = current_token.get("refresh_token")
        if not refresh_token:
            raise ValueError(f"No refresh token available for {self.service_name}.")
            
        payload = {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(config["token_url"], data=payload)
            resp.raise_for_status()
            new_data = resp.json()
            
        # Update token data
        current_token.update(new_data)
        if "expires_in" in new_data:
            current_token["expires_at"] = time.time() + new_data["expires_in"]
            
        # Save back to secrets
        self.secrets_mgr.set_secret(self.token_key, json.dumps(current_token))
        return current_token

    def save_config(self, client_id: str, client_secret: str, auth_url: str, token_url: str, scopes: list[str]):
        """Save OAuth configuration to secrets."""
        config = {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_url": auth_url,
            "token_url": token_url,
            "scopes": scopes
        }
        self.secrets_mgr.set_secret(self.config_key, json.dumps(config))

    def save_token(self, access_token: str, refresh_token: Optional[str] = None, expires_in: Optional[int] = None):
        """Save OAuth token to secrets."""
        token = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in if expires_in else 0
        }
        self.secrets_mgr.set_secret(self.token_key, json.dumps(token))