"""
npm Install Serialization — Project-Level Mutex

Root Cause (P1 Fix 3):
    When call_subordinate_batch spawns parallel agents that both run
    `npm install` in the same project directory, concurrent writes to
    node_modules/ cause ENOTEMPTY race conditions. npm's internal file
    operations are not atomic — two processes writing the same package
    directory simultaneously results in one failing with ENOTEMPTY.

Fix:
    File-based exclusive lock (fcntl.flock) per project directory.
    Any agent that needs to run `npm install` first acquires the lock.
    Other agents block until the lock is released. Different project
    directories use different lock files, so they don't block each other.

Architecture:
    - NpmMutex: Context manager wrapping fcntl.flock on a per-project lock file
    - is_npm_install_command: Regex detection of npm install/ci/i commands
    - The code_execution_tool wraps detected commands with NpmMutex
"""
from __future__ import annotations

import fcntl
import os
import re
import logging

logger = logging.getLogger("agix.npm_mutex")

# Patterns that indicate an npm install operation (needs mutex)
_NPM_INSTALL_PATTERNS = [
    re.compile(r'\bnpm\s+install\b'),
    re.compile(r'\bnpm\s+ci\b'),
    re.compile(r'\bnpm\s+i\b'),
]

# Patterns that are NOT install operations (exclude false positives)
_NPM_NON_INSTALL_PATTERNS = [
    re.compile(r'\bnpm\s+run\b'),
    re.compile(r'\bnpm\s+test\b'),
    re.compile(r'\bnpm\s+start\b'),
]


class NpmMutex:
    """Project-level file lock for npm install operations.

    Uses fcntl.flock for exclusive locking — blocking, POSIX-compatible,
    and automatically released on process exit or crash.

    Usage:
        with NpmMutex("/path/to/project"):
            subprocess.run(["npm", "install"], cwd="/path/to/project")

        # With timeout to prevent infinite blocking:
        with NpmMutex("/path/to/project", timeout=60.0):
            subprocess.run(["npm", "install"], cwd="/path/to/project")

    Different project directories use different lock files, so parallel
    installs in different projects are NOT blocked.
    """

    def __init__(self, project_dir: str, timeout: float | None = None):
        """
        Args:
            project_dir: Path to the project directory.
            timeout: Maximum seconds to wait for lock acquisition.
                     None = block forever (original behavior).
                     >0 = raise TimeoutError if lock not acquired within timeout.
        """
        self.project_dir = project_dir
        self.lock_path = os.path.join(project_dir, ".npm_install.lock")
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        # Ensure the project directory exists
        os.makedirs(self.project_dir, exist_ok=True)
        self._fd = open(self.lock_path, "w")
        
        if self.timeout is not None:
            # Non-blocking acquisition with polling and timeout
            # ADR rca_terminal_blocking_stall: prevents cascading stalls
            import time
            start = time.time()
            poll_interval = 0.5  # 500ms polling
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    logger.debug(f"Acquired npm mutex for {self.project_dir}")
                    return self
                except (OSError, IOError):
                    elapsed = time.time() - start
                    if elapsed >= self.timeout:
                        self._fd.close()
                        self._fd = None
                        logger.warning(
                            f"npm mutex timeout ({self.timeout}s) for {self.project_dir} — "
                            f"proceeding without lock"
                        )
                        raise TimeoutError(
                            f"Could not acquire npm mutex for {self.project_dir} "
                            f"within {self.timeout}s"
                        )
                    time.sleep(poll_interval)
        else:
            # Original blocking behavior
            logger.debug(f"Acquiring npm mutex for {self.project_dir}")
            fcntl.flock(self._fd, fcntl.LOCK_EX)  # Blocking exclusive lock
            logger.debug(f"Acquired npm mutex for {self.project_dir}")
            return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            logger.debug(f"Released npm mutex for {self.project_dir}")
        return False  # Don't suppress exceptions


def is_npm_install_command(command: str) -> bool:
    """Detect if a shell command includes an npm install operation.

    Handles compound commands (e.g., "cd /project && npm install").
    Returns False for npm run, npm test, npm start, etc.

    Args:
        command: The shell command string to check.

    Returns:
        True if the command contains an npm install/ci/i operation.
    """
    if not command:
        return False

    # Check for non-install npm commands first (higher priority)
    for pattern in _NPM_NON_INSTALL_PATTERNS:
        if pattern.search(command):
            # But it might also have an install in a compound command
            # e.g., "npm run build && npm install" — check if install is separate
            pass

    # Check for install patterns
    for pattern in _NPM_INSTALL_PATTERNS:
        if pattern.search(command):
            return True

    return False
