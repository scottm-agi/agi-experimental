"""
TDD Stub Immutability Guard (FIX-025, §12.7).

Extension that prevents modification of test files during Phase 3-5
(implementation phases). Test files written during Phase 2 (TDD stubs)
are contracts — the agent must write implementation code that passes
the tests, NOT modify the tests to pass.

This is a WARNING gate (not a hard blocker). The agent is strongly
nudged to write implementation code instead. Test infrastructure
fixes (jest.config, vitest.config) are allowed with explanation.

Extension point: tool_execute_before
Order: 37 (after infra_tdd_gate at 36)

Root Cause:
    ITR-42, ITR-46: Agent repeatedly modified test files during
    implementation to make them pass trivially (removing assertions,
    weakening matchers) instead of writing correct implementation code.
    This violated the TDD contract and produced code that only appeared
    to work.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

try:
    from python.helpers.phase_category import is_post_tdd_generation_phase, PhaseCategory
except ImportError:
    def is_post_tdd_generation_phase(p):  # type: ignore
        try: return float(p) >= 3.0
        except: return False
    import enum
    class PhaseCategory(enum.Enum):  # type: ignore
        PLANNING = "planning"
        DESIGN = "design"
        IMPLEMENTATION = "implementation"
        INTEGRATION = "integration"
        VERIFICATION = "verification"
        DELIVERY = "delivery"

# Try importing Extension — graceful fallback for tests
try:
    from python.helpers.extension import Extension
except ImportError:
    class Extension:  # type: ignore
        def __init__(self, agent=None):
            self.agent = agent


logger = logging.getLogger("agix.tdd_stub_guard")


class TDDStubImmutabilityGuard(Extension):
    """Prevent modification of test files during implementation phases.

    Test files are contracts during Phase 3-5. The agent should write
    implementation code that passes the tests, not modify the tests.
    """

    # Context-aware: only fire for code agents on write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "replace_in_file", "apply_diff", "save_to_file"})
    CATEGORIES = {
        PhaseCategory.IMPLEMENTATION,
        PhaseCategory.INTEGRATION,
        PhaseCategory.VERIFICATION,
    }


    # Patterns matching common test file naming conventions
    TEST_FILE_PATTERNS = [
        re.compile(r"\.test\.[jt]sx?$"),       # .test.ts, .test.tsx, .test.js, .test.jsx
        re.compile(r"\.spec\.[jt]sx?$"),       # .spec.ts, .spec.tsx, etc
        re.compile(r"test_.*\.py$"),            # test_utils.py
        re.compile(r".*_test\.py$"),            # utils_test.py
        re.compile(r"__tests__/"),             # __tests__/Button.tsx
    ]

    # Write tool names that modify files
    _WRITE_TOOLS = frozenset({
        "write_to_file",
        "replace_in_file",
        "apply_diff",
        "create_file",
        "save_to_file",
    })

    # Safe modification patterns — these are test INFRASTRUCTURE, not test assertions.
    # Modifying these is always allowed because they don't weaken test contracts.
    _SAFE_FILE_PATTERNS = [
        re.compile(r"vitest\.config\.[jt]sx?$"),    # vitest config
        re.compile(r"jest\.config\.[jt]sx?$"),       # jest config
        re.compile(r"setup\.[jt]sx?$"),              # test setup files
        re.compile(r"tsconfig.*\.json$"),             # TypeScript config
        re.compile(r"\.babelrc"),                     # Babel config
    ]

    async def execute(
        self,
        tool_name: str = "",
        tool_args: Optional[dict] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        """Check if a write operation targets a test file during Phase 3-5.

        AF-5 (ITR-49): Promoted from WARNING to HARD BLOCK.
        Test files are contracts — the agent must write implementation code
        that passes the tests, NOT modify the tests to pass.

        Returns:
            Block message if a test file write is attempted during
            implementation phases, None otherwise.
        """
        # Only enforce during post-TDD-generation phases (IMPLEMENTATION+)
        phase = self.agent.data.get("_current_phase", 0)
        if not is_post_tdd_generation_phase(phase):
            return None

        # Only check write tools
        if tool_name not in self._WRITE_TOOLS:
            return None

        # Extract file path from tool args
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

        # AF-5: Allow safe modifications (test infrastructure, not assertions)
        if any(p.search(file_path) for p in self._SAFE_FILE_PATTERNS):
            return None

        # Check if the file matches test file patterns
        if not any(p.search(file_path) for p in self.TEST_FILE_PATTERNS):
            return None

        # TDD stub immutability violation detected — HARD BLOCK
        msg = (
            "## ⛔ TDD STUB IMMUTABILITY (FIX-025, AF-5)\n"
            f"Cannot modify test file `{file_path}` during Phase {phase}. "
            "Test files are contracts — write implementation code that "
            "passes the tests instead of modifying the tests.\n"
            "If you need to fix a test infrastructure issue (jest.config, "
            "vitest.config, setup.ts), those are allowed."
        )

        logger.warning(
            "[TDD STUB GUARD] %s: Test file modification BLOCKED "
            "during Phase %s: %s",
            self.agent.agent_name,
            phase,
            file_path,
        )

        return msg

