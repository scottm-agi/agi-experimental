"""
Utility functions for repository automation.
Contains helper functions for search, deduplication, and content processing.
"""
from __future__ import annotations
import os
import re
import subprocess
import shutil
import difflib
import json
from typing import Dict, Any, List

from .base import logger


def ripgrep_search(
    pattern: str, 
    path: str, 
    file_types: List[str] = None, 
    max_results: int = 20, 
    context_lines: int = 2
) -> List[Dict[str, Any]]:
    """
    Use ripgrep for fast, powerful code search. Ripgrep is REQUIRED.
    
    Args:
        pattern: Search pattern (regex supported)
        path: Directory to search (must be a valid project path, not system dirs)
        file_types: List of file types to include (e.g., ['py', 'js'])
        max_results: Maximum number of matches to return
        context_lines: Lines of context before/after match
        
    Returns:
        List of match dictionaries with file, line, content
    """
    # CRITICAL: Validate path is not a system directory
    abs_path = os.path.abspath(path)
    system_dirs = ["/proc", "/sys", "/dev", "/run", "/var/run", "/boot", "/lib", "/lib64", "/sbin", "/bin"]
    for sys_dir in system_dirs:
        if abs_path.startswith(sys_dir) or abs_path == "/":
            logger.warning(f"Refusing to search system directory: {abs_path}")
            return []
    
    # Ensure path exists and is within a reasonable scope
    if not os.path.exists(abs_path):
        logger.warning(f"Search path does not exist: {abs_path}")
        return []
    
    # Find ripgrep binary - it is REQUIRED, no grep fallback
    rg_bin = shutil.which("rg") or "/usr/bin/rg" or "/opt/homebrew/bin/rg"
    if not os.path.exists(rg_bin):
        logger.error("ripgrep (rg) not found! Install with: apt-get install ripgrep OR brew install ripgrep")
        return []
    
    cmd = [rg_bin, "--json", "-C", str(context_lines), "-m", str(max_results)]
    
    # Add file type filters
    if file_types:
        for ft in file_types:
            cmd.extend(["-t", ft])
    
    # Exclude common noise directories AND system directories
    exclusions = [
        # Common dev noise
        "node_modules", "vendor", ".git", "__pycache__", "venv", ".venv",
        "dist", "build", "tmp", "temp", "out", "target", ".cache",
        # System directories (in case search is from root)
        "proc", "sys", "dev", "run", "boot", "lib", "lib64", "sbin",
        # Container-specific
        "opt/venv*", "usr/lib", "usr/share"
    ]
    for exclude in exclusions:
        cmd.extend(["-g", f"!{exclude}"])
        cmd.extend(["-g", f"!**/{exclude}/**"])
    
    cmd.extend([pattern, abs_path])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        matches = []
        
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
                if data.get("type") == "match":
                    match_data = data.get("data", {})
                    matches.append({
                        "file": match_data.get("path", {}).get("text", ""),
                        "line": match_data.get("line_number", 0),
                        "content": match_data.get("lines", {}).get("text", "").strip()
                    })
            except (json.JSONDecodeError, KeyError):
                continue
        
        return matches
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep search timed out")
        return []
    except Exception as e:
        logger.warning(f"ripgrep error: {e}")
        return []


async def check_duplicate_comment(body: str, comments: List[Dict[str, Any]]) -> bool:
    """Heuristic check to prevent posting identical or nearly identical comments."""
    if not body or not comments:
        return False
        
    body_clean = body.strip().lower()
    
    for c in comments:
        c_body = c.get('body', '').strip().lower()
        if not c_body:
            continue
        
        # 1. Exact match
        if body_clean == c_body:
            logger.info(f"[DEBUG] Exact duplicate match with comment ID={c.get('id')}")
            return True
            
        # 2. Very high overlap (98% similarity) - only block near-exact duplicates
        # Threshold raised from 0.9 to 0.98 to allow follow-up responses with similar templates
        diff_ratio = difflib.SequenceMatcher(None, body_clean, c_body).ratio()
        if diff_ratio > 0.98:
            logger.info(f"[DEBUG] Near-exact duplicate ({diff_ratio:.2f}) with comment ID={c.get('id')}")
            return True
            
    return False


