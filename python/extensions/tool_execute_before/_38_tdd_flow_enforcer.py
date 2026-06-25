"""
TDD Flow Enforcer (FIX-7, RCA-ITR49 SS-11).

Structural enforcement of the Red-Green-Refactor TDD cycle.
WIRES the existing `TDDCycleState` from `tdd_cycle_detector.py` which was
built (694 lines, full state machine) but NEVER activated — same bug class
as inject_contract_assertions (SS-1).

This extension:
1. ACTIVATES `_tdd_cycle_state` when Phase 2.8 (RED baseline) starts
2. Blocks test file creation during Phase 3+ (GREEN/implementation phase)
3. Tracks test count baseline from Phase 2.8
4. Uses TDDCycleState for state management instead of ad-hoc flags

Architecture inspired by tdd-guard (github.com/nizos/tdd-guard).

Extension point: tool_execute_before
Order: 38 (after _37_tdd_stub_guard which handles test MODIFICATION)

Combined with _37_tdd_stub_guard:
  - _37: Blocks test file MODIFICATION during Phase 3-5 (existing)
  - _38: Blocks test file CREATION during Phase 3-5 (this extension)
         Also activates _tdd_cycle_state for consumption by:
         - _12_tool_failure_tracker.py (exempts TDD from failure count)
         - _24_build_loop_hook.py (adjusts build behavior during TDD)
         - same_message_bridge.py (allows repeated test runs)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

# Try importing Extension — graceful fallback for tests
try:
    from python.helpers.extension import Extension
except ImportError:
    class Extension:  # type: ignore
        def __init__(self, agent=None):
            self.agent = agent

try:
    from python.helpers.tool import Response
except ImportError:
    class Response:  # type: ignore
        def __init__(self, message="", break_loop=False):
            self.message = message
            self.break_loop = break_loop

# Import the existing TDD state machine (694 lines, never activated)
try:
    from python.helpers.tdd_cycle_detector import TDDCycleState, TDDPhase
except ImportError:
    # Fallback for test isolation
    TDDCycleState = None  # type: ignore
    TDDPhase = None  # type: ignore

# Use the universal phase category module — NOT raw float comparisons
try:
    from python.helpers.phase_category import (
        is_post_tdd_generation_phase,
        is_design_phase,
        PhaseCategory,
        get_phase_category,
    )
except ImportError:
    # Fallback for test isolation
    def is_post_tdd_generation_phase(p):  # type: ignore
        try: return float(p) >= 3.0
        except: return False
    def is_design_phase(p):  # type: ignore
        try: return float(p) < 3.0
        except: return False
    def get_phase_category(p):  # type: ignore
        return None
    PhaseCategory = None  # type: ignore

logger = logging.getLogger("agix.tdd_flow_enforcer")

# Maximum TDD violation warnings before escape hatch triggers.
MAX_TDD_VIOLATIONS = 3

# Profiles where TDD enforcement applies.
ENFORCED_PROFILES = {"code"}

# Patterns matching test file naming conventions
TEST_FILE_PATTERNS = [
    re.compile(r"\.test\.[jt]sx?$"),       # .test.ts, .test.tsx
    re.compile(r"\.spec\.[jt]sx?$"),       # .spec.ts, .spec.tsx
    re.compile(r"test_.*\.py$"),            # test_utils.py
    re.compile(r".*_test\.py$"),            # utils_test.py
    re.compile(r"__tests__/"),             # __tests__/Button.tsx
]

# Config files that look like tests but aren't
CONFIG_EXCEPTIONS = [
    re.compile(r"jest\.config"),
    re.compile(r"vitest\.config"),
    re.compile(r"\.babelrc"),
    re.compile(r"tsconfig.*\.json$"),
    re.compile(r"setupTests"),
    re.compile(r"test-setup"),
]

# Write tool names
WRITE_TOOLS = frozenset({
    "write_to_file",
    "replace_in_file",
    "apply_diff",
    "create_file",
    "save_to_file",
})


class TDDFlowEnforcer(Extension):
    """Enforce Red-Green-Refactor flow using existing TDDCycleState.

    ACTIVATES the existing _tdd_cycle_state (which was never populated)
    and blocks test file creation during implementation phases.
    """

    # Context-aware loading: only fire for code agents, during impl+, on writes
    PROFILES = {"code"}
    TOOLS = frozenset({
        "write_to_file", "replace_in_file", "apply_diff",
        "create_file", "save_to_file",
    })
    CATEGORIES = {
        PhaseCategory.IMPLEMENTATION,
        PhaseCategory.INTEGRATION,
        PhaseCategory.VERIFICATION,
    }


    def _is_test_file(self, file_path: str) -> bool:
        """Check if a file path matches test file patterns."""
        if not file_path:
            return False
        for exc_pattern in CONFIG_EXCEPTIONS:
            if exc_pattern.search(file_path):
                return False
        return any(p.search(file_path) for p in TEST_FILE_PATTERNS)

    def _is_implementation_phase(self, phase) -> bool:
        """Post-TDD-generation phases (IMPLEMENTATION, INTEGRATION, VERIFICATION).

        Uses phase_category.py instead of raw float comparisons.
        Phase categories come from the skill, not hardcoded numbers.
        """
        return is_post_tdd_generation_phase(phase)

    def _budget_exhausted(self) -> bool:
        """Escape hatch after N violations."""
        violations = self.agent.data.get("_tdd_violations", 0)
        return violations > MAX_TDD_VIOLATIONS

    def _should_enforce(self) -> bool:
        """Only enforce for code profiles."""
        profile = getattr(getattr(self.agent, "config", None), "profile", "")
        return profile in ENFORCED_PROFILES

    def _ensure_tdd_state_activated(self, phase) -> None:
        """Activate TDDCycleState if entering implementation phase.

        This is the WIRING FIX: TDDCycleState existed in tdd_cycle_detector.py
        but was never activated. Three consumers READ _tdd_cycle_state
        (tool_failure_tracker, build_loop_hook, same_message_bridge) but
        nobody ever WROTE it. This method fixes that.

        Uses phase_category.py for phase classification — not raw floats.
        """
        if TDDCycleState is None:
            return  # Import failed (test isolation)

        is_post_tdd = is_post_tdd_generation_phase(phase)
        tdd_state_dict = self.agent.data.get("_tdd_cycle_state")

        if tdd_state_dict and isinstance(tdd_state_dict, dict):
            # State already exists — check if phase transition needed
            current_phase_name = tdd_state_dict.get("phase", "IDLE")
            if is_post_tdd and current_phase_name == "RUN_TEST":
                # Transition from RED to GREEN (WRITE_CODE)
                tdd_state_dict["phase"] = "WRITE_CODE"
                self.agent.data["_tdd_cycle_state"] = tdd_state_dict
                logger.info(
                    "[TDD FLOW] Phase transition: RUN_TEST → WRITE_CODE "
                    "(post-TDD phase %s detected)", phase
                )
        else:
            # No state exists — activate it
            state = TDDCycleState()
            if not is_post_tdd:
                # Pre-implementation: RED phase (writing tests)
                state.phase = TDDPhase.RUN_TEST
            else:
                # Implementation phase: GREEN (writing code)
                state.phase = TDDPhase.WRITE_CODE
            state.activate()
            # Override phase after activate (activate sets RUN_TEST)
            if is_post_tdd:
                state.phase = TDDPhase.WRITE_CODE
            self.agent.data["_tdd_cycle_state"] = state.to_dict()
            logger.info(
                "[TDD FLOW] Activated _tdd_cycle_state: phase=%s "
                "(was NEVER populated before — ITR-49 SS-11 fix)",
                state.phase.name,
            )

    async def execute(
        self,
        tool_name: str = "",
        tool_args: Optional[dict] = None,
        **kwargs: Any,
    ) -> Optional[Any]:
        """Check if a write creates a test file during implementation."""
        if not self._should_enforce():
            return None

        # Get phase — use raw value, phase_category handles conversion
        phase = self.agent.data.get("_current_phase", 0)

        # Ensure TDD state machine is activated (the WIRING fix)
        self._ensure_tdd_state_activated(phase)

        # Only check write tools
        if tool_name not in WRITE_TOOLS:
            return None

        # Extract file path
        file_path = ""
        if tool_args and isinstance(tool_args, dict):
            file_path = (
                tool_args.get("path", "")
                or tool_args.get("file_path", "")
                or tool_args.get("target_file", "")
                or ""
            )

        if not file_path:
            return None

        # Check if it's a test file
        if not self._is_test_file(file_path):
            return None

        # Check if we're in implementation phase
        if not self._is_implementation_phase(phase):
            return None

        # Escape hatch
        if self._budget_exhausted():
            logger.info(
                "[TDD FLOW ENFORCER] Budget exhausted (%d violations) — "
                "allowing test file write: %s",
                self.agent.data.get("_tdd_violations", 0),
                file_path,
            )
            return None

        # Increment violation counter
        violations = self.agent.data.get("_tdd_violations", 0) + 1
        self.agent.data["_tdd_violations"] = violations

        baseline = self.agent.data.get("_tdd_test_baseline_count", "unknown")
        msg = (
            f"## ⚠️ TDD FLOW VIOLATION (FIX-7)\n"
            f"You are creating/adding a test file during Phase {phase} "
            f"(implementation phase).\n\n"
            f"**Expected flow**: Tests were written during the DESIGN phase "
            f"(RED baseline: {baseline} tests). During implementation, you should "
            f"ONLY write implementation code that makes those tests pass.\n\n"
            f"**File**: `{file_path}`\n"
            f"**Violation**: {violations}/{MAX_TDD_VIOLATIONS} "
            f"(escape after {MAX_TDD_VIOLATIONS})\n\n"
            f"If this test is genuinely needed to cover a gap, continue. "
            f"Otherwise, write implementation code that passes existing tests."
        )

        logger.warning(
            "[TDD FLOW ENFORCER] %s: Test file creation during Phase %s: %s "
            "(violation %d/%d)",
            getattr(self.agent, "agent_name", "unknown"),
            phase,
            file_path,
            violations,
            MAX_TDD_VIOLATIONS,
        )

        return msg
