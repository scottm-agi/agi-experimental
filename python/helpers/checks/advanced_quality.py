"""Advanced quality gate checks — test execution, behavior assertions,
gate remediation, requirements coverage, workpackage coverage, smart mock L2.

Previously contained: body depth (quality_gate_body_depth), mock linter
(quality_gate_mock_linter), project stubs, page completeness, layout navigation
(stub_detection) — all deleted with their backing validators.
"""

import logging
from typing import Optional

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
)

logger = logging.getLogger("agix.orchestrator_completion_gate")


# [REMOVED] 1. Body Depth — deleted with quality_gate_body_depth.py
# [REMOVED] 2. Mock Data Linter — deleted with quality_gate_mock_linter.py


# ── 3. Test Execution Proof ───────────────────────────────────────────────────

@register_check(1.302, "Test execution proof", critical=True, web_only=True, gate="tdd")
def _check_test_execution_proof(ctx: CheckContext):
    """Verify agents actually RAN tests, not just wrote them.

    RCA-475 GAP-4: Enhanced to also parse test output for pass/fail counts.
    If tests ran but failed, the check now flags the failure count.
    """
    try:
        from python.helpers.test_result_parser import (
            detect_test_run_commands,
            parse_test_result,
        )

        # Get tool history from agent data
        tool_history = ctx.agent_data.get('_tool_history', [])
        code_exec_commands = [h for h in tool_history if isinstance(h, str) and ('test' in h.lower() or 'jest' in h.lower() or 'pytest' in h.lower())]

        if not code_exec_commands:
            # No test commands found at all — advisory only
            return None

        result = detect_test_run_commands(code_exec_commands)
        if not result.get('executed', False):
            return ctx.block(
                "⚠️ TESTS NOT EXECUTED: Test files were written but no test runner "
                "commands were detected. Run your tests before marking complete."
            )

        # RCA-475 GAP-4: Parse test output for pass/fail if available
        test_output = ctx.agent_data.get('_last_test_output', '')
        if test_output:
            parsed = parse_test_result(test_output)
            if parsed.get('confidence') in ('high', 'medium') and parsed.get('tests_failed', 0) > 0:
                return ctx.block(
                    f"⚠️ TESTS FAILED: {parsed['tests_failed']} test(s) failed, "
                    f"{parsed.get('tests_passed', 0)} passed. Fix failing tests "
                    f"before marking complete."
                )
            # Store parsed results for downstream visibility
            ctx.agent_data['_test_execution_result'] = parsed

        return None
    except Exception:
        return None


# ── 4. Behavior Assertion Runner ──────────────────────────────────────────────

@register_advisory(1.303, "Behavior assertions", web_only=True)
def _check_behavior_assertions(ctx: CheckContext):
    """Validate behavioral contracts from prompt against generated code."""
    try:
        from python.helpers.behavior_assertion_runner import run_behavior_assertions

        if not ctx.project_dir:
            return None

        # Get behaviors from agent data (set by contract pipeline)
        behaviors = ctx.agent_data.get('_behavior_assertions', [])
        if not behaviors:
            return None

        result = run_behavior_assertions(behaviors, ctx.project_dir)
        if not result.get('passed', True):
            pass_rate = result.get('pass_rate', 0)
            missing = [d['name'] for d in result.get('details', []) if not d.get('found', False)][:5]
            details = "\n".join(f"  • {m}" for m in missing)
            return ctx.block(
                f"⚠️ BEHAVIOR ASSERTIONS FAILED ({pass_rate:.0%} pass rate):\n"
                f"{details}\n\nImplement the missing behavioral contracts."
            )
        return None
    except Exception:
        return None


# ── 6. Requirements Coverage ─────────────────────────────────────────────────

@register_advisory(1.305, "Requirements coverage", web_only=True)
def _check_requirements_coverage(ctx: CheckContext):
    """Check requirement coverage percentage."""
    try:
        from python.helpers.requirements_coverage import get_full_coverage

        coverage = get_full_coverage(ctx.agent_data)
        pct = coverage.get('coverage_pct', 100)

        if pct < 80:
            pending = coverage.get('pending', [])
            details = "\n".join(f"  • {p.get('id', '?')}: {p.get('text', '?')[:60]}" for p in pending[:5]) if pending else "  (no details available)"
            return ctx.block(
                f"⚠️ LOW REQUIREMENTS COVERAGE ({pct:.0f}%):\n"
                f"{details}\n\nAddress pending requirements before completion."
            )
        return None
    except Exception:
        return None


# ── 7. Workpackage Coverage ──────────────────────────────────────────────────

@register_advisory(1.306, "Workpackage coverage", web_only=True)
def _check_workpackage_coverage(ctx: CheckContext):
    """Verify workpackage file/route coverage."""
    try:
        from python.helpers.workpackage_coverage import check_workpackage_coverage
        import os

        if not ctx.project_dir:
            return None

        # Check key source files for workpackage assignment
        src_dir = os.path.join(ctx.project_dir, 'src')
        if not os.path.isdir(src_dir):
            return None

        uncovered = []
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in {'node_modules', '.next', '__pycache__'}]
            for f in files:
                if f.endswith(('.tsx', '.ts', '.jsx', '.js')):
                    rel = os.path.relpath(os.path.join(root, f), ctx.project_dir)
                    result = check_workpackage_coverage(ctx.project_dir, rel)
                    if result and not result.get('covered', True):
                        uncovered.append(rel)

        if uncovered and len(uncovered) > 3:  # Only flag if significant
            details = "\n".join(f"  • {f}" for f in uncovered[:5])
            return ctx.block(
                f"⚠️ WORKPACKAGE GAP: {len(uncovered)} file(s) not covered by any workpackage:\n"
                f"{details}\n\nEnsure all source files are assigned to a workpackage."
            )
        return None
    except Exception:
        return None


# [REMOVED] 8. Project Stub Patterns — deleted with stub_detection.py
# [REMOVED] 9. Page Completeness — deleted with stub_detection.py
# [REMOVED] 10. Layout Navigation — deleted with stub_detection.py
# [REMOVED] 11. Smart Mock Classifier — deleted with quality_gate_mock_linter.py
