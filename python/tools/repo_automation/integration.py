"""
Integration module for repository automation.
Handles Stage 4: Dev Integration & Multi-Merge.
"""
from __future__ import annotations
import os
import re
import subprocess
from typing import Dict, Any, List, TYPE_CHECKING
from typing import Dict, Any, List, TYPE_CHECKING, Optional

from .base import logger
from .git_ops import clone_or_update_repo, verify_clone_context, _validate_git_workspace
from .providers import load_github_credentials, load_forgejo_credentials
from .utils import generate_branch_name

# Refined trigger patterns for Stage 4 Integration
TRIGGER_PATTERN = r"(?i)(agix|agix)\s+merge(\s+all|\s+#\d+)*"

if TYPE_CHECKING:
    from python.agent import Agent

async def list_ready_for_merge(provider: str, creds: Any, params: Dict[str, Any], agent: "Agent") -> List[Dict[str, Any]]:
    """
    Identify issues and their associated branches that are 'Ready to Merge'.
    Handles three modes:
    1. Single Merge: 'agix merge' (current issue only)
    2. Batch Merge: 'agix merge all' (labels #approved)
    3. Targeted Merge: 'agix merge #123 #456' (specific issues)
    """
    from .sweeps import check_for_integration_trigger
    # verify_clone_context is already imported at the top level, no need to re-import
    ready = []
    
    # Check for trigger body in params
    trigger_raw = params.get("trigger_body", "")
    if isinstance(trigger_raw, list):
        trigger_raw = " ".join([str(t) for t in trigger_raw])
    trigger_body = str(trigger_raw).lower()
    
    issue_number = params.get("issue_number")
    
    # Mode Detection
    is_batch_all = params.get("is_batch_all", False)
    explicit_ids = params.get("explicit_ids", [])
    
    # If no IDs and no "all", but "merge" is present, it's a Single Merge on current issue
    if not is_batch_all and not explicit_ids and "merge" in trigger_body:
        if issue_number:
            explicit_ids = [int(issue_number)]

    logger.info(f"[integration] Mode: {'Batch' if is_batch_all else 'Targeted/Single'}, Explicit IDs: {explicit_ids}")

    if provider == "github":
        from .github import list_issues_github, list_comments_raw_github
        
        # PROACTIVE BRANCH DISCOVERY: Sync once
        project_path = await verify_clone_context(provider, params, agent.context)
        
        # Determine candidate issue numbers and build issue lookup
        candidate_nums = set()
        issues_lookup = {}  # num -> issue dict for title lookup
        if is_batch_all:
            # For "merge all", we scan top open issues for #approved or triggers
            issues = await list_issues_github(creds, {"state": "open", "limit": 30})
            for i in issues:
                if isinstance(i, dict):
                    num = int(i.get("number", 0))
                    candidate_nums.add(num)
                    issues_lookup[num] = i
        else:
            # For Targeted/Single, we only care about the explicit IDs
            candidate_nums = set(explicit_ids)
            
        for num in candidate_nums:
            # Get issue title for branch name generation
            issue_data = issues_lookup.get(num, {})
            issue_title = issue_data.get("title", f"issue-{num}")
            
            # PROACTIVE BRANCH DISCOVERY
            branch_name = await find_branch_for_issue(num, project_path)
            branch_found_on_remote = branch_name is not None
            
            # fallback to generated name if not found (for non-batch modes)
            if not branch_name:
                branch_name = generate_branch_name(num, issue_title)
            
            # 1. Always include if in explicit_ids
            if num in explicit_ids:
                ready.append({"number": num, "title": issue_title, "branch": branch_name})
                continue
            
            # 2. For batch_all: ONLY include if branch actually exists on remote
            #    Branch existence = build completed = eligible for merge
            #    No branch = stale issue from prior runs → skip
            if is_batch_all:
                if branch_found_on_remote:
                    logger.info(f"[integration] Auto-including #{num} — branch '{branch_name}' exists on remote")
                    ready.append({"number": num, "title": issue_title, "branch": branch_name})
                else:
                    logger.info(f"[integration] Skipping #{num} — no branch found on remote (stale issue?)")
                continue
                
            # 4. For non-batch: check for #approved in comments or TRIGGER_PATTERN
            comments = await list_comments_raw_github(creds, num, {})
            if comments:
                last_comment = comments[-1]
                last_body_raw = last_comment.get("body", "") if isinstance(last_comment, dict) else str(last_comment)
                if isinstance(last_body_raw, list):
                    last_body_raw = " ".join([str(t) for t in last_body_raw])
                last_body = str(last_body_raw)

                # Note: We check for #approved OR the merge trigger pattern
                if last_body and (re.search(TRIGGER_PATTERN, last_body) or "#approved" in last_body.lower()):
                    ready.append({"number": num, "title": issue_title, "branch": branch_name})
                    
    elif provider == "forgejo":
        from .forgejo import list_issues_raw_forgejo, list_comments_raw_forgejo
        
        candidate_nums = set()
        issues_lookup = {}  # num -> issue dict for title lookup
        if is_batch_all:
            issues = await list_issues_raw_forgejo(creds, {"state": "open", "limit": 30})
            for i in issues:
                num = i.get("number")
                candidate_nums.add(num)
                issues_lookup[num] = i
        else:
            candidate_nums = set(explicit_ids)
            
        for num in candidate_nums:
            # Get issue title for branch name generation
            issue_data = issues_lookup.get(num, {})
            issue_title = issue_data.get("title", f"issue-{num}")
            
            project_path = await verify_clone_context(provider, params, agent.context)
            branch_name = await find_branch_for_issue(num, project_path)
            branch_found_on_remote = branch_name is not None

            if not branch_name:
                branch_name = generate_branch_name(num, issue_title)
            
            if num in explicit_ids:
                ready.append({"number": num, "title": issue_title, "branch": branch_name})
                continue
            
            # For batch_all: ONLY include if branch actually exists on remote
            if is_batch_all:
                if branch_found_on_remote:
                    logger.info(f"[integration] Auto-including #{num} — branch '{branch_name}' exists on remote")
                    ready.append({"number": num, "title": issue_title, "branch": branch_name})
                else:
                    logger.info(f"[integration] Skipping #{num} — no branch found on remote (stale issue?)")
                continue
                
            comments = await list_comments_raw_forgejo(creds, num, {})
            if comments:
                last_body_raw = comments[-1].get("body", "")
                if isinstance(last_body_raw, list):
                    last_body_raw = " ".join([str(t) for t in last_body_raw])
                last_body = str(last_body_raw)

                if last_body and (re.search(TRIGGER_PATTERN, last_body) or "#approved" in last_body.lower()):
                    ready.append({"number": num, "title": issue_title, "branch": branch_name})
                    
    return ready

