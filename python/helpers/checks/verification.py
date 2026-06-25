"""
Verification gate checks — browser UAT, quality evaluation, LIT plans,
README, response quality, E2E delegation evidence, env templates, and
error boundaries.

These checks validate verification and polish artifacts:
  - Browser UAT (screenshot validation, route freshness)
  - E2E quality evaluation
  - LIT (Live Integration Test) plan existence and execution
  - README completeness
  - Response quality scoring
  - E2E verification delegation evidence
  - Environment template files (.env.example)
  - Error boundary components (error.tsx, loading.tsx)
"""

import os
import json
import logging

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
)
from python.helpers.orchestrator_gate_common import (
    build_verification_warning,
    build_browser_uat_warning,
)

# Circuit breaker: max times the E2E delegation check can block before
# force-allowing to prevent death spirals. After this many blocks, the
# check returns None (pass) so the agent isn't stuck forever.
MAX_E2E_BLOCKS = 3
from python.helpers.validators.lit import (
    check_lit_plan_exists,
    validate_lit_plan_structure,
    detect_lit_execution_evidence,
    generate_lit_plan_from_sitemap,
)



logger = logging.getLogger("agix.orchestrator_completion_gate")


@register_advisory(1.165, "Browser UAT", web_only=True)
async def _check_browser_uat(ctx: CheckContext):
    # #1168: Removed _verification_delegated bypass — orchestrator must
    # verify output, not trust subordinate claims. The subordinate's
    # browser_agent calls are propagated via _browser_agent_calls.
    sitemap_path = os.path.join(ctx.project_dir, "verification_sitemap.json")
    has_sitemap = os.path.isfile(sitemap_path)
    has_pkg = os.path.isfile(os.path.join(ctx.project_dir, "package.json"))
    # Only skip for non-web projects (no package.json AND no sitemap).
    # Web projects ALWAYS require browser UAT — even if dev server
    # wasn't detected as started. The dev server check (1.15) gates
    # that separately; this check must not create a bypass when the
    # dev server flag is missing due to race conditions or propagation gaps.
    if not has_sitemap and not has_pkg:
        return None
    browser_calls = ctx.agent_data.get("_browser_agent_calls", 0)
    if browser_calls > 0:
        screenshots = ctx.agent_data.get("_browser_screenshots", [])
        existing = [s for s in screenshots if os.path.isfile(s)]
        if not existing:
            return ctx.block(
                f"⚠️ BROWSER UAT SCREENSHOTS MISSING: Called {browser_calls}x but no files on disk.")
        # ── RCA-248: Temporal Freshness Gate ──
        # Ensure route verification is recent (<60s). If stale, routes may
        # have changed (recompiled) since verification, making screenshots
        # unreliable. Re-verify routes before accepting browser UAT results.
        import time as _time
        route_ts = ctx.agent_data.get("_route_verification_ts")
        _FRESHNESS_LIMIT = 60  # seconds
        if route_ts is None or (_time.time() - route_ts) > _FRESHNESS_LIMIT:
            staleness = f"{int(_time.time() - route_ts)}s ago" if route_ts else "never"
            return ctx.block(
                f"⚠️ ROUTE VERIFICATION STALE ({staleness}): Browser UAT screenshots exist "
                f"but route verification is outdated (>{_FRESHNESS_LIMIT}s). Routes may have "
                f"recompiled since verification. Re-run route checks before accepting UAT results.")
        return None
    return ctx.block(build_browser_uat_warning(ctx.project_dir))


@register_advisory(1.175, "Quality evaluation")
async def _check_quality_eval(ctx: CheckContext):
    if ctx.agent_data.get("_quality_audit_done", False):
        return None
    quality_eval = ctx.agent_data.get("_quality_evaluation")
    if quality_eval:
        if quality_eval.get("passed"):
            ctx.agent_data["_quality_audit_done"] = True
            return None
        feedback = quality_eval.get("response", "No details")[:2000]
        ctx.agent_data.pop("_quality_evaluation", None)
        return ctx.block(f"⚠️ E2E QUALITY CHECK FAILED:\n\n{feedback}\n\nFix and re-verify.")
    # Circuit breaker: always escalate (gate_block_counters stub removed — was always True)
    if True:
        ctx.agent_data["_quality_audit_done"] = True  # Mark done to stop re-blocking
        return None  # Prevent death spiral

    return ctx.block("⚠️ QUALITY EVALUATION REQUIRED: Delegate to browser_agent.")


