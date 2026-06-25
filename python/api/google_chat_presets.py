import json
import os

# Google Chat Identity Presets
# These are pre-configured credentials for specific organizations.
# Selecting one of these in the UI will auto-populate the Phase 1 registration JSON.

_DEFAULT_WEB_REDIRECT_URIS = [
    "https://example.com/google_chat_oauth_callback",
    "https://example.com/google_chat_oauth_callback",
    "https://example.com/google_chat_oauth_callback",
    "https://api.example.com/google_chat_oauth_callback",
]

def _get_web_redirect_uris() -> list:
    """Build redirect_uris list from OAUTH_REDIRECT_BASE_URL env var if set,
    otherwise fall back to the hardcoded defaults."""
    base_url = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
    if base_url:
        return [f"{base_url}/google_chat_oauth_callback"]
    return _DEFAULT_WEB_REDIRECT_URIS

GOOGLE_CHAT_PRESETS = {
    "AGIX OAuthClient": {
        "installed": {
            "client_id": "dummy-client-id-1.apps.googleusercontent.com",
            "project_id": "agi-experimental-chat",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": "dummy-client-secret-1",
            "redirect_uris": ["http://localhost"]
        }
    },
    "AGIX Hosted": {
        "web": {
            "client_id": "dummy-client-id-2.apps.googleusercontent.com",
            "project_id": "agi-experimental-chat",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": "dummy-client-secret-2",
            "redirect_uris": _get_web_redirect_uris()
        }
    }
}

def get_presets_json():
    """Returns a JSON string of all presets for the frontend to use."""
    return json.dumps(GOOGLE_CHAT_PRESETS)
