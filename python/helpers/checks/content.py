"""
Content gate checks — landing pages, placeholder content, BDD scenarios,
mock data, dev server, linting, async params, routes, navigation.

These checks validate the quality and completeness of project content:
  - Landing page content depth
  - Placeholder/dummy content detection
  - BDD scenario compliance (structural acceptance criteria)
  - Mock/fake data in production code
  - Dev server lifecycle
  - Lint execution evidence
  - Next.js async params (v15+)
  - TypeScript path aliases
  - Plan implementation coverage
  - Route reachability (static + live curl)
  - Build freshness / staleness
  - Navigation link consistency
  - Page data API wiring
"""

import os
import json
import logging
import time as _time

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
)
from python.helpers.orchestrator_gate_common import format_gate_block
from python.helpers.universal_gate_budget import gate_check, get_block_count
from python.helpers.validators.route_reachability import (
    check_route_reachability,
    curl_verify_routes,
    check_build_freshness as check_build_freshness_fn,
    check_nav_existence,
    check_nav_link_consistency,
    check_plan_vs_implementation,
)
from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS
from python.helpers.source_scanner import list_project_files, read_project_files


logger = logging.getLogger("agix.orchestrator_completion_gate")







# BDD Scenario Compliance (BDD Frontend Quality Pipeline)
# Verifies source code satisfies mechanical acceptance criteria from bdd-scenarios.md.
# Critical (blocking) + max 3 blocks circuit breaker = escape hatch prevents death spirals.
@register_check(1.145, "BDD: Scenario compliance", critical=True, gate="bdd")
def _check_bdd_scenario_compliance(ctx: CheckContext):
    """Verify project source code satisfies BDD acceptance scenarios.

    5-Why Root Cause (MSR_Smoke_1777134673): No mechanical acceptance criteria
    existed. Frontend output was skeletal because nothing enforced structure
    (section count, nav, footer, CTA) at the quality gate level.
    """
    from python.helpers.validators.bdd_scenarios import validate_bdd_scenarios

    # Always validate first — BLOCKING failures bypass the circuit breaker.
    result = validate_bdd_scenarios(ctx.project_dir)
    if result is None:
        return None  # No BDD scenarios → skip

    if result["passed"]:
        # ── NEW (I-2): BDD Implementation Quality — fill the reserved L2 slot ──
        # When structural BDD checks pass, verify integration implementations are
        # REAL (not mocked/stubbed). This fills the L2 semantic slot in the
        # BDD implementation verifier architecture.
        try:
            from python.helpers.bdd_implementation_verifier import (
                assess_bdd_implementation_quality,
            )
            quality = assess_bdd_implementation_quality(ctx.project_dir)
            if quality and not quality["passed"]:
                if True:  # gate_block_counters stub removed — was always True
                    return None  # Prevent death spiral, escalation was recorded


                violation_summary = "; ".join(
                    v["reason"][:120] for v in quality["violations"][:3]
                )
                return ctx.block(
                    f"⛔ BDD IMPLEMENTATION QUALITY: {len(quality['violations'])} issue(s). "
                    f"{violation_summary}. "
                    f"Check integration code — BDD THEN clauses require REAL API implementations, "
                    f"not mocked/stubbed code."
                )
        except ImportError:
            pass  # bdd_implementation_verifier not available — skip quality check
        except Exception as e:
            logger.debug(f"BDD implementation quality check error (non-fatal): {e}")
        return None  # All scenarios satisfied


    # ── COMPLIANCE: BLOCKING failures — higher threshold circuit breaker ──
    # These are serious (PCI-DSS, CAN-SPAM) so they get more attempts than
    # non-blocking (5 vs 3), but still have an escape hatch to prevent
    # infinite agent loops. Some progress is always better than hard-locking.
    has_blocking = result.get("has_blocking_failures", False)
    if has_blocking:
        MAX_BLOCKING_BLOCKS = 5
        # System 6 (ITR-44): Use circuit_breaker_escalate() instead of silent pass.
        if True:  # gate_block_counters stub removed — was always True
            return None  # Prevent death spiral, but escalation was recorded


        blocking_failures = [
            f for f in result["failures"]
            if f.get("severity") == "BLOCKING"
        ]
        failure_summary = "; ".join(
            f"{f['scenario']}: expected {f['clause']}, got {f['actual']}"
            for f in blocking_failures[:3]
        )
        return ctx.block(
            f"🚨 BLOCKING COMPLIANCE FAILURES ({blocking_blocks + 1}/{MAX_BLOCKING_BLOCKS}): "
            f"{len(blocking_failures)} compliance scenario(s) failed. "
            f"Failures: {failure_summary}. "
            f"Read docs/bdd-scenarios.md and fix the source code."
        )

    # ── Non-blocking failures: circuit breaker applies ──
    MAX_BDD_BLOCKS = 3
    # System 6 (ITR-44): Use circuit_breaker_escalate() instead of silent pass.
    if True:  # gate_block_counters stub removed — was always True
        return None  # Prevent death spiral, but escalation was recorded


    failure_summary = "; ".join(
        f"{f['scenario']}: expected {f['clause']}, got {f['actual']}"
        for f in result["failures"][:3]
    )
    return ctx.block(
        f"⛔ BDD SCENARIO FAILURES: {result['passed_count']}/{result['total_scenarios']} "
        f"scenarios passed. Failures: {failure_summary}. "
        f"Read docs/bdd-scenarios.md and fix the frontend source code to satisfy "
        f"the acceptance criteria (section counts, nav, footer, design system)."
    )


