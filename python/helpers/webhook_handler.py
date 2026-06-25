"""
GitHub Webhook Event Handler

Lightweight, deterministic event handler that:
1. Receives GitHub webhooks
2. Verifies signatures (HMAC-SHA256)
3. Detects state changes (via TaskStateManager pattern)
4. Routes events to agix agents (only when new events detected)

This module is designed for cost reduction - AI agents are only invoked
when actual events occur, not for polling/detection.
"""

import hashlib
import hmac
import json
import os
import re
import time
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Callable
from flask import Flask, request, jsonify

import logging

# Bounded thread pool for webhook background work (Forgejo #762)
# Prevents unbounded Thread().start() from exhausting OS threads
# CRITICAL: max_workers=1 serializes webhook processing to prevent event loop
# cross-contamination. Each webhook thread creates a new asyncio event loop, but
# shared singletons (Redis, rate limiter, MCP) have Futures/locks bound to specific
# loops. With max_workers>1, the 3rd+ thread hits "attached to a different loop"
# errors causing agents to silently hang without completing. Serial processing is
# slightly slower but 100% reliable. (RCA: Test 11 issue #40 hung indefinitely)
_webhook_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="webhook")
logger = logging.getLogger("webhook-handler")


# ============================================================================
# Configuration & Secrets Integration
# ============================================================================

# Cache paths
WEBHOOK_CONFIG_CACHE_PATH = "data/webhook_config.json"
REPO_PROJECT_CACHE_PATH = "data/webhook_repo_cache.json"

def get_webhook_secret(provider: str = "github") -> str:
    """
    Load webhook secret from secrets manager with fallback to env.
    
    Args:
        provider: 'github' or 'forgejo' - determines which secret key to use
    
    Priority: SecretsManager -> settings.json -> OS env -> empty string
    """
    # Determine key based on provider
    if provider == "forgejo":
        key = "FORGEJO_WEBHOOK_SECRET"
        fallback_key = "GITHUB_WEBHOOK_SECRET"  # Fall back to GitHub secret if not set
        settings_key = "forgejo_webhook_secret"
        settings_fallback_key = "github_webhook_secret"
    else:
        key = "GITHUB_WEBHOOK_SECRET"
        fallback_key = None
        settings_key = "github_webhook_secret"
        settings_fallback_key = None
    
    logger.debug(f"[WEBHOOK] get_webhook_secret called for provider={provider}, looking for key={key}")
    
    # Priority 1: SecretsManager (authoritative: DB -> .env files)
    try:
        from python.helpers.secrets_helper import get_default_secrets_manager
        sm = get_default_secrets_manager()
        secrets = sm.load_secrets()
        
        # Primary key
        if key in secrets and secrets[key] and secrets[key] != "***":
            logger.debug(f"[WEBHOOK] Found {key} in secrets manager (len={len(secrets[key])})")
            return secrets[key]
        
        # Fallback key (Forgejo can use GitHub secret if FORGEJO not set)
        if fallback_key and fallback_key in secrets and secrets[fallback_key] and secrets[fallback_key] != "***":
            logger.debug(f"[WEBHOOK] Using fallback key {fallback_key} from secrets manager")
            return secrets[fallback_key]
            
    except Exception as e:
        logger.debug(f"[WEBHOOK] Secrets manager unavailable ({e}), trying settings fallback")
    
    # Priority 2: settings.json (direct read — tries known absolute paths)
    try:
        import json as _json, os as _os
        _candidate_paths = [
            "/agix/data/settings.json",
            "/agix/data/settings.json",
            _os.path.join(_os.getcwd(), "data", "settings.json"),
        ]
        for _p in _candidate_paths:
            if _os.path.isfile(_p):
                with open(_p) as _f:
                    raw_settings = _json.load(_f)
                secret = raw_settings.get(settings_key, "")
                if secret and secret != "***":
                    logger.debug(f"[WEBHOOK] Found {settings_key} in {_p} (len={len(secret)})")
                    return secret
                if settings_fallback_key:
                    secret = raw_settings.get(settings_fallback_key, "")
                    if secret and secret != "***":
                        logger.debug(f"[WEBHOOK] Using fallback {settings_fallback_key} from {_p}")
                        return secret
                break  # Found the file but no secret in it — don't check others
    except Exception as e:
        logger.debug(f"[WEBHOOK] settings.json direct-read fallback failed: {e}")
    
    # Priority 3: OS Environment fallback (legacy/dev)
    secret = os.environ.get(key, "")
    if not secret and fallback_key:
        secret = os.environ.get(fallback_key, "")
    
    if secret:
        logger.debug(f"[WEBHOOK] Found {key} in OS environment (len={len(secret)})")
    else:
        logger.error(f"[WEBHOOK] Secret {key} not found in internal storage OR environment! Webhook verification will fail.")
    
    return secret


def load_webhook_config() -> Dict[str, Any]:
    """
    Load webhook configuration from settings.
    
    Returns config dict with enabled, auto_project, allowed_repos, workflows.
    """
    try:
        from python.helpers.settings import get_settings
        s = get_settings()
        return {
            "enabled": s.get("event_hooks_enabled", True),
            "auto_project": s.get("event_hooks_auto_project", True),
            "allowed_repos": [r.strip() for r in s.get("event_hooks_repos", "").split("\n") if r.strip()],
            "workflows": s.get("event_hooks_workflows", ["new_issue_analysis", "comment_response", "build_branch", "integration_merge", "health_monitoring"]),
            "command_triggers": s.get("event_hooks_command_triggers", {}),
            "prompt_templates": s.get("event_hooks_prompt_templates", {})
        }
    except Exception as e:
        logger.debug(f"Failed to load settings: {e}, using defaults")
        return {
            "enabled": True,
            "auto_project": True,
            "allowed_repos": [],  # Empty means allow all
            "workflows": ["new_issue_analysis", "comment_response", "build_branch", "integration_merge", "health_monitoring"],
            "command_triggers": {},
            "prompt_templates": {}
        }


def get_forgejo_url() -> str:
    """
    Resolve Forgejo URL from multiple sources with high priority fallback.
    Priority: OS ENV -> RAILWAY_SERVICE_FORGEJO_URL -> Settings -> Parameters
    """
    # 1. Direct OS ENV (authoritative)
    url = os.environ.get("FORGEJO_URL")
    if url: return url
    
    # 2. Railway Service Proxy
    url = os.environ.get("RAILWAY_SERVICE_FORGEJO_URL")
    if url: return url
    
    # 3. Settings
    try:
        from python.helpers.settings import get_settings
        s = get_settings()
        mcp_servers_json = s.get("mcp_servers")
        if mcp_servers_json:
            mcp_config = json.loads(mcp_servers_json)
            url = mcp_config.get("mcpServers", {}).get("forgejo", {}).get("env", {}).get("FORGEJO_URL")
            if url: return url
    except Exception:
        pass
        
    # 4. Parameters
    try:
        from python.helpers.parameters import get_parameters_manager
        pm = get_parameters_manager()
        params = pm.load_parameters()
        url = params.get("FORGEJO_URL")
        if url: return url
    except Exception:
        pass
        
    # Final fallback (default production URL)
    return "https://your-forgejo-instance.example.com"


def get_forgejo_token() -> str:
    """
    Resolve Forgejo Token from multiple sources.
    """
    token = os.environ.get("FORGEJO_TOKEN")
    if token: return token
    
    try:
        from python.helpers.settings import get_settings
        s = get_settings()
        mcp_servers_json = s.get("mcp_servers")
        if mcp_servers_json:
            mcp_config = json.loads(mcp_servers_json)
            token = mcp_config.get("mcpServers", {}).get("forgejo", {}).get("env", {}).get("FORGEJO_TOKEN")
            if token: return token
    except Exception:
        pass
        
    return ""



