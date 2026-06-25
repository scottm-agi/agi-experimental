"""
GitHub-specific operations for repository automation.
Handles issue listing, fetching, commenting, and creation for GitHub.
"""

import logging
import requests
from typing import Dict, Any, List, Optional, Union

from .providers import GitHubCredentials
from .base import logger

# =============================================================================
# GITHUB API OPERATIONS
# =============================================================================

API_BASE = "https://api.github.com"


def _headers(creds: GitHubCredentials) -> Dict[str, str]:
    """Build GitHub API headers."""
    return {
        "Authorization": f"token {creds.token}",
        "Accept": "application/vnd.github.v3+json"
    }


async def list_issues_github(
    creds: GitHubCredentials,
    params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    List issues from GitHub with pagination support.
    
    Args:
        creds: GitHub credentials
        params: Request parameters (state, sort, direction, limit)
        
    Returns:
        List of issue dicts
    """
    if not creds.is_complete():
        logger.error(f"[list_issues_github] Incomplete credentials: {creds}")
        return []
    
    state = params.get("state", "open")
    sort = params.get("sort", "created")
    direction = params.get("direction", "desc")
    limit = params.get("limit", 200)
    
    all_issues = []
    page = 1
    per_page = 100
    
    while len(all_issues) < limit:
        api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues"
        query_params = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": per_page,
            "page": page
        }
        
        logger.info(f"[list_issues_github] Fetching page {page}: {api_url}")
        
        try:
            resp = requests.get(api_url, headers=_headers(creds), params=query_params, timeout=30)
            if resp.status_code != 200:
                logger.error(f"[list_issues_github] API error {resp.status_code}: {resp.text}")
                break
            
            issues = resp.json()
            if not issues:
                break
            
            # Filter out PRs from issues endpoint
            for issue in issues:
                if "pull_request" not in issue:
                    all_issues.append(issue)
            
            if len(issues) < per_page:
                break
            page += 1
            
        except Exception as e:
            logger.error(f"[list_issues_github] Request failed: {e}")
            break
    
    return all_issues


async def get_issue_github(
    creds: GitHubCredentials,
    issue_number: int
) -> str:
    """
    Fetch issue details from GitHub.
    
    Args:
        creds: GitHub credentials
        issue_number: Issue number to fetch
        
    Returns:
        Formatted issue content string
    """
    if not creds.is_complete():
        return f"ERROR: Missing credentials"
    
    api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues/{issue_number}"
    
    try:
        resp = requests.get(api_url, headers=_headers(creds), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return f"#{data.get('number')}: {data.get('title')}\n\n{data.get('body')}"
        return f"ERROR: GitHub API returned {resp.status_code}: {resp.text}"
    except Exception as e:
        return f"ERROR: {e}"


async def comment_github(
    creds: GitHubCredentials,
    issue_number: int,
    body: str,
    hash_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Post a comment to a GitHub issue.
    
    Args:
        creds: GitHub credentials
        issue_number: Issue number to comment on
        body: Comment body
        hash_id: Optional hash for deduplication
        params: Additional parameters
        
    Returns:
        Result dict with success/error info
    """
    params = params or {}
    
    if not creds.is_complete():
        return {"success": False, "message": "ERROR: Missing credentials"}
    
    # Generate hash tag for deduplication
    from python.helpers.hashing import content_hash_short
    if not hash_id:
        # Hashing the body ensures unique content is always posted, 
        # while keeping a per-issue prefix for basic scoping.
        hash_id = content_hash_short(f"{issue_number}:{body[:100]}", length=12)
    
    hash_tag = f"\n\n<!-- agix-id: {hash_id} -->"
    
    headers = _headers(creds)
    
    try:
        # Check for existing comments with same hash
        api_url_list = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments?per_page=30"
        resp_list = requests.get(api_url_list, headers=headers, timeout=10)
        
        if resp_list.status_code == 200:
            recent_comments = resp_list.json()
            
            # Check if hash already exists
            for c in recent_comments:
                c_body = c.get("body", "")
                if hash_tag.strip() in c_body or f"agix-id: {hash_id}" in c_body:
                    logger.info(f"[comment_github] Comment with hash {hash_id} already exists")
                    return {"success": True, "skipped": True, "message": "SKIP: Comment already exists"}
        
        # Post new comment
        api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments"
        full_body = body + hash_tag
        
        resp = requests.post(api_url, json={"body": full_body}, headers=headers, timeout=30)
        
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"success": True, "comment_id": data.get("id"), "message": f"Comment posted: {data.get('id')}"}
        
        return {"success": False, "message": f"ERROR: GitHub API returned {resp.status_code}: {resp.text}"}
        
    except Exception as e:
        return {"success": False, "message": f"ERROR: {e}"}


