"""Test Result → Stage SM Updater.

Pipeline gap fix: Bridges test execution results (from test_result_parser.py)
into per-requirement RequirementStageSM updates via set_stage_status().

When test results include req_id mappings, this module updates the tdd
stage for each matched requirement:
  - Test passed → tdd stage = "completed"
  - Test failed → tdd stage = "failed"

This closes the gap where test pass/fail info was used by advanced_quality.py
for gate blocking decisions but never updated the per-requirement stage tracking.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger("agix.test_result_stage_updater")


def update_reqs_from_test_results(
    agent_data: dict,
    test_results: Dict[str, Any],
) -> int:
    """Update requirement tdd stages based on test execution results.

    Args:
        agent_data: The agent.data dict containing _requirements_ledger.
        test_results: Parsed test results dict with a "results" list.
            Each result may contain:
              - "req_id": str — matched requirement ID
              - "passed": bool — whether the test passed
              - "test_name": str — name of the test

    Returns:
        Number of requirements updated.
    """
    ledger = agent_data.get("_requirements_ledger", {})
    requirements = ledger.get("requirements", [])
    if not requirements:
        return 0

    results = test_results.get("results", [])
    if not results:
        return 0

    # Build req map for O(1) lookup
    req_map = {req.get("id"): req for req in requirements if req.get("id")}

    updated = 0
    for result in results:
        req_id = result.get("req_id")
        if not req_id:
            continue

        req = req_map.get(req_id)
        if not req:
            logger.debug(f"[TEST→STAGE] req_id {req_id} not found in ledger — skipping")
            continue

        passed = result.get("passed", False)
        test_name = result.get("test_name", "unknown")

        from python.helpers.requirements_stage import set_stage_status, get_stage_status

        current_tdd = get_stage_status(req, "tdd")

        # Don't downgrade verified → completed
        if current_tdd == "verified":
            continue

        if passed:
            # Only promote if not already completed/verified
            if current_tdd not in ("completed", "verified"):
                set_stage_status(req, "tdd", "completed")
                updated += 1
                logger.info(
                    f"[TEST→STAGE] {req_id} tdd stage → completed "
                    f"(test '{test_name}' passed)"
                )
        else:
            # Test failed — mark tdd as failed
            set_stage_status(req, "tdd", "failed")
            updated += 1
            logger.info(
                f"[TEST→STAGE] {req_id} tdd stage → failed "
                f"(test '{test_name}' failed)"
            )

    if updated:
        logger.info(f"[TEST→STAGE] Updated {updated} requirement tdd stages from test results")

    return updated