async def find_branch_for_issue(issue_number: int, project_path: str) -> Optional[str]:
    """Find the best branch match for an issue number."""
    import subprocess
    patterns = [
        f"agixagi-{issue_number}-",
        f"issue-{issue_number}-",
        f"agix-{issue_number}-"
    ]
    
    try:
        # CRITICAL: Use cwd= instead of git -C to satisfy GitGuard shim
        # GitGuard checks CWD, not -C argument, so -C gets blocked
        fetch_result = subprocess.run(
            ["git", "fetch", "--prune", "origin"],
            capture_output=True, text=True, timeout=30,
            cwd=project_path
        )
        if fetch_result.returncode != 0:
            logger.warning(f"[integration] git fetch --prune failed in {project_path}: {fetch_result.stderr.strip()}")
        
        # Get all remote branches (now includes freshly-pushed ones)
        result = subprocess.run(
            ["git", "branch", "-r"],
            capture_output=True, text=True,
            cwd=project_path
        )
        if result.returncode != 0:
            logger.error(f"[integration] find_branch_for_issue: git branch -r failed in {project_path}: {result.stderr.strip()}")
            return None
            
        branches = [b.strip().replace("origin/", "") for b in result.stdout.split("\n") if b.strip()]
        
        # Look for matches
        matches = []
        for b in branches:
            for p in patterns:
                if p in b:
                    matches.append(b)
                    
        if not matches:
            logger.debug(f"[integration] No branch found for #{issue_number} in {len(branches)} remote branches")
            return None
            
        best = sorted(matches, key=len, reverse=True)[0]
        logger.info(f"[integration] Found branch for #{issue_number}: {best}")
        return best
    except Exception as e:
        logger.error(f"[integration] Error finding branch for #{issue_number}: {e}")
        return None

