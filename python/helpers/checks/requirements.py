"""
Requirements gate checks — contract assertions, .env.example verification,
form-route completeness, server health, theme coherence, and dead code detection.

These checks validate prompt-to-code alignment and runtime health:
  - Contract assertion enforcement (literal values in source)
  - .env.example completeness
  - Form action / fetch() → API route cross-reference
  - Server health (runtime error detection behind HTTP 200)
  - Theme coherence (CSS dark/light mode configuration)
  - Dead code detection (unreachable statements)

NOTE: The PDV (Prompt-Driven Verification) feature coverage gate was removed
(ITR-44 RCA). It produced false positives by regex-extracting routes from the
raw prompt instead of using the architect's Page Map. The architect's route
decisions are validated by:
  - Page Coverage (2.06): architecture.md → decomposition work packages
  - Plan Implementation Coverage (1.154): architect_plan.json → page.tsx files
  - Route Reachability (1.155): <Link href> → App Router pages
"""

import os
import json
import logging
import re as _re

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
)
from python.helpers.orchestrator_gate_common import format_gate_block
from python.helpers.boomerang_context import get_original_user_message
from python.helpers.prompt_contract_parser import build_contract
from python.helpers.contract_assertion_runner import run_contract_assertions
from python.helpers.requirements_ledger import record_gate_failure, mark_verified_from_gate_results
from python.helpers.planning_paths import get_path as _planning_path
from python.helpers.server_health import check_server_health



logger = logging.getLogger("agix.orchestrator_completion_gate")


# ─── Contract Assertions Gate (RCA-244) ──────────────────────────────


@register_check(0.061, "Contract Assertions", critical=True, gate="tdd")
def _check_contract_assertions(ctx):
    """Run prompt-contract assertions against project source code.

    RCA-244: Extracts literal requirements from the raw user prompt
    (URLs, prices, model names, emails) and verifies each one is present
    in the generated source code via grep-style matching.

    Returns None if all assertions pass, or a block message if any fail.
    """
    # Get the raw prompt
    original_prompt = ctx.agent_data.get("_raw_user_prompt", "")
    if not original_prompt:
        try:
            original_prompt = get_original_user_message(ctx.agent)
        except Exception:
            original_prompt = ""
    if not original_prompt:
        # Try prompt.md file
        prompt_path = os.path.join(ctx.project_dir, "prompt.md")
        if not os.path.exists(prompt_path):
            prompt_path = os.path.join(ctx.project_dir, "backup_init", "prompt.md")
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path, "r") as f:
                    original_prompt = f.read()
            except IOError:
                pass

    if not original_prompt:
        return None  # No prompt to assert against

    # Build the contract from the raw prompt
    contract = build_contract(original_prompt)
    if not contract.get("assertions"):
        return None  # No assertions extracted

    # Persist the contract for visibility
    contract_path = os.path.join(ctx.project_dir, "requirements_contract.json")
    try:
        with open(contract_path, "w") as f:
            import json as _json
            _json.dump(contract, f, indent=2)
    except IOError:
        pass

    # Run assertions (Phase 4, Fix A: load model catalog for name → slug resolution)
    model_catalog = None
    try:
        from python.helpers.model_resolver import load_catalog
        model_catalog = load_catalog()
    except Exception:
        pass  # Graceful degradation if catalog unavailable
    result = run_contract_assertions(
        contract, ctx.project_dir, model_catalog=model_catalog,
    )

    # §10.3: Store result for L2 visibility
    failed_assertions = [r for r in result["results"] if not r["passed"]]
    ctx.agent_data["_last_contract_result"] = {
        "passed": result["passed"],
        "total": result["total"],
        "pass_rate": result["pass_rate"],
        "failed_values": [fa["value"] for fa in failed_assertions[:10]],
    }

    if result["pass_rate"] == 1.0:
        logger.info(
            f"[CONTRACT GATE] All {result['total']} prompt assertions passed ✓"
        )
        # Close the verification loop: promote completed → verified
        try:
            promoted = mark_verified_from_gate_results(ctx.agent_data)
            if promoted:
                logger.info(
                    f"[CONTRACT GATE] Verification loop closed: "
                    f"{promoted} requirements promoted to verified"
                )
        except Exception as vl_err:
            logger.warning(f"[CONTRACT GATE] Verification loop failed (non-fatal): {vl_err}")
        return None  # All pass

    # Build failure message with §10.4 remediation hints
    failure_lines = []
    for fa in failed_assertions[:10]:  # Cap at 10 to avoid token overflow
        hint = _remediation_hint(fa.get("type", "unknown"))
        # RCA-335: Surface stale slug detection in block message
        stale_slug = fa.get("stale_slug_found")
        correct_slug = fa.get("correct_slug")
        if stale_slug and correct_slug:
            failure_lines.append(
                f"  ✗ [{fa['id']}] STALE MODEL SLUG: Replace \"{stale_slug}\" → \"{correct_slug}\"\n"
                f"    Found wrong model slug in source code. Search & replace ALL occurrences."
            )
        else:
            failure_lines.append(
                f"  ✗ [{fa['id']}] Missing: \"{fa['value']}\"\n    → {hint}"
            )

    # Component 5: Surface extraction metadata for operator diagnostics
    meta_line = ""
    extraction_meta = ctx.agent_data.get("_extraction_metadata", {})
    if extraction_meta:
        meta_line = (
            f"\n[Pipeline: {extraction_meta.get('pipeline', 'unknown')} | "
            f"Regex candidates: {extraction_meta.get('regex_candidates', '?')} | "
            f"Validated: {extraction_meta.get('validated_count', '?')}]\n"
        )

    block_msg = (
        f"Prompt Contract Assertions: {result['passed']}/{result['total']} passed "
        f"({result['pass_rate']:.0%}).{meta_line}\n"
        f"The following literal values from the user prompt are NOT in source code:\n"
        + "\n".join(failure_lines)
        + "\n\nYou MUST add these exact values to your code before delivery."
    )

    # Record failures in requirements ledger
    for fa in failed_assertions:
        try:
            record_gate_failure(
                gate_name="contract_assertions",
                failure_detail=f"Missing: {fa['value']}",
                project_dir=ctx.project_dir,
            )
        except Exception:
            pass

    return format_gate_block(
        reason=f"Contract Assertions: {block_msg[:150]}",
        action="Add the missing literal values listed above to your source code before retrying delivery.",
        block_count=0,
        check_type="requirements_not_met",
    )


