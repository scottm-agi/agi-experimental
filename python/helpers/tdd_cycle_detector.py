"""
TDD-Aware Intelligent Loop Protection System (§12)
====================================================

Implements progress-aware loop detection for Test-Driven Development cycles.
Replaces the blunt "same command = loop" heuristic with intelligent progress
tracking that distinguishes "productive iteration" from "stuck loop".

Architecture (2-Layer Detection):
    Layer 1 (Deterministic) — GUIDANCE ONLY:
        Fast regex-based comparison of test output (pass/fail counts,
        error signatures, TS error counts, output hashes).
        Can GREEN-LIGHT (PROGRESS) but can NEVER block or stop.

    Layer 2 (LLM-Assessed) — DECISION MAKER:
        Semantic progress assessment via cheap LLM call.
        Invoked ONLY when L1 returns STUCK/REGRESSION/INCONCLUSIVE.
        ONLY L2 can make blocking/stopping decisions.

Stop Conditions (ONLY two):
    1. Hard cap: 15 test runs per TDD cycle (absolute maximum)
    2. 3 consecutive L2-confirmed STUCK verdicts

Root Cause:
    ITR-36, ITR-46: TDD red-green-refactor cycles were killed by
    same_message_bridge after 3 identical `npm test` calls, even when
    each run showed progress (fewer failures). The agent was "looping"
    from the bridge's perspective but actually making productive progress.

Usage:
    State is stored in agent.data["_tdd_cycle_state"] as a serialized dict.
    Integration points in: same_message_bridge, build_loop_hook,
    loop_detection, verification_spiral_detector, tool_failure_tracker.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agix.tdd_cycle_detector")

# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

# TDD Mode Thresholds
TDD_MAX_TEST_RUNS = 15            # Hard cap per TDD cycle (prevent runaway)
TDD_MAX_STUCK_CONSECUTIVE = 3     # L2-confirmed zero-progress runs before stop
TDD_PROGRESS_RESETS_STUCK = True  # Progress resets stuck counter

# Normal Mode (unchanged)
NORMAL_SAME_MESSAGE_HARD_CAP = 3  # Existing SAME_MESSAGE_HARD_CAP

# Patterns that indicate the agent is running tests (TDD mode)
TDD_COMMAND_PATTERNS = [
    re.compile(r"\bnpm\s+test\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+run\s+test\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+jest\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+vitest\b", re.IGNORECASE),
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\bpython\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+run\s+build\b", re.IGNORECASE),
    re.compile(r"\bnext\s+build\b", re.IGNORECASE),
    re.compile(r"\bvite\s+build\b", re.IGNORECASE),
    re.compile(r"\btsc\b", re.IGNORECASE),
]

# Build command patterns (separate from test — build is compile, not verify)
BUILD_COMMAND_PATTERNS = [
    re.compile(r"\bnpm\s+run\s+build\b", re.IGNORECASE),
    re.compile(r"\bnext\s+build\b", re.IGNORECASE),
    re.compile(r"\bvite\s+build\b", re.IGNORECASE),
    re.compile(r"\btsc\s+--noEmit\b", re.IGNORECASE),
]


# ═══════════════════════════════════════════════════════════════════════════
# Command Detection
# ═══════════════════════════════════════════════════════════════════════════

def is_tdd_command(command: str) -> bool:
    """Check if a command is a TDD-related command (test or build).

    Args:
        command: The shell command string to check.

    Returns:
        True if the command matches any TDD command pattern.
    """
    if not command:
        return False
    return any(pat.search(command) for pat in TDD_COMMAND_PATTERNS)


def is_test_command(command: str) -> bool:
    """Check if a command is specifically a test runner (not just build).

    Args:
        command: The shell command string to check.

    Returns:
        True if the command is a test command (not a build-only command).
    """
    if not command:
        return False
    # Build-only pattern strings for comparison
    build_pattern_strs = {p.pattern for p in BUILD_COMMAND_PATTERNS}
    test_only = [p for p in TDD_COMMAND_PATTERNS if p.pattern not in build_pattern_strs]
    return any(pat.search(command) for pat in test_only)


# ═══════════════════════════════════════════════════════════════════════════
# TDD Phase State Machine
# ═══════════════════════════════════════════════════════════════════════════

class TDDPhase(Enum):
    """TDD cycle phases."""
    IDLE = auto()          # Not in a TDD cycle
    WRITE_TEST = auto()    # Agent is writing test files
    RUN_TEST = auto()      # Agent is running tests
    EVALUATE = auto()      # System is evaluating test results
    WRITE_CODE = auto()    # Agent is writing implementation code
    COMPLETE = auto()      # All tests pass — cycle done


@dataclass
class TDDCycleState:
    """Tracks the state of a TDD cycle for a single agent.

    Stored in agent.data["_tdd_cycle_state"] via to_dict()/from_dict().
    """
    phase: TDDPhase = TDDPhase.IDLE
    test_run_count: int = 0           # Total test runs in this cycle
    stuck_count: int = 0              # Consecutive L2-confirmed zero-progress runs
    last_test_output_hash: str = ""   # MD5 of last test output
    last_passing_count: int = 0       # Tests passing in last run
    last_failing_count: int = 0       # Tests failing in last run
    last_error_signatures: List[str] = field(default_factory=list)
    snapshot_paths: List[str] = field(default_factory=list)  # Rollback snapshots
    cycle_start_time: float = 0.0
    last_l2_verdict: Optional["ProgressVerdict"] = None  # Set by caller after L2

    # Hard caps
    MAX_TEST_RUNS: int = TDD_MAX_TEST_RUNS
    MAX_STUCK_CONSECUTIVE: int = TDD_MAX_STUCK_CONSECUTIVE

    def activate(self) -> None:
        """Activate TDD mode."""
        if self.phase == TDDPhase.IDLE:
            self.phase = TDDPhase.RUN_TEST
            self.test_run_count = 0
            self.stuck_count = 0
            self.cycle_start_time = time.time()

    def is_active(self) -> bool:
        """Check if TDD mode is active."""
        return self.phase not in (TDDPhase.IDLE, TDDPhase.COMPLETE)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state for storage in agent.data."""
        return {
            "phase": self.phase.name,
            "test_run_count": self.test_run_count,
            "stuck_count": self.stuck_count,
            "last_test_output_hash": self.last_test_output_hash,
            "last_passing_count": self.last_passing_count,
            "last_failing_count": self.last_failing_count,
            "last_error_signatures": list(self.last_error_signatures),
            "snapshot_paths": list(self.snapshot_paths),
            "cycle_start_time": self.cycle_start_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TDDCycleState":
        """Deserialize state from agent.data."""
        if not data:
            return cls()
        state = cls()
        phase_name = data.get("phase", "IDLE")
        try:
            state.phase = TDDPhase[phase_name]
        except KeyError:
            state.phase = TDDPhase.IDLE
        state.test_run_count = data.get("test_run_count", 0)
        state.stuck_count = data.get("stuck_count", 0)
        state.last_test_output_hash = data.get("last_test_output_hash", "")
        state.last_passing_count = data.get("last_passing_count", 0)
        state.last_failing_count = data.get("last_failing_count", 0)
        state.last_error_signatures = data.get("last_error_signatures", [])
        state.snapshot_paths = data.get("snapshot_paths", [])
        state.cycle_start_time = data.get("cycle_start_time", 0.0)
        return state


# ═══════════════════════════════════════════════════════════════════════════
# Progress Verdict
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ProgressVerdict:
    """Result of progress detection (Layer 1 or Layer 2).

    Attributes:
        status: One of "PROGRESS", "STUCK", "REGRESSION", "INCONCLUSIVE"
        confidence: 0.0 to 1.0
        detail: Human-readable explanation
        passing_delta: Change in passing test count
        failing_delta: Change in failing test count
        new_errors: New error signatures not in previous output
    """
    status: str
    confidence: float
    detail: str
    passing_delta: int = 0
    failing_delta: int = 0
    new_errors: Optional[List[str]] = None


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1: Deterministic Progress Detection (GUIDANCE ONLY — NEVER BLOCKS)
# ═══════════════════════════════════════════════════════════════════════════

class DeterministicProgressDetector:
    """Layer 1: Fast, deterministic progress detection based on test output diffs.

    Can GREEN-LIGHT (PROGRESS) but can NEVER RED-LIGHT (block/stop).
    When L1 returns STUCK/REGRESSION/INCONCLUSIVE, the caller MUST
    escalate to Layer 2 (LLM) before taking any action.
    """

    # Test result patterns for common frameworks
    _JEST_SUMMARY = re.compile(
        r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed,\s+(\d+)\s+total",
        re.IGNORECASE,
    )
    _VITEST_SUMMARY = re.compile(
        r"Tests\s+(\d+)\s+failed\s*\|\s*(\d+)\s+passed",
        re.IGNORECASE,
    )
    _PYTEST_SUMMARY = re.compile(
        r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?",
        re.IGNORECASE,
    )
    _TS_ERROR_COUNT = re.compile(
        r"Found\s+(\d+)\s+error",
        re.IGNORECASE,
    )

    def detect(
        self,
        prev_output: str,
        curr_output: str,
        state: Optional[TDDCycleState],
    ) -> ProgressVerdict:
        """Compare two test outputs and determine if progress was made.

        Progress signals (any one = PROGRESS):
        1. Number of passing tests INCREASED
        2. Number of failing tests DECREASED
        3. Error messages are DIFFERENT (new errors = progress)
        4. TypeScript error count DECREASED

        Stuck signals (all must be true = STUCK):
        1. Same number of passing/failing tests
        2. Same error messages (by signature)
        3. Same or worse TypeScript error count

        Regression signals:
        1. Number of passing tests DECREASED
        2. Number of failing tests INCREASED

        Args:
            prev_output: Previous test/build output
            curr_output: Current test/build output
            state: Current TDD cycle state (optional)

        Returns:
            ProgressVerdict with L1 assessment
        """
        if not prev_output or not curr_output:
            return ProgressVerdict(
                status="INCONCLUSIVE",
                confidence=0.1,
                detail="Missing test output for comparison",
            )

        # Compare test statistics
        prev_stats = self._extract_test_stats(prev_output)
        curr_stats = self._extract_test_stats(curr_output)

        if prev_stats and curr_stats:
            prev_pass, prev_fail = prev_stats
            curr_pass, curr_fail = curr_stats

            passing_delta = curr_pass - prev_pass
            failing_delta = curr_fail - prev_fail

            if curr_pass > prev_pass or curr_fail < prev_fail:
                return ProgressVerdict(
                    status="PROGRESS",
                    confidence=0.95,
                    detail=f"Tests: {prev_pass}→{curr_pass} passing, {prev_fail}→{curr_fail} failing",
                    passing_delta=passing_delta,
                    failing_delta=failing_delta,
                )

            if curr_pass < prev_pass or curr_fail > prev_fail:
                return ProgressVerdict(
                    status="REGRESSION",
                    confidence=0.95,
                    detail=f"Regression: {prev_pass}→{curr_pass} passing, {prev_fail}→{curr_fail} failing",
                    passing_delta=passing_delta,
                    failing_delta=failing_delta,
                )

        # Compare error signatures — different errors = progress
        prev_errors = self._extract_error_signatures(prev_output)
        curr_errors = self._extract_error_signatures(curr_output)

        if prev_errors and curr_errors:
            new_errors = curr_errors - prev_errors
            fixed_errors = prev_errors - curr_errors

            if new_errors or fixed_errors:
                return ProgressVerdict(
                    status="PROGRESS",
                    confidence=0.8,
                    detail=f"Error set changed: {len(fixed_errors)} fixed, {len(new_errors)} new",
                    new_errors=list(new_errors),
                )

        # Compare TypeScript error counts
        prev_ts = self._extract_ts_error_count(prev_output)
        curr_ts = self._extract_ts_error_count(curr_output)

        if prev_ts is not None and curr_ts is not None:
            if curr_ts < prev_ts:
                return ProgressVerdict(
                    status="PROGRESS",
                    confidence=0.9,
                    detail=f"TS errors: {prev_ts}→{curr_ts}",
                )
            if curr_ts > prev_ts:
                return ProgressVerdict(
                    status="REGRESSION",
                    confidence=0.85,
                    detail=f"TS errors increased: {prev_ts}→{curr_ts}",
                )

        # Compare raw output hashes — identical output = definitely stuck
        prev_hash = hashlib.md5(prev_output.encode()).hexdigest()
        curr_hash = hashlib.md5(curr_output.encode()).hexdigest()

        if prev_hash == curr_hash:
            return ProgressVerdict(
                status="STUCK",
                confidence=1.0,
                detail="Identical test output — zero progress",
            )

        # Output changed but we couldn't determine direction
        return ProgressVerdict(
            status="INCONCLUSIVE",
            confidence=0.3,
            detail="Output changed but couldn't determine if progress or regression",
        )

    def _extract_test_stats(self, output: str) -> Optional[Tuple[int, int]]:
        """Extract (passing, failing) counts from test output."""
        for pattern in [self._JEST_SUMMARY, self._VITEST_SUMMARY, self._PYTEST_SUMMARY]:
            match = pattern.search(output)
            if match:
                groups = match.groups()
                if pattern == self._JEST_SUMMARY:
                    return int(groups[1]), int(groups[0])  # passed, failed
                elif pattern == self._VITEST_SUMMARY:
                    return int(groups[1]), int(groups[0])  # passed, failed
                elif pattern == self._PYTEST_SUMMARY:
                    passed = int(groups[0])
                    failed = int(groups[1]) if groups[1] else 0
                    return passed, failed
        return None

    def _extract_error_signatures(self, output: str) -> set:
        """Extract unique error signatures from output."""
        signatures = set()
        # TypeScript errors: "error TS2345: ..."
        for m in re.finditer(r"error\s+(TS\d+):\s*(.+?)(?:\n|$)", output):
            signatures.add(f"{m.group(1)}:{m.group(2)[:80]}")
        # Jest/Vitest failures: "FAIL src/..."
        for m in re.finditer(r"FAIL\s+(\S+)", output):
            signatures.add(f"FAIL:{m.group(1)}")
        # Generic error lines
        for m in re.finditer(r"(?:Error|TypeError|ReferenceError):\s*(.+?)(?:\n|$)", output):
            signatures.add(f"ERR:{m.group(1)[:80]}")
        return signatures

    def _extract_ts_error_count(self, output: str) -> Optional[int]:
        """Extract TypeScript error count from output."""
        match = self._TS_ERROR_COUNT.search(output)
        return int(match.group(1)) if match else None


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2: LLM-Assessed Progress Detection
# ═══════════════════════════════════════════════════════════════════════════

async def llm_assess_progress(
    agent: Any,
    prev_output: str,
    fix_description: str,
    curr_output: str,
) -> ProgressVerdict:
    """Layer 2: LLM-based semantic progress assessment.

    Only invoked when Layer 1 returns INCONCLUSIVE. The LLM reviews
    the fix attempt and its results to determine if real progress
    was made.

    DESIGN RULE: Only L2 can make blocking/stopping decisions.

    Args:
        agent: The agent instance (for LLM access)
        prev_output: Previous test/build output
        fix_description: Description of what the agent changed
        curr_output: New test/build output after fix

    Returns:
        ProgressVerdict with LLM-assessed status
    """
    prompt = f"""You are evaluating whether a code fix made progress toward passing tests.

PREVIOUS TEST OUTPUT (last 2000 chars):
{prev_output[-2000:]}

FIX APPLIED:
{fix_description[:1000]}

NEW TEST OUTPUT (last 2000 chars):
{curr_output[-2000:]}

QUESTION: Did this fix make progress toward passing tests?

Respond with EXACTLY one of:
- PROGRESS: The fix addressed real issues. Different errors or fewer failures.
- STUCK: The fix didn't change anything meaningful. Same errors, same failures.
- REGRESSION: The fix made things worse. More failures or new unrelated errors.

Your response (one word only):"""

    try:
        from python.helpers.call_llm import call_llm
        # Get a small/cheap model for this assessment
        chat_model = agent.config.chat_model
        result = await call_llm(
            system="You are a concise test progress evaluator. Respond with exactly one word: PROGRESS, STUCK, or REGRESSION.",
            model=chat_model,
            message=prompt,
        )
        result_clean = result.strip().upper()

        if "PROGRESS" in result_clean:
            return ProgressVerdict(
                status="PROGRESS", confidence=0.7,
                detail="LLM assessed: fix made progress",
            )
        elif "REGRESSION" in result_clean:
            return ProgressVerdict(
                status="REGRESSION", confidence=0.7,
                detail="LLM assessed: fix caused regression",
            )
        else:
            return ProgressVerdict(
                status="STUCK", confidence=0.6,
                detail="LLM assessed: no meaningful progress",
            )
    except Exception as e:
        logger.warning(
            "L2 LLM assessment failed, falling back to INCONCLUSIVE: %s", e
        )
        return ProgressVerdict(
            status="INCONCLUSIVE", confidence=0.3,
            detail=f"LLM assessment failed: {e}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# TDD Cycle Decision Function
# ═══════════════════════════════════════════════════════════════════════════

def should_stop_tdd_cycle(
    state: TDDCycleState,
    l1_verdict: ProgressVerdict,
) -> Tuple[bool, str]:
    """Determine if TDD cycle should be stopped.

    DESIGN RULE: L1 can green-light (PROGRESS), but ONLY L2 can red-light.

    Flow:
      L1 → PROGRESS       → continue immediately (no L2 needed)
      L1 → STUCK/REGR/INC → invoke L2 for confirmation
      L2 → PROGRESS        → continue (L1 was wrong)
      L2 → STUCK           → increment stuck counter
      L2 → REGRESSION      → trigger rollback

    Args:
        state: Current TDD cycle state
        l1_verdict: Layer 1 progress verdict

    Returns:
        (should_stop, reason) — reason is "INVOKE_L2" when L2 is needed
    """
    # Hard cap — absolute maximum test runs (the ONLY deterministic stop)
    if state.test_run_count >= state.MAX_TEST_RUNS:
        return True, f"TDD hard cap reached: {state.test_run_count}/{state.MAX_TEST_RUNS} test runs"

    # L1 PROGRESS — trusted, continue immediately
    if l1_verdict.status == "PROGRESS":
        state.stuck_count = 0  # Reset on progress
        return False, ""

    # L1 anything else — MUST escalate to L2 before acting
    # (L2 invocation happens in the caller; here we process the L2 result)
    l2_verdict = state.last_l2_verdict  # Set by caller after L2 completes

    if l2_verdict is None:
        # L2 hasn't been invoked yet — signal caller to invoke L2
        return False, "INVOKE_L2"

    if l2_verdict.status == "PROGRESS":
        # L2 overrode L1 — the cycle IS making progress, L1 was too dumb to see it
        state.stuck_count = 0
        state.last_l2_verdict = None  # Clear for next iteration
        return False, ""

    if l2_verdict.status == "REGRESSION":
        state.last_l2_verdict = None
        return True, "L2 confirmed REGRESSION — triggering rollback to last snapshot"

    # L2 confirmed STUCK
    state.stuck_count += 1
    state.last_l2_verdict = None

    if state.stuck_count >= state.MAX_STUCK_CONSECUTIVE:
        return True, (
            f"TDD stuck (L2-confirmed): {state.stuck_count} consecutive zero-progress runs. "
            f"Escalating to supervisor with test report."
        )

    return False, ""


# ═══════════════════════════════════════════════════════════════════════════
# TDD Quality Gate
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_tdd_gate(
    state: TDDCycleState,
    test_output: str,
) -> Tuple[bool, str]:
    """Evaluate whether the TDD quality gate passes.

    The TDD cycle IS the quality gate:
    - If all tests pass → gate passes, proceed to next phase
    - If tests still failing after hard cap → escalate with full report
    - If regression detected → rollback and retry

    Args:
        state: Current TDD cycle state
        test_output: Latest test/build output

    Returns:
        (gate_passed, message)
    """
    detector = DeterministicProgressDetector()
    stats = detector._extract_test_stats(test_output)

    if stats:
        passing, failing = stats
        if failing == 0:
            return True, f"✅ TDD QUALITY GATE PASSED: All {passing} tests passing"

    # Check for zero failures in build output
    if "Build completed successfully" in test_output or "✓ Compiled" in test_output:
        return True, "✅ TDD QUALITY GATE PASSED: Build succeeded"

    # Check TS error count
    ts_errors = detector._extract_ts_error_count(test_output)
    if ts_errors == 0:
        return True, "✅ TDD QUALITY GATE PASSED: Zero TypeScript errors"

    fail_count = stats[1] if stats else "?"
    return False, f"TDD gate: {fail_count} tests still failing"


# ═══════════════════════════════════════════════════════════════════════════
# Snapshot Manager (Rollback Protection)
# ═══════════════════════════════════════════════════════════════════════════

class TDDSnapshotManager:
    """Manages code snapshots for TDD rollback protection.

    Before each fix attempt, snapshots the affected files. If the fix
    causes regression (fewer tests passing), auto-rollback to the
    snapshot.

    Snapshots are stored in {project_dir}/.tdd_snapshots/{run_number}/
    and cleaned up when the TDD cycle completes.
    """

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._snapshot_dir = os.path.join(project_dir, ".tdd_snapshots")
        self._current_run = 0

    def snapshot_files(self, files: list) -> str:
        """Create a snapshot of specific files before a fix attempt.

        Args:
            files: List of absolute file paths to snapshot

        Returns:
            Snapshot directory path
        """
        self._current_run += 1
        snapshot_path = os.path.join(self._snapshot_dir, f"run_{self._current_run}")
        os.makedirs(snapshot_path, exist_ok=True)

        for file_path in files:
            if os.path.exists(file_path):
                rel = os.path.relpath(file_path, self._project_dir)
                dest = os.path.join(snapshot_path, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(file_path, dest)

        return snapshot_path

    def rollback(self, snapshot_path: str) -> list:
        """Rollback files from a snapshot.

        Args:
            snapshot_path: Path to the snapshot directory

        Returns:
            List of files that were restored
        """
        restored = []
        if not os.path.exists(snapshot_path):
            return restored
        for root, _, files in os.walk(snapshot_path):
            for fname in files:
                snap_file = os.path.join(root, fname)
                rel = os.path.relpath(snap_file, snapshot_path)
                orig_file = os.path.join(self._project_dir, rel)
                os.makedirs(os.path.dirname(orig_file), exist_ok=True)
                shutil.copy2(snap_file, orig_file)
                restored.append(orig_file)
        return restored

    def cleanup(self) -> None:
        """Remove all snapshots (called when TDD cycle completes)."""
        if os.path.exists(self._snapshot_dir):
            shutil.rmtree(self._snapshot_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# Helper: Check TDD state from agent.data
# ═══════════════════════════════════════════════════════════════════════════

def is_tdd_active(agent_data: Optional[Dict] = None) -> bool:
    """Check if TDD cycle is active from agent data dict.

    Args:
        agent_data: The agent's data dict (agent.data)

    Returns:
        True if TDD cycle is active (not IDLE/COMPLETE)
    """
    if agent_data is None:
        return False
    tdd_state = agent_data.get("_tdd_cycle_state")
    if not tdd_state or not isinstance(tdd_state, dict):
        return False
    phase = tdd_state.get("phase")
    return phase not in ("IDLE", "COMPLETE", None)