# [REMOVED] _check_mock_data — deleted with mock_data_guard.py (heuristic gate disabled)


# ─── Port Range Validation (RCA-335 ISS-3) ───────────────────────────
# services_mgt allocates ports in 5100-5500 range. Any port outside this
# range means the agent bypassed services_mgt (e.g., raw `npm run dev`
# on port 3000), which won't work in Docker.

_MANAGED_PORT_MIN = 5100
_MANAGED_PORT_MAX = 5500


def _is_managed_port(port) -> bool:
    """Check if a port is within the services_mgt managed range (5100-5500).

    RCA-335: Agent used port 3000 (default `npm run dev`) instead of
    services_mgt auto-allocated port. Port 3000 is not docker-mapped
    and will never be accessible.

    Args:
        port: Port number (int or str).

    Returns:
        True if port is in the managed range [5100, 5500].
    """
    try:
        port_int = int(port)
    except (ValueError, TypeError):
        return False
    return _MANAGED_PORT_MIN <= port_int <= _MANAGED_PORT_MAX


@register_check(1.15, "Dev server started", critical=True, requires=["npm install"], web_only=True, gate="done")
def _check_dev_server(ctx: CheckContext):
    # Dev server detection via agent_data flags
    has_started_flag = ctx.agent_data.get("_dev_server_started", False)
    has_port = bool(ctx.agent_data.get("_dev_server_port", ""))
    via_services_mgt = ctx.agent_data.get("_services_mgt_dev_server", False)

    if not has_started_flag and not has_port:
        return ctx.block(
            f"⚠️ DEV SERVER NOT STARTED: Use services_mgt tool with "
            f"action=start_service to start the dev server in {ctx.project_dir}. "
            f"Do NOT use raw 'npm run dev' — it must be managed for port routing."
        )

    if has_started_flag and not via_services_mgt:
        logger.warning(
            f"[INTEGRATION] Dev server started but NOT via services_mgt "
            f"(flag source: code_execution_tracker or delegation). "
            f"Port routing may fail in Docker environments."
        )

    # RCA-335: Validate port is in managed range
    port = ctx.agent_data.get("_dev_server_port", "")
    if port and not _is_managed_port(port):
        return ctx.block(
            f"⚠️ DEV SERVER ON UNMANAGED PORT {port}: Port must be in range "
            f"{_MANAGED_PORT_MIN}-{_MANAGED_PORT_MAX} (allocated by services_mgt). "
            f"Port {port} is not docker-mapped and won't be accessible. "
            f"Stop the current server and restart using services_mgt tool with "
            f"action=start_service (port will be auto-allocated)."
        )

    return None





