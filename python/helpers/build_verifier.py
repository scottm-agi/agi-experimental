"""Build verification helper — L1 deterministic tool.

Provides universal build command detection and result parsing.
Works for any project type (Next.js, Vite, Python, etc.).

Usage:
    from python.helpers.build_verifier import detect_build_command, parse_build_output
    cmd = detect_build_command("/path/to/project")
    result = parse_build_output(build_output_text)
"""
from __future__ import annotations

import os
import re
import json
import logging
from typing import Optional, Dict, List, Any

logger = logging.getLogger("agix.build_verifier")

# Map of file → build command for common project types.
# Order matters: first match wins (package.json is checked first).
BUILD_COMMANDS = {
    'package.json': 'npm run build',
    'Cargo.toml': 'cargo build --release',
    'pyproject.toml': 'python -m py_compile',
    'go.mod': 'go build ./...',
    'Makefile': 'make',
}

# Patterns that indicate build failure — each is a regex fragment
BUILD_ERROR_PATTERNS = [
    r'error TS\d+',          # TypeScript
    r'SyntaxError:',         # Python/JS
    r'Module not found',     # Node.js
    r'Cannot find module',   # Node.js
    r'ERROR in',             # Webpack
    r'Build failed',         # Generic
    r'error\[E\d+\]',       # Rust
    r'FAIL',                 # Jest/testing
]


def detect_build_command(project_dir: Optional[str]) -> Optional[str]:
    """Detect the appropriate build command for a project.

    Checks for known build config files and returns the command.
    Returns None if no build system detected.

    Args:
        project_dir: Path to the project directory.

    Returns:
        Build command string, or None if no build system detected.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return None

    for config_file, command in BUILD_COMMANDS.items():
        config_path = os.path.join(project_dir, config_file)
        if os.path.exists(config_path):
            # For package.json, verify 'build' script exists
            if config_file == 'package.json':
                try:
                    with open(config_path) as f:
                        pkg = json.load(f)
                    if 'build' not in pkg.get('scripts', {}):
                        continue
                except (json.JSONDecodeError, IOError, OSError):
                    continue
            return command
    return None


def parse_build_output(output: str) -> Dict[str, Any]:
    """Parse build output for errors.

    Scans the output text for known error patterns and returns
    a structured result.

    Args:
        output: Raw build command stdout/stderr text.

    Returns:
        dict with:
            'passed': bool — True if no errors found
            'error_count': int — number of error lines matched
            'errors': list[str] — matching error lines (capped at 20)
    """
    errors: List[str] = []
    for pattern in BUILD_ERROR_PATTERNS:
        matches = re.findall(f'.*{pattern}.*', output, re.MULTILINE)
        errors.extend(matches[:5])  # Cap at 5 per pattern

    return {
        'passed': len(errors) == 0,
        'error_count': len(errors),
        'errors': errors[:20],  # Cap total at 20
    }


def build_verification_prompt(project_dir: str) -> Optional[str]:
    """Generate a build verification instruction for agents.

    Returns a prompt string the agent should follow, or None
    if no build system is detected.

    Args:
        project_dir: Path to the project directory.

    Returns:
        Markdown instruction string, or None if no build system detected.
    """
    cmd = detect_build_command(project_dir)
    if not cmd:
        return None

    return (
        f"## 🔨 BUILD VERIFICATION REQUIRED\n\n"
        f"Before reporting completion, you MUST run the build:\n"
        f"```bash\ncd {project_dir} && {cmd}\n```\n\n"
        f"If the build fails, fix ALL errors before reporting success.\n"
        f"Build errors that reach E2E testing waste significant time.\n"
    )
