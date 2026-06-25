from __future__ import annotations
"""
Project Path Enforcement Extension

Intercepts code execution attempts that try to create project directories
outside of the allowed usr/projects/ location.

This enforces the mise-en-place requirement that all projects must be
created using the setup_project tool, which places them in the correct location.

Container-Aware: Detects Docker vs local execution and enforces appropriate paths.
"""

import os
import re
from typing import Any
from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers import files, projects

import logging

logger = logging.getLogger("agix.project_path_enforcer")


def is_running_in_docker() -> bool:
    """
    Detect if we're running inside a Docker container.
    
    Returns:
        True if running in Docker, False otherwise
    """
    # Check for /.dockerenv file (most reliable)
    if os.path.exists("/.dockerenv"):
        return True
    
    # Check for /agix directory (AGIX container mount point)
    if os.path.exists("/agix") and os.path.isdir("/agix"):
        return True
    
    # Check cgroup for docker/container indicators
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            if "docker" in content or "containerd" in content or "lxc" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass
    
    return False


def get_projects_path() -> str:
    """
    Get the correct projects path based on execution environment.
    
    Returns:
        The absolute path to the projects directory
    """
    if is_running_in_docker():
        return "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects"
    else:
        return files.get_abs_path("usr/projects")


class ProjectPathEnforcer(Extension):
    """
    Extension that blocks code execution attempts to create project directories
    outside of usr/projects/.
    
    This ensures agents use the setup_project tool instead of manually creating
    directories in /tmp/, /projects/, or other locations.
    
    Container-aware: Detects Docker environment and enforces /agix/usr/projects/
    """
    
    # Patterns that indicate project directory creation
    MKDIR_PATTERNS = [
        # Shell mkdir commands - absolute paths
        r'mkdir\s+(?:-p\s+)?["\']?(/tmp/[^\s"\']+)',
        r'mkdir\s+(?:-p\s+)?["\']?(/projects/[^\s"\']+)',
        r'mkdir\s+(?:-p\s+)?["\']?(/var/[^\s"\']+)',
        r'mkdir\s+(?:-p\s+)?["\']?(/home/[^\s"\']+)',
        r'mkdir\s+(?:-p\s+)?["\']?(~/[^\s"\']+)',
        
        # Shell mkdir commands - relative paths that look like projects
        r'mkdir\s+(?:-p\s+)?["\']?([a-z]+-(?:app|api|project|service|website|dashboard|cli|tool)[^\s"\']*)',
        
        # Python os.makedirs / os.mkdir
        r'os\.makedirs?\s*\(\s*["\'](/tmp/[^"\']+)["\']',
        r'os\.makedirs?\s*\(\s*["\'](/projects/[^"\']+)["\']',
        r'os\.makedirs?\s*\(\s*["\'](/var/[^"\']+)["\']',
        r'os\.makedirs?\s*\(\s*["\'](/home/[^"\']+)["\']',
        
        # Python pathlib
        r'Path\s*\(\s*["\'](/tmp/[^"\']+)["\'].*\.mkdir',
        r'Path\s*\(\s*["\'](/projects/[^"\']+)["\'].*\.mkdir',
        
        # Node.js fs.mkdir
        r'fs\.mkdir(?:Sync)?\s*\(\s*["\'](/tmp/[^"\']+)["\']',
        r'fs\.mkdir(?:Sync)?\s*\(\s*["\'](/projects/[^"\']+)["\']',
        
        # cd + mkdir pattern (split commands)
        r'cd\s+(/tmp)\s*[;&|]+\s*mkdir',
        r'cd\s+(/projects)\s*[;&|]+\s*mkdir',
    ]
    
    # Package manager install commands that modify dependency state
    # These MUST only run inside project directories, never at framework root
    PKG_MANAGER_INSTALL_PATTERNS = [
        # npm — install/i/ci/add (but NOT run/start/test/exec)
        r'\bnpm\s+(?:install|i|ci|add)\b',
        # yarn — add/install
        r'\byarn\s+(?:add|install)\b',
        # pnpm — add/install/i
        r'\bpnpm\s+(?:add|install|i)\b',
        # bun — add/install/i
        r'\bbun\s+(?:add|install|i)\b',
        # pip/pip3 — install
        r'\bpip3?\s+install\b',
        # poetry — add/install
        r'\bpoetry\s+(?:add|install)\b',
        # cargo — add (Rust)
        r'\bcargo\s+add\b',
        # go — get/mod tidy
        r'\bgo\s+(?:get|mod\s+tidy)\b',
        # npx — scaffold commands that run npm install internally
        # create-next-app, create-vite, create-react-app, etc.
        r'\bnpx\s+(?:-y\s+)?create-\w+',
        # npx prisma — generates files and writes to CWD
        r'\bnpx\s+(?:-y\s+)?prisma\b',
    ]
    
    # Config files that MUST NOT be written to the framework root
    # These are package manager config files that agents might directly write via
    # Python open() or shell cat/echo, bypassing the CLI install guard.
    ROOT_CONFIG_FILES = [
        'package.json',
        'package-lock.json',
        'yarn.lock',
        'pnpm-lock.yaml',
        'bun.lockb',
        'requirements.txt',
        'Pipfile',
        'Pipfile.lock', 
        'poetry.lock',
        'pyproject.toml',
        'Cargo.toml',
        'Cargo.lock',
        'go.mod',
        'go.sum',
        'composer.json',
        'composer.lock',
        'Gemfile',
        'Gemfile.lock',
    ]
    
    # Patterns that look like project creation (not just temp files)
    PROJECT_INDICATORS = [
        r'(?:my-|new-|test-)?[a-z]+-(?:app|api|project|service|website|dashboard|cli|tool)',
        r'(?:flask|django|express|react|vue|angular|rust|go|python|node)-',
        r'-(?:backend|frontend|server|client)',
    ]
    
    # File creation patterns that indicate project setup
    FILE_CREATION_PATTERNS = [
        # touch/echo to create files in forbidden paths
        r'touch\s+["\']?(/tmp/[^\s"\']+/(?:package\.json|requirements\.txt|Cargo\.toml|README))',
        r'echo\s+.*>\s*["\']?(/tmp/[^\s"\']+/(?:package\.json|requirements\.txt|Cargo\.toml|README))',
        r'touch\s+["\']?(/projects/[^\s"\']+/(?:package\.json|requirements\.txt|Cargo\.toml|README))',
        r'echo\s+.*>\s*["\']?(/projects/[^\s"\']+/(?:package\.json|requirements\.txt|Cargo\.toml|README))',
    ]
    
    # CRITICAL: Git operations in /agix/ root MUST be blocked
    # Agent should only run git commands inside usr/projects/
    GIT_BLOCK_PATTERNS = [
        # git -C /agix/ <command>
        r'git\s+-C\s+["\\'']?(/agix/)["\\'']?\s+(?:checkout|commit|push|branch|merge|rebase)',
        # cd /agix/ && git <command>
        r'cd\s+["\\'']?(/agix/)["\\'']?\s*[;&|]+\s*git\s+(?:checkout|commit|push|branch|merge|rebase)',
        # Note: Bare git commands are handled by CWD check in _check_forbidden_git_ops
    ]
    
    def __init__(self, agent):
        super().__init__(agent)
        self._in_docker = is_running_in_docker()
        self._projects_path = get_projects_path()
        logger.info(
            f"ProjectPathEnforcer initialized: docker={self._in_docker}, "
            f"projects_path={self._projects_path}"
        )
    
    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs
    ):
        """
        Check if code execution is trying to create project directories
        outside of usr/projects/.
        
        Args:
            tool_args: Arguments passed to the tool
            tool_name: Name of the tool being executed
            **kwargs: Additional arguments
            
        Returns:
            None if allowed, or Response with error if blocked
        """
        # Only check code execution tools
        if tool_name not in ["code_execution_tool", "code_execution"]:
            return None
        
        if not tool_args:
            return None
        
        # Get the code being executed
        code = tool_args.get("code", "") or tool_args.get("runtime", "")
        if not code:
            return None
        
        # FAST PATH: If the agent's context already has a project set,
        # then code_execution_tool will run with CWD inside that project.
        # In this case, pkg manager ops (npm install, npx prisma, etc.) and
        # config file writes are safe — they'll land in the project dir.
        # We still check mkdir and git ops since those use absolute paths.
        context_project = projects.get_context_project_name(self.agent.context) if hasattr(self.agent, 'context') else None
        agent_has_project_cwd = bool(context_project)
        if agent_has_project_cwd:
            logger.debug(
                f"Agent has project context '{context_project}' — "
                f"skipping CWD-dependent checks (pkg manager, config writes)"
            )
        
        # Check for forbidden directory creation patterns (absolute paths — always check)
        blocked_path = self._check_forbidden_paths(code)
        if blocked_path:
            logger.warning(
                f"Blocked project creation at forbidden path: {blocked_path}"
            )
            return self._generate_blocked_response(blocked_path)
        
        # Check for file creation in forbidden paths (absolute paths — always check)
        blocked_file = self._check_forbidden_file_creation(code)
        if blocked_file:
            logger.warning(
                f"Blocked file creation at forbidden path: {blocked_file}"
            )
            return self._generate_blocked_response(blocked_file)
        
        # CRITICAL: Block git operations in /agix/ root (only in Docker)
        if self._in_docker:
            blocked_git = self._check_forbidden_git_ops(code)
            if blocked_git:
                # Lazy import for redaction (same pattern as line 450)
                try:
                    from python.helpers.git_guard import GitGuard as _GG
                    _redact = _GG.redact_secrets
                except ImportError:
                    _redact = str  # Fallback: log as-is if import fails
                logger.warning(
                    f"Blocked git operation targeting host repo: {_redact(blocked_git)}"
                )
                return self._generate_git_blocked_response(blocked_git)
        
        # CWD-dependent checks: SKIP if agent already has a project context
        # (code_execution_tool's get_cwd() will set the shell CWD to the project dir)
        if not agent_has_project_cwd:
            # CRITICAL: Block package manager installs in framework root
            blocked_pkg = self._check_forbidden_pkg_manager_ops(code)
            if blocked_pkg:
                logger.warning(
                    f"Blocked package manager operation in framework root: {blocked_pkg}"
                )
                return self._generate_pkg_manager_blocked_response(blocked_pkg)
            
            # CRITICAL: Block direct file writes to config files at framework root
            # (e.g. Python open('package.json', 'w') or cat > package.json)
            blocked_config = self._check_forbidden_root_config_writes(code)
            if blocked_config:
                logger.warning(
                    f"Blocked config file write in framework root: {blocked_config}"
                )
                return self._generate_config_write_blocked_response(blocked_config)
        
        return None
    
    def _check_forbidden_paths(self, code: str) -> str | None:
        """
        Check if code contains forbidden directory creation patterns.
        
        Args:
            code: The code being executed
            
        Returns:
            The forbidden path if found, None otherwise
        """
        for pattern in self.MKDIR_PATTERNS:
            matches = re.findall(pattern, code, re.IGNORECASE)
            for path in matches:
                # Check if this looks like a project directory
                if self._looks_like_project(path):
                    return path
        
        return None
    
    def _check_forbidden_file_creation(self, code: str) -> str | None:
        """
        Check if code contains forbidden file creation patterns.
        
        Args:
            code: The code being executed
            
        Returns:
            The forbidden path if found, None otherwise
        """
        for pattern in self.FILE_CREATION_PATTERNS:
            matches = re.findall(pattern, code, re.IGNORECASE)
            if matches:
                return matches[0]
        
        return None
    
    def _looks_like_project(self, path: str) -> bool:
        """
        Determine if a path looks like a project directory.
        
        Args:
            path: The path to check
            
        Returns:
            True if it looks like a project directory
        """
        # Always block /projects/ at root (common mistake)
        if path.startswith("/projects/"):
            return True
        
        # Check for project-like names in /tmp/
        if path.startswith("/tmp/"):
            for indicator in self.PROJECT_INDICATORS:
                if re.search(indicator, path, re.IGNORECASE):
                    return True
            
            # Also block if it has common project structure indicators
            project_structure_indicators = [
                "src/", "lib/", "app/", "api/", "server/", "client/",
                "package.json", "requirements.txt", "Cargo.toml",
                ".git", ".mise.toml", "README"
            ]
            for indicator in project_structure_indicators:
                if indicator in path:
                    return True
        
        # Check for relative paths that look like projects
        if not path.startswith("/"):
            for indicator in self.PROJECT_INDICATORS:
                if re.search(indicator, path, re.IGNORECASE):
                    return True
        
        return False
    
    def _generate_blocked_response(self, blocked_path: str) -> Response:
        """
        Generate a helpful error response when project creation is blocked.
        
        Args:
            blocked_path: The path that was blocked
            
        Returns:
            Response with error message and guidance
        """
        # Get the correct projects directory based on environment
        if self._in_docker:
            correct_path = "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects"
            env_note = "You are running in a Docker container."
        else:
            correct_path = self._projects_path
            env_note = "You are running locally."
        
        message = f"""⚠️ **Project Directory Creation Blocked**

You attempted to create a project directory at: `{blocked_path}`

**This is not allowed.** All projects must be created using the `setup_project` tool.

## Environment Detection
{env_note}

## Why This Was Blocked

Projects must be created in the AGIX managed directory:
- **Correct location:** `{correct_path}/<project-name>/`
- **Your attempted location:** `{blocked_path}`

## How to Fix This

Use the `setup_project` tool instead:

```yaml
setup_project:
  name: your-project-name
  description: Your project description
  framework: python  # or nodejs, rust, go, etc.
```

The `setup_project` tool will:
1. Create the project at the correct location (`{correct_path}/<name>/`)
2. Initialize git repository
3. Create .mise.toml for environment management
4. Set up proper project structure

## DO NOT:
- Use `mkdir` to create project directories
- Use `os.makedirs()` to create project directories
- Create projects in `/tmp/`, `/projects/`, or other locations
- Create projects in the current directory without using setup_project

## CORRECT PATHS:
- **In Docker:** `/agix/usr/projects/<name>/`
- **Locally:** `usr/projects/<name>/` (relative to AGIX root)

**Please use the `setup_project` tool to create your project.**
"""
        


        return Response(
            message=message,
            break_loop=False,
        )
    
    def _check_forbidden_git_ops(self, code: str) -> str | None:
        """
        Check if code contains forbidden git operations targeting protected repos.
        
        CRITICAL: This blocks git commands that would modify the host repository.
        Uses GitGuard for deterministic protection against .git traversal attacks.
        
        Protection works EVERYWHERE (not just Docker) because git traversal
        can affect any parent .git, not just the Docker mount.
        
        Args:
            code: The code being executed
            
        Returns:
            The forbidden operation description if blocked, None if allowed
        """
        # Fast path: no git commands at all
        if not re.search(r'\bgit\b', code, re.IGNORECASE):
            return None
        
        logger.debug(f"[GIT_CHECK] Checking code for git ops: {code[:200]}...")
        
        # Import GitGuard for protection
        try:
            from python.helpers.git_guard import GitGuard, GitGuardError
        except ImportError:
            logger.error("[GIT_CHECK] Failed to import GitGuard, falling back to basic checks")
            # Fall back to basic pattern matching
            return self._check_forbidden_git_ops_legacy(code)
        
        # Extract git commands from code
        git_cmd_pattern = r'\bgit\s+([\w\-]+(?:\s+[^\n;|&]+)?)'
        git_matches = re.findall(git_cmd_pattern, code, re.IGNORECASE)
        logger.debug(f"[GIT_CHECK] Found git commands: {git_matches}")
        
        # Determine working directory from code
        # Look for cd commands to determine where git will run
        working_dir = self._extract_working_dir_from_code(code)
        logger.debug(f"[GIT_CHECK] Detected working directory: {working_dir}")
        
        for git_cmd in git_matches:
            try:
                # Use GitGuard to validate each command
                is_allowed, warning = GitGuard.validate_git_operation(
                    working_dir,
                    git_cmd.strip(),
                    raise_on_block=False
                )
                
                if not is_allowed:
                    logger.warning(f"[GIT_CHECK] BLOCKING via GitGuard: {GitGuard.redact_secrets(warning)}")
                    return warning
                elif warning:
                    # Soft warning - log but allow
                    logger.warning(f"[GIT_CHECK] Soft warning: {GitGuard.redact_secrets(warning)}")
                    
            except Exception as e:
                logger.error(f"[GIT_CHECK] GitGuard error: {e}")
                # On error, use legacy check as fallback
                legacy_result = self._check_forbidden_git_ops_legacy(code)
                if legacy_result:
                    return legacy_result
        
        logger.debug("[GIT_CHECK] All git commands passed GitGuard checks")
        return None
    
    def _extract_working_dir_from_code(self, code: str) -> str:
        """
        Extract the working directory from code by parsing cd commands.
        
        Handles BOTH absolute and relative cd paths by tracking CWD state
        across the command chain, just like a real shell does.
        
        RCA-270: When the agent has _active_project_dir set, use it as the
        default CWD instead of the Docker root (/agix). This prevents
        GitGuard false-positives for agents running git inside their project.
        
        Examples:
            cd /foo && cd bar       -> /foo/bar
            cd /foo && cd ../baz    -> /baz
            cd tmp/push_staging/    -> <default>/tmp/push_staging
        
        Returns the detected directory or default based on Docker status.
        """
        # RCA-270: Check agent's _active_project_dir first
        agent_project_dir = ""
        if hasattr(self, 'agent') and hasattr(self.agent, 'data'):
            agent_project_dir = self.agent.data.get("_active_project_dir", "")
        
        # Default working directory — prioritize agent project dir
        if agent_project_dir:
            current_dir = agent_project_dir
        elif self._in_docker:
            current_dir = "/agix"
        else:
            current_dir = os.getcwd()
        
        # Match ALL cd commands (absolute AND relative)
        cd_pattern = r'cd\s+([^\s;&|]+)'
        cd_matches = re.findall(cd_pattern, code, re.IGNORECASE)
        
        if not cd_matches:
            return current_dir
        
        for cd_target in cd_matches:
            cd_target = cd_target.rstrip("/")
            if not cd_target:
                continue
            if cd_target.startswith("/"):
                # Absolute path resets CWD entirely
                current_dir = cd_target
            else:
                # Relative path resolves against current CWD
                current_dir = os.path.normpath(os.path.join(current_dir, cd_target))
        
        return current_dir
    
    def _check_forbidden_git_ops_legacy(self, code: str) -> str | None:
        """
        Legacy git operation check (fallback if GitGuard unavailable).
        """
        DESTRUCTIVE_GIT_OPS = [
            'checkout', 'commit', 'push', 'merge', 'rebase', 'reset',
            'branch', 'init', 'pull', 'cherry-pick', 'revert',
            'stash', 'tag', 'am', 'apply', 'remote'
        ]
        
        # Check for explicit /agix patterns
        for pattern in self.GIT_BLOCK_PATTERNS:
            matches = re.findall(pattern, code, re.IGNORECASE)
            for path in matches:
                if "/tmp" in path:
                    continue
                return f"git targeting {path} (explicit path)"
        
        return None

    def _check_forbidden_pkg_manager_ops(self, code: str) -> str | None:
        """
        Check if code contains package manager install commands that would
        execute in the framework root rather than inside a project directory.
        
        SECURITY: Agents must only install dependencies inside their project
        directory (/agix/usr/projects/<name>/). Running `npm install` at
        /agix/ pollutes the host repo's package.json.
        
        Uses PER-STATEMENT CWD tracking: splits code into individual statements,
        tracks CWD state across them (like a real shell), and checks EACH
        npm/yarn/pip command independently against the CWD at that point.
        
        Args:
            code: The code being executed
            
        Returns:
            Description of blocked operation if blocked, None if allowed
        """
        # Fast path: no package manager commands at all
        pkg_mgr_keywords = ['npm', 'yarn', 'pnpm', 'bun', 'pip', 'pip3', 'poetry', 'cargo', 'go', 'npx']
        code_lower = code.lower()
        if not any(kw in code_lower for kw in pkg_mgr_keywords):
            return None
        
        # Per-LINE analysis with CWD tracking.
        # 
        # Rules:
        # 1. Within a line, `&&` and `;` create a chain where CWD carries
        #    (e.g., `cd /project && npm install` → cd sets CWD for npm)
        # 2. A standalone `cd /dir` on its own line carries to the next line
        #    (like a real shell script: `cd /dir\nnpm install`)
        # 3. If a line contains BOTH a cd AND an install command (chained),
        #    the CWD does NOT carry to the next line — it was consumed
        # 
        # This prevents the exploit: `cd /project && npm install X\nnpm install Y`
        # where Y would incorrectly inherit the cd from line 1.
        
        projects_path = self._projects_path
        persistent_cwd = None  # CWD that carries across lines (from standalone cd)
        
        cd_pattern = re.compile(r'cd\s+["\']?(/[^\s"\';|&]+)["\']?', re.IGNORECASE)
        
        lines = code.split('\n')
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            
            # Split line into chain segments (&&, ;)
            segments = re.split(r'\s*(?:&&|;)\s*', line_stripped)
            
            # Start each line with the persistent CWD (from standalone cd on prev line)
            line_cwd = persistent_cwd
            line_has_install = False
            line_has_cd = False
            
            for seg in segments:
                seg = seg.strip()
                if not seg:
                    continue
                
                # Update CWD if this segment has a cd command
                cd_match = cd_pattern.search(seg)
                if cd_match:
                    line_cwd = cd_match.group(1)
                    line_has_cd = True
                
                # Check if this segment contains a package manager install command
                matched_cmd = None
                for pattern in self.PKG_MANAGER_INSTALL_PATTERNS:
                    match = re.search(pattern, seg, re.IGNORECASE)
                    if match:
                        matched_cmd = match.group(0)
                        break
                
                if not matched_cmd:
                    continue  # No install command in this segment
                
                line_has_install = True
                
                # Allow global installs (npm install -g, pip install --user, etc.)
                global_flags = ['-g', '--global', '--user', '--system']
                is_global = False
                for flag in global_flags:
                    if re.search(
                        rf'\b(?:npm|yarn|pnpm|bun)\s+(?:install|i|add)\s+.*{re.escape(flag)}\b',
                        seg, re.IGNORECASE
                    ):
                        is_global = True
                        break
                    if re.search(
                        rf'\b(?:npm|yarn|pnpm|bun)\s+(?:install|i|add)\s+{re.escape(flag)}\b',
                        seg, re.IGNORECASE
                    ):
                        is_global = True
                        break
                if is_global:
                    continue  # Global installs are always allowed
                
                # Check the CWD at this point in the chain
                if line_cwd and line_cwd.startswith(projects_path):
                    continue  # CWD is inside a project — ALLOWED
                
                # CWD is NOT inside a project — BLOCKED
                return f"{matched_cmd} (would execute in framework root, not inside a project directory)"
            
            # Update persistent CWD for next line:
            # Only carry cd to next line if this line was a standalone cd
            # (no install command consumed it in a chain)
            if line_has_cd and not line_has_install:
                persistent_cwd = line_cwd
            elif line_has_cd and line_has_install:
                # cd was consumed by the chain — reset for next line
                persistent_cwd = None
        
        # All install commands (if any) are inside project directories
        return None
    
    def _split_into_statements(self, code: str) -> list[str]:
        """
        Split a code block into individual statements for per-statement analysis.
        
        Handles:
        - Newlines: each line is a separate statement group
        - `&&` chains: cd /dir && npm install → two statements, CWD carries over
        - `;` chains: cd /dir; npm install → two statements, CWD carries over
        - `||` chains: treated as separate statements
        
        Returns:
            List of individual statement strings
        """
        statements = []
        # First split on newlines
        lines = code.split('\n')
        for line in lines:
            # Split each line on && and ; (shell statement separators)
            # This preserves order so CWD tracking works
            parts = re.split(r'\s*(?:&&|;)\s*', line)
            statements.extend(parts)
        return statements
    
    def _check_forbidden_root_config_writes(self, code: str) -> str | None:
        """
        Check if code directly writes config files (package.json, requirements.txt, etc.)
        that would land in the framework root rather than a project directory.
        
        Attack vectors caught:
        - Python: open("package.json", "w") — relative path resolves to /agix/
        - Shell: cat > package.json << 'EOF'
        - Shell: echo '{...}' > package.json
        - Shell: ... | tee package.json
        
        ALLOWED when:
        - Absolute path inside projects dir (open('/agix/usr/projects/x/package.json', 'w'))
        - cd to project dir precedes the write (cd /agix/usr/projects/x && cat > ...)
        - os.chdir() to project dir precedes the write
        - Reading the file (open('package.json', 'r') or cat package.json without redirect)
        
        Returns:
            Description of blocked write if blocked, None if allowed 
        """
        projects_path = self._projects_path
        
        # Determine effective CWD from cd/chdir commands
        effective_dir = self._get_effective_cwd_for_writes(code)
        if effective_dir and effective_dir.startswith(projects_path):
            # CWD is inside a project — all relative writes are safe
            return None
        
        # Check each config file for write patterns
        for config_file in self.ROOT_CONFIG_FILES:
            # Escape dots in filename for regex
            escaped = re.escape(config_file)
            
            # --- Python open() writes ---
            # Match: open("package.json", "w"), open('package.json', 'w'), open("package.json", "a")
            # But NOT: open("package.json", "r") or open("package.json") (read mode)
            # Also NOT: open("/agix/usr/projects/x/package.json", "w")
            python_write = re.search(
                rf'open\s*\(\s*["\'](?!.*{re.escape(projects_path)}){escaped}["\']\s*,\s*["\'][wa]',
                code
            )
            if python_write:
                return f"Python write to '{config_file}' in framework root"
            
            # --- Shell redirect writes (>, >>) ---
            # Match: cat > package.json, echo '...' > package.json, etc.
            # But NOT: cat > /agix/usr/projects/x/package.json
            shell_redirect = re.search(
                rf'>\s*(?!\S*{re.escape(projects_path)}){escaped}(?:\s|$|\'|"|\\n)',
                code
            )
            if shell_redirect:
                return f"Shell redirect write to '{config_file}' in framework root"
            
            # --- Tee writes ---
            # Match: ... | tee package.json
            # But NOT: ... | tee /agix/usr/projects/x/package.json  
            tee_write = re.search(
                rf'tee\s+(?!\S*{re.escape(projects_path)}){escaped}(?:\s|$)',
                code
            )
            if tee_write:
                return f"Tee write to '{config_file}' in framework root"
        
        return None
    
    def _get_effective_cwd_for_writes(self, code: str) -> str | None:
        """
        Determine the effective CWD from cd/os.chdir commands in the code.
        Used to determine if relative file writes would land in a project dir.
        
        Returns:
            The effective directory path, or None for framework root default
        """
        # Check for shell cd commands
        cd_pattern = r'cd\s+["\']?(/[^\s"\';|&]+)["\']?'
        cd_matches = re.findall(cd_pattern, code, re.IGNORECASE)
        
        # Check for Python os.chdir
        chdir_pattern = r'os\.chdir\s*\(\s*["\'](/[^"\']+)["\']\s*\)'
        chdir_matches = re.findall(chdir_pattern, code)
        
        all_dirs = cd_matches + chdir_matches
        if all_dirs:
            return all_dirs[-1].rstrip()  # Use the last directory change
        
        return None
    
    def _generate_config_write_blocked_response(self, blocked_op: str) -> Response:
        """
        Generate a helpful error response when config file writes are blocked.
        """
        projects_path = self._projects_path
        
        message = f"""⚠️ **Config File Write Blocked — Framework Root Protection**

You attempted: `{blocked_op}`

**This is BLOCKED** because writing config files (package.json, requirements.txt, etc.)
to the framework root pollutes the host repository.

## How to Fix

Use **absolute paths** inside your project directory:

```python
# ✅ CORRECT — absolute path to project
with open("{projects_path}/<your-project>/package.json", "w") as f:
    f.write(json.dumps(pkg_data, indent=2))
```

```bash
# ✅ CORRECT — cd to project first
cd {projects_path}/<your-project> && cat > package.json << 'EOF'
{{...}}
EOF
```

```python
# ❌ WRONG — relative path writes to framework root
with open("package.json", "w") as f:  # Goes to /agix/package.json!
    ...
```

Always use absolute paths when writing config files, or `cd` to your project directory first.
"""


        return Response(
            message=message,
            break_loop=False,
        )
    
    def _generate_pkg_manager_blocked_response(self, blocked_op: str) -> Response:
        """
        Generate a helpful error response when package manager ops are blocked.
        """
        projects_path = self._projects_path
        
        message = f"""⚠️ **Package Manager Operation Blocked — Framework Root Protection**

You attempted to run: `{blocked_op}`

**This is BLOCKED** because the command would install dependencies into the
framework root directory, polluting the host repository's package.json/requirements.txt.

## Why This Was Blocked

Package manager install commands must run inside your project directory:
- **FORBIDDEN:** Running `npm install` in `/agix/`
- **ALLOWED:** Running `npm install` in `{projects_path}/<your-project>/`

## How to Fix This

Always `cd` into your project directory before installing packages:

```bash
# ✅ CORRECT — install inside project directory
cd {projects_path}/<your-project-name>
npm install clsx tailwind-merge

# ❌ WRONG — installs into framework root
npm install clsx tailwind-merge
```

## DO NOT:
- Run `npm install` without first `cd`-ing to your project
- Run `pip install` without activating your project's venv
- Run `yarn add` at the framework root level

**Always navigate to your project directory first, then install dependencies.**
"""
        


        return Response(
            message=message,
            break_loop=False,
        )

    
    def _generate_git_blocked_response(self, blocked_path: str) -> Response:
        """
        Generate a helpful error response when git operations in /agix/ are blocked.

        ITR-45 F-7: Escalation logic — tracks consecutive blocks via
        _git_guard_block_count in agent.data. Blocks 1-2 get a standard
        message (with git_publish reference). Block 3+ gets an ESCALATED
        message with break_loop=True and explicit git_publish tool usage.

        Args:
            blocked_path: The path that was blocked

        Returns:
            Response with error message and guidance (escalated after 3 blocks)
        """
        # ── Increment block counter ──────────────────────────────────
        count = self.agent.data.get("_git_guard_block_count", 0) + 1
        self.agent.data["_git_guard_block_count"] = count

        if count >= 3:
            # ── ESCALATED message (3+ blocks) ────────────────────────
            message = f"""🚨 **STOP — GitGuard Has Blocked You {count} Times**

You have been blocked **{count} consecutive times** trying to run git commands
targeting: `{blocked_path}`

**Your previous approaches are WRONG. STOP trying them.**

## ✅ THE SOLUTION: Use the `git_publish` Tool

The `git_publish` tool safely pushes code to a remote repository using an
isolated staging directory. It bypasses GitGuard because the staging clone
has its own `.git` directory.

**Use this tool call NOW:**
```json
{{
  "tool_name": "git_publish",
  "tool_args": {{
    "repo_url": "https://github.com/owner/repo.git",
    "project_dir": "/agix/usr/projects/<project-name>",
    "commit_message": "Deploy project",
    "branch": "main"
  }}
}}
```

## ❌ STOP Doing These (They Will ALWAYS Be Blocked):
- `git init` / `git remote add` / `git push` in the project directory
- `git -C /agix/ <command>`
- Creating staging directories manually
- Any raw git commands targeting `/agix/`

**Use `git_publish` — it is the ONLY safe way to push code.**
"""
            logger.warning(
                f"[GITGUARD_ESCALATION] Agent #{self.agent.number} blocked "
                f"{count} times — escalating with break_loop=True"
            )
            return Response(
                message=message,
                break_loop=True,
            )

        # ── Standard message (blocks 1-2) ────────────────────────────
        message = f"""⚠️ **Git Operation Blocked - Host Repository Protection**

You attempted to run git commands targeting: `{blocked_path}`

**This is BLOCKED** because `/agix/` is mounted from the host filesystem.
Any git operations there would modify the host repository directly!

## Why This Was Blocked

Git operations must be isolated to project directories:
- **FORBIDDEN:** `/agix/` (host repo)
- **ALLOWED:** `/agix/usr/projects/<project-name>/`

## ✅ Recommended: Use the `git_publish` Tool

The **`git_publish`** tool safely handles git push operations using an
isolated staging directory. Use it instead of raw git commands:

```json
{{
  "tool_name": "git_publish",
  "tool_args": {{
    "repo_url": "https://github.com/owner/repo.git",
    "project_dir": "/agix/usr/projects/<project-name>",
    "commit_message": "Deploy project",
    "branch": "main"
  }}
}}
```

## Alternative: Manual Workflow

1. Use `setup_project` to create an isolated project workspace
2. Clone the target repository INTO that project directory
3. Run all git operations within that project

## DO NOT:
- Run `git checkout -b` in `/agix/`
- Run `git commit` or `git push` in `/agix/`
- Use `git -C /agix/ <command>`

**All git operations must happen inside `/agix/usr/projects/`**
"""

        return Response(
            message=message,
            break_loop=False,
        )

