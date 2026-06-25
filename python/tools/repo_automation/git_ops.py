"""
Git operations for repository automation.
Handles cloning, updating, and authenticated URL generation.

REFACTORED: Uses async_subprocess for non-blocking operations with proper timeouts.
"""
from __future__ import annotations

import os
import shutil
import logging
from typing import Optional

from .base import logger
from python.helpers.projects import PROJECT_META_DIR, LEGACY_PROJECT_META_DIR

# Import async subprocess utilities
from python.helpers.async_subprocess import (
    run_git_command,
    run_command,
    CommandResult,
    SubprocessConfig,
    SubprocessError,
    SubprocessTimeoutError,
)


# Import GitGuard for centralized protection
from python.helpers.git_guard import GitGuard, GitGuardError


# =============================================================================
# HOST REPOSITORY PROTECTION
# =============================================================================


def _validate_git_workspace(path: str, command: str = "git operation", project_name: Optional[str] = None) -> bool:
    """
    CRITICAL: Validate that git operations won't affect protected repos.
    
    Uses GitGuard for deterministic protection against host repo pollution.
    
    Protection levels:
    - HARD BLOCK: Commands that would traverse UP to /agix/.git or /agix/.git (host repo)
    - HARD BLOCK: Command targets path outside of designated project (if project_name provided)
    - SOFT WARNING: Commands without .git but no protected parent
    
    Args:
        path: The path where git operation will run (can be "." for CWD)
        command: The git command being run (for better error messages)
        project_name: Optional project name for scoped validation
        
    Returns:
        True if safe (may include soft warnings logged)
        
    Raises:
        ValueError if the command would affect the host repo or project mismatch
    """
    try:
        is_allowed, warning = GitGuard.validate_git_operation(
            path, 
            command, 
            raise_on_block=True,
            active_project=project_name
        )
        if warning:
            logger.warning(f"[_validate_git_workspace] {warning}")
        return is_allowed
    except GitGuardError as e:
        # Convert to ValueError for backwards compatibility
        raise ValueError(str(e)) from e


# =============================================================================
# GIT URL AUTHENTICATION
# =============================================================================


def get_authenticated_url(
    repo_url: str,
    token: str,
    provider: str = "github"
) -> str:
    """
    Inject authentication token into git URL.
    
    Args:
        repo_url: Repository URL
        token: Authentication token
        provider: 'github' or 'forgejo'
        
    Returns:
        URL with auth token injected
    """
    token = (token or "").strip()
    if not token:
        return repo_url
    
    # Inject token into HTTPS URL
    if "https://" in repo_url:
        if "@" in repo_url:
            # Already has credentials, don't double-inject
            return repo_url
            
        if provider == "github" and "github.com" in repo_url:
            # Use standard GitHub token authentication
            return repo_url.replace("https://", f"https://x-access-token:{token}@")
        
        return repo_url.replace("https://", f"https://{token}@")
    
    return repo_url