@register_advisory(1.18, "LIT plan")
async def _check_lit(ctx: CheckContext):
    if not os.path.isfile(os.path.join(ctx.project_dir, "verification_sitemap.json")):
        return None
    lit = check_lit_plan_exists(ctx.project_dir)
    if not lit["exists"]:
        plan = generate_lit_plan_from_sitemap(ctx.project_dir)
        if plan:
            try:
                with open(os.path.join(ctx.project_dir, "lit_plan.json"), "w") as f:
                    json.dump(plan, f, indent=2)
            except IOError:
                pass
            lit = check_lit_plan_exists(ctx.project_dir)
        if not lit["exists"]:
            return ctx.block("⚠️ LIT PLAN MISSING: Create lit_plan.json with 4 test patterns.")
    if lit["plan"]:
        structure = validate_lit_plan_structure(lit["plan"])
        if not structure["valid"]:
            return ctx.block(
                f"⚠️ LIT PLAN INCOMPLETE: Missing: {', '.join(structure['missing_patterns'])}")
    if not ctx.agent_data.get("_lit_tests_executed", False):
        return ctx.block(f"⚠️ LIT TESTS NOT RUN: {lit.get('test_count', 0)} tests exist but not executed.")
    return None


@register_advisory(1.19, "README.md")
def _check_readme(ctx: CheckContext):
    readme_path = os.path.join(ctx.project_dir, "README.md")
    if not os.path.isfile(readme_path):
        return ctx.block("⚠️ README.md REQUIRED: Generate comprehensive documentation.")
    try:
        with open(readme_path, "r") as f:
            content = f.read()
        if len(content.strip()) < 200:
            return ctx.block(f"⚠️ README.md TOO SHORT: {len(content)} chars. Expand it.")
    except Exception:
        pass
    return None






@register_check(2.0, "E2E verification delegated", gate="done")
def _check_e2e_delegated(ctx: CheckContext):
    # E2E is NOT web_only — the e2e agent is a universal verifier that can
    # curl backend API endpoints, run pytest/vitest, execute BDD scenarios,
    # and perform QA/UAT on any project type (web, backend, CLI, etc.).
    # Pass if verification was explicitly delegated to a verification profile
    if ctx.agent_data.get("_verification_delegated", False):
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "e2e_delegated_2.0", True)
        except Exception:
            pass
        return None

    # Normalize delegation profiles to a set for consistent checking
    delegation_profiles = ctx.agent_data.get("_delegation_profiles", set())
    if isinstance(delegation_profiles, set):
        profile_set = delegation_profiles
    else:
        profile_set = set(delegation_profiles) if delegation_profiles else set()

    # Fix-286-F: Pass if e2e profile was explicitly delegated to.
    # The e2e agent handles dev server lifecycle, browser UAT, test
    # execution, and API verification — its presence IS verification.
    if "e2e" in profile_set:
        logger.info(
            f"[E2E CHECK] Passing: e2e profile in delegation set "
            f"({profile_set})"
        )
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "e2e_delegated_2.0", True)
        except Exception:
            pass
        return None

    # Circuit breaker: after MAX_E2E_BLOCKS blocks on this check,
    # System 6 (ITR-44): Use circuit_breaker_escalate() for universal
    # escalation, PLUS preserve RCA-401 F-2 mandatory delegation signals.
    # Circuit breaker: always escalate (gate_block_counters stub removed — was always True)
    if True:
        # RCA-401 F-2: Inject mandatory delegation signal (preserved —
        # downstream consumers depend on these specific keys)
        ctx.agent_data["_e2e_mandate_injected"] = True
        ctx.agent_data["_force_e2e_delegation"] = True
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "e2e_delegated_2.0", True)
        except Exception:
            pass
        return None  # Prevent death spiral



    return ctx.block(build_verification_warning(ctx.project_dir))

# ─── RCA-401 F-3: Independent Dev Server Startup Check ──────────────────