@register_advisory(1.152, "tsconfig path alias", web_only=True)
def _check_tsconfig(ctx: CheckContext):
    is_nextjs = any(
        os.path.isfile(os.path.join(ctx.project_dir, f))
        for f in ("next.config.js", "next.config.ts", "next.config.mjs"))
    if not is_nextjs:
        return None
    tsconfig_path = os.path.join(ctx.project_dir, "tsconfig.json")
    if not os.path.isfile(tsconfig_path):
        return None
    try:
        with open(tsconfig_path, "r", encoding="utf-8") as f:
            tsconfig = json.load(f)
        paths = tsconfig.get("compilerOptions", {}).get("paths", {})
        if not any("@" in key for key in paths):
            return ctx.block(
                f"⚠️ TSCONFIG PATH ALIAS MISSING: Add @/* path alias to {tsconfig_path}.")
    except Exception as e:
        logger.warning(f"[INTEGRATION] Failed to parse tsconfig.json: {e}")
    return None


@register_check(1.154, "Plan implementation coverage", critical=True, gate="bdd")
def _check_plan_coverage(ctx: CheckContext):
    """Verify architect's planned routes are actually implemented.

    5-Why Root Cause: The architect's plan is ephemeral — produced as chat
    output, never persisted. E2E gates validated what EXISTS (circular),
    not what was PLANNED. Agent builds 4 of 7 planned pages, gate says OK.

    Fix: Architect persists plan as architect_plan.json. This gate
    cross-checks planned_routes against actual page.tsx files.
    """
    result = check_plan_vs_implementation(ctx.project_dir)
    if result is None:
        return None  # No architect_plan.json — graceful degradation
    if result["missing_routes"]:
        missing = ", ".join(result["missing_routes"][:8])
        ratio = result["coverage_ratio"]
        return ctx.block(
            f"⛔ PLAN COVERAGE GAP: Architect planned {result['planned_count']} pages "
            f"but only {result['implemented_count']} exist ({ratio:.0%} coverage). "
            f"Missing: {missing}. Create page.tsx files for each route or "
            f"update architect_plan.json to remove dropped routes.")
    return None


@register_check(1.155, "Route reachability", critical=True, requires=["npm install"], web_only=True, gate="done")
def _check_route_reachability(ctx: CheckContext):
    """Verify every <Link href> in components has a matching App Router page.
    Then curl each route against the running dev server for live validation.

    Forgejo #1168: Prevents shipping broken navigation links.
    Evidence stored in agent_data for orchestrator to validate against plan.
    """
    # Static check: do page files exist for each Link href?
    result = check_route_reachability(ctx.project_dir)
    if result is None:
        return None
    if result["has_missing"]:
        missing = result["missing_routes"][:8]
        # Wire: Inject missing routes as trackable requirements
        try:
            from python.helpers.route_remediation import inject_route_remediation, build_route_remediation_message
            inject_route_remediation(ctx.agent_data, missing)
            return ctx.block(build_route_remediation_message(missing))
        except Exception as _rr_err:
            logger.debug(f"Route remediation injection failed: {_rr_err}")
            return ctx.block(
                f"⛔ MISSING ROUTES: Navigation links point to pages that don't exist: "
                f"{', '.join(missing)}. Create page.tsx files for each route under src/app/.",
                action=(
                    f"Delegate a TARGETED fix to the code agent: Create these page files "
                    f"with default export React components: "
                    f"{', '.join(f'src/app{r}/page.tsx' for r in missing[:4])}. "
                    f"Do NOT re-scaffold or re-delegate the entire project."
                ),
            )

    # Live check: curl each route against running dev server
    if ctx.agent_data.get("_dev_server_started", False):
        # FIX Iteration 11b: Use the propagated port from services_mgt
        # instead of defaulting to 3000 from package.json. The subordinate
        # sets _dev_server_port when it starts the server via services_mgt.
        port_override = ctx.agent_data.get("_dev_server_port")
        port = int(port_override) if port_override else None
        live = curl_verify_routes(ctx.project_dir, port=port)
        if live is not None:
            # Store evidence for orchestrator validation
            ctx.agent_data["_route_verification_evidence"] = live
            # RCA-248: Stamp timestamp for browser UAT freshness gate
            ctx.agent_data["_route_verification_ts"] = _time.time()
            if not live["all_reachable"]:
                # Separate soft-404s from hard errors for clearer guidance
                soft_404s = live.get("soft_404s", [])
                hard_errors = [r for r in live["unreachable"] if r not in soft_404s]
                error_bodies = live.get("error_bodies", {})

                parts = []
                if hard_errors:
                    # Include error body snippets for each failing route
                    error_details = []
                    for route in hard_errors[:4]:
                        body = error_bodies.get(route, "")
                        if body:
                            error_details.append(f"{route} → {body[:120]}")
                        else:
                            error_details.append(route)
                    parts.append(f"HTTP errors: {'; '.join(error_details)}")
                if soft_404s:
                    parts.append(
                        f"Soft-404s (HTTP 200 but 404 content): {', '.join(soft_404s[:4])}"
                    )
                detail = "; ".join(parts) if parts else ", ".join(live["unreachable"][:5])
                # Wire: Inject unreachable routes as trackable requirements
                all_unreachable = hard_errors + soft_404s
                try:
                    from python.helpers.route_remediation import inject_route_remediation, build_route_remediation_message
                    inject_route_remediation(ctx.agent_data, all_unreachable[:8])
                    return ctx.block(build_route_remediation_message(all_unreachable[:8]))
                except Exception as _rr_err:
                    logger.debug(f"Route remediation injection failed: {_rr_err}")
                    return ctx.block(
                        f"⛔ ROUTES NOT REACHABLE: Dev server running but these routes "
                        f"have problems: {detail}. Fix the pages — ensure each route "
                        f"renders real content, not a generic 404/error page.")
            logger.info(f"[ROUTE CHECK] Live: {live['summary']}")
    else:
        logger.debug("[ROUTE CHECK] Dev server not started — skipping live route verification")
    return None


