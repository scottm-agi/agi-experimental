from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import settings as settings_module
from python.helpers import runtime
import os
import json
import logging
import threading
from typing import Any
from flask import Flask
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# Use a fixed port for the callback if possible, or let the user configure it.
# For now, we'll use the same port as the Web UI but a different path.
CALLBACK_PATH = "/google_chat_oauth_callback"

# PKCE: Store code_verifier between initiate and callback requests
# Key: OAuth state parameter, Value: code_verifier string
_oauth_pkce_store: dict = {}

class GoogleChatAuth(ApiHandler):
    def __init__(self, app: Flask, thread_lock: threading.Lock):
        super().__init__(app, thread_lock)
        # Use /agix/ paths as primary, /agix/ as fallback.
        # DO NOT fall back to current directory (e.g. "token.json") as it 
        # causes false "Connected" status in local dev or fresh clones.
        self.creds_path = "/agix/credentials.json" if os.path.exists("/agix/credentials.json") else "/agix/credentials.json"
        self.token_path = "/agix/token.json" if os.path.exists("/agix/token.json") else "/agix/token.json"
        self.alt_creds_path = None
        self.alt_token_path = None

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action")
        
        if action == "initiate":
            config_json = input.get("config_json")
            if config_json:
                from python.helpers.secrets_helper import get_default_secrets_manager
                secrets_manager = get_default_secrets_manager()
                secrets_manager.set_secret("GOOGLE_CHAT_CREDENTIALS", config_json)
            return await self.initiate_oauth(request)
        elif action == "callback":
            return await self.handle_callback(request)
        elif action == "status":
            return self.get_auth_status()
        elif action == "disconnect":
            return await self.disconnect_oauth()
        else:
            return Response("Invalid action", 400)

    async def initiate_oauth(self, request: Request) -> dict | Response:
        try:
            # 1. Load credentials from secret or file
            from python.helpers.secrets_helper import get_default_secrets_manager
            secrets_manager = get_default_secrets_manager()
            creds_json = secrets_manager.load_secrets().get("GOOGLE_CHAT_CREDENTIALS")
            
            client_config = None
            if creds_json:
                try:
                    client_config = json.loads(creds_json)
                except json.JSONDecodeError:
                    logger.warning("GOOGLE_CHAT_CREDENTIALS secret is not valid JSON")
            
            # 2. Setup Flow using either secret (priority) or file
            scopes = [
                "https://www.googleapis.com/auth/chat.messages",
                "https://www.googleapis.com/auth/chat.spaces.readonly",
                "https://www.googleapis.com/auth/chat.memberships.readonly",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/contacts.other.readonly"
            ]
            
            # Build redirect_uri: prefer OAUTH_REDIRECT_BASE_URL env var, then X-Forwarded-Host, then Host
            oauth_base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
            if oauth_base:
                redirect_uri = f"{oauth_base}{CALLBACK_PATH}"
            else:
                # protocol = "https" if request.is_secure else "http"
                # We assume http for local dev unless cert.pem exists in /agix/
                # Force https if we are on Railway
                _cert_check = "/agix/cert.pem" if os.path.exists("/agix/cert.pem") else "/agix/cert.pem"
                protocol = "https" if (os.path.exists(_cert_check) or os.environ.get("RAILWAY_ENVIRONMENT")) else "http"
                # Prefer X-Forwarded-Host (set by reverse proxies) over Host header
                host = request.headers.get("x-forwarded-host", request.headers.get("host", f"localhost:{runtime.get_web_ui_port()}"))
                if request.headers.get("x-forwarded-proto"):
                    protocol = request.headers.get("x-forwarded-proto")
                redirect_uri = f"{protocol}://{host}{CALLBACK_PATH}"
            
            if client_config:
                logger.info("Using client_config from secrets for OAuth initiation")
                flow = Flow.from_client_config(
                    client_config,
                    scopes=scopes,
                    redirect_uri=redirect_uri
                )
            elif os.path.exists(self.creds_path):
                logger.info(f"Using credentials from {self.creds_path}")
                flow = Flow.from_client_secrets_file(
                    self.creds_path,
                    scopes=scopes,
                    redirect_uri=redirect_uri
                )
            elif self.alt_creds_path and os.path.exists(self.alt_creds_path):
                logger.info(f"Using credentials from {self.alt_creds_path}")
                flow = Flow.from_client_secrets_file(
                    self.alt_creds_path,
                    scopes=scopes,
                    redirect_uri=redirect_uri
                )
            else:
                return {
                    "status": "setup_required",
                    "message": "Google Cloud credentials missing.",
                    "instructions": "To connect Google Chat, you need to provide your Google Cloud OAuth 2.0 Client ID configuration JSON.\n\n1. Go to Google Cloud Console > APIs & Services > Credentials.\n2. Create an 'OAuth 2.0 Client ID'.\n   - Select 'Desktop App' for local development (supports http://localhost).\n   - Select 'Web Application' for hosted environments (Railway, Custom Domains).\n3. Download the JSON file.\n4. Paste the content of that file into the 'GOOGLE_CHAT_CREDENTIALS' secret in the Secrets Store tab.",
                    "setup_url": "https://console.cloud.google.com/apis/credentials"
                }
            
            # 3. Generate authorization URL with PKCE
            auth_url, state = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent'
            )
            
            # Store PKCE code_verifier for the callback phase
            if hasattr(flow, 'code_verifier') and flow.code_verifier:
                _oauth_pkce_store[state] = flow.code_verifier
                logger.info(f"[OAUTH] Stored PKCE code_verifier for state={state[:8]}...")
            
            # DEBUG LOGGING
            try:
                actual_client_id = getattr(flow.client_config, 'client_id', 'unknown')
                logger.info(f"[OAUTH DEBUG] Initiating with client_id: {actual_client_id}")
                logger.info(f"[OAUTH DEBUG] Redirect URI: {redirect_uri}")
                logger.info(f"[OAUTH DEBUG] Auth URL generated: {auth_url[:100]}...")
            except Exception: pass

            return {"status": "success", "auth_url": auth_url}
            
        except Exception as e:
            logger.error(f"Failed to initiate Google Chat OAuth: {e}")
            return Response(f"OAuth initiation failed: {str(e)}", 500)

    async def handle_callback(self, request: Request) -> dict | Response:
        # This is actually handled by a separate direct route in run_ui.py 
        # because the callback comes from Google directly and might not 
        # follow the ApiHandler pattern perfectly (GET with params).
        # We implementation this here just in case or for manual call.
        return {"status": "error", "message": "Callback should be directed to /google_chat_oauth_callback"}

    def get_auth_status(self) -> dict:
        from python.helpers.secrets_helper import get_default_secrets_manager
        secrets_manager = get_default_secrets_manager()
        
        secrets = secrets_manager.load_secrets()
        creds_json = secrets.get("GOOGLE_CHAT_CREDENTIALS")
        
        client_id = None
        if creds_json:
            try:
                config = json.loads(creds_json)
                client_id = (config.get("installed", {}) or config.get("web", {})).get("client_id")
            except (json.JSONDecodeError, KeyError, ValueError): pass
        
        # Fallback to file check for client_id detection
        if not client_id:
            for p in [self.creds_path, self.alt_creds_path]:
                if p and os.path.exists(p):
                    try:
                        with open(p, 'r') as f:
                            config = json.load(f)
                            client_id = (config.get("installed", {}) or config.get("web", {})).get("client_id")
                            if client_id: break
                    except (json.JSONDecodeError, OSError, KeyError): pass
        
        is_connected = self.validate_token()
        
        status = "ready" if is_connected else ("setup_required" if not client_id else "auth_pending")
        
        return {
            "status": "success",
            "is_connected": is_connected,
            "client_id": client_id,
            "auth_status": status,
            "message": "Connected" if is_connected else ("Setup Required" if not client_id else "Authorization Pending")
        }

    def validate_token(self) -> bool:
        """
        Validates the current Google Chat token.
        Checks if the token exists, is not expired, or can be refreshed.
        Returns True if valid, False otherwise.
        """
        try:
            from python.helpers.secrets_helper import get_default_secrets_manager
            secrets_manager = get_default_secrets_manager()
            secrets = secrets_manager.load_secrets()
            token_json = secrets.get("GOOGLE_CHAT_TOKEN")

            if not token_json:
                for p in [self.token_path, self.alt_token_path]:
                    if p and os.path.exists(p):
                        with open(p, "r") as f:
                            token_json = f.read()
                        if token_json: break

            if not token_json:
                return False

            token_data = json.loads(token_json)
            # Add scopes if missing from token file for proper loading
            if "scopes" not in token_data:
                token_data["scopes"] = [
                    "https://www.googleapis.com/auth/chat.messages",
                    "https://www.googleapis.com/auth/chat.spaces.readonly",
                    "https://www.googleapis.com/auth/chat.memberships.readonly",
                    "https://www.googleapis.com/auth/drive"
                ]
            
            creds = Credentials.from_authorized_user_info(token_data)

            # Mandatory Scope Check: Prevent false "Connected" status (Issue #645)
            required_scopes = [
                "https://www.googleapis.com/auth/chat.messages",
                "https://www.googleapis.com/auth/chat.spaces.readonly",
                "https://www.googleapis.com/auth/chat.memberships.readonly"
            ]
            token_scopes = token_data.get("scopes", [])
            if not all(s in token_scopes for s in required_scopes):
                logger.warning(f"Google Chat token missing required scopes. Found: {token_scopes}")
                return False

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(GoogleRequest())
                        # Save refreshed token back to secrets and file
                        refreshed_json = creds.to_json()
                        secrets_manager.set_secret("GOOGLE_CHAT_TOKEN", refreshed_json)
                        with open(self.token_path, "w") as f:
                            f.write(refreshed_json)
                        return True
                    except Exception as refresh_err:
                        logger.warning(f"Failed to refresh Google Chat token: {refresh_err}")
                        return False
                return False

            # Optional: Perform a lightweight API call to verify the token actually works
            # service = build('chat', 'v1', credentials=creds)
            # service.spaces().list(pageSize=1).execute()
            
            return True
        except Exception as e:
            logger.error(f"Error validating Google Chat token: {e}")
            return False

    async def disconnect_oauth(self) -> dict:
        """
        Revokes the Google Chat integration by deleting secrets and token files.
        """
        try:
            from python.helpers.secrets_helper import get_default_secrets_manager
            secrets_manager = get_default_secrets_manager()
            
            # 1. Delete secrets from DB/env
            secrets_manager.delete_secret("GOOGLE_CHAT_CREDENTIALS")
            secrets_manager.delete_secret("GOOGLE_CHAT_TOKEN")
            
            # 2. Delete files from disk
            # We check both standard and alt paths
            paths_to_delete = [
                self.creds_path,
                self.token_path,
                "/agix/credentials.json",
                "/agix/token.json",
                "/agix/credentials.json",
                "/agix/token.json"
            ]
            
            deleted_files = []
            for path in paths_to_delete:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        deleted_files.append(path)
                    except Exception as fe:
                        logger.warning(f"Failed to delete {path}: {fe}")
            
            logger.info(f"Google Chat OAuth disconnected. Deleted secrets and files: {deleted_files}")
            return {
                "status": "success",
                "message": "Google Chat disconnected and credentials removed.",
                "deleted_files": deleted_files
            }
        except Exception as e:
            logger.error(f"Error during Google Chat disconnect: {e}")
            return {"status": "error", "message": f"Disconnect failed: {str(e)}"}
