"""
Integration Checks — modular, registry-based web project quality validators.

Each check is a decorated function that auto-registers into the CHECK_REGISTRY.
To add a new check: just define a function with @register_check(order, name, critical).

The runner sweeps ALL registered checks in order. Critical checks run even
during the escape hatch. Non-critical checks are skipped after MAX_INTEGRATION_BLOCKS.

Used by MultiagentdevCompletionGate.
"""

import os
import json
import logging
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

# gate_registry removed — all gates always enabled now
def is_gate_enabled(*args, **kwargs) -> bool:
    return True

from python.helpers.universal_gate_budget import gate_check

from python.helpers.orchestrator_gate_common import (
    format_gate_block,
    resolve_project_dir_from_context,
    MAX_INTEGRATION_BLOCKS,
)

# Removed: build_verification_warning, build_browser_uat_warning (gate-only)
def build_verification_warning(*args, **kwargs):
    return ""
def build_browser_uat_warning(*args, **kwargs):
    return ""
# gate_check_cache removed — no caching needed without gates
def cache_check_result(agent_data, name, result=None, **kwargs):
    pass
def get_cached_result(agent_data, name):
    return None
def invalidate_failed_checks(agent_data):
    pass
def set_gate_retry_active(agent_data, active):
    pass
from python.helpers.requirements_ledger import (
    record_gate_failure,
    get_delegation_ledger_for_gate,
)
from python.helpers.boomerang_context import get_original_user_message
from python.helpers.prompt_contract_parser import build_contract
from python.helpers.contract_assertion_runner import run_contract_assertions


# Per-critical-check circuit breaker: after this many blocks on the SAME
# critical check, force-allow to prevent infinite response loops.
# Root cause: Test 20 (Iteration 93) — agent never renames package.json
# from 'scaffold-temp', and critical=True means the escape hatch never fires.
MAX_CRITICAL_CHECK_BLOCKS = 3
from python.helpers.validators.version_pinning import check_version_drift
from python.helpers.validators.lit import (
    check_lit_plan_exists,
    validate_lit_plan_structure,
    detect_lit_execution_evidence,
    generate_lit_plan_from_sitemap,
)
from python.helpers.validators.route_reachability import (
    check_route_reachability,
    curl_verify_routes,
    check_build_freshness as check_build_freshness_fn,
    check_nav_link_consistency,
    check_api_response_quality,
    check_plan_vs_implementation,
)
from python.helpers.server_health import check_server_health


logger = logging.getLogger("agix.orchestrator_completion_gate")

# ─── Check Registry ────────────────────────────────────────────────────

@dataclass
class IntegrationCheck:
    """A single registered integration check."""
    order: float           # Sort order (1.0, 1.5, 1.5b → use decimals)
    name: str              # Human-readable name
    fn: Callable           # The check function
    critical: bool = False # If True, runs even during escape hatch
    is_async: bool = False # If True, fn is a coroutine
    requires: list = field(default_factory=list)  # Prerequisite check names (Fix F3, RCA Iteration 158)
    web_only: bool = False # RCA-327: If True, skip for non-web projects
    gate_name: str = ""    # Maps to GATE_REGISTRY key for is_gate_enabled() filtering
    gate: str = ""         # Phase 1 Gate Refactor: "bdd", "tdd", "done", or "" (all gates)


def should_skip_for_prerequisites(requires: list, passed_checks: list) -> bool:
    """Check if a gate check should be skipped due to unmet prerequisites.

    RCA Iteration 158, Issue C: Gate checks ran independently — Route
    reachability fired even when Build failed, creating impossible
    delegation loops. This function enables a prerequisite DAG: checks
    declare their dependencies, and are skipped when those haven't passed.

    Args:
        requires: List of prerequisite check names that must have passed.
        passed_checks: List of check names that have already passed.

    Returns:
        True if the check should be SKIPPED (prerequisites not met).
    """
    if not requires:
        return False
    return not all(req in passed_checks for req in requires)