# §10.4: Remediation hint mapping for contract assertion types
def _remediation_hint(assertion_type: str) -> str:
    """Map assertion types to actionable remediation guidance.

    RCA-251 §10.4: Gate block messages previously said WHAT was missing
    but not HOW to fix it. This maps each assertion type to specific
    locations and patterns the agent should check.
    """
    hints = {
        "url": (
            "Add this URL to the appropriate component, page, or config. "
            "Common locations: navigation links, footer, CTA buttons, "
            "environment variables, or API endpoint configs."
        ),
        "price": (
            "Add this exact price string to the pricing page/component. "
            "Check pricing tables, plan cards, or subscription displays. "
            "Must appear verbatim — not rounded or reformatted."
        ),
        "model": (
            "Add to .env as model configuration AND to code as display name. "
            "Use the OpenRouter API slug (e.g., 'anthropic/claude-sonnet-4') "
            "in code configs. Common locations: .env (MODEL=, OPENAI_MODEL=), "
            "config files, README.md, UI display strings."
        ),
        "model_name": (
            "Add the AI model to your code using its OpenRouter API slug. "
            "Check data/openrouter_models.json for the correct slug. "
            "Common locations: .env (MODEL=), API route handlers, "
            "config objects, or UI model selector components."
        ),
        "email": (
            "Add to contact or support configuration. Common locations: "
            "footer contact info, support pages, .env (SUPPORT_EMAIL=), "
            "or email template sender addresses."
        ),
        "phone": (
            "Add to contact information sections. Common locations: "
            "footer, contact page, business profile components."
        ),
        "person_name": (
            "Add this person's name to the appropriate location. "
            "Common locations: testimonials, team/about page, "
            "footer credits, or business owner profile sections."
        ),
    }
    return hints.get(assertion_type, (
        "Add this exact value to your source code. Check the user's prompt "
        "for context on where it should appear. Search your codebase with "
        "grep to find related patterns."
    ))


# ─── Fabrication Detection Gate (Gap-7 Universal Fix) ──────────────────


