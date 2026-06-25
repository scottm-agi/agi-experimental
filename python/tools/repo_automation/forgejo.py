"""
Forgejo-specific operations for repository automation.
Handles issue listing, fetching, commenting, and creation for Forgejo/Gitea.

REFACTORED: Uses AsyncHTTPClient with circuit breaker and retry support.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, Any, List, Optional, Union

from .providers import ForgejoCredentials
from .base import logger

# Import async HTTP client
from python.helpers.async_http import AsyncHTTPClient, HTTPClientConfig
from python.helpers.circuit_breaker import CircuitBreakerError


# =============================================================================
# FORGEJO HTTP CLIENT
# =============================================================================

# Singleton client for Forgejo API
_forgejo_client: Optional[AsyncHTTPClient] = None
_client_lock = asyncio.Lock()


async def _get_forgejo_client(creds: "ForgejoCredentials") -> AsyncHTTPClient:
    """Get or create the Forgejo HTTP client."""
    global _forgejo_client
    
    async with _client_lock:
        if _forgejo_client is None:
            config = HTTPClientConfig(
                timeout=30.0,
                connect_timeout=10.0,
                max_retries=3,
                retry_on_status=(429, 500, 502, 503, 504),
                initial_delay=1.0,
                max_delay=30.0,
                backoff_multiplier=2.0,
                jitter_factor=0.2,
                circuit_failure_threshold=5,
                circuit_success_threshold=3,
                circuit_timeout=60.0,
            )
            _forgejo_client = AsyncHTTPClient(
                service_name="forgejo",
                config=config,
                default_headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return _forgejo_client


# =============================================================================
# CREDENTIAL NORMALIZATION
# =============================================================================


def _normalize_creds(creds: Union[ForgejoCredentials, Dict[str, Any]]) -> ForgejoCredentials:
    """
    Normalize credentials to ForgejoCredentials object.
    Accepts either a ForgejoCredentials dataclass or a dict.
    """
    if isinstance(creds, ForgejoCredentials):
        return creds
    if isinstance(creds, dict):
        return ForgejoCredentials(
            token=creds.get("token", ""),
            url=creds.get("url", ""),
            owner=creds.get("owner", ""),
            repo=creds.get("repo", "")
        )
    raise TypeError(f"Expected ForgejoCredentials or dict, got {type(creds)}")


def _auth_headers(creds: ForgejoCredentials) -> Dict[str, str]:
    """Build authentication headers."""
    return {"Authorization": f"token {creds.token}"}


# =============================================================================
# FORGEJO API OPERATIONS - ASYNC WITH CIRCUIT BREAKER
# =============================================================================


async def list_issues_raw_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    List issues from Forgejo with pagination.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        params: Request parameters (state, sort, direction, page_size, max_pages)
        
    Returns:
        List of issue dicts
    """
    creds = _normalize_creds(creds)
    state = params.get("state", "open")
    sort = params.get("sort", "created")
    direction = params.get("direction", "desc")
    page_size = params.get("page_size", 50)
    max_pages = params.get("max_pages", 20)
    
    client = await _get_forgejo_client(creds)
    all_issues = []
    page = 1
    
    while page <= max_pages:
        api_url = (
            f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues"
            f"?state={state}&limit={page_size}&page={page}&sort={sort}&direction={direction}"
        )
        
        try:
            resp = await client.get(api_url, headers=_auth_headers(creds))
            
            if resp.status_code != 200:
                logger.error(f"[list_issues_raw_forgejo] API error {resp.status_code}: {resp.text}")
                break
            
            issues = resp.json()
            if not issues:
                break
            
            all_issues.extend(issues)
            
            if len(issues) < page_size:
                break
            page += 1
            
        except CircuitBreakerError as e:
            logger.error(f"[list_issues_raw_forgejo] Circuit breaker open: {e}")
            break
        except Exception as e:
            logger.error(f"[list_issues_raw_forgejo] Request failed: {e}")
            break
    
    return all_issues