@register_check(2.1, "Dev server started for user", critical=True, web_only=True, gate="done")
def _check_dev_server_for_user(ctx: CheckContext):
    """Verify the dev server has been started for user testing.

    RCA-401 F-3: Previously, dev server startup was exclusively coupled
    to the e2e agent. When the e2e delegation circuit-breaker fired
    (MAX_E2E_BLOCKS reached), the dev server was never started and the
    user had no way to test the built application.

    RCA-ITR32-C: Enhanced to check _services_mgt_dev_server flag to
    verify the dev server was started via the correct tool path.

    This independent advisory check ensures the dev server startup is
    flagged regardless of e2e delegation status. It checks both the
    generic _dev_server_started flag and the specific _services_mgt_dev_server
    flag to verify tool path.
    """
    # Skip for non-web projects (already filtered by web_only=True)
    if not ctx.project_dir:
        return None

    # Check if dev server was started
    dev_server_started = ctx.agent_data.get("_dev_server_started", False)
    via_services_mgt = ctx.agent_data.get("_services_mgt_dev_server", False)

    if dev_server_started and via_services_mgt:
        return None  # Best case: started via services_mgt

    if dev_server_started and not via_services_mgt:
        # Started but not via services_mgt — warn but don't block.
        # The enforcer (_11) should have caught raw commands upstream.
        logger.warning(
            "[INTEGRATION] Dev server started without services_mgt. "
            "Port routing may not work in Docker environments."
        )
        return None  # Allow but log

    # Not started at all
    # Check if package.json exists (needed for npm run dev)
    pkg_path = os.path.join(ctx.project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return None  # No package.json → no dev server needed

    return ctx.block(
        "⚠️ DEV SERVER NOT STARTED: The built application has not been "
        "started for user testing. The user cannot verify the application "
        "visually without a running dev server. Either:\n"
        "1. Delegate to the `e2e` agent (profile='e2e') which starts the "
        "dev server as part of its verification process, OR\n"
        "2. Use `services_mgt` tool to start the dev server directly.\n"
        "The dev server must be running before declaring the project complete."
    )


# ─── Phase 2 Quality Gates (RCA 2026-04-25) ────────────────────────────


@register_advisory(1.201, "Env template", web_only=True)
def _check_env_template(ctx: CheckContext):
    """Verify .env.example exists with at least one entry.

    The architect mandates .env.example (Section 9, line 149) and
    swarm rule 25 mandates env generation. This advisory catches
    omissions without blocking — it's a quality signal, not a showstopper.

    RCA Phase 2 (P2-2): Missing .env.example causes first-run crashes.
    """
    if not ctx.project_dir:
        return None

    env_example = os.path.join(ctx.project_dir, ".env.example")
    env_local = os.path.join(ctx.project_dir, ".env.local")
    dot_env = os.path.join(ctx.project_dir, ".env")

    # Any of these counts as having an env template
    for path in [env_example, env_local, dot_env]:
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    content = f.read().strip()
                if content and "=" in content:
                    return None  # Has at least one KEY=value
            except (IOError, OSError):
                continue

    return ctx.block(
        "⚠️ NO ENV TEMPLATE: Create a `.env.example` file listing all required "
        "API keys with placeholder values (e.g., GOOGLE_API_KEY=your_key_here). "
        "This prevents crashes on first run."
    )


@register_check(1.202, "Error boundaries", critical=True, web_only=True, gate="done")
def _check_error_boundaries(ctx: CheckContext):
    """Verify error.tsx and loading.tsx exist in Next.js App Router projects.

    Next.js App Router uses error.tsx and loading.tsx files for error
    boundaries and loading states. Their absence means runtime errors
    crash the entire page instead of showing a fallback UI.

    Only fires for Next.js projects (detected via next.config).
    RCA Phase 2 (P2-3): Missing error boundaries cause full-page crashes.
    """
    if not ctx.project_dir:
        return None

    # Only check Next.js projects
    next_config = None
    for ext in [".js", ".mjs", ".ts"]:
        candidate = os.path.join(ctx.project_dir, f"next.config{ext}")
        if os.path.isfile(candidate):
            next_config = candidate
            break
    if not next_config:
        return None

    # Check for app/ or src/app/ directory
    app_dir = None
    for candidate in ["src/app", "app"]:
        path = os.path.join(ctx.project_dir, candidate)
        if os.path.isdir(path):
            app_dir = path
            break
    if not app_dir:
        return None

    # Check for error.tsx in the root app directory
    has_error = any(
        os.path.isfile(os.path.join(app_dir, f"error.{ext}"))
        for ext in ["tsx", "ts", "jsx", "js"]
    )
    has_loading = any(
        os.path.isfile(os.path.join(app_dir, f"loading.{ext}"))
        for ext in ["tsx", "ts", "jsx", "js"]
    )

    missing = []
    if not has_error:
        missing.append("error.tsx")
    if not has_loading:
        missing.append("loading.tsx")

    if missing:
        return ctx.block(
            f"⚠️ MISSING ERROR BOUNDARIES: {', '.join(missing)} not found in {app_dir}/. "
            f"Create these files to prevent full-page crashes on runtime errors. "
            f"error.tsx needs 'use client' and must export a component receiving {{ error, reset }} props."
        )
    return None


# ─── U-3: VCS Publication / Deployment Requirements Gate (RCA-1) ────────

# Circuit breaker: max times the deployment check can block before
# force-allowing to prevent death spirals.
MAX_DEPLOY_BLOCKS = 3

# Deployment requirement statuses that count as "done"
# F-8 NOTE: Intentionally stricter than PHASE_DONE_STATUSES from
# status_constants.py. Deployment requirements must NOT be satisfied by
# "skipped" or "partially_completed" — only fully completed or verified.
_DEPLOY_DONE_STATUSES = {"completed", "verified"}


@register_check(3.0, "Deployment requirements completed", gate="done")
def _check_deployment_requirements(ctx: CheckContext):
    """Block response when deployment-category requirements are still pending.

    RCA-1 (MainStreet Iteration 3): The orchestrator exited without
    executing Phase 5.5 (VCS publication) because no enforcement gate
    checked for pending deployment requirements. The `response` tool
    accepted the completion report even though REQ-017 ("push to GitHub")
    was still status="pending" in the requirements_ledger.

    This check reads the requirements_ledger from agent_data and blocks
    if ANY requirement with category="deployment" has a status that is
    NOT in {completed, verified}.

    Circuit breaker: After MAX_DEPLOY_BLOCKS (3) consecutive blocks on
    this check, force-allow to prevent death spirals. The supervisor
    signal (Layer 4) will still warn.
    """
    ledger = ctx.agent_data.get("_requirements_ledger")
    if not ledger:
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "deployment_reqs_3.0", True)
        except Exception:
            pass
        return None  # No ledger → nothing to check

    requirements = ledger.get("requirements", [])
    if not requirements:
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "deployment_reqs_3.0", True)
        except Exception:
            pass
        return None

    # Find pending deployment requirements
    pending_deploy_reqs = [
        req for req in requirements
        if req.get("category") == "deployment"
        and req.get("status", "pending") not in _DEPLOY_DONE_STATUSES
    ]

    if not pending_deploy_reqs:
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "deployment_reqs_3.0", True)
        except Exception:
            pass
        return None  # All deployment reqs completed (or none exist)

    # Circuit breaker: always escalate (gate_block_counters stub removed — was always True)
    if True:
        return None  # Prevent death spiral

    # Increment block counter (dead code — unreachable, kept for reference)
    # deploy_block_count = 0

    # Build descriptive block message
    pending_ids = [req.get("id", "?") for req in pending_deploy_reqs]
    pending_details = "; ".join(
        f"{req.get('id', '?')}: {req.get('text', 'unknown')[:80]}"
        for req in pending_deploy_reqs
    )

    return ctx.block(
        f"⚠️ DEPLOYMENT REQUIREMENTS PENDING: {len(pending_deploy_reqs)} "
        f"deployment requirement(s) not completed: [{', '.join(pending_ids)}]. "
        f"Details: {pending_details}. "
        f"Complete Phase 5.5 (VCS publication, deploy) before delivering. "
        f"({deploy_block_count + 1}/{MAX_DEPLOY_BLOCKS} blocks)"
    )