@register_check(1.1551, "Build staleness", critical=True, web_only=True, gate="done")
def _check_build_freshness_gate(ctx: CheckContext):
    """Verify production build is not stale (source files added after build).

    5-Why Root Cause: Agent adds files after `npm run build` → pages exist
    in src/ but return 404 because the build output predates the source.
    Fix: Detect build staleness and force a rebuild before UAT.
    """
    result = check_build_freshness_fn(ctx.project_dir)
    if result is None:
        return None
    if result["stale"]:
        newer = ", ".join(result["newer_files"][:5])
        return ctx.block(
            f"⛔ STALE BUILD: These source files were modified AFTER the last build: "
            f"{newer}. Run `npm run build` again to include these changes.")
    return None


@register_check(1.15515, "Nav existence", critical=True, web_only=True, gate="done")
def _check_nav_existence(ctx: CheckContext):
    """Verify multi-page apps have a navigation component.

    M-7 Fix: Route reachability returned None (vacuous pass) when zero
    links existed. For multi-page apps, zero navigation links means the
    user can't navigate between pages — this is a hard failure, not a pass.
    """
    result = check_nav_existence(ctx.project_dir)
    if result is None:
        return None  # Single-page app or nav exists
    return ctx.block(
        f"⛔ NO NAVIGATION: {result['message']} "
        f"Multi-page apps MUST have a shared navigation component "
        f"(navbar, sidebar, or menu) in layout.tsx so users can reach all pages."
    )


@register_check(1.1552, "Nav-link consistency", critical=True, web_only=True, gate="done")
def _check_nav_link_consistency(ctx: CheckContext):
    """Verify all navigation links point to pages that actually exist.

    5-Why Root Cause: Agent creates sidebar with links to /dashboard/prospects,
    /dashboard/analytics, etc. but never creates the corresponding page files.
    The existing route reachability check only verifies sitemap routes, not
    href values embedded in layout/nav components.
    """
    result = check_nav_link_consistency(ctx.project_dir)
    if not result["missing_pages"]:
        return None
    missing = ", ".join(result["missing_pages"][:6])
    return ctx.block(
        f"⛔ NAV LINKS WITHOUT PAGES: Layout/nav components have links to routes "
        f"with no page.tsx: {missing}. Either create the page files or remove/disable "
        f"the nav links (use href='#' with a 'Coming Soon' indicator).")





# ─── RCA-327: Content Density Gate ────────────────────────────────────────

# Minimum non-blank lines for a page file to be considered non-skeleton.
MIN_PAGE_LINES = 25

# Page file patterns to check for content density.
_PAGE_FILENAMES = {"page.tsx", "page.jsx", "page.js", "index.tsx", "index.jsx", "index.js"}