async def list_comments_raw_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    issue_number: int,
    params: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """
    List comments for a Forgejo issue with bot filtering.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        issue_number: Issue number
        params: Request parameters (page_size, max_pages)
        
    Returns:
        List of comment dicts (bot comments filtered out)
    """
    creds = _normalize_creds(creds)
    params = params or {}
    page_size = params.get("page_size", 50)
    max_pages = params.get("max_pages", 10)
    
    client = await _get_forgejo_client(creds)
    all_comments = []
    page = 1
    
    while page <= max_pages:
        api_url = (
            f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments"
            f"?limit={page_size}&page={page}"
        )
        
        try:
            resp = await client.get(api_url, headers=_auth_headers(creds))
            
            if resp.status_code != 200:
                logger.error(f"[list_comments_raw_forgejo] API error {resp.status_code}: {resp.text}")
                break
            
            comments = resp.json()
            if not comments:
                break
            
            for c in comments:
                # Filter out bot comments
                body = c.get("body", "")
                user = c.get("user", {})
                username = user.get("login", "")
                user_type = user.get("type", "")
                
                # Skip if has agix-id tag
                if "agix-id:" in body:
                    logger.debug(f"[list_comments_raw_forgejo] Skipping self-generated comment {c.get('id')}")
                    continue
                
                # Skip bot accounts
                if user_type.lower() == "bot" or "[bot]" in username.lower():
                    logger.debug(f"[list_comments_raw_forgejo] Skipping bot comment from {username}")
                    continue
                
                all_comments.append(c)
            
            if len(comments) < page_size:
                break
            page += 1
            
        except CircuitBreakerError as e:
            logger.error(f"[list_comments_raw_forgejo] Circuit breaker open: {e}")
            break
        except Exception as e:
            logger.error(f"[list_comments_raw_forgejo] Request failed: {e}")
            break
    
    return all_comments


async def get_issue_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    issue_number: int
) -> str:
    """
    Fetch issue details from Forgejo.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        issue_number: Issue number to fetch
        
    Returns:
        Formatted issue content string
    """
    creds = _normalize_creds(creds)
    if not creds.is_complete():
        missing = []
        if not creds.url:
            missing.append("FORGEJO_URL")
        if not creds.token:
            missing.append("FORGEJO_TOKEN")
        if not creds.owner:
            missing.append("owner")
        if not creds.repo:
            missing.append("repo")
        return f"ERROR: Missing Forgejo credentials: {', '.join(missing)}"
    
    api_url = f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues/{issue_number}"
    
    try:
        client = await _get_forgejo_client(creds)
        resp = await client.get(api_url, headers=_auth_headers(creds))
        
        if resp.status_code == 200:
            data = resp.json()
            return f"#{data.get('number')}: {data.get('title')}\n\n{data.get('body')}"
        return f"ERROR: Forgejo API returned {resp.status_code}: {resp.text}"
    except CircuitBreakerError as e:
        return f"ERROR: Forgejo API circuit breaker open: {e}"
    except Exception as e:
        return f"ERROR: Failed to connect to Forgejo: {e}"