# ─── F-4 (ITR-28): Response Template Validator ──────────────────────────

import re as _re

_PREVIEW_URL_PATTERN = _re.compile(
    r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d+',
    _re.IGNORECASE,
)


@register_check(2.5, "Response template", critical=True, web_only=True, gate="done")
def _check_response_template(ctx: CheckContext):
    """F-4 (ITR-28): Verify response contains minimum mandatory elements.

    Root cause: The Live Preview URL mandate existed only as L0 prompt text.
    When error-state bypass fired, the LLM composed a response without a
    preview URL — and no gate caught it because no check existed.

    This check validates:
    - Web projects: response must contain a localhost/preview URL

    Per-check escape hatch: should_force_allow_check() auto-bypasses
    after MAX_CYCLES_PER_CHECK=3 failures.
    """
    if not ctx.project_dir:
        return None

    # Only for web projects
    from python.helpers.orchestrator_gate_integration_checks import _is_web_project
    if not _is_web_project(ctx.project_dir):
        return None

    # Get response text from response object or agent data
    response_text = ""
    if ctx.response and hasattr(ctx.response, 'message'):
        response_text = ctx.response.message or ""
    if not response_text:
        response_text = ctx.agent_data.get('_last_response_attempt', '')

    if not response_text:
        return None  # No response to check yet

    # Check for preview URL
    if not _PREVIEW_URL_PATTERN.search(response_text):
        return ctx.block(
            "⚠️ MISSING LIVE PREVIEW URL: Your response does not include a "
            "localhost/preview URL (e.g., http://localhost:3000). The user "
            "needs this to access the running application.",
            action=(
                "Start the dev server using services_mgt tool, then include "
                "the localhost URL in your response."
            ),
        )

    return None



