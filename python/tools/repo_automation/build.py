"""
Build automation module for repository automation.
Contains functions for TDD build task triggering and implementation workflows.
"""
from __future__ import annotations
import os
import re
from typing import Dict, Any, TYPE_CHECKING

from .base import logger
from .utils import generate_branch_name
from python.helpers.output_truncation import truncate_output_middle_out

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.task_state import TaskStateManager


def build_tdd_prompt(
    issue_number: int,
    branch_name: str,
    repo_url: str,
    provider: str,
    issue_text: str,
    title: str,
    project_name: str = None,
    project_path: str = None,
    base_clone_path: str = None
) -> str:
    """
    Build the TDD workflow prompt for a build task.
    
    Args:
        issue_number: Issue number
        branch_name: Git branch name
        repo_url: Repository URL
        provider: Provider type (github/forgejo)
        issue_text: Full issue text for context
        title: Issue title
        project_name: Optional project name for workspace safety
        project_path: Absolute path to the worktree directory
        base_clone_path: Absolute path to the base clone (for worktree cleanup)
    
    Returns:
        Complete TDD prompt string
    """
    # Build Phase 1 based on whether we have a pre-cloned project path
    if project_path:
        phase1 = f"""#### Phase 1: Environment Verification
> [!CAUTION]
> **WORKSPACE SAFETY**: Your repository has been pre-cloned and the branch pre-created deterministically.
> **ALL work MUST happen in**: `{project_path}`
> **NEVER** run git commands, create files, or modify code outside this directory.
> **NEVER** clone the repository — it is already cloned.

1. **VERIFY**: Confirm you are in the correct directory `{project_path}` and on branch `{branch_name}` by running `git -C {project_path} status`.
2. **DO NOT CLONE**: The repository is already set up. Do not run `git clone`.
3. **DO NOT CREATE BRANCHES**: The branch `{branch_name}` is already checked out."""
    else:
        # Legacy fallback (should not normally be reached)
        phase1 = f"""#### Phase 1: Environment Setup
> [!CAUTION]
> **WORKSPACE SAFETY**: You are running in an isolated project environment. You MUST ensure all `git` operations and code changes are performed within the current project directory (e.g., `usr/projects/{project_name or 'current'}`). Never modify the parent `/agix/` or root repository files.

1. **CLONE**: Clone the repository `{repo_url}` into the current project directory if not already present.
2. **BRANCH**: Create and checkout a NEW branch named `{branch_name}` (`git checkout -b {branch_name}`)."""

    return f"""## TDD Build Task: Issue #{issue_number}

**Branch**: `{branch_name}`
**Repository**: {repo_url}
**Project Directory**: `{project_path or 'current project'}`
**Trigger**: agix build

### YOUR MISSION: TDD Implementation

> [!IMPORTANT]
> **TASK COMPLETION CRITERIA**: This task is considered **FAILED** if you push the branch but forget to post the completion comment back to the issue. You MUST use `repository_automation.execute(action='comment', ...)` as the final step.

#### Phase 0: UAT Generation
1. **GENERATE UAT**: Call `repository_automation.execute(action='generate_user_story_and_uat', provider='{provider}', issue_number={issue_number})` to decompose the issue into a User Story and step-by-step UAT plan.
2. **DOCUMENT**: Save the UAT plan to `{project_path}/UAT_{issue_number}.md` for reference.

{phase1}

#### Phase 2: TDD Loop
Follow this loop until ALL tests pass:
1. **ANALYZE**: Read the issue requirements AND the generated UAT plan.
2. **TEST FIRST**: Write failing tests that define the expected behavior per UAT steps.
3. **IMPLEMENT**: Write minimal code to make tests pass.
4. **RUN TESTS**: Execute the test suite from `{project_path}` (e.g., `cd {project_path} && pytest`, `cd {project_path} && npm test`, etc.).
5. **FIX**: If tests fail, fix the code and re-run.
6. **REPEAT**: Loop steps 3-5 until ALL tests are GREEN.

#### Phase 3: Completion
1. **COMMIT**: Once green, commit with message "Issue #{issue_number}: {title}" using `git -C {project_path} commit`.
2. **PUSH**: Push the branch `{branch_name}` to the remote using `git -C {project_path} push origin {branch_name}`.
3. **COMMENT ON ISSUE** (CRITICAL - DO NOT SKIP):
   > [!WARNING]
   > You MUST include `provider='{provider}'` in the comment call. Without it, the comment will go to the WRONG platform!
   
   Post a detailed success comment to issue #{issue_number} using:
   ```
   repository_automation.execute(action='comment', provider='{provider}', issue_number={issue_number}, body='...')
   ```
   The comment MUST include:
   - **Branch pushed**: `{branch_name}`
   - **Files changed**: List the new/modified files.
   - **Tests added**: Summarize the test cases created.
   - **UAT verification**: Confirm which UAT steps are covered by tests.
   - **Manual Test Plan**: Include the generated "Manual Test Plan" so the human reviewer can verify the changes step-by-step.
4. **CLEANUP WORKTREE** (after push and comment are done):
   > Remove the worktree to free disk space. This does NOT delete the remote branch.
   ```
   git -C {base_clone_path or project_path} worktree remove {project_path} --force
   ```
   If this fails, it's safe to ignore — stale worktrees are cleaned up automatically.

### Success Criteria:
- UAT plan is generated and documented.
- New tests cover the issue requirements (mapped to UAT steps).
- Existing tests still pass (no regressions).
- Branch `{branch_name}` is pushed to `{repo_url}`.
- A success comment is posted back to the issue with implementation summary.

### Issue Context:
{truncate_output_middle_out(issue_text, max_chars=2000, head_ratio=0.3)}

BEGIN TDD IMPLEMENTATION NOW.
"""