@register_check(1.16, "Page content density", critical=False, web_only=True, gate="done")
def _check_page_content_density(ctx):
    """SS-2: Semantic page content quality check.

    2-layer detection architecture:
      Layer 1 (fast): Walk project for page files, check non-blank line count.
                      Files below MIN_PAGE_LINES are immediately flagged.
      Layer 2 (semantic): For files that pass L1, check semantic embedding
                          similarity against the user prompt. Catches pages
                          with 30+ lines of boilerplate/lorem ipsum that
                          have zero relevance to the requirement.

    Replaces the old line-count-only heuristic that would pass any file
    with >25 lines regardless of content quality. (ITR-44 RCA, SS-2)
    """
    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    # Get the user prompt for semantic checking
    prompt = (
        ctx.agent_data.get("_original_prompt", "")
        or ctx.agent_data.get("_user_prompt", "")
    )

    skeleton_pages = []

    for fpath in list_project_files(ctx.project_dir):
        fname = os.path.basename(fpath)
        if fname not in _PAGE_FILENAMES:
            continue

        relpath = os.path.relpath(fpath, ctx.project_dir)

        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (IOError, OSError):
            continue

        # ── Layer 1: Line count (fast filter) ──
        non_blank = [line for line in content.splitlines() if line.strip()]
        if len(non_blank) < MIN_PAGE_LINES:
            skeleton_pages.append({
                "path": relpath,
                "lines": len(non_blank),
                "reason": "too_few_lines",
            })
            continue

        # ── Layer 2: Semantic quality (catches boilerplate with many lines) ──
        if prompt and len(prompt.strip()) > 10:
            try:
                from python.helpers.validators.semantic_fidelity import (
                    check_file_content_quality,
                )
                result = check_file_content_quality(fpath, prompt)
                if not result["quality_pass"]:
                    skeleton_pages.append({
                        "path": relpath,
                        "lines": len(non_blank),
                        "reason": result.get("reason", "low_quality"),
                        "similarity": result.get("similarity", 0.0),
                    })
            except Exception as e:
                logger.debug(f"[PAGE DENSITY] Semantic check skipped for {relpath}: {e}")

    if not skeleton_pages:
        return None

    # Report the worst offenders
    worst = sorted(skeleton_pages, key=lambda p: p.get("similarity", p["lines"]))[:3]
    details = []
    for p in worst:
        if p.get("reason") == "too_few_lines":
            details.append(f"{p['path']} ({p['lines']} lines)")
        else:
            sim = p.get('similarity', 0)
            details.append(f"{p['path']} (semantic={sim:.2f}, {p.get('reason', 'low_quality')})")
    summary = ", ".join(details)

    return ctx.block(
        f"⚠️ SKELETON PAGES: {len(skeleton_pages)} page(s) have low content quality: {summary}. "
        f"Add real content — hero sections, feature grids, CTAs, forms. "
        f"Pages must semantically match the user's requirements, not just have many lines.",
        action=(
            f"Add substantive content to skeleton pages. Each page needs real UI components "
            f"that match the user's prompt — not boilerplate or lorem ipsum."
        ),
    )





# ─── Gap 1: Framework Anti-Pattern Detection ──────────────────────────
# RCA: Previous hardcoded pattern advice told agents to add `export const dynamic = 'force-dynamic'`
# WITHOUT warning that this MUST NOT coexist with 'use client' in the same file.
# This co-occurrence is a Next.js anti-pattern that causes 100% route failure
# (HTTP 500). The existing 71 checks had ZERO detection of this pattern.
#
# 2-Layer Architecture:
#   Layer 1 (deterministic): Strip comments, scan .tsx/.ts files for
#     co-occurrence of 'use client' directive + 'export const dynamic'.
#   Layer 2 (LLM): Reserved but not needed — signals are unambiguous.

import re

# DUP-3: Uses shared DEFAULT_PROJECT_SKIP_DIRS from project_scan_constants.
_ANTI_PATTERN_SKIP_DIRS = DEFAULT_PROJECT_SKIP_DIRS

# File extensions to scan
_ANTI_PATTERN_SCAN_EXTENSIONS = {".tsx", ".ts"}

