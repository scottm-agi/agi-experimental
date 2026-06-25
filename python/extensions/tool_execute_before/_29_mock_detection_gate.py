"""
Mock API Detection Gate — tool_execute_before extension.

F-1 Fix: Code agents write mock/stub implementations (returning hardcoded data)
when they should implement real API calls. This gate detects mock patterns in
lib/ and src/lib/ files when API keys are present in .env, firing only during
Phase 4.9+ (build-freeze and later).

Architecture:
- Hook: tool_execute_before (fires before call_subordinate delegations)
- Only fires during Phase 4.9+ — mocks are acceptable during Phase 3
- Scans lib/ and src/lib/ for mock patterns (framework-agnostic)
- Cross-references with .env file — needs BOTH api keys AND mock patterns
- Returns ADVISORY response (break_loop=False) — warns but doesn't block
- Excludes: node_modules, test files, non-source files
"""
from __future__ import annotations

import os
import re
import logging
from typing import Dict, List, Optional, Tuple

from python.helpers.extension import Extension
from python.helpers.phase_category import PhaseCategory
from python.helpers.tool import Response
from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS

logger = logging.getLogger("agix.mock_detection_gate")

# ─── Mock Pattern Detection ────────────────────────────────────────────

# Patterns that indicate mock/stub implementations in source files.
# Framework-agnostic: works for Next.js, Vite, Flask, Express, etc.
MOCK_PATTERNS = [
    re.compile(r'TODO[:\s].*[Ii]mplement', re.IGNORECASE),        # TODO: Implement real API
    re.compile(r'\bhardcoded\b', re.IGNORECASE),                   # // hardcoded
    re.compile(r'\bMock\s+data\b', re.IGNORECASE),                 # Mock data
    re.compile(r'console\.log\s*\(.*\bsend\b', re.IGNORECASE),    # console.log("send email")
    re.compile(r'//\s*TODO\b'),                                    # // TODO
    re.compile(r'#\s*TODO\b'),                                     # # TODO (Python)
    re.compile(r'\breturn\s+\{[^}]*\}\s*;?\s*//\s*stub', re.IGNORECASE),  # return {}; // stub
    re.compile(r'\breturn\s+\[\s*\]\s*;?\s*//\s*(mock|stub|fake|dummy)', re.IGNORECASE),  # return []; // mock
]

# Source extensions to scan
_SOURCE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".vue", ".svelte", ".astro",
}

# Test file patterns to exclude
_TEST_FILE_PATTERN = re.compile(
    r'(?:__tests__|tests?/|\.test\.|\.spec\.|test_|_test\.)',
    re.IGNORECASE,
)

# DUP-3: Uses shared DEFAULT_PROJECT_SKIP_DIRS from project_scan_constants.
_SKIP_DIRS = DEFAULT_PROJECT_SKIP_DIRS

# ─── API Key Detection ──────────────────────────────────────────────────

# Env var name patterns that indicate API keys/secrets
_API_KEY_PATTERNS = [
    re.compile(r'_KEY\s*=', re.IGNORECASE),
    re.compile(r'_SECRET\s*=', re.IGNORECASE),
    re.compile(r'_TOKEN\s*=', re.IGNORECASE),
    re.compile(r'_API\s*=', re.IGNORECASE),
    re.compile(r'API_KEY\s*=', re.IGNORECASE),
    re.compile(r'SECRET_KEY\s*=', re.IGNORECASE),
]

# Env vars that are NOT API keys (database URLs, ports, etc.)
_NON_API_KEY_PATTERNS = [
    re.compile(r'DATABASE_URL\s*=', re.IGNORECASE),
    re.compile(r'PORT\s*=', re.IGNORECASE),
    re.compile(r'NODE_ENV\s*=', re.IGNORECASE),
    re.compile(r'NEXT_PUBLIC_URL\s*=', re.IGNORECASE),
    re.compile(r'BASE_URL\s*=', re.IGNORECASE),
]

# Minimum phase number to activate the gate (4.9 = build-freeze)
_MIN_PHASE = 4.9


def _parse_phase(phase_str: str) -> float:
    """Parse phase string to float for comparison.

    Handles formats: '3', '4.9', '5', '4.9.1', etc.

    Args:
        phase_str: Phase string from agent.data['_current_phase']

    Returns:
        Float phase number. Returns 0.0 if unparseable.
    """
    if not phase_str:
        return 0.0
    try:
        # Handle '4.9.1' → take first two components only
        parts = phase_str.split(".")
        if len(parts) >= 2:
            return float(f"{parts[0]}.{parts[1]}")
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _has_api_keys(env_path: str) -> bool:
    """Check if .env file contains API key-like environment variables.

    Args:
        env_path: Path to .env file

    Returns:
        True if at least one API key pattern is found
    """
    if not os.path.isfile(env_path):
        return False

    try:
        with open(env_path, "r", errors="replace") as f:
            content = f.read()
    except (IOError, OSError):
        return False

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Skip known non-API-key env vars
        if any(p.search(line) for p in _NON_API_KEY_PATTERNS):
            continue

        # Check for API key patterns
        if any(p.search(line) for p in _API_KEY_PATTERNS):
            return True

    return False


def _scan_file_for_mocks(filepath: str) -> List[Dict[str, str]]:
    """Scan a single source file for mock/stub patterns.

    Args:
        filepath: Absolute path to source file

    Returns:
        List of dicts with 'line', 'pattern', 'text' for each match
    """
    matches = []
    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except (IOError, OSError):
        return matches

    for i, line in enumerate(lines, start=1):
        for pattern in MOCK_PATTERNS:
            if pattern.search(line):
                matches.append({
                    "line": i,
                    "pattern": pattern.pattern[:40],
                    "text": line.strip()[:100],
                })
                break  # One match per line is enough

    return matches


def _scan_project_for_mocks(project_dir: str) -> Dict[str, List[Dict]]:
    """Scan lib/ and src/lib/ directories for mock patterns.

    Excludes: node_modules, test files, non-source files.

    Args:
        project_dir: Absolute path to project directory

    Returns:
        Dict mapping relative filepath to list of mock matches
    """
    results = {}
    scan_dirs = []

    # G-22 FIX: Expanded scan dirs — previously only lib/ dirs, missing mocks
    # in app/, pages/, src/app/ where most Next.js/React code lives.
    for subdir in [
        "lib", "src/lib", "src/libs", "libs",
        "app", "src/app", "pages", "src/pages",
        "components", "src/components",
    ]:
        full_path = os.path.join(project_dir, subdir)
        if os.path.isdir(full_path):
            scan_dirs.append((subdir, full_path))

    for rel_base, abs_base in scan_dirs:
        for root, dirs, files in os.walk(abs_base):
            # Prune skip directories
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

            # Check root path doesn't contain skip dirs
            rel_root = os.path.relpath(root, project_dir)
            if any(skip in rel_root.split(os.sep) for skip in _SKIP_DIRS):
                continue

            for filename in files:
                _, ext = os.path.splitext(filename)
                if ext not in _SOURCE_EXTENSIONS:
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, project_dir)

                # Skip test files
                if _TEST_FILE_PATTERN.search(rel_path):
                    continue

                matches = _scan_file_for_mocks(filepath)
                if matches:
                    results[rel_path] = matches

    return results


class MockDetectionGate(Extension):
    # Context-aware: code agents, response tool
    PROFILES = {"code"}
    TOOLS = frozenset({"call_subordinate"})
    CATEGORIES = {
        PhaseCategory.INTEGRATION,
        PhaseCategory.VERIFICATION,
        PhaseCategory.DELIVERY,
    }

    """Detect mock/stub API implementations during Phase 4.9+ build-freeze.

    Cross-references .env API keys with lib/ source files to identify
    integrations that have API keys configured but implementations that
    are still using mock/hardcoded data.
    """

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        """Run before call_subordinate — check for mock implementations."""
        # Only fires for call_subordinate
        if not tool_name or tool_name.lower() != "call_subordinate":
            return None

        if not tool_args or not isinstance(tool_args, dict):
            return None

        # Get project directory
        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return None

        # Phase gate — only fire during Phase 4.9+ (build-freeze and later)
        phase_str = str(self.agent.data.get("_current_phase", ""))
        phase = _parse_phase(phase_str)
        if phase < _MIN_PHASE:
            return None

        # Check .env for API keys
        env_path = os.path.join(project_dir, ".env")
        if not _has_api_keys(env_path):
            return None

        # Scan lib/ and src/lib/ for mock patterns
        mock_results = _scan_project_for_mocks(project_dir)
        if not mock_results:
            return None

        # Build advisory message
        file_reports = []
        for filepath, matches in mock_results.items():
            match_lines = ", ".join(f"L{m['line']}" for m in matches[:3])
            file_reports.append(f"  - `{filepath}` ({match_lines})")

        files_list = "\n".join(file_reports)
        message = (
            f"⚠️ **Mock API Implementations Detected** (Phase {phase_str}) — "
            f"{len(mock_results)} file(s) in lib/ contain mock/stub patterns "
            f"but .env has API keys configured:\n\n"
            f"{files_list}\n\n"
            f"**These files should use REAL API calls** since API keys are available. "
            f"Replace hardcoded/mock data with actual service integrations.\n\n"
            f"Advisory — the delegation will proceed, but subordinate should "
            f"prioritize replacing these mocks with real implementations."
        )

        logger.warning(
            f"[MOCK DETECTION GATE] Phase {phase_str}: "
            f"{len(mock_results)} mock file(s) detected with API keys present"
        )

        # Inject warning into agent history for visibility
        try:
            await self.agent.hist_add_warning(
                f"🔍 Mock Detection: {len(mock_results)} file(s) with mock patterns "
                f"found while API keys exist in .env. Fix before delivery."
            )
        except Exception:
            pass

        return Response(
            message=message,
            break_loop=False,  # Advisory only — delegation proceeds
        )