async def create_integration_branch(project_path: str, base_branch: str = "main") -> str:
    """Create a new integration branch from base."""
    import time
    
    # CRITICAL: Validate workspace before any git operations
    _validate_git_workspace(project_path)
    
    integration_branch = f"agix-integration-{int(time.time())}"
    
    logger.info(f"[integration] Creating branch {integration_branch} at {project_path}")
    
    # helper for git commands — each command validated by GitGuard
    def git(args):
        cmd_str = " ".join(args)
        _validate_git_workspace(project_path, command=cmd_str)
        return subprocess.run(["git"] + args, capture_output=True, text=True, cwd=project_path)

    # 1. Fetch all branches first
    git(["fetch", "origin"])
    
    # 2. CRITICAL: Clean workspace before checkout to handle dirty state
    #    from previous build branches (TDD work leaves uncommitted files)
    git(["reset", "--hard"])
    git(["clean", "-fdx"])
    
    # 3. Robust Checkout base
    res = git(["checkout", base_branch])
    if res.returncode != 0:
        logger.warning(f"[integration] Local '{base_branch}' not found, trying origin")
        res = git(["checkout", "-b", base_branch, f"origin/{base_branch}"])
        if res.returncode != 0:
            # Last ditch effort: use --track
            res = git(["checkout", "--track", f"origin/{base_branch}"])
            if res.returncode != 0:
                err = f"Failed to find base branch '{base_branch}': {res.stderr or res.stdout}"
                logger.error(f"[integration] {err}")
                raise Exception(err)

    # 3. Pull latest
    git(["pull", "origin", base_branch])
    
    # 4. Create fresh integration branch
    result = git(["checkout", "-b", integration_branch])
    if result.returncode != 0:
        err = f"Failed to create integration branch '{integration_branch}': {result.stderr or result.stdout}"
        logger.error(f"[integration] {err}")
        raise Exception(err)
        
    return integration_branch

async def batch_merge(
    project_path: str, 
    integration_branch: str, 
    source_branches: List[str]
) -> Dict[str, Any]:
    """
    Merge a list of branches into the integration branch.
    Returns results including success/failure for each branch.
    """
    results = {
        "success": [],
        "conflicts": [],
        "errors": []
    }
    
    # CRITICAL: Validate workspace before any git operations
    _validate_git_workspace(project_path)
    
    def git(args):
        cmd_str = " ".join(args)
        _validate_git_workspace(project_path, command=cmd_str)
        return subprocess.run(["git"] + args, capture_output=True, text=True, cwd=project_path)
    
    # Ensure we are on the integration branch
    git(["checkout", integration_branch])
    
    for branch in source_branches:
        logger.info(f"[integration] Merging {branch} into {integration_branch}")
        
        # 1. CRITICAL: Explicitly fetch THIS branch to ensure ref is available
        fetch_res = git(["fetch", "origin", f"{branch}:{branch}"])
        if fetch_res.returncode != 0:
            # Also try fetch without local ref creation (just update FETCH_HEAD)
            fetch_res2 = git(["fetch", "origin", branch])
            if fetch_res2.returncode != 0:
                err_msg = f"Branch '{branch}' not found on remote: {fetch_res2.stderr.strip()}"
                logger.error(f"[integration] {err_msg}")
                results["errors"].append({"branch": branch, "error": err_msg})
                continue
        
        # 2. Verify the ref actually exists before merge
        verify = git(["rev-parse", "--verify", f"origin/{branch}"])
        if verify.returncode != 0:
            # Try using FETCH_HEAD as fallback
            verify_fh = git(["rev-parse", "--verify", "FETCH_HEAD"])
            if verify_fh.returncode != 0:
                err_msg = f"Ref 'origin/{branch}' could not be resolved after fetch"
                logger.error(f"[integration] {err_msg}")
                results["errors"].append({"branch": branch, "error": err_msg})
                continue
            # Use FETCH_HEAD for merge
            merge_ref = "FETCH_HEAD"
            logger.info(f"[integration] Using FETCH_HEAD for {branch}")
        else:
            merge_ref = f"origin/{branch}"
        
        # 3. Targeted Merge attempt
        merge_res = git(["merge", "--allow-unrelated-histories", merge_ref])
        
        if merge_res.returncode == 0:
            results["success"].append(branch)
        else:
            # Check if it's a conflict
            if "CONFLICT" in merge_res.stdout or "CONFLICT" in merge_res.stderr:
                logger.warning(f"[integration] Conflict detected in {branch}")
                results["conflicts"].append(branch)
                # Abort merge to keep integration branch clean for next ones
                git(["merge", "--abort"])
            else:
                logger.error(f"[integration] Error merging {branch}: {merge_res.stderr}")
                results["errors"].append({"branch": branch, "error": merge_res.stderr})
                git(["merge", "--abort"])
        
    return results

