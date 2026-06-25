"""
Async Subprocess Execution with Timeout and Retry Support

Provides non-blocking subprocess execution with:
- Proper timeout handling
- Retry support with exponential backoff
- Safe working directory validation
- Output streaming for long-running commands

Usage:
    from python.helpers.async_subprocess import run_command, run_git_command
    
    # Simple command
    result = await run_command(["ls", "-la"], cwd="/path/to/dir")
    
    # Git command with retries
    result = await run_git_command(["clone", url, path], timeout=300)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SubprocessConfig:
    """Configuration for async subprocess execution."""
    
    timeout: float = 60.0                    # Command timeout in seconds
    max_retries: int = 2                     # Maximum retry attempts
    initial_delay: float = 1.0               # Initial retry delay
    max_delay: float = 30.0                  # Maximum retry delay
    backoff_multiplier: float = 2.0          # Exponential backoff multiplier
    jitter_factor: float = 0.2               # Random jitter (±20%)
    check_cwd: bool = True                   # Validate working directory exists
    
    def calculate_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        base = self.initial_delay * (self.backoff_multiplier ** attempt)
        capped = min(base, self.max_delay)
        jitter = random.uniform(-self.jitter_factor, self.jitter_factor)
        return max(0.1, capped * (1 + jitter))


@dataclass
class CommandResult:
    """Result of a command execution."""
    
    returncode: int
    stdout: str
    stderr: str
    command: List[str]
    timeout_expired: bool = False
    attempts: int = 1
    
    @property
    def success(self) -> bool:
        """Check if command succeeded."""
        return self.returncode == 0
    
    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        return f"{self.stdout}\n{self.stderr}".strip()


class SubprocessError(Exception):
    """Error raised when subprocess execution fails."""
    
    def __init__(self, message: str, result: Optional[CommandResult] = None):
        super().__init__(message)
        self.result = result


class SubprocessTimeoutError(SubprocessError):
    """Error raised when subprocess times out."""
    pass


# Default configurations for common command types
GIT_CONFIG = SubprocessConfig(
    timeout=300.0,      # 5 minutes for clone operations
    max_retries=2,
    initial_delay=2.0,
)

QUICK_CONFIG = SubprocessConfig(
    timeout=30.0,       # 30 seconds for quick operations
    max_retries=1,
    initial_delay=1.0,
)

NETWORK_CONFIG = SubprocessConfig(
    timeout=120.0,      # 2 minutes for network operations
    max_retries=3,
    initial_delay=2.0,
)


async def run_command(
    cmd: List[str],
    cwd: Optional[Union[str, Path]] = None,
    config: Optional[SubprocessConfig] = None,
    env: Optional[dict] = None,
    capture_output: bool = True,
    raise_on_error: bool = False,
) -> CommandResult:
    """
    Execute a command asynchronously with timeout and retry support.
    
    Args:
        cmd: Command and arguments as a list
        cwd: Working directory (optional)
        config: Subprocess configuration
        env: Environment variables (optional, merges with current env)
        capture_output: Whether to capture stdout/stderr
        raise_on_error: Whether to raise on non-zero exit code
        
    Returns:
        CommandResult with execution details
        
    Raises:
        SubprocessTimeoutError: If command times out after all retries
        SubprocessError: If command fails and raise_on_error is True
    """
    config = config or SubprocessConfig()
    
    # Validate working directory
    if cwd and config.check_cwd:
        cwd_path = Path(cwd)
        if not cwd_path.exists():
            raise SubprocessError(f"Working directory does not exist: {cwd}")
        if not cwd_path.is_dir():
            raise SubprocessError(f"Working directory is not a directory: {cwd}")
    
    # Prepare environment
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    
    # CRITICAL: Hardened Isolation - Prevent git from traversing above /agix
    # This is a global protection for ALL git commands executed through this helper.
    process_env["GIT_CEILING_DIRECTORIES"] = "/agix"
    
    last_result: Optional[CommandResult] = None
    last_exception: Optional[Exception] = None
    
    for attempt in range(config.max_retries + 1):
        try:
            result = await _execute_command(
                cmd=cmd,
                cwd=str(cwd) if cwd else None,
                env=process_env,
                timeout=config.timeout,
                capture_output=capture_output,
            )
            result.attempts = attempt + 1
            
            if result.success or attempt >= config.max_retries:
                if raise_on_error and not result.success:
                    raise SubprocessError(
                        f"Command failed with exit code {result.returncode}: {' '.join(cmd)}",
                        result=result,
                    )
                return result
            
            # Retry on failure
            delay = config.calculate_retry_delay(attempt)
            logger.warning(
                f"Command failed (exit {result.returncode}), "
                f"retry {attempt + 1}/{config.max_retries} in {delay:.1f}s: {' '.join(cmd)}"
            )
            await asyncio.sleep(delay)
            last_result = result
            
        except asyncio.TimeoutError:
            last_exception = SubprocessTimeoutError(
                f"Command timed out after {config.timeout}s: {' '.join(cmd)}"
            )
            if attempt < config.max_retries:
                delay = config.calculate_retry_delay(attempt)
                logger.warning(
                    f"Command timed out, retry {attempt + 1}/{config.max_retries} in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
            else:
                raise last_exception
    
    # All retries exhausted
    if last_result and raise_on_error:
        raise SubprocessError(
            f"Command failed after {config.max_retries} retries: {' '.join(cmd)}",
            result=last_result,
        )
    
    return last_result or CommandResult(
        returncode=-1,
        stdout="",
        stderr="All retries exhausted",
        command=cmd,
        attempts=config.max_retries + 1,
    )


async def _execute_command(
    cmd: List[str],
    cwd: Optional[str],
    env: dict,
    timeout: float,
    capture_output: bool,
) -> CommandResult:
    """Execute a single command with timeout."""
    
    stdout_pipe = asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL
    stderr_pipe = asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=stdout_pipe,
        stderr=stderr_pipe,
    )
    
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        
        return CommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            command=cmd,
        )
        
    except asyncio.TimeoutError:
        # Kill the process on timeout
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass  # Process already terminated
        
        return CommandResult(
            returncode=-1,
            stdout="",
            stderr=f"Process killed after {timeout}s timeout",
            command=cmd,
            timeout_expired=True,
        )


# Convenience functions for common command types

async def run_git_command(
    args: List[str],
    cwd: Optional[Union[str, Path]] = None,
    timeout: Optional[float] = None,
    raise_on_error: bool = False,
) -> CommandResult:
    """
    Execute a git command with appropriate timeouts.
    
    Args:
        args: Git command arguments (without 'git' prefix)
        cwd: Working directory
        timeout: Override default timeout
        raise_on_error: Whether to raise on failure
        
    Returns:
        CommandResult
    """
    # Defense-in-depth: Validate through GitGuard before executing (Issue #712)
    try:
        from python.helpers.git_guard import GitGuard
        effective_cwd = str(cwd) if cwd else os.getcwd()
        command_str = " ".join(args)
        is_allowed, warning = GitGuard.validate_git_operation(
            effective_cwd, command_str, raise_on_block=False
        )
        if not is_allowed:
            logger.error(f"[run_git_command] BLOCKED by GitGuard: {warning}")
            return CommandResult(
                returncode=1,
                stdout="",
                stderr=f"BLOCKED by GitGuard: {warning}",
                command=["git"] + args,
            )
        if warning:
            logger.warning(f"[run_git_command] GitGuard warning: {warning}")
    except ImportError:
        pass  # GitGuard not available, proceed with raw execution

    config = SubprocessConfig(
        timeout=timeout or _get_git_timeout(args),
        max_retries=2 if _is_network_git_command(args) else 1,
        initial_delay=2.0,
    )
    
    cmd = ["git"] + args
    return await run_command(
        cmd=cmd,
        cwd=cwd,
        config=config,
        raise_on_error=raise_on_error,
    )


def _get_git_timeout(args: List[str]) -> float:
    """Get appropriate timeout for git command."""
    if not args:
        return 30.0
    
    cmd = args[0]
    
    # Network-heavy operations
    if cmd in ("clone", "fetch", "pull", "push"):
        return 300.0  # 5 minutes
    
    # Moderate operations
    if cmd in ("checkout", "merge", "rebase"):
        return 120.0  # 2 minutes
    
    # Quick operations
    return 30.0


def _is_network_git_command(args: List[str]) -> bool:
    """Check if git command involves network."""
    if not args:
        return False
    return args[0] in ("clone", "fetch", "pull", "push", "ls-remote")


async def run_shell_command(
    command: str,
    cwd: Optional[Union[str, Path]] = None,
    timeout: float = 60.0,
    shell: str = "/bin/bash",
) -> CommandResult:
    """
    Execute a shell command string.
    
    Args:
        command: Shell command string
        cwd: Working directory
        timeout: Timeout in seconds
        shell: Shell to use
        
    Returns:
        CommandResult
    """
    config = SubprocessConfig(timeout=timeout, max_retries=0)
    return await run_command(
        cmd=[shell, "-c", command],
        cwd=cwd,
        config=config,
    )


async def check_command_exists(command: str) -> bool:
    """Check if a command exists in PATH."""
    result = await run_command(
        cmd=["which", command],
        config=QUICK_CONFIG,
    )
    return result.success