async def process_mermaid_blocks(
    body: str, 
    provider: str, 
    creds: Dict[str, Any], 
    issue_number: int,
    render_fn=None
) -> str:
    """
    Scan for mermaid blocks, render them to PNG, and embed in the body.
    
    Args:
        body: Comment body with potential mermaid blocks
        provider: Provider type (github/forgejo)
        creds: Credentials dict
        issue_number: Issue number for asset upload
        render_fn: Optional render function (for testing)
    """
    if "```mermaid" not in body:
        return body

    # Extract mermaid blocks
    mermaid_pattern = r"```mermaid\s*\n(.*?)\n\s*```"
    matches = re.finditer(mermaid_pattern, body, re.DOTALL)
    
    new_body = body
    for match in matches:
        mmd_code = match.group(1).strip()
        if not mmd_code:
            continue
        
        logger.info(f"[MERMAID] Rendering block for issue #{issue_number}")
        try:
            # For now, only Forgejo supports direct asset upload in this tool
            if provider == "forgejo":
                if render_fn:
                    asset_url = await render_fn(
                        mmd_code=mmd_code,
                        repo_owner=creds["owner"],
                        repo_name=creds["repo"],
                        issue_num=issue_number
                    )
                else:
                    from python.tools.mermaid_renderer import render_mermaid_png
                    asset_url = await render_mermaid_png(
                        mmd_code=mmd_code,
                        repo_owner=creds["owner"],
                        repo_name=creds["repo"],
                        issue_num=issue_number
                    )
                image_markdown = f"\n\n![Mermaid Diagram]({asset_url})\n\n"
                new_body = new_body.replace(match.group(0), match.group(0) + image_markdown)
            else:
                logger.warning(f"[MERMAID] PNG rendering not supported for provider '{provider}' yet.")
        except Exception as e:
            logger.error(f"[MERMAID] Failed to render block: {e}")
                
    return new_body


def generate_comment_hash(issue_number: int, trigger_id: str, suffix: str = "") -> str:
    """
    Generate a deterministic hash for comment deduplication.
    
    Args:
        issue_number: The issue number
        trigger_id: Trigger identifier (e.g., "initial" or "reply_to_123")
        suffix: Optional suffix for different comment types (e.g., "_expert")
    
    Returns:
        12-character hash string
    """
    from python.helpers.hashing import content_hash_short
    hash_input = f"{issue_number}:{trigger_id}{suffix}"
    return content_hash_short(hash_input, length=12)


def generate_hash_tag(comment_hash: str) -> str:
    """Generate the HTML comment tag for agix-id."""
    return f"\n\n<!-- agix-id: {comment_hash} -->"


def extract_hash_from_body(body: str) -> str | None:
    """Extract agix-id hash from comment body if present."""
    match = re.search(r'agix-id:\s*([a-f0-9]+)', body)
    return match.group(1) if match else None


def has_agix_marker(body: str) -> bool:
    """Check if body contains agix-id or AI-HANDLED marker."""
    return '<!-- agix-id:' in body or '<!-- AI-HANDLED' in body


def generate_branch_name(issue_number: int, title: str, prefix: str = "agixagi") -> str:
    """
    Generate a standardized git branch name for an issue.
    
    Args:
        issue_number: Issue number
        title: Issue title for slug generation
        prefix: Branch name prefix
    
    Returns:
        Branch name in format: {prefix}-{issue_number}-{slug}
    """
    slug = re.sub(r'[^a-zA-Z0-9-]', '-', title.lower())[:20].strip('-')
    return f"{prefix}-{issue_number}-{slug}"


