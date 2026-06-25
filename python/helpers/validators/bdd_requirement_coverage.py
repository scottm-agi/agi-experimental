"""
BDD Requirement Coverage Validator.

Upstream Testability Audit: Phase 2.5 validates REQ→Phase mapping
(does every requirement have a decomposition phase), but does NOT
validate REQ→BDD mapping (does every requirement have a BDD scenario).

This validator closes that gap by checking that every REQ-ID in the
requirements ledger has a corresponding mention in docs/bdd-scenarios.md.

Requirements without a BDD scenario means the code agent gets no
testable acceptance criterion — the contractor receives the work order
but no pass/fail criteria.
"""

import os
import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("agix.bdd_requirement_coverage")


def validate_bdd_requirement_coverage(
    project_dir: str,
    ledger: Optional[dict],
) -> dict:
    """Validate that every requirement has a corresponding BDD scenario.

    Scans docs/bdd-scenarios.md for REQ-ID mentions and compares against
    the requirements ledger.

    Args:
        project_dir: Path to the project directory.
        ledger: Requirements ledger dict with 'requirements' list.

    Returns:
        dict with keys:
        - total_requirements: int (count of requirements with IDs)
        - covered_count: int (REQ-IDs found in BDD file)
        - uncovered_count: int (REQ-IDs NOT found in BDD file)
        - uncovered_req_ids: list of missing REQ-ID strings
        - coverage_ratio: float (0.0 to 1.0)
    """
    empty_result = {
        "total_requirements": 0,
        "covered_count": 0,
        "uncovered_count": 0,
        "uncovered_req_ids": [],
        "coverage_ratio": 1.0,
    }

    if not ledger or not isinstance(ledger, dict):
        return empty_result

    requirements = ledger.get("requirements", [])
    if not isinstance(requirements, list):
        return empty_result

    # Extract REQ-IDs (skip entries without 'id' field)
    # ITR-30: Also skip untestable requirements (API keys, example URLs,
    # copy fragments) to match skeleton_generator.py filtering.
    try:
        from python.helpers.skeleton_generator import _is_untestable_requirement
    except ImportError:
        _is_untestable_requirement = None  # Graceful fallback

    req_ids = []
    for req in requirements:
        req_id = req.get("id")
        if req_id and isinstance(req_id, str):
            # Skip untestable requirements
            if _is_untestable_requirement and _is_untestable_requirement(req):
                continue
            req_ids.append(req_id)


    if not req_ids:
        return empty_result

    # Read BDD file
    bdd_content = ""
    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if os.path.isfile(bdd_path):
        try:
            with open(bdd_path, "r", encoding="utf-8") as f:
                bdd_content = f.read()
        except (IOError, OSError) as e:
            logger.warning(f"[BDD COVERAGE] Failed to read {bdd_path}: {e}")

    # Check which REQ-IDs appear in the BDD content
    covered = []
    uncovered = []
    for req_id in req_ids:
        if req_id in bdd_content:
            covered.append(req_id)
        else:
            uncovered.append(req_id)

    total = len(req_ids)
    coverage_ratio = len(covered) / total if total > 0 else 1.0

    if uncovered:
        logger.info(
            f"[BDD COVERAGE] {len(uncovered)}/{total} requirements lack BDD "
            f"scenarios: {uncovered[:5]}"
        )

    return {
        "total_requirements": total,
        "covered_count": len(covered),
        "uncovered_count": len(uncovered),
        "uncovered_req_ids": uncovered,
        "coverage_ratio": coverage_ratio,
    }