CHECK_REGISTRY: list[IntegrationCheck] = []
ADVISORY_REGISTRY: list[IntegrationCheck] = []


def register_check(order: float, name: str, critical: bool = False, requires: list = None, web_only: bool = False, gate_name: str = "", gate: str = ""):
    """Decorator to register a BLOCKING integration check function.

    Blocking checks can reject the agent's response if they fail.
    Only use for checks that are mechanically verifiable, mechanically
    fixable, and universal across all web projects.

    Args:
        order: Sort order (lower runs first).
        name: Human-readable check name.
        critical: If True, runs even during escape hatch.
        requires: List of prerequisite check names that must pass first.
                  If prerequisites haven't passed, this check is skipped.
                  (Fix F3, RCA Iteration 158)
        web_only: RCA-327. If True, this check is skipped for non-web
                  projects. Universal checks (TDD, structural) use
                  web_only=False (default). Web-specific checks (dev server,
                  route reachability, npm install) use web_only=True.
        gate_name: Maps to GATE_REGISTRY key. If set, the check is skipped
                   when is_gate_enabled() returns False for this gate.
        gate: Phase 1 Gate Refactor. Which gate this check belongs to:
              "bdd", "tdd", "done". If empty, the check runs in ALL gates
              (backward compat for untagged checks during migration).
              The router reads this tag, not priority numbers.

    Usage:
        @register_check(1.5, "Package boilerplate", critical=True, gate="tdd")
        def check_pkg_boilerplate(ctx):
            ...
            return None  # pass, or a string message to block
    """
    def decorator(fn):
        import asyncio
        is_async = asyncio.iscoroutinefunction(fn)
        CHECK_REGISTRY.append(IntegrationCheck(
            order=order, name=name, fn=fn,
            critical=critical, is_async=is_async,
            requires=requires or [],
            web_only=web_only,
            gate_name=gate_name,
            gate=gate,
        ))
        # Keep sorted by order
        CHECK_REGISTRY.sort(key=lambda c: c.order)
        return fn
    return decorator


def register_advisory(order: float, name: str, critical: bool = False, web_only: bool = False):
    """Decorator to register an ADVISORY (non-blocking) integration check.

    Advisory checks are logged for diagnostic purposes but never block
    the agent's response. Use for quality-of-life checks that provide
    useful signal but shouldn't cause death spirals.

    Gate Audit (Iteration 19): Demoted from blocking to advisory because
    these checks are either not universally applicable, not mechanically
    fixable by the agent, or better suited to prompt-level guidance.

    Args:
        web_only: RCA-327. If True, this advisory is skipped for non-web
                  projects.
    """
    def decorator(fn):
        import asyncio
        is_async = asyncio.iscoroutinefunction(fn)
        ADVISORY_REGISTRY.append(IntegrationCheck(
            order=order, name=name, fn=fn,
            critical=critical, is_async=is_async,
            web_only=web_only,
        ))
        ADVISORY_REGISTRY.sort(key=lambda c: c.order)
        return fn
    return decorator