# ============================================================================
# Project Shim (Repo → Project Mapping)
# ============================================================================

_repo_cache: Dict[str, str] = None
_repo_cache_lock = threading.Lock()
# RCA-20260612 Issue 17: Prevent TOCTOU race in project auto-creation
_project_create_lock = threading.Lock()

def _load_repo_cache() -> Dict[str, str]:
    """Load repo→project cache from disk."""
    global _repo_cache
    if _repo_cache is not None:
        return _repo_cache
    
    cache_path = os.path.join(os.path.dirname(__file__), "..", "..", REPO_PROJECT_CACHE_PATH)
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                _repo_cache = json.load(f)
        else:
            _repo_cache = {}
    except Exception as e:
        logger.warning(f"Failed to load repo cache: {e}")
        _repo_cache = {}
    return _repo_cache


def normalize_repo_url(url: str) -> str:
    """
    Normalize repository URL for consistent mapping.
    - lowercase
    - strip protocol (http/https/git)
    - strip trailing /
    - strip .git suffix
    """
    if not url:
        return ""
    
    # Handle git@github.com:owner/repo
    if "@" in url and ":" in url and "://" not in url:
        url = url.split("@", 1)[1].replace(":", "/")
    
    url = url.lower().strip().rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]
        
    # Strip protocol prefix
    if "://" in url:
        url = url.split("://", 1)[1]
        
    return url