async def comment_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    issue_number: int,
    body: str,
    hash_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    tsm=None
) -> Dict[str, Any]:
    """
    Post a comment to a Forgejo issue with deduplication.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        issue_number: Issue number to comment on
        body: Comment body
        hash_id: Optional hash for deduplication
        params: Additional parameters
        tsm: Optional TaskStateManager for tracking
        
    Returns:
        Result dict with success/error info
    """
    creds = _normalize_creds(creds)
    params = params or {}
    
    if not creds.is_complete():
        missing = [k for k, v in {
            "token": creds.token, "url": creds.url,
            "owner": creds.owner, "repo": creds.repo
        }.items() if not v]
        return {"success": False, "message": f"ERROR: Missing required fields: {missing}"}
    
    if not body:
        return {"success": False, "message": "ERROR: Comment body is empty"}
    
    logger.info(f"[comment_forgejo] Preparing comment for issue #{issue_number}. Body length: {len(body)} chars")
    
    # Expand includes if needed
    from python.helpers.strings import replace_file_includes
    body = replace_file_includes(body)
    
    # Truncate if too long
    MAX_BODY_LEN = 64000
    if len(body) > MAX_BODY_LEN:
        logger.warning(f"[comment_forgejo] Body too long ({len(body)} chars), truncating")
        body = body[:MAX_BODY_LEN] + "\n\n... (content truncated)"
    
    try:
        client = await _get_forgejo_client(creds)
        auth_headers = _auth_headers(creds)
        
        # Generate hash for deduplication
        if not hash_id:
            trigger_id = "initial"
            try:
                api_url_comments = f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments?limit=30"
                resp = await client.get(api_url_comments, headers=auth_headers)
                if resp.status_code == 200:
                    comments = resp.json()
                    for c in reversed(comments):
                        c_body = c.get("body", "")
                        if "<!-- agix-id:" not in c_body and "AI-HANDLED:" not in c_body:
                            trigger_id = f"reply_to_{c.get('id')}"
                            break
            except Exception as e:
                logger.debug(f"Failed to determine trigger_id: {e}")
            
            from python.helpers.hashing import content_hash_short
            hash_id = content_hash_short(f"{issue_number}:{trigger_id}", length=12)
        
        hash_tag = f"\n\n<!-- agix-id: {hash_id} -->"
        
        # Check for existing comment with hash
        api_url = f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments"
        api_url_list = f"{api_url}?limit=30"
        resp_list = await client.get(api_url_list, headers=auth_headers)
        
        if resp_list.status_code == 200:
            recent_comments = resp_list.json()
            
            # Check if hash exists
            for c in recent_comments:
                c_body = c.get("body", "")
                if hash_tag.strip() in c_body or f"agix-id: {hash_id}" in c_body:
                    logger.info(f"[comment_forgejo] Comment with hash {hash_id} already exists")
                    return {"success": True, "skipped": True, "message": "SKIP: Comment already exists (hash matched)"}
        
        # Post the comment
        full_body = body + hash_tag
        logger.info(f"[comment_forgejo] POSTing to {api_url}")
        
        resp = await client.post(api_url, json={"body": full_body}, headers=auth_headers)
        
        if resp.status_code in (200, 201):
            data = resp.json()
            comment_id = data.get("id")
            logger.info(f"[comment_forgejo] Comment posted, ID: {comment_id}")
            
            # Track in TSM if available
            if tsm:
                try:
                    tsm.track_id(f"commented_issue_fj_{issue_number}", str(comment_id))
                    tsm.track_id(f"comment_hashes_fj_{issue_number}", hash_id)
                    if params.get("mark_handled", True):
                        tsm.track_id("handled_issue_fj", str(issue_number))
                    tsm.save()
                except Exception as e:
                    logger.debug(f"TSM tracking failed: {e}")
            
            return {"success": True, "comment_id": comment_id, "message": f"Comment posted: {comment_id}"}
        
        return {"success": False, "message": f"ERROR: Forgejo API returned {resp.status_code}: {resp.text}"}
        
    except CircuitBreakerError as e:
        return {"success": False, "message": f"ERROR: Circuit breaker open - {e}"}
    except Exception as e:
        return {"success": False, "message": f"ERROR: {e}"}


