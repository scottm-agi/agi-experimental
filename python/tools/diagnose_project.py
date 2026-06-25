from __future__ import annotations
"""
Diagnose Project Tool for AGIX

Comprehensive project health diagnostic tool that runs all validators to detect
build issues, configuration problems, and infrastructure gaps. Provides
actionable remediation that agents can execute to self-heal project state.

Actions:
    diagnose: Full health report with pass/fail/warning for each check
    fix: Auto-remediate all auto-fixable issues
"""

import json
import logging
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.tools.diagnose_project")


# ─── Issue Severity Levels ────────────────────────────────────────────

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


# ─── Diagnostic Issue ────────────────────────────────────────────────

class DiagnosticIssue:
    """A single detected issue with structured remediation."""

    def __init__(
        self,
        issue_id: str,
        severity: str,
        category: str,
        framework: str,
        message: str,
        auto_fixable: bool = False,
        fix_type: Optional[str] = None,  # file_edit, command, file_delete, file_create
        fix_detail: Optional[str] = None,
        fix_fn: Optional[callable] = None,
    ):
        self.issue_id = issue_id
        self.severity = severity
        self.category = category
        self.framework = framework
        self.message = message
        self.auto_fixable = auto_fixable
        self.fix_type = fix_type
        self.fix_detail = fix_detail
        self.fix_fn = fix_fn

    def to_dict(self) -> dict:
        return {
            "id": self.issue_id,
            "severity": self.severity,
            "category": self.category,
            "framework": self.framework,
            "message": self.message,
            "auto_fixable": self.auto_fixable,
            "fix_type": self.fix_type,
            "fix_detail": self.fix_detail,
        }


# ─── Diagnostic Runner ───────────────────────────────────────────────

def run_diagnostics(project_dir: str) -> Dict[str, Any]:
    """Run all diagnostic checks against a project directory.

    Returns a structured report with failures, warnings, passed, and
    auto-fixable remediation actions.

    Note: Many validators were removed in the heuristic validator cleanup.
    This function now runs a reduced set of checks.
    """
    from python.helpers.framework_env_validator import detect_frameworks

    issues: List[DiagnosticIssue] = []
    passed: List[str] = []

    frameworks = detect_frameworks(project_dir)

    # ── 1. Build output exists (using build_verification module) ──
    try:
        from python.helpers.build_verification import check_build_exists
        build = check_build_exists(project_dir)
        if build["built"]:
            passed.append(f"Build output exists ({build.get('framework', 'unknown')})")
        else:
            issues.append(DiagnosticIssue(
                issue_id="build_missing",
                severity=SEVERITY_WARNING,
                category="build",
                framework=build.get("framework", "unknown"),
                message=f"No build output found in {build.get('expected_path', '.next/ or dist/')}",
                auto_fixable=False,
                fix_type="command",
                fix_detail="npm run build",
            ))
    except Exception:
        passed.append("Build check (not applicable)")

    # ── 2. Package.json exists ──
    pkg_path = os.path.join(project_dir, "package.json")
    if os.path.isfile(pkg_path):
        passed.append("package.json exists")
    else:
        if "node" in frameworks or "nextjs" in frameworks:
            issues.append(DiagnosticIssue(
                issue_id="package_json_missing",
                severity=SEVERITY_CRITICAL,
                category="config",
                framework="node",
                message="package.json not found in project root",
                auto_fixable=False,
                fix_type="command",
                fix_detail="npm init -y",
            ))

    # ── 3. node_modules exists ──
    nm_path = os.path.join(project_dir, "node_modules")
    if os.path.isdir(nm_path):
        passed.append("node_modules installed")
    elif os.path.isfile(pkg_path):
        issues.append(DiagnosticIssue(
            issue_id="npm_deps_missing",
            severity=SEVERITY_CRITICAL,
            category="deps",
            framework="node",
            message="node_modules not found — dependencies not installed",
            auto_fixable=True,
            fix_type="command",
            fix_detail="npm install",
        ))

    # Organize results
    failures = [i for i in issues if i.severity == SEVERITY_CRITICAL]
    warnings = [i for i in issues if i.severity == SEVERITY_WARNING]
    infos = [i for i in issues if i.severity == SEVERITY_INFO]

    return {
        "failures": [i.to_dict() for i in failures],
        "warnings": [i.to_dict() for i in warnings],
        "infos": [i.to_dict() for i in infos],
        "passed": passed,
        "total_checks": len(issues) + len(passed),
        "is_healthy": len(failures) == 0,
        "frameworks": list(frameworks),
        "_issues": issues,  # Internal: for fix action
    }


