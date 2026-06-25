"""
Centralized credential management for all tools.

This module provides unified access to credentials for:
- GitHub
- Forgejo
- Other providers

IMPORTANT: DB (config.db) is the source of truth.
os.environ may be stale after process start.
Priority: params → secrets (DB) → parameters → os.environ

Tools should import from this module rather than implementing
their own credential loading logic (DRY principle).
"""

import os
import re
import logging
import fnmatch
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List, Iterable

logger = logging.getLogger("credentials")

# =============================================================================
# FUZZY MATCHING UTILITIES
# =============================================================================

def find_credential_value(
    data_sources: List[Dict[str, Any]],
    target_key: str,
    patterns: List[str] = None,
    priority_keys: List[str] = None
) -> Optional[str]:
    """
    Find a credential value across multiple data sources using exact and fuzzy matching.
    
    Priority:
    1. Exact match in priority_keys (in order of priority, across all sources)
    2. Exact match for target_key (across all sources)
    3. Pattern-based matches (fnmatch)
    
    Args:
        data_sources: List of dictionaries to search (e.g. [params, secrets, parameters, os.environ])
        target_key: The canonical key name (e.g. 'FORGEJO_TOKEN')
        patterns: List of glob patterns (e.g. ['*FORGEJO*TOKEN*'])
        priority_keys: List of high-priority keys (e.g. ['FORGEJO_API_TOKEN'])
        
    Returns:
        The found value as a stripped string, or None.
    """
    # Filter out empty or None sources
    valid_sources = [s for s in data_sources if s is not None]
    
    # 1. Exact priority keys
    if priority_keys:
        for pk in priority_keys:
            pk_up = pk.upper()
            for source in valid_sources:
                # Find the key in the source (case-insensitive for key names in source)
                # Note: secrets/parameters/environ are already uppercase, but params might not be
                val = None
                if pk_up in source:
                    val = source[pk_up]
                elif pk.lower() in source:
                    val = source[pk.lower()]
                
                if val is not None and str(val).strip():
                    logger.info(f"[CRED] Found exact match for priority key '{pk}'")
                    return str(val).strip()
    
    # 2. Exact target key
    target_up = target_key.upper()
    for source in valid_sources:
        val = source.get(target_up) or source.get(target_key.lower())
        if val is not None and str(val).strip():
            logger.info(f"[CRED] Found exact match for target key '{target_key}'")
            return str(val).strip()

    # 3. Pattern matches (glob patterns)
    if patterns:
        for p in patterns:
            p_up = p.upper()
            for source in valid_sources:
                for key in source.keys():
                    if fnmatch.fnmatch(key.upper(), p_up):
                        val = source[key]
                        if val is not None and str(val).strip():
                            logger.info(f"[CRED] Found pattern match '{p}' matching key '{key}'")
                            return str(val).strip()
                            
    return None

# =============================================================================
# CREDENTIAL DATACLASSES
# =============================================================================

@dataclass
class GitHubCredentials:
    """GitHub API credentials."""
    token: str
    owner: str
    repo: str
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "token": self.token,
            "owner": self.owner,
            "repo": self.repo
        }
    
    def is_complete(self) -> bool:
        return bool(self.token and self.owner and self.repo)


@dataclass
class ForgejoCredentials:
    """Forgejo API credentials."""
    token: str
    url: str
    owner: str
    repo: str
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "token": self.token,
            "url": self.url,
            "owner": self.owner,
            "repo": self.repo
        }
    
    def is_complete(self) -> bool:
        return bool(self.token and self.url and self.owner and self.repo)


# =============================================================================
# PROVIDER DETECTION
# =============================================================================

GITHUB_PATTERNS = [
    r"github\.com",
    r"api\.github\.com",
]

FORGEJO_PATTERNS = [
    r"forgejo\.",
    r"gitea\.",
    r"codeberg\.org",
    r"git\..*\.org",
]


def detect_provider(repo_url: str) -> str:
    """
    Detect provider type from repository URL.
    
    Args:
        repo_url: Repository URL or API endpoint
        
    Returns:
        'github', 'forgejo', or 'unknown'
    """
    if not repo_url:
        return "unknown"
    
    repo_url_lower = repo_url.lower()
    
    for pattern in GITHUB_PATTERNS:
        if re.search(pattern, repo_url_lower, re.IGNORECASE):
            return "github"
    
    for pattern in FORGEJO_PATTERNS:
        if re.search(pattern, repo_url_lower, re.IGNORECASE):
            return "forgejo"
    
    return "unknown"


