import asyncio
import json
from flask import Flask, request, Response, session, redirect, url_for, render_template_string, send_from_directory, jsonify

from datetime import timedelta
import os
import re
import secrets
import time
import socket
import struct
from functools import wraps
import threading
import logging
logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger("app")
from python.helpers import runtime, dotenv_manager as dotenv, process, login, files, git_helper
from python.helpers.api import requires_loopback_async as requires_loopback, is_loopback_address
from werkzeug.wrappers.response import Response as BaseResponse
from python.helpers.print_style import PrintStyle

UI_DEBUG = os.environ.get("UI_DEBUG", "false").lower() == "true"

# Enable INFO level for agi-experimental context management modules
logging.getLogger("agix.context_watcher").setLevel(logging.INFO)
logging.getLogger("agix.context_recovery").setLevel(logging.INFO)

# Add console handler for context management logs
_az_console_handler = logging.StreamHandler()
_az_console_handler.setLevel(logging.INFO)
_az_console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger("agix.context_watcher").addHandler(_az_console_handler)
logging.getLogger("agix.context_recovery").addHandler(_az_console_handler)

# Add file handler for persistent context management logs
try:
    _az_log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(_az_log_dir, exist_ok=True)
    _az_file_handler = logging.FileHandler(os.path.join(_az_log_dir, "context_management.log"))
    _az_file_handler.setLevel(logging.INFO)
    _az_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logging.getLogger("agix.context_watcher").addHandler(_az_file_handler)
    logging.getLogger("agix.context_recovery").addHandler(_az_file_handler)
except Exception:
    pass  # Silently fail if log directory can't be created


# Set the new timezone to 'UTC'
os.environ["TZ"] = "UTC"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
# Apply the timezone change
if hasattr(time, 'tzset'):
    time.tzset()


# initialize the internal Flask server
def _get_webapp():
    # print("DEBUG: _get_webapp ENTER", flush=True)
    from python.helpers.files import get_abs_path
    webapp = Flask("app", static_folder=get_abs_path("./webui"), static_url_path="/")
    webapp.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    _cert = "/agix/cert.pem" if os.path.exists("/agix/cert.pem") else "/agix/cert.pem"
    _key = "/agix/key.pem" if os.path.exists("/agix/key.pem") else "/agix/key.pem"
    is_https = os.path.exists(_cert) and os.path.exists(_key)
    
    # SESSION HARDENING: Ensure cookies are secure and protected
    webapp.config.update(
        JSON_SORT_KEYS=False,
        SESSION_COOKIE_NAME="session_" + runtime.get_runtime_id(),
        SESSION_COOKIE_SAMESITE="Lax", # Lax is better for cross-domain redirects like OAuth
        SESSION_COOKIE_SECURE=is_https or os.environ.get("RAILWAY_ENVIRONMENT") is not None, 
        SESSION_COOKIE_HTTPONLY=True,   # CRITICAL: Prevent JS access to session cookie
        SESSION_PERMANENT=True,
        PERMANENT_SESSION_LIFETIME=timedelta(days=1)
    )
    return webapp

# Global webapp instance (deferred)
webapp = None

def get_webapp():
    global webapp
    # print(f"DEBUG: get_webapp ENTER (global webapp is {webapp})", flush=True)
    if webapp is None:
        webapp = _get_webapp()
        # print("DEBUG: Calling _register_core_routes", flush=True)
        _register_core_routes(webapp)
    return webapp

lock = threading.Lock()

# Loopback helpers imported from python.helpers.api