async def create_issue_github(
    creds: GitHubCredentials,
    title: str,
    body: str,
    labels: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Create a new issue on GitHub.
    
    Args:
        creds: GitHub credentials
        title: Issue title
        body: Issue body
        labels: Optional list of labels
        
    Returns:
        Result dict with issue number or error
    """
    if not creds.is_complete():
        return {"success": False, "message": "ERROR: Missing credentials"}
    
    from python.helpers.strings import replace_file_includes
    body = replace_file_includes(body)
    
    api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues"
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    
    try:
        resp = requests.post(api_url, json=payload, headers=_headers(creds), timeout=30)
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "success": True,
                "issue_number": data.get("number"),
                "message": f"Issue created: #{data.get('number')} - {data.get('title')}"
            }
        return {"success": False, "message": f"ERROR: GitHub API returned {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "message": f"ERROR: {e}"}


async def list_comments_raw_github(
    creds: GitHubCredentials,
    issue_number: int,
    params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    List comments for a GitHub issue with bot filtering.
    
    Args:
        creds: GitHub credentials
        issue_number: Issue number
        params: Request parameters (page_size, max_pages)
        
    Returns:
        List of comment dicts (bot comments filtered out)
    """
    page_size = params.get("page_size", 50)
    max_pages = params.get("max_pages", 10)
    
    headers = _headers(creds)
    all_comments = []
    page = 1
    
    while page <= max_pages:
        api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments"
        query_params = {"per_page": page_size, "page": page}
        
        try:
            resp = requests.get(api_url, headers=headers, params=query_params, timeout=30)
            if resp.status_code != 200:
                logger.error(f"[list_comments_raw_github] API error {resp.status_code}: {resp.text}")
                break
            
            comments = resp.json()
            if not comments:
                break
            
            for c in comments:
                # Filter out bot comments and self-generated comments
                body = c.get("body", "")
                user = c.get("user", {})
                username = user.get("login", "")
                user_type = user.get("type", "")
                
                # Skip if has agix-id tag
                if "agix-id:" in body:
                    logger.debug(f"[list_comments_raw_github] Skipping self-generated comment {c.get('id')}")
                    continue
                
                # Skip bot accounts
                if user_type.lower() == "bot" or "[bot]" in username.lower():
                    logger.debug(f"[list_comments_raw_github] Skipping bot comment from {username}")
                    continue
                
                all_comments.append(c)
            
            if len(comments) < page_size:
                break
            page += 1
            
        except Exception as e:
            logger.error(f"[list_comments_raw_github] Request failed: {e}")
            break
    
    return all_comments


async def check_triage_status_github(
    creds: GitHubCredentials,
    issue_number: int
) -> Dict[str, Any]:
    """
    Deterministic triage status check for GitHub issue.
    
    Returns structured JSON indicating whether triage is needed.
    
    Args:
        creds: GitHub credentials
        issue_number: Issue number to check
        
    Returns:
        Dict with decision, reason, and action
    """
    import json
    import re
    
    if not creds.is_complete():
        return {"error": "Missing credentials", "decision": "ERROR"}
    
    headers = _headers(creds)
    api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/issues/{issue_number}/comments"
    
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return {"error": f"GitHub API returned {resp.status_code}", "decision": "ERROR"}
        
        comments = resp.json()
        
        # No comments = skip
        if not comments:
            return {
                "issue_number": issue_number,
                "decision": "SKIP",
                "reason": "No comments on issue - nothing to respond to",
                "action": "None - skip this issue",
                "total_comments": 0
            }
        
        # Check last comment for agix-id
        last_comment = comments[-1]
        last_body = last_comment.get("body", "")
        last_user = last_comment.get("user", {}).get("login", "Unknown")
        has_agix_id = "<!-- agix-id:" in last_body or "<!-- AI-HANDLED" in last_body
        
        if has_agix_id:
            match = re.search(r'agix-id:\s*([a-f0-9]+)', last_body)
            tag_id = match.group(1) if match else "detected"
            
            return {
                "issue_number": issue_number,
                "decision": "SKIP",
                "reason": f"Last comment (#{len(comments)}) by bot has agix-id: {tag_id}",
                "action": "SKIP to next issue",
                "total_comments": len(comments)
            }
        else:
            return {
                "issue_number": issue_number,
                "decision": "TRIAGE",
                "reason": f"Last comment (#{len(comments)}) by user '{last_user}' has NO agix-id",
                "action": "CALL analyze_issue NOW",
                "total_comments": len(comments),
                "last_commenter": last_user
            }
            
    except Exception as e:
        return {"error": str(e), "decision": "ERROR"}


async def list_branches_github(
    creds: GitHubCredentials,
    params: Dict[str, Any] = None
) -> List[str]:
    """
    List branch names from GitHub.
    
    Args:
        creds: GitHub credentials
        params: Optional parameters
        
    Returns:
        List of branch names
    """
    if not creds.is_complete():
        return []
    
    api_url = f"{API_BASE}/repos/{creds.owner}/{creds.repo}/branches"
    headers = _headers(creds)
    
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return [b.get("name") for b in resp.json()]
        logger.error(f"[list_branches_github] API error {resp.status_code}: {resp.text}")
        return []
    except Exception as e:
        logger.error(f"[list_branches_github] Request failed: {e}")
        return []


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'list_issues_github',
    'get_issue_github',
    'comment_github',
    'create_issue_github',
    'list_comments_raw_github',
    'check_triage_status_github',
    'list_branches_github'
]