@register_check(0.065, "Fabrication detection", critical=True, gate="tdd")
def _check_fabrication_detection(ctx):
    """Inverse Contract Assertion — find values in source NOT in the prompt.

    Gap-7 (Universal Fix): Contract Assertions (0.06) checks "is this prompt
    value in source?" (missing = FAIL). This check does the INVERSE:
    "is this source value in the prompt?" (fabrication = FAIL).

    Catches agents that INVENT pricing ($99/mo), URLs, or brand names
    the user never specified — a common failure in 9/15 iterations.

    Advisory (not blocking) to avoid false-positive loops on CSS values
    and boilerplate URLs. Surfaces fabricated values in the gate report
    so the agent is aware and can correct them.
    """
    # Resolve the raw prompt
    original_prompt = ctx.agent_data.get("_raw_user_prompt", "")
    if not original_prompt:
        try:
            original_prompt = get_original_user_message(ctx.agent)
        except Exception:
            original_prompt = ""
    if not original_prompt:
        prompt_path = os.path.join(ctx.project_dir, "prompt.md")
        if not os.path.exists(prompt_path):
            prompt_path = os.path.join(ctx.project_dir, "backup_init", "prompt.md")
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path, "r") as f:
                    original_prompt = f.read()
            except IOError:
                pass

    if not original_prompt:
        return None  # No prompt to check against

    try:
        from python.helpers.fabrication_detector import detect_fabricated_values
        from python.helpers.source_scanner import scan_project_sources

        # Read all source files using existing scanner
        source_files = scan_project_sources(ctx.project_dir)
        if not source_files:
            return None

        # Load manifest for additional cross-reference
        manifest = None
        manifest_path = _planning_path(ctx.project_dir, "content_manifest")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
            except (IOError, json.JSONDecodeError):
                pass

        result = detect_fabricated_values(
            source_files=source_files,
            original_prompt=original_prompt,
            manifest=manifest,
        )

        if result["clean"]:
            return None  # No fabricated values

        # Store for L2 visibility
        ctx.agent_data["_fabrication_result"] = {
            "fabricated_count": len(result["fabricated"]),
            "matched_count": len(result["matched"]),
            "fabricated_values": [
                f["value"] for f in result["fabricated"][:10]
            ],
        }

        # Build advisory message
        fab = result["fabricated"]
        lines = []
        for f in fab[:8]:
            lines.append(
                f"  ⚠️ [{f['type'].upper()}] `{f['value']}` in {f['file']}:{f['line']} "
                f"— NOT in user prompt"
            )
        overflow = f"\n  ... and {len(fab) - 8} more" if len(fab) > 8 else ""

        return (
            f"⚠️ FABRICATION DETECTED: {len(fab)} value(s) in source code "
            f"that do NOT appear in the user's original prompt:\n"
            + "\n".join(lines) + overflow
            + "\n\nThese may be INVENTED by the agent. Verify each value "
            "against the original prompt and content_manifest.json. "
            "Replace fabricated values with prompt-specified values."
        )

    except Exception as e:
        logger.debug(f"[FABRICATION] Check failed: {e}")
        return None


# ─── .env.example Verification Gate (U-5, RCA-260) ────────────────────


@register_check(0.07, ".env.example verification", critical=True, gate="done")  # F-6: promoted to critical
def _check_env_example(ctx):
    """Verify .env.example exists and contains all required env vars.

    U-5 (RCA-260): After scaffold phase, the project must have a .env.example
    file listing all required environment variables derived from the prompt's
    integration mentions. Without this, deployment fails silently.

    Returns None if gate passes, or a block message string if it fails.
    """
    # Load contract to get required env vars
    contract = None
    contract_path = os.path.join(ctx.project_dir, "requirements_contract.json")
    if os.path.exists(contract_path):
        try:
            with open(contract_path, "r") as f:
                contract = json.load(f)
        except (IOError, json.JSONDecodeError):
            pass

    if contract is None:
        # Try building from raw prompt
        original_prompt = ctx.agent_data.get("_raw_user_prompt", "")
        if not original_prompt:
            return None  # No prompt, no env vars to check

        try:
            from python.helpers.prompt_contract_parser import build_contract
            contract = build_contract(original_prompt)
        except Exception:
            return None

    required_env_vars = contract.get("env_vars", [])
    if not required_env_vars:
        return None  # No env vars required (static site)

    # Check if .env.example exists in root or web/
    env_example_path = os.path.join(ctx.project_dir, ".env.example")
    if not os.path.exists(env_example_path):
        # Try web subdirectory
        web_env_path = os.path.join(ctx.project_dir, "web", ".env.example")
        if os.path.exists(web_env_path):
            env_example_path = web_env_path
        else:
            var_names = [v["name"] for v in required_env_vars]
            return format_gate_block(
                reason=f".env.example MISSING — {len(var_names)} env vars required",
                action=(
                    f"Create a .env.example file in the project root with these variables:\n"
                    + "\n".join(f"  {v['name']}={v['example']}" for v in required_env_vars)
                ),
                block_count=0,
                check_type="infrastructure_missing",
            )

    # Check that all required vars are present in .env.example
    try:
        with open(env_example_path, "r") as f:
            env_content = f.read()
    except IOError:
        return None

    missing = []
    for var in required_env_vars:
        if var["name"] not in env_content:
            missing.append(var)

    if missing:
        return format_gate_block(
            reason=f".env.example missing {len(missing)} required variable(s)",
            action=(
                f"Add these variables to .env.example:\n"
                + "\n".join(f"  {v['name']}={v['example']}" for v in missing)
            ),
            block_count=0,
            check_type="infrastructure_missing",
        )

    return None  # All vars present