def requires_api_key(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        # 1. Check for API key in headers or body
        from python.helpers.settings import get_settings
        valid_api_key = get_settings().get("mcp_server_token")

        api_key = request.headers.get("X-API-KEY")
        from python.helpers.print_style import PrintStyle
        if UI_DEBUG: PrintStyle().debug(f"[AUTH_DEBUG] Comparing provided key '{api_key}' with valid key '{valid_api_key}'")
        if not api_key:
            # Try to get from JSON body safely
            json_data = request.get_json(silent=True)
            if json_data:
                api_key = json_data.get("api_key")

        if api_key:
            if api_key == valid_api_key:
                return await f(*args, **kwargs)
            return Response("Invalid API key", 401)

        # 2. Fallback to session-based auth (for Web UI)
        from python.helpers import login
        user_pass_hash = login.get_credentials_hash()
        if not user_pass_hash or session.get("authentication") == user_pass_hash:
            return await f(*args, **kwargs)

        return Response("API key or session required", 401)

    return decorated


# allow only loopback addresses
# @requires_loopback decorator imported from python.helpers.api


# require authentication for handlers
def requires_auth(f):
    if UI_DEBUG: print(f"[UI_DEBUG] requires_auth WRAPPING {f.__name__}", flush=True)
    @wraps(f)
    async def decorated(*args, **kwargs):
        if UI_DEBUG: print(f"[UI_DEBUG] requires_auth ENTER: {request.path}", flush=True)
        # 0. Check for API key bypass (same as csrf_protect)
        api_key = request.headers.get("X-API-KEY")
        if not api_key and request.method != 'GET':
            # Try to get from JSON body safely
            try:
                json_data = request.get_json(silent=True)
                if json_data:
                    api_key = json_data.get("api_key")
            except:
                pass
        
        if api_key:
            from python.helpers.settings import get_settings
            valid_api_key = get_settings().get("mcp_server_token")
            if valid_api_key and api_key == valid_api_key:
                return await f(*args, **kwargs)

        user_pass_hash = login.get_credentials_hash()
        # If no auth is configured:
        if not user_pass_hash:
            # In production, this is a misconfiguration - deny access
            if not runtime.is_development():
                print("[AUTH_CRITICAL] Production running without AUTH_LOGIN configured! Denying access.", flush=True)
                return Response("Authentication required but not configured. Please set AUTH_LOGIN and AUTH_PASSWORD.", 503)
            # In development, allow for convenience
            return await f(*args, **kwargs)

        auth_val = session.get('authentication')
        if auth_val != user_pass_hash:
            if UI_DEBUG: print(f"[AUTH_DEBUG] Redirecting to login. Session auth: {auth_val}, Expected: {user_pass_hash}", flush=True)
            return redirect(url_for('login_handler'))
        
        return await f(*args, **kwargs)

    return decorated

def csrf_protect(f):
    if UI_DEBUG: print(f"[UI_DEBUG] csrf_protect WRAPPING {f.__name__}", flush=True)
    @wraps(f)
    async def decorated(*args, **kwargs):
        if UI_DEBUG: print(f"[UI_DEBUG] csrf_protect ENTER: {request.path}", flush=True)
        # 0. Bypass CSRF for local development environments entirely
        if runtime.is_development():
            return await f(*args, **kwargs)

        # 1. Check for API key in headers or body - allow bypass if valid
        from python.helpers.settings import get_settings
        valid_api_key = get_settings().get("mcp_server_token")
        api_key = request.headers.get("X-API-KEY")
        if UI_DEBUG: print(f"[AUTH_DEBUG] Comparing provided key '{api_key}' with valid key '{valid_api_key}'", flush=True)
        if not api_key:
            json_data = request.get_json(silent=True)
            if json_data:
                api_key = json_data.get("api_key")
        
        if api_key and api_key == valid_api_key:
            return await f(*args, **kwargs)

        # 2. Exempt specific API endpoints from CSRF
        if request.path in ["/api/prompts/golden/save", "/api/prompts/golden/list", "/api/prompts/common", "/api/prompts/common/delete"]:
            return await f(*args, **kwargs)
            
        token = session.get("csrf_token")
        header = request.headers.get("X-CSRF-Token")
        cookie = request.cookies.get("csrf_token_" + runtime.get_runtime_id())
        sent = header or cookie
        if not token or not sent or token != sent:
            # Only log as error if there's an actual mismatch (potential attack)
            # If token is simply missing, it's often a startup race condition - log at debug
            _log_fn = PrintStyle.error if (token and sent and token != sent) else PrintStyle.debug
            
            _log_fn(f"CSRF Check Failed for {request.path}:")
            _log_fn(f"  - Session CSRF: {'[FOUND]' if token else '[MISSING]'}")
            _log_fn(f"  - Request CSRF Header: {'[FOUND]' if header else '[MISSING]'}")
            _log_fn(f"  - Request CSRF Cookie: {'[FOUND]' if cookie else '[MISSING]'}")
            _log_fn(f"  - Match: {'Yes' if token == sent else 'No'}")
            _log_fn(f"  - Origin: {request.headers.get('Origin')}")
            _log_fn(f"  - Referer: {request.headers.get('Referer')}")
            return Response("CSRF token missing or invalid", 403)
        return await f(*args, **kwargs)

    return decorated

def _register_core_routes(app):
    from python.helpers.analysis_feedback import record_analysis_feedback
    from flask import g
    import time



    @app.before_request
    def start_timer():
        g.start_time = time.time()
        # Log request start
        if UI_DEBUG:
            print(f"[UI_DEBUG] Request START: {request.method} {request.path}", flush=True)

    if UI_DEBUG: print("[UI_DEBUG] Registering enforce_production_auth hook", flush=True)
    @app.before_request
    def enforce_production_auth():
        """
        Global authentication guard for production environments.
        Ensures all endpoints require authentication unless explicitly exempted.
        
        ╔══════════════════════════════════════════════════════════════════════════════╗
        ║ ⚠️  CRITICAL: DO NOT REMOVE OR DISABLE THIS FUNCTION! ⚠️                      ║
        ║                                                                              ║
        ║ This guard ensures PRODUCTION (Railway) always requires authentication.     ║
        ║                                                                              ║
        ║ Production Domain: https://example.com                                     ║
        ║   - Login:    /agi/login                                                     ║
        ║   - Main App: /agi/ (after successful login)                                 ║
        ║                                                                              ║
        ║ Flow: Unauthenticated requests → redirect to /agi/login → authenticate →    ║
        ║       session token stored → redirect to /agi/                               ║
        ║                                                                              ║
        ║ Credentials are set via AUTH_LOGIN and AUTH_PASSWORD env vars.              ║
        ║ If missing in production, requests receive HTTP 503 (see requires_auth).    ║
        ╚══════════════════════════════════════════════════════════════════════════════╝
        """
        # AUTH REQUIRED BY DEFAULT
        path = request.path
        if UI_DEBUG: print(f"[DEBUG_AUTH] Hook execution START for path: {path}", flush=True)
        asset_extensions = ('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.json', '.webp', '.woff', '.woff2', '.ttf')
        
        is_explicit_dev = runtime.is_development()
        if UI_DEBUG: print(f"[AUTH_GUARD] Request: {request.method} {path} (is_development={is_explicit_dev})", flush=True)
        
        if is_explicit_dev:
            # Still check for asset extensions for logging purposes, but return None regardless
            if path.endswith(asset_extensions) and UI_DEBUG:
                print(f"[AUTH_GUARD] Asset detected in dev mode: {path}", flush=True)
            return None  # Dev mode - skip auth
        
        # Exempt paths that must be accessible without auth
        exempt_paths = [
            '/login', '/logout',
            '/health', '/healthz',  # Health checks for Railway
            '/favicon.ico', '/robots.txt',
        ]
        # Re-define or use existing asset_extensions
        exempt_prefixes = [
            '/webhook/',   # Webhooks have their own HMAC signature verification
            '/public/',    # Public assets (logo for login page, etc.)
            '/agi/public/', # Explicit prefix for mounted public assets
        ]
        # Static assets needed for login page styling
        login_assets = ['/login.css', '/login.js', '/agi/login.css', '/agi/login.js']
        
        # Check for asset extension bypass
        if path.endswith(asset_extensions):
            if UI_DEBUG: print(f"[AUTH_GUARD] Bypassing auth for asset: {path}", flush=True)
            return None
            
        # Check exact path exemptions
        if path in exempt_paths or path in login_assets:
            if UI_DEBUG: print(f"[AUTH_GUARD] Bypassing auth for exact path: {path}", flush=True)
            return None
        
        # Check prefix exemptions
        for prefix in exempt_prefixes:
            if path.startswith(prefix):
                if UI_DEBUG: print(f"[AUTH_GUARD] Bypassing auth for prefix: {path}", flush=True)
                return None
        
        # Check for valid API key (allows programmatic access)
        api_key = request.headers.get("X-API-KEY")
        if api_key:
            from python.helpers.settings import get_settings
            valid_api_key = get_settings().get("mcp_server_token")
            if valid_api_key and api_key == valid_api_key:
                return None
        
        # Check for valid session authentication
        user_pass_hash = login.get_credentials_hash()
        if user_pass_hash and session.get('authentication') == user_pass_hash:
            return None
        
        # Not authenticated - redirect to login
        print(f"[AUTH_GUARD] Unauthenticated request to {path}, redirecting to login.", flush=True)
        # Use explicit /agi/login to ensure it works across all mount scenarios
        return redirect("/agi/login")

    @app.after_request
    def log_request_time(response):
        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            if UI_DEBUG and not request.path.startswith('/static') and not request.path.endswith(('.js', '.css', '.png', '.jpg', '.ico')):
                print(f"[UI_DEBUG] Request END: {request.method} {request.path} - Duration: {duration:.3f}s - Status: {response.status_code}", flush=True)
                response.headers["X-Response-Time"] = f"{duration:.3f}s"

        # --- EDGE HARDENING & CORS ---
        origin = request.headers.get("Origin")
        allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
        # Add default domains and Railway dynamic detection
        base_allowed = ["example.com", "example.com"]
        if os.environ.get("RAILWAY_ENVIRONMENT"):
            # Trust any .example.com domain if we are on Railway
            base_allowed.append(".example.com")

        def is_origin_allowed(o):
            if not o: return True
            if o in allowed_origins: return True
            for b in base_allowed:
                if b in o: return True
            # Allow localhost/loopback in dev - relax check to allow any port for local dev
            if runtime.is_development() and ("localhost" in o or "127.0.0.1" in o or o.startswith("http://localhost:") or o.startswith("http://127.0.0.1:") or o.startswith("https://localhost:") or o.startswith("https://127.0.0.1:")): return True
            return False

        if origin and is_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token, X-API-KEY"
            response.headers["Access-Control-Allow-Credentials"] = "true"

        # Status Headers
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Security Headers - Only enable HSTS in production environments
        on_railway = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_NAME")
        if on_railway and not runtime.is_development():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        
        # CSP: Including example.com and example.com
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' example.com example.com *.example.com; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net blob: accounts.google.com example.com example.com http://localhost:3000 https://localhost:3000; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdn.jsdelivr.net stackpath.bootstrapcdn.com; "
            "font-src 'self' fonts.gstatic.com cdn.jsdelivr.net data:; "
            "img-src 'self' data: blob: img: *.googleusercontent.com *.google.com example.com example.com http://localhost:3000 https://localhost:3000; "
            "media-src 'self' data: blob:; "
            "connect-src 'self' blob: ws: wss: cdn.jsdelivr.net accounts.google.com example.com example.com https://127.0.0.1:8443 http://localhost:8880 http://localhost:3000 https://localhost:3000; "
            "frame-src 'self' accounts.google.com;"
        )
        
        return response

    @app.route("/login", methods=["GET", "POST"])
    @app.route("/login.html", methods=["GET", "POST"])  # Handle both to prevent raw template serving
    async def login_handler():
        error = None
        if request.method == 'POST':
            user = dotenv.get_dotenv_value("AUTH_LOGIN")
            password = dotenv.get_dotenv_value("AUTH_PASSWORD")
            
            if request.form['username'] == user and request.form['password'] == password:
                session['authentication'] = login.get_credentials_hash()
                # Redirect to the main app root
                dest = url_for('serve_index')
                if UI_DEBUG: print(f"[AUTH_DEBUG] Login successful. Redirecting to {dest}", flush=True)
                return redirect(dest)
            else:
                # EDGE HARDENING: Anti-Brute Force Delay
                PrintStyle.warning(f"Failed login attempt for user: {request.form.get('username')}")
                await asyncio.sleep(2.5)
                error = 'Invalid Credentials. Please try again.'
                
        # Use render_template_string to process Jinja tags
        login_page_content = files.read_file("webui/login.html")
        return render_template_string(login_page_content, error=error)

    @app.route("/logout")
    @app.route("/logout.html")
    async def logout_handler():
        session.pop('authentication', None)
        return redirect(url_for('login_handler'))

    # Root handler is handled by serve_index later
    

    @app.route("/api/feedback", methods=["POST"])
    @requires_auth
    @csrf_protect
    async def feedback_handler():
        try:
            data = request.get_json()
            if not data:
                return Response("Missing data", 400)
                
            message_id = data.get("message_id")
            feedback_type = data.get("type") # 'up' or 'down'
            content = data.get("content", "")
            
            # Convert to score for tracker
            score = 1 if feedback_type == "up" else -1
            
            # Extract issue # from content if possible (heuristic)
            issue_match = re.search(r'Issue #(\d+)', content)
            issue_id = f"#{issue_match.group(1)}" if issue_match else "unknown"
            
            success = record_analysis_feedback(
                issue_id=issue_id,
                quality_score=score,
                user_comment=f"Msg {message_id}: {content}"
            )
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            logger.error(f"Feedback error: {e}")
            return Response(str(e), 500)

    @app.route("/google_chat_auth", methods=["GET", "POST"])
    async def google_chat_auth_route():
        from python.api.google_chat_auth import GoogleChatAuth
        handler = GoogleChatAuth(app, lock)
        return await handler.handle_request(request=request)

    @app.route("/google_chat_oauth_callback", methods=["GET"])
    async def google_chat_oauth_callback():
        try:
            from google_auth_oauthlib.flow import Flow
            import os
            from flask import request as flask_request, redirect, url_for
            from python.helpers import runtime

            creds_path = "/agix/credentials.json" if os.path.exists("/agix/credentials.json") else "/agix/credentials.json"
            scopes = [
                "https://www.googleapis.com/auth/chat.messages",
                "https://www.googleapis.com/auth/chat.spaces.readonly",
                "https://www.googleapis.com/auth/chat.memberships.readonly",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/contacts.other.readonly"
            ]

            # protocol = "http"
            # protocol = "https" if flask_request.is_secure else "http"
            # We assume http for local dev unless cert.pem exists
            # Force https if we are on Railway
            CALLBACK_PATH = "/google_chat_oauth_callback"
            # Build redirect_uri: prefer OAUTH_REDIRECT_BASE_URL env var, then X-Forwarded-Host, then Host
            oauth_base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
            if oauth_base:
                redirect_uri = f"{oauth_base}{CALLBACK_PATH}"
            else:
                _cert_check = "/agix/cert.pem" if os.path.exists("/agix/cert.pem") else "/agix/cert.pem"
                protocol = "https" if (os.path.exists(_cert_check) or os.environ.get("RAILWAY_ENVIRONMENT")) else "http"
                # Prefer X-Forwarded-Host (set by reverse proxies) over Host header
                host = flask_request.headers.get("x-forwarded-host", flask_request.headers.get("host", f"localhost:{runtime.get_web_ui_port()}"))
                if flask_request.headers.get("x-forwarded-proto"):
                    protocol = flask_request.headers.get("x-forwarded-proto")
                redirect_uri = f"{protocol}://{host}{CALLBACK_PATH}"

            from python.helpers.secrets_helper import get_default_secrets_manager
            secrets_manager = get_default_secrets_manager()
            creds_json = secrets_manager.load_secrets().get("GOOGLE_CHAT_CREDENTIALS")
            
            client_config = None
            if creds_json:
                try:
                    client_config = json.loads(creds_json)
                except:
                    logger.warning("GOOGLE_CHAT_CREDENTIALS secret is not valid JSON in callback")

            if client_config:
                flow = Flow.from_client_config(
                    client_config,
                    scopes=scopes,
                    redirect_uri=redirect_uri
                )
            elif os.path.exists(creds_path):
                flow = Flow.from_client_secrets_file(
                    creds_path,
                    scopes=scopes,
                    redirect_uri=redirect_uri
                )
            else:
                return Response("Google Cloud credentials missing in callback", 400)

            # Mark: We don't verify state here for simplicity in this MVP, 
            # but ideally we should fetch it from session.
            auth_response_url = flask_request.url
            if protocol == "https" and auth_response_url.startswith("http:"):
                auth_response_url = auth_response_url.replace("http:", "https:", 1)
            
            # PKCE: Retrieve stored code_verifier for Desktop App clients
            from python.api.google_chat_auth import _oauth_pkce_store
            state_param = flask_request.args.get('state', '')
            if state_param and state_param in _oauth_pkce_store:
                flow.code_verifier = _oauth_pkce_store.pop(state_param)
                logger.info(f"[OAUTH] Retrieved PKCE code_verifier for state={state_param[:8]}...")
            
            flow.fetch_token(authorization_response=auth_response_url)

            credentials = flow.credentials
            token_json = credentials.to_json()
            
            # Save to file for legacy/compatibility
            token_path = "/agix/token.json" if os.path.exists("/agix") else "/agix/token.json"
            with open(token_path, "w") as token_file:
                token_file.write(token_json)
                
            # Save to Secrets Store for MCP and persistence
            secrets_manager.set_secret("GOOGLE_CHAT_TOKEN", token_json)

            # Redirect back to settings with a success flag
            return redirect("/?google_chat_auth=success&activeTab=oauth")
        except Exception as e:
            logger.error(f"Google Chat OAuth callback failed: {e}")
            return Response(f"OAuth callback failed: {str(e)}", 500)

    # handle default address, load index
    @app.route("/", methods=["GET"])
    @requires_auth
    async def serve_index():
        gitinfo = None
        try:
            gitinfo = git_helper.get_git_info()
        except Exception:
            gitinfo = {
                "version": "unknown",
                "commit_time": "unknown",
                "commit_hash": "unknown",
            }
        
        # Use absolute path to avoid resolution issues in production
        index_path = "/agix/webui/index.html"
        if not os.path.exists(index_path):
            index_path = "/agix/webui/index.html"
        if not os.path.exists(index_path):
            # Fallback to relative for local dev
            index_path = "webui/index.html"
            
        if UI_DEBUG: print(f"[UI_DEBUG] Serving index from: {index_path}", flush=True)
        try:
            index = files.read_file(index_path)
            index = files.replace_placeholders_text(
                _content=index,
                version_no=gitinfo["version"],
                version_time=gitinfo["commit_time"],
                version_hash=gitinfo["commit_hash"]
            )
            return index
        except Exception as e:
            PrintStyle.error(f"Error serving index: {str(e)}")
            return Response(f"Error serving index: {str(e)}", 500)

    @app.route("/health", methods=["GET"])
    @app.route("/healthz", methods=["GET"])
    def health_check():
        """Lightweight health check for Railway/infra with thread stats."""
        try:
            from python.helpers.thread_monitor import get_thread_stats
            stats = get_thread_stats()
            return jsonify({
                "status": "healthy",
                "timestamp": time.time(),
                "thread_count": stats["count"],
                "thread_peak": stats["peak"],
                "thread_threshold": stats["threshold"],
                "thread_names": stats["names"],
            }), 200
        except Exception:
            return jsonify({"status": "healthy", "timestamp": time.time()}), 200

    @app.route("/public/<path:filename>")
    def serve_public(filename):
        """Explicitly serve static assets from the public folder."""
        if UI_DEBUG: print(f"[DEBUG_ASSETS] Serving public file: {filename}")
        return send_from_directory(os.path.join(app.static_folder, "public"), filename)