async def clone_or_update_repo(
    repo_url: str,
    local_path: str = None,
    token: str = None,
    provider: str = "github",
    project_name: Optional[str] = None
) -> str:
    """
    Clone or update a repository for code analysis.
    
    Uses async subprocess operations with proper timeouts:
    - Clone: 5 minute timeout
    - Fetch: 2 minute timeout
    - Pull: 1 minute timeout
    
    Args:
        repo_url: Repository URL to clone
        local_path: Local path for the repo (auto-generated if not provided)
        token: Authentication token
        provider: 'github' or 'forgejo'
        project_name: Optional project name for scoped validation
        
    Returns:
        Local path on success, error string on failure
    """
    if not local_path:
        # Extract repo name from URL
        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        local_path = f"/tmp/repo_analysis/{repo_name}"
    
    # CRITICAL: Validate workspace before any git operations
    try:
        _validate_git_workspace(local_path, project_name=project_name)
    except ValueError as e:
        logger.error(f"[clone_or_update_repo] {e}")
        return f"ERROR: {e}"
    
    authenticated_url = get_authenticated_url(repo_url, token, provider) if token else repo_url
    
    try:
        if os.path.exists(local_path):
            # Check if it's a valid git repo
            if not os.path.exists(os.path.join(local_path, ".git")):
                logger.warning(f"[clone_or_update_repo] {local_path} exists but is not a git repo. Re-cloning.")
                _surgical_clean(local_path)
                return await _fresh_clone(authenticated_url, local_path, project_name=project_name)
            
            # Try to pull latest
            logger.info(f"[clone_or_update_repo] Updating existing repo at {local_path}")
            
            # CRITICAL: Fetch ALL branches first (needed for merge operations)
            # Timeout: 2 minutes
            fetch_result = await run_git_command(
                ["-C", local_path, "fetch", "--all", "--prune"],
                timeout=120.0,
                # Scoping already verified by _validate_git_workspace above
            )
            if not fetch_result.success:
                logger.warning(f"[clone_or_update_repo] Git fetch --all failed: {fetch_result.stderr}")
            
            # Then pull current branch (timeout: 1 minute)
            pull_result = await run_git_command(
                ["-C", local_path, "pull", "--ff-only"],
                timeout=60.0,
            )
            
            if not pull_result.success:
                logger.warning(f"[clone_or_update_repo] Git pull failed: {pull_result.stderr}, doing fresh clone")
                _surgical_clean(local_path)
                return await _fresh_clone(authenticated_url, local_path, project_name=project_name)
            
            return local_path
        else:
            # Clone fresh
            return await _fresh_clone(authenticated_url, local_path, project_name=project_name)
            
    except SubprocessTimeoutError as e:
        logger.error(f"[clone_or_update_repo] Git operation timed out: {e}")
        return "ERROR: Git operation timed out"
    except SubprocessError as e:
        logger.error(f"[clone_or_update_repo] Git operation failed: {e}")
        return f"ERROR: {e}"
    except Exception as e:
        logger.error(f"[clone_or_update_repo] Git operation failed: {e}")
        return f"ERROR: {e}"


def _surgical_clean(local_path: str):
    """
    Remove everything in path EXCEPT project meta dirs (preserves project settings).
    """
    if not os.path.exists(local_path):
        return
    
    for item in os.listdir(local_path):
        item_path = os.path.join(local_path, item)
        if item in (PROJECT_META_DIR, LEGACY_PROJECT_META_DIR):
            continue
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path, ignore_errors=True)
            else:
                os.remove(item_path)
        except Exception as e:
            logger.warning(f"[_surgical_clean] Failed to remove {item_path}: {e}")


