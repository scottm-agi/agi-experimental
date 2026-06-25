"""
Artifact Freshness — mtime-based staleness detection for phase artifacts.

F-2 Fix: Phase 2.6 (cross-check) ran before Phase 2.3 (frontend design)
completed during a system restart, producing a stale cross-check report that
reported missing files which later appeared. There was no dependency ordering
enforcement.

This module provides:
- is_artifact_stale(artifact_path, dependency_paths): True if artifact mtime
  is OLDER than ANY dependency mtime — meaning the artifact was generated
  from outdated inputs and should be regenerated.
- get_stale_artifacts(project_dir): Checks known dependency chains and
  returns a list of stale artifacts with the dependencies that are newer.

Usage:
    from python.helpers.artifact_freshness import is_artifact_stale, get_stale_artifacts

    # Check a single artifact
    if is_artifact_stale("/project/docs/cross-check-report.md",
                         ["/project/design-tokens.json", "/project/component-spec.md"]):
        print("Cross-check report is stale — regenerate it")

    # Check all known dependency chains
    stale = get_stale_artifacts("/project")
    for entry in stale:
        print(f"{entry['artifact']} is stale because of: {entry['stale_because_of']}")
"""

import logging
import os
from typing import Dict, List

logger = logging.getLogger("agix.artifact_freshness")

# ─── Known dependency chains ──────────────────────────────────────────────
# Maps artifact (relative path) → list of dependency files (relative paths)
# that MUST exist and be OLDER than the artifact for it to be considered fresh.
#
# If the artifact's mtime is older than ANY dependency's mtime, the artifact
# is stale and should be regenerated.

DEPENDENCY_CHAINS: Dict[str, List[str]] = {
    "docs/cross-check-report.md": [
        "docs/design-tokens.json",         # RCA-461 path audit: was missing docs/ prefix
        "docs/component-spec.md",          # RCA-461 path audit: was missing docs/ prefix
        "docs/architecture-spec.md",       # RCA-461 path audit: was "docs/architecture.md" (wrong name)
        "docs/framework-research.md",
    ],
}


def is_artifact_stale(artifact_path: str, dependency_paths: list) -> bool:
    """Check if an artifact is stale relative to its dependencies.

    Returns True if the artifact's mtime is OLDER than ANY of its
    dependencies' mtimes — meaning the artifact was generated from
    outdated inputs.

    Gracefully handles missing files:
    - If the artifact doesn't exist → returns False (nothing to check)
    - If a dependency doesn't exist → skips it (can't compare against
      something that isn't on disk yet)

    Args:
        artifact_path: Absolute path to the artifact file.
        dependency_paths: List of absolute paths to dependency files.

    Returns:
        True if the artifact is stale (should be regenerated).
        False if the artifact is fresh or doesn't exist.
    """
    # If the artifact doesn't exist, it can't be stale
    if not os.path.isfile(artifact_path):
        return False

    try:
        artifact_mtime = os.path.getmtime(artifact_path)
    except OSError:
        return False

    for dep_path in dependency_paths:
        if not os.path.isfile(dep_path):
            # Dependency doesn't exist — skip it gracefully
            continue

        try:
            dep_mtime = os.path.getmtime(dep_path)
        except OSError:
            continue

        if dep_mtime > artifact_mtime:
            logger.info(
                f"[ARTIFACT FRESHNESS] {os.path.basename(artifact_path)} is STALE: "
                f"dependency {os.path.basename(dep_path)} is newer "
                f"(dep_mtime={dep_mtime:.0f} > artifact_mtime={artifact_mtime:.0f})"
            )
            return True

    return False


def get_stale_artifacts(project_dir: str) -> list:
    """Check all known dependency chains and return stale artifacts.

    Iterates over DEPENDENCY_CHAINS, resolving relative paths against
    project_dir, and returns a list of dicts describing stale artifacts.

    Args:
        project_dir: Absolute path to the project root directory.

    Returns:
        List of dicts, each with:
        - "artifact": relative path of the stale artifact
        - "artifact_mtime": mtime of the artifact
        - "stale_because_of": list of relative dependency paths that are newer
    """
    stale_results: List[dict] = []

    for artifact_rel, dep_rels in DEPENDENCY_CHAINS.items():
        artifact_abs = os.path.join(project_dir, artifact_rel)

        # Skip if the artifact doesn't exist on disk
        if not os.path.isfile(artifact_abs):
            continue

        try:
            artifact_mtime = os.path.getmtime(artifact_abs)
        except OSError:
            continue

        # Check each dependency
        stale_deps: List[str] = []
        for dep_rel in dep_rels:
            dep_abs = os.path.join(project_dir, dep_rel)
            if not os.path.isfile(dep_abs):
                continue
            try:
                dep_mtime = os.path.getmtime(dep_abs)
            except OSError:
                continue

            if dep_mtime > artifact_mtime:
                stale_deps.append(dep_rel)

        if stale_deps:
            entry = {
                "artifact": artifact_rel,
                "artifact_mtime": artifact_mtime,
                "stale_because_of": stale_deps,
            }
            stale_results.append(entry)
            logger.warning(
                f"[ARTIFACT FRESHNESS] {artifact_rel} is STALE — "
                f"newer deps: {stale_deps}"
            )

    return stale_results
