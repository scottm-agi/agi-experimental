from __future__ import annotations
import os
import asyncio
import re
from typing import Optional

class IsolatedToolExecutor:
    """
    Ensures tool execution is performed within an isolated environment.
    Supports MISE (https://mise.jdx.dev/) for environment management.
    """
    
    ENVIRONMENT_CONFIG_FILES = [
        ".mise.toml",
        ".python-version",
        ".node-version",
        ".ruby-version",
        "package.json",
        "requirements.txt",
        "pyproject.toml"
    ]
    
    def __init__(self, project_path: Optional[str] = None):
        """
        Initialize the executor.
        
        Args:
            project_path: Absolute path to the project directory.
        """
        self.project_path = project_path

    async def should_use_mise(self) -> bool:
        """Check if mise should be used for command execution."""
        # Check if mise is installed first
        if not await self._is_mise_installed():
            return False
            
        if not self.project_path or not os.path.isdir(self.project_path):
            return False
            
        # Search upwards for environment config files
        current = os.path.abspath(self.project_path)
        while True:
            for config_file in self.ENVIRONMENT_CONFIG_FILES:
                if os.path.exists(os.path.join(current, config_file)):
                    return True
            
            parent = os.path.dirname(current)
            if parent == current: # Reached root
                break
            current = parent
            
        return False

    async def _is_mise_installed(self) -> bool:
        """Check if 'mise' executable is available in the system."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "mise", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    SHELL_BUILTINS = {"cd", "alias", "export", "source", ".", "exit", "history", "read", "type", "unset", "set"}

    # Git commands that modify repository state - BLOCKED in /agix/ or /agix/ root
    GIT_DANGEROUS_PATTERNS = [
        "git checkout",        # Branch switching
        "git switch",          # Branch switching (newer syntax)
        "git commit",          # Commits
        "git push",            # Pushing changes
        "git branch -",        # Branch deletion/creation flags
        "git merge",           # Merging
        "git rebase",          # Rebasing  
        "git reset",           # Resetting state
        "git revert",          # Reverting commits
        "git cherry-pick",     # Cherry-picking
        "git stash",           # Stashing changes
        "git pull",            # Pulling (can cause merges)
        "git fetch --all",     # Could be followed by checkout
        "git init",            # Repository initialization
        "git clone",           # Cloning into current dir
    ]

    # Commands that write files — used by _is_tmp_write to check system /tmp/ targets
    TMP_WRITE_COMMANDS = {
        "mkdir", "cp", "mv", "rsync", "tee", "git clone", "git init",
        "touch", "install", "tar", "unzip", "wget", "curl -o", "curl -O",
    }

    # Paths that are safe project sandboxes — /tmp/ under these is project-local, not system /tmp/
    _SAFE_PATH_PREFIXES = (
        "/agix/usr/projects/",
        "/agix/tmp/",
    )

    def _is_system_tmp_path(self, path_str: str) -> bool:
        """Check if a path refers to system /tmp/ vs project-local tmp/.
        
        Returns True only for system /tmp/ paths like:
          /tmp/push_staging  → True (system /tmp/)
          /tmp               → True
          
        Returns False for project-local paths:
          /agix/usr/projects/.../tmp/staging → False (project-local)
          tmp/push_staging                     → False (relative, project-local)
          
        5-Why RCA (Iteration 139): The old check used '/tmp/' as a substring
        which matched project-local absolute paths like
        /agix/usr/projects/mainstreet_review_123/docs/design-mockups/
        """
        path_str = path_str.strip()
        # Relative paths containing tmp/ are always project-local
        if not path_str.startswith("/"):
            return False
        # Absolute paths under safe project prefixes are project-local
        for prefix in self._SAFE_PATH_PREFIXES:
            if path_str.startswith(prefix):
                return False
        # Only match absolute /tmp/ or /tmp (system tmp)
        return path_str.startswith("/tmp/") or path_str == "/tmp"
    
    def _extract_path_args(self, subcmd: str) -> list:
        """Extract path-like arguments from a shell sub-command."""
        words = subcmd.split()
        paths = []
        for word in words:
            # Skip flags
            if word.startswith("-"):
                continue
            # Skip URLs
            if "://" in word:
                continue
            # Keep things that look like paths
            if "/" in word or word.startswith("."):
                paths.append(word)
        return paths

    def _is_tmp_write(self, command: str) -> bool:
        """
        Check if a command writes to system /tmp/ directory.
        
        SECURITY: Agents must use project-local tmp/ for all staging operations.
        System /tmp/ is outside the project sandbox and is FORBIDDEN for writes.
        Read operations (cat, ls, head, tail, grep, find) are allowed.
        
        5-Why RCA (Iteration 139): Fixed false positive where project-local
        paths like /agix/usr/projects/.../docs/design-mockups/ were blocked
        because the guard matched '/tmp/' as a substring anywhere in the path.
        Now uses _is_system_tmp_path() to distinguish system vs project-local.
        
        Returns True if command should be BLOCKED.
        """
        if "/tmp/" not in command and "/tmp " not in command:
            return False
        
        # Split by shell operators to check each sub-command
        subcmds = re.split(r'[;&|]', command)
        
        # Read-only commands that are always safe
        read_only_cmds = {"cat", "ls", "head", "tail", "grep", "find", "file",
                          "wc", "stat", "du", "df", "less", "more", "strings",
                          "hexdump", "xxd", "diff", "echo", "printf"}
        
        for subcmd in subcmds:
            subcmd = subcmd.strip()
            if not subcmd:
                continue
            
            # Check for shell redirect to system /tmp/
            redirect_match = re.search(r'>\s*(/\S+)', subcmd)
            if redirect_match and self._is_system_tmp_path(redirect_match.group(1)):
                return True
            
            # Check for tee writing to system /tmp/
            if "tee" in subcmd:
                for path in self._extract_path_args(subcmd):
                    if self._is_system_tmp_path(path):
                        return True
            
            # Check for cd /tmp (navigating to system /tmp/ to do work)
            cd_match = re.match(r'cd\s+(\S+)', subcmd)
            if cd_match and self._is_system_tmp_path(cd_match.group(1)):
                return True
                
            # Get the base command (first word)
            words = subcmd.split()
            if not words:
                continue
            base_cmd = words[0].lower()
            
            # Skip read-only commands
            if base_cmd in read_only_cmds:
                continue
            
            # Check if any path argument targets system /tmp/
            if base_cmd in {"mkdir", "cp", "mv", "rsync", "install", "touch"}:
                for path in self._extract_path_args(subcmd):
                    if self._is_system_tmp_path(path):
                        return True
            
            # Check write commands with system /tmp/ target
            for write_cmd in self.TMP_WRITE_COMMANDS:
                if write_cmd in subcmd.lower():
                    for path in self._extract_path_args(subcmd):
                        if self._is_system_tmp_path(path):
                            return True
        
        return False
    
    def _is_dangerous_git_in_root(self, command: str) -> bool:
        """
        Check if command is a dangerous git operation executing in /agix/ or /agix/ root.
        
        SECURITY: The /agix/ and /agix/ directories are volume mounts to the host repository.
        TDD agents must NEVER modify git state here - they should only work
        in /agix/usr/projects/, /agix/usr/projects/ or /tmp/.

        Uses GitGuard for deterministic protection against .git traversal attacks.
        
        Returns True if command should be BLOCKED.
        """
        if not self.project_path:
            return False
            
        # Use GitGuard for deterministic protection
        try:
            from python.helpers.git_guard import GitGuard
            
            # Extract git commands from code
            # Note: We split by ; and | to handle multiple commands in one string
            cmds = re.split(r'[;&|]', command)
            for cmd in cmds:
                cmd = cmd.strip()
                if not cmd.lower().startswith("git"):
                    continue
                
                # Strip 'git ' prefix for validation
                git_args = cmd[3:].strip()
                
                is_allowed, warning = GitGuard.validate_git_operation(
                    self.project_path,
                    git_args,
                    raise_on_block=False
                )
                
                if not is_allowed:
                    from python.helpers.print_style import PrintStyle
                    PrintStyle.error(f"[GIT ISOLATION] Blocked dangerous git command via GitGuard: {cmd}")
                    return True
            
            return False
        except ImportError:
            # Fallback to legacy check if GitGuard is not available
            # (Warning: simplified checks in fallback)
            normalized_path = os.path.normpath(self.project_path)
            safe_paths = ["/agix/usr/projects", "/agix/tmp", "/tmp"]
            
            is_in_root = (normalized_path == "/agix" or normalized_path.startswith("/agix/"))
            is_in_safe_zone = any(normalized_path.startswith(safe) for safe in safe_paths)
            
            if is_in_root and not is_in_safe_zone:
                cmd_lower = command.lower().strip()
                for pattern in self.GIT_DANGEROUS_PATTERNS:
                    if pattern.lower() in cmd_lower:
                        return True
            return False

    # ── RCA-266: Auto-inject .env.local vars into shell environment ──
    # Root cause: code_execution_tool runs commands in a subprocess that
    # doesn't have .env.local vars. CLI tools like Prisma that read env vars
    # directly (not from .env.local) fail with "Environment variable not found".
    # Fix: Prefix commands with `set -a && source .env.local && set +a` when
    # .env.local exists in the project directory.

    def _build_env_source_prefix(self) -> str:
        """Build a shell prefix that sources .env.local if it exists.

        Uses `set -a` (allexport) so that all variables defined in .env.local
        are automatically exported to child processes (Prisma, Next.js CLI, etc.).

        Returns:
            Shell prefix string like 'set -a && source .env.local && set +a && '
            or empty string if no .env.local exists.
        """
        if not self.project_path or not os.path.isdir(self.project_path):
            return ""
        
        env_local = os.path.join(self.project_path, ".env.local")
        if not os.path.isfile(env_local):
            return ""
        
        # Use set -a to auto-export, source the file, then set +a to stop
        return f"set -a && source {env_local} && set +a && "

    async def wrap_command(self, command: str) -> str:
        """
        Wrap a command with environment isolation if needed.
        Also enforces security checks to prevent host repository pollution.
        
        Example: "python script.py" -> "mise exec -- python script.py"
        """
        # SANDBOX CHECK: Block writes to system /tmp/ — use project-local tmp/ instead
        if self._is_tmp_write(command):
            from python.helpers.print_style import PrintStyle
            PrintStyle.error(f"[SANDBOX BLOCK] Blocked write to system /tmp/: {command[:80]}")
            # ITR-34 RCA: Include the concrete project path so agents can self-correct
            # immediately instead of looping on /tmp/ variants. The old message said
            # "use project-local tmp/" but never told the agent WHERE that was.
            if self.project_path:
                project_tmp = f"{self.project_path}/tmp/"
                blocked_msg = (
                    f"echo '[SANDBOX BLOCK] Writing to system /tmp/ is FORBIDDEN. "
                    f"Use your project tmp directory instead: {project_tmp} — "
                    f"Run: mkdir -p {project_tmp} then rewrite your command to use {project_tmp} "
                    f"instead of /tmp/. "
                    f"Blocked command: {command[:60]}...'"
                )
            else:
                blocked_msg = (
                    f"echo '[SANDBOX BLOCK] Writing to system /tmp/ is FORBIDDEN. "
                    f"Use project-local tmp/ directory instead (e.g., tmp/push_staging/). "
                    f"Blocked command: {command[:60]}...'"
                )
            return blocked_msg

        # SECURITY CHECK: Block dangerous git commands in /agix/ root
        if self._is_dangerous_git_in_root(command):
            # Return a safe echo command that explains the block
            blocked_msg = (
                f"echo '[SECURITY BLOCK] Git state-changing commands are forbidden in /agix/ root or its subdirectories without .git isolation. "
                f"Work in /agix/usr/projects/ instead. Blocked command: {command[:50]}...'"
            )
            return blocked_msg

        # ── RCA-266: Check for shell builtins BEFORE env injection ──
        # Shell builtins (cd, export, source, etc.) should NOT get env prefix
        stripped_command = command.strip()
        first_word = stripped_command.split()[0] if stripped_command.split() else ""
        is_builtin = first_word in self.SHELL_BUILTINS

        # ── RCA ITR-41: CWD enforcement for EVERY non-builtin command ──
        # Root cause: Terminal CWD drifts after scaffold tools (create-next-app),
        # cd commands, or process working directory changes. Subsequent commands
        # with relative paths resolve against wrong CWD, creating nested duplicates.
        # Fix: Prepend `cd <project_path> && ` to every non-builtin command.
        # This is core to agent operation — the project path is deterministic
        # from chat context and MUST scope every file system operation.
        cd_prefix = ""
        if not is_builtin and self.project_path and self.project_path.strip():
            cd_prefix = f"cd {self.project_path} && "
        
        if await self.should_use_mise():
            # Basic check: if it's a builtin or contains shell operators, don't wrap directly with 'mise exec'
            # 'mise exec' is intended for standalone executables.
            if not stripped_command:
                return command
            
            # If it's a shell builtin or contains operators, let it run as-is
            if is_builtin or any(c in stripped_command for c in ";&|"):
                return f"{cd_prefix}{command}" if cd_prefix else command
            
            # Detect environment variable assignment at the start (e.g., PYTHONPATH=/foo cmd)
            # mise exec -- PYTHONPATH=/foo cmd fails because it expects an executable.
            # Fix: Wrap with 'env' if assignment detected.
            if "=" in first_word and not first_word.startswith("-"):
                # Ensure we don't prefix if it already starts with 'env' (though '=' check covers it)
                if first_word != "env":
                    return f"{cd_prefix}mise exec -- env {command}"
                
            return f"{cd_prefix}mise exec -- {command}"

        # ── RCA-266: Auto-source .env.local for non-builtin commands ──
        if not is_builtin:
            env_prefix = self._build_env_source_prefix()
            if env_prefix:
                return f"{cd_prefix}{env_prefix}{command}"

        return f"{cd_prefix}{command}" if cd_prefix else command


    async def get_isolated_env(self) -> dict:
        """
        Get the environment variables for the isolated environment.
        This can be used to pass to subprocess.run or TTYSession.
        """
        if not await self.should_use_mise():
            return os.environ.copy()
            
        try:
            # Run 'mise env' to get the environment variables
            proc = await asyncio.create_subprocess_exec(
                "mise", "env",
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            env = os.environ.copy()
            if proc.returncode == 0:
                # Parse 'export KEY=VALUE' or 'KEY=VALUE' lines
                for line in stdout.decode().splitlines():
                    if line.startswith("export "):
                        line = line[7:]
                    if "=" in line:
                        key, value = line.split("=", 1)
                        # Remove quotes if present
                        value = value.strip("'\"")
                        env[key] = value
            return env
        except Exception:
            return os.environ.copy()