def parse_issue_numbers(text: str) -> List[int]:
    """Parses issue numbers from a given text string."""
    return [int(num) for num in re.findall(r'#(\d+)', text)]

async def integration_manager(provider: str, params: Dict[str, Any], agent: "Agent") -> str:
    import os
    """Main entry point for Stage 4 actions."""
    sub_action = params.get("sub_action", "list_ready")
    
    creds = load_github_credentials(agent.context, params) if provider == "github" else load_forgejo_credentials(agent.context, params)
    
    if sub_action == "list_ready":
        ready = await list_ready_for_merge(provider, creds, params, agent)
        if not ready:
            return "Stage 4: No issues found with #approved tag."
        
        output = "### 🏁 Issues Ready for Integration\n\n"
        for item in ready:
            output += f"- **#{item['number']}**: {item['title']} (Branch: `{item['branch']}`)\n"
        return output
        
    elif sub_action == "start_batch":
        project_path = await verify_clone_context(provider, params, agent.context)
        
        # CRITICAL: Strict validation - never allow "." or paths that would affect host repo
        if project_path == ".":
            return "ERROR: Project path cannot be '.'. Integration requires a dedicated project in /usr/projects/."
        if not os.path.isabs(project_path):
            return f"ERROR: Project path must be absolute, got: {project_path}"
        if not os.path.exists(project_path):
            return f"ERROR: Project path does not exist: {project_path}"
        
        # Double-check with workspace validation  
        try:
            _validate_git_workspace(project_path)
        except ValueError as e:
            return f"ERROR: Git workspace validation failed: {e}"
            
        # Determine explicit IDs from trigger body if present
        explicit_ids = []
        trigger_body = params.get("trigger_body", "")
        if trigger_body:
            explicit_ids = parse_issue_numbers(str(trigger_body))
            
        ready_params = {**params, "is_batch_all": not explicit_ids, "explicit_ids": explicit_ids}
        ready = await list_ready_for_merge(provider, creds, ready_params, agent)
        if not ready:
            no_branch_msg = (
                "⚠️ **No branches found ready for merge.**\n\n"
                "This can happen if:\n"
                "- Build tasks are still in progress (branches not yet pushed)\n"
                "- No build was triggered for this issue yet\n\n"
                "💡 To start a build, comment: `AGIX build`\n"
                "Then try merging again after the build completes."
            )
            # Post feedback to the issue so the user sees it
            issue_number = params.get("issue_number")
            if issue_number:
                if provider == "github":
                    from .github import comment_github
                    await comment_github(creds, int(issue_number), no_branch_msg)
                else:
                    from .forgejo import comment_forgejo
                    await comment_forgejo(creds, int(issue_number), no_branch_msg)
            return no_branch_msg
            
        branches = [item['branch'] for item in ready]
        
        # 1. Create integration branch
        try:
            int_branch = await create_integration_branch(project_path)
        except Exception as e:
            return f"ERROR: Integration branch creation failed: {e}"
            
        # 2. Batch merge
        results = await batch_merge(project_path, int_branch, branches)
        
        # 3. Build report
        output = f"### 🧬 Multi-Issue Integration Report: `{int_branch}`\n\n"
        output += f"✅ **Successfully Merged**: {', '.join(results['success']) if results['success'] else 'None'}\n"
        output += f"⚠️ **Merge Conflicts**: {', '.join(results['conflicts']) if results['conflicts'] else 'None'}\n"
        
        if results['errors']:
            output += "\n❌ **Errors**:\n"
            for err in results['errors']:
                output += f"- `{err['branch']}`: {err['error'][:100]}...\n"
                
        if not results['success'] and not results['conflicts'] and not results['errors']:
            output += "⚠️ **Warning**: No branches were found for merging. Check issue labels or branches.\n"
        elif not results['success']:
            output += "❌ **Result**: No branches were successfully merged due to conflicts or errors.\n"
            
        if results['success']:
            output += f"\n👉 Integration branch `{int_branch}` created. Pushing...\n"
            _validate_git_workspace(project_path, command=f"push origin {int_branch}")
            subprocess.run(["git", "push", "origin", int_branch], capture_output=True, cwd=project_path)
            output += f"✅ Integration branch `{int_branch}` pushed to remote.\n"
            
        # 4. DETERMINISTIC: Always post results to issue (tool-governed, not prompt-dependent)
        issue_number = params.get("issue_number")
        if issue_number:
            # Generate a unique hash for THIS report to avoid suppression
            from python.helpers.hashing import content_hash_short
            report_hash = content_hash_short(f"integration-{int_branch}", length=12)
            
            # Post the full report as a comment
            if provider == "github":
                from .github import comment_github
                await comment_github(creds, int(issue_number), output, hash_id=report_hash)
                logger.info(f"[integration] Posted merge results to GitHub issue #{issue_number}")
            else:
                from .forgejo import comment_forgejo
                await comment_forgejo(creds, int(issue_number), output)
                logger.info(f"[integration] Posted merge results to Forgejo issue #{issue_number}")
                
        return output

        
    elif sub_action == "integrate_single":
        issue_number = params.get("issue_number")
        if not issue_number:
            return "ERROR: issue_number required for integrate_single."
            
        project_path = await verify_clone_context(provider, params, agent.context)
        
        # CRITICAL: Strict validation - never allow "." or paths that would affect host repo
        if project_path == ".":
            return "ERROR: Project path cannot be '.'. Integration requires a dedicated project in /usr/projects/."
        if not os.path.isabs(project_path):
            return f"ERROR: Project path must be absolute, got: {project_path}"
        if not os.path.exists(project_path):
            return f"ERROR: Project path does not exist: {project_path}"
        
        # Double-check with workspace validation  
        try:
            _validate_git_workspace(project_path)
        except ValueError as e:
            return f"ERROR: Git workspace validation failed: {e}"
            
        # Determine branch name from number
        branch = params.get("branch") or f"agix-issue-{issue_number}-fix"
        
        # 1. Create/Use integration branch
        try:
            int_branch = await create_integration_branch(project_path)
        except Exception as e:
            return f"ERROR: Integration branch creation failed: {e}"
            
        # 2. Single merge
        results = await batch_merge(project_path, int_branch, [branch])
        
        # 3. Report
        output = f"### 🧬 Project Integration: Issue #{issue_number}\n\n"
        if branch in results['success']:
            output += f"✅ **Successfully Merged**: {branch}\n"
            output += f"\n👉 Integration branch `{int_branch}` updated. Pushing...\n"
            _validate_git_workspace(project_path, command=f"push origin {int_branch}")
            subprocess.run(["git", "push", "origin", int_branch], capture_output=True, cwd=project_path)
        else:
            if branch in results['conflicts']:
                output += f"⚠️ **Merge Conflict**: {branch}\n"
                # Feedback to issue on conflict
                feedback = f"⚠️ **Merge Aborted**: A conflict was detected while integrating `{branch}`. Please resolve manually or prompt for a fix."
                if provider == "github":
                    from .github import comment_github
                    await comment_github(creds, int(issue_number), feedback)
                else:
                    from .forgejo import comment_forgejo
                    await comment_forgejo(creds, int(issue_number), feedback)
            else:
                output += f"❌ **Error**: {results['errors'][0]['error'] if results['errors'] else 'Unknown'}\n"
                
        return output
        
    return f"Integration Manager: Unknown sub-action '{sub_action}'"