def _save_repo_cache(repo_url: str, project_name: str):
    """Save repo→project mapping to cache."""
    global _repo_cache
    with _repo_cache_lock:
        if _repo_cache is None:
            _repo_cache = _load_repo_cache()
        
        # Save both raw and normalized keys to be safe
        _repo_cache[repo_url] = project_name
        norm_url = normalize_repo_url(repo_url)
        if norm_url:
            _repo_cache[norm_url] = project_name
        
        cache_path = os.path.join(os.path.dirname(__file__), "..", "..", REPO_PROJECT_CACHE_PATH)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w') as f:
                json.dump(_repo_cache, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save repo cache: {e}")


def _validate_cached_project(project_name: str) -> bool:
    """Check if a cached project actually exists on disk."""
    if not project_name:
        return False
    try:
        from python.helpers import projects
        project_folder = projects.get_project_folder(project_name)
        meta_folder = projects.get_project_meta_folder(project_name)
        header_path = os.path.join(str(meta_folder), projects.PROJECT_HEADER_FILE)
        exists = os.path.exists(header_path)
        if not exists:
            logger.warning(f"[WEBHOOK] Cached project '{project_name}' no longer exists at {header_path}")
        return exists
    except Exception as e:
        logger.error(f"[WEBHOOK] Error validating project {project_name}: {e}")
        return False


def _invalidate_cache_entry(repo_url: str):
    """Remove a stale entry from the repo cache."""
    global _repo_cache
    with _repo_cache_lock:
        if _repo_cache is None:
            return
        
        # Remove exact and normalized keys
        if repo_url in _repo_cache:
            del _repo_cache[repo_url]
        
        norm_url = normalize_repo_url(repo_url)
        if norm_url and norm_url in _repo_cache:
            del _repo_cache[norm_url]
        
        # Persist cleanup
        cache_path = os.path.join(os.path.dirname(__file__), "..", "..", REPO_PROJECT_CACHE_PATH)
        try:
            with open(cache_path, 'w') as f:
                json.dump(_repo_cache, f, indent=2)
            logger.info(f"[WEBHOOK] Invalidated stale cache entry for {repo_url}")
        except Exception as e:
            logger.error(f"[WEBHOOK] Failed to persist cache invalidation: {e}")


def resolve_project_for_repo(owner: str, repo: str, provider: str = "github", payload: Dict[str, Any] = None) -> Optional[str]:
    """
    Resolve GitHub/Forgejo repo to agix project (cached 1:1 mapping).
    
    Returns project name if found/cached, None if needs to be created.
    """
    repo_url = ""
    if payload and "repository" in payload:
        repo_url = payload["repository"].get("clone_url", "") or payload["repository"].get("html_url", "")
    
    if not repo_url:
        if provider == "forgejo":
            # Heuristic for Forgejo/Gitea if URL not in payload
            domain = os.environ.get("FORGEJO_DOMAIN", "your-forgejo-instance.example.com")
            repo_url = f"https://{domain}/{owner}/{repo}.git"
        else:
            repo_url = f"https://github.com/{owner}/{repo}.git"
    
    # Check cache first (fastest) - but VALIDATE the project exists
    cache = _load_repo_cache()
    norm_url = normalize_repo_url(repo_url)
    logger.debug(f"[WEBHOOK] Resolving project for {owner}/{repo}. URL: {repo_url}, Normalized: {norm_url}")
    
    # Cache hit check with validation
    cached_project = None
    if repo_url in cache:
        cached_project = cache[repo_url]
    elif norm_url in cache:
        cached_project = cache[norm_url]
    
    if cached_project:
        # CRITICAL: Validate the cached project still exists
        if _validate_cached_project(cached_project):
            logger.info(f"[WEBHOOK] Cache hit (validated): {repo_url} -> {cached_project}")
            return cached_project
        else:
            # Project was deleted - invalidate stale cache
            logger.warning(f"[WEBHOOK] Stale cache entry: {repo_url} -> {cached_project} (project deleted)")
            _invalidate_cache_entry(repo_url)
    
    # 1. Search existing projects by repo URL parameter (Highest Precision)
    try:
        from python.helpers import projects
        projects_list = projects.get_active_projects_list()
        
        target_norm = normalize_repo_url(repo_url)
        for p in projects_list:
            p_name = p.get("name")
            if not p_name: continue
            
            # Check project parameters for matching URL
            params = projects.load_project_parameters(p_name)
            if params:
                try:
                    import json
                    p_params = json.loads(params)
                    p_url = p_params.get("FORGEJO_URL", "") + "/" + p_params.get("FORGEJO_OWNER", "") + "/" + p_params.get("FORGEJO_REPO", "") if provider == "forgejo" else p_params.get("REPO_URL", "")
                    if not p_url:
                        # Fallback to repo owner/name combo if URL not explicit
                        p_owner = p_params.get("REPO_OWNER") or p_params.get("FORGEJO_OWNER")
                        p_repo = p_params.get("REPO_NAME") or p_params.get("FORGEJO_REPO")
                        if p_owner and p_repo:
                            if p_owner.lower() == owner.lower() and p_repo.lower() == repo.lower():
                                logger.info(f"[WEBHOOK] Match found via project params (owner/repo): {p_name}")
                                _save_repo_cache(repo_url, p_name)
                                return p_name

                    if p_url and normalize_repo_url(p_url) == target_norm:
                        logger.info(f"[WEBHOOK] Match found via project params (URL): {p_name}")
                        _save_repo_cache(repo_url, p_name)
                        return p_name
                except (KeyError, ValueError, TypeError) as e:
                    logger.debug(f"[WEBHOOK] Error matching project {p_name}: {e}")

        # 2. Try finding by git remote (Filesystem check)
        # projects.find_project_by_git_remote already normalizes
        project_name = projects.find_project_by_git_remote(repo_url)
        
        if project_name:
            if _validate_cached_project(project_name):
                logger.info(f"[WEBHOOK] Found project for {repo_url} via remote matching: {project_name}")
                _save_repo_cache(repo_url, project_name)
                return project_name
            else:
                logger.warning(f"[WEBHOOK] Project '{project_name}' found by remote but is invalid/missing.")
            
        # 3. Match by project name conventions
        repo_lower = repo.lower()
        owner_lower = owner.lower()
        legacy_prefixed = f"repo-{repo_lower}"
        unique_prefixed = f"repo-{owner_lower}-{repo_lower}"
        
        # 3a. Try unique prefix first (repo-owner-repo)
        for p in projects_list:
            orig_name = p.get("name", "")
            if orig_name.lower() == unique_prefixed:
                logger.info(f"[WEBHOOK] Matching project found by unique repo prefix: {orig_name}")
                _save_repo_cache(repo_url, orig_name)
                return orig_name

        # 3b. Try legacy prefix (repo-repo)
        for p in projects_list:
            orig_name = p.get("name", "")
            if orig_name.lower() == legacy_prefixed:
                logger.info(f"[WEBHOOK] Matching project found by legacy repo prefix: {orig_name}")
                _save_repo_cache(repo_url, orig_name)
                return orig_name
                
        # 3c. Try direct name match
        for p in projects_list:
            orig_name = p.get("name", "")
            if not orig_name: continue
            if orig_name.lower() == repo_lower:
                logger.info(f"[WEBHOOK] Matching project found by direct name: {orig_name}")
                _save_repo_cache(repo_url, orig_name)
                return orig_name
        
        logger.info(f"[WEBHOOK] No matching project found for {repo_url} in {len(projects_list)} active projects")
                
    except Exception as e:
        logger.warning(f"[WEBHOOK] Error searching projects: {e}")
    
    # No project found - check if auto-create is enabled
    config = load_webhook_config()
    if config.get("auto_project", True):
        logger.info(f"[WEBHOOK] No project for {owner}/{repo}, auto-creating...")
        try:
            from python.helpers import projects
            # RCA-20260612 Issue 17: Lock to prevent TOCTOU race
            with _project_create_lock:
                # RCA-20260612 Issue 14: Include owner for multi-owner uniqueness
                new_project_name = f"repo-{owner}-{repo}"
                # Ensure name is unique
                cnt = 1
                temp_name = new_project_name
                while os.path.exists(projects.get_project_folder(temp_name)):
                    temp_name = f"{new_project_name}-{cnt}"
                    cnt += 1
                new_project_name = temp_name
                
                projects.create_project(new_project_name, {
                    "title": f"Auto: {owner}/{repo}",
                    "description": f"Automatically created for {owner}/{repo} via webhook.",
                    "instructions": f"Project for repository {owner}/{repo}.",
                    "color": "#10b981", # Emerald
                    "memory": "own",
                    "file_structure": projects._default_file_structure_settings()
                })

                # Initialize project parameters with repository details
                try:
                    import json
                    params_dict = {}
                    if provider == "forgejo":
                        params_dict["FORGEJO_OWNER"] = owner
                        params_dict["FORGEJO_REPO"] = repo
                        params_dict["FORGEJO_URL"] = os.environ.get("FORGEJO_URL", "https://your-forgejo-instance.example.com")
                    else:
                        params_dict["REPO_OWNER"] = owner
                        params_dict["REPO_NAME"] = repo
                        params_dict["REPO_URL"] = repo_url
                    
                    projects.save_project_parameters(new_project_name, json.dumps(params_dict))
                    logger.info(f"[WEBHOOK] Initialized parameters for project: {new_project_name}")
                except Exception as param_err:
                    logger.warning(f"[WEBHOOK] Failed to initialize project parameters: {param_err}")
                
                # Save the cache mapping so future webhooks find this project
                _save_repo_cache(repo_url, new_project_name)
                logger.info(f"[WEBHOOK] Auto-created project: {new_project_name}")
                return new_project_name
            
        except Exception as create_err:
            logger.error(f"[WEBHOOK] Failed to auto-create project: {create_err}")
    
    return None


# ============================================================================
# Signature Verification
# ============================================================================

def verify_webhook_signature(payload: bytes, headers: dict, secret: str) -> bool:
    """
    Verify webhook signature for GitHub, Forgejo, or Gitea.
    
    Supports multiple signature headers:
    - X-Hub-Signature-256 (GitHub) - format: sha256=<hex>
    - X-Forgejo-Signature (Forgejo) - format: <hex>
    - X-Gitea-Signature (Gitea) - format: <hex>
    
    Args:
        payload: Raw request body bytes
        headers: Request headers dict
        secret: Webhook secret
        
    Returns:
        True if any valid signature found, False otherwise
    """
    if not secret:
        return False
    
    # Compute expected HMAC-SHA256
    expected_hex = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    # Check GitHub signature (sha256=<hex>)
    github_sig = headers.get('X-Hub-Signature-256', '')
    if github_sig:
        logger.debug(f"[WEBHOOK] Checking X-Hub-Signature-256: {github_sig}")
        if github_sig.startswith('sha256='):
            actual = github_sig[7:]  # Remove 'sha256=' prefix
            if hmac.compare_digest(expected_hex, actual):
                logger.debug("[WEBHOOK] GitHub signature verified")
                return True
        else:
            # Some Forgejo versions might send plain hex in this header
            if hmac.compare_digest(expected_hex, github_sig):
                logger.debug("[WEBHOOK] GitHub-compatible signature verified (plain hex)")
                return True
    
    # Check Forgejo signature (plain hex)
    forgejo_sig = headers.get('X-Forgejo-Signature', '')
    if forgejo_sig:
        logger.debug(f"[WEBHOOK] Checking X-Forgejo-Signature: {forgejo_sig}")
        if hmac.compare_digest(expected_hex, forgejo_sig):
            logger.debug("[WEBHOOK] Forgejo signature verified")
            return True
    
    # Check Gitea signature (plain hex)
    gitea_sig = headers.get('X-Gitea-Signature', '')
    if gitea_sig:
        logger.debug(f"[WEBHOOK] Checking X-Gitea-Signature: {gitea_sig}")
        if hmac.compare_digest(expected_hex, gitea_sig):
            logger.debug("[WEBHOOK] Gitea signature verified")
            return True
    
    logger.warning(f"[WEBHOOK] Signature mismatch. Expected: {expected_hex[:10]}... Received headers: { {k: v for k, v in headers.items() if 'signature' in k.lower()} }")
    return False


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Legacy wrapper for backward compatibility with tests."""
    headers = {'X-Hub-Signature-256': signature} if signature else {}
    return verify_webhook_signature(payload, headers, secret)


# ============================================================================
# State Change Detection
# ============================================================================

class WebhookStateDetector:
    """
    Deterministic state change detection for webhooks.
    
    Follows the TaskStateManager pattern - tracks event IDs to prevent
    duplicate processing. State persists to JSON file.
    """
    
    _lock = threading.RLock()
    
    def __init__(self, storage_path: str = None):
        if storage_path is None:
            storage_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "..", "data", "webhook_state.json"
            )
        self.storage_path = storage_path
        self._cache: Dict[str, list] = {}
        self._load()
    
    def _load(self):
        """Load state from disk."""
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, 'r') as f:
                    self._cache = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load webhook state: {e}")
            self._cache = {}
    
    def _save(self):
        """Save state to disk."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, 'w') as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save webhook state: {e}")
    
    def is_new_event(self, source: str, event_id: str) -> bool:
        """Check if this event has been seen before."""
        with self._lock:
            key = f"handled_{source}"
            if key not in self._cache:
                return True
            return event_id not in self._cache[key]
    
    def mark_handled(self, source: str, event_id: str):
        """Mark an event as handled (prevents reprocessing)."""
        with self._lock:
            key = f"handled_{source}"
            if key not in self._cache:
                self._cache[key] = []
            
            if event_id not in self._cache[key]:
                self._cache[key].append(event_id)
                
                # Cap size to prevent unbounded growth (like TSM)
                if len(self._cache[key]) > 5000:
                    self._cache[key] = self._cache[key][-5000:]
                
                self._save()
                logger.info(f"[WEBHOOK] Marked event as handled: {source}/{event_id}")