def run(host: str = None, port: int = None, http_port: int = None, https_port: int = None):
    from python.helpers.extract_tools import load_classes_from_folder
    from python.helpers.api import ApiHandler
    from python.api.supervisor_endpoints import register_supervisor_routes
    from python.api.mode_endpoints import register_mode_endpoints
    from python.api.session_tasks_endpoints import register_session_tasks_routes
    from python.api.prompts_common import register_prompts_endpoints
    from python.helpers import mcp_server, fasta2a_server

    PrintStyle().print("Initializing framework...")

    # Suppress only request logs but keep the startup messages
    from werkzeug.serving import WSGIRequestHandler
    from werkzeug.serving import make_server
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from a2wsgi import ASGIMiddleware

    PrintStyle().print("Starting server...")

    # Sync core secrets to os.environ for sub-process/MCP accessibility EARLY
    try:
        from python.helpers.secrets_helper import get_default_secrets_manager
        get_default_secrets_manager().sync_to_environ()
        PrintStyle().print("Secrets synchronized to environment.")
    except Exception as e:
        PrintStyle().warning(f"Initial secret synchronization failed: {e}")

    # ── Boot-time Railway env → internal state reconciliation ─────────────
    # Railway pushes updated env vars on container restart but the persistent
    # data/settings.json and SecretsManager DB retain stale first-boot values.
    # EnvIntegrity.sync_railway_env_to_stores() bridges that gap using MD5
    # drift detection — same integrity engine used for alias sync and self-heal.
    try:
        from python.helpers.env_integrity import EnvIntegrity
        sync_result = EnvIntegrity.sync_railway_env_to_stores()
        n_s = len(sync_result.get("settings_updated", []))
        n_sec = len(sync_result.get("secrets_updated", []))
        if n_s or n_sec:
            PrintStyle().print(f"[EnvIntegrity] Reconciled {n_s} setting(s), {n_sec} secret(s) from Railway env")
        else:
            PrintStyle().print("[EnvIntegrity] All stores in sync — no changes needed")
    except Exception as e:
        PrintStyle().warning(f"[EnvIntegrity] Railway env sync failed (non-fatal): {e}")

    # AUTH STARTUP DIAGNOSTIC: Log auth configuration source for production debugging.
    # This helps identify the priority conflict between Railway env vars and .env file.
    _auth_login = os.environ.get("AUTH_LOGIN")
    _auth_from_dotenv = False
    try:
        _dotenv_path = dotenv.get_dotenv_file_path()
        if os.path.isfile(_dotenv_path):
            with open(_dotenv_path) as _f:
                _dotenv_raw = _f.read()
            _auth_from_dotenv = "AUTH_LOGIN=" in _dotenv_raw
    except Exception:
        pass
    if _auth_login:
        _source = "Railway env var (os.environ)" if not _auth_from_dotenv else "Railway env var (os.environ, .env file also present — Railway wins)"
        PrintStyle().print(f"[AUTH] AUTH_LOGIN configured ✓ | Source: {_source} | User: {_auth_login}")
    else:
        PrintStyle().warning("[AUTH] AUTH_LOGIN NOT configured — users will NOT be able to log in on production!")

    # Initialize Redis at startup to verify connectivity and discovery (DEBUG)
    try:
        from python.redis_client import RedisClient
        PrintStyle().info("Verifying Redis host discovery...")
        RedisClient.get_instance()
    except Exception as e:
        PrintStyle().warning(f"Redis initialization check failed: {e}")

    # Startup GC: clean up orphan chat dirs with empty/0-byte chat.json (#920, #911, #913)
    try:
        from python.helpers.lifecycle_service import LifecycleService
        removed = LifecycleService.gc_empty_chats()
        if removed:
            PrintStyle().print(f"Startup GC: cleaned {len(removed)} orphan chat dirs: {', '.join(removed[:5])}")
    except Exception as e:
        PrintStyle().warning(f"Startup GC failed: {e}")

    class NoRequestLoggingWSGIRequestHandler(WSGIRequestHandler):
        def log_request(self, code="-", size="-"):
            pass  # Override to suppress request logging

    # Get configuration from environment
    port = port or runtime.get_web_ui_port()
    host = (
        host or runtime.get_arg("host") or dotenv.get_dotenv_value("WEB_UI_HOST") or "localhost"
    )
    server = None

    registered_endpoints = set()

    def register_api_handler(app, handler: type[ApiHandler]):
        name = handler.__module__.split(".")[-1]
        
        # Prevent duplicate registration of the same function/endpoint
        # Flask's add_url_rule uses the endpoint name to store the view function.
        # We must ensure both the URL and the endpoint name are unique.
        if name in registered_endpoints:
            return
        registered_endpoints.add(name)
        
        instance = handler(app, lock)

        # Forgejo #762: async handler — thread capping via ASGI_THREADS env var.
        # Flask + flask[async] + asgiref creates threads for async handlers.
        # Set ASGI_THREADS=10 to cap the thread pool.
        async def handler_wrap() -> BaseResponse:
            return await instance.handle_request(request=request)

        if handler.requires_loopback():
            handler_wrap = requires_loopback(handler_wrap)
        if handler.requires_auth():
            handler_wrap = requires_auth(handler_wrap)
        if handler.requires_api_key():
            handler_wrap = requires_api_key(handler_wrap)
        if handler.requires_csrf():
            if UI_DEBUG: print(f"[UI_DEBUG] applying csrf_protect for {name}", flush=True)
            handler_wrap = csrf_protect(handler_wrap)

        if UI_DEBUG: print(f"[UI_DEBUG] Registered handler {name} with wrap {handler_wrap.__name__}", flush=True)
        # Set unique name to avoid Flask endpoint collision
        handler_wrap.__name__ = f"api_handler_{name}"

        app.add_url_rule(
            f"/{name}",
            name,  # Use name as the endpoint identifier
            handler_wrap,
            methods=handler.get_methods(),
        )

    # initialize and register API handlers
    webapp = get_webapp()
    
    # Root redirect handler for DispatcherMiddleware
    def root_redirect(environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path in ('/health', '/healthz'):
            if UI_DEBUG: print(f"[HEALTH_DEBUG] Root handler hit for {path}", flush=True)
            start_response('200 OK', [('Content-Type', 'application/json')])
            health_data = {"status": "healthy", "timestamp": time.time(), "source": "root"}
            try:
                from python.helpers.thread_monitor import get_thread_stats
                stats = get_thread_stats()
                health_data["thread_count"] = stats["count"]
                health_data["thread_peak"] = stats["peak"]
                health_data["thread_threshold"] = stats["threshold"]
                health_data["thread_names"] = stats["names"]
            except Exception:
                pass
            return [json.dumps(health_data).encode('utf-8')]
            
        start_response('302 Found', [('Location', '/agi/')])
        return [b'Redirecting...']

    handlers = load_classes_from_folder("python/api", "*.py", ApiHandler)
    for handler in handlers:
        register_api_handler(webapp, handler)

    # Register supervisor API routes
    register_supervisor_routes(webapp)
    
    # Register mode API routes (MultiAgentDev)
    register_mode_endpoints(webapp)
    
    # Register session tasks API routes
    register_session_tasks_routes(webapp)

    # Register prompts API routes (Common Prompts)
    register_prompts_endpoints(webapp)

    # Register webhook handler routes (GitHub, etc.)
    try:
        from python.helpers.webhook_handler import register_webhook_routes
        register_webhook_routes(webapp)
        PrintStyle().print("Webhook routes registered: /webhook/github, /webhook/health")
    except Exception as e:
        PrintStyle().warning(f"Webhook handler registration failed: {e}")

    # add the webapp, mcp, and a2a to the app
    # mounting the webapp at /agi ensures all its internal routes are sub-pathed
    middleware_routes = {
        "/agi": webapp,
        "/a2a": ASGIMiddleware(app=fasta2a_server.DynamicA2AProxy.get_instance()),  # type: ignore
    }
    # Mount MCP proxy only if DynamicMcpProxy is available
    if hasattr(mcp_server, 'DynamicMcpProxy'):
        middleware_routes["/mcp"] = ASGIMiddleware(app=mcp_server.DynamicMcpProxy.get_instance())  # type: ignore

    # DispatcherMiddleware uses root_redirect for '/' and maps other paths
    app = DispatcherMiddleware(root_redirect, middleware_routes)  # type: ignore

    # CRITICAL: Wrap with path rewriter so API routes work without /agi prefix.
    # The Flask app is mounted at /agi via DispatcherMiddleware, but external consumers
    # (smoke tests, webhooks, curl, etc.) use bare paths like /poll, /csrf_token.
    # This middleware transparently rewrites non-prefixed paths → /agi/* paths.
    class ApiPathRewriter:
        """WSGI middleware that rewrites bare API paths to /agi/* paths."""
        # Paths that are already handled by other DispatcherMiddleware mounts
        # or root-level handlers (like health checks)
        SKIP_PATHS = ('/', '/health', '/healthz')
        SKIP_PREFIXES = ('/agi', '/mcp', '/a2a')
        
        def __init__(self, inner_app):
            self.inner_app = inner_app
        def __call__(self, environ, start_response):
            path = environ.get('PATH_INFO', '')
            # Don't rewrite paths that already have a known prefix or are exactly in SKIP_PATHS
            if path in self.SKIP_PATHS or any(path.startswith(p) for p in self.SKIP_PREFIXES):
                if UI_DEBUG and path in self.SKIP_PATHS:
                    print(f"[HEALTH_DEBUG] ApiPathRewriter skipping rewrite for {path}", flush=True)
                return self.inner_app(environ, start_response)
            # Rewrite all other paths to /agi/* so Flask routes are found
            environ['PATH_INFO'] = '/agi' + path
            return self.inner_app(environ, start_response)
    
    app = ApiPathRewriter(app)  # type: ignore

    # SSL Setup
    ssl_context = None
    cert_path = "/agix/cert.pem" if os.path.exists("/agix/cert.pem") else "/agix/cert.pem"
    key_path = "/agix/key.pem" if os.path.exists("/agix/key.pem") else "/agix/key.pem"
    
    # Auto-generate self-signed cert if in dev mode and certs are missing
    if runtime.is_development() and not (os.path.exists(cert_path) and os.path.exists(key_path)):
        try:
            from python.helpers.generate_cert import generate_self_signed_cert
            generate_self_signed_cert(cert_path, key_path)
        except Exception as e:
            PrintStyle().error(f"Failed to auto-generate SSL certificates: {e}")

    on_railway = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_NAME")
    if os.path.exists(cert_path) and os.path.exists(key_path) and not on_railway:
        ssl_context = (cert_path, key_path)
        PrintStyle().info("SSL certificates found, enabling HTTPS support...")

    # Determine SSL context for the primary port
    # If a dedicated https_port is provided, we keep the primary port as HTTP
    primary_ssl_context = None
    if ssl_context:
        if https_port:
            primary_ssl_context = None
            PrintStyle().debug(f"Starting primary HTTP server at http://{host}:{port} ...")
        else:
            primary_ssl_context = ssl_context
            PrintStyle().debug(f"Starting primary HTTPS server at https://{host}:{port} ...")
    else:
        PrintStyle().debug(f"Starting server at http://{host}:{port} ...")

    server = make_server(
        host=host,
        port=port,
        app=app,
        request_handler=NoRequestLoggingWSGIRequestHandler,
        threaded=True,
        ssl_context=primary_ssl_context
    )
    process.set_server(server)
    server.log_startup()

    # Start dedicated HTTPS server if requested and certs exist
    if ssl_context and https_port:
        def run_https():
            try:
                PrintStyle().info(f"Starting dedicated HTTPS server on https://{host}:{https_port} ...")
                https_server = make_server(
                    host=host,
                    port=https_port,
                    app=app,
                    request_handler=NoRequestLoggingWSGIRequestHandler,
                    threaded=True,
                    ssl_context=ssl_context
                )
                https_server.serve_forever()
            except Exception as e:
                PrintStyle().error(f"HTTPS server failed: {e}")

        threading.Thread(target=run_https, daemon=True, name="https-server").start()

    # Start HTTP redirect server if enabled and SSL is on
    # HARDENING: Disable if we're on Railway as it handles redirects natively
    on_railway = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_NAME")
    http_redirect_port = http_port or runtime.get_http_redirect_port()
    # HARDENING: Disable if we're on Railway or in dev mode (unless forced)
    force_https = os.environ.get("FORCE_HTTPS", "").lower() == "true"
    if ssl_context and http_redirect_port and not on_railway and (not runtime.is_development() or force_https):
        def run_redirect():
            try:
                from flask import Flask, redirect, request as flask_request
                import os
                redirect_app = Flask("http_redirect")
                
                @redirect_app.route('/', defaults={'path': ''})
                @redirect_app.route('/<path:path>')
                def do_redirect(path):
                    # Check X-Forwarded-Proto to avoid infinite loops when behind a proxy like Railway
                    if flask_request.headers.get("X-Forwarded-Proto", "").lower() == "https":
                        # If we're already on HTTPS according to the proxy, we shouldn't be here,
                        # but if we are, just don't redirect again.
                        return "OK", 200

                    host = flask_request.headers.get("Host", "")
                    origin = f"http://{host}"
                    
                    # Try to find HTTPS counterpart in ALLOWED_ORIGINS to handle port changes (e.g. 8880 -> 8443)
                    allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
                    target_origin = None
                    if host:
                        hostname = host.split(":")[0]
                        # First look for an exact protocol match for this specific hostname
                        for o in allowed_origins:
                            if o.startswith("https://") and hostname in o:
                                target_origin = o.rstrip("/")
                                break
                    
                    if target_origin:
                        # Construct new URL using the discovered HTTPS origin
                        # flask_request.full_path includes leading /
                        new_url = f"{target_origin}{flask_request.full_path}"
                        return redirect(new_url, code=301)

                    # Simple protocol replacement fallback
                    target_url = flask_request.url.replace("http://", "https://", 1)
                    return redirect(target_url, code=301)
                
                PrintStyle().info(f"Starting HTTP redirect server on port {http_redirect_port}...")
                redirect_server = make_server(
                    host=host,
                    port=http_redirect_port,
                    app=redirect_app,
                    request_handler=NoRequestLoggingWSGIRequestHandler
                )
                redirect_server.serve_forever()
            except Exception as e:
                PrintStyle().error(f"HTTP redirect server failed: {e}")

        threading.Thread(target=run_redirect, daemon=True, name="http-redirect").start()

    # Start init_a0 in a background thread when server starts
    threading.Thread(target=init_a0, daemon=True, name="init-agix").start()

    # Start thread monitor daemon (Forgejo #762)
    try:
        from python.helpers.thread_monitor import start_monitor
        start_monitor()
    except Exception as e:
        print(f"[WARNING] Thread monitor failed to start: {e}", flush=True)

    # run the server
    server.serve_forever()


# Global supervisor event loop and thread
from typing import Optional
_supervisor_loop: Optional[asyncio.AbstractEventLoop] = None
_supervisor_thread: Optional[threading.Thread] = None


def _run_supervisor_loop(loop: asyncio.AbstractEventLoop):
    """Run the supervisor event loop in a background thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def init_a0():
    global _supervisor_loop, _supervisor_thread
    from python.helpers.database_client import DatabaseClient
    from python.helpers.supervisor_agent import SupervisorAgent, SupervisorConfig, set_llm_supervisor
    import python.initialize as initialize
    
    from python.helpers.print_style import PrintStyle
    try:
        # Standard DB initialization is handled lazily
        pass
    except Exception as e:
        PrintStyle().print(f"Warning: SQL Database initialization failed: {e}")

    # initialize agent tracing (check environment variable or default to enabled)
    import os
    from python.helpers.settings import get_settings
    s = get_settings()
    tracing_enabled = os.environ.get("AGENT_TRACING", "true").lower() == "true"
    if tracing_enabled:
        initialize.initialize_tracing(
            enabled=True,
            console_output=True,  # Print to console
            log_to_file=True,      # Write to logs/agent_trace_*.log
            log_to_context=s.get("agent_trace_to_context", False)
        )
    
    # Initialize LLM Supervisor BEFORE any agents are created
    # This ensures agents created during context initialization are registered
    raw_supervisor = os.environ.get("SUPERVISOR_ENABLED", "true")
    raw_llm = os.environ.get("LLM_SUPERVISOR_ENABLED", "true")
    print(f"[DEBUG] Raw env values: SUPERVISOR_ENABLED={repr(raw_supervisor)}, LLM_SUPERVISOR_ENABLED={repr(raw_llm)}", flush=True)
    
    # Accept both 'true' and '1' as truthy values
    supervisor_enabled = raw_supervisor.lower() in ("true", "1", "yes")
    llm_supervisor_enabled = raw_llm.lower() in ("true", "1", "yes")
    
    print(f"[DEBUG] Supervisor flags: supervisor_enabled={supervisor_enabled}, llm_supervisor_enabled={llm_supervisor_enabled}", flush=True)
    
    if supervisor_enabled and llm_supervisor_enabled:
        try:
            # Create a dedicated event loop for the supervisor that runs in a background thread
            _supervisor_loop = asyncio.new_event_loop()
            
            # Create LLM supervisor with default config
            config = SupervisorConfig(
                check_interval_minutes=3.0,
                context_condense_threshold=0.76,
                max_interventions_per_agent=5,
                intervention_cooldown_seconds=60.0,
            )
            supervisor = SupervisorAgent(config=config)
            
            # Set global supervisor BEFORE starting so extensions can find it
            set_llm_supervisor(supervisor)
            
            # Start supervisor in the dedicated loop
            future = asyncio.run_coroutine_threadsafe(supervisor.start(), _supervisor_loop)
            
            # Start the event loop in a background thread
            _supervisor_thread = threading.Thread(
                target=_run_supervisor_loop,
                args=(_supervisor_loop,),
                daemon=True,
                name="supervisor-event-loop"
            )
            _supervisor_thread.start()
            
            # Wait for supervisor to start (with timeout)
            try:
                future.result(timeout=5.0)
            except Exception as e:
                PrintStyle().print(f"Warning: Supervisor start timed out: {e}")
            
            PrintStyle().print("LLM Supervisor Agent started (background thread)")
        except Exception as e:
            PrintStyle().print(f"Warning: Failed to start LLM supervisor: {e}")
            
        # RCA-249 Phase 7: MasterAgentSupervisor permanently removed.
        # The L1/L2 pipeline (structural guards → IntelligentSupervisor)
        # is the sole supervisory architecture.
    
    # initialize contexts and MCP in background
    def background_init():
        from python.helpers import status
        try:
            # Give UI server a head start to become responsive
            print("UI Head Start: Waiting 5 seconds before background initialization...", flush=True)
            time.sleep(5)
            
            print("Starting background initialization (Chats, MCP, Preload)...", flush=True)
            status.set_status("initializing_agent")
            config = initialize.initialize_agent()
            
            # Load chats
            status.set_status("loading_chats")
            init_chats = initialize.initialize_chats(config=config)
            init_chats.result_sync() # Still wait here, but it's in a background thread

            # Issue #1095: Re-nudge any chat that was mid-execution when we crashed
            from python.helpers.crash_recovery import _post_restart_nudge as post_restart_nudge
            post_restart_nudge()

            # start job loop
            status.set_status("starting_job_loop")
            initialize.initialize_job_loop()
            
            # preload
            status.set_status("preloading")
            initialize.initialize_preload()
            
            status.set_status("ready")
            print("Background initialization successfully completed!", flush=True)
        except Exception as e:
            status.set_status("error")
            status.add_error(str(e))
            print(f"Error during background initialization: {e}", flush=True)
            import traceback
            traceback.print_exc()

    threading.Thread(target=background_init, daemon=True, name="background-init").start()



# run the internal server
if __name__ == "__main__":
    print("DEBUG: run_ui.py starting...", flush=True)
    runtime.initialize()
    print("DEBUG: Runtime initialized.", flush=True)
    dotenv.load_dotenv()
    print("DEBUG: Dotenv loaded.", flush=True)
    print(f"DEBUG: Starting run() on host={runtime.get_arg('host') or 'localhost'}, port={runtime.get_web_ui_port()}", flush=True)
    # FINAL AUDIT (Railway Debugging)
    try:
        from python.helpers.projects import PROJECTS_PARENT_DIR
        from python.helpers.files import get_base_dir
        PrintStyle().info(f"=== PRODUCTION AUDIT ===")
        PrintStyle().info(f"Base Directory: {get_base_dir()}")
        PrintStyle().info(f"Static Folder: {get_webapp().static_folder}")
        PrintStyle().info(f"Projects Parent: {PROJECTS_PARENT_DIR}")
        PrintStyle().info(f"Working Dir: {os.getcwd()}")
        PrintStyle().info(f"========================")
    except Exception as e:
        print(f"Audit log failed: {e}")

    run(
        host=runtime.get_arg("host") or "localhost",
        port=runtime.get_web_ui_port(),
        http_port=runtime.get_http_redirect_port(),
        https_port=runtime.get_https_port()
    )
    print("DEBUG: run_ui.py finished.", flush=True)
