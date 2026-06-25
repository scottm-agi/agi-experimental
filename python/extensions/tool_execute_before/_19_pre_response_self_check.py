"""Pre-Response Self-Check Gate — tool_execute_before extension.

Follows the same extension pattern as _28_bdd_injection_gate.py.
Calls the same run_self_check_suite() used by the explicit tool.
Returns non-None to BLOCK if structural checks fail AND are fixable.

Uses VerificationLedger for project-level state persistence:
- Same failure twice → unfixable (no more wasted attempts)
- Known unfixable patterns → auto-classified
- Per-check and global attempt budgets
- Persists across delegations, restarts, crashes

RCA: ITR-43 had 39 self-check loops because the old agent.data counter
reset per delegation. The VerificationLedger writes state to
docs/.verification_state.json on disk.
"""
from __future__ import annotations

import logging
import os
import re
from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.self_check_suite import run_self_check_suite
from python.helpers.verification_ledger import VerificationLedger
from python.helpers.universal_gate_budget import gate_check
from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS

logger = logging.getLogger("agix.pre_response_self_check")

# ── TDD skeleton phase patterns ──
# During TDD skeleton phases, tests are EXPECTED to fail (RED stage).
# The gate must not block responses when the agent is generating stubs.
_TDD_SKELETON_PATTERNS = [
    re.compile(r"Phase\s*2\.8", re.IGNORECASE),
    re.compile(r"TDD\s+[Ss]keleton", re.IGNORECASE),
    re.compile(r"failing\s+test\s+stubs?", re.IGNORECASE),
    re.compile(r"skeleton\s+expansion", re.IGNORECASE),
    re.compile(r"generate.*skeleton.*test", re.IGNORECASE),
]


def _is_tdd_skeleton_phase(task_description: str | None) -> bool:
    """Detect whether the current task is a TDD skeleton generation phase.

    During TDD skeleton phases (Phase 2.8), test failures are EXPECTED
    because the stubs intentionally throw errors. The pre-completion gate
    must not block the response in these cases.
    """
    if not task_description:
        return False
    return any(p.search(task_description) for p in _TDD_SKELETON_PATTERNS)

# ── Source file extensions to track for modification detection ──
_SOURCE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".css", ".scss", ".less", ".sass",
    ".html", ".json", ".vue", ".svelte",
    ".py", ".prisma", ".graphql", ".gql",
}

# DUP-3: Uses shared DEFAULT_PROJECT_SKIP_DIRS from project_scan_constants.
_SKIP_DIRS = DEFAULT_PROJECT_SKIP_DIRS


def _get_latest_source_mtime(project_dir: str) -> float:
    """Get the latest modification time of source files in project_dir.

    Scans only known source extensions, skipping node_modules/dist/build.
    Returns 0.0 if no source files are found.
    """
    latest = 0.0
    if not project_dir or not os.path.isdir(project_dir):
        return latest

    for root, dirs, files in os.walk(project_dir):
        # Prune heavy directories in-place for os.walk efficiency
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            _, ext = os.path.splitext(fname)
            if ext.lower() in _SOURCE_EXTENSIONS:
                try:
                    mtime = os.path.getmtime(os.path.join(root, fname))
                    if mtime > latest:
                        latest = mtime
                except OSError:
                    pass
    return latest