def is_final_summary(body: str) -> bool:
    """
    Check if the body contains markers indicating this is a final TDD summary.
    Used to allow completion comments even if last comment was from bot.
    """
    final_markers = [
        "SUCCESS", "COMPLETE", "Implementation is verified", 
        "🎯", "✅", "TDD Implementation Complete", 
        "Mission Accomplished", "Completion Report"
    ]
    return any(marker in body for marker in final_markers)


def expand_body_variables(body: str) -> str:
    """
    Expand environment variables and file includes in body content.
    
    Args:
        body: Raw body content
    
    Returns:
        Expanded body content
    """
    from python.helpers.strings import replace_file_includes
    
    # Expand §§include() placeholders
    body = replace_file_includes(body)
    
    # Expand environment variables ($HOME, $PWD, etc.)
    try:
        body = os.path.expandvars(body)
    except Exception as e:
        logger.debug(f"Variable expansion failed: {e}")
    
    return body


def truncate_body(body: str, max_length: int = 64000) -> str:
    """
    Truncate body to maximum length with notice.
    
    Args:
        body: Body content
        max_length: Maximum allowed length
    
    Returns:
        Truncated body if necessary
    """
    if len(body) > max_length:
        logger.warning(f"Body too long ({len(body)} chars), truncating to {max_length}...")
        return body[:max_length] + "\n\n... (content truncated)"
    return body


def upload_attachment_forgejo(
    creds: Dict[str, str], 
    issue_number: int, 
    file_path: str
) -> bool:
    """
    Upload an attachment to a Forgejo issue.
    
    Args:
        creds: Forgejo credentials dict
        issue_number: Issue number
        file_path: Path to file to upload
    
    Returns:
        True if upload successful, False otherwise
    """
    import requests
    import time
    
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        logger.warning(f"Attachment file not found: {file_path}")
        return False

    api_url = f"{creds['url']}/api/v1/repos/{creds['owner']}/{creds['repo']}/issues/{issue_number}/assets"
    try:
        headers = {"Authorization": f"token {creds['token']}"}
        
        with open(abs_path, "rb") as f:
            files = {"attachment": (os.path.basename(abs_path), f)}
            
            res = requests.post(api_url, headers=headers, files=files, timeout=60)
            
            # Issue #284/474: Transient bug protection - retry once on 5xx errors
            if res.status_code >= 500:
                logger.warning(f"[upload_attachment_forgejo] Transient internal error ({res.status_code}), retrying...")
                time.sleep(2)
                f.seek(0)  # Reset file pointer for retry
                res = requests.post(api_url, headers=headers, files=files, timeout=60)
            
            if res.status_code in (200, 201):
                logger.info(f"Uploaded attachment {file_path} to issue #{issue_number}")
                return True
            else:
                logger.error(f"Failed to upload attachment {file_path}: {res.status_code} {res.text}")
                return False
    except Exception as e:
        logger.error(f"Error uploading attachment {file_path}: {e}")
        return False


def validate_issue_content(body: str) -> Dict[str, Any]:
    """
    Preview what an issue or comment body will look like after variable expansion.
    Checks for any remaining unexpanded placeholders.
    
    Args:
        body: Body content to validate
    
    Returns:
        Dict with status, unexpanded vars, and expanded content
    """
    from python.helpers.strings import replace_file_includes
    
    # Expand §§include()
    expanded_body = replace_file_includes(body)
    
    # Check for generic variable patterns that might be unexpanded
    unexpanded = []
    
    # Match §§include(path) if it still exists (e.g. file not found)
    if "§§include(" in expanded_body:
        unexpanded.append("§§include(...)")
    
    # Match ${VAR} or {{VAR}}
    shell_vars = re.findall(r"\$\{[A-Za-z0-9_]+\}", expanded_body)
    if shell_vars:
        unexpanded.extend(list(set(shell_vars)))
        
    double_braces = re.findall(r"\{\{[A-Za-z0-9_]+\}\}", expanded_body)
    if double_braces:
        unexpanded.extend(list(set(double_braces)))
    
    return {
        "valid": len(unexpanded) == 0,
        "unexpanded": unexpanded,
        "expanded_content": expanded_body
    }