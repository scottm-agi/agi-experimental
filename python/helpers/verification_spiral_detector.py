"""
Verification Spiral Detector — Detects when agents spend too many
iterations reading/verifying without producing file changes.

Root Cause (ITR-29f — 2026-06-05):
    Code agent subordinates enter a "verification spiral" where they:
    1. Fix a small issue
    2. Verify the fix (read_file, code_execution)
    3. Discover a new issue during verification
    4. Fix that issue
    5. Verify again → discover yet another issue → repeat

    Each iteration stamps _last_tool_activity (resetting idle timeout)
    and produces valid tool calls (so empty-response breaker doesn't fire).
    The existing budget_reserve_advisor fires at 60% of 200 = iter 120,
    far too late to prevent 20+ iteration stalls.

Fix:
    Track "iterations since last file write". After a threshold of
    consecutive read-only iterations, inject escalating wrap-up directives.
    This is ORTHOGONAL to budget_reserve (which tracks total budget) —
    this tracks the verification-without-writing pattern specifically.

Architecture:
    - Stateless pure functions — no agent dependency
    - Agent integration in _38_verification_spiral_guard.py extension
    - Tracks via agent.data["_iters_since_last_write"]
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("agix.verification_spiral")

# ═══════════════════════════════════════════════════════════════════════════
# Thresholds
# ═══════════════════════════════════════════════════════════════════════════

# After this many consecutive iterations with no file write, inject a WARNING
# ITR-42b: Reduced from 8 → 6 (code agents were burning 16 iterations before
# the non-functional circuit breaker fired)
SPIRAL_WARN_THRESHOLD = 6

# After this many, inject a HARD directive to wrap up NOW
# ITR-42b: Reduced from 12 → 8
SPIRAL_HARD_THRESHOLD = 8

# After this many, force a synthetic response (circuit breaker)
# ITR-42b: Reduced from 16 → 10
SPIRAL_CIRCUIT_BREAK_THRESHOLD = 10

# ─── ITR-30 SS-4: Profile-Aware Thresholds ───────────────────────────────
# Research-heavy profiles (frontend, architect, researcher) naturally spend
# more iterations reading without writing. Using the same thresholds as
# code agents causes false spiral detection during design phases.
_PROFILE_THRESHOLDS = {
    # Profile → (warn, hard, circuit_break)
    "frontend":    (10, 14, 18),
    "architect":   (10, 14, 18),
    "researcher":  (10, 14, 18),
    "code":        (SPIRAL_WARN_THRESHOLD, SPIRAL_HARD_THRESHOLD, SPIRAL_CIRCUIT_BREAK_THRESHOLD),
}

# ── ITR-41 RCA: Profiles fully exempt from spiral detection ──
# The orchestrator's JOB is reading, delegating, and managing requirements.
# It almost never writes files directly. Penalizing it for not writing
# is penalizing it for doing its job correctly.
_EXEMPT_PROFILES = frozenset({"orchestrator"})


def _get_thresholds_for_profile(profile: Optional[str] = None) -> tuple:
    """Get spiral thresholds for a given agent profile.

    ITR-30 SS-4 FIX: Research-heavy profiles get elevated thresholds.

    Args:
        profile: Agent profile name (e.g., 'frontend', 'code', 'researcher').
                 None or unknown profiles use default thresholds.

    Returns:
        Tuple of (warn_threshold, hard_threshold, circuit_break_threshold).
    """
    if profile and profile in _PROFILE_THRESHOLDS:
        return _PROFILE_THRESHOLDS[profile]
    return (SPIRAL_WARN_THRESHOLD, SPIRAL_HARD_THRESHOLD, SPIRAL_CIRCUIT_BREAK_THRESHOLD)

# ── ITR-41 RCA: Productive tools — anything that advances the task ──
# Root cause: The old _WRITE_TOOLS only recognized file-writing tools,
# treating delegation, requirements updates, and task completion as
# "read-only". This caused false spiral detection on the orchestrator
# (whose JOB is delegating and managing requirements, not writing files)
# and on any agent doing productive non-file-write work.
#
# Fix: Aligned with _10_structural_guards.py PRODUCTIVE_TOOLS definition.
# Any tool that advances the task — writing files, delegating, updating
# requirements, completing a task — resets the spiral counter.
_PRODUCTIVE_TOOLS = frozenset({
    # File writes
    "write_to_file",
    "replace_in_file",
    "apply_diff",
    "create_file",
    "save_to_file",
    "code_execution_tool",  # can write files via shell commands
    # Delegation (coordinating subordinates IS productive work)
    "call_subordinate",
    "call_sub",
    # Requirements management (updating ledger IS productive work)
    "requirements",
    # Task completion
    "response",
    # Knowledge/search (actively gathering data)
    "knowledge_tool",
    "web_search",
})

# Legacy alias for backward compatibility with existing tests
_WRITE_TOOLS = _PRODUCTIVE_TOOLS

# Read-only tool names — these tools do NOT advance the task
_READ_ONLY_TOOLS = frozenset({
    "read_file",
    "search_files",
    "list_dir",
    "list_files",
})


# ═══════════════════════════════════════════════════════════════════════════
# Core Detection Logic (Pure Functions)
# ═══════════════════════════════════════════════════════════════════════════

def is_write_tool(tool_name: str, agent_data: dict | None = None) -> bool:
    """Check if a tool name is a productive tool (advances the task).

    ITR-41 RCA: Renamed concept from "write tool" to "productive tool".
    Any tool that advances the task — writing files, delegating work,
    updating requirements, completing tasks — is "productive".

    §12 TDD mode: When TDD cycle is active, code_execution_tool is
    always considered productive (running tests IS productive work
    in a TDD cycle, even if the same command is repeated).

    Args:
        tool_name: Name of the tool used.
        agent_data: Optional agent.data dict. When TDD cycle is active,
                    code_execution tools are always productive.

    Returns:
        True if the tool is known to be productive.
    """
    if not tool_name:
        return False
    # §12: TDD mode — test execution is productive work
    if agent_data is not None:
        from python.helpers.tdd_cycle_detector import is_tdd_active
        if is_tdd_active(agent_data):
            if tool_name.lower() in ("code_execution_tool", "code_execution"):
                return True
    # Exact match first
    if tool_name in _PRODUCTIVE_TOOLS:
        return True
    # code_execution_tool variants
    if "code_execution" in tool_name.lower():
        return True
    return False


def update_write_counter(
    data: dict,
    tool_name: str,
    tool_result: str = "",
) -> int:
    """Update the iterations-since-last-write counter.

    Called after every tool execution. If the tool was a write tool,
    reset the counter to 0. Otherwise, increment.

    Special case: code_execution_tool only counts as a write if the
    command appears to modify files (contains write-like patterns).
    Pure verification commands (curl, npm test, cat, echo) are NOT writes.

    Args:
        data: Agent's data dict (mutated in-place).
        tool_name: Name of the tool that just executed.
        tool_result: The result/output of the tool (for code_execution heuristic).

    Returns:
        Current counter value after update.
    """
    counter_key = "_iters_since_last_write"
    current = data.get(counter_key, 0)

    if tool_name == "code_execution_tool":
        # code_execution is ambiguous — check if it actually wrote files
        if _is_write_command(tool_result):
            data[counter_key] = 0
            return 0
        else:
            data[counter_key] = current + 1
            return current + 1
    elif is_write_tool(tool_name, data):
        data[counter_key] = 0
        return 0
    else:
        data[counter_key] = current + 1
        return current + 1


# ── RCA-362: All-Tests-Pass regex patterns ──
# Reuses the same framework-level patterns as tdd_cycle_detector.py
# for consistency. These detect the summary line of common test runners.
_VITEST_ALL_PASS_RE = re.compile(
    r'Tests\s+(?:(\d+)\s+passed)\s+\((\d+)\)',
    re.IGNORECASE,
)
_JEST_SUMMARY_RE = re.compile(
    r'Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed,\s+(\d+)\s+total',
    re.IGNORECASE,
)
_PYTEST_SUMMARY_RE = re.compile(
    r'(?:(\d+)\s+failed,\s+)?(\d+)\s+passed\s+in\s+',
    re.IGNORECASE,
)


def _all_tests_pass(tool_output: str) -> bool:
    """Check if test output indicates ALL tests passed (0 failures).

    RCA-362 FIX: When an agent runs tests and ALL pass, this is completion
    evidence — the agent has PROVEN its work correct. Treating this the same
    as "ran tests with failures" causes false verification spiral detection.

    Supports: Vitest, Jest, pytest summary lines.

    Args:
        tool_output: The raw output from code_execution_tool.

    Returns:
        True if the output contains a test summary with 0 failures.
    """
    if not tool_output:
        return False

    # Vitest: "Tests  11 passed (11)" — no "failed" in the Tests line
    m = _VITEST_ALL_PASS_RE.search(tool_output)
    if m:
        # Check there's no 'failed' on the same logical block
        # Vitest shows "N failed | N passed" when failures exist
        if 'failed' not in tool_output.lower().split('tests')[0] if 'tests' in tool_output.lower() else True:
            # Simple check: look for "failed" near "Tests" line
            lines = tool_output.split('\n')
            for line in lines:
                if 'tests' in line.lower() and 'passed' in line.lower():
                    if 'failed' not in line.lower():
                        return True

    # Jest: "Tests: 0 failed, 11 passed, 11 total"
    m = _JEST_SUMMARY_RE.search(tool_output)
    if m:
        failed_count = int(m.group(1))
        if failed_count == 0:
            return True

    # pytest: "42 passed in 3.21s" (no "failed" clause)
    # or "2 failed, 40 passed in 3.21s" (with failures)
    m = _PYTEST_SUMMARY_RE.search(tool_output)
    if m:
        failed_str = m.group(1)  # group(1) = failed count (optional)
        if failed_str is None or int(failed_str) == 0:
            return True

    return False


def _is_write_command(tool_output: str) -> bool:
    """Heuristic: did the code_execution_tool write files?

    Looks for patterns that suggest file modification:
    - npm/npx commands that generate files (install, prisma generate)
    - echo/cat/tee that write to files
    - git operations
    - mkdir, touch, cp, mv

    ITR-43 FIX: Explicitly EXCLUDES verification commands that DON'T
    advance the task, even though they may generate temp artifacts:
    - npm run build / npm run dev (generates .next/ but that's not productive)
    - npm test / npx vitest / npx jest (verification)
    - npm run lint / npx eslint (verification)
    - grep, find, cat, curl (read-only)

    RCA-362 FIX: EXCEPTION — when test output shows ALL tests passing
    (0 failures), this is treated as productive/completion evidence.
    The agent has PROVEN its work is correct.

    Args:
        tool_output: The command string or output from code_execution.

    Returns:
        True if the command likely wrote files OR all tests passed.
    """
    if not tool_output:
        return False

    output_lower = tool_output.lower()

    # ── RCA-362: All-tests-pass OVERRIDES verification classification ──
    # When ALL tests pass (0 failures), the agent has proven its work
    # correct. This is completion evidence, not idle verification.
    # Check this BEFORE verification_patterns so it takes priority.
    if _all_tests_pass(tool_output):
        return True

    # ── ITR-43: Check for VERIFICATION patterns FIRST ──
    # These commands do NOT advance the task, even if they produce
    # side-effect files (like .next/ build cache). If any verification
    # pattern is found, this is NOT a write command.
    verification_patterns = [
        "npm run build", "npm run dev", "npm run start",
        "npm test", "npm run test", "npx vitest", "npx jest",
        "npm run lint", "npx eslint",
        "grep ", "find ", "cat ", "ls ", "wc ", "head ", "tail ",
        "curl ", "wget ",
    ]
    for pattern in verification_patterns:
        if pattern in output_lower:
            return False

    write_patterns = [
        "npm install", "npm i ", "npx prisma generate", "npx prisma db push",
        "git add", "git commit", "git push",
        "mkdir", "touch ", " > ", " >> ", "tee ", "cp ", "mv ",
        "echo.*>",  # echo redirect
    ]
    for pattern in write_patterns:
        if pattern in output_lower:
            return True
    return False


def get_spiral_action(
    iters_since_write: int,
    is_subordinate: bool,
    profile: Optional[str] = None,
) -> dict:
    """Determine what action to take based on verification spiral state.

    ITR-30 SS-4 FIX: Accepts optional profile parameter for profile-aware
    thresholds. Research-heavy profiles (frontend, architect, researcher)
    get elevated thresholds.

    Args:
        iters_since_write: Number of consecutive iterations without a file write.
        is_subordinate: Whether this agent is a subordinate (not orchestrator).
        profile: Agent profile name for threshold selection. None uses defaults.

    Returns:
        dict with:
            action: "none" | "warn" | "hard" | "circuit_break"
            message: str - the directive to inject (empty if action="none")
    """
    if not is_subordinate:
        return {"action": "none", "message": ""}

    # ── ITR-41 RCA: Exempt profiles skip spiral entirely ──
    # The orchestrator's entire job is reading/delegating — it almost
    # never writes files. Punishing it for not writing is wrong.
    if profile and profile in _EXEMPT_PROFILES:
        return {"action": "none", "message": ""}

    warn_thresh, hard_thresh, cb_thresh = _get_thresholds_for_profile(profile)

    if iters_since_write >= cb_thresh:
        return {
            "action": "circuit_break",
            "message": (
                f"## 🛑 VERIFICATION SPIRAL DETECTED — Forcing wrap-up\n\n"
                f"You have spent **{iters_since_write} consecutive iterations** "
                f"without writing any files. You are stuck in a verification spiral: "
                f"reading files, running tests, discovering new issues, but never "
                f"finishing.\n\n"
                f"### ⛔ MANDATORY: Call `response` tool NOW\n"
                f"1. Summarize ALL work you completed (files written, bugs fixed)\n"
                f"2. List any remaining issues you discovered\n"
                f"3. The orchestrator will re-delegate remaining work\n\n"
                f"**Do NOT read more files. Do NOT run more tests. Do NOT start "
                f"new fixes.** Call `response` IMMEDIATELY.\n\n"
                f"**IMPORTANT**: TODO/FIXME comments in files OUTSIDE your task "
                f"scope are NOT your responsibility. Do NOT grep for TODOs across "
                f"the entire project — they belong to other phases."
            ),
        }
    elif iters_since_write >= hard_thresh:
        remaining = cb_thresh - iters_since_write
        return {
            "action": "hard",
            "message": (
                f"## ⚠️ VERIFICATION SPIRAL — Wrap up in {remaining} iterations\n\n"
                f"You have spent **{iters_since_write} consecutive iterations** "
                f"reading/verifying without writing files. This is a verification "
                f"spiral.\n\n"
                f"### Required NOW:\n"
                f"1. **STOP discovering new issues** — every verification reveals more, "
                f"but you cannot fix everything in one delegation\n"
                f"2. **Complete your current fix** if you're mid-edit\n"
                f"3. **Call `response`** with what you accomplished and what remains\n"
                f"4. Remaining unfixed issues will be handled in the NEXT delegation\n\n"
                f"You have **{remaining} iterations** before forced exit."
            ),
        }
    elif iters_since_write >= warn_thresh:
        remaining = cb_thresh - iters_since_write
        return {
            "action": "warn",
            "message": (
                f"⚠️ **Verification spiral warning**: You have spent "
                f"{iters_since_write} iterations reading/verifying without writing "
                f"any files. You have ~{remaining} iterations before forced wrap-up. "
                f"Finish your current task and call `response` to report progress. "
                f"Don't keep discovering new issues to fix."
            ),
        }
    else:
        return {"action": "none", "message": ""}


def reset_write_counter(data: dict) -> None:
    """Reset the verification spiral counter.

    Called when an agent starts a new delegation or when the counter
    needs manual reset.

    Args:
        data: Agent's data dict (mutated in-place).
    """
    data["_iters_since_last_write"] = 0
