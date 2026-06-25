from __future__ import annotations
import json
import yaml
import os
from typing import Dict, Any, Optional
from python.helpers import oauth_helper

class OAuthProfileManager:
    """
    Manages multi-vendor OAuth profiles, ensuring both WEB and DESKTOP profiles
    are created for each integration.
    """
    
    REGISTRY_PATH = "conf/oauth_providers.yaml"
    
    def __init__(self, context=None):
        self.context = context
        self.registry = self._load_registry()

    def _load_registry(self) -> Dict[str, Any]:
        """Load the vendor registry from YAML."""
        if not os.path.exists(self.REGISTRY_PATH):
            return {}
        with open(self.REGISTRY_PATH, 'r') as f:
            return yaml.safe_all_load(f) if hasattr(yaml, 'safe_all_load') else yaml.safe_load(f)

    def configure_vendor(self, vendor: str, client_id: str, client_secret: str, 
                         custom_scopes: Optional[list[str]] = None,
                         redirect_uri_web: Optional[str] = None):
        """
        Configure both WEB and DESKTOP profiles for a vendor.
        """
        vendor = vendor.upper()
        vendor_defaults = self.registry.get(vendor, {})
        
        if not vendor_defaults:
            raise ValueError(f"Vendor {vendor} not found in registry. Please add it first.")
            
        scopes = custom_scopes or vendor_defaults.get("default_scopes", [])
        
        # 1. Configure WEB Profile
        web_mgr = oauth_helper.OAuthManager(f"{vendor}_WEB", self.context)
        web_mgr.save_config(
            client_id=client_id,
            client_secret=client_secret,
            auth_url=vendor_defaults["auth_url"],
            token_url=vendor_defaults["token_url"],
            scopes=scopes
        )
        
        # 2. Configure DESKTOP Profile
        desktop_mgr = oauth_helper.OAuthManager(f"{vendor}_DESKTOP", self.context)
        desktop_mgr.save_config(
            client_id=client_id,
            client_secret=client_secret,
            auth_url=vendor_defaults["auth_url"],
            token_url=vendor_defaults["token_url"],
            scopes=scopes
        )
        
        return {
            "vendor": vendor,
            "profiles": [f"{vendor}_WEB", f"{vendor}_DESKTOP"],
            "scopes": scopes
        }

    def get_configured_profiles(self) -> list[str]:
        """List all configured OAuth profile names."""
        from python.helpers.secrets_helper import get_default_secrets_manager
        sm = get_default_secrets_manager()
        secrets = sm.load_secrets()
        
        profiles = []
        for key in secrets.keys():
            if key.startswith("OAUTH_") and key.endswith("_CONFIG"):
                # Extract profile name: OAUTH_{PROFILE}_CONFIG -> {PROFILE}
                profile = key[6:-7]
                profiles.append(profile)
        return sorted(profiles)

    def get_configured_vendors(self) -> list[str]:
        """List all unique vendors that have at least one profile configured."""
        profiles = self.get_configured_profiles()
        vendors = set()
        for p in profiles:
            # Profiles are usually VENDOR_WEB or VENDOR_DESKTOP
            if "_" in p:
                vendor = p.rsplit("_", 1)[0]
                vendors.add(vendor)
            else:
                vendors.add(p)
        return sorted(list(vendors))