# ─── Form/Fetch → API Route Cross-Reference Gate (U-9, RCA-260) ───────


@register_check(1.209, "Form-route completeness", critical=True, web_only=True, gate="done")
def _check_form_route_completeness(ctx):
    """Verify every form action and fetch() call has a matching API route.

    U-9 (RCA-260): Scan all TSX/JSX/TS files for <form action="/api/...">
    and fetch('/api/...') patterns, then verify each referenced API path
    has a corresponding route.ts/route.js file.

    Returns None if gate passes, or a block message string if it fails.
    """
    src_dir = os.path.join(ctx.project_dir, "src")
    if not os.path.isdir(src_dir):
        return None  # No src directory

    # Patterns to match form actions and fetch calls pointing to /api/
    _FORM_ACTION_RE = _re.compile(
        r'''action\s*=\s*["'](/api/[^"']+)["']''',
        _re.IGNORECASE,
    )
    _FETCH_API_RE = _re.compile(
        r'''fetch\s*\(\s*["'`](/api/[^"'`]+)["'`]''',
        _re.IGNORECASE,
    )

    # Collect all /api/ references from source files
    api_references = set()
    for root, dirs, files in os.walk(src_dir):
        # Skip node_modules
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for fname in files:
            if fname.endswith((".tsx", ".jsx", ".ts", ".js")):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()
                    for m in _FORM_ACTION_RE.finditer(content):
                        api_references.add(m.group(1))
                    for m in _FETCH_API_RE.finditer(content):
                        api_references.add(m.group(1))
                except IOError:
                    pass

    if not api_references:
        return None  # No API references found

    # Check each API reference has a matching route file
    missing_routes = []
    for api_path in sorted(api_references):
        # /api/contact → src/app/api/contact/route.ts
        route_segments = api_path.strip("/").split("/")
        route_dir = os.path.join(ctx.project_dir, "src", "app", *route_segments)

        # Check for route.ts, route.js, route.tsx
        has_route = any(
            os.path.exists(os.path.join(route_dir, f"route.{ext}"))
            for ext in ("ts", "js", "tsx")
        )

        if not has_route:
            missing_routes.append(api_path)

    if missing_routes:
        return format_gate_block(
            reason=f"{len(missing_routes)} form/fetch API endpoint(s) have no route file",
            action=(
                "Create route files for these API endpoints:\n"
                + "\n".join(
                    f"  {path} → src/app{path}/route.ts"
                    for path in missing_routes
                )
            ),
            block_count=0,
            check_type="routes_missing",
        )

    return None  # All routes exist


