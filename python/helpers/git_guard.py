"""
Git Guard - Centralized Protection Against Host Repository Pollution

This module provides deterministic protection against git operations that could
affect parent repositories (especially the host repo mounted at /agix/).

CRITICAL RULE: Git commands traverse UP to find .git directories. If a directory
doesn't have its own .git, git will use the parent's .git, causing pollution.

Usage:
    from python.helpers.git_guard import GitGuard
    
    # Validate before running git commands
    GitGuard.validate_git_operation("/agix/usr/projects/my-project", "checkout -b feature")
    
    # Or execute safely
    result = GitGuard.execute_git_safely("/agix/usr/projects/my-project", ["checkout", "-b", "feature"])
"""

from __future__ import annotations
import os
import re as _re
import subprocess
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("agix.git_guard")


class GitGuardError(Exception):
    """Raised when a git operation would affect a protected repository."""
    pass


class GitGuard:
    """
    Deterministic protection against git operations affecting host repos.
    
    ALL git commands should go through this guard:
    - Validates working directory has own .git
    - Blocks commands that would traverse UP to parent .git
    - Logs all blocked operations for audit
    
    Protected paths:
    - /agix/ (host repository mount)
    - Any directory without its own .git that has a parent with .git
    
    Always safe paths:
    - /tmp/ (completely isolated from host)
    - Any directory with its own .git subdirectory
    """
    
    # Layer 1: URL-embedded credentials with colon separator
    # Matches: https://user:TOKEN@host, https://x-access-token:TOKEN@host
    # Preserves: §§secret() macro placeholders
    _SECRET_PATTERN = _re.compile(
        r'(https?://)([^:]+):(?!§§secret\()([^@]+)@'
    )

    # Layer 2: Bare-token-in-URL (RCA #210 — the actual leak vector)
    # Matches: https://gh" + "o_TOKEN@host, https://gh" + "p_TOKEN@host,
    #          https://github_pat_TOKEN@host, https://ANYTOKEN@host
    # Does NOT match: https://user:token@host (handled by Layer 1)
    _BARE_TOKEN_URL_PATTERN = _re.compile(
        r'(https?://)([^:@\s]{8,})@'
    )

    # Layer 3: Raw token strings in command arguments (not in URLs)
    # Matches: gh" + "o_xxx, gh" + "p_xxx, github_pat_xxx, sk-xxx, pplx-xxx, re_xxx
    _RAW_TOKEN_PATTERNS = [
        _re.compile(r'\bgh" + "o_[A-Za-z0-9_]{10,}\b'),
        _re.compile(r'\bgh" + "p_[A-Za-z0-9_]{10,}\b'),
        _re.compile(r'\bgithub_pat_[A-Za-z0-9_]{10,}\b'),
        _re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9]{10,}\b'),
        _re.compile(r'\bpplx-[A-Za-z0-9]{10,}\b'),
        _re.compile(r'\bre_[A-Za-z0-9]{10,}\b'),
    ]

    # Paths that are ALWAYS blocked for git state-changing operations
    PROTECTED_ROOTS = ["/agix", "/agix"]
    
    # Paths that are ALWAYS safe (outside any repo tree)
    ALWAYS_SAFE_PREFIXES = ["/tmp/", "/tmp"]
    
    # Git commands that modify repository state
    DESTRUCTIVE_COMMANDS = {
        "checkout", "switch", "commit", "push", "merge", "rebase", 
        "reset", "branch", "init", "pull", "cherry-pick", "revert",
        "stash", "tag", "am", "apply", "remote", "fetch", "clone",
        "worktree", "submodule", "mv", "rm", "add"
    }
    
    # Git commands that are always safe (read-only)
    SAFE_COMMANDS = {
        "status", "log", "diff", "show", "ls-files", "ls-tree",
        "rev-parse", "describe", "config", "help", "version"
    }
    
    @classmethod
    def redact_secrets(cls, text: str) -> str:
        """
        Redact embedded credentials from git command strings.
        
        Three-layer redaction:
          Layer 1: https://user:TOKEN@host → https://user:***@host
          Layer 2: https://TOKEN@host → https://***@host  (RCA #210)
          Layer 3: Raw token strings (gh" + "o_, gh" + "p_, sk-, pplx-, re_) → ***REDACTED***
        
        Preserves §§secret() macro placeholders (framework-internal, not real tokens).
        """
        # Layer 1: user:token@host (existing behavior)
        result = cls._SECRET_PATTERN.sub(r'\1\2:***@', text)
        # Layer 2: bare-token@host (RCA #210 fix)
        result = cls._BARE_TOKEN_URL_PATTERN.sub(r'\1***@', result)
        # Layer 3: raw token strings anywhere in text
        for pattern in cls._RAW_TOKEN_PATTERNS:
            result = pattern.sub('***REDACTED***', result)
        return result
    
    @classmethod
    def _normalize_path(cls, path: str) -> str:
        """Normalize a path for comparison."""
        if not path or path == ".":
            return os.getcwd()
        return os.path.abspath(os.path.expanduser(path)).rstrip("/")
    
    @classmethod
    def _is_always_safe(cls, path: str) -> bool:
        """Check if path is in an always-safe zone (like /tmp)."""
        normalized = cls._normalize_path(path)
        return any(normalized.startswith(prefix) for prefix in cls.ALWAYS_SAFE_PREFIXES)
    
    @classmethod
    def _has_own_git(cls, path: str) -> bool:
        """Check if directory has its own .git (is a git repo root)."""
        normalized = cls._normalize_path(path)
        git_dir = os.path.join(normalized, ".git")
        return os.path.exists(git_dir)
    
    @classmethod
    def _find_parent_git(cls, path: str) -> Optional[str]:
        """
        Find the nearest parent directory with a .git.
        Returns the parent path if found, None otherwise.
        """
        normalized = cls._normalize_path(path)
        # BUG FIX: Start from current path, not parent, to detect local repos correctly
        current = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
        
        while current and current != "/":
            if os.path.exists(os.path.join(current, ".git")):
                return current
            current = os.path.dirname(current)
        
        return None
    
    @classmethod
    def _is_destructive_command(cls, command: str) -> bool:
        """Check if a git command is destructive (modifies state)."""
        # Parse the git subcommand from various formats
        parts = command.strip().split()
        
        # Skip flags like -C to find actual subcommand
        i = 0
        while i < len(parts):
            part = parts[i].lower()
            if part == "git":
                i += 1
                continue
            # Skip -C /path (takes an argument)
            if part in ["-c", "--git-dir", "--work-tree"] and i + 1 < len(parts):
                i += 2
                continue
            # Found the subcommand
            return part in cls.DESTRUCTIVE_COMMANDS
        
        return False
    
    @classmethod
    def _extract_target_paths(cls, command: str) -> list:
        """
        Extract target paths from git command arguments with robust parsing.
        
        Handles:
        - -C /path (changes working directory)
        - --git-dir=/path (overrides .git location)
        - --work-tree=/path
        - worktree add /path
        - submodule add url /path
        - clone url /path
        
        Returns list of paths that command targets.
        """
        import re
        paths = []
        
        # Use regex to find -C and --git-dir with or without equals
        # -C path, -Cpath, --git-dir=path, --git-dir path
        c_matches = re.finditer(r'(?:^|\s)-C\s*([^\s]+)', command)
        for m in c_matches:
            paths.append(m.group(1))
            
        gd_matches = re.finditer(r'--git-dir\s*[=\s]\s*([^\s]+)', command)
        for m in gd_matches:
            paths.append(m.group(1))
            
        wt_matches = re.finditer(r'--work-tree\s*[=\s]\s*([^\s]+)', command)
        for m in wt_matches:
            paths.append(m.group(1))
            
        parts = command.strip().split()
        
        # Positional path extraction for specific subcommands
        i = 0
        while i < len(parts):
            part = parts[i].lower()
            # worktree add <path> [<commit>]
            if part == "worktree" and i + 2 < len(parts):
                if parts[i + 1].lower() == "add":
                    paths.append(parts[i + 2])
            # submodule add <url> [<path>]
            elif part == "submodule" and i + 2 < len(parts):
                if parts[i + 1].lower() == "add":
                    # If there's a 4th part that doesn't start with '-', it's likely the path
                    if i + 3 < len(parts) and not parts[i + 3].startswith("-"):
                        paths.append(parts[i + 3])
            # clone <url> [<path>]
            elif part == "clone" and i + 1 < len(parts):
                # Simple heuristic: last positional arg if it doesn't look like a URL/option
                potential_path = parts[-1]
                if not potential_path.startswith("-") and "://" not in potential_path and "@" not in potential_path:
                    paths.append(potential_path)
            i += 1
        
        return [p.strip('"\'') for p in paths if p]
    
    @classmethod
    def _is_git_creation_command(cls, command: str) -> bool:
        """
        Check if the command is a git 'creation' command that CREATES .git isolation.
        
        These commands (worktree add, clone, init) are expected to target paths
        that do NOT yet have .git — because they're the ones creating it.
        """
        cmd_lower = command.lower().strip()
        creation_patterns = [
            "worktree add",
            "clone",
            "init",
        ]
        return any(pattern in cmd_lower for pattern in creation_patterns)

    @classmethod
    def _check_target_paths(cls, command: str, active_project: Optional[str] = None) -> tuple:
        """
        Check if command targets protected paths or paths outside active project.
        
        Returns (is_blocked, message) where is_blocked=True means HARD BLOCK.
        """
        paths = cls._extract_target_paths(command)
        
        # Determine project prefix if scoping is active
        project_prefix = None
        if active_project:
            projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects")
            project_prefix = cls._normalize_path(os.path.join(projects_dir, active_project))

        # Creation commands (worktree add, clone, init) target paths that
        # WON'T have .git yet — because they CREATE the .git isolation.
        is_creation = cls._is_git_creation_command(command)

        for path in paths:
            normalized = cls._normalize_path(path) if path else ""
            
            # Layer 1: Project Scoping (if active)
            if project_prefix:
                if not (normalized == project_prefix or normalized.startswith(project_prefix + "/")):
                    # Exception: allow always-safe paths
                    if not cls._is_always_safe(normalized):
                        return True, (
                            f"HARD BLOCK: Project Scoping Enforcement active for '{active_project}'. "
                            f"Command targets path '{path}' ({normalized}) which is outside the project sandbox."
                        )

            # Layer 2: Protected Roots (Legacy traversal protection)
            for root in cls.PROTECTED_ROOTS:
                if normalized == root.rstrip("/") or normalized.startswith(root + "/"):
                    # For creation commands, the target path won't have .git yet
                    # because the command itself creates it. Allow if target is
                    # under the projects directory (safe zone for builds).
                    if is_creation:
                        projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects") 
                        projects_norm = cls._normalize_path(projects_dir)
                        if normalized.startswith(projects_norm + "/") or normalized == projects_norm:
                            logger.info(
                                f"[GitGuard] Allowing creation command targeting "
                                f"'{path}' in projects dir (will create .git isolation)"
                            )
                            continue  # Allow — creation in projects dir is safe
                    
                    # For non-creation commands, require .git isolation
                    if not cls._has_own_git(normalized):
                        return True, (
                            f"HARD BLOCK: Command contains path '{path}' targeting "
                            f"PROTECTED zone without .git isolation. "
                            f"Clone the target repository first."
                        )
        
        return False, ""

    
    @classmethod
    def validate_git_operation(
        cls, 
        working_dir: str, 
        command: str,
        raise_on_block: bool = True,
        active_project: Optional[str] = None
    ) -> tuple:

        """
        Validate that a git operation is safe to execute.
        
        Uses HYBRID blocking approach:
        - SOFT WARNING: General concerns, allowed but agent should review
        - HARD BLOCK: Confirmed .git traversal to PROTECTED parent (/agix)
        
        Args:
            working_dir: Directory where git command will run
            command: The git command (e.g., "checkout -b feature")
            raise_on_block: If True, raise GitGuardError for HARD blocks only
            active_project: Optional project name to RESTRICT command to that project's tree.
            
        Returns:
            Tuple of (is_allowed: bool, warning_message: str)
            - (True, "") = Fully allowed, no issues
            - (True, "warning...") = Allowed with soft warning to agent
            - (False, "blocked...") = Hard blocked (protected parent or project mismatch)
            
        Raises:
            GitGuardError: ONLY for HARD blocks when raise_on_block=True
        """

        normalized_dir = cls._normalize_path(working_dir)
        
        # CRITICAL FIX: If the command uses -C <path>, that path IS the effective
        # working directory for all git operations. Use it instead of CWD for all
        # subsequent checks. This prevents false HARD BLOCKs when agents run from
        # /agix (host mount) but correctly target project dirs via -C.
        import re
        c_match = re.search(r'(?:^|\s)-C\s+([^\s]+)', command)
        if c_match:
            c_path = c_match.group(1).strip("\"'")
            effective_dir = cls._normalize_path(c_path)
            logger.debug(
                f"[GitGuard] Command uses -C flag; overriding CWD '{normalized_dir}' "
                f"with effective dir '{effective_dir}'"
            )
            normalized_dir = effective_dir
        
        # FIRST: Check for bypass attempts via command arguments (-C, --git-dir, etc.)
        is_blocked, block_msg = cls._check_target_paths(command, active_project=active_project)
        if is_blocked:
            logger.error(f"[GitGuard] {cls.redact_secrets(block_msg)}")
            if raise_on_block:
                raise GitGuardError(cls.redact_secrets(block_msg))
            return False, cls.redact_secrets(block_msg)

        # PROJECT SCOPING LAYER: If active_project is set, command MUST stay in that project
        if active_project:
            # Resolve allowed project path
            projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects")
            allowed_project_path = cls._normalize_path(os.path.join(projects_dir, active_project))
            
            # Check if working_dir is in allowed_project_path
            if not (normalized_dir == allowed_project_path or normalized_dir.startswith(allowed_project_path + "/")):
                 # Exception: allow always-safe paths even when project is set (e.g. for /tmp/repo_analysis)
                if not cls._is_always_safe(normalized_dir):
                    msg = (
                        f"HARD BLOCK: Project Scoping Enforcement active for '{active_project}'. "
                        f"Command targeted directory '{normalized_dir}' which is outside the project sandbox."
                    )
                    logger.error(f"[GitGuard] {cls.redact_secrets(msg)}")
                    if raise_on_block:
                        raise GitGuardError(cls.redact_secrets(msg))
                    return False, cls.redact_secrets(msg)

        # Always-safe paths are allowed
        if cls._is_always_safe(normalized_dir):
            logger.debug(f"[GitGuard] ALLOWED: {command} in always-safe path {normalized_dir}")
            return True, ""
        
        # Non-destructive commands are always allowed
        if not cls._is_destructive_command(command):
            logger.debug(f"[GitGuard] ALLOWED: Non-destructive command {command}")
            return True, ""

        # CRITICAL FIX (Issue #712): Block destructive ops at PROTECTED_ROOT itself.
        # Even though /agix/ and /agix/ have their own .git, they ARE the host
        # repo volume mount. Only SUBdirectories with their own .git should pass.
        for root in cls.PROTECTED_ROOTS:
            if normalized_dir.rstrip("/") == root.rstrip("/"):
                if cls._is_destructive_command(command):
                    # Iteration 109: Include active project path in error for faster recovery
                    projects_base = os.environ.get("PROJECTS_DIR", "/agix/usr/projects")
                    msg = (
                        f"HARD BLOCK: Destructive git command '{command}' at "
                        f"PROTECTED ROOT '{normalized_dir}'. This would modify "
                        f"the host repository. Run git commands inside your project "
                        f"directory instead. "
                        f"HINT: Use `git -C {projects_base}/<your-project-name>/ {command}` "
                        f"or `cd` into the project first."
                    )
                    logger.error(f"[GitGuard] {cls.redact_secrets(msg)}")
                    if raise_on_block:
                        raise GitGuardError(cls.redact_secrets(msg))
                    return False, cls.redact_secrets(msg)

        # isolation creators (init/clone/worktree add) bypass subtree and traversal
        # checks because they CREATE the .git isolation themselves.
        # Must be checked BEFORE subtree protection (they don't need .git to exist).
        if cls._is_git_creation_command(command):
            # Extra safety: only allow in projects dir, not at protected root itself
            projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects")
            projects_norm = cls._normalize_path(projects_dir)
            if normalized_dir.startswith(projects_norm + "/") or normalized_dir == projects_norm:
                logger.debug(f"[GitGuard] ALLOWED isolation creator in projects dir: {command}")
                return True, ""
            # For creation commands outside projects dir, check if cwd has .git
            # (e.g., `git -C /some/clone worktree add ...` — cwd is the clone)
            if cls._has_own_git(normalized_dir):
                logger.debug(f"[GitGuard] ALLOWED isolation creator from git repo: {command}")
                return True, ""

        # FIX (Issue #712 re-open): Defense-in-depth subtree protection.
        # If we are ANYWHERE under a protected root and don't have our own .git,
        # block destructive commands. This catches the case where _find_parent_git
        # might fail (filesystem race, caching, etc.) but git's own traversal
        # would still find the host repo's .git.
        for root in cls.PROTECTED_ROOTS:
            root_norm = root.rstrip("/")
            if normalized_dir.startswith(root_norm + "/"):
                if not cls._has_own_git(normalized_dir):
                    # EXEMPTION: Paths inside the projects directory are the
                    # designated safe zone for agent work. Allow ALL git ops
                    # there, not just creation commands. Project subdirs
                    # (e.g. tmp/push_staging) won't have their own .git but
                    # are children of the project's .git — that's safe.
                    projects_dir = os.environ.get("PROJECTS_DIR", "/agix/usr/projects" if os.path.exists("/agix/usr/projects") else "/agix/usr/projects")
                    projects_norm = cls._normalize_path(projects_dir)
                    if normalized_dir.startswith(projects_norm + "/") or normalized_dir == projects_norm:
                        logger.info(
                            f"[GitGuard] Allowing git command in projects dir "
                            f"(safe zone): {command} in {normalized_dir}"
                        )
                        continue  # Allow — projects dir is the safe zone
                    msg = (
                        f"HARD BLOCK: Destructive git command '{command}' in "
                        f"'{normalized_dir}' which is under PROTECTED ROOT "
                        f"'{root_norm}' without its own .git isolation. "
                        f"Clone the repository first with `git clone <url> .`"
                    )
                    logger.error(f"[GitGuard] {cls.redact_secrets(msg)}")
                    if raise_on_block:
                        raise GitGuardError(cls.redact_secrets(msg))
                    return False, cls.redact_secrets(msg)

        # No .git here - check if a parent has .git (would be affected!)
        parent_git = cls._find_parent_git(normalized_dir)
        
        if parent_git:
            # CRITICAL: Only block if the matched .git IS the host root repo.
            # Sub-repositories (sandboxes) under /agix/usr/projects/ are allowed.
            is_protected = any(
                parent_git.rstrip("/") == root.rstrip("/")
                for root in cls.PROTECTED_ROOTS
            )
            
            if is_protected:
                # HARD BLOCK: Would affect PROTECTED host repository
                msg = (
                    f"HARD BLOCK: Git command '{command}' in '{normalized_dir}' would traverse UP "
                    f"and affect PROTECTED host repository at '{parent_git}'. "
                    f"SOLUTION: Clone the target repository first with `git clone <url> .` "
                    f"so this directory has its own .git"
                )
                logger.error(f"[GitGuard] {cls.redact_secrets(msg)}")
                
                if raise_on_block:
                    raise GitGuardError(cls.redact_secrets(msg))
                return False, cls.redact_secrets(msg)
            else:
                # SOFT WARNING: Non-protected parent, allow but warn
                warning = (
                    f"SOFT WARNING: Git command '{command}' in '{normalized_dir}' has no .git. "
                    f"Would affect parent repository at '{parent_git}'. "
                    f"Consider cloning first to isolate changes."
                )
                logger.warning(f"[GitGuard] {cls.redact_secrets(warning)}")
                return True, cls.redact_secrets(warning)  # Allow with warning (redacted)
        
        # NOTE: Duplicate init/clone check and checkout -b/switch -c whitelist
        # were REMOVED here as part of Issue #712 fix. The init/clone check exists
        # at L320-328 already, and the checkout -b whitelist was the PRIMARY BYPASS
        # VECTOR that allowed host repo branch changes.

        # No .git anywhere - soft warning, allow but advise
        warning = (
            f"SOFT WARNING: Git command '{command}' in '{normalized_dir}' - "
            f"no .git directory found. Initialize a repo first with `git init` "
            f"or clone with `git clone <url> .`"
        )
        logger.warning(f"[GitGuard] {cls.redact_secrets(warning)}")
        return True, cls.redact_secrets(warning)  # Allow with warning (redacted)
    
    @classmethod
    def execute_git_safely(
        cls,
        working_dir: str,
        args: List[str],
        timeout: int = 60,
        capture_output: bool = True,
        active_project: Optional[str] = None
    ) -> subprocess.CompletedProcess:
        """
        Execute a git command with safety validation.
        
        Args:
            working_dir: Directory to run git command in
            args: Git arguments (without 'git' prefix), e.g., ["checkout", "-b", "feature"]
            timeout: Command timeout in seconds
            capture_output: Whether to capture stdout/stderr
            active_project: Optional project name for scoping
            
        Returns:
            subprocess.CompletedProcess with command result
            
        Raises:
            GitGuardError: If operation would affect PROTECTED repo (hard block)
        """
        command = " ".join(args)
        is_allowed, warning = cls.validate_git_operation(
            working_dir, 
            command, 
            raise_on_block=True,
            active_project=active_project
        )
        
        if warning:
            logger.warning(f"[GitGuard] Proceeding with warning: {cls.redact_secrets(warning)}")
        
        # Execute the validated command
        full_cmd = ["git"] + args
        logger.info(f"[GitGuard] Executing: {cls.redact_secrets(' '.join(full_cmd))} in {working_dir}")
        
        return subprocess.run(
            full_cmd,
            cwd=working_dir,
            timeout=timeout,
            capture_output=capture_output,
            text=True
        )

    
    @classmethod
    def check_directory_safety(cls, path: str) -> dict:
        """
        Check a directory's git safety status.
        
        Returns a dict with:
        - safe: bool - whether destructive git ops are allowed
        - has_own_git: bool - whether dir has its own .git
        - parent_git: str|None - path to parent .git if exists
        - reason: str - explanation of status
        """
        normalized = cls._normalize_path(path)
        
        if cls._is_always_safe(normalized):
            return {
                "safe": True,
                "has_own_git": cls._has_own_git(normalized),
                "parent_git": None,
                "reason": "Path is in always-safe zone (/tmp)"
            }
        
        has_own = cls._has_own_git(normalized)
        parent = cls._find_parent_git(normalized)
        
        if has_own:
            # Even with own .git, protected roots are unsafe for destructive ops
            is_root = any(
                normalized.rstrip("/") == root.rstrip("/")
                for root in cls.PROTECTED_ROOTS
            )
            if is_root:
                return {
                    "safe": False,
                    "has_own_git": True,
                    "parent_git": parent,
                    "reason": f"UNSAFE: Directory IS a PROTECTED ROOT ({normalized}) — destructive git ops blocked"
                }
            return {
                "safe": True,
                "has_own_git": True,
                "parent_git": parent,
                "reason": "Directory has its own .git - git ops are isolated"
            }
        
        if parent:
            is_protected = any(
                parent.rstrip("/") == root.rstrip("/")
                for root in cls.PROTECTED_ROOTS
            )
            return {
                "safe": False,
                "has_own_git": False,
                "parent_git": parent,
                "reason": f"UNSAFE: No .git here, would affect {'PROTECTED ' if is_protected else ''}parent repo at {parent}"
            }
        
        return {
            "safe": False,
            "has_own_git": False,
            "parent_git": None,
            "reason": "No .git found - must init or clone first"
        }