async def _fresh_clone(authenticated_url: str, local_path: str, project_name: Optional[str] = None) -> str:
    """
    Perform a fresh shallow clone. Handles existing non-empty directories safely.
    
    Uses async subprocess with proper timeouts.
    """
    # Mask token in logs
    log_url = authenticated_url.split('@')[-1] if '@' in authenticated_url else authenticated_url
    logger.info(f"[_fresh_clone] Syncing {log_url} to {local_path} (Project: {project_name})")
    
    os.makedirs(local_path, exist_ok=True)
    
    # Check if directory already has files (like project meta dirs)
    if os.listdir(local_path):
        logger.info(f"[_fresh_clone] Directory not empty, using init/fetch/reset strategy")
        
        # Init (quick, 30s timeout)
        init_result = await run_git_command(
            ["init"],
            cwd=local_path,
            timeout=30.0,
        )
        if not init_result.success:
            logger.error(f"[_fresh_clone] git init failed: {init_result.stderr}")
            return f"ERROR: git init failed: {init_result.stderr}"
        
        # Try to add remote first. If it exists, set-url to ensure it's correct.
        add_result = await run_git_command(
            ["remote", "add", "origin", authenticated_url],
            cwd=local_path,
            timeout=30.0,
        )
        if not add_result.success:
            # If add fails (likely because it already exists), use set-url
            remote_result = await run_git_command(
                ["remote", "set-url", "origin", authenticated_url],
                cwd=local_path,
                timeout=30.0,
            )
        else:
            remote_result = add_result
        
        if not remote_result.success:
            logger.error(f"[_fresh_clone] remote setup failed: {remote_result.stderr}")
            return f"ERROR: remote setup failed: {remote_result.stderr}"
        
        # Fetch (network bound, 2 minute timeout)
        # REMOVED --depth 1: Full history is required for multi-branch integration/merging
        fetch_result = await run_git_command(
            ["fetch", "origin"],
            cwd=local_path,
            timeout=120.0,
        )
        if not fetch_result.success:
            logger.error(f"[_fresh_clone] fetch failed: {fetch_result.stderr}")
            return f"ERROR: fetch failed: {fetch_result.stderr}"
        
        # Reset (quick, 30s timeout)
        reset_result = await run_git_command(
            ["reset", "--hard", "origin/HEAD"],
            cwd=local_path,
            timeout=30.0,
        )
        if not reset_result.success:
            # Fallback to common branch names
            for branch in ["main", "master"]:
                reset_result = await run_git_command(
                    ["reset", "--hard", f"origin/{branch}"],
                    cwd=local_path,
                    timeout=30.0,
                )
                if reset_result.success:
                    break
            
            if not reset_result.success:
                logger.error(f"[_fresh_clone] Manual sync failed: {reset_result.stderr}")
                return f"ERROR: manual sync failed: {reset_result.stderr}"
        
        # CRITICAL: Set upstream tracking for the current branch to prevent 'no tracking information' errors
        # on subsequent pulls.
        curr_branch_res = await run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=local_path, timeout=10.0)
        if curr_branch_res.success:
            curr_branch = curr_branch_res.stdout.strip()
            await run_git_command(
                ["branch", f"--set-upstream-to=origin/{curr_branch}", curr_branch],
                cwd=local_path,
                timeout=10.0
            )
            
        return local_path
    
    # Standard clone for empty directories (timeout: 5 minutes)
    # REMOVED --depth 1: Full history is required for multi-branch integration/merging
    clone_result = await run_git_command(
        ["clone", authenticated_url, "."],
        cwd=local_path,
        timeout=300.0,
    )
    
    if not clone_result.success:
        logger.error(f"[_fresh_clone] Clone failed: {clone_result.stderr}")
        return f"ERROR: clone failed: {clone_result.stderr}"
    
    return local_path


# =============================================================================
# PROJECT CONTEXT VERIFICATION
# =============================================================================


