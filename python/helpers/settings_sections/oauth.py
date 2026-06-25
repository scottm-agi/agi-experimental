"""
OAuth integration settings section builders.

Phase 6 of settings.py modularization.
Extracts Google Chat integration and dynamic OAuth vendor sections.
"""

import html
import json
import os
from datetime import datetime, timezone
from typing import Any

from python.api.google_chat_presets import GOOGLE_CHAT_PRESETS
from python.helpers.secrets_helper import get_default_secrets_manager

from .base import (
    SettingsField,
    SettingsSection,
    SectionBuilderContext,
)


def _get_google_chat_status() -> tuple[bool, bool, str]:
    """
    Check Google Chat OAuth status using the authoritative GoogleChatAuth handler.
    
    Returns:
        tuple: (has_creds, is_valid_token, status_html)
    """
    try:
        from python.api.google_chat_auth import GoogleChatAuth
        # We need an instance to call get_auth_status, but we don't have the app/lock here easily.
        # However, looking at get_auth_status, it doesn't actually use app or lock.
        # We'll create a dummy instance or refactor get_auth_status to be classmethod/static if possible.
        # For now, let's just use the logic from GoogleChatAuth.get_auth_status directly
        # but cleaned up to match our new standards.
        
        from python.helpers.secrets_helper import get_default_secrets_manager
        sm = get_default_secrets_manager()
        secrets_data = sm.load_secrets()
        
        # 1. Check for Credentials
        creds_json = secrets_data.get("GOOGLE_CHAT_CREDENTIALS")
        client_id = None
        if creds_json:
            try:
                config = json.loads(creds_json)
                client_id = (config.get("installed", {}) or config.get("web", {})).get("client_id")
            except (json.JSONDecodeError, KeyError, ValueError): pass
            
        if not client_id:
            # Only check specific system paths, NO relative paths
            for p in ["/agix/credentials.json", "/agix/credentials.json"]:
                if os.path.exists(p):
                    try:
                        with open(p, 'r') as f:
                            config = json.load(f)
                            client_id = (config.get("installed", {}) or config.get("web", {})).get("client_id")
                            if client_id: break
                    except (json.JSONDecodeError, OSError, KeyError): pass
        
        has_creds = bool(client_id)
        
        # 2. Check for Token and validate it
        # We'll use a simplified version of validate_token logic here to avoid dependency loops
        # or just instantiate GoogleChatAuth if safe.
        # Actually, let's just make it robust here.
        
        token_json = secrets_data.get("GOOGLE_CHAT_TOKEN")
        if not token_json:
            for p in ["/agix/token.json", "/agix/token.json"]:
                if os.path.exists(p):
                    try:
                        with open(p, "r") as f:
                            token_json = f.read()
                        if token_json: break
                    except (OSError, ValueError): pass
        
        is_valid_token = False
        if token_json:
            try:
                token_data = json.loads(token_json)
                if token_data.get("access_token") or token_data.get("token"):
                    expiry_str = token_data.get("expiry")
                    if expiry_str:
                        # Handle potential fractional seconds in expiry string
                        expiry_clean = expiry_str.split(".")[0].replace("Z", "")
                        expiry = datetime.strptime(expiry_clean, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        if expiry > datetime.now(timezone.utc):
                            is_valid_token = True
                        elif token_data.get("refresh_token"):
                            # If we have a refresh token, we count it as "Connected" 
                            # because the backend will refresh it on demand.
                            is_valid_token = True
                    else:
                        is_valid_token = True
            except (json.JSONDecodeError, KeyError, ValueError): pass

        # Premium status badge with integrated theme styling
        if is_valid_token:
            status_html = '<span class="scheduler-status-badge scheduler-status-idle" style="display: inline-flex; align-items: center; gap: 5px; margin-right: 5px; padding: 2px 8px;"><i class="material-symbols-outlined" style="font-size: 14px;">check_circle</i> Connected</span>'
        elif not has_creds:
            status_html = '<span class="scheduler-status-badge scheduler-status-error" style="display: inline-flex; align-items: center; gap: 5px; margin-right: 5px; padding: 2px 8px;" title="Missing Google Cloud credentials. Paste them below."><i class="material-symbols-outlined" style="font-size: 14px;">error</i> Setup Required</span>'
        else:
            status_html = '<span class="scheduler-status-badge scheduler-status-warning" style="display: inline-flex; align-items: center; gap: 5px; margin-right: 5px; padding: 2px 8px;" title="Authorized, but needs user connection."><i class="material-symbols-outlined" style="font-size: 14px;">warning</i> Authorization Pending</span>'

        return has_creds, is_valid_token, status_html
    except Exception as e:
        return False, False, f"Status Error: {str(e)}"


def _build_google_chat_fields() -> list[SettingsField]:
    """Build Google Chat integration fields."""
    has_creds, has_token, status_html = _get_google_chat_status()
    
    gc_btn_text = "Re-connect Google Chat" if has_token else "Connect Google Chat"
    gc_desc = f"Status: {status_html}. Connect your Google Chat account to enable the agent to interact with your spaces and threads."

    oauth_fields: list[SettingsField] = []
    
    oauth_fields.append(
        {
            "id": "google_chat_connect",
            "title": "Google Chat Integration",
            "description": gc_desc,
            "type": "button",
            "value": gc_btn_text,
            "action": "google_chat_auth_initiate"
        }
    )

    if has_creds or has_token:
        oauth_fields.append(
            {
                "id": "google_chat_disconnect",
                "title": "Danger Zone",
                "description": "Disconnect the integration and wipe all stored credentials and tokens.",
                "type": "button",
                "value": "Disconnect Google Chat",
                "action": "google_chat_auth_disconnect",
                "classes": "btn-cancel" # Match visually with delete/cancel actions
            }
        )

    # Manual JSON configuration tray
    oauth_fields.append(
        {
            "id": "google_chat_manual_tray",
            "type": "html",
            "value": f"""
<div id="gc-manual-tray" style="margin-top: 20px; border: 2px solid #444; border-radius: 8px; background: #111; display: block !important; visibility: visible !important; position: relative; clear: both; min-height: 400px;">
    <div style="padding: 15px; background: #222; border-bottom: 1px solid #333; font-weight: bold; color: #fff; display: block;">
        <span style="font-size: 16px;">Step 1: Application Identity Registration</span>
        <br>
        <span style="font-size: 11px; font-weight: normal; color: #aaa;">(REQUIRED BEFORE CLICKING CONNECT)</span>
    </div>
    
    <div style="padding: 20px; display: block;">
        <!-- Preset Selector -->
        <div style="margin-bottom: 20px; padding: 15px; background: rgba(77,171,247,0.1); border: 1px solid rgba(77,171,247,0.3); border-radius: 8px;">
            <label style="display: block; color: #4dabf7; font-size: 13px; font-weight: bold; margin-bottom: 8px;">Select Organization Profile:</label>
            <select id="gc_preset_selector" style="width: 100%; padding: 10px; background: #000; color: #fff; border: 1px solid #4dabf7; border-radius: 4px; font-size: 14px;" 
                onchange="const val = this.value; const area = document.getElementById('gc_manual_json'); if (val) {{ try {{ area.value = JSON.stringify(JSON.parse(val), null, 2); if (window.showToast) showToast('Profile applied! Click SAVE.', 'info'); }} catch(e) {{ console.error(e); }} }} else {{ area.value = ''; }}">
                <option value="">-- Manual Configuration --</option>
                {"".join([f'<option value="{html.escape(json.dumps(val))}">{name}</option>' for name, val in GOOGLE_CHAT_PRESETS.items()])}
            </select>
            <p style="font-size: 11px; color: #aaa; margin-top: 8px;">Choosing a profile will automatically fill in the "Birth Certificate" (ID & Secret) below.</p>
        </div>

        <div style="margin-bottom: 25px; line-height: 1.5; color: #ddd; font-size: 13px;">
            <p>If not using a profile, follow these steps to register manually:</p>
            <ol style="margin-left: 20px; color: #4dabf7;">
                <li>Visit <a href="https://console.cloud.google.com/apis/credentials" target="_blank" style="color: #4dabf7; text-decoration: underline;">Google Cloud Credentials</a></li>
                <li>Enable "Google Chat API" and configure OAuth Consent Screen.</li>
                <li>Create OAuth Client ID (Select <strong>Desktop App</strong>).</li>
                <li>Paste the JSON content below.</li>
            </ol>
        </div>


        <div style="display: block; margin-top: 15px;">
            <label style="display: block; color: #4dabf7; font-size: 12px; font-weight: bold; margin-bottom: 8px;">CREDENTIALS JSON (BIRTH CERTIFICATE):</label>
            <textarea id="gc_manual_json" style="width: 100%; height: 200px; background: #111; color: #4dabf7; border: 1px solid #444; padding: 10px; font-family: monospace; font-size: 12px; border-radius: 4px;" placeholder='{{ "installed": {{ ... }} }}'></textarea>
        </div>

        <div style="margin-top: 20px; border-top: 1px solid #333; padding-top: 20px;">
            <button class="btn btn-ok" style="width: 100%; height: 44px; font-weight: bold; cursor: pointer;" onclick="window.submitManualGoogleChat()">
                SAVE IDENTITY & PROCEED TO LOGIN
            </button>
            <p style="text-align: center; font-size: 11px; color: #888; margin-top: 10px;">
                <strong>Phase 2:</strong> After saving, you will click the "Connect Google Chat" button at the top to finalize the login.
            </p>
        </div>
    </div>
</div>

<style>
    .premium-tray {{
        transition: all 0.2s ease;
    }}
    .premium-tray:hover {{
        border-color: rgba(255, 255, 255, 0.3);
    }}
    .tray-arrow::after {{
        content: '\\25BC';
        display: inline-block;
        transition: transform 0.3s ease;
        margin-left: 10px;
    }}
    .premium-tray[open] .tray-arrow::after {{
        transform: rotate(180deg);
    }}
    
    .premium-textarea {{
        resize: vertical;
        outline: none;
    }}
    .premium-textarea:focus {{
        border-color: #4dabf7 !important;
        box-shadow: 0 0 0 2px rgba(77, 171, 247, 0.2);
    }}
    
    .setup-steps-col {{
        padding: 24px;
        background: rgba(255, 255, 255, 0.01);
        border-right: 1px solid rgba(255, 255, 255, 0.05);
    }}
    
    .setup-input-col {{
        padding: 24px;
        background: rgba(0, 0, 0, 0.15);
    }}

    .setup-step {{
        display: flex;
        align-items: flex-start;
        gap: 14px;
        margin-bottom: 16px;
        padding: 10px;
        border-radius: 8px;
        transition: all 0.2s;
        border: 1px solid transparent;
    }}
    .setup-step:hover {{
        background: rgba(255, 255, 255, 0.03);
        border-color: rgba(255, 255, 255, 0.05);
    }}
    .step-num {{
        background: var(--accent-primary);
        color: var(--bg-primary);
        width: 22px;
        height: 22px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 800;
        flex-shrink: 0;
        box-shadow: 0 0 10px rgba(var(--accent-primary-rgb), 0.3);
    }}
    .step-text {{
        font-size: 11.5px;
        line-height: 1.5;
        color: var(--text-secondary);
    }}
    .step-text strong {{ color: var(--text-primary); }}
    .step-text a {{ color: var(--accent-primary); text-decoration: none; font-weight: 600; }}
    .step-text a:hover {{ text-decoration: underline; }}

    .premium-textarea {{
        width: 100%;
        background: rgba(0, 0, 0, 0.3) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        color: var(--text-primary);
        border-radius: 12px;
        padding: 16px;
        font-family: var(--font-mono);
        font-size: 13px;
        line-height: 1.6;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: inset 0 2px 8px rgba(0,0,0,0.4);
    }}
    .premium-textarea:focus {{
        border-color: var(--accent-primary) !important;
        box-shadow: inset 0 2px 8px rgba(0,0,0,0.4), 0 0 12px rgba(var(--accent-primary-rgb), 0.15);
    }}
    
    .tray-footer {{
        display: flex;
        justify-content: flex-end;
        margin-top: 12px;
    }}
</style>
"""
        }
    )
    
    return oauth_fields


def _build_dynamic_vendor_fields() -> list[SettingsField]:
    """Build dynamic OAuth vendor fields based on configured vendors."""
    oauth_fields: list[SettingsField] = []
    
    try:
        from python.helpers.oauth_profile_manager import OAuthProfileManager
        opm = OAuthProfileManager()
        configured_vendors = opm.get_configured_vendors()
        
        for vendor in configured_vendors:
            # Skip Google Chat as it has its own section
            if vendor == "GOOGLE_CHAT":
                continue
                
            vendor_profiles = [p for p in opm.get_configured_profiles() if p.startswith(vendor)]
            
            # Check status of profiles
            status_parts = []
            for p in vendor_profiles:
                from python.helpers.oauth_helper import OAuthManager
                om = OAuthManager(p)
                token = om.get_token()
                if token:
                    if om.is_token_broken():
                        status_parts.append(f'<span class="scheduler-status-badge scheduler-status-error" style="padding: 2px 8px; margin-right: 5px;" title="{html.escape(token.get("broken_reason", "Unknown error"))}">{p}: Broken</span>')
                    else:
                        status_parts.append(f'<span class="scheduler-status-badge scheduler-status-idle" style="padding: 2px 8px; margin-right: 5px;">{p}: Connected</span>')
                else:
                    status_parts.append(f'<span class="scheduler-status-badge scheduler-status-warning" style="padding: 2px 8px; margin-right: 5px;">{p}: Pending</span>')
            
            vendor_desc = f"Status: {' '.join(status_parts)}"
            
            oauth_fields.append({
                "id": f"oauth_vendor_{vendor}_header",
                "type": "html",
                "value": f'<div style="margin-top: 20px; padding: 10px; background: #222; border-radius: 4px; font-weight: bold; color: #4dabf7;">{vendor} Integration</div>'
            })
            
            oauth_fields.append({
                "id": f"oauth_vendor_{vendor}_status",
                "title": f"{vendor} Status",
                "description": vendor_desc,
                "type": "html",
                "value": ""
            })
            
            # Add profile selector
            oauth_fields.append({
                "id": f"oauth_vendor_{vendor}_profile",
                "title": "Active Profile",
                "description": f"Select which {vendor} profile to use for agent tasks.",
                "type": "select",
                "value": vendor_profiles[0] if vendor_profiles else "",
                "options": [{"value": p, "label": p} for p in vendor_profiles]
            })
            
            # Add Re-connect button
            oauth_fields.append({
                "id": f"oauth_vendor_{vendor}_reconnect",
                "title": f"Authorize {vendor}",
                "description": f"Start OAuth flow for {vendor}.",
                "type": "button",
                "value": f"Connect {vendor}",
                "action": f"oauth_auth_initiate:{vendor}"
            })
    except Exception:
        # If OAuth profile manager is not available, skip dynamic sections
        pass
    
    return oauth_fields


def build_oauth_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the OAuth Integrations settings section."""
    
    oauth_fields: list[SettingsField] = []
    
    # Add Google Chat integration fields
    oauth_fields.extend(_build_google_chat_fields())
    
    # Add dynamic vendor fields
    oauth_fields.extend(_build_dynamic_vendor_fields())

    return {
        "id": "oauth",
        "title": "OAuth Integrations",
        "description": "Manage external service connections using OAuth 2.0. Only configured vendors are shown here.",
        "fields": oauth_fields,
        "tab": "oauth",
    }