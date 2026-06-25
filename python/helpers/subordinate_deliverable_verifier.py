"""
Subordinate Deliverable Verifier — Anti-Hallucination Layer

Utility module that cross-references file paths claimed in subordinate
agent return messages against actual filesystem state.

5-Why RCA (2026-04-24, Iteration 152):
  Root cause: No automated verification between call_subordinate returning
  and the orchestrator processing the result. Agents can claim "I created
  Navbar.tsx" without the file existing on disk, and the orchestrator trusts
  the message verbatim.

This module provides:
  - extract_claimed_files(): Parse file paths from text
  - verify_deliverables(): Cross-reference against filesystem
  - VerificationReport: Structured report with pass/fail per file
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List


# File extensions we care about verifying
VERIFIABLE_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js",
    ".css", ".scss", ".less",
    ".json", ".md", ".mdx",
    ".html", ".py", ".yaml", ".yml",
    ".prisma", ".sql", ".env",
    ".toml", ".cfg", ".ini",
}

# Patterns to extract file paths from text
# Matches paths like: src/components/Navbar.tsx, docs/framework-research.md
# Both backtick-quoted (`path/to/file.ext`) and plain text
_BACKTICK_FILE_RE = re.compile(
    r'`([^`\s]+\.(?:' + '|'.join(ext.lstrip('.') for ext in VERIFIABLE_EXTENSIONS) + r'))`'
)
_PLAIN_FILE_RE = re.compile(
    r'(?:^|[\s\-\*\•])(' +
    r'(?:[/\w\.\-\[\]]+/)*' +      # Optional directory path segments
    r'[\w\.\-\[\]]+\.' +            # Filename
    r'(?:' + '|'.join(ext.lstrip('.') for ext in VERIFIABLE_EXTENSIONS) + r')' +
    r')(?:\s|$|[,;:\)\]\}])',
    re.MULTILINE
)

# Patterns to EXCLUDE (not file paths)
_URL_RE = re.compile(r'https?://')
_PACKAGE_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')


def extract_claimed_files(text: str) -> List[str]:
    """Extract file paths from subordinate return text.

    Looks for paths with verifiable extensions in both backtick-quoted
    and plain text contexts. Deduplicates and filters out URLs.

    Args:
        text: The subordinate's return message text

    Returns:
        Deduplicated list of file paths found in the text
    """
    found: set = set()

    # Extract backtick-quoted paths (higher confidence)
    for match in _BACKTICK_FILE_RE.finditer(text):
        path = match.group(1).strip()
        if _is_valid_file_path(path):
            found.add(_normalize_path(path))

    # Extract plain-text paths (lower confidence, more filtering needed)
    for match in _PLAIN_FILE_RE.finditer(text):
        path = match.group(1).strip()
        if _is_valid_file_path(path):
            found.add(_normalize_path(path))

    return sorted(found)


def _is_valid_file_path(path: str) -> bool:
    """Check if a string looks like a real file path (not URL, version, etc)."""
    if _URL_RE.search(path):
        return False
    if _PACKAGE_VERSION_RE.match(path):
        return False
    # Must have at least one path-like character
    if len(path) < 3:
        return False
    # Filter out common false positives
    if path.startswith("http") or path.startswith("ftp"):
        return False
    return True


def _normalize_path(path: str) -> str:
    """Normalize a file path for deduplication.

    Strips leading /agix/usr/projects/*/  prefix if present,
    and ensures consistent forward slashes.
    """
    path = path.replace("\\", "/")

    # Strip common sandbox prefixes
    prefixes_to_strip = [
        "/agix/usr/projects/",
        "/usr/projects/",
    ]
    for prefix in prefixes_to_strip:
        if path.startswith(prefix):
            # Strip prefix + project name dir
            remainder = path[len(prefix):]
            # Skip the project directory name
            if "/" in remainder:
                path = remainder.split("/", 1)[1]
            break

    # Strip leading slash if still present (make relative)
    if path.startswith("/"):
        path = path.lstrip("/")

    return path


# Scaffold detection patterns — files that exist but contain only boilerplate
_SCAFFOLD_PATTERNS_TSX = [
    re.compile(r'return\s*\(?\s*<div\s*/?\s*>\s*\)?', re.MULTILINE),
    re.compile(r'return\s*\(?\s*<div>\s*</div>\s*\)?', re.MULTILINE),
    re.compile(r'return\s*\(?\s*<>\s*</>\s*\)?', re.MULTILINE),
]
_MIN_MEANINGFUL_LINES = 5


