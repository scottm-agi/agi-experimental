"""
Verification Evidence Persistence — disk-based evidence for cross-agent access.

All verification systems (health gates, proof gates, requirement verifiers)
store evidence in-memory via agent.data. This module ALSO persists that
evidence to disk in .agix.proj/verification/ so that:
1. Subordinate agents can read parent evidence
2. Evidence survives agent restarts
3. Humans can audit verification state

File layout:
    <project>/.agix.proj/verification/
        health_evidence.json     — server health check results
        proof_evidence.json      — per-requirement proof objects
        route_evidence.json      — route reachability results
        requirements_report.json — requirement verification summary
"""
import json
import os
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("agix.evidence_persistence")

# Subdirectory within .agix.proj where evidence files live
_VERIFICATION_DIR = os.path.join(".agix.proj", "verification")


def _get_evidence_path(project_dir: str, evidence_type: str) -> str:
    """Build the full path to an evidence JSON file.

    Args:
        project_dir: Absolute path to the project directory.
        evidence_type: Evidence type name (becomes the filename).

    Returns:
        Full path to <project_dir>/.agix.proj/verification/<evidence_type>.json
    """
    return os.path.join(project_dir, _VERIFICATION_DIR, f"{evidence_type}.json")


def write_evidence(project_dir: str, evidence_type: str, data: Any) -> str:
    """Write evidence dict to .agix.proj/verification/<type>.json.

    Creates the verification directory if it doesn't exist.
    Overwrites any existing file of the same name.

    Args:
        project_dir: Absolute path to the project directory.
        evidence_type: Evidence type name (e.g., "health_evidence").
        data: JSON-serializable data to write.

    Returns:
        Path to the written file.
    """
    path = _get_evidence_path(project_dir, evidence_type)
    dir_path = os.path.dirname(path)

    os.makedirs(dir_path, exist_ok=True)

    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"[EVIDENCE] Wrote {evidence_type} to {path}")
    return path


def read_evidence(project_dir: str, evidence_type: str) -> Optional[Dict]:
    """Read evidence from disk, returns None if not found.

    Args:
        project_dir: Absolute path to the project directory.
        evidence_type: Evidence type name (e.g., "health_evidence").

    Returns:
        Parsed JSON dict, or None if file doesn't exist or parse fails.
    """
    path = _get_evidence_path(project_dir, evidence_type)

    if not os.path.isfile(path):
        return None

    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[EVIDENCE] Failed to read {path}: {e}")
        return None


def merge_evidence(
    project_dir: str,
    evidence_type: str,
    new_data: Dict,
) -> Dict:
    """Read existing evidence, merge new data, write back.

    New keys are added. Existing keys with the same name are overwritten
    by the new data. This is a shallow merge (dict.update semantics).

    Args:
        project_dir: Absolute path to the project directory.
        evidence_type: Evidence type name.
        new_data: Dict of new evidence to merge in.

    Returns:
        The merged data dict (also written to disk).
    """
    existing = read_evidence(project_dir, evidence_type) or {}
    existing.update(new_data)
    write_evidence(project_dir, evidence_type, existing)
    return existing