# Patterns for Layer 1 deterministic detection
_USE_CLIENT_DIRECTIVE_RE = re.compile(
    r"""^['"]use client['"];?\s*$""",
    re.MULTILINE,
)
_EXPORT_DYNAMIC_RE = re.compile(
    r"""^export\s+const\s+dynamic\s*=""",
    re.MULTILINE,
)


def _strip_comments(source: str) -> str:
    """Strip single-line (//) and multi-line (/* */) comments from source.

    Preserves line structure so line numbers remain valid for reporting.
    Does NOT strip 'use client' directives that appear as string literals
    at the top of the file (those are real directives, not comments).
    """
    # Remove multi-line comments (/* ... */), replacing with equivalent newlines
    def _replace_block(m):
        return "\n" * m.group(0).count("\n")
    result = re.sub(r"/\*.*?\*/", _replace_block, source, flags=re.DOTALL)
    # Remove single-line comments (// ...)
    result = re.sub(r"//[^\n]*", "", result)
    return result


def _is_use_client_directive(line: str) -> bool:
    """Check if a line is a real 'use client' directive (not in a string).

    A 'use client' directive is the FIRST non-empty, non-comment line of
    the file and must be a standalone string literal: 'use client' or
    "use client", optionally followed by a semicolon.

    This distinguishes from 'use client' appearing inside a larger string
    like: const msg = "Remember to add 'use client'";
    """
    stripped = line.strip()
    return stripped in (
        "'use client'",
        "'use client';",
        '"use client"',
        '"use client";',
    )