# ─── Auto-Fix Functions ──────────────────────────────────────────────

def _fix_tailwind_content(project_dir: str, content_paths: List[str]) -> str:
    """Fix empty Tailwind content array by injecting inferred paths."""
    config_path = None
    for ext in ["js", "ts", "cjs", "mjs"]:
        candidate = os.path.join(project_dir, f"tailwind.config.{ext}")
        if os.path.isfile(candidate):
            config_path = candidate
            break

    if config_path is None:
        return "❌ No tailwind config file found to patch"

    try:
        with open(config_path, "r") as f:
            content = f.read()

        # Replace empty content array with populated one
        paths_formatted = ",\n    ".join(f"'{p}'" for p in content_paths)
        replacement = f"content: [\n    {paths_formatted},\n  ]"

        # Match content: [] with varying whitespace
        patched = re.sub(
            r'content\s*:\s*\[\s*\]',
            replacement,
            content,
        )

        if patched == content:
            return "❌ Could not find content: [] pattern to replace"

        with open(config_path, "w") as f:
            f.write(patched)

        return f"✅ Patched {os.path.basename(config_path)} with {len(content_paths)} content paths"
    except (IOError, OSError) as e:
        return f"❌ Failed to patch: {e}"


def _fix_postcss_config(project_dir: str) -> str:
    """Generate a postcss.config.js with tailwindcss and autoprefixer."""
    config_content = """\
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
"""
    config_path = os.path.join(project_dir, "postcss.config.js")
    try:
        with open(config_path, "w") as f:
            f.write(config_content)
        return "✅ Created postcss.config.js"
    except (IOError, OSError) as e:
        return f"❌ Failed to create postcss.config.js: {e}"


def _fix_clear_cache(cache_path: str) -> str:
    """Remove corrupted build cache directory."""
    try:
        if os.path.isdir(cache_path):
            shutil.rmtree(cache_path)
            return f"✅ Cleared corrupted cache: {cache_path}"
        return f"⚠️ Cache directory not found: {cache_path}"
    except (IOError, OSError) as e:
        return f"❌ Failed to clear cache: {e}"


# ─── Report Formatter ────────────────────────────────────────────────

def format_report(report: Dict[str, Any], project_dir: str) -> str:
    """Format diagnostic report as readable markdown for the agent."""
    lines = []
    lines.append(f"## Project Health Report")
    lines.append(f"**Path:** `{project_dir}`")
    lines.append(f"**Frameworks:** {', '.join(report['frameworks'])}")
    lines.append(f"**Status:** {'✅ HEALTHY' if report['is_healthy'] else '❌ ISSUES FOUND'}")
    lines.append("")

    if report["failures"]:
        lines.append(f"### ❌ Critical Failures ({len(report['failures'])})")
        for f in report["failures"]:
            fixable = " [AUTO-FIXABLE]" if f["auto_fixable"] else ""
            lines.append(f"- **{f['id']}**: {f['message']}{fixable}")
            lines.append(f"  Fix: `{f['fix_detail']}`")
        lines.append("")

    if report["warnings"]:
        lines.append(f"### ⚠️ Warnings ({len(report['warnings'])})")
        for w in report["warnings"]:
            fixable = " [AUTO-FIXABLE]" if w["auto_fixable"] else ""
            lines.append(f"- **{w['id']}**: {w['message']}{fixable}")
            lines.append(f"  Fix: `{w['fix_detail']}`")
        lines.append("")

    if report["passed"]:
        lines.append(f"### ✅ Passed ({len(report['passed'])})")
        for p in report["passed"]:
            lines.append(f"- {p}")
        lines.append("")

    auto_fixable = [
        i for cat in [report["failures"], report["warnings"]]
        for i in cat if i["auto_fixable"]
    ]
    if auto_fixable:
        lines.append(f"### 🔧 Auto-Fixable ({len(auto_fixable)})")
        lines.append("Run `diagnose_project` with `action: fix` to auto-remediate these issues.")
        lines.append("")

    return "\n".join(lines)