# Global detector instance
_webhook_detector = None


def is_self_generated_comment(comment_body: str) -> bool:
    """
    Check if a comment was generated by agix itself to avoid feedback loops.
    
    CRITICAL: This function MUST detect ALL patterns used by the agent when posting
    comments, otherwise the agent's own comments will re-trigger webhook processing,
    creating a self-response loop that floods the system and drowns out real commands.
    """
    if not comment_body:
        return False
    # Patterns used by the agent when posting comments
    patterns = [
        # Active markers (used by repository_automation._comment and _answer_comment)
        "<!-- agix-id:",           # Hash-based dedup marker (primary agent marker)
        "<!-- agix-agent-comment -->",  # Legacy explicit agent marker
        # Analysis/expert response patterns
        "# 🤖 Comment Assistant:",   # Expert analysis header
        "### [Analysis Report]",     # Legacy analysis header
        "### [Build Status]",        # Legacy build status header
        "### [Action Completed]",    # Legacy action header
        # Build trigger response patterns
        "🚀 **Build Triggered**",    # Build trigger acknowledgment
    ]
    for pattern in patterns:
        if pattern in comment_body:
            return True
    return False

def is_bot_account(username: str, user_type: str = "") -> bool:
    """
    Check if a user is a known bot account.
    """
    if not username:
        return False
    if user_type.lower() == "bot":
        return True
    if username.lower().endswith("[bot]"):
        return True
    # Known bots
    bots = ["github-actions", "dependabot", "agix-bot", "agix-bot", "agix", "agix"]
    if username.lower() in bots:
        return True
    return False

def get_webhook_detector() -> WebhookStateDetector:
    """Get or create the global webhook state detector."""
    global _webhook_detector
    if _webhook_detector is None:
        _webhook_detector = WebhookStateDetector()
    return _webhook_detector


# ============================================================================
# Event Routing (Deterministic, No AI)
# ============================================================================

