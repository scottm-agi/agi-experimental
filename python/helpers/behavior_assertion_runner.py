"""
Behavior Assertion Runner — Scans generated source code for behavioral
requirements extracted by the prompt contract parser.

U-3/U-4 Gate Integration (RCA-302): This module bridges the gap between
extracted behaviors/compliance/journeys and the orchestrator gate. It
takes a list of behavior dicts (each with a `verify_pattern` regex) and
scans all source files in the project directory for matches.

Results include per-behavior pass/fail and an overall pass_rate scored
against a configurable threshold (default: 0.7).

Escape Hatch Policy:
    When integrating into the orchestrator gate, this module MUST be:
    - Registered as NON-CRITICAL (critical=False) so the escape hatch
      skips it after MAX_INTEGRATION_BLOCKS.
    - OR if registered as critical, the per-check circuit breaker
      (MAX_CRITICAL_CHECK_BLOCKS=3) ensures it force-allows after 3 blocks.
    - NEVER used as a hard blocker without a circuit breaker. Progress
      to completion is always more important than perfect compliance.

Usage:
    from python.helpers.behavior_assertion_runner import run_behavior_assertions

    result = run_behavior_assertions(behaviors, project_path)
    # Returns: {"passed": True/False, "pass_rate": 0.85, "details": [...]}
"""

import logging
import os
import re
from typing import Dict, List

from python.helpers.source_scanner import get_combined_source_text, SOURCE_EXTENSIONS

logger = logging.getLogger("agix.behavior_assertion_runner")

# OVL-3: Scanning now delegated to source_scanner.list_project_files /
# get_combined_source_text(). Local _SCANNABLE_EXTENSIONS and _SKIP_DIRS
# removed — canonical constants live in source_scanner / project_scan_constants.


def _scan_project_source(project_path: str) -> str:
    """Walk the project directory and concatenate all scannable source files.

    Returns a single string of all source content for regex matching.
    OVL-3: Now delegates to source_scanner.get_combined_source_text().
    """
    if not project_path or not os.path.isdir(project_path):
        return ""
    return get_combined_source_text(project_path)


def run_behavior_assertions(
    behaviors: List[Dict],
    project_path: str,
    threshold: float = 0.7,
) -> Dict:
    """Scan project source code for behavioral requirement patterns.

    Args:
        behaviors: List of behavior dicts, each with at minimum:
            - name: Description of the behavior
            - verify_pattern: Regex pattern to search for in source
        project_path: Absolute path to the project root directory.
        threshold: Minimum pass_rate to consider the check passed (0.0-1.0).
                   Default is 0.7 (70% of behaviors must be found).

    Returns:
        {
            "passed": bool,       # True if pass_rate >= threshold
            "pass_rate": float,   # 0.0-1.0 ratio of found behaviors
            "total": int,         # Total behaviors checked
            "found_count": int,   # Number of behaviors found in source
            "missing_count": int, # Number of behaviors NOT found
            "details": [          # Per-behavior results
                {
                    "name": str,
                    "verify_pattern": str,
                    "found": bool,
                    "match_snippet": str | None,  # First match if found
                },
                ...
            ]
        }
    """
    if not behaviors:
        return {
            "passed": True,
            "pass_rate": 1.0,
            "total": 0,
            "found_count": 0,
            "missing_count": 0,
            "details": [],
        }

    # Concatenate all source code for scanning
    source_text = _scan_project_source(project_path)

    details: List[Dict] = []
    found_count = 0

    for behavior in behaviors:
        name = behavior.get("name", "unknown")
        pattern = behavior.get("verify_pattern", "")

        if not pattern:
            details.append({
                "name": name,
                "verify_pattern": pattern,
                "found": False,
                "match_snippet": None,
            })
            continue

        try:
            match = re.search(pattern, source_text)
            if match:
                found_count += 1
                # Extract a snippet around the match for evidence
                start = max(0, match.start() - 30)
                end = min(len(source_text), match.end() + 30)
                snippet = source_text[start:end].replace("\n", " ").strip()
                details.append({
                    "name": name,
                    "verify_pattern": pattern,
                    "found": True,
                    "match_snippet": snippet[:120],
                })
            else:
                details.append({
                    "name": name,
                    "verify_pattern": pattern,
                    "found": False,
                    "match_snippet": None,
                })
        except re.error as e:
            logger.warning(f"Invalid regex for behavior '{name}': {e}")
            details.append({
                "name": name,
                "verify_pattern": pattern,
                "found": False,
                "match_snippet": f"REGEX ERROR: {e}",
            })

    total = len(behaviors)
    pass_rate = found_count / total if total > 0 else 1.0
    passed = pass_rate >= threshold

    logger.info(
        f"[BEHAVIOR ASSERTION RUNNER] {found_count}/{total} behaviors found "
        f"(pass_rate={pass_rate:.2f}, threshold={threshold}, "
        f"passed={passed})"
    )

    return {
        "passed": passed,
        "pass_rate": round(pass_rate, 4),
        "total": total,
        "found_count": found_count,
        "missing_count": total - found_count,
        "details": details,
    }
