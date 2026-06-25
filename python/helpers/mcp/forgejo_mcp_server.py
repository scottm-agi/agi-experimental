from __future__ import annotations

# Configure logging for MCP stability (must be before any other imports that might trigger logging)
from python.helpers.mcp_logging import init_mcp_logging
init_mcp_logging()

import asyncio
import os
import logging
import sys
from typing import List, Dict, Any, Optional


from pydantic import BaseModel, Field
import httpx
from fastmcp import FastMCP

import logging
import sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("forgejo-mcp")

# Environment variables for URL and Token (set by MCP configuration)
# REPO_OWNER, REPO_NAME, FORGEJO_TOKEN are read from Parameter/Secrets Store instead
FORGEJO_URL = os.getenv("FORGEJO_URL", "").rstrip('/')
_ENV_FORGEJO_TOKEN = os.getenv("FORGEJO_TOKEN", "")

def _get_forgejo_token():
    """Get FORGEJO_TOKEN from secrets manager, with env fallback."""
    try:
        from python.helpers.secrets_helper import get_secrets_manager
        # Use global context by default for MCP server
        sm = get_secrets_manager() 
        secrets = sm.load_secrets()
        token = secrets.get("FORGEJO_TOKEN")
        if token:
            return token
    except Exception as e:
        logger.warning(f"Could not read FORGEJO_TOKEN from secrets: {e}")
    return _ENV_FORGEJO_TOKEN

def _get_repo_config():
    """Get REPO_OWNER and REPO_NAME from parameter store, with env fallback."""
    try:
        from python.helpers.parameters import get_parameters_manager
        pm = get_parameters_manager()
        # Note: MCP server typically runs globally, so we use global params
        params = pm.load_parameters()
        
        repo_owner = os.getenv("FORGEJO_OWNER") or os.getenv("REPO_OWNER") or params.get("REPO_OWNER") or params.get("FORGEJO_OWNER") or ""
        repo_name = os.getenv("FORGEJO_REPO") or os.getenv("REPO_NAME") or params.get("REPO_NAME") or params.get("FORGEJO_REPO") or ""
        forgejo_url = os.getenv("FORGEJO_URL") or params.get("FORGEJO_URL") or FORGEJO_URL
        
        logger.info(f"Loaded from parameter store: owner={repo_owner}, repo={repo_name}, url={forgejo_url}")
        return repo_owner, repo_name, forgejo_url.rstrip('/')
    except Exception as e:
        logger.warning(f"Could not read from parameter store: {e}, falling back to env vars")
        return os.getenv("REPO_OWNER", "") or os.getenv("FORGEJO_OWNER", ""), os.getenv("REPO_NAME", "") or os.getenv("FORGEJO_REPO", ""), FORGEJO_URL

# Initialize FastMCP
mcp = FastMCP("forgejo")

class Issue(BaseModel):
    number: int
    title: str
    state: str
    body: Optional[str] = None
    html_url: str
    created_at: str
    user: Dict[str, Any]