def detect_provider_from_params(params: Dict[str, Any], context: "AgentContext" = None) -> str:
    """
    Detect provider from parameters using heuristics and environment secrets.
    
    Priority:
    1. Explicit owner/repo match against known test repositories (highest precision)
    2. Detection from repository URL
    3. Detection based on token presence (Github vs Forgejo)
    4. Default to Forgejo (baseline)
    """
    owner = params.get("owner", "")
    repo = params.get("repo", "")
    url = params.get("repo_url", "") or params.get("url", "")
    full_repo = f"{owner}/{repo}".lower() if owner and repo else repo.lower() if repo else ""
    
    # 1. Check Parameters/Settings (matching owner/repo)
    parameters = _load_parameters(context)
    if full_repo:
        # Match against configured test repos in settings
        # Use parameters.py keys for dynamic project-based overrides
        github_test = parameters.get("AGIX_GITHUB_TEST_REPO")
        forgejo_test = parameters.get("AGIX_FORGEJO_TEST_REPO")
        
        if github_test and full_repo in github_test.lower():
            logger.info(f"[PROV_DETECT] Matched 'github' via AGIX_GITHUB_TEST_REPO ({full_repo})")
            return "github"
        if forgejo_test and full_repo in forgejo_test.lower():
            logger.info(f"[PROV_DETECT] Matched 'forgejo' via AGIX_FORGEJO_TEST_REPO ({full_repo})")
            return "forgejo"

    # 2. Heuristic from URL
    if url:
        from python.tools.repo_automation.providers import detect_provider
        prov = detect_provider(url)
        if prov in ("github", "forgejo"):
            logger.info(f"[PROV_DETECT] Matched '{prov}' via URL analysis ({url})")
            return prov

    # 3. Owner-based pattern heuristics (less priority than explicit matches)
    if owner and owner.lower().endswith("-agi"):
        logger.info(f"[PROV_DETECT] Matched 'github' via owner suffix '-agi' ({owner})")
        return "github"

    # 4. Check secrets (priority based on detected context if possible)
    secrets = _load_secrets(context)
    has_github = bool(secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    has_forgejo = bool(secrets.get("FORGEJO_TOKEN") or os.environ.get("FORGEJO_TOKEN"))
    
    if has_github and not has_forgejo:
        logger.info("[PROV_DETECT] Matched 'github' via exclusive GITHUB_TOKEN presence")
        return "github"
    if has_forgejo and not has_github:
        logger.info("[PROV_DETECT] Matched 'forgejo' via exclusive FORGEJO_TOKEN presence")
        return "forgejo"
        
    # Default fallback
    logger.info("[PROV_DETECT] Fallback to 'forgejo' (default)")
    return "forgejo"
        
    # If both exist, or none, we use URL patterns as a final fallback if available
    # No URL? default to forgejo for backward compatibility with current stack preference
    return "forgejo"


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _load_secrets(context=None) -> Dict[str, Any]:
    """
    Load secrets from SecretsManager (DB-first).
    
    Args:
        context: Agent context for scoped secrets
        
    Returns:
        Dict of secrets
    """
    try:
        from python.helpers.secrets_helper import get_secrets_manager, get_default_secrets_manager
        if context:
            sm = get_secrets_manager(context)
        else:
            sm = get_default_secrets_manager()
        return sm.load_secrets()
    except Exception as e:
        logger.debug(f"Could not load secrets: {e}")
        return {}


def _load_parameters(context=None) -> Dict[str, Any]:
    """
    Load parameters from ParametersManager.
    
    Args:
        context: Agent context for scoped parameters
        
    Returns:
        Dict of parameters
    """
    if not context:
        return {}
    try:
        from python.helpers.parameters import get_parameters_manager
        pm = get_parameters_manager(context)
        return pm.load_parameters()
    except Exception as e:
        logger.debug(f"Could not load parameters: {e}")
        return {}


# =============================================================================
# CREDENTIAL LOADING
# =============================================================================

def get_github_credentials(context=None, params: Dict[str, Any] = None) -> GitHubCredentials:
    """
    Load GitHub credentials from secrets, params, parameters, or environment.
    
    Priority order (DB is authoritative, os.environ may be stale):
    1. Explicit params
    2. Secrets manager (DB - always fresh)
    3. Parameters manager
    4. Environment variables (fallback)
    
    Args:
        context: Agent context for secrets manager
        params: Request parameters (optional)
        
    Returns:
        GitHubCredentials instance
    """
    params = params or {}
    secrets = _load_secrets(context)
    parameters = _load_parameters(context)
    
    # Priority: params → secrets (DB) → parameters → os.environ
    sources = [params, secrets, parameters, os.environ]
    
    token = find_credential_value(
        sources,
        "GITHUB_TOKEN",
        patterns=["*GITHUB*TOKEN*", "*GH*TOKEN*", "*GITHUB*PAT*", "*GITHUB*API*KEY*"],
        priority_keys=["GITHUB_TOKEN", "GITHUB_PAT", "GH_TOKEN"]
    )
    if isinstance(token, str):
        token = token.strip()

    if token and isinstance(token, str):
        source = "params" if params.get("token") else "secrets" if secrets.get("GITHUB_TOKEN") else "parameters" if parameters.get("GITHUB_TOKEN") else "environ"
        logger.info(f"[get_github_credentials] Found GITHUB_TOKEN from {source} (len={len(token)})")
    elif token:
        logger.warning(f"[get_github_credentials] GITHUB_TOKEN found but is not a string (type={type(token)})")
    else:
        logger.warning("[get_github_credentials] GITHUB_TOKEN not found")
    
    owner = find_credential_value(
        sources,
        "GITHUB_OWNER",
        patterns=["*GITHUB*OWNER*", "*GH*OWNER*", "*REPO*OWNER*"],
        priority_keys=["owner", "GITHUB_OWNER", "GH_OWNER"]
    )
    if isinstance(owner, str):
        owner = owner.strip()
    
    repo = find_credential_value(
        sources,
        "GITHUB_REPO",
        patterns=["*GITHUB*REPO*", "*GH*REPO*", "*GITHUB*NAME*", "*REPO*NAME*"],
        priority_keys=["repo", "GITHUB_REPO", "GITHUB_REPO_WITHAI", "GH_REPO"]
    )
    if isinstance(repo, str):
        repo = repo.strip()
    
    # If repo is a full URL, extract owner/repo
    if repo and ("github.com" in repo or "http" in repo):
        repo_parts = repo.rstrip("/").split("/")
        if len(repo_parts) >= 2:
            repo = repo_parts[-1]
            owner = repo_parts[-2]
    # Handle owner/repo format (e.g. 'your-bot-username/agix-test' from AGIX_GITHUB_TEST_REPO)
    elif repo and "/" in repo:
        parts = repo.split("/")
        if len(parts) == 2:
            logger.info(f"[get_github_credentials] Splitting owner/repo format: '{repo}' -> owner='{parts[0]}', repo='{parts[1]}'")
            if not owner:
                owner = parts[0]
            repo = parts[1]
    
    # Fallback to combined GITHUB_REPOSITORY
    if not owner or not repo:
        combined = secrets.get("GITHUB_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY", "")
        if "/" in combined:
            owner, repo = combined.split("/", 1)
    
    # If still missing owner/repo, try to resolve from active project git remote
    if not owner or not repo:
        try:
            from python.helpers import projects
            project_name = projects.get_context_project_name(context)
            if project_name:
                remote_url = projects.get_project_git_remote(project_name)
                if remote_url:
                    logger.debug(f"[get_github_credentials] Resolving owner/repo from project remote: {remote_url}")
                    # Extract owner/repo from URL
                    parts = remote_url.rstrip("/").split("/")
                    if len(parts) >= 2:
                        resolved_repo = parts[-1].replace(".git", "")
                        resolved_owner = parts[-2]
                        if not owner: owner = resolved_owner
                        if not repo: repo = resolved_repo
        except Exception as e:
            logger.debug(f"[get_github_credentials] Project remote resolution failed: {e}")

    return GitHubCredentials(token=token or "", owner=owner or "", repo=repo or "")


def get_forgejo_credentials(context=None, params: Dict[str, Any] = None) -> ForgejoCredentials:
    """
    Load Forgejo credentials from secrets, params, parameters, or environment.
    
    Priority order (DB is authoritative, os.environ may be stale):
    1. Secrets manager (DB - always fresh)
    2. Parameters manager
    3. Environment variables (fallback)
    
    Args:
        context: Agent context for secrets manager
        params: Request parameters (optional)
        
    Returns:
        ForgejoCredentials instance
    """
    params = params or {}
    secrets = _load_secrets(context)
    parameters = _load_parameters(context)
    
    sources = [params, secrets, parameters, os.environ]

    # Token is always a secret - check params, then DB, then fallback to environ
    token = find_credential_value(
        sources,
        "FORGEJO_TOKEN",
        patterns=["*FORGEJO*TOKEN*", "*GITEA*TOKEN*", "*FORGEJO*API*KEY*"],
        priority_keys=["FORGEJO_TOKEN", "GITEA_TOKEN"]
    )
    if isinstance(token, str):
        token = token.strip()
    
    if token:
        source = "params" if params.get("token") else "secrets" if secrets.get("FORGEJO_TOKEN") else "environ"
        logger.info(f"[get_forgejo_credentials] Found FORGEJO_TOKEN from {source} (len={len(token)})")
    else:
        logger.warning("[get_forgejo_credentials] FORGEJO_TOKEN not found")
    
    # URL can be in params, secrets, parameters, or env
    url = find_credential_value(
        sources,
        "FORGEJO_URL",
        patterns=["*FORGEJO*URL*", "*GITEA*URL*", "*FORGEJO*BASE*URL*"],
        priority_keys=["FORGEJO_URL", "GITEA_URL"]
    )
    
    # Owner/Repo can be in params, secrets, parameters, or env
    owner = find_credential_value(
        sources,
        "FORGEJO_OWNER",
        patterns=["*FORGEJO*OWNER*", "*GITEA*OWNER*", "*REPO*OWNER*"],
        priority_keys=["FORGEJO_OWNER", "GITEA_OWNER"]
    ) or "your-org"  # Hard-pin default for Forgejo
    
    repo_name = find_credential_value(
        sources,
        "FORGEJO_REPO",
        patterns=["*FORGEJO*REPO*", "*GITEA*REPO*", "*FORGEJO*NAME*", "*REPO*NAME*", "*GITEA*NAME*"],
        priority_keys=["FORGEJO_REPO", "FORGEJO_NAME", "REPO_NAME"]
    )
    if isinstance(repo_name, str):
        repo_name = repo_name.strip()
    
    # If still missing owner/repo/url, try to resolve from active project git remote
    if not owner or not repo_name or not url:
        try:
            from python.helpers import projects
            project_name = projects.get_context_project_name(context)
            if project_name:
                remote_url = projects.get_project_git_remote(project_name)
                if remote_url:
                    logger.debug(f"[get_forgejo_credentials] Resolving context from project remote: {remote_url}")
                    # Extract owner/repo/url from URL
                    # e.g. https://forgejo.com/owner/repo.git
                    if not url:
                        url_parts = remote_url.split("//")
                        if len(url_parts) > 1:
                            domain_parts = url_parts[1].split("/")
                            if domain_parts:
                                url = f"{url_parts[0]}//{domain_parts[0]}"
                    
                    parts = remote_url.rstrip("/").split("/")
                    if len(parts) >= 2:
                        resolved_repo = parts[-1].replace(".git", "")
                        resolved_owner = parts[-2]
                        if not owner: owner = resolved_owner
                        if not repo_name: repo_name = resolved_repo
        except Exception as e:
            logger.debug(f"[get_forgejo_credentials] Project remote resolution failed: {e}")

    return ForgejoCredentials(
        token=token or "",
        url=(url or "").rstrip("/"),
        owner=owner or "",
        repo=repo_name or ""
    )


def get_github_base_url(context=None, params: Dict[str, Any] = None) -> str:
    """
    Get GitHub API base URL.
    
    Args:
        context: Agent context
        params: Request parameters
        
    Returns:
        GitHub API base URL (default: https://api.github.com)
    """
    params = params or {}
    secrets = _load_secrets(context)
    parameters = _load_parameters(context)
    
    return (
        params.get("github_api_url") or
        secrets.get("GITHUB_API_URL") or
        parameters.get("GITHUB_API_URL") or
        os.environ.get("GITHUB_API_URL") or
        "https://api.github.com"
    )


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'GitHubCredentials',
    'ForgejoCredentials',
    'detect_provider',
    'detect_provider_from_params',
    'get_github_credentials',
    'get_forgejo_credentials',
    'get_github_base_url',
    'GITHUB_PATTERNS',
    'FORGEJO_PATTERNS',
]