def build_system_prompt_for_tdd() -> str:
    """Build the system prompt for TDD mode agents."""
    return """You are a senior software engineer running in TDD (Test-Driven Development) mode.
Your primary role is to implement code changes following strict TDD methodology.

CRITICAL RULES:
1. ALWAYS write tests FIRST before implementing any code
2. Run tests after EVERY code change
3. Fix failing tests immediately before proceeding
4. Commit only when ALL tests pass
5. Follow the project's existing code patterns and conventions
6. Push to the designated branch when complete

You have full access to code editing, command execution, and repository tools."""


def build_acknowledgment_comment(
    task_name: str,
    branch_name: str
) -> str:
    """Build the acknowledgment comment for build task trigger."""
    return f"""🚀 **Build Triggered**: A TDD build task has been initialized for this issue. Monitoring progress...

I've created a dedicated build task to implement this issue using TDD:

- **Task**: `{task_name}`
- **Branch**: `{branch_name}`
- **Profile**: multiagentdev (code mode)

The agent will:
1. Write tests for the requirements
2. Implement code to pass tests
3. Run TDD loop until green
4. Push to branch and report back

You'll receive an update when the implementation is complete or if assistance is needed.
"""


async def generate_implementation_code(
    issue_body: str, 
    context: str,
    agent: "Agent" = None
) -> Dict[str, str]:
    """
    Uses LLM to generate the actual file changes.
    Returns a dictionary of {file_path: file_content}.
    Uses the multiagentdev profile and context if available.
    """
    try:
        from python.helpers.call_llm import call_llm
        from python.models import get_chat_model
        from python.helpers import files
        
        # Load multiagentdev profile components
        try:
            profile_context = files.read_prompt_file("_context.md", [files.get_abs_path("agents", "multiagentdev")])
            orig_profile = agent.config.profile
            agent.config.profile = "multiagentdev"
            try:
                role_prompt = agent.read_prompt("agent.system.main.role.md")
            finally:
                agent.config.profile = orig_profile
            
            system_prompt = f"{profile_context}\n\n{role_prompt}\n\nYou are a senior developer. Output only valid JSON with file paths and contents."
            chat_model = get_chat_model("role", "multiagentdev")
            logger.info("[PHASE 3] Using multiagentdev profile for code generation")
        except Exception as e:
            logger.warning(f"multiagentdev profile load failed: {e}. Falling back to default model.")
            chat_config = agent.config.chat_model
            chat_model = get_chat_model(chat_config.provider, chat_config.name)
            system_prompt = "You are a senior developer. Output only valid JSON with file paths and contents."

        prompt = f"""
You are an expert engineer. Based on the issue description and codebase context, generate the necessary code changes.
Return ONLY a valid JSON object where keys are relative file paths and values are the NEW COMPLETE file contents.

## Issue Description:
{issue_body}

## Codebase Context Summary:
{truncate_output_middle_out(context, max_chars=8000, head_ratio=0.3)}

## REQUIRED OUTPUT FORMAT:
{{
  "path/to/file1.py": "content of file 1...",
  "path/to/file2.js": "content of file 2..."
}}
"""
        response = await call_llm(
            system=system_prompt,
            model=chat_model,
            message=prompt
        )
        
        from python.helpers import dirty_json
        return dirty_json.try_parse(str(response))
    except Exception as e:
        logger.error(f"Failed to generate implementation code: {e}")
        return {}