@register_check(9.0, "Server health", critical=True, requires=["Dev server started"], gate="done")
def _check_server_health(ctx: CheckContext):
    """RCA-233/327: Post-serve runtime health assertion (UNIVERSAL).

    After any server is started (web dev server OR API backend), this gate
    curls it to verify that responses contain actual content, not fatal
    errors hidden behind HTTP 200 status codes.

    RCA-327: Removed web_only=True — API projects have servers too.
    Now checks both dev server ports and API service ports from the
    service registry.

    This is the LAST gate (order 9.0) — it only runs after all other
    checks pass, and only when a server has been started.
    """
    # Get port from agent state
    port = ctx.agent_data.get("_dev_server_port", "")
    if not port:
        # No port means dev server check may have used flag-only evidence.
        # Try the service registry — check both dev and api service types.
        try:
            from python.helpers.services_mgt import get_active_services
            services = get_active_services(ctx.project_dir)
            for svc in services:
                svc_type = svc.get("type", "")
                if svc_type in ("dev", "api", "backend", "server") and svc.get("port"):
                    port = svc["port"]
                    break
        except Exception:
            pass

    if not port:
        # No port available — skip this gate (don't block on missing metadata)
        logger.debug("[HEALTH GATE] No server port found, skipping")
        return None

    try:
        port_int = int(port)
    except (ValueError, TypeError):
        return None

    result = check_server_health(
        port=port_int,
        project_dir=ctx.project_dir,
    )

    if not result["healthy"]:
        error_summary = "; ".join(result["errors"][:3])
        return ctx.block(
            f"⚠️ SERVER UNHEALTHY: Server on port {port_int} is returning "
            f"errors hidden in HTTP 200 responses. Errors: {error_summary}. "
            f"Fix the server errors before completing.",
            action=(
                f"Check the server output for errors. Run "
                f"`curl -s http://localhost:{port_int}/` and examine the "
                f"response body. Fix any MODULE_NOT_FOUND, statusCode:500, "
                f"or hydration errors, then restart the server."
            ),
        )

    # Store health evidence in agent_data for proof gate
    ctx.agent_data["_server_health_evidence"] = result
    return None





# ─── Behavioral Requirement Verification Gate (U-8, RCA-339) ─────────


@register_check(0.08, "Behavioral Requirements", critical=True, gate="bdd")
def _check_behavioral_requirements(ctx):
    """Verify extracted behavior verify_patterns exist in source code.

    U-8 (RCA-339): The prompt_contract_parser extracts behavioral requirements
    (scheduling, scoring, temporal logic) and generates verify_pattern regexes.
    This gate runs those patterns against the source tree to ensure the actual
    scheduling/scoring logic is implemented — not just a wrapper function.

    Layer 1 (deterministic): regex scan, sub-second execution.
    This catches the ISS-7 gap: a 27-line module that technically passes
    the content density gate but contains no real scheduling logic.

    Runs at order 0.08 (after contract assertions at 0.06, before server
    health at 0.09) to catch behavioral gaps early.
    """
    from python.helpers.source_scanner import regex_exists

    contract = ctx.agent_data.get("_prompt_contract", {})
    behaviors = contract.get("behaviors", [])
    if not behaviors:
        return None  # No behavioral requirements to check

    missing = []
    for behavior in behaviors:
        name = behavior.get("name", "unnamed")
        pattern = behavior.get("verify_pattern", "")
        if not pattern:
            continue

        if not regex_exists(ctx.project_dir, pattern):
            missing.append(name)

    if not missing:
        return None  # All behavioral patterns found in source

    missing_list = ", ".join(missing)
    return ctx.block(
        f"Behavioral requirements NOT implemented: {missing_list}. "
        f"Source code must contain matching logic (scheduling, scoring, etc.).",
        action=(
            f"Implement the following behavioral requirements: {missing_list}. "
            f"The code must contain actual logic matching the verify_patterns, "
            f"not just wrapper functions or imports."
        ),
    )


# ─── GitHub Push Verification Gate (U-6, RCA-339) ────────────────────