def is_scaffold_content(content: str, filename: str) -> bool:
    """Detect if file content is scaffold boilerplate with no real implementation.

    Catches the failure mode where a file EXISTS on disk but contains only
    the default scaffold template (e.g., `export default function Home() { return <div /> }`).

    Args:
        content: The file's text content
        filename: The filename (used to select detection strategy)

    Returns:
        True if the content appears to be scaffold-only
    """
    if not content or not content.strip():
        return True

    # Count meaningful lines (non-empty, non-comment)
    meaningful_lines = [
        line for line in content.strip().splitlines()
        if line.strip() and not line.strip().startswith('//') and not line.strip().startswith('#')
    ]

    if len(meaningful_lines) < _MIN_MEANINGFUL_LINES:
        return True

    # For TSX/JSX files, check for empty-div-return patterns
    if filename.endswith(('.tsx', '.jsx')):
        for pattern in _SCAFFOLD_PATTERNS_TSX:
            if pattern.search(content):
                # Found a scaffold return pattern — but only if the file is small
                # (a large component with one empty div return in a sub-component is OK)
                if len(meaningful_lines) < 10:
                    return True

    return False


@dataclass
class VerificationReport:
    """Result of verifying claimed deliverables against filesystem."""

    verified: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    scaffold_only: List[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """Fraction of claimed files that actually exist."""
        total = len(self.verified) + len(self.missing)
        if total == 0:
            return 1.0  # Nothing claimed, nothing missing
        return len(self.verified) / total

    def format(self) -> str:
        """Format the report as a human-readable string for injection."""
        total = len(self.verified) + len(self.missing)
        if total == 0:
            return "📋 DELIVERABLE VERIFICATION: No file paths found in subordinate response."

        lines = ["📋 DELIVERABLE VERIFICATION:"]

        for f in self.verified:
            if f in self.scaffold_only:
                lines.append(f"  ⚠️ {f} — EXISTS (SCAFFOLD ONLY — no real content)")
            else:
                lines.append(f"  ✅ {f} — EXISTS")

        for f in self.missing:
            lines.append(f"  ❌ {f} — MISSING")

        pct = int(self.pass_rate * 100)
        lines.append(f"  Pass rate: {pct}% ({len(self.verified)}/{total} files verified)")

        if self.scaffold_only:
            lines.append(
                f"  ⚠️ SCAFFOLD WARNING: {len(self.scaffold_only)} file(s) exist but contain "
                f"only scaffold boilerplate. These need real implementation."
            )

        if self.missing:
            lines.append(
                "  ⚠️ WARNING: Subordinate claimed files that do not exist on disk. "
                "Re-dispatch required for missing deliverables."
            )

        return "\n".join(lines)


def verify_deliverables(
    project_dir: str,
    claimed_files: List[str],
) -> VerificationReport:
    """Verify claimed file paths against actual filesystem.

    Args:
        project_dir: Absolute path to the project root directory
        claimed_files: List of file paths (relative or absolute) to verify

    Returns:
        VerificationReport with verified and missing file lists
    """
    report = VerificationReport()

    for file_path in claimed_files:
        # Handle absolute paths that include the project_dir prefix
        if os.path.isabs(file_path):
            if file_path.startswith(project_dir):
                # Already absolute and under project_dir — check directly
                check_path = file_path
            else:
                # Absolute but different root — try stripping to relative
                check_path = os.path.join(project_dir, os.path.basename(file_path))
        else:
            check_path = os.path.join(project_dir, file_path)

        if os.path.exists(check_path):
            report.verified.append(file_path)
            # Check for scaffold content
            try:
                with open(check_path, 'r', errors='ignore') as f:
                    content = f.read()
                basename = os.path.basename(check_path)
                if is_scaffold_content(content, basename):
                    report.scaffold_only.append(file_path)
            except Exception:
                pass  # Can't read file — skip scaffold check
        else:
            report.missing.append(file_path)

    return report