async def verify_clone_context(
    provider: str,
    params: dict,
    agent_context
) -> str:
    """
    Ensure a repository is correctly cloned within a project context.
    
    Args:
        provider: 'github' or 'forgejo'
        params: Request parameters with owner/repo
        agent_context: Agent context for project lookup
        
    Returns:
        Absolute local project path (NEVER returns '.')
        
    Raises:
        ValueError if path validation fails
    """
    from python.helpers import projects
    from .providers import load_github_credentials, load_forgejo_credentials
    
    project_path = params.get("project_path", ".")
    
    # CRITICAL: Never allow '.' - resolve to absolute path and validate
    if project_path == ".":
        project_path = os.getcwd()
        
    # Ensure absolute path
    project_path = os.path.abspath(project_path)
    
    # CRITICAL: Block /agix/ or /agix/ root - must be in /agix/usr/projects/ or /agix/usr/projects/
    if (project_path.startswith("/agix/") or project_path.startswith("/agix/")) and "/usr/projects/" not in project_path:
        raise ValueError(
            f"BLOCKED: verify_clone_context refusing path '{project_path}'. "
            "Repository operations must be in /agix/usr/projects/<project>/ or /agix/usr/projects/<project>/."
        )
    
    if provider not in ("forgejo", "github"):
        return project_path
    
    target_project_name = None
    
    # Get credentials and build repo URL
    if provider == "github":
        creds = load_github_credentials(agent_context, params)
        if not creds.owner or not creds.repo:
            return project_path
        repo_url = f"https://{creds.token}@github.com/{creds.owner}/{creds.repo}.git"
        repo_name = creds.repo
        repo_owner = creds.owner
    else:
        creds = load_forgejo_credentials(agent_context, params)
        if not creds.owner or not creds.repo:
            return project_path
        repo_url = f"{creds.url}/{creds.owner}/{creds.repo}.git"
        repo_name = creds.repo
        repo_owner = creds.owner
    
    ctx_id = agent_context.id if hasattr(agent_context, 'id') else str(agent_context)
    
    # Search for existing project matching this repo
    existing_project = projects.find_project_by_git_remote(repo_url)
    
    if existing_project:
        project_path = projects.get_project_folder(existing_project)
        logger.info(f"[verify_clone_context] Found existing project: {existing_project}")
        target_project_name = existing_project
        
        if projects.get_context_project_name(agent_context) != existing_project:
            await projects.activate_project(ctx_id, existing_project)
    else:
        # Try to match by project name patterns
        projects_list = projects.get_active_projects_list()
        repo_lower = repo_name.lower()
        owner_lower = repo_owner.lower() if repo_owner else ""
        # Match the canonical naming: repo-{owner}-{repo}
        repo_prefixed_full = f"repo-{owner_lower}-{repo_lower}" if owner_lower else f"repo-{repo_lower}"
        repo_prefixed_short = f"repo-{repo_lower}"  # Legacy fallback
        
        # Try repo-{owner}-{repo} first (canonical), then repo-{repo} (legacy)
        for p in projects_list:
            orig_name = p.get("name", "")
            if orig_name.lower() == repo_prefixed_full:
                target_project_name = orig_name
                break
        
        # Legacy fallback: repo-{repo} without owner
        if not target_project_name:
            for p in projects_list:
                orig_name = p.get("name", "")
                if orig_name.lower() == repo_prefixed_short:
                    target_project_name = orig_name
                    break
        
        # Try direct name match
        if not target_project_name:
            for p in projects_list:
                orig_name = p.get("name", "")
                if orig_name.lower() == repo_lower:
                    target_project_name = orig_name
                    break
        
        if target_project_name:
            project_path = projects.get_project_folder(target_project_name)
            if projects.get_context_project_name(agent_context) != target_project_name:
                await projects.activate_project(ctx_id, target_project_name)
        else:
            # Create new project
            target_project_name = f"repo-{repo_owner}-{repo_name}" if repo_owner else f"repo-{repo_name}"
            logger.info(f"[verify_clone_context] Auto-creating project {target_project_name}")
            
            try:
                from python.helpers.project_setup import ProjectSetup
                setup = ProjectSetup(
                    project_name=target_project_name,
                    description=f"Auto-created for {provider.title()} repo: {creds.owner}/{repo_name}",
                    framework=None
                )
                setup_res = setup.run()
                if setup_res.success:
                    project_path = setup_res.project_path
                    await projects.activate_project(ctx_id, target_project_name)
                else:
                    # If project creation failed, do not fallback to "." as it's ambiguous
                    # Force the path to be in the intended projects directory
                    projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.isdir("/agix") else "/agix/usr/projects")
                    project_path = os.path.join(projects_dir, target_project_name)
                    logger.warning(f"[verify_clone_context] Using un-registered fallback path: {project_path}")
            except Exception as e:
                logger.warning(f"[verify_clone_context] Project creation error: {e}")
    
    # Ensure repo is cloned/updated
    if project_path != ".":
        clone_res = await clone_or_update_repo(
            repo_url,
            project_path,
            creds.token if hasattr(creds, 'token') else None,
            provider,
            project_name=target_project_name
        )
        if str(clone_res).startswith("ERROR"):
            logger.error(f"[verify_clone_context] Repo sync failed: {clone_res}")
        else:
            logger.info(f"[verify_clone_context] Repo synced at {project_path}")
    
    return project_path


# =============================================================================
# UNIVERSAL REPO CONTEXT HELPER
# =============================================================================

