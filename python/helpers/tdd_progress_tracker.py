"""TDD Progress Tracker — monitors test run improvement over time.

D-1: Tracks consecutive test runs and determines if the agent is making
progress (more tests passing or fewer failing) or stuck (same results
repeated 3+ times).

Uses parse_test_result() from test_execution_gate.py to avoid duplicating
test output parsing logic.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from python.helpers.test_result_parser import parse_test_result

logger = logging.getLogger("agix.tdd_progress_tracker")


class TDDProgressTracker:
    """Tracks test run results and detects progress/stagnation.

    Usage:
        tracker = TDDProgressTracker()
        result = tracker.record_test_run(test_output_str)
        if not tracker.is_making_progress():
            # Agent is stuck — escalate or change strategy
    """

    def __init__(self) -> None:
        """Initialize with empty run history."""
        self._runs: List[Dict[str, Any]] = []

    def record_test_run(self, output: str) -> Dict[str, Any]:
        """Parse test output and record the result.

        Args:
            output: Raw stdout/stderr from the test runner.

        Returns:
            Dict with 'tests_passed', 'tests_failed', 'confidence' keys
            (same shape as parse_test_result()).
        """
        result = parse_test_result(output)
        self._runs.append(result)
        logger.info(
            "[TDD PROGRESS] Run #%d: %d passed, %d failed (confidence=%s)",
            len(self._runs),
            result.get("tests_passed", 0),
            result.get("tests_failed", 0),
            result.get("confidence", "low"),
        )
        return result

    def is_making_progress(self) -> bool:
        """Check if the agent is making progress across recent test runs.

        Rules:
          - First run: always considered progress (baseline established).
          - Improvement detected if:
              * More tests passing than any previous run, OR
              * Fewer tests failing than previous run.
          - Stuck if the last 3 runs have identical pass/fail counts.

        Returns:
            True if making progress, False if stuck.
        """
        if len(self._runs) <= 1:
            return True  # First run = always progress

        # Check for stuck: 3 consecutive identical results
        if len(self._runs) >= 3:
            last_3 = self._runs[-3:]
            pass_counts = [r.get("tests_passed", 0) for r in last_3]
            fail_counts = [r.get("tests_failed", 0) for r in last_3]

            if (
                len(set(pass_counts)) == 1
                and len(set(fail_counts)) == 1
            ):
                logger.warning(
                    "[TDD PROGRESS] STUCK: Last 3 runs identical "
                    "(passed=%d, failed=%d)",
                    pass_counts[0],
                    fail_counts[0],
                )
                return False

        # Compare latest run vs previous run
        current = self._runs[-1]
        previous = self._runs[-2]

        current_passed = current.get("tests_passed", 0)
        previous_passed = previous.get("tests_passed", 0)
        current_failed = current.get("tests_failed", 0)
        previous_failed = previous.get("tests_failed", 0)

        # Progress if more passing OR fewer failing
        if current_passed > previous_passed:
            return True
        if current_failed < previous_failed:
            return True

        return False

    @property
    def run_count(self) -> int:
        """Number of recorded test runs."""
        return len(self._runs)

    @property
    def latest_result(self) -> Optional[Dict[str, Any]]:
        """Most recent test run result, or None if no runs recorded."""
        return self._runs[-1] if self._runs else None