async def create_issue_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    title: str,
    body: str,
    labels: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Create a new issue on Forgejo.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        title: Issue title
        body: Issue body
        labels: Optional list of labels
        
    Returns:
        Result dict with issue number or error
    """
    creds = _normalize_creds(creds)
    if not creds.is_complete():
        return {"success": False, "message": "ERROR: Missing credentials"}
    
    if not title:
        return {"success": False, "message": "ERROR: Missing title"}
    
    from python.helpers.strings import replace_file_includes
    body = replace_file_includes(body)
    
    # Truncate if too long
    MAX_BODY_LEN = 64000
    if len(body) > MAX_BODY_LEN:
        logger.warning(f"[create_issue_forgejo] Body too long ({len(body)} chars), truncating")
        body = body[:MAX_BODY_LEN] + "\n\n... (content truncated)"
    
    api_url = f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues"
    payload = {"title": title, "body": body}
    
    try:
        client = await _get_forgejo_client(creds)
        resp = await client.post(api_url, json=payload, headers=_auth_headers(creds))
        
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "success": True,
                "issue_number": data.get("number"),
                "message": f"Issue created: #{data.get('number')} - {data.get('title')}"
            }
        return {"success": False, "message": f"ERROR: {resp.status_code}: {resp.text}"}
    except CircuitBreakerError as e:
        return {"success": False, "message": f"ERROR: Circuit breaker open - {e}"}
    except Exception as e:
        return {"success": False, "message": f"ERROR: {e}"}


async def upload_attachment_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    issue_number: int,
    file_path: str
) -> Dict[str, Any]:
    """
    Upload an attachment to a Forgejo issue.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        issue_number: Issue number
        file_path: Path to file to upload
        
    Returns:
        Result dict with success/error info
    """
    creds = _normalize_creds(creds)
    
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        logger.warning(f"[upload_attachment_forgejo] File not found: {file_path}")
        return {"success": False, "message": f"File not found: {file_path}"}
    
    api_url = f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/assets"
    
    try:
        client = await _get_forgejo_client(creds)
        
        # Use httpx file upload
        with open(abs_path, "rb") as f:
            files = {"attachment": (os.path.basename(abs_path), f)}
            # Override content-type for file upload
            headers = {"Authorization": f"token {creds.token}"}
            resp = await client.post(api_url, headers=headers, files=files)
            
            if resp.status_code in (200, 201):
                logger.info(f"[upload_attachment_forgejo] Uploaded {file_path} to issue #{issue_number}")
                return {"success": True, "message": "Attachment uploaded"}
            
            logger.error(f"[upload_attachment_forgejo] Failed: {resp.status_code} {resp.text}")
            return {"success": False, "message": f"ERROR: {resp.status_code}"}
    except CircuitBreakerError as e:
        logger.error(f"[upload_attachment_forgejo] Circuit breaker open: {e}")
        return {"success": False, "message": f"ERROR: Circuit breaker open - {e}"}
    except Exception as e:
        logger.error(f"[upload_attachment_forgejo] Error: {e}")
        return {"success": False, "message": f"ERROR: {e}"}


async def list_branches_forgejo(
    creds: Union[ForgejoCredentials, Dict[str, Any]],
    params: Dict[str, Any] = None
) -> List[str]:
    """
    List branch names from Forgejo.
    
    Args:
        creds: Forgejo credentials (ForgejoCredentials or dict)
        params: Optional parameters
        
    Returns:
        List of branch names
    """
    creds = _normalize_creds(creds)
    if not creds.is_complete():
        return []

    api_url = f"{creds.url}/api/v1/repos/{creds.owner}/{creds.repo}/branches"

    try:
        client = await _get_forgejo_client(creds)
        resp = await client.get(api_url, headers=_auth_headers(creds))
        
        if resp.status_code == 200:
            return [b.get("name") for b in resp.json()]
        logger.error(f"[list_branches_forgejo] API error {resp.status_code}: {resp.text}")
        return []
    except CircuitBreakerError as e:
        logger.error(f"[list_branches_forgejo] Circuit breaker open: {e}")
        return []
    except Exception as e:
        logger.error(f"[list_branches_forgejo] Error: {e}")
        return []


def get_circuit_breaker_status() -> Optional[Dict[str, Any]]:
    """Get Forgejo circuit breaker status for monitoring."""
    if _forgejo_client:
        return _forgejo_client.get_circuit_breaker_status()
    return None


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'list_issues_raw_forgejo',
    'list_comments_raw_forgejo',
    'get_issue_forgejo',
    'comment_forgejo',
    'create_issue_forgejo',
    'upload_attachment_forgejo',
    'list_branches_forgejo',
    'get_circuit_breaker_status',
]