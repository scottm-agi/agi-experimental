"""
DelegationRetestDetector — detects when the orchestrator delegates to the
same test/verification profile consecutively without a code-fix delegation
in between.

This is the deterministic trigger for the supervisor's goal-alignment check.
When this fires, the supervisor knows the orchestrator is retesting without
fixing and should intervene with a redirect_approach.

Root cause addressed: Iter73 E2E-Fix Loop Gap.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from python.helpers.detectors.base import AgentState, DetectedPattern, PatternDetector
from python.helpers.loop_prevention import PatternType

logger = logging.getLogger(__name__)

# Profiles that represent "testing/verification" work
TEST_PROFILES = {"e2e", "browser", "qa", "test", "testing", "review"}

# Profiles that represent "fixing/building" work — a delegation to one of
# these between two test delegations means the orchestrator IS fixing
FIX_PROFILES = {"code", "architect", "debug", "fix"}

# Pattern to detect quality fail signals in delegation results
RE_QUALITY_FAIL = re.compile(r"QUALITY:\s*FAIL", re.IGNORECASE)
RE_QUALITY_PASS = re.compile(r"QUALITY:\s*PASS", re.IGNORECASE)


class DelegationRetestDetector(PatternDetector):
    """
    Detects when the orchestrator repeatedly delegates to the same
    test-type profile without a code-fix delegation in between.

    Triggers on:
      1. Same test profile delegated 2+ consecutive times without a
         fix-profile delegation in between (severity: high)
      2. 3+ consecutive retests → severity: critical
      3. QUALITY: FAIL in result AND retesting same profile increases
         confidence to 0.95

    Does NOT trigger on:
      - fix-type profiles being called consecutively (legitimate iterative work)
      - test profile following a fix profile (correct flow)
      - single delegation (not enough data)
      - QUALITY: PASS followed by same profile (revalidation is fine)
    """

    @property
    def pattern_type(self) -> PatternType:
        return PatternType.DELEGATION_RETEST_LOOP

    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Extract only call_subordinate calls from recent tool history
        delegations = self._extract_delegations(state.recent_tool_calls)

        if len(delegations) < 2:
            return None

        # Find consecutive test-profile delegations without a fix in between
        consecutive_test = self._find_consecutive_test_retests(delegations)

        if consecutive_test is None:
            return None

        profile, count, had_quality_fail = consecutive_test

        # QUALITY: PASS → PASS retesting is fine (revalidation)
        if not had_quality_fail:
            return None

        # Determine severity
        if count >= 3:
            severity = "critical"
        else:
            severity = "high"

        # Determine confidence
        if had_quality_fail:
            confidence = round(min(0.70 + (count * 0.10), 0.99), 2)
        else:
            confidence = round(min(0.60 + (count * 0.10), 0.95), 2)

        description = (
            f"Orchestrator delegated to '{profile}' profile {count} consecutive times "
            f"without a code-fix delegation in between. "
            f"{'QUALITY: FAIL detected in results — fixes are needed, not retesting.' if had_quality_fail else ''}"
        )

        suggestion = (
            f"Stop delegating to '{profile}'. Delegate to the 'code' agent (profile='code') "
            f"to fix the specific issues from the last {profile} failure report, THEN re-run "
            f"{profile} after the code fixes are applied."
        )

        return self._create_pattern(
            state=state,
            confidence=confidence,
            severity=severity,
            description=description,
            metadata={
                "profile": profile,
                "consecutive_count": count,
                "had_quality_fail": had_quality_fail,
                "suggestion": suggestion,
            },
        )

    def _extract_delegations(self, tool_calls: list) -> list:
        """
        Extract delegation tool calls from the tool call history.
        Filters to only call_subordinate calls and extracts profile.
        Non-delegation calls are skipped (they don't reset the sequence).
        """
        delegations = []
        for call in tool_calls:
            tool_name = call.get("tool_name", "")
            if tool_name != "call_subordinate":
                continue

            # Extract profile from args
            args = call.get("tool_args") or call.get("arguments") or {}
            profile = args.get("profile", "default").lower().strip()
            result = call.get("result", "")

            delegations.append({
                "profile": profile,
                "result": result,
                "has_quality_fail": bool(RE_QUALITY_FAIL.search(str(result))),
                "has_quality_pass": bool(RE_QUALITY_PASS.search(str(result))),
            })

        return delegations

    def _find_consecutive_test_retests(self, delegations: list) -> Optional[tuple]:
        """
        Find the longest run of consecutive test-profile delegations
        without a fix-profile in between.

        Returns (profile, count, had_quality_fail) or None.
        """
        if not delegations:
            return None

        # Walk backwards to find the most recent consecutive run
        last_profile = delegations[-1]["profile"]

        # Only trigger for test-type profiles
        if last_profile not in TEST_PROFILES:
            return None

        count = 0
        had_quality_fail = False

        # Walk backwards through delegations
        for d in reversed(delegations):
            if d["profile"] == last_profile:
                count += 1
                if d["has_quality_fail"]:
                    had_quality_fail = True
            elif d["profile"] in FIX_PROFILES:
                # A fix delegation breaks the consecutive run
                break
            else:
                # A different test profile also breaks the run
                break

        if count < 2:
            return None

        return (last_profile, count, had_quality_fail)