class PreResponseSelfCheck(Extension):
    # Context-aware: only fire for code agents responding
    PROFILES = {"code"}
    TOOLS = frozenset({"response"})

    """Auto-fire self-check suite before response tool for code agents.

    Returns non-None Response to BLOCK if structural checks fail AND are fixable.
    Returns None to ALLOW the response through for passed/unfixable/exhausted.
    Uses VerificationLedger for project-level persistence (survives delegations).
    """

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        # Only intercept the response tool
        if tool_name != "response":
            return None

        # Only fire for code profile
        profile = getattr(self.agent.config, "profile", "")
        if profile != "code":
            return None

        # Find project dir
        project_dir = (
            self.agent.data.get("project_dir", "")
            or self.agent.data.get("_active_project_dir", "")
        )
        if not project_dir:
            return None

        # ── TDD Skeleton bypass ──
        # During Phase 2.8 (TDD Skeleton Expansion), tests are EXPECTED
        # to fail — they are stubs that throw errors intentionally.
        # The gate must not block the code agent from responding.
        task_desc = self.agent.data.get("_delegation_task", "") or ""
        if _is_tdd_skeleton_phase(task_desc):
            logger.info(
                "[PRE-RESPONSE SELF-CHECK] TDD skeleton phase detected — "
                "skipping test_pass check (tests are expected to fail in RED stage)"
            )
            # Still run other checks (build_pass, boilerplate, etc.)
            # but filter out test_pass from failures
            self.agent.data["_tdd_skeleton_phase"] = True

        # ── FIX-3: Build-pass bypass ──
        # If all checks (including build + test) already passed and no
        # source files have changed since, skip the entire suite to
        # avoid redundant npm run build invocations (was 15+ per agent).
        if self.agent.data.get("_build_verified_clean"):
            clean_mtime = self.agent.data.get("_build_clean_mtime", 0.0)
            current_mtime = _get_latest_source_mtime(project_dir)
            if current_mtime <= clean_mtime:
                logger.info(
                    "[PRE-RESPONSE SELF-CHECK] Build verified clean, "
                    "no source changes — skipping suite"
                )
                return None
            else:
                # Source files changed — invalidate the cache
                logger.info(
                    "[PRE-RESPONSE SELF-CHECK] Source files changed since "
                    "clean build — re-running suite"
                )
                self.agent.data["_build_verified_clean"] = False

        # Run the self-check suite
        report = run_self_check_suite(project_dir)

        # Create a ledger to persist state to disk
        ledger = VerificationLedger(project_dir)

        if report.all_passed:
            # Record all passing results
            for result in report.results:
                ledger.record(result.name, passed=True, failures=[])

            # ── FIX-3: Set build-verified-clean flag ──
            # Check if build_pass and test_pass are among the passing results.
            # If so, mark the build as verified clean to skip future suite runs.
            passed_names = {r.name for r in report.results if r.passed}
            if "build_pass" in passed_names or "test_pass" in passed_names:
                current_mtime = _get_latest_source_mtime(project_dir)
                self.agent.data["_build_verified_clean"] = True
                self.agent.data["_build_clean_mtime"] = current_mtime
                logger.info(
                    "[PRE-RESPONSE SELF-CHECK] All checks passed including "
                    "build/test — setting _build_verified_clean flag "
                    f"(mtime={current_mtime})"
                )

            return None  # Allow response through

        # Process each failure through the ledger
        is_skeleton = self.agent.data.get("_tdd_skeleton_phase", False)
        fixable_failures = []
        for result in report.failures:
            # Skip test_pass failures during TDD skeleton phases
            if is_skeleton and result.name == "test_pass":
                logger.info(
                    "[PRE-RESPONSE SELF-CHECK] Skipping test_pass failure "
                    "— TDD skeleton phase (RED stage, expected)"
                )
                continue

            verdict = ledger.record(
                result.name,
                passed=False,
                failures=[result.message],
            )
            if verdict == "fixable":
                fixable_failures.append(result)
            else:
                logger.info(
                    f"[PRE-RESPONSE SELF-CHECK] {result.name}: "
                    f"verdict={verdict} — not blocking"
                )

        # Also record passing checks
        for result in report.results:
            if result.passed:
                ledger.record(result.name, passed=True, failures=[])

        # Only block if there are fixable failures
        if not fixable_failures:
            logger.info(
                "[PRE-RESPONSE SELF-CHECK] No fixable failures remaining — "
                "allowing response through"
            )
            return None

        # Build block message with only the fixable failures
        failure_lines = []
        for f in fixable_failures:
            failure_lines.append(f"  ❌ {f.name}: {f.message}")

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "pre_response_self_check"):
            return None  # Allow through

        summary = ledger.summary()
        return Response(
            message=(
                f"⛔ PRE-COMPLETION SELF-CHECK — {len(fixable_failures)} fixable "
                f"failure(s):\n"
                + "\n".join(failure_lines)
                + f"\n\n{summary}\n\n"
                f"Fix these before completing."
            ),
            break_loop=False,
        )
