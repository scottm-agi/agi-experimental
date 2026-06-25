"""Self-Check Suite — runs existing raw validators for pre-completion checking.

ZERO new validation logic. Calls the exact same functions the gate
wrappers in checks/quality.py and checks/content.py call. The only new
code is the orchestration loop and report formatting.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agix.self_check_suite")


@dataclass
class SelfCheckResult:
    name: str
    passed: bool
    message: str = ""
    details: Optional[dict] = None


@dataclass
class SelfCheckReport:
    results: List[SelfCheckResult] = field(default_factory=list)

    @property
    def failures(self) -> List[SelfCheckResult]:
        return [r for r in self.results if not r.passed]

    @property
    def all_passed(self) -> bool:
        return len(self.failures) == 0

    def format_markdown(self) -> str:
        if self.all_passed:
            return f"✅ Self-check PASSED: all {len(self.results)} checks clean."
        lines = [f"⛔ Self-check: {len(self.failures)} failure(s):"]
        for f in self.failures:
            lines.append(f"  ❌ {f.name}: {f.message}")
        lines.append("\nFix these before completing.")
        return "\n".join(lines)


# ── Validator Registry ──
# Each entry: (name, module_path, function_name, interpreter_name)
# Result interpreters parse the raw validator return value into pass/fail.
#
# IMPORTANT: These are the SAME raw validators the gate wrappers call.
# Only validators that ACTUALLY EXIST in the codebase are listed here.

VALIDATORS = [
    # From validators/common.py — check_fetch_route_completeness(project_dir) -> Optional[Dict]
    ("fetch_route", "python.helpers.validators.common", "check_fetch_route_completeness", "_interpret_fetch_route"),

    # From validators/node_checks_build.py — check_boilerplate(project_dir) -> Optional[Dict]
    ("boilerplate", "python.helpers.validators.node_checks_build", "check_boilerplate", "_interpret_boilerplate"),

    # From validators/route_reachability.py — check_nav_link_consistency(project_dir) -> dict
    ("nav_link", "python.helpers.validators.route_reachability", "check_nav_link_consistency", "_interpret_nav_link"),

    # From validators/prisma_provider_coherence.py — validate_schema_route_consistency(project_dir) -> List[dict]
    ("schema_route", "python.helpers.validators.prisma_provider_coherence", "validate_schema_route_consistency", "_interpret_schema_route"),

    # From validators/build_pass_check.py — check_build_passes(project_dir) -> dict
    # C-3: Runs `npm run build` and reports pass/fail. Skips if no package.json.
    ("build_pass", "python.helpers.validators.build_pass_check", "check_build_passes", "_interpret_build_pass"),

    # From validators/test_pass_check.py — check_tests_pass(project_dir) -> dict
    # FIX-17: Runs `npm test` and reports pass/fail. Skips if no package.json or no test script.
    ("test_pass", "python.helpers.validators.test_pass_check", "check_tests_pass", "_interpret_test_pass"),

    # From validators/bdd_scenarios.py — validate_bdd_scenarios(project_dir) -> dict
    ("bdd_scenarios", "python.helpers.validators.bdd_scenarios", "validate_bdd_scenarios", "_interpret_bdd_scenario"),

    # From validators/semantic_fidelity.py — check_semantic_fidelity(project_dir, prompt) -> dict
    ("semantic_fidelity", "python.helpers.validators.semantic_fidelity", "check_semantic_fidelity", "_interpret_semantic_fidelity"),
]


def _load_and_call_validator(name: str, module_path: str, func_name: str, project_dir: str, agent_data: dict = None) -> Any:
    """Lazy-load a validator module and call the function.

    Separated for easy mocking in tests.
    """
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    if name == "semantic_fidelity":
        prompt = (agent_data.get("_original_prompt") or agent_data.get("_user_prompt")) if agent_data else ""
        return func(project_dir, prompt, cache=agent_data)
    return func(project_dir)


def run_self_check_suite(project_dir: str, agent_data: dict = None) -> SelfCheckReport:
    """Run all structural quality checks against project_dir.

    Each validator is the SAME function the gate wrapper calls.
    Only the orchestration and reporting is new.
    """
    report = SelfCheckReport()

    if not project_dir:
        return report

    for name, module_path, func_name, interpreter_name in VALIDATORS:
        try:
            raw_result = _load_and_call_validator(name, module_path, func_name, project_dir, agent_data)

            interpreter = _INTERPRETERS[interpreter_name]
            passed, message = interpreter(raw_result)

            report.results.append(SelfCheckResult(
                name=name,
                passed=passed,
                message=message,
                details=raw_result if isinstance(raw_result, dict) else None,
            ))
        except Exception as e:
            # Validator not applicable (e.g., no Prisma in project) = pass
            report.results.append(SelfCheckResult(
                name=name, passed=True, message=f"skipped ({type(e).__name__})"
            ))
            logger.debug(f"[SELF-CHECK] {name} skipped: {e}")

    return report


# ── Result Interpreters ──
# Each interpreter takes the raw validator return and produces (passed, message)

def _interpret_fetch_route(result: Any) -> Tuple[bool, str]:
    """Interpret check_fetch_route_completeness() result."""
    if result is None:
        return (True, "no API calls found")
    if result.get("orphaned_fetches", 0) == 0:
        return (True, "all fetch calls have route handlers")
    orphans = ", ".join(
        r["path"] if isinstance(r, dict) else str(r)
        for r in result.get("orphaned_routes", [])[:5]
    )
    return (False, f"{result['orphaned_fetches']} orphaned fetch call(s): {orphans}")


def _interpret_boilerplate(result: Any) -> Tuple[bool, str]:
    """Interpret check_boilerplate() result."""
    if result is None:
        return (True, "no entry files found")
    if isinstance(result, dict) and result.get("has_boilerplate", False):
        files = result.get("boilerplate_files", [])
        file_list = ", ".join(f[0] if isinstance(f, tuple) else str(f) for f in files[:3])
        return (False, f"boilerplate content detected in: {file_list}")
    return (True, "no boilerplate detected")


def _interpret_dep_result(result: Any) -> Tuple[bool, str]:
    """Interpret dependency validation result."""
    if result is None:
        return (True, "")
    if isinstance(result, dict):
        unresolved = result.get("unresolved", [])
        if unresolved:
            return (False, f"{len(unresolved)} unresolved import(s): {', '.join(str(u) for u in unresolved[:5])}")
    return (True, "")


def _interpret_nav_link(result: Any) -> Tuple[bool, str]:
    """Interpret check_nav_link_consistency() result."""
    if result is None:
        return (True, "")
    if isinstance(result, dict):
        missing = result.get("missing_pages", [])
        if missing:
            return (False, f"{len(missing)} nav link(s) without page: {', '.join(str(m) for m in missing[:5])}")
    return (True, "all nav links have pages")


def _interpret_schema_route(result: Any) -> Tuple[bool, str]:
    """Interpret validate_schema_route_consistency() result.

    NOTE: This validator returns List[dict] (findings), NOT a dict.
    An empty list = no issues.
    """
    if result is None:
        return (True, "")
    if isinstance(result, list) and len(result) > 0:
        models = [f.get("model", "?") for f in result[:5]]
        return (False, f"{len(result)} Prisma model(s) referenced but not in schema: {', '.join(models)}")
    return (True, "schema-route consistency ok")


def _interpret_build_pass(result: Any) -> Tuple[bool, str]:
    """Interpret check_build_passes() result.

    Returns dict with 'passed' bool and 'reason' string.
    None means no package.json — skip (pass).
    """
    if result is None:
        return (True, "no package.json — skipped")
    if isinstance(result, dict):
        passed = result.get("passed", True)
        reason = result.get("reason", "")
        if passed:
            return (True, reason or "build passed")
        return (False, reason or "build failed")
    return (True, "")


def _interpret_test_pass(result: Any) -> Tuple[bool, str]:
    """Interpret check_tests_pass() result.

    Returns dict with 'passed' bool and 'reason' string.
    None means no package.json — skip (pass).
    """
    if result is None:
        return (True, "no package.json — skipped")
    if isinstance(result, dict):
        passed = result.get("passed", True)
        reason = result.get("reason", "")
        if passed:
            return (True, reason or "tests passed")
        return (False, reason or "tests failed")
    return (True, "")


def _interpret_bdd_scenario(result: Any) -> Tuple[bool, str]:
    if result is None:
        return (True, "no bdd-scenarios.md found")
    if isinstance(result, dict):
        passed = result.get("passed", True)
        if passed:
            return (True, f"all {result.get('total_scenarios', 0)} BDD scenarios passed")
        failures = result.get("failures", [])
        has_blocking = result.get("has_blocking_failures", False)
        blocking_str = " (BLOCKING)" if has_blocking else ""
        return (False, f"{len(failures)} BDD scenario clause(s) failed{blocking_str}")
    return (True, "")


def _interpret_semantic_fidelity(result: Any) -> Tuple[bool, str]:
    if result is None:
        return (True, "no scorable terms")
    if isinstance(result, dict):
        passed = result.get("passed", True)
        reasons = result.get("reasons", [])
        msg = ", ".join(reasons) if reasons else ""
        if passed:
            return (True, msg or "semantic fidelity passed")
        return (False, msg or "semantic fidelity failed")
    return (True, "")


# ── Interpreter lookup table ──
_INTERPRETERS = {
    "_interpret_fetch_route": _interpret_fetch_route,
    "_interpret_boilerplate": _interpret_boilerplate,
    "_interpret_dep_result": _interpret_dep_result,
    "_interpret_nav_link": _interpret_nav_link,
    "_interpret_schema_route": _interpret_schema_route,
    "_interpret_build_pass": _interpret_build_pass,
    "_interpret_test_pass": _interpret_test_pass,
    "_interpret_bdd_scenario": _interpret_bdd_scenario,
    "_interpret_semantic_fidelity": _interpret_semantic_fidelity,
}