def route_github_event(event_type: str, payload: Dict[str, Any], config: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Route GitHub webhook event to appropriate workflow.
    
    This uses a dynamic command registry from config.
    
    Args:
        event_type: X-GitHub-Event header (e.g., 'issues', 'issue_comment')
        payload: Parsed JSON payload
        config: Webhook configuration dictionary
        
    Returns:
        Dict with 'workflow' key and relevant data
    """
    # Basic payload extraction
    action = payload.get("action", "")
    issue = payload.get("issue", {})
    issue_number = issue.get("number")
    
    # Detect effective workflow key (alias mapping)
    workflow_key = event_type
    if event_type == "workpackage":
        workflow_key = "workpackage_decomposition"
    elif event_type == "issues" and action == "opened":
        workflow_key = "new_issue_analysis"
    elif event_type == "issue_comment" and action == "created":
        workflow_key = "comment_response"

    # 1. Dynamic Routing for configured events
    templates = config.get("prompt_templates", {})
    if workflow_key in templates or event_type in templates:
        logger.info(f"[WEBHOOK] Routing event '{event_type}/{action}' via dynamic config")
        
        # Standard variables for templates
        repo_data = payload.get("repository", {})
        repo_owner = repo_data.get("owner", {}).get("login", "") if isinstance(repo_data.get("owner"), dict) else ""
        repo_name = repo_data.get("name", "")
        wf_data = {
            "workflow": workflow_key if workflow_key in templates else event_type,
            "issue_number": issue_number,
            "title": issue.get("title", ""),
            "body": issue.get("body") or "",
            "action": action,
            "event_type": event_type,
            "owner": repo_owner,
            "repo": repo_name,
        }
        
        # Carry workpackage attachments through to dispatch
        if event_type == "workpackage":
            wf_data["attachments"] = payload.get("attachments", [])
        
        # Add comment-specific data
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "") or ""
        wf_data.update({
            "comment_id": comment.get("id"),
            "comment_body": comment_body
        })
        
        # Loop & Bot prevention
        if event_type == "issue_comment":
            if is_self_generated_comment(comment_body):
                 logger.info(f"[WEBHOOK] Skipping self-generated comment. Body: {comment_body[:100]}...")
                 return {"workflow": "skip", "reason": "self_generated_comment"}
            
            comment_user = comment.get("user", {})
            comment_username = comment_user.get("login", "") if isinstance(comment_user, dict) else ""
            comment_user_type = comment_user.get("type", "") if isinstance(comment_user, dict) else ""
            if is_bot_account(comment_username, comment_user_type):
                 logger.info(f"[WEBHOOK] Skipping bot comment from {comment_username}. Type: {comment_user_type}")
                 return {"workflow": "skip", "reason": f"bot_account:{comment_username}"}

        # Check for command triggers (Dynamic Mapping Override)
        command_triggers = config.get("command_triggers", {})
        search_text = comment_body or wf_data.get("body", "")
        for pattern, wf_name in command_triggers.items():
            if re.search(pattern, search_text, re.IGNORECASE):
                logger.info(f"[WEBHOOK] Command trigger matched: {pattern} -> {wf_name}")
                wf_data["workflow"] = wf_name
                return wf_data # Command takes priority
                
        return wf_data

    # Legacy/Default handling if no dynamic templates found
    if event_type == "issues" and action == "opened":
        return {
            "workflow": "new_issue_analysis",
            "issue_number": issue_number,
            "title": issue.get("title", ""),
            "body": issue.get("body") or ""
        }
    
    if event_type == "issue_comment" and action == "created":
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")
        if is_self_generated_comment(comment_body):
            return {"workflow": "skip", "reason": "self_generated_comment"}
            
        return {
            "workflow": "comment_response",
            "issue_number": issue_number,
            "comment_id": comment.get("id"),
            "comment_body": comment_body
        }


    # AGIX Space work package events (fallback if no dynamic template)
    if event_type == "workpackage":
        repo_data = payload.get("repository", {})
        repo_owner = repo_data.get("owner", {}).get("login", "") if isinstance(repo_data.get("owner"), dict) else ""
        repo_name = repo_data.get("name", "")
        return {
            "workflow": "workpackage_decomposition",
            "issue_number": issue_number,
            "title": issue.get("title", ""),
            "body": issue.get("body") or "",
            "action": action,
            "event_type": event_type,
            "owner": repo_owner,
            "repo": repo_name,
            "attachments": payload.get("attachments", []),
        }

    # Push events (for future use)
    if event_type == "push":
        return {"workflow": "skip", "reason": "Push events not yet implemented"}
    
    # Unknown events
    return {"workflow": "skip", "reason": f"Unhandled event/action: {event_type}/{action}"}


# ============================================================================
# Agent Triggering
# ============================================================================

def save_webhook_attachments(
    project_name: str,
    attachments: List[Dict[str, Any]],
) -> List[str]:
    """
    Decode base64 `attachments[]` from a workpackage webhook payload
    and write them to the project's uploads directory.

    Each attachment has: uid, label, filename, mimeType, sizeBytes, data (base64).
    Files are saved to: {project_folder}/uploads/workpackage-attachments/

    Returns list of saved file paths (absolute).
    """
    if not project_name or not attachments:
        return []

    from python.helpers import projects
    import base64

    project_folder = projects.get_project_folder(project_name)
    upload_dir = os.path.join(project_folder, "uploads", "workpackage-attachments")
    os.makedirs(upload_dir, exist_ok=True)

    saved_paths = []
    for att in attachments:
        try:
            filename = att.get("filename", f"attachment-{att.get('uid', 'unknown')}.png")
            # Sanitize filename: replace URL-unsafe chars (&, ?, #, %, etc.) with hyphens
            filename = re.sub(r'[&?#%=+;\s]+', '-', filename)
            filename = re.sub(r'-{2,}', '-', filename)  # collapse multiple hyphens
            data_b64 = att.get("data", "")
            if not data_b64:
                logger.warning(f"[WEBHOOK] Attachment '{filename}' has no data — skipping")
                continue

            raw_bytes = base64.b64decode(data_b64)
            filepath = os.path.join(upload_dir, filename)
            with open(filepath, "wb") as f:
                f.write(raw_bytes)

            saved_paths.append(filepath)
            logger.info(f"[WEBHOOK] Saved attachment: {filename} ({len(raw_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"[WEBHOOK] Failed to save attachment {att.get('filename')}: {e}")

    return saved_paths


def upload_attachments_to_github(
    owner: str,
    repo: str,
    attachment_paths: List[str],
    branch: str = "main",
) -> Dict[str, str]:
    """
    Upload saved attachment files to a GitHub repo via the Contents API.

    Files are committed to `docs/mockups/{filename}` in the target repo.
    Returns a dict mapping each filename to its raw GitHub URL, e.g.:
      { "dashboard-abc123.png": "https://raw.githubusercontent.com/owner/repo/main/docs/mockups/dashboard-abc123.png" }

    Falls back gracefully — if any upload fails (missing token, permissions),
    it logs the error and returns an empty dict (non-fatal).
    """
    import base64
    import requests as http_requests
    from urllib.parse import quote as url_quote

    if not attachment_paths:
        return {}

    # Get GitHub token via centralized credentials (no context needed — uses secrets + env)
    from python.helpers.credentials import get_github_credentials
    creds = get_github_credentials(params={"owner": owner, "repo": repo})
    token = creds.token

    if not token:
        logger.warning("[WEBHOOK] No GitHub token available — cannot upload attachments to repo")
        return {}

    url_map: Dict[str, str] = {}
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AGIX-Webhook/1.0",
    }

    for filepath in attachment_paths:
        filename = os.path.basename(filepath)
        gh_path = f"docs/mockups/{filename}"
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{gh_path}"

        try:
            with open(filepath, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode("utf-8")

            # Check if file already exists (need SHA for update)
            sha = None
            check_resp = http_requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
            if check_resp.status_code == 200:
                sha = check_resp.json().get("sha")

            payload = {
                "message": f"chore: add workpackage mockup {filename}",
                "content": content_b64,
                "branch": branch,
            }
            if sha:
                payload["sha"] = sha

            put_resp = http_requests.put(api_url, headers=headers, json=payload, timeout=60)
            if put_resp.status_code in (200, 201):
                # Use github.com/{owner}/{repo}/raw/ URL format — this goes through
                # GitHub's auth proxy, so it works for both public AND private repos
                # for authenticated users. raw.githubusercontent.com does NOT work
                # for private repos without auth tokens.
                encoded_path = url_quote(gh_path, safe='/')
                proxied_url = f"https://github.com/{owner}/{repo}/raw/{branch}/{encoded_path}"
                url_map[filename] = proxied_url
                logger.info(f"[WEBHOOK] Uploaded mockup to GitHub: {gh_path} -> {proxied_url}")
            else:
                logger.warning(f"[WEBHOOK] GitHub upload failed for {filename}: HTTP {put_resp.status_code} {put_resp.text[:200]}")
        except Exception as e:
            logger.warning(f"[WEBHOOK] Failed to upload {filename} to GitHub: {e}")

    return url_map


async def _post_error_comment(
    provider: str, owner: str, repo: str, issue_number: int, error_msg: str
):
    """Post error feedback comment to issue when agent processing fails.
    
    RCA-20260612 Issue 10: Users got zero feedback when the agent failed.
    This posts a standardized error comment directly via the GitHub/Forgejo
    API (bypassing the agent, which is dead at this point).
    """
    try:
        body = (
            f"⚠️ **AGIX Processing Error**\n\n"
            f"The automated workflow encountered an error:\n"
            f"```\n{str(error_msg)[:500]}\n```\n\n"
            f"_This is an automated error notification._"
        )
        if provider == "forgejo":
            from python.tools.repo_automation.forgejo import comment_forgejo
            from python.helpers.credentials import ForgejoCredentials
            url = os.environ.get("FORGEJO_URL", "")
            token = os.environ.get("FORGEJO_TOKEN", "")
            if token and url:
                creds = ForgejoCredentials(url=url, token=token, owner=owner, repo=repo)
                await comment_forgejo(creds, issue_number, body)
        else:
            from python.tools.repo_automation.github import comment_github
            from python.helpers.credentials import GitHubCredentials
            token = os.environ.get("GITHUB_TOKEN", "")
            if token:
                creds = GitHubCredentials(token=token, owner=owner, repo=repo)
                await comment_github(creds, issue_number, body)
        logger.info(f"[WEBHOOK] Posted error comment to {provider} issue #{issue_number}")
    except Exception as post_err:
        logger.error(f"[WEBHOOK] Failed to post error comment: {post_err}")


async def trigger_agent_workflow(
    workflow: str,
    issue_number: int,
    owner: str = None,
    repo: str = None,
    project_name: str = None,
    provider: str = "github",  # github or forgejo
    delivery_id: str = None,   # Unique delivery ID for workpackage context isolation
    **kwargs
) -> bool:
    """
    Trigger agix agent for a specific workflow.
    
    Args:
        workflow: Workflow type (new_issue_analysis, comment_response, build_branch)
        issue_number: GitHub/Forgejo issue number
        owner: Repository owner
        repo: Repository name
        project_name: agix project name to activate (optional)
        provider: Source platform - 'github' or 'forgejo'
        
    Returns:
        True if triggered successfully
    """
    try:
        # Import here to avoid circular imports
        from python.agent import AgentContext, AgentContextType
        from python.helpers import projects
        
        # Determine owner/repo from environment if not provided
        if not owner:
            owner = os.environ.get("GITHUB_WEBHOOK_OWNER", "your-bot-username")
        if not repo:
            repo = os.environ.get("GITHUB_WEBHOOK_REPO", "agix-test")
        
        # Create context ID for this webhook event.
        # workpackage_decomposition: unique ID per delivery so every delivery spawns a
        # fresh agent context — prevents reuse of a completed context across LIT runs.
        # All other workflows: deterministic ID groups related events for the same issue.
        effective_delivery_id = delivery_id or kwargs.get("delivery_id") or ""
        if workflow == "workpackage_decomposition" and effective_delivery_id:
            safe_delivery = effective_delivery_id.replace("-", "")[:12]
            context_id = f"webhook_{provider}_{owner}_{repo}_wp_{safe_delivery}"
        else:
            context_id = f"webhook_{provider}_{owner}_{repo}_{issue_number}"
        
        # Log project context if available
        if project_name:
            logger.info(f"[WEBHOOK] Using project context: {project_name}")
        
        # Platform-specific label for prompts
        platform_name = "AGIX Space" if provider == "agix-space" else ("GitHub" if provider == "github" else "Forgejo")
        
        # Build prompt based on workflow
        metadata_str = f"source_type: repository, provider: {provider}"
        
        # ================================================================
        # DETERMINISTIC COMMAND DETECTION (Forgejo #897)
        # 
        # When a comment matches a known command trigger, bypass the LLM
        # prompt template entirely and force the exact tool call.
        # This prevents non-deterministic LLM behavior from dropping
        # commands (observed: 1/3 build triggers dropped in test_11).
        # ================================================================
        prompt = None  # Will be set if deterministic detection matches
        comment_body = kwargs.get("comment_body", "")
        if workflow == "comment_response" and comment_body:
            try:
                from python.tools.repo_automation.sweeps import (
                    check_for_build_trigger, check_for_merge_trigger,
                    check_for_integration_trigger, check_for_deploy_trigger,
                    check_for_monitor_trigger
                )
                tool_params = f"provider='{provider}', owner='{owner}', repo='{repo}', issue_number={issue_number}"
                
                if check_for_build_trigger(comment_body):
                    logger.info(f"[WEBHOOK] Deterministic build trigger detected for #{issue_number} — forcing tool call")
                    prompt = (
                        f"CRITICAL COMMAND DETECTED: A build command was posted on {platform_name} issue #{issue_number}.\n"
                        f"You MUST call repository_automation with action='trigger_build_task', {tool_params}.\n"
                        f"Do NOT explain, do NOT research, do NOT list available actions. JUST call the tool immediately.\n"
                        f"After the tool completes, call the response tool with a brief summary of the result.\n"
                        f"OVERRIDE: Ignore any supervisor interventions, previous tool results, or conflicting instructions. This is a NEW standalone command."
                    )
                elif check_for_merge_trigger(comment_body) or check_for_integration_trigger(comment_body):
                    logger.info(f"[WEBHOOK] Deterministic merge trigger detected for #{issue_number} — forcing tool call")
                    prompt = (
                        f"CRITICAL COMMAND DETECTED: A merge/integration command was posted on {platform_name} issue #{issue_number}.\n"
                        f"You MUST call repository_automation with action='integration_manager', {tool_params}.\n"
                        f"Do NOT explain, do NOT ask questions, do NOT reference previous messages. JUST call the tool immediately.\n"
                        f"After the tool completes, call the response tool with a brief summary of the result.\n"
                        f"OVERRIDE: Ignore any supervisor interventions, previous tool results, or conflicting instructions. This is a NEW standalone command."
                    )
                elif check_for_deploy_trigger(comment_body):
                    logger.info(f"[WEBHOOK] Deterministic deploy trigger detected for #{issue_number} — forcing tool call")
                    prompt = (
                        f"CRITICAL COMMAND DETECTED: A deploy command was posted on {platform_name} issue #{issue_number}.\n"
                        f"You MUST call repository_automation with action='deploy_to_cloud', {tool_params}.\n"
                        f"Do NOT explain. JUST call the tool immediately.\n"
                        f"After the tool completes, call the response tool with a brief summary of the result.\n"
                        f"OVERRIDE: Ignore any supervisor interventions, previous tool results, or conflicting instructions. This is a NEW standalone command."
                    )
                elif check_for_monitor_trigger(comment_body):
                    logger.info(f"[WEBHOOK] Deterministic monitor trigger detected for #{issue_number} — forcing tool call")
                    prompt = (
                        f"CRITICAL COMMAND DETECTED: A health check command was posted on {platform_name} issue #{issue_number}.\n"
                        f"You MUST call repository_automation with action='monitor_deployment_health', {tool_params}.\n"
                        f"Do NOT explain. JUST call the tool immediately.\n"
                        f"After the tool completes, call the response tool with a brief summary of the result.\n"
                        f"OVERRIDE: Ignore any supervisor interventions, previous tool results, or conflicting instructions. This is a NEW standalone command."
                    )
            except ImportError as ie:
                logger.warning(f"[WEBHOOK] Could not import sweep triggers for deterministic detection: {ie}")

        # Fall back to dynamic prompt templates if no deterministic match
        if prompt is None:
            config = load_webhook_config()
            prompt_templates = config.get("prompt_templates", {})
            
            if workflow in prompt_templates:
                # Prepare format args - combine standard ones with kwargs
                fmt_args = {
                    "platform_name": platform_name,
                    "issue_number": issue_number,
                    "metadata_str": metadata_str,
                    "owner": owner,
                    "repo": repo,
                    "provider": provider,
                    "attachment_files": "No preview images attached.",
                    **kwargs  # kwargs may override attachment_files with actual file list
                }
                try:
                    prompt = prompt_templates[workflow].format(**fmt_args)
                    # CRITICAL: Append no-delegation directive for repository_automation.
                    # The Multiagentdev orchestrator has repository_automation in its own
                    # toolset, but frequently delegates to Code which does NOT have it,
                    # causing escalation loops and timeout failures.
                    prompt += (
                        "\n\nAfter completing the action, you MUST call the response tool "
                        "with a brief summary of what was done so the user can see the result."
                        "\n\n🔴 CRITICAL: You MUST call repository_automation DIRECTLY. "
                        "Do NOT delegate this to a subordinate agent (code, researcher, etc.) — "
                        "they do NOT have repository_automation in their toolset. "
                        "Call the tool yourself in this conversation."
                    )
                except Exception as fmt_err:
                    logger.error(f"[WEBHOOK] Failed to format prompt template for {workflow}: {fmt_err}")
                    return False
            elif workflow == "workpackage_decomposition":
                # Hardcoded fallback — tenants provisioned before this template was added
                # to tenant_defaults.json won't have it in their settings store.
                logger.info(f"[WEBHOOK] Using built-in workpackage_decomposition template (not in settings)")
                fmt_args = {
                    "platform_name": platform_name,
                    "issue_number": issue_number,
                    "metadata_str": metadata_str,
                    "owner": owner,
                    "repo": repo,
                    "provider": provider,
                    "attachment_files": "No preview images attached.",
                    **kwargs
                }
                builtin_template = (
                    "A new work package has arrived from the AGIX frontend onboarding system.\n"
                    "[METADATA] {metadata_str}\n\n"
                    "Work Package Title: {title}\n"
                    "Work Package Details:\n{body}\n\n"
                    "Preview Image Mockups (uploaded to repo):\n{attachment_files}\n\n"
                    "Your task:\n"
                    "1. Decompose this work package into 3-5 concrete, actionable GitHub issues.\n"
                    "2. For EACH issue, call repository_automation with:\n"
                    "   action=\"create_issue\"\n"
                    "   provider=\"github\"\n"
                    "   owner=\"{owner}\"\n"
                    "   repo=\"{repo}\"\n"
                    '   title="<descriptive title> [work-package]"\n'
                    '   body="<task description with acceptance criteria>"\n'
                    '3. CRITICAL: provider MUST be "github" (not forgejo). owner MUST be "{owner}". repo MUST be "{repo}".\n'
                    "4. For frontend issues, EMBED the mockup images inline in the issue body using markdown image syntax. "
                    "The images are already uploaded to the repo — copy the ![image](url) references from the attachment "
                    "list above directly into each issue body so the mockups render visually on GitHub.\n"
                    "5. Create all 3-5 issues. Do not ask for confirmation.\n"
                )
                try:
                    prompt = builtin_template.format(**fmt_args)
                except Exception as fmt_err:
                    logger.error(f"[WEBHOOK] Failed to format built-in workpackage template: {fmt_err}")
                    return False
            else:
                logger.warning(f"[WEBHOOK] Unknown workflow OR no template found: {workflow}")
                return False
            
            # Append response tool instruction for Simple Chat visibility
            prompt += "\n\nAfter completing the action, you MUST call the response tool with a brief summary of what was done so the user can see the result."
        
        logger.info(f"[WEBHOOK] Triggering workflow '{workflow}' for issue #{issue_number}")
        
        # Use direct AgentContext.communicate() - HTTP API doesn't work inside container
        try:
            from python.agent import AgentContext, UserMessage
            from python.helpers import projects as projects_helper
            
            # Get or create agent context
            context = AgentContext.get(context_id)
            if not context:
                # Create new context for this webhook workflow with proper metadata
                from python.agent import AgentContextType
                from python.helpers import persist_chat
                
                context_name = f"[{platform_name}] #{issue_number} - {workflow.replace('_', ' ').title()}"
                
                # RCA-webhook-20260612: Force multiagentdev profile for webhook contexts.
                # Without this, AgentContext(config=None) falls through to initialize_agent()
                # which loads the user's GLOBAL default profile (e.g., "alex" or "default").
                # Repo automation requires the multiagentdev orchestrator profile which has
                # repository_automation tool access and proper delegation capabilities.
                from python.initialize import initialize_agent
                webhook_config = initialize_agent(
                    override_settings={"agent_profile": "multiagentdev"},
                    context_id=context_id
                )
                
                context = AgentContext(
                    id=context_id,
                    name=context_name,
                    config=webhook_config,
                    type=AgentContextType.EVENT_HOOK,
                    data={"project": project_name} if project_name else {},
                    skip_agent_init=False
                )
                
                # Explicitly save so the UI can discover it
                persist_chat.save_tmp_chat(context)
                logger.info(f"[WEBHOOK] Created and saved context: {context_id} ({context_name})")
            
            # Activate project if available (this also handles config refreshes)
            if project_name:
                try:
                    await projects_helper.activate_project(context_id, project_name)
                    logger.info(f"[WEBHOOK] Activated project: {project_name}")
                except Exception as proj_err:
                    logger.warning(f"[WEBHOOK] Failed to activate project: {proj_err}")
            
            # Send message to agent (async in background thread)
            async def _send_message():
                try:
                    # CRITICAL FIX: Ensure the context ID is bound to this thread/task
                    # This prevents AgentContext.current() from falling back to UI context
                    AgentContext.set_current(context_id)
                    
                    # Use queue_if_busy=True so that if the agent is already
                    # processing a previous workflow (e.g., analysis), this
                    # message is queued and processed after the current
                    # monologue completes, instead of being set as an
                    # intervention (which gets silently dropped).
                    task = context.communicate(UserMessage(prompt), queue_if_busy=True)
                    # RCA-20260612 Issue 16: Add timeout to prevent indefinite blocking
                    result = await asyncio.wait_for(task.result(), timeout=1800)
                    logger.info(f"[WEBHOOK] Agent completed workflow '{workflow}' for issue #{issue_number}")
                    return result
                except asyncio.TimeoutError:
                    logger.error(f"[WEBHOOK] Agent timed out after 1800s for issue #{issue_number}")
                    # RCA-20260612 Issue 10: Post error feedback on timeout
                    try:
                        await _post_error_comment(
                            provider, owner, repo, issue_number,
                            f"Agent processing timed out after 30 minutes for workflow '{workflow}'."
                        )
                    except Exception:
                        pass
                    return None
                except Exception as e:
                    logger.error(f"[WEBHOOK] Agent communication failed: {e}")
                    # RCA-20260612 Issue 10: Post error feedback to issue
                    try:
                        await _post_error_comment(
                            provider, owner, repo, issue_number, str(e)
                        )
                    except Exception:
                        pass
                    return None
            
            # Run in background thread
            import threading
            def _run_async():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(_send_message())
                finally:
                    loop.close()
            
            # Use bounded pool instead of raw Thread (Forgejo #762)
            _webhook_executor.submit(_run_async)
            logger.info(f"[WEBHOOK] Agent triggered in background for issue #{issue_number}")
            return True
            
        except Exception as context_err:
            logger.error(f"[WEBHOOK] Direct context trigger failed: {context_err}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to trigger agent workflow: {e}")
        return False


# ============================================================================
# Flask Route Registration
# ============================================================================

def register_webhook_routes(app: Flask):
    """
    Register webhook endpoints on the Flask app.
    
    Call this from run_ui.py to add webhook support.
    """
    
    @app.route('/webhook/github', methods=['POST'])
    def github_webhook():
        """Receive and process GitHub webhooks."""
        # Load config and secret from settings/secrets manager
        config = load_webhook_config()
        
        # Check if webhooks are enabled
        if not config.get("enabled", True):
            return jsonify({'status': 'disabled', 'reason': 'Event hooks disabled in settings'}), 200
        
        from python.helpers.print_style import PrintStyle
        PrintStyle().print(f"[DEBUG_WEBHOOK] Received request at /webhook/github")
        PrintStyle().print(f"[DEBUG_WEBHOOK] Headers: {dict(request.headers)}")
        
        # Detect webhook source FIRST (needed for correct secret lookup)
        # Check AGIX Space headers first, then Forgejo
        is_agix = 'X-AGIX-Event' in request.headers
        is_forgejo = any(h in request.headers for h in ['X-Forgejo-Event', 'X-Forgejo-Signature', 'X-Gitea-Event', 'X-Gitea-Signature'])
        
        # If hitting /webhook/github but it might be Forgejo or AGIX Space
        webhook_provider = 'agix-space' if is_agix else ('forgejo' if is_forgejo else 'github')
        
        PrintStyle().print(f"[DEBUG_WEBHOOK] Provider: {webhook_provider}")
        logger.info(f"[WEBHOOK] Detected provider: {webhook_provider}")
        
        # Get secret from secrets manager (provider-aware: FORGEJO_WEBHOOK_SECRET or GITHUB_WEBHOOK_SECRET)
        try:
            secret = get_webhook_secret(webhook_provider)
            if not secret:
                logger.error(f"[WEBHOOK] No secret found for provider={webhook_provider}. Verification will fail.")
        except Exception as e:
            logger.error(f"[WEBHOOK] Critical error retrieving secret: {e}")
            return jsonify({'error': 'Internal configuration error', 'detail': 'Secret retrieval failed'}), 500
        
        # Verify signature (supports GitHub, Forgejo, Gitea)
        try:
            if not verify_webhook_signature(request.data, dict(request.headers), secret):
                logger.warning(f"[WEBHOOK] Invalid signature rejected for provider={webhook_provider}")
                return jsonify({'error': 'Invalid signature'}), 401
            print("[WEBHOOK_DEBUG] Signature verification PASSED", flush=True)
        except Exception as e:
            logger.error(f"[WEBHOOK] Error during signature verification: {e}")
            return jsonify({'error': 'Internal verification error'}), 500
        
        try:
            # Parse event type (AGIX Space, GitHub, Forgejo/Gitea)
            event_type = (
                request.headers.get('X-AGIX-Event') or
                request.headers.get('X-GitHub-Event') or 
                request.headers.get('X-Forgejo-Event') or 
                request.headers.get('X-Gitea-Event') or 
                ''
            )
            
            # webhook_provider already detected above for secret lookup
            
            try:
                payload = request.get_json()
            except Exception as e:
                return jsonify({'error': f'Invalid JSON: {e}'}), 400
            
            # Generate event ID for deduplication (check multiple delivery headers)
            # X-AGIX-Delivery carries the stable workEffort.id for frontend workpackages
            delivery_id = (
                request.headers.get('X-AGIX-Delivery') or
                request.headers.get('X-GitHub-Delivery') or 
                request.headers.get('X-Forgejo-Delivery') or 
                request.headers.get('X-Gitea-Delivery') or 
                ''
            )
            event_id = delivery_id or f"{event_type}_{payload.get('action')}_{int(time.time())}"
            
            # Check if already handled (deterministic)
            detector = get_webhook_detector()
            if not detector.is_new_event(webhook_provider, event_id):
                logger.info(f"[WEBHOOK] Duplicate event skipped: {event_id}")
                return jsonify({'status': 'duplicate', 'event_id': event_id}), 200
            
            # Get repo info from payload
            repo_info = payload.get("repository", {})
            owner = repo_info.get("owner", {}).get("login")
            repo = repo_info.get("name")
            PrintStyle().print(f"[DEBUG_WEBHOOK] Extracted from info: owner={owner}, repo={repo}")
            
            if not owner or not repo:
                # Fallback for some Forgejo structures or GitHub comment payloads
                owner = payload.get("owner") or (payload.get("repository") or {}).get("owner", {}).get("username")
                repo = payload.get("repo") or (payload.get("repository") or {}).get("name")
                PrintStyle().print(f"[DEBUG_WEBHOOK] Fallback extraction: owner={owner}, repo={repo}")
            
            # Check allowed repos (empty list = allow all)
            # BYPASS for workpackage events — these are internal platform traffic
            # from example.com. The repo in the payload is the TARGET for issue
            # creation, not a security boundary for inbound webhooks.
            is_workpackage = event_type in ("workpackage", "work_effort.created", "work_effort.updated")
            allowed_repos = config.get("allowed_repos", [])
            if allowed_repos and not is_workpackage:
                full_name = f"{owner}/{repo}"
                is_allowed = False
                for allowed in allowed_repos:
                    # Exact match (owner/repo)
                    if allowed.strip() == full_name:
                        is_allowed = True
                        break
                    # Simple repo match (if filter doesn't contain a slash)
                    if "/" not in allowed and allowed.strip() == repo:
                        is_allowed = True
                        break
                    
                if not is_allowed:
                    logger.info(f"[WEBHOOK] Repo {full_name} not in allowed list: {allowed_repos}")
                    return jsonify({'status': 'skipped', 'reason': 'Repo not allowed'}), 200
            elif is_workpackage and allowed_repos:
                full_name = f"{owner}/{repo}"
                logger.info(f"[WEBHOOK] Workpackage event bypassed allowed_repos check for {full_name}")

            # RE-VALDIATE provider based on payload if it was detected as github but URL suggests otherwise
            # (Needed because Forgejo can mimic GitHub headers, but we want correct chat naming and API calls)
            if webhook_provider == 'github' and repo_info:
                repo_url = repo_info.get("html_url", "") or repo_info.get("clone_url", "")
                forgejo_base_url = get_forgejo_url()
                if repo_url and ("forgejo" in repo_url.lower() or "gitea" in repo_url.lower() or (forgejo_base_url and forgejo_base_url in repo_url)):
                    logger.info(f"[WEBHOOK] Correcting provider to forgejo based on payload URL: {repo_url}")
                    webhook_provider = 'forgejo'

            # Route event (deterministic, uses dynamic config)
            route_result = route_github_event(event_type, payload, config=config)
            workflow = route_result.get("workflow")
            
            if workflow == "skip":
                logger.info(f"[WEBHOOK] Event skipped: {route_result.get('reason')}")
                return jsonify({'status': 'skipped', 'reason': route_result.get('reason')}), 200
            
            # Check if workflow is enabled in config
            enabled_workflows = config.get("event_hooks_workflows", [])
            if enabled_workflows and workflow not in enabled_workflows:
                logger.info(f"[WEBHOOK] Workflow {workflow} disabled in settings")
                return jsonify({'status': 'skipped', 'reason': f'Workflow {workflow} disabled'}), 200
            
            # Resolve project for this repo (cached 1:1 mapping)
            project_name = resolve_project_for_repo(owner, repo, webhook_provider, payload) if owner and repo else None
            
            # ── Backpressure Guard (Forgejo #1034) ──────────────────────
            # MUST be BEFORE mark_handled so GitHub retries on 503.
            # Reject new webhook work if thread count exceeds threshold
            # to prevent system crashes from thread exhaustion.
            try:
                from python.helpers.thread_monitor import is_thread_safe
                if not is_thread_safe():
                    import threading as _thr
                    _count = _thr.active_count()
                    logger.warning(
                        f"[WEBHOOK] ⚠ BACKPRESSURE: Rejecting webhook "
                        f"(threads={_count}, threshold exceeded). "
                        f"Event: {event_type}/{payload.get('action')} "
                        f"for {owner}/{repo} #{route_result.get('issue_number')}"
                    )
                    # Do NOT mark as handled — let GitHub retry later
                    return jsonify({
                        'status': 'overloaded',
                        'error': 'Thread count exceeds threshold, retry later',
                        'thread_count': _count,
                    }), 503
            except ImportError:
                pass  # thread_monitor not available, proceed without guard
            
            # Mark as handled BEFORE triggering (prevents race conditions)
            detector.mark_handled(webhook_provider, event_id)
            
            # Trigger agent workflow (async in background)
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Run async trigger - remove keys already passed as positional args
            trigger_kwargs = {k: v for k, v in route_result.items() if k not in ('workflow', 'issue_number', 'owner', 'repo')}
            
            # Save workpackage attachments to project filesystem before triggering agent.
            # The raw base64 data is too large to include in the prompt, so we decode it,
            # write PNGs to disk, upload to GitHub repo, and embed raw URLs in the prompt.
            if workflow == "workpackage_decomposition" and trigger_kwargs.get("attachments"):
                attachment_paths = save_webhook_attachments(project_name, trigger_kwargs["attachments"])
                if attachment_paths:
                    logger.info(f"[WEBHOOK] Saved {len(attachment_paths)} workpackage attachments to project '{project_name}'")

                    # Upload to GitHub repo so images render inline in issues
                    gh_url_map = upload_attachments_to_github(
                        owner=owner or route_result.get("owner", ""),
                        repo=repo or route_result.get("repo", ""),
                        attachment_paths=attachment_paths,
                    )

                    if gh_url_map:
                        # Build markdown with inline images using raw GitHub URLs
                        trigger_kwargs["attachment_files"] = "\n".join(
                            f"- **{fname}**: `docs/mockups/{fname}`\n  ![{fname}]({url})"
                            for fname, url in gh_url_map.items()
                        )
                        logger.info(f"[WEBHOOK] Uploaded {len(gh_url_map)} mockups to GitHub repo")
                    else:
                        # Fallback to local file listing if GitHub upload failed
                        trigger_kwargs["attachment_files"] = "\n".join(
                            f"- {os.path.basename(p)}" for p in attachment_paths
                        )
                        logger.warning("[WEBHOOK] GitHub upload failed — using local file paths as fallback")

                # Always remove raw base64 from kwargs — never forward to prompt
                trigger_kwargs.pop("attachments", None)
            else:
                trigger_kwargs.pop("attachments", None)
            
            # Run async trigger in background thread (Flask is sync)
            def _trigger_in_background():
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(trigger_agent_workflow(
                        workflow=workflow,
                        issue_number=route_result.get("issue_number"),
                        owner=owner,
                        repo=repo,
                        project_name=project_name,
                        provider=webhook_provider,  # Pass detected source
                        delivery_id=delivery_id,     # For unique workpackage context IDs
                        **trigger_kwargs
                    ))
                except Exception as e:
                    logger.error(f"Background trigger failed: {e}")
                finally:
                    loop.close()
            
            # Use bounded pool instead of raw Thread (Forgejo #762)
            _webhook_executor.submit(_trigger_in_background)
            
            logger.info(f"[WEBHOOK] Event processed: {event_type}/{payload.get('action')} -> {workflow}")
            return jsonify({
                'status': 'processed',
                'workflow': workflow,
                'event_id': event_id
            }), 200
            
        except Exception as e:
            logger.error(f"[WEBHOOK] Unexpected error processing {event_type} event: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error', 'message': str(e)}), 500
    
    @app.route('/webhook/health', methods=['GET'])
    def webhook_health():
        """Health check for webhook endpoint."""
        return jsonify({
            'status': 'ok',
            'timestamp': time.time(),
            'service': 'agix-webhook-handler'
        }), 200
    
    logger.info("[WEBHOOK] Routes registered: /webhook/github, /webhook/health")