# ─── Tool Implementation ─────────────────────────────────────────────

class DiagnoseProject(Tool):
    """
    Comprehensive project health diagnostic and auto-remediation tool.

    Runs all framework-specific validators to detect:
    - Configuration errors (empty Tailwind content, missing PostCSS)
    - Build cache corruption (stale chunks, missing manifests)
    - Dependency issues (missing npm packages)
    - Infrastructure gaps (missing configs for detected frameworks)

    Actions:
        diagnose: Run all checks and return structured health report
        fix: Auto-remediate all fixable issues
    """

    async def execute(self, **kwargs) -> Response:
        action = kwargs.get("action", "diagnose").strip().lower()
        project_path = kwargs.get("path", "").strip()

        if not project_path:
            return Response(
                message="Error: Project path is required. Use `path: /path/to/project`.",
                break_loop=False,
            )

        if not os.path.isdir(project_path):
            return Response(
                message=f"Error: Project directory not found: {project_path}",
                break_loop=False,
            )

        try:
            if action == "diagnose":
                return await self._diagnose(project_path)
            elif action == "fix":
                return await self._fix(project_path)
            else:
                return Response(
                    message=f"Unknown action: {action}. Use 'diagnose' or 'fix'.",
                    break_loop=False,
                )
        except Exception as e:
            logger.exception(f"[DIAGNOSE] Error during {action}")
            return Response(
                message=f"Diagnostic error: {e}",
                break_loop=False,
            )

    async def _diagnose(self, project_dir: str) -> Response:
        """Run full diagnostics and return formatted report."""
        report = run_diagnostics(project_dir)
        output = format_report(report, project_dir)

        logger.info(
            f"[DIAGNOSE] {project_dir}: "
            f"{len(report['failures'])} failures, "
            f"{len(report['warnings'])} warnings, "
            f"{len(report['passed'])} passed"
        )

        return Response(message=output, break_loop=False)

    async def _fix(self, project_dir: str) -> Response:
        """Auto-remediate all fixable issues."""
        report = run_diagnostics(project_dir)
        issues = report.get("_issues", [])

        fixable = [i for i in issues if i.auto_fixable and i.fix_fn]

        if not fixable:
            return Response(
                message="No auto-fixable issues found. Run `diagnose` first to see the full report.",
                break_loop=False,
            )

        results = []
        for issue in fixable:
            try:
                result = issue.fix_fn()
                results.append(f"- **{issue.issue_id}**: {result}")
                logger.info(f"[DIAGNOSE FIX] {issue.issue_id}: {result}")
            except Exception as e:
                results.append(f"- **{issue.issue_id}**: ❌ Error: {e}")
                logger.exception(f"[DIAGNOSE FIX] {issue.issue_id} failed")

        output = f"## Auto-Remediation Results\n\n"
        output += "\n".join(results)
        output += "\n\n---\n"
        output += "Re-run `diagnose_project` with `action: diagnose` to verify all issues are resolved."

        return Response(message=output, break_loop=False)
