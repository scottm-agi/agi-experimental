"""
Post-Implementation Env Var Coverage Checker
=============================================

Scans .ts/.tsx/.js/.jsx files under src/ for `process.env.<VAR>` references
and verifies that every referenced var is defined in at least one .env file.

Fix 5 (ITR-25): Code agents create `process.env.STRIPE_SECRET_KEY` in source
but never add the key to .env files. No check catches this gap.

Used by:
- Orchestrator post-implementation gates
- Post-execution requirement verification
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Set

from python.helpers.source_scanner import read_project_files

# Regex to find process.env.VAR_NAME references
_PROCESS_ENV_RE = re.compile(r'process\.env\.([A-Z][A-Z0-9_]+)')

# File extensions to scan for env var references
_SOURCE_EXTENSIONS = {'.ts', '.tsx', '.js', '.jsx'}

# Framework vars excluded from coverage checks — these are provided by the
# runtime environment and should NOT require .env definitions
_FRAMEWORK_VARS = {
    'NODE_ENV',
    'NEXT_PUBLIC_VERCEL_URL',
    'VERCEL_URL',
    'PORT',
}

# .env file names to check for definitions (in project root)
_ENV_FILES = ['.env.local', '.env', '.env.example']

# Regex to parse KEY=value lines from .env files (skip comments and blanks)
_ENV_LINE_RE = re.compile(r'^([A-Z][A-Z0-9_]+)\s*=')


def check_env_var_coverage(project_dir: str) -> Dict[str, Any]:
    """Check that all process.env vars in source are defined in .env files.

    Scans .ts/.tsx/.js/.jsx files under src/ for `process.env.VAR` patterns.
    Reads .env.local, .env, .env.example for defined keys.
    Excludes framework vars (NODE_ENV, VERCEL_URL, etc.).

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        Dict with keys:
            - covered: bool — True if all referenced vars are defined
            - uncovered: list — Sorted list of vars not in any .env file
            - referenced: list — Sorted list of non-framework vars found in source
            - defined: list — Sorted list of vars defined in .env files
    """
    src_dir = os.path.join(project_dir, 'src')

    # Scan source files for process.env references
    # OVL-3: Use centralized scanner instead of inline os.walk
    referenced: Set[str] = set()
    if os.path.isdir(src_dir):
        src_files = read_project_files(src_dir, extensions=_SOURCE_EXTENSIONS)
        for _rel_path, content in src_files.items():
            for match in _PROCESS_ENV_RE.finditer(content):
                var_name = match.group(1)
                if var_name not in _FRAMEWORK_VARS:
                    referenced.add(var_name)

    # Read .env files for defined keys
    defined: Set[str] = set()
    for env_filename in _ENV_FILES:
        env_path = os.path.join(project_dir, env_filename)
        if not os.path.isfile(env_path):
            continue

        try:
            with open(env_path, 'r', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and blank lines
                    if not line or line.startswith('#'):
                        continue
                    m = _ENV_LINE_RE.match(line)
                    if m:
                        defined.add(m.group(1))
        except (IOError, OSError):
            continue

    # Compute coverage
    uncovered = sorted(referenced - defined)

    return {
        'covered': len(uncovered) == 0,
        'uncovered': uncovered,
        'referenced': sorted(referenced),
        'defined': sorted(defined),
    }