def _get_headers():
    """Get headers with dynamic token from secrets manager."""
    token = _get_forgejo_token()
    return {
        "Authorization": f"token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

@mcp.tool()
async def list_issues(
    owner: str | None = None,
    repo: str | None = None,
    state: str = "open",
    page: int = 1,
    limit: int = 50
) -> str:
    """
    List issues from a Forgejo repository.
    """
    return await _list_issues_logic(owner, repo, state, page, limit)

async def _list_issues_logic(
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    state: str = "open",
    page: int = 1,
    limit: int = 50
) -> str:
    # Get config from parameter store
    config_owner, config_repo, config_url = _get_repo_config()
    target_owner = owner or config_owner
    target_repo = repo or config_repo
    target_url = config_url
    
    if not all([target_url, _get_forgejo_token(), target_owner, target_repo]):
        return "ERROR: Missing Forgejo configuration (URL, Token, Owner, or Repo)"
    
    async with httpx.AsyncClient() as client:
        url = f"{target_url}/api/v1/repos/{target_owner}/{target_repo}/issues"
        params = {
            "state": state,
            "page": page,
            "limit": limit
        }
        
        try:
            response = await client.get(url, headers=_get_headers(), params=params)
            response.raise_for_status()
            issues = response.json()
            
            if not issues:
                return "No issues found."
            
            result = f"### Issues in {target_owner}/{target_repo} ({state})\n\n"
            for issue in issues:
                created = issue.get('created_at', 'unknown')
                closed = f" | Closed: {issue['closed_at']}" if issue.get('closed_at') else ""
                result += f"- **#{issue['number']}**: {issue['title']} (by {issue['user']['login']})\n"
                result += f"  Status: {issue['state']} | Created: {created}{closed}\n"
                result += f"  URL: {issue['html_url']}\n"
            
            return result
        except httpx.HTTPStatusError as e:
            return f"ERROR: Forgejo API returned {e.response.status_code}: {e.response.text}"
        except Exception as e:
            return f"ERROR: {str(e)}"

@mcp.tool()
async def get_issue(issue_number: int | None = None, index: int | None = None, owner: str | None = None, repo: str | None = None) -> str:
    """
    Get detailed information about a specific Forgejo issue.
    Supports both 'issue_number' and 'index' as argument names.
    """
    target_index = issue_number if issue_number is not None else index
    if target_index is None:
        return "ERROR: Either 'issue_number' or 'index' MUST be provided."
    return await _get_issue_logic(target_index, owner, repo)

async def _get_issue_logic(issue_number: int, owner: Optional[str] = None, repo: Optional[str] = None) -> str:
    config_owner, config_repo, config_url = _get_repo_config()
    target_owner = owner or config_owner
    target_repo = repo or config_repo
    
    if not all([config_url, _get_forgejo_token(), target_owner, target_repo]):
        return "ERROR: Missing Forgejo configuration"
    
    async with httpx.AsyncClient() as client:
        url = f"{config_url}/api/v1/repos/{target_owner}/{target_repo}/issues/{issue_number}"
        
        try:
            response = await client.get(url, headers=_get_headers())
            response.raise_for_status()
            issue = response.json()
            
            result = f"## Issue #{issue['number']}: {issue['title']}\n"
            result += f"State: {issue['state']}\n"
            result += f"Author: {issue['user']['login']}\n"
            result += f"Created: {issue['created_at']}\n"
            result += f"URL: {issue['html_url']}\n\n"
            result += "### Description\n"
            result += f"{issue.get('body', '[No description]')}\n"
            
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"ERROR: Issue #{issue_number} not found in {target_owner}/{target_repo}."
            return f"ERROR: Forgejo API returned {e.response.status_code}"
        except Exception as e:
            return f"ERROR: {str(e)}"

@mcp.tool()
async def list_comments(issue_number: int | None = None, index: int | None = None, owner: str | None = None, repo: str | None = None, page: int = 1, limit: int = 50) -> str:
    """
    List comments on a specific Forgejo issue with pagination.
    Supports both 'issue_number' and 'index' as argument names.
    """
    target_index = issue_number if issue_number is not None else index
    if target_index is None:
        return "ERROR: Either 'issue_number' or 'index' MUST be provided."
    return await _list_comments_logic(target_index, owner, repo, page, limit)

async def _list_comments_logic(issue_number: int, owner: Optional[str] = None, repo: Optional[str] = None, page: int = 1, limit: int = 50) -> str:
    config_owner, config_repo, config_url = _get_repo_config()
    target_owner = owner or config_owner
    target_repo = repo or config_repo
    
    if not all([config_url, _get_forgejo_token(), target_owner, target_repo]):
        return "ERROR: Missing Forgejo configuration"
    
    async with httpx.AsyncClient() as client:
        url = f"{config_url}/api/v1/repos/{target_owner}/{target_repo}/issues/{issue_number}/comments"
        
        all_comments = []
        current_page = page
        
        try:
            while True:
                params = {"page": current_page, "limit": limit}
                response = await client.get(url, headers=_get_headers(), params=params)
                response.raise_for_status()
                page_comments = response.json()
                
                if not page_comments:
                    break
                    
                all_comments.extend(page_comments)
                
                if len(page_comments) < limit:
                    break
                
                current_page += 1
            
            if not all_comments:
                return f"No comments found on issue #{issue_number}."
            
            result = f"### Comments on Issue #{issue_number} ({len(all_comments)} found)\n\n"
            for comment in all_comments:
                result += f"**{comment['user']['login']}** ({comment['created_at']}) [ID: {comment['id']}]:\n"
                result += f"{comment['body']}\n\n---\n"
            
            return result
        except httpx.HTTPStatusError as e:
            return f"ERROR: Forgejo API returned {e.response.status_code}"
        except Exception as e:
            return f"ERROR: {str(e)}"

@mcp.tool()
async def create_issue_comment(issue_number: int, body: str, owner: str | None = None, repo: str | None = None) -> str:
    """
    Add a comment to a Forgejo issue.
    """
    return await _create_issue_comment_logic(issue_number, body, owner, repo)

async def _create_issue_comment_logic(issue_number: int, body: str, owner: Optional[str] = None, repo: Optional[str] = None) -> str:
    config_owner, config_repo, config_url = _get_repo_config()
    target_owner = owner or config_owner
    target_repo = repo or config_repo
    
    if not all([config_url, _get_forgejo_token(), target_owner, target_repo]):
        return "ERROR: Missing Forgejo configuration"
    
    # CRITICAL: Expand §§include() placeholders before posting to external API
    # Using inline regex to avoid circular import with strings module
    import re
    import logging
    _log = logging.getLogger("forgejo_mcp")
    
    def _expand_includes(text: str) -> str:
        """Inline expansion of §§include() placeholders."""
        pattern = r'§§include\(([^)]+)\)'
        def repl(match):
            path = match.group(1)
            try:
                with open(path, 'r') as f:
                    content = f.read()
                _log.info(f"Expanded §§include({path}) -> {len(content)} chars")
                return content
            except Exception as e:
                _log.warning(f"Could not read {path}: {e}")
                return match.group(0)  # Return original if file not readable
        return re.sub(pattern, repl, text)
    
    body = _expand_includes(body)
    
    async with httpx.AsyncClient() as client:
        url = f"{config_url}/api/v1/repos/{target_owner}/{target_repo}/issues/{issue_number}/comments"
        data = {"body": body}
        
        try:
            response = await client.post(url, headers=_get_headers(), json=data)
            response.raise_for_status()
            comment = response.json()
            return f"Successfully added comment to issue #{issue_number}. Comment ID: {comment['id']}"
        except httpx.HTTPStatusError as e:
            return f"ERROR: Failed to add comment ({e.response.status_code})"
        except Exception as e:
            return f"ERROR: {str(e)}"

@mcp.tool()
async def create_issue(title: str, body: str | None = None, labels: list[str] | None = None, owner: str | None = None, repo: str | None = None) -> str:
    """
    Create a new issue in a Forgejo repository.
    """
    return await _create_issue_logic(title, body, labels, owner, repo)

async def _create_issue_logic(title: str, body: Optional[str] = None, labels: Optional[List[str]] = None, owner: Optional[str] = None, repo: Optional[str] = None) -> str:
    config_owner, config_repo, config_url = _get_repo_config()
    target_owner = owner or config_owner
    target_repo = repo or config_repo
    
    if not all([config_url, _get_forgejo_token(), target_owner, target_repo]):
        return "ERROR: Missing Forgejo configuration"
    
    async with httpx.AsyncClient() as client:
        url = f"{config_url}/api/v1/repos/{target_owner}/{target_repo}/issues"
        data = {
            "title": title,
            "body": body or "",
            "labels": labels or []
        }
        
        try:
            response = await client.post(url, headers=_get_headers(), json=data)
            response.raise_for_status()
            issue = response.json()
            return f"Successfully created issue #{issue['number']}: {issue['html_url']}"
        except httpx.HTTPStatusError as e:
            return f"ERROR: Failed to create issue ({e.response.status_code})"
        except Exception as e:
            return f"ERROR: {str(e)}"
@mcp.tool()
async def upload_issue_attachment(
    issue_number: int, 
    file_path: str, 
    name: str | None = None, 
    owner: str | None = None, 
    repo: str | None = None
) -> str:
    """
    Upload an attachment to a Forgejo issue.
    """
    return await _upload_issue_attachment_logic(issue_number, file_path, name, owner, repo)

async def _upload_issue_attachment_logic(
    issue_number: int, 
    file_path: str, 
    name: Optional[str] = None, 
    owner: Optional[str] = None, 
    repo: Optional[str] = None
) -> str:
    config_owner, config_repo, config_url = _get_repo_config()
    target_owner = owner or config_owner
    target_repo = repo or config_repo
    
    if not all([config_url, _get_forgejo_token(), target_owner, target_repo]):
        return "ERROR: Missing Forgejo configuration"
    
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return f"ERROR: File not found: {file_path}"
    
    filename = name or os.path.basename(abs_path)
    
    async with httpx.AsyncClient() as client:
        url = f"{config_url}/api/v1/repos/{target_owner}/{target_repo}/issues/{issue_number}/assets"
        
        try:
            with open(abs_path, "rb") as f:
                files = {"attachment": (filename, f)}
                response = await client.post(
                    url, 
                    headers={"Authorization": f"token {_get_forgejo_token()}"}, 
                    files=files,
                    timeout=60
                )
            
            response.raise_for_status()
            attachment = response.json()
            return f"Successfully uploaded attachment '{filename}' to issue #{issue_number}. ID: {attachment['id']}"
        except httpx.HTTPStatusError as e:
            return f"ERROR: Forgejo API returned {e.response.status_code}: {e.response.text}"
        except Exception as e:
            return f"ERROR: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