async def ensure_repo_context(
    provider: str,
    params: dict,
    agent_context,
    require_code: bool = True
) -> dict:
    """
    Universal helper to ensure proper repository context for all repo automation.
    
    Use this before ANY repository automation operation to ensure:
    1. A project exists for the repo (named after owner/repo)
    2. The repo is cloned with latest code (if require_code=True)
    3. All remote branches are fetched (if require_code=True)
    
    Args:
        provider: 'github' or 'forgejo'
        params: Request parameters with owner/repo
        agent_context: Agent context for project lookup
        require_code: If True, ensure git fetch --all is done. 
                      If False (for comments), just verify git exists.
    
    Returns:
        dict with:
            - success: bool
            - project_path: str (path to project dir)
            - error: str (if failed)
    
    Usage:
        # For code operations (build, merge):
        ctx = await ensure_repo_context(provider, params, agent.context, require_code=True)
        
        # For comment operations (can use cache):
        ctx = await ensure_repo_context(provider, params, agent.context, require_code=False)
    """
    try:
        from python.helpers import projects
        # Use existing verify_clone_context - it now calls clone_or_update_repo
        # which does git fetch --all 
        project_path = await verify_clone_context(provider, params, agent_context)
        
        if project_path == ".":
            return {
                "success": False,
                "project_path": None,
                "error": "Could not determine project context - got '.' fallback"
            }
        
        # Get active project name for scoping
        project_name = projects.get_context_project_name(agent_context)
        
        # Validate the path is safe
        try:
            _validate_git_workspace(project_path, project_name=project_name)
        except ValueError as e:
            return {
                "success": False,
                "project_path": None,
                "error": str(e)
            }
        
        # For require_code=False (comment operations), just verify .git exists
        if not require_code:
            git_dir = os.path.join(project_path, ".git")
            if not os.path.exists(git_dir):
                logger.warning(f"[ensure_repo_context] No .git at {project_path}, falling back to full clone")
                # Still need code, do full sync
                require_code = True
        
        # For require_code=True, verify_clone_context already did fetch+pull
        # via clone_or_update_repo, so we're good
        
        return {
            "success": True,
            "project_path": project_path,
            "error": None
        }
        
    except Exception as e:
        logger.error(f"[ensure_repo_context] Failed: {e}", exc_info=True)
        return {
            "success": False,
            "project_path": None,
            "error": str(e)
        }


# =============================================================================
# GIT WORKTREE OPERATIONS (Concurrent Build Support)
# =============================================================================


async def ensure_base_clone(
    repo_url: str,
    base_path: str,
    token: str = None,
    provider: str = "github",
    project_name: str = None
) -> str:
    """
    Ensure a base clone exists and is up-to-date.
    
    This is the shared clone that worktrees branch from. It maintains
    the .git directory, objects, refs, and authentication config.
    
    MUST be called inside build_setup_lock() for serialization.
    
    Args:
        repo_url: Repository URL
        base_path: Path for the base clone
        token: Authentication token
        provider: 'github' or 'forgejo'
        project_name: Project name for GitGuard scoping
        
    Returns:
        base_path on success, error string on failure
    """
    result = await clone_or_update_repo(
        repo_url, base_path, token, provider, project_name=project_name
    )
    if str(result).startswith("ERROR"):
        logger.error(f"[ensure_base_clone] Failed: {result}")
        return result
    
    # Ensure we're on the default branch (main/master) so worktrees
    # can branch from the latest remote state
    checkout_result = await run_git_command(
        ["-C", base_path, "checkout", "main"],
        timeout=15.0
    )
    if not checkout_result.success:
        # Try master as fallback
        await run_git_command(
            ["-C", base_path, "checkout", "master"],
            timeout=15.0
        )
    
    logger.info(f"[ensure_base_clone] Base clone ready at {base_path}")
    return base_path