@dataclass
class CheckContext:
    """Context object passed to every check function."""
    agent: Any
    agent_data: dict
    response: Any
    block_count: int
    project_dir: str
    passed_checks: list = field(default_factory=list)  # Names of checks that passed before this one
    advisory: bool = False  # If True, block() is a no-op (advisory audit mode)

    def block(self, message: str, action: str = "") -> str:
        """Helper: mark response as blocked and return the message.

        In advisory mode (self.advisory=True), returns the message without
        any side effects (no response mutation, no counter increments).

        Wraps the plain-text block reason with format_gate_block() JSON
        that includes real dynamic context (delegation ledger, completed
        tasks, recent agent messages) so the LLM has concrete state to
        decide what targeted fix to delegate.

        Args:
            message: The reason the response was blocked (what's wrong).
            action: Optional explicit remediation action. If empty, one is
                    auto-generated from the message to ensure the agent gets
                    a DISTINCT action vs reason (Fix 3: Gate Diagnostic Feedback).
        """
        # Advisory mode: return finding without side effects
        if self.advisory:
            return message
        if self.response is not None and hasattr(self.response, 'break_loop'):
            self.response.break_loop = False
        pass  # gate_block_counters stub removed — set_block_count was a no-op

        # Build dynamic context from agent state
        dynamic_context = {}
        ledger = get_delegation_ledger_for_gate(self.agent_data)
        if ledger:
            dynamic_context["delegation_ledger"] = ledger
        # Recent tool results from subordinate agents
        recent_tools = self.agent_data.get("recent_tool_calls", [])
        if recent_tools:
            dynamic_context["recent_agent_messages"] = [
                {"tool": t.get("tool_name", ""), "timestamp": t.get("timestamp", "")}
                for t in recent_tools[-5:]
            ]
        # Active project context from memory bank tracking
        active_ctx = self.agent_data.get("_active_context_summary", "")
        if active_ctx:
            dynamic_context["completed_tasks"] = active_ctx

        # Fix 3: Generate a distinct remediation action if none provided.
        # Previously both reason and action were message[:200] — identical text
        # that gave the agent no guidance on what to DO. Now we auto-generate
        # an action that tells the agent exactly what targeted fix to delegate.
        if not action:
            action = (
                f"Delegate a TARGETED fix for: {message[:150]}. "
                f"Do NOT re-delegate the entire project or retry your response with different wording."
            )


        # RCA-462: Response can be either a Response object or a dict.
        # Handle both formats to prevent "'dict' object has no attribute 'message'" crash.
        gate_block_msg = format_gate_block(
            reason=message,
            action=action,
            block_count=self.block_count + 1,
            passed_checks=getattr(self, 'passed_checks', []),
            context=dynamic_context,
        )
        if isinstance(self.response, dict):
            self.response["message"] = gate_block_msg
        elif self.response is not None:
            self.response.message = gate_block_msg
        return message  # Non-None = blocked


# ─── Web Project Classification ─────────────────────────────────────────