async def generate_user_story_and_uat(
    agent: "Agent",
    issue_number: int,
    body: str,
    title: str = ""
) -> str:
    """
    Decompose a task into a User Story and a step-by-step UAT plan.
    
    Args:
        agent: Agent instance for LLM access
        issue_number: The number of the issue being analyzed
        body: Issue body/task description
        title: Issue title
    
    Returns:
        User Story and UAT plan as markdown string
    """
    try:
        from python.helpers.call_llm import call_llm
        from python.models import get_chat_model
        
        chat_config = agent.config.utility_model
        chat_model = get_chat_model(chat_config.provider, chat_config.name)

        prompt = f"""
Decompose the following technical requirement or task into a standard User Story and a step-by-step UAT (User Acceptance Testing) plan.

## Requirement / Task:
{body}

## Output Format:
### User Story
**As a** [user role]
**I want** [capability]
**So that** [benefit/value]

### UAT Plan
1. [Step 1 description]
   * **Expected Result**: [Result]
2. [Step 2 description]
   ...

### Manual Test Plan (Step-by-Step)
Provide a full "human - point & click" plan for manual verification:
1. [Action 1: e.g. Open the UI...]
2. [Action 2: e.g. Click the button...]
3. [Action 3: e.g. Verify that...]

Ensure both the UAT plan and the Manual Test Plan are detailed enough for verification. 
Identify edge cases and include them in the steps if appropriate.
"""
        response = await call_llm(
            system="You are an expert product manager and QA engineer. Decompose complex tasks into clear user stories and testable plans.",
            model=chat_model,
            message=prompt
        )

        return str(response)
    except Exception as e:
        logger.error(f"[generate_user_story_and_uat] Error: {e}", exc_info=True)
        raise


def check_build_authorization(trigger_author: str) -> dict:
    """
    Check if a user is authorized to trigger builds.
    
    Args:
        trigger_author: Username of the user triggering the build
    
    Returns:
        Dict with 'authorized' (bool) and 'message' (str)
    """
    # Currently, any known safe user can trigger builds
    unauthorized_users = []
    
    if trigger_author in unauthorized_users:
        msg = f"ERROR: User '{trigger_author}' is not authorized to trigger builds."
        logger.warning(f"[trigger_build_task] {msg}")
        return {"authorized": False, "message": msg}
    
    logger.info(f"[trigger_build_task] Authorized trigger by user: {trigger_author}")
    return {"authorized": True, "message": "Authorized"}


def parse_title_from_issue_text(issue_text: str, default: str = "feature") -> str:
    """
    Extract title from issue text for branch naming.
    
    Args:
        issue_text: Full issue text
        default: Default title if extraction fails
    
    Returns:
        Extracted or default title
    """
    lines = issue_text.split("\n")
    if lines:
        first_line = lines[0]
        # Pattern like "#123: Title here"
        if ":" in first_line:
            return first_line.split(":", 1)[1].strip()[:50]
    return default


def construct_repo_url(
    provider: str,
    creds: Dict[str, Any],
    params: Dict[str, Any] = None
) -> str:
    """
    Construct repository URL from credentials.
    
    Args:
        provider: Provider type (github/forgejo)
        creds: Credentials dict
        params: Optional additional params
    
    Returns:
        Repository URL or empty string
    """
    params = params or {}
    repo_url = params.get("repo_url", "")
    
    if repo_url:
        return repo_url
    
    if provider == "forgejo":
        base_url = (creds.get('url') or "").rstrip("/")
        if base_url and creds.get('owner') and creds.get('repo'):
            return f"{base_url}/{creds['owner']}/{creds['repo']}"
    elif provider == "github":
        base_url = (params.get("github_url") or 
                   os.environ.get("GITHUB_URL") or 
                   "https://github.com").rstrip("/")
        if creds.get('owner') and creds.get('repo'):
            return f"{base_url}/{creds['owner']}/{creds['repo']}"
    
    return ""