@register_check(0.095, "GitHub Push Verification", critical=False, gate="done")
def _check_github_push(ctx):
    """Verify GitHub/git push when the user prompt mentions it.

    U-6 (RCA-339): When the user prompt requests pushing code to GitHub,
    verify that:
    1. A .git directory exists in the project
    2. A remote origin is configured

    Layer 1 (deterministic): filesystem check + config file scan.
    Skips entirely when the prompt doesn't mention git/GitHub/push.

    Runs at order 0.095 (near end) as infrastructure verification.
    """
    # Resolve the raw prompt
    original_prompt = ctx.agent_data.get("_raw_user_prompt", "")
    if not original_prompt:
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "github_push_0.095", True)
        except Exception:
            pass
        return None

    # Check if prompt mentions git/GitHub/push
    prompt_lower = original_prompt.lower()
    git_keywords = ["github", "git push", "push to git", "push code", "git repo"]
    if not any(kw in prompt_lower for kw in git_keywords):
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "github_push_0.095", True)
        except Exception:
            pass
        return None  # No git mention — skip

    # Check .git directory existence
    git_dir = os.path.join(ctx.project_dir, ".git")
    if not os.path.isdir(git_dir):
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "github_push_0.095", False)
        except Exception:
            pass
        return ctx.block(
            "Git repository not initialized. Prompt requires pushing to GitHub "
            "but no .git directory found.",
            action=(
                "The project directory needs a Git repository initialized "
                "with a remote origin configured pointing to the target "
                "GitHub repository."
            ),
        )

    # Check for remote configuration
    git_config = os.path.join(git_dir, "config")
    if os.path.isfile(git_config):
        try:
            with open(git_config, "r") as f:
                config_content = f.read()
            if "[remote" not in config_content:
                return ctx.block(
                    "Git repository exists but no remote configured. Prompt "
                    "requires pushing to GitHub but no remote origin found.",
                    action=(
                        "The Git repository needs a remote origin configured "
                        "pointing to the target GitHub repository URL."
                    ),
                )
        except IOError:
            pass  # Can't read config — don't block on IOError

    try:
        from python.helpers.check_sm_wiring import transition_check_sm
        transition_check_sm(ctx.agent_data, "github_push_0.095", True)
    except Exception:
        pass
    return None  # .git exists with remote — pass




# ─── ADR-007 Wave Ordering Advisory ──────────────────────────────────────


@register_advisory(0.10, "ADR-007 Wave Ordering")
def _check_wave_ordering(ctx):
    """Advisory: warn if SALES_UI features are active before CORE_PRODUCT.

    ADR-007 defines wave ordering: CORE_PRODUCT → INFRASTRUCTURE → SALES_UI.
    If sales features are in_progress/completed while core features remain
    pending, it suggests the decomposition gate ordered work incorrectly.

    This is advisory-only (never blocks) to avoid death spirals.
    """
    registry = ctx.agent_data.get("_feature_registry", [])
    if not registry:
        return None  # No registry data — skip

    # Categorize features by wave
    core_pending = []
    sales_active = []
    for feat in registry:
        cat = feat.get("category", "")
        status = feat.get("status", "pending")

        if cat == "CORE_PRODUCT" and status == "pending":
            core_pending.append(feat.get("name", "?"))
        elif cat == "SALES_UI" and status in ("in_progress", "completed"):
            sales_active.append(feat.get("name", "?"))

    if core_pending and sales_active:
        logger.warning(
            f"[ADR-007] Wave ordering violation: {len(sales_active)} SALES_UI features "
            f"active while {len(core_pending)} CORE_PRODUCT features still pending"
        )
        return (
            f"⚠️ ADR-007 Wave Ordering Advisory: {len(sales_active)} SALES_UI features "
            f"({', '.join(sales_active[:3])}) are active while "
            f"{len(core_pending)} CORE_PRODUCT features "
            f"({', '.join(core_pending[:3])}) remain pending. "
            f"Consider prioritizing core features first."
        )

    return None  # Wave ordering OK






# ─── Class L: Requirements Completeness Advisory ─────────────────────────────


@register_advisory(0.09, "Requirements completeness")
def _check_requirements_completeness_advisory(ctx):
    """Advisory: check requirements_ledger.json for lingering PENDING items.
    Addresses Class L (Requirements Completeness, 3 audits).
    """
    try:
        from python.helpers.requirements_ledger import _ensure_ledger
        ledger = _ensure_ledger(ctx.agent_data)
        requirements = ledger.get("requirements", []) if ledger else []
        if not requirements:
            return None  # No requirements — skip
        total = len(requirements)
        pending = [r for r in requirements if r.get("status", "").lower() in ("pending", "")]
        pending_count = len(pending)
        if total == 0:
            return None
        ratio = pending_count / total
        if ratio > 0.3:  # More than 30% still pending
            return (
                f"⚠️ REQUIREMENTS COMPLETENESS: {pending_count}/{total} requirements "
                f"({ratio:.0%}) still PENDING. Expected < 30% pending at delivery."
            )
        return None
    except Exception as e:
        logger.debug(f"[REQUIREMENTS COMPLETENESS] Validator error: {e}")
        return None