def _scan_file_for_anti_pattern(filepath: str):
    """Scan a single file for the 'use client' + 'export const dynamic' anti-pattern.

    Returns a dict with violation details if found, or None if clean.
    Layer 1 (deterministic): strips comments, then checks for co-occurrence.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            raw_content = f.read()
    except (IOError, OSError):
        return None

    # Strip comments to avoid false positives
    content = _strip_comments(raw_content)

    # Check for 'use client' directive — must be a real directive line,
    # not 'use client' buried inside a string literal.
    has_use_client = False
    use_client_line = 0
    for i, line in enumerate(content.splitlines(), 1):
        if _is_use_client_directive(line):
            has_use_client = True
            use_client_line = i
            break
        # 'use client' must be the first non-empty line (after comments stripped)
        if line.strip():
            break  # First non-empty line isn't 'use client' → no directive

    if not has_use_client:
        return None

    # Check for 'export const dynamic' in the same file
    dynamic_match = _EXPORT_DYNAMIC_RE.search(content)
    if not dynamic_match:
        return None

    # Find line number of the dynamic export
    dynamic_line = content[:dynamic_match.start()].count("\n") + 1

    return {
        "use_client_line": use_client_line,
        "dynamic_line": dynamic_line,
    }


@register_check(1.157, "Framework anti-pattern", critical=True, web_only=True, gate="tdd")
def _check_framework_anti_patterns(ctx: CheckContext):
    """Detect 'use client' + 'export const dynamic' co-occurrence in same file.

    Gap 1 Fix: Previous hardcoded pattern advice told agents to add `export const dynamic =
    'force-dynamic'` without warning this MUST NOT be in a 'use client' file.
    This co-occurrence is a Next.js anti-pattern causing 100% HTTP 500 on
    every route where it occurs.

    Layer 1 (deterministic): Scans all .tsx/.ts files for co-occurrence of
      'use client' directive AND 'export const dynamic' in the same file.
      Comments are stripped before scanning to prevent false positives.
    Layer 2 (LLM): Not invoked — the signals are unambiguous binary patterns.
    """
    if not ctx.project_dir:
        return None

    # Scan directories: try src/ first, fall back to app/, scan both if both exist
    scan_roots = []
    src_dir = os.path.join(ctx.project_dir, "src")
    app_dir = os.path.join(ctx.project_dir, "app")
    if os.path.isdir(src_dir):
        scan_roots.append(src_dir)
    if os.path.isdir(app_dir):
        scan_roots.append(app_dir)

    if not scan_roots:
        return None  # No source directories to scan

    violations = []  # List of (relative_path, use_client_line, dynamic_line)

    for scan_root in scan_roots:
        for fpath in list_project_files(scan_root, extensions=_ANTI_PATTERN_SCAN_EXTENSIONS):
            result = _scan_file_for_anti_pattern(fpath)
            if result is not None:
                rel_path = os.path.relpath(fpath, ctx.project_dir)
                violations.append((
                    rel_path,
                    result["use_client_line"],
                    result["dynamic_line"],
                ))

    if not violations:
        return None

    # Build a clear block message with file paths and line numbers
    file_details = []
    for rel_path, uc_line, dyn_line in violations[:5]:
        file_details.append(
            f"{rel_path} (line {uc_line}: 'use client', line {dyn_line}: export const dynamic)"
        )
    details_str = "; ".join(file_details)
    overflow = f" ... and {len(violations) - 5} more" if len(violations) > 5 else ""

    return ctx.block(
        f"⛔ FRAMEWORK ANTI-PATTERN: {len(violations)} file(s) have BOTH "
        f"'use client' AND 'export const dynamic' — this causes HTTP 500. "
        f"Files: {details_str}{overflow}. "
        f"FIX: Remove 'export const dynamic' from client component files. "
        f"If you need dynamic rendering, create a SEPARATE server component "
        f"wrapper (without 'use client') that has 'export const dynamic = "
        f"\"force-dynamic\"' and imports the client component."
    )





@register_advisory(1.159, "Env usage audit", web_only=True)
def _check_env_usage_audit_advisory(ctx):
    """Advisory: scan source for process.env.X and cross-reference .env.example.
    Addresses Class F (Env Var issues, 6 audits).
    """
    try:
        import re
        project_dir = ctx.project_dir
        if not project_dir or not os.path.isdir(project_dir):
            return None

        # Step 1: Scan source files for process.env.VARIABLE_NAME references
        env_refs = set()
        src_dirs = ["src", "app", "pages", "lib", "components", "utils"]
        for src_dir in src_dirs:
            src_path = os.path.join(project_dir, src_dir)
            if not os.path.isdir(src_path):
                continue
            for dirpath, _, filenames in os.walk(src_path):
                if "node_modules" in dirpath or ".next" in dirpath:
                    continue
                for fname in filenames:
                    if not fname.endswith((".ts", ".tsx", ".js", ".jsx")):
                        continue
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        # Match process.env.VARIABLE_NAME
                        matches = re.findall(r"process\.env\.([A-Z][A-Z0-9_]+)", content)
                        env_refs.update(matches)
                    except (IOError, OSError):
                        continue

        if not env_refs:
            return None  # No env refs found

        # Step 2: Read .env.example (or .env.local, .env)
        defined_vars = set()
        for env_file in [".env.example", ".env.local", ".env", ".env.development"]:
            env_path = os.path.join(project_dir, env_file)
            if not os.path.isfile(env_path):
                continue
            try:
                with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or "=" not in line:
                            continue
                        key = line.split("=", 1)[0].strip()
                        if key:
                            defined_vars.add(key)
            except (IOError, OSError):
                continue

        # Built-in Next.js env vars that don't need .env entries
        BUILTIN_VARS = {
            "NODE_ENV", "NEXT_PUBLIC_VERCEL_URL", "VERCEL_URL",
            "VERCEL", "CI", "NEXT_RUNTIME", "PORT",
        }

        # Step 3: Find missing entries
        missing = env_refs - defined_vars - BUILTIN_VARS
        if not missing:
            return None

        return (
            f"⚠️ ENV USAGE AUDIT: {len(missing)} env var(s) referenced in source "
            f"but not defined in .env files: {', '.join(sorted(missing)[:5])}"
            + (f" (and {len(missing) - 5} more)" if len(missing) > 5 else "")
        )
    except Exception as e:
        logger.debug(f"[ENV USAGE AUDIT] Validator error: {e}")
        return None


# ─── PARTIAL-4: Content Fidelity Check (Phase 5) ────────────────────────
# gate_phase_scope.py:35 registers "check_content_fidelity" as Phase 5.
# This check validates that the generated code actually contains the key
# terms, requirements, and specifics from the original user prompt.
# Without this, agents can ship technically valid pages that ignore the
# user's business name, prices, URLs, and feature requests.

from python.helpers.validators.semantic_fidelity import check_semantic_fidelity

@register_check(1.161, "Content fidelity", critical=True, gate="tdd")
def _check_content_fidelity(ctx: CheckContext):
    """Phase 5: Verify generated code contains key terms from the user prompt.

    System 6 Phase 4 (ITR-44): Wraps the reusable check_semantic_fidelity 
    validator so both Orchestrator and Code Agent share the same logic.
    """
    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    # Get the original user prompt (try both keys)
    prompt = (
        ctx.agent_data.get("_original_prompt", "")
        or ctx.agent_data.get("_user_prompt", "")
    )
    if not prompt or len(prompt.strip()) < 10:
        return None  # No prompt → skip

    # ── Circuit breaker: max 3 blocks ──
    if gate_check(ctx.agent_data, "fidelity"):
        return None  # Prevent death spiral

    # Call the reusable validator
    result = check_semantic_fidelity(ctx.project_dir, prompt, cache=ctx.agent_data)
    
    if not result.get("passed", True):
        # Build block message
        fidelity_blocks = get_block_count(ctx.agent_data, "fidelity")
        fail_msg = "\n".join(result.get("reasons", []))
        
        # Details
        details = result.get("details", {})
        missing_items = details.get("missing", [])
        missing_items.sort(key=lambda x: x[2] if len(x) > 2 else 0, reverse=True)
        missing_display = ", ".join(
            f"{t[0]} ({t[1]})" if len(t) > 1 else str(t)
            for t in missing_items[:8]
        )
        overflow = f" ... and {len(missing_items) - 8} more" if len(missing_items) > 8 else ""

        boilerplate_note = ""
        if details.get("boilerplate_found", 0) > 0:
            boilerplate_note = (
                f" ⚠️ {details['boilerplate_found']} boilerplate patterns "
                f"detected (Lorem ipsum, Create Next App, etc.)."
            )

        return ctx.block(
            f"⛔ CONTENT FIDELITY: {fail_msg}\n"
            f"Missing (by priority): {missing_display}{overflow}. "
            f"The user asked for specific content that doesn't appear in the code.{boilerplate_note} "
            f"Read the original prompt and add the missing content to the "
            f"appropriate source files. "
            f"(Block {fidelity_blocks + 1}/3)"
        )

    # Success
    pass_msg = "\n".join(result.get("reasons", []))
    logger.info(f"[GATE] {pass_msg}")
    return None


@register_check(1.162, "Page data wiring", critical=True, web_only=True, gate="tdd")
def _check_page_data_wiring(ctx):
    """RCA-322 Issue 5: Detect pages using hardcoded arrays instead of real API calls.

    Pages with .map()/.filter() on hardcoded in-component arrays instead of
    fetching data from an API/database represent a deliverable-quality failure.
    This check walks page files looking for suspicious patterns and blocks
    completion if found.
    """
    import re
    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    suspect_pages = []
    # Patterns: const items = [{ ... }] followed by .map( in same file
    HARDCODED_PATTERN = re.compile(
        r'const\s+\w+\s*=\s*\[[\s\S]{20,500}?\][\s\S]{0,200}?\.map\s*\(',
        re.MULTILINE,
    )

    for fpath in list_project_files(ctx.project_dir):
        fname = os.path.basename(fpath)
        if fname not in _PAGE_FILENAMES:
            continue
        relpath = os.path.relpath(fpath, ctx.project_dir)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (IOError, OSError):
            continue
        if HARDCODED_PATTERN.search(content):
            suspect_pages.append(relpath)

    if not suspect_pages:
        return None

    pages_list = ", ".join(suspect_pages[:5])
    overflow = f" ... and {len(suspect_pages) - 5} more" if len(suspect_pages) > 5 else ""
    return ctx.block(
        f"⛔ PAGE DATA WIRING: {len(suspect_pages)} page(s) appear to render hardcoded "
        f"arrays instead of fetching real API data: {pages_list}{overflow}. "
        f"Replace hardcoded data arrays with real fetch() or API calls."
    )


# Backward-compat alias — _check_landing_page_depth was renamed to _check_page_content_density
_check_landing_page_depth = _check_page_content_density
# Backward-compat alias — _check_placeholder_content was deleted; stub to nearest equivalent
_check_placeholder_content = _check_bdd_scenario_compliance