def _is_web_project(project_dir: str) -> bool:
    """Multi-signal web project classification.

    RCA-326: The old single-signal check (package.json only) caused false
    negatives when package.json hadn't been created yet but other web
    indicators existed (e.g., verification_sitemap.json, next.config.*,
    src/app/ directory). This caused ALL integration quality gates to be
    bypassed — dev server never started, browser UAT never ran.

    A project is classified as web if ANY web indicator file/directory exists.
    Individual checks have their own guards for specific requirements, so
    false positives here are harmless — checks self-filter.

    Returns True if the project has ANY web indicator, False otherwise.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return False

    # File-based indicators
    file_indicators = [
        os.path.join(project_dir, "package.json"),
        os.path.join(project_dir, "verification_sitemap.json"),
    ]
    # next.config with any extension
    for ext in [".js", ".mjs", ".ts"]:
        file_indicators.append(os.path.join(project_dir, f"next.config{ext}"))

    if any(os.path.isfile(p) for p in file_indicators):
        return True

    # Directory-based indicators (web framework conventions)
    dir_indicators = [
        os.path.join(project_dir, "src", "app"),
        os.path.join(project_dir, "app"),
        os.path.join(project_dir, "pages"),
        os.path.join(project_dir, "public"),
    ]

    return any(os.path.isdir(d) for d in dir_indicators)


# ─── Batch Rejection Message Builder (ITR-26 Fix 1) ────────────────────


def _build_batch_rejection_message(
    failures: list[tuple[str, str]],
) -> str:
    """Build a structured, ACTIONABLE batch rejection message.

    ITR-26 Fix 1: The old format listed check NAMES only ("Batch block: 8
    critical checks failed: X, Y, Z") — the orchestrator couldn't parse
    this into targeted delegations. Now each failing check includes:
    - The check name
    - The original result message (with file paths, line numbers)
    - A per-check remediation ACTION instruction

    This makes the batch rejection message directly actionable: the
    orchestrator can read each section and delegate a targeted fix.

    Args:
        failures: List of (check_name, result_message) tuples.

    Returns:
        Formatted batch rejection message string.
    """
    lines = [
        f"⛔ GATE BLOCKED — {len(failures)} check(s) failed "
        f"(batched into ONE rejection):\n"
    ]

    for i, (check_name, result_msg) in enumerate(failures, 1):
        lines.append(f"### {i}. {check_name}")
        lines.append(f"**Finding:** {result_msg}")
        # Per-check remediation ACTION — tells the orchestrator exactly what to do
        lines.append(
            f"**ACTION:** Delegate a TARGETED fix to a code agent for "
            f"'{check_name}'. Include the finding above verbatim in your "
            f"delegation message so the code agent knows exactly what to fix."
        )
        lines.append("")

    lines.append(
        "\n🔴 REMEDIATION INSTRUCTIONS:\n"
        "For EACH failed check above, delegate a SEPARATE targeted fix. "
        "Include the **Finding** text in your delegation so the code agent "
        "knows what file(s) to fix. Do NOT re-delegate the entire project. "
        "Do NOT retry your response with different wording.\n"
        "Fix ALL issues above before attempting to complete again."
    )

    return "\n".join(lines)


def are_gates_enabled() -> bool:
    """Check if gate checks are globally enabled.

    Controlled by the AGIX_GATES_ENABLED environment variable.
    Defaults to True (gates on) when the env var is not set.

    Values that disable gates: 'false', '0', 'no', 'off'
    All other values (including absent) keep gates enabled.

    Returns:
        True if gates should run, False if suppressed.
    """
    val = os.environ.get("AGIX_GATES_ENABLED", "true").lower().strip()
    return val not in ("false", "0", "no", "off")

# ─── Domain Check Sub-Modules ──────────────────────────────────────────
# All @register_check / @register_advisory decorated functions have been
# extracted into the python.helpers.checks package (structural, content,
# verification, quality, requirements). Importing the package triggers
# side-effect registration into CHECK_REGISTRY / ADVISORY_REGISTRY.
import python.helpers.checks  # noqa: F401 — triggers check auto-registration


# ─── Advisory Audit ─────────────────────────────────────────────────────

async def advisory_integration_audit(agent):
    """Run ADVISORY_REGISTRY checks as diagnostic logging only (no blocking).

    Gate Audit (Iteration 19): Refactored to iterate ADVISORY_REGISTRY
    dynamically instead of hardcoded check calls. Uses advisory=True on
    CheckContext so block() is a no-op — findings are logged but never
    mutate the response or increment block counters.
    """
    project_dir = resolve_project_dir_from_context(agent.data)
    if not project_dir:
        return
    ctx = CheckContext(
        agent=agent,
        agent_data=agent.data,
        response=None,
        block_count=0,
        project_dir=project_dir,
        advisory=True,  # block() becomes a no-op
    )
    audit = {}
    for check in ADVISORY_REGISTRY:
        try:
            result = await check.fn(ctx) if check.is_async else check.fn(ctx)
            audit[check.name] = "pass" if result is None else "advisory"
        except Exception as e:
            audit[check.name] = f"error: {e}"
    passed = sum(1 for v in audit.values() if v == "pass")
    logger.info(f"[ADVISORY AUDIT] {project_dir}: {passed}/{len(audit)} checks. Details: {audit}")

    # §10.5: Escalate to L2 when 3+ advisory checks fail
    failing_checks = [k for k, v in audit.items() if v != "pass"]
    if len(failing_checks) >= 3:
        agent.data.setdefault("_l2_escalation_signals", []).append({
            "severity": "warning",
            "detector": "advisory_quality_gap",
            "detail": (
                f"{len(failing_checks)} advisory checks failing: {failing_checks}. "
                f"Agent should address these quality gaps before delivery."
            ),
        })


async def collect_incomplete_items(agent) -> list:
    """Collect incomplete items for the escape hatch summary."""
    # Lazy import to avoid circular dependency — these functions live in the shim
    from python.extensions.tool_execute_after._22_orchestrator_completion_gate import (
        check_test_coverage,
        check_api_route_coverage,
        check_build_success,
        check_integration_markers,
    )
    items = []
    project_dir = agent.data.get("_active_project_dir", "")
    if not project_dir or not os.path.isdir(project_dir):
        return items
    try:
        cov = check_test_coverage(project_dir)
        if cov and not cov.get("sufficient", True):
            items.append(f"Test coverage: {cov['coverage_ratio']:.0%}")
    except Exception:
        pass
    try:
        api = check_api_route_coverage(project_dir)
        if api and api.get("missing_routes"):
            items.append(f"Missing API routes: {', '.join(api['missing_routes'])}")
    except Exception:
        pass
    try:
        build = check_build_success(project_dir)
        if build is not None and not build.get("has_build_output", True):
            items.append("No build output")
    except Exception:
        pass
    try:
        integ = check_integration_markers(project_dir)
        if integ and not integ.get("has_fetch", True):
            items.append("Missing fetch() calls")
        if integ and not integ.get("has_use_client", True):
            items.append("Missing 'use client' directives")
    except Exception:
        pass
    return items


# ─── Backward-Compatible Re-Exports (LAZY) ─────────────────────────────
# Existing test files import check functions directly from this module.
# Re-export all check functions so those imports continue to work.
#
# GAP-2 FIX: These were previously top-level imports that created a circular
# dependency: checks/__init__.py → structural.py → this file → structural.py.
# Now uses module-level __getattr__ for lazy loading on first access.
# LAZY: Lazy import pattern to break circular dependency

_LAZY_EXPORTS = {
    # structural checks
    "_check_manifest_fidelity": "python.helpers.checks.structural",
    "_check_manifest_code_fidelity": "python.helpers.checks.structural",
    "_check_blueprint": "python.helpers.checks.structural",
    "_check_scaffold_only": "python.helpers.checks.structural",
    "_check_markers": "python.helpers.checks.structural",
    "_check_boilerplate_content": "python.helpers.checks.structural",
    "check_default_metadata": "python.helpers.checks.structural",
    "_check_tdd_semantic_quality": "python.helpers.checks.tdd_semantic_quality",
    "_check_pkg_boilerplate": "python.helpers.checks.structural",
    "_check_secrets": "python.helpers.checks.structural",
    "_check_build": "python.helpers.checks.structural",
    "_check_build_cache": "python.helpers.checks.structural",
    "_check_npm": "python.helpers.checks.structural",
    "_check_source_imports": "python.helpers.checks.structural",
    "_check_tdd": "python.helpers.checks.structural",
    "_check_tailwind": "python.helpers.checks.structural",
    "_check_tailwind_content": "python.helpers.checks.structural",
    "_check_postcss": "python.helpers.checks.structural",
    # content checks
    "_check_landing_page_depth": "python.helpers.checks.content",
    "_check_placeholder_content": "python.helpers.checks.content",
    "_check_bdd_scenario_compliance": "python.helpers.checks.content",
    "_check_mock_data": "python.helpers.checks.content",
    "_check_dev_server": "python.helpers.checks.content",
    "_check_lint": "python.helpers.checks.content",
    "_check_async_params": "python.helpers.checks.content",
    "_check_tsconfig": "python.helpers.checks.content",
    "_check_plan_coverage": "python.helpers.checks.content",
    "_check_route_reachability": "python.helpers.checks.content",
    "_check_build_freshness_gate": "python.helpers.checks.content",
    "_check_nav_link_consistency": "python.helpers.checks.content",
    "_check_page_data_wiring": "python.helpers.checks.content",
    "_check_link_semantic_integrity": "python.helpers.checks.content",
    # verification checks
    "_check_browser_uat": "python.helpers.checks.verification",
    "_check_quality_eval": "python.helpers.checks.verification",
    "_check_lit": "python.helpers.checks.verification",
    "_check_readme": "python.helpers.checks.verification",
    "_check_response_quality": "python.helpers.checks.verification",
    "_check_e2e_delegated": "python.helpers.checks.verification",
    "_check_dev_server_for_user": "python.helpers.checks.verification",
    "_check_env_template": "python.helpers.checks.verification",
    "_check_error_boundaries": "python.helpers.checks.verification",
    # quality checks
    "_check_css_integrity": "python.helpers.checks.quality",
    "_check_css_apply_coherence": "python.helpers.checks.quality",
    "_check_prisma_provider_coherence": "python.helpers.checks.quality",
    "_check_lib_test_coverage": "python.helpers.checks.quality",
    "_check_blueprint_requirement_verification": "python.helpers.checks.quality",
    "_check_fetch_route_completeness": "python.helpers.checks.quality",
    "_check_content_presence": "python.helpers.checks.quality",
    "_check_stub_endpoints": "python.helpers.checks.quality",
    "_check_config_coherence": "python.helpers.checks.quality",
    "_check_pre_build_hint": "python.helpers.checks.quality",
    "_check_manifest_packages": "python.helpers.checks.quality",
    "_check_todo_hardcode_detection": "python.helpers.checks.quality",
    # requirements checks
    "_check_contract_assertions": "python.helpers.checks.requirements",
    "_check_env_example": "python.helpers.checks.requirements",
    "_check_form_route_completeness": "python.helpers.checks.requirements",
    "_check_server_health": "python.helpers.checks.requirements",
    "_check_theme_coherence": "python.helpers.checks.requirements",
    "_check_dead_code": "python.helpers.checks.requirements",
}



# ═══════════════════════════════════════════════════════════════════════
# P0-2: Trust Model Wiring
# ═══════════════════════════════════════════════════════════════════════


def wire_trust_into_gate_dispatch(
    agent_data: dict,
    check_name: str,
    passed: bool,
) -> dict:
    """Record a check result — STUB (gate trust model removed).

    Returns the expected dict shape so callers don't break.
    """
    return {
        "is_regression": False,
        "trust_score": 1.0,
        "check_name": check_name,
        "passed": passed,
    }


def __getattr__(name):
    """Lazy import for backward-compatible re-exports from checks/ sub-modules.

    GAP-2 FIX: This replaces top-level imports that caused circular dependency.
    On first access, the target module is imported and the attribute is cached
    on this module for subsequent O(1) lookups.
    """
    if name in _LAZY_EXPORTS:
        import importlib
        module = importlib.import_module(_LAZY_EXPORTS[name])
        attr = getattr(module, name)
        # Cache on this module for subsequent access (avoids repeated import)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ─── Stage-Based Gate Runner (Phase 1 Gate Refactor) ────────────────────


async def run_gate_checks(
    agent, gate: str, block_count: int, response: Any = None,
) -> bool:
    """Run only checks tagged with the given gate.

    Unlike run_all_checks() which runs ALL checks at delivery time, this
    runs only checks where check.gate == gate (or check.gate == "" for
    backward-compatible untagged checks).

    Escape mechanism: per-requirement partial status. When a gate blocks
    3 times, all requirements linked to the failing checks are marked
    partial and the gate allows through. Replaces universal_gate_budget.

    Args:
        agent: The agent instance.
        gate: "bdd", "tdd", or "done".
        block_count: Current gate attempt number (for logging).
        response: The response tool args (optional).

    Returns:
        True if response was BLOCKED, False if allowed.
    """
    if not are_gates_enabled():
        return False

    from python.helpers.gate_router import get_checks_for_gate, MAX_PARTIAL_ATTEMPTS
    from python.helpers.orchestrator_gate_common import resolve_project_dir_from_context

    project_dir = resolve_project_dir_from_context(agent.data)
    _is_web = _is_web_project(project_dir) if project_dir else False

    checks = get_checks_for_gate(gate)
    if not checks:
        logger.info(f"[GATE:{gate}] No checks registered — passing")
        return False

    ctx = CheckContext(
        agent=agent, agent_data=agent.data, response=response,
        block_count=block_count, project_dir=project_dir or "",
        passed_checks=[],
    )

    all_failures = []

    for check in checks:
        # Skip web_only for non-web projects
        if check.web_only and not _is_web:
            continue
        # Skip if prerequisites not met
        if should_skip_for_prerequisites(check.requires, ctx.passed_checks):
            continue

        try:
            result = await check.fn(ctx) if check.is_async else check.fn(ctx)
        except Exception as e:
            logger.error(f"[GATE:{gate}] Check '{check.name}' error: {e}")
            result = None  # Fail-open

        if result is not None:
            result_msg = result if isinstance(result, str) else f"Check '{check.name}' failed"
            all_failures.append((check.name, result_msg))
            logger.warning(f"[GATE:{gate}] Check '{check.name}' FAILED")
        else:
            ctx.passed_checks.append(check.name)

    if all_failures:
        # ── Per-requirement partial escape ──
        # Track gate-level attempt counter. After MAX_PARTIAL_ATTEMPTS (3),
        # mark affected requirements as partial and allow through.
        gate_attempt_key = f"_gate_{gate}_attempt"
        gate_attempt = agent.data.get(gate_attempt_key, 0) + 1
        agent.data[gate_attempt_key] = gate_attempt

        if gate_attempt >= MAX_PARTIAL_ATTEMPTS:
            # Escape: mark requirements whose gate STAGE is not yet done.
            # Bug 3 fix: filter by gate-specific stage status, not just
            # overall status. A req with bdd="completed" should NOT be
            # marked partial by BDD gate failure — its BDD work is done.
            from python.helpers.requirements_delegation_tracker import (
                mark_partial,
                get_partial_summary,
            )
            from python.helpers.requirements_stage import get_stage_status

            # Map gate → stage name (they're 1:1 in current architecture)
            gate_stage = gate  # "bdd" → "bdd", "tdd" → "tdd", "done" → "code"
            if gate == "done":
                gate_stage = "code"

            DONE_STAGE_STATUSES = ("completed", "verified", "partial", "failed")

            ledger = agent.data.get("_requirements_ledger", {})
            for req in ledger.get("requirements", []):
                # Skip if overall status is terminal
                if req.get("status") in ("completed", "verified", "partial", "failed"):
                    continue
                # Bug 3: Skip if this gate's specific stage is already done
                stage_val = get_stage_status(req, gate_stage)
                if stage_val in DONE_STAGE_STATUSES:
                    continue
                mark_partial(
                    agent.data,
                    req["id"],
                    reason=f"Gate '{gate}' failed {gate_attempt}× — "
                           f"checks: {', '.join(f[0] for f in all_failures[:5])}",
                    attempt=gate_attempt,
                    gate_name=gate,
                )

            summary = get_partial_summary(agent.data)
            # Store for response template consumption (not just logging)
            agent.data["_partial_summary"] = summary
            logger.warning(
                f"[GATE:{gate}] PARTIAL ESCAPE after {gate_attempt} attempts. "
                f"{len(summary)} requirements marked partial."
            )
            return False  # Allow through — partial accepted

        # Build batch rejection for this specific gate
        batch_msg = _build_batch_rejection_message(all_failures)

        # Store for retrieval by the extension
        agent.data["_last_gate_block_details"] = {
            "message": batch_msg,
            "gate": gate,
            "failures": [f[0] for f in all_failures],
            "attempt": gate_attempt,
        }
        agent.data["_last_gate_failing_check"] = all_failures[0][0]

        # Record for redelegation guard targeted remediation
        from python.helpers.redelegation_guard import set_failing_check, store_gate_block_details
        set_failing_check(agent.data, all_failures[0][0])
        store_gate_block_details(agent.data, all_failures[0][0], batch_msg)

        logger.warning(
            f"[GATE:{gate}] BLOCKED: {len(all_failures)} of "
            f"{len(checks)} checks failed (attempt {gate_attempt}/{MAX_PARTIAL_ATTEMPTS})"
        )
        return True

    # All checks passed — reset attempt counter for this gate
    agent.data.pop(f"_gate_{gate}_attempt", None)

    logger.info(
        f"[GATE:{gate}] All {len(checks)} applicable checks PASSED"
    )
    return False


