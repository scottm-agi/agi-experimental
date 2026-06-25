"""
Requirements Verification — Filesystem-Based Deliverable Checking
===================================================================

Part of P0-3 Fix: Requirements Pipeline Signal Loss (GAP 3).

Replaces subordinate self-reporting with filesystem evidence. Before
transitioning a delegation to 'delegation_returned', this module checks
that the files referenced in bdd_specs and test_specs actually exist
on disk and contain the expected REQ-ID markers.

Checks:
  1. Test file exists at test_specs[].test_file
  2. Test file contains [REQ-XXX] marker string
  3. Implementation file exists (from bdd_specs[].implementation_file)
  4. Implementation file > 100 bytes (not a stub)

Usage:
    from python.helpers.requirements_verification import verify_delegation_deliverables

    result = verify_delegation_deliverables(delegation, project_dir)
    if not result["verified"]:
        # delegation transitions to 'needs_verification' instead of 'delegation_returned'
        logger.warning(f"Missing: {result['missing_tests']}, {result['missing_impls']}")
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger("agix.requirements_verification")

_REQ_ID_PATTERN = re.compile(r"\[REQ-[a-zA-Z0-9]+\]")
_MIN_IMPL_BYTES = 100  # Minimum bytes to consider a file non-stub


def verify_delegation_deliverables(
    delegation: dict,
    project_dir: str,
) -> Dict[str, Any]:
    """Verify that a delegation's deliverables exist on the filesystem.

    GAP 3 FIX: Before transitioning a delegation to 'delegation_returned',
    check that the files referenced in bdd_specs and test_specs actually exist
    and contain the expected REQ-ID markers.

    Args:
        delegation: The delegation dict from the ledger. Expected keys:
            - test_specs: List[Dict] with 'test_file' key
            - bdd_specs: List[Dict] with 'implementation_file' key
            - requirement_ids: List[str] of REQ-IDs covered
        project_dir: Root of the project directory

    Returns:
        Dict with:
          - verified: bool — True if all deliverables exist and pass checks
          - missing_tests: List[str] — test files that don't exist
          - missing_impls: List[str] — impl files that don't exist
          - missing_markers: List[str] — test files without REQ-ID markers
          - stub_files: List[str] — impl files that are stubs (<100 bytes)
          - found_tests: List[str] — test files that exist
          - found_impls: List[str] — impl files that exist
    """
    result = {
        "verified": False,
        "missing_tests": [],
        "missing_impls": [],
        "missing_markers": [],
        "stub_files": [],
        "found_tests": [],
        "found_impls": [],
    }

    test_specs = delegation.get("test_specs", [])
    bdd_specs = delegation.get("bdd_specs", [])
    requirement_ids = set(delegation.get("requirement_ids", []))

    has_specs = bool(test_specs) or bool(bdd_specs)

    if not has_specs:
        # No file specs at all — cannot verify. Mark as unverified.
        logger.warning(
            f"[REQ VERIFICATION] Delegation {delegation.get('id', '?')} has no "
            f"test_specs or bdd_specs — cannot verify deliverables"
        )
        return result

    # Check test files
    for spec in test_specs:
        test_file = spec.get("test_file", "")
        if not test_file:
            continue

        full_path = os.path.join(project_dir, test_file)
        if not os.path.exists(full_path):
            result["missing_tests"].append(test_file)
            logger.warning(
                f"[REQ VERIFICATION] Test file missing: {test_file}"
            )
        else:
            result["found_tests"].append(test_file)
            # Check for REQ-ID markers
            try:
                with open(full_path, "r", errors="replace") as f:
                    content = f.read()
                found_markers = _REQ_ID_PATTERN.findall(content)
                if not found_markers:
                    result["missing_markers"].append(test_file)
                    logger.warning(
                        f"[REQ VERIFICATION] Test file {test_file} has no [REQ-XXX] markers"
                    )
            except (IOError, OSError) as e:
                logger.warning(
                    f"[REQ VERIFICATION] Cannot read test file {test_file}: {e}"
                )
                result["missing_markers"].append(test_file)

    # Check implementation files
    for spec in bdd_specs:
        impl_file = spec.get("implementation_file", "")
        if not impl_file:
            continue

        full_path = os.path.join(project_dir, impl_file)
        if not os.path.exists(full_path):
            result["missing_impls"].append(impl_file)
            logger.warning(
                f"[REQ VERIFICATION] Implementation file missing: {impl_file}"
            )
        else:
            result["found_impls"].append(impl_file)
            # Check for stub (< 100 bytes)
            try:
                file_size = os.path.getsize(full_path)
                if file_size < _MIN_IMPL_BYTES:
                    result["stub_files"].append(impl_file)
                    logger.warning(
                        f"[REQ VERIFICATION] Implementation file {impl_file} "
                        f"is a stub ({file_size} bytes < {_MIN_IMPL_BYTES})"
                    )
            except (IOError, OSError) as e:
                logger.warning(
                    f"[REQ VERIFICATION] Cannot check size of {impl_file}: {e}"
                )

    # Determine overall verification status
    has_issues = (
        result["missing_tests"]
        or result["missing_impls"]
        or result["missing_markers"]
        or result["stub_files"]
    )

    result["verified"] = not has_issues

    if result["verified"]:
        logger.info(
            f"[REQ VERIFICATION] Delegation {delegation.get('id', '?')} "
            f"VERIFIED — {len(result['found_tests'])} test files, "
            f"{len(result['found_impls'])} impl files all present"
        )
    else:
        logger.warning(
            f"[REQ VERIFICATION] Delegation {delegation.get('id', '?')} "
            f"FAILED verification — "
            f"{len(result['missing_tests'])} missing tests, "
            f"{len(result['missing_impls'])} missing impls, "
            f"{len(result['missing_markers'])} missing markers, "
            f"{len(result['stub_files'])} stubs"
        )

    return result
