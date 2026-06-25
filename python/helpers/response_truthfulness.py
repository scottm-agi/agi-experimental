"""
Response Truthfulness Check — verify response file paths against disk reality.

G-8 (ITR-24): Orchestrator agents sometimes fabricate file paths in their
delivery responses — claiming to have created files that don't actually exist
on disk. This module provides a deterministic check that scans response text
for file paths and verifies each one exists in the project directory.

Usage:
    from python.helpers.response_truthfulness import check_response_truthfulness
    result = check_response_truthfulness(response_text, project_dir)
    if not result["passed"]:
        # Block: too many fabricated paths
        ...
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger("agix.response_truthfulness")

# Regex to match file paths in response text.
# Captures paths starting with common project directory prefixes followed by
# file extensions. Excludes paths inside URLs (http:// or https://).
_FILE_PATH_RE = re.compile(
    r'(?<!\w://)(?:^|(?<=\s)|(?<=[-*]\s))'
    r'((?:src|app|components|pages|lib|public|styles|api|utils|hooks|config|types)'
    r'[/\\][\w./@-]+\.(?:tsx|jsx|ts|js|css|json|md|html|svg|png))',
    re.MULTILINE,
)

# Pattern to detect URLs — we exclude paths that appear inside these
_URL_PREFIX_RE = re.compile(r'https?://\S*', re.IGNORECASE)

# Threshold: if more than this fraction of paths are fabricated, block.
_FABRICATION_THRESHOLD = 0.30


def check_response_truthfulness(
    response_text: str,
    project_dir: str,
) -> Dict:
    """Check that file paths mentioned in the response actually exist on disk.

    Args:
        response_text: The response text to scan for file paths.
        project_dir: The project root directory to check paths against.

    Returns:
        Dict with:
            - passed: bool — True if fabrication ratio is below threshold
            - fabricated_paths: list of paths found in response but not on disk
            - real_paths: list of paths found in response AND on disk
            - total_paths: int — total file paths found
    """
    if not response_text or not project_dir:
        return {
            "passed": True,
            "fabricated_paths": [],
            "real_paths": [],
            "total_paths": 0,
        }

    # First, mask out all URLs so we don't extract paths from them
    masked_text = _URL_PREFIX_RE.sub("", response_text)

    # Extract all file paths from the masked response
    raw_matches = _FILE_PATH_RE.findall(masked_text)

    # Deduplicate while preserving order
    seen = set()
    paths = []
    for p in raw_matches:
        # Normalize path separators
        normalized = p.replace("\\", "/").strip()
        if normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)

    if not paths:
        return {
            "passed": True,
            "fabricated_paths": [],
            "real_paths": [],
            "total_paths": 0,
        }

    # Check each path against disk
    real_paths: List[str] = []
    fabricated_paths: List[str] = []

    for path in paths:
        full_path = os.path.join(project_dir, path)
        if os.path.exists(full_path):
            real_paths.append(path)
        else:
            fabricated_paths.append(path)

    total = len(paths)
    fabricated_ratio = len(fabricated_paths) / total if total > 0 else 0.0

    passed = fabricated_ratio <= _FABRICATION_THRESHOLD

    if fabricated_paths:
        logger.warning(
            f"[RESPONSE TRUTHFULNESS] {len(fabricated_paths)}/{total} paths "
            f"fabricated ({fabricated_ratio:.0%}): "
            f"{', '.join(fabricated_paths[:5])}"
            f"{'...' if len(fabricated_paths) > 5 else ''}"
        )

    return {
        "passed": passed,
        "fabricated_paths": fabricated_paths,
        "real_paths": real_paths,
        "total_paths": total,
    }
