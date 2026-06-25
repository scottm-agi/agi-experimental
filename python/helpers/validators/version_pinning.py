"""
Version Pinning Validator — Prevents sub-agent dependency drift.

Checks versions.lock.json against package.json to detect mismatches
where different sub-agents install conflicting versions of the same library
(e.g., Tailwind 3.x vs 4.x, Prisma 6.x vs 7.x).

Usage:
    from python.helpers.validators.version_pinning import check_version_drift
    result = check_version_drift(project_dir)
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.validators.version_pinning")


def check_version_drift(project_dir: str) -> Optional[Dict[str, Any]]:
    """Check for version drift between versions.lock.json and package.json.

    The orchestrator creates versions.lock.json during Phase 1 (Architecture)
    to pin exact versions. All sub-agents must install these exact versions.
    This validator catches drift where a sub-agent installed a different version.

    Returns:
        None if no lock file or no package.json exists (skip check)
        Dict with 'has_drift' bool and 'drifted_packages' list
    """
    lock_path = os.path.join(project_dir, "versions.lock.json")
    pkg_path = os.path.join(project_dir, "package.json")

    if not os.path.isfile(lock_path) or not os.path.isfile(pkg_path):
        return None

    try:
        with open(lock_path, "r") as f:
            locked_versions = json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.warning(f"[VERSION_PIN] Failed to parse versions.lock.json at {lock_path}")
        return None

    try:
        with open(pkg_path, "r") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    # Merge deps and devDeps
    all_deps = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
    }

    drifted: List[Dict[str, str]] = []
    for name, expected_version in locked_versions.items():
        actual_version = all_deps.get(name)
        if actual_version is None:
            drifted.append({
                "name": name,
                "expected": expected_version,
                "actual": "NOT_INSTALLED",
            })
        elif actual_version != expected_version:
            drifted.append({
                "name": name,
                "expected": expected_version,
                "actual": actual_version,
            })

    has_drift = len(drifted) > 0
    if has_drift:
        logger.warning(
            f"[VERSION_PIN] Detected version drift in {len(drifted)} packages: "
            f"{[d['name'] for d in drifted]}"
        )

    return {
        "has_drift": has_drift,
        "drifted_packages": drifted,
        "locked_count": len(locked_versions),
    }