async def create_worktree(
    base_path: str,
    worktree_path: str,
    branch_name: str,
    start_point: str = "origin/main",
    project_name: str = None
) -> str:
    """
    Create a git worktree for an isolated concurrent build.
    
    Each worktree gets:
    - Its own working directory with checked-out files
    - Its own index file (safe for concurrent git add/commit)
    - Its own HEAD tracking the specified branch
    - Shared objects/refs from the base clone
    
    MUST be called inside build_setup_lock() for serialization.
    
    Args:
        base_path: Path to the base clone (has .git directory)
        worktree_path: Path for the new worktree
        branch_name: New branch to create and check out
        start_point: Git ref to branch from (default: origin/main)
        project_name: Project name for GitGuard scoping
        
    Returns:
        worktree_path on success, error string on failure
    """
    import os
    
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
    
    # Validate via GitGuard — validate the BASE clone (which HAS .git),
    # NOT the worktree target (which gets .git AFTER worktree add runs).
    # Just sanity-check that worktree target is in the projects directory.
    if project_name:
        try:
            # Validate BASE path (has .git) — this is where the git command runs
            _validate_git_workspace(
                base_path, 
                command=f"worktree add {worktree_path} -b {branch_name}",
                project_name=None  # Don't scope to project — base clone is shared
            )
            # Sanity check: worktree path should be under projects dir
            projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.isdir("/agix") else "/agix/usr/projects")
            if not worktree_path.startswith(projects_dir):
                return f"ERROR: Worktree path {worktree_path} is not under {projects_dir}"
        except ValueError as e:
            return f"ERROR: GitGuard blocked worktree creation: {e}"
    
    # Create worktree with new branch from start_point
    # git -C <base> worktree add <wt_path> -b <branch> <start_point>
    result = await run_git_command(
        ["-C", base_path, "worktree", "add", worktree_path, "-b", branch_name, start_point],
        cwd=base_path,
        timeout=30.0
    )
    
    if not result.success:
        # Branch might already exist — try without -b
        logger.warning(f"[create_worktree] worktree add -b failed: {result.stderr}")
        
        # Clean up any partial state (git worktree add is atomic, but be safe)
        if os.path.exists(worktree_path):
            shutil.rmtree(worktree_path, ignore_errors=True)
        
        # Try checking out existing branch into worktree
        result = await run_git_command(
            ["-C", base_path, "worktree", "add", worktree_path, branch_name],
            cwd=base_path,
            timeout=30.0
        )
        
        if not result.success:
            logger.error(f"[create_worktree] Failed to create worktree: {result.stderr}")
            return f"ERROR: worktree creation failed: {result.stderr}"
    
    logger.info(f"[create_worktree] Worktree created at {worktree_path} on branch '{branch_name}'")
    return worktree_path


async def remove_worktree(base_path: str, worktree_path: str) -> bool:
    """
    Remove a git worktree after build completion.
    
    Safe to call even if the worktree was already deleted with rm -rf.
    Does NOT delete the remote branch — only local worktree + metadata.
    
    Args:
        base_path: Path to the base clone
        worktree_path: Path to the worktree to remove
        
    Returns:
        True if cleanup succeeded or was unnecessary
    """
    import os
    
    # Try graceful removal first
    result = await run_git_command(
        ["-C", base_path, "worktree", "remove", worktree_path, "--force"],
        timeout=30.0
    )
    
    if result.success:
        logger.info(f"[remove_worktree] Removed worktree at {worktree_path}")
        return True
    
    # If worktree was already deleted (rm -rf), just prune stale entries
    logger.warning(f"[remove_worktree] Graceful remove failed, pruning: {result.stderr}")
    prune_result = await run_git_command(
        ["-C", base_path, "worktree", "prune"],
        timeout=15.0
    )
    
    if prune_result.success:
        logger.info(f"[remove_worktree] Pruned stale worktree entries")
    
    # Clean up directory if it still exists
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
        logger.info(f"[remove_worktree] Cleaned up directory {worktree_path}")
    
    return True


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'get_authenticated_url',
    'clone_or_update_repo',
    'verify_clone_context',
    'ensure_repo_context',
    '_validate_git_workspace',
    'ensure_base_clone',
    'create_worktree',
    'remove_worktree',
]