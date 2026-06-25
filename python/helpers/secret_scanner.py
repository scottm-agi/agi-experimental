"""
Secret Scanner — Regex-based secret detection helper.

Scans source files for hardcoded secrets (API keys, tokens, passwords,
connection strings) to prevent accidental exposure in commits.

Key design decisions:
- .env / .env.local / .env.example / .env.template files are EXCLUDED
  (they are the correct place for secrets, and are .gitignored)
- Binary files are skipped via extension check
- node_modules, .git, __pycache__ are skipped during directory scans
- Patterns are tuned to minimize false positives on env var references
  (e.g., `os.environ.get("KEY")` should NOT match)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Pattern, Tuple


@dataclass
class SecretMatch:
    """A detected secret in a source file."""

    file_path: str
    line_number: int
    pattern_name: str
    matched_value: str
    line_content: str

    def __str__(self) -> str:
        return (
            f"[{self.pattern_name}] {self.file_path}:{self.line_number} — "
            f"matched: {self.matched_value[:40]}..."
        )


# ── Secret detection patterns ──────────────────────────────────────────
# Each pattern is (name, compiled_regex).
# Regex should match the entire secret value (group 0 or group 1).
SECRET_PATTERNS: List[Tuple[str, Pattern]] = [
    # OpenAI API keys (sk-proj-*, sk-*)
    ("openai_key", re.compile(r"""(?:"|')?(sk-(?:proj-)?[A-Za-z0-9_-]{20,})(?:"|')?""")),
    # GitHub Personal Access Tokens (gh" + "p_, gh" + "o_, ghs_, ghr_, github_pat_)
    ("github_pat", re.compile(r"""(?:"|')?(gh[phosr]_[A-Za-z0-9_]{20,})(?:"|')?""")),
    ("github_fine_pat", re.compile(r"""(?:"|')?(github_pat_[A-Za-z0-9_]{20,})(?:"|')?""")),
    # GitLab PATs
    ("gitlab_pat", re.compile(r"""(?:"|')?(glpat-[A-Za-z0-9_-]{20,})(?:"|')?""")),
    # Generic API key assignment (api_key = "value" or apiKey = "value")
    # Must have a quoted string value of 16+ chars to reduce false positives
    (
        "generic_api_key",
        re.compile(
            r"""(?:api[_-]?key|apikey)\s*[=:]\s*["']([A-Za-z0-9_\-./+]{16,})["']""",
            re.IGNORECASE,
        ),
    ),
    # Password assignments (password = "value")
    (
        "password_assignment",
        re.compile(
            r"""(?:password|passwd|pwd)\s*[=:]\s*["']([^"']{8,})["']""",
            re.IGNORECASE,
        ),
    ),
    # Database connection strings
    (
        "database_url",
        re.compile(
            r"""(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s"']+:[^\s"']+@[^\s"']+"""
        ),
    ),
    # Bearer tokens (hardcoded in headers)
    (
        "bearer_token",
        re.compile(r"""["']Bearer\s+(eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_.-]+)["']"""),
    ),
    # AWS Secret Access Key
    (
        "aws_secret_key",
        re.compile(
            r"""(?:aws_secret_access_key|aws_secret)\s*[=:]\s*["']([A-Za-z0-9/+=]{30,})["']""",
            re.IGNORECASE,
        ),
    ),
    # Stripe keys (sk_" + "live_, sk_" + "test_)
    ("stripe_key", re.compile(r"""(?:"|')?((?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,})(?:"|')?""")),
    # Slack tokens
    ("slack_token", re.compile(r"""(?:"|')?(xox[bposa]-[A-Za-z0-9-]{20,})(?:"|')?""")),
    # Private keys (-----BEGIN ... PRIVATE KEY-----)
    ("private_key", re.compile(r"""-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----""")),
]

# ── Files that are SAFE to contain secrets ─────────────────────────────
# These are the correct locations for secret values.
SAFE_FILES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
    ".env.test",
    ".env.example",
    ".env.template",
    ".env.sample",
}

# ── Extensions to SKIP (binary / non-source) ──────────────────────────
SAFE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif",
    ".mp4", ".webm", ".mov", ".avi",
    ".mp3", ".wav", ".ogg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".gz", ".tar", ".bz2", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".pyc", ".pyo", ".so", ".dylib", ".dll",
    ".exe", ".bin",
    ".lock",  # package lock files
}

# ── Directories to SKIP during directory scan ──────────────────────────
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".next", ".nuxt",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    "venv", ".venv", "env",
}


def _is_safe_file(file_path: str) -> bool:
    """Check if the file is a safe location for secrets (e.g., .env files)."""
    basename = os.path.basename(file_path)
    return basename in SAFE_FILES


def _is_binary_extension(file_path: str) -> bool:
    """Check if the file has a binary extension that should be skipped."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in SAFE_EXTENSIONS


def scan_file_content(
    content: str,
    file_path: str = "<stdin>",
) -> List[SecretMatch]:
    """Scan a string of file content for secrets.

    Args:
        content: The file content to scan.
        file_path: The file path (used for reporting, not read).

    Returns:
        List of SecretMatch objects for each detected secret.
    """
    matches: List[SecretMatch] = []

    if not content or not content.strip():
        return matches

    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        # Skip comments and empty lines
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue

        # Skip env var references (not hardcoded values)
        if "process.env." in line or "os.environ" in line or "os.getenv" in line:
            continue
        if "os.environ.get" in line:
            continue
        # Skip input() prompts
        if "input(" in line:
            continue

        for pattern_name, pattern in SECRET_PATTERNS:
            m = pattern.search(line)
            if m:
                # Use group(1) if available, else group(0)
                matched_value = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                matches.append(
                    SecretMatch(
                        file_path=file_path,
                        line_number=line_number,
                        pattern_name=pattern_name,
                        matched_value=matched_value,
                        line_content=line.rstrip(),
                    )
                )
                break  # One match per line is enough

    return matches


def scan_file(file_path: str) -> List[SecretMatch]:
    """Scan a single file for secrets.

    Automatically skips:
    - .env and related template files
    - Binary file extensions

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        List of SecretMatch objects.
    """
    if _is_safe_file(file_path):
        return []

    if _is_binary_extension(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (IOError, OSError):
        return []

    return scan_file_content(content, file_path)


def scan_directory(
    directory: str,
    max_files: int = 500,
) -> List[SecretMatch]:
    """Scan a directory tree for secrets.

    Automatically skips:
    - .env and related template files
    - Binary file extensions
    - node_modules, .git, __pycache__, etc.

    Args:
        directory: Root directory to scan.
        max_files: Maximum number of files to scan (safety limit).

    Returns:
        List of SecretMatch objects across all scanned files.
    """
    # OVL-3: Use centralized scanner instead of inline os.walk
    from python.helpers.source_scanner import list_project_files, EXCLUDE_DIRS

    abs_paths = list_project_files(
        directory,
        skip_dirs=EXCLUDE_DIRS | SKIP_DIRS,
        max_files=max_files,
    )

    all_matches: List[SecretMatch] = []
    for fpath in abs_paths:
        matches = scan_file(fpath)
        all_matches.extend(matches)

    return all_matches
