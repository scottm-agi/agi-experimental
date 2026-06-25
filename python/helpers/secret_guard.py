"""
Secret Guard — proactive write-time secret scanner.

Detects hardcoded secrets in file content BEFORE they reach the completion gate.
This eliminates the cycle: write secret → gate blocks → remove secret → gate
blocks on different check → agent re-writes secret.

Reuses patterns from validators/node.py:check_hardcoded_secrets() but as a
pure utility with no side effects — suitable for inline use in tool chains.

Usage:
    from python.helpers.secret_guard import scan_content, scan_file, should_scan_file

    # In tool_execute_after for write_to_file:
    if should_scan_file(filepath):
        secrets = scan_content(content)
        if secrets:
            # Inject warning into tool response
            ...
"""

import os
import re
import logging
from typing import Dict, List

logger = logging.getLogger("agix.secret_guard")

# ─── Secret Patterns ───────────────────────────────────────────────────
# Each pattern is a tuple of (name, compiled_regex, description)

SECRET_PATTERNS = [
    (
        "openrouter_key",
        re.compile(r'sk-or-' + 'v1-[a-zA-Z0-9]{10,}'),
        "OpenRouter API key",
    ),
    (
        "openai_key",
        re.compile(r'sk-(?:proj-)?[a-zA-Z0-9]{20,}'),
        "OpenAI API key",
    ),
    (
        "github_token",
        re.compile(r'gh[ps]_[a-zA-Z0-9]{30,}'),
        "GitHub token",
    ),
    (
        "generic_bearer",
        re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.]{30,}'),
        "Bearer token",
    ),
    (
        "unresolved_template",
        re.compile(r'\{\{SECRET_[A-Z_]+\}\}'),
        "Unresolved secret template variable",
    ),
    # ITR-21 F-15: Hardcoded password/secret patterns in source code
    (
        "hardcoded_password",
        re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\'][^"\']{4,}["\']', re.IGNORECASE),
        "Hardcoded password in source code",
    ),
    (
        "hardcoded_secret",
        re.compile(r'(?:secret|api_secret|app_secret)\s*[:=]\s*["\'][^"\']{4,}["\']', re.IGNORECASE),
        "Hardcoded secret value in source code",
    ),
    (
        "hardcoded_auth_token",
        re.compile(r'(?:auth_token|access_token|api_token)\s*[:=]\s*["\'][^"\']{8,}["\']', re.IGNORECASE),
        "Hardcoded auth/access token in source code",
    ),
]

# Patterns that indicate safe references (not actual secrets)
SAFE_PATTERNS = [
    re.compile(r'process\.env\.'),
    re.compile(r'os\.environ'),
    re.compile(r'your[-_]api[-_]key[-_]here', re.IGNORECASE),
    re.compile(r'your[-_].*[-_]here', re.IGNORECASE),
    re.compile(r'<your[-_]'),
    re.compile(r'REPLACE_WITH_'),
    re.compile(r'TODO:?\s*replace', re.IGNORECASE),
]

# File extensions that should be scanned
SCANNABLE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".rb", ".go", ".java", ".rs",
    ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".scss",
    ".md", ".txt",
}

# Files that should NEVER be scanned (secrets belong there)
EXCLUDED_FILENAMES = {
    ".env", ".env.local", ".env.development", ".env.production",
    ".env.staging", ".env.test", ".env.example",
}


def should_scan_file(filepath: str) -> bool:
    """Check if a file should be scanned for secrets.

    Args:
        filepath: Path to the file (can be relative or absolute)

    Returns:
        True if the file should be scanned
    """
    basename = os.path.basename(filepath)

    # Excluded files (secrets belong there)
    if basename in EXCLUDED_FILENAMES:
        return False
    # Also catch .env.anything patterns
    if basename.startswith(".env"):
        return False

    # Check extension
    _, ext = os.path.splitext(basename)
    if ext and ext.lower() in SCANNABLE_EXTENSIONS:
        return True

    # If no extension but not excluded, scan anyway
    return bool(ext)


def scan_content(content: str) -> List[Dict]:
    """Scan content string for hardcoded secrets.

    Args:
        content: The file content to scan

    Returns:
        List of dicts with keys: secret_type, line_number, matched_text
    """
    if not content:
        return []

    results = []
    lines = content.split("\n")

    for line_num, line in enumerate(lines, start=1):
        # Skip lines that are safe references
        if any(safe.search(line) for safe in SAFE_PATTERNS):
            continue

        for secret_name, pattern, description in SECRET_PATTERNS:
            matches = pattern.findall(line)
            for match in matches:
                # Mask the secret for logging
                masked = match[:8] + "..." if len(match) > 8 else match
                results.append({
                    "secret_type": secret_name,
                    "line_number": line_num,
                    "matched_text": masked,
                    "description": description,
                })

    if results:
        logger.warning(
            f"[SECRET GUARD] Found {len(results)} potential secrets "
            f"in content ({len(lines)} lines)"
        )

    return results


def scan_file(filepath: str) -> List[Dict]:
    """Scan a file for hardcoded secrets.

    Args:
        filepath: Absolute path to the file

    Returns:
        List of dicts with keys: secret_type, line_number, matched_text
    """
    if not os.path.isfile(filepath):
        return []

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return scan_content(content)
    except Exception as e:
        logger.error(f"[SECRET GUARD] Error scanning {filepath}: {e}")
        return []
