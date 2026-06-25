"""
Universal Delegation Package Builder.

Builds a curated, profile-aware context package for subordinate agents.
Consolidates ALL 25 inject_* functions into a single structured document
that gives each agent exactly what it needs — nothing more.

Three-tier architecture:
  Tier 1: Project Context (from disk, LRU-cached)
  Tier 2: Runtime State   (from agent.data, conditional)
  Tier 3: Operational     (lightweight, always computed)

RCA: rca-context-loss-5why.md
Architecture: agix-devdocs/docs/architecture/delegation-brief-architecture.md

Entry points:
    build_delegation_package()  — the consolidated single-call API
    build_delegation_brief()    — backward-compat alias (delegates to package)

Sub-modules (extracted for maintainability):
    delegation_brief_sections_policy — Policy/quality section builders
    delegation_brief_sections_ops    — Ops/scaffolding section builders
"""

from __future__ import annotations

import functools
import glob
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from python.helpers.phase_category import (
    is_design_phase,
    is_planning_phase,
    is_scaffold_phase,
    is_post_tdd_generation_phase,
    is_verification_or_later,
)
from python.helpers.planning_paths import PLANNING_PATHS

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Profile → Section Configuration (23 keys per profile)
# ═══════════════════════════════════════════════════════════════
# Each key controls a section. True/False for boolean inclusion;
# string values for mode variations ("full", "visual", "assigned", "all").


from python.helpers.delegation_brief_context import ProjectContext, _load_project_context
from python.helpers.delegation_brief_config import (
    get_phase_aware_config, detect_delegation_phase,
    _PROFILE_TOOLS, _AUTH_CATEGORIES, _AUTH_KEYWORDS, _MAX_DOMAIN_CONTEXT_CHARS,
    PROFILE_CONTEXT_CONFIG, _DEFAULT_CONFIG, _PHASE_RESTRICTED_OVERRIDES,
    _FRAMEWORK_PITFALL_RULES, check_type_coherence
)

# ═══════════════════════════════════════════════════════════════
# Re-exports from sub-modules (backward compatibility)
# ═══════════════════════════════════════════════════════════════
# All existing imports from this module continue to work because
# we re-export every public function from the two sub-modules.

from python.helpers.delegation_brief_sections_policy import (  # noqa: F401
    _build_research_docs_inline_section,
    _build_schema_lock_section,
    _build_codebase_state_section,
    _build_infrastructure_fast_pass_section,
    _build_error_relay_section,
    _build_gate_failure_section,
    _build_verification_section,
    _build_fidelity_section,
    _build_manifest_values_section,
    _build_tdd_section,
    _build_acceptance_section,
)

from python.helpers.delegation_brief_sections_ops import (  # noqa: F401
    detect_orm_and_build_seed_mandate,
    _build_database_seed_mandate_section,
    _build_tsc_mandate_section,
    _build_scaffold_mandate_section,
    _build_tools_section,
    _build_dev_server_url_section,
    _build_budget_section,
    _build_skill_section,
    _build_auth_prohibition_section,
    _detect_framework,
    _build_framework_pitfalls_section,
    _build_domain_context_section,
    _build_compliance_section,
)


# ═══════════════════════════════════════════════════════════════
# Group 1: Core Section Builders (kept in this file)
# ═══════════════════════════════════════════════════════════════

def _build_who_section(
    profile: str,
    kwargs: dict,
    ctx: ProjectContext,
) -> str:
    """Build WHO section — agent identity + phase context."""
    lines = ["### WHO YOU ARE"]

    # Current phase from decomp index
    if ctx.decomp_index:
        phases = ctx.decomp_index.get("phases", [])
        completed = [p for p in phases if isinstance(p, dict) and p.get("status") == "completed"]
        running = [p for p in phases if isinstance(p, dict) and p.get("status") == "running"]
        pending = [p for p in phases if isinstance(p, dict) and p.get("status") == "pending"]

        if running:
            current = running[0]
            lines.append(
                f"You are a `{profile}` agent working on **{current.get('title', 'unknown')}** "
                f"(phase {current.get('seq', '?')})."
            )
        else:
            lines.append(f"You are a `{profile}` agent.")

        if completed:
            completed_titles = [p.get("title", "?") for p in completed]
            lines.append(f"Completed phases: {', '.join(completed_titles)}.")

        if pending:
            lines.append(f"{len(pending)} phase(s) remaining after your work.")
    else:
        lines.append(f"You are a `{profile}` agent.")

    return "\n".join(lines)

def _build_why_section(
    profile: str,
    kwargs: dict,
    ctx: ProjectContext,
) -> str:
    """Build WHY section — orchestrator reasoning and dependencies."""
    if not ctx.decomp_index:
        return ""

    lines = ["### WHY THIS TASK"]
    phases = ctx.decomp_index.get("phases", [])
    req_ids = kwargs.get("requirement_ids", [])

    # Find the task matching the assigned requirement_ids
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        phase_reqs = phase.get("req_guids", [])
        if req_ids and any(r in phase_reqs for r in req_ids):
            lines.append(f"Task: **{phase.get('title', 'Unknown')}** (seq {phase.get('seq', '?')})")

            # Dependencies
            depends = phase.get("depends_on", [])
            if depends:
                dep_details = []
                for dep_seq in depends:
                    dep_phase = next(
                        (p for p in phases if isinstance(p, dict) and str(p.get("seq", "")) == str(dep_seq)),
                        None,
                    )
                    if dep_phase:
                        dep_details.append(
                            f"{dep_phase.get('title', dep_seq)} ({dep_phase.get('status', 'unknown')})"
                        )
                    else:
                        dep_details.append(str(dep_seq))
                lines.append(f"Depends on: {', '.join(dep_details)}")
            break

    return "\n".join(lines) if len(lines) > 1 else ""

def _build_what_section(
    profile: str,
    kwargs: dict,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build WHAT section — assigned requirements."""
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    req_mode = config.get("requirements", False)
    if not req_mode or not ctx.requirements:
        return ""

    all_reqs = ctx.requirements.get("requirements", [])
    if not all_reqs:
        return ""

    req_ids = kwargs.get("requirement_ids", [])

    lines = ["### WHAT TO BUILD"]

    if req_mode == "assigned" and req_ids:
        lines.append("Your assigned requirements:")
        for req in all_reqs:
            if isinstance(req, dict) and req.get("id") in req_ids:
                line = f"- **{req['id']}**: {req.get('text', 'Unknown')}"
                # ADR-086: Include stage status breakdown when available
                ss = req.get("stage_status")
                if ss and isinstance(ss, dict):
                    stage_parts = [f"{k}={v}" for k, v in sorted(ss.items())]
                    line += f"  [{', '.join(stage_parts)}]"
                lines.append(line)
    elif req_mode == "all" or (req_mode == "assigned" and not req_ids):
        lines.append("Requirements to address:")
        for req in all_reqs:
            if isinstance(req, dict) and req.get("id"):
                line = f"- **{req['id']}**: {req.get('text', 'Unknown')}"
                # ADR-086: Include stage status breakdown when available
                ss = req.get("stage_status")
                if ss and isinstance(ss, dict):
                    stage_parts = [f"{k}={v}" for k, v in sorted(ss.items())]
                    line += f"  [{', '.join(stage_parts)}]"
                lines.append(line)

    # F-4: Architect completeness mandate when requirements mode is 'all'
    if req_mode == "all" and all_reqs:
        count = len(all_reqs)
        lines.append(
            f"\n⚠️ COMPLETENESS MANDATE: You received {count} requirements. "
            f"Your architecture MUST map ALL {count} to routes, API endpoints, or components. "
            f"Any requirement not explicitly mapped is a DROPPED requirement — this is a violation."
        )

    return "\n".join(lines) if len(lines) > 1 else ""

def _build_with_section(
    profile: str,
    kwargs: dict,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build WITH section — content manifest values (profile-filtered)."""
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    manifest_mode = config.get("manifest", False)
    if not manifest_mode or not ctx.manifest:
        return ""

    lines = ["### WITH THESE VALUES (Use EXACTLY — fidelity gate enforced)"]

    manifest = ctx.manifest

    # Business identity
    biz = manifest.get("business", {})
    if isinstance(biz, dict):
        if biz.get("name"):
            lines.append(f"- Business: `{biz['name']}`")
        if biz.get("tagline"):
            lines.append(f"- Tagline: `{biz['tagline']}`")

    # Skip technical details for frontend/visual profiles
    if manifest_mode == "visual":
        return "\n".join(lines) if len(lines) > 1 else ""

    # G-3 (ITR-24): Include founder name when present in manifest
    founder = manifest.get("founder")
    if founder and isinstance(founder, str) and founder.strip():
        lines.append(f"- Founder: `{founder.strip()}`")

    # Pricing
    pricing = manifest.get("pricing", [])
    if isinstance(pricing, list) and pricing:
        for tier in pricing:
            if isinstance(tier, dict):
                name = tier.get("tier", tier.get("name", ""))
                price = tier.get("price", "")
                if name and price:
                    lines.append(f"- {name}: `{price}`")

    # URLs — G-4 (ITR-24): Include surface placement guidance
    urls = manifest.get("urls", {})
    if isinstance(urls, dict):
        # Placement guidance by URL label keyword
        _URL_PLACEMENT_HINTS = {
            "booking": "→ Place in CTA buttons, contact sections, and hero area",
            "calendly": "→ Place in CTA buttons, contact sections, and hero area",
            "demo": "→ Place in CTA buttons and pricing section",
            "social": "→ Place in footer social links and about page",
            "instagram": "→ Place in footer social links and about page",
            "facebook": "→ Place in footer social links and about page",
            "twitter": "→ Place in footer social links and about page",
            "linkedin": "→ Place in footer social links and about page",
            "github": "→ Place in footer links and documentation",
            "docs": "→ Place in navigation and footer",
            "support": "→ Place in footer and help section",
            "privacy": "→ Place in footer legal links",
            "terms": "→ Place in footer legal links",
            "blog": "→ Place in navigation and footer",
            "api": "→ Reference in developer documentation",
            "website": "→ Place in navigation and meta tags",
        }
        for label, url in urls.items():
            if url:
                label_display = label.replace('_', ' ').title()
                # Find matching placement hint
                hint = ""
                label_lower = label.lower()
                for keyword, guidance in _URL_PLACEMENT_HINTS.items():
                    if keyword in label_lower:
                        hint = f" {guidance}"
                        break
                lines.append(f"- {label_display}: `{url}`{hint}")

    # AI Models
    models = manifest.get("models", [])
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and model.get("name"):
                use = model.get("use", "")
                lines.append(f"- AI Model: `{model['name']}`" + (f" ({use})" if use else ""))

    # ── SS-9 (ITR-23): Voice, Tone, Constraints, Copywriting Rules ──
    # These brand identity fields were silently dropped from the delegation
    # brief, causing subordinate agents to ignore voice/tone guidance.

    # Voice — can be dict or string
    voice = manifest.get("voice")
    if voice:
        if isinstance(voice, dict) and voice:
            lines.append("- **Voice**:")
            for vk, vv in voice.items():
                lines.append(f"  - {vk}: `{vv}`")
        elif isinstance(voice, str) and voice.strip():
            lines.append(f"- **Voice**: `{voice}`")

    # Tone — typically a string
    tone = manifest.get("tone")
    if tone and isinstance(tone, str) and tone.strip():
        lines.append(f"- **Tone**: `{tone}`")

    # Constraints — typically a list
    constraints = manifest.get("constraints")
    if constraints and isinstance(constraints, list) and constraints:
        lines.append("- **Constraints**:")
        for c in constraints:
            if c:
                lines.append(f"  - {c}")

    # Copywriting rules — fallback key for constraints/voice rules
    copywriting_rules = manifest.get("copywriting_rules")
    if copywriting_rules and isinstance(copywriting_rules, list) and copywriting_rules:
        lines.append("- **Copywriting Rules**:")
        for rule in copywriting_rules:
            if rule:
                lines.append(f"  - {rule}")

    return "\n".join(lines) if len(lines) > 1 else ""

def _flatten_token_dict(d: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Recursively flatten nested token dicts to (name, value) pairs.

    RCA-FIX: The old code did `f"{k}: `{v}`"` which for nested dicts like
    {"primary": {"500": "#3b82f6"}} produced `primary: `{'500': '#3b82f6'}`
    — an unreadable Python dict repr. This function recursively flattens
    nested structures into CSS-ready name-value pairs.

    Examples:
        {"primary": "#3b82f6"} → [("primary", "#3b82f6")]
        {"primary": {"500": "#3b82f6", "600": "#2563eb"}} →
            [("primary-500", "#3b82f6"), ("primary-600", "#2563eb")]
    """
    result = []
    for k, v in d.items():
        full_key = f"{prefix}-{k}" if prefix else k
        if isinstance(v, dict):
            result.extend(_flatten_token_dict(v, full_key))
        else:
            result.append((full_key, str(v)))
    return result

def _build_how_section(
    profile: str,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build HOW section — design tokens.

    RCA-FIX: Ported comprehensive logic from inject_design_context()
    (delegation_message.py:1234) which was dead code. The old version:
    1. Couldn't handle nested color dicts (rendered as Python dict repr)
    2. Only output colors + typography (missed spacing, borderRadius,
       shadows, gradients, breakpoints — 5 of 7 categories)
    3. Provided no CSS custom property mapping instructions

    The new version:
    1. Recursively flattens nested token structures into CSS-ready values
    2. Includes ALL token categories found in design-tokens.json
    3. Adds explicit CSS custom property mapping mandate
    """
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    if not config.get("design_tokens") or not ctx.design_tokens:
        return ""

    tokens = ctx.design_tokens
    lines = [
        "### 🎨 HOW TO BUILD IT (Design Contract — MANDATORY)",
        "",
        "You MUST use these exact design tokens. Do NOT use scaffold defaults.",
        "Map these to CSS custom properties in `globals.css` AND to",
        "`tailwind.config.ts` → `theme.extend`.",
        "",
    ]

    # Process ALL token categories in a consistent order
    # Primary categories first, then everything else
    primary_keys = ["colors", "typography", "spacing", "borderRadius", "shadows"]
    processed_keys = set()

    def _format_category(cat_name: str, cat_data: dict) -> list[str]:
        """Format a single token category with recursive flattening."""
        import re
        # Split camelCase (borderRadius → border Radius), then normalize
        spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', cat_name)
        readable_name = spaced.replace("_", " ").replace("-", " ").title()
        cat_lines = [f"**{readable_name}**:"]
        flat_pairs = _flatten_token_dict(cat_data)
        for name, value in flat_pairs:
            cat_lines.append(f"  - `--{cat_name}-{name}`: `{value}`")
        return cat_lines

    # Primary categories
    for key in primary_keys:
        cat_data = tokens.get(key, {})
        if isinstance(cat_data, dict) and cat_data:
            lines.extend(_format_category(key, cat_data))
            lines.append("")
            processed_keys.add(key)

    # All other categories (gradients, breakpoints, custom, etc.)
    for key, val in tokens.items():
        if key in processed_keys:
            continue
        if isinstance(val, dict) and val:
            lines.extend(_format_category(key, val))
            lines.append("")
            processed_keys.add(key)
        elif isinstance(val, str) and val:
            # Scalar tokens (e.g., "fontFamily": "Inter")
            import re
            spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', key)
            readable = spaced.replace("_", " ").replace("-", " ").title()
            lines.append(f"**{readable}**: `{val}`")
            lines.append("")

    # Design system mandate (always included when tokens exist)
    lines.extend([
        "⚠️ **IMPLEMENTATION REQUIREMENTS**:",
        "1. Create CSS custom properties (`--color-primary`, etc.) in `globals.css`",
        "2. Extend `tailwind.config.ts` → `theme.extend.colors` with these values",
        "3. Use Tailwind utility classes (e.g., `bg-primary-500`) — NOT hardcoded hex",
        "4. If `tailwind.config.ts` doesn't exist or doesn't extend these tokens,",
        "   generate it FIRST before implementing any components.",
    ])

    return "\n".join(lines) if len(lines) > 7 else ""  # 7 = header lines only

def _build_where_section(
    profile: str,
    ctx: ProjectContext,
    config: dict | None = None,
    *,
    project_dir: str = "",
) -> str:
    """Build WHERE section — research docs reference with absolute paths.

    ISSUE-6: Paths must be absolute so subordinate agents don't waste an
    iteration self-correcting from relative to absolute paths.
    """
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    lines = []

    if ctx.research_docs and config.get("api_docs"):
        lines.append("### WHERE TO FIND DOCS")
        for doc in ctx.research_docs:
            if project_dir:
                abs_doc_path = os.path.join(project_dir, "docs", doc)
                lines.append(f"- `{abs_doc_path}`")
            else:
                lines.append(f"- `docs/{doc}`")

    return "\n".join(lines) if lines else ""


def _build_route_map_section(
    profile: str,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build ROUTE MAP section — all routes this agent should know about."""
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    if not config.get("route_map") or not ctx.route_map:
        return ""

    lines = ["### APP ROUTES MANDATE"]
    lines.append("⚠️ **CRITICAL REQUIREMENT**: You MUST implement ALL of the following routes. Missing routes will cause the quality gate to fail.")
    for route in sorted(ctx.route_map.keys()):
        name = ctx.route_map[route]
        if name:
            lines.append(f"- `{route}` — {name}")
        else:
            lines.append(f"- `{route}`")

    return "\n".join(lines)

def _build_integration_section(
    profile: str,
    kwargs: dict,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build INTEGRATION section — services, env vars, API details.

    F-8: Injects model slug as bold constant when present in manifest.
    Research mandate: All SDKs get a universal research verification prompt
    instead of hardcoded SDK-specific action instructions.
    """
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    if not config.get("integration"):
        return ""

    lines = ["### INTEGRATION CONTEXT"]

    # From manifest integrations
    if ctx.manifest:
        integrations = ctx.manifest.get("integrations", [])
        if isinstance(integrations, list):
            for intg in integrations:
                if isinstance(intg, dict):
                    name = intg.get("name", "")
                    itype = intg.get("type", "")
                    env_var = intg.get("env_var", "")
                    parts = [f"**{name}**"]
                    if itype:
                        parts.append(f"({itype})")
                    if env_var and env_var in ctx.env_vars:
                        parts.append(f"— env: `{env_var}` ✅")
                    elif env_var:
                        parts.append(
                            f"— env: `{env_var}` ⚠️ NOT YET PROVISIONED "
                            f"(write real SDK code using `process.env.{env_var}` — "
                            f"it will work when provisioned at deploy time. "
                            f"NEVER mock or stub this integration.)"
                        )
                    lines.append(f"- {' '.join(parts)}")

                    # F-5 fix: ACTIVE prohibition — agents MUST use env vars,
                    # not hardcode values. Previous passive language ("configured")
                    # was ignored by code agents.
                    if name.strip() and env_var:
                        lines.append(
                            f"  - ⚡ **ACTION REQUIRED**: import and initialize "
                            f"the `{name}` SDK using `process.env.{env_var}` "
                            f"(or `import.meta.env.{env_var}` for Vite client-side). "
                            f"Hardcoding the API key, URL, or secret value is "
                            f"**FORBIDDEN** and will be **REJECTED** by the gate."
                        )
                    elif name.strip():
                        lines.append(
                            f"  - ⚡ **ACTION REQUIRED**: import and initialize "
                            f"the `{name}` SDK using the configured env var"
                        )
                    # Research mandate: verify with researcher/context7 before implementing
                    lines.append(
                        f"  - ⚠️ **VERIFY FIRST**: Use `researcher` tool or `context7` MCP "
                        f"to check the CURRENT API docs, SDK version, and endpoint for `{name}` "
                        f"before implementing. Training data may be stale."
                    )

                    # ADR-ITR48 F-3: SDK metadata passthrough — no hardcoded lookup.
                    # The LLM populates these fields during Phase 0 manifest creation.
                    # We just surface whatever the planning agent extracted.
                    # RCA-ITR51: Added sdk_version to emit pinned install commands.
                    sdk_pkg = intg.get("sdk_package", "")
                    sdk_ver = intg.get("sdk_version", "")
                    api_url = intg.get("api_base_url", "")
                    auth_pat = intg.get("auth_pattern", "")
                    if sdk_pkg:
                        install_target = f"{sdk_pkg}@{sdk_ver}" if sdk_ver else sdk_pkg
                        ver_display = f" (version: `{sdk_ver}`)" if sdk_ver else ""
                        lines.append(
                            f"  - 📦 **SDK Package**: `{sdk_pkg}`{ver_display} — "
                            f"install with `npm install {install_target}` or `pip install {install_target}`"
                        )
                    if api_url:
                        lines.append(
                            f"  - 🌐 **API Base URL**: `{api_url}`"
                        )
                    if auth_pat:
                        lines.append(
                            f"  - 🔑 **Auth Pattern**: `{auth_pat}`"
                        )

    # Env vars not tied to manifest integrations
    if ctx.env_vars:
        manifest_env_vars = set()
        if ctx.manifest:
            for intg in ctx.manifest.get("integrations", []):
                if isinstance(intg, dict) and intg.get("env_var"):
                    manifest_env_vars.add(intg["env_var"])

        extra_vars = [k for k in ctx.env_vars if k not in manifest_env_vars]
        if extra_vars:
            lines.append("")
            lines.append(
                "⚠️ **ENV VAR MANDATE**: You MUST access the following "
                "environment variables at runtime via `process.env.VAR_NAME` "
                "(or `import.meta.env.VAR_NAME` for Vite client-side). "
                "Hardcoding any URL, API key, or secret value is **FORBIDDEN** "
                "and will be **REJECTED** by the completion gate."
            )
            for var in sorted(extra_vars):
                lines.append(f"- `{var}` → use `process.env.{var}`")

    # F-8: Inject model slug as bold constant when present
    if ctx.manifest:
        models = ctx.manifest.get("models", [])
        if isinstance(models, list):
            for model in models:
                if isinstance(model, dict):
                    slug = model.get("slug", "")
                    model_name = model.get("name", "")
                    if slug:
                        lines.append(
                            f"- 🤖 Model: **{slug}** — use this EXACT model identifier"
                        )
                    elif model_name:
                        lines.append(f"- 🤖 Model: {model_name}")

    return "\n".join(lines) if len(lines) > 1 else ""

def _build_mandates_section(
    profile: str,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build MANDATES section — navigation + design system requirements."""
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    lines = []

    # Navigation mandate: when >1 pages exist, code agents MUST create nav.
    # Gap-3 fix: Use requirements ledger page count as PRIMARY signal (available
    # on early delegations), fall back to route_map (available after architect_plan
    # is populated). Previously only checked route_map which is empty on Wave 1/2.
    if config.get("navigation_mandate"):
        # Count pages from requirements ledger (always available early)
        req_page_count = 0
        if ctx.requirements and isinstance(ctx.requirements, dict):
            for req in ctx.requirements.get("requirements", []):
                if isinstance(req, dict) and req.get("category") == "page":
                    req_page_count += 1

        # Count pages from route_map (available after architect_plan is populated)
        route_count = len(ctx.route_map) if ctx.route_map else 0

        # Use whichever gives higher page count
        page_count = max(req_page_count, route_count)

        if page_count > 1:
            lines.append("### NAVIGATION MANDATE")
            lines.append(
                f"This app has **{page_count} pages**. You MUST create a shared "
                f"navigation component in `layout.tsx` linking ALL pages."
            )
            if ctx.route_map:
                route_list = ", ".join(sorted(ctx.route_map.keys()))
                lines.append(f"Routes: {route_list}")
            lines.append(
                "Bare `<body>{children}</body>` will be **REJECTED** by the completion gate."
            )

    return "\n".join(lines) if lines else ""

def _build_dedup_advisory_section(
    profile: str,
    config: dict | None = None,
) -> str:
    """Build DEDUP ADVISORY section — prevent duplicate data-write logic.

    SS-5: Code agents frequently duplicate data-write logic across API route
    handlers because they don't know about existing helper functions in lib/.
    This advisory instructs the agent to search lib/ before implementing
    any data writes in API routes.

    Only emitted for 'code' profile (controlled by config['dedup_advisory']).
    """
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    if not config.get("dedup_advisory"):
        return ""

    return (
        "### ⚠️ DEDUP ADVISORY\n"
        "Before implementing data writes in API routes, search lib/ for existing "
        "functions that perform the same writes to avoid duplicates. "
        "Reuse existing helpers instead of re-implementing data access logic."
    )

def _build_component_spec_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build COMPONENTS section — designer component specs."""
    if not config.get("component_specs"):
        return ""
    # RCA-461 Bug #4: use canonical docs/ path (was project root)
    spec_path = os.path.join(project_dir, "docs", "component-spec.md")
    if not os.path.isfile(spec_path):
        return ""
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, OSError):
        return ""
    if not content.strip():
        return ""
    if len(content) > 3000:
        content = content[:3000] + "\n...(truncated)"
    return (
        "### COMPONENT SPECIFICATION (from designer — MANDATORY)\n"
        "Follow this component spec. Do NOT invent your own component structure.\n"
        "🔴 You MUST create a file for EVERY component listed below. "
        "The delivery gate will verify these files exist.\n\n"
        f"{content}"
    )

def _build_mockup_refs_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build MOCKUPS section — designer mockup PNG refs."""
    if not config.get("mockup_refs"):
        return ""
    mockup_dir = os.path.join(project_dir, "docs", "design-mockups")
    if not os.path.isdir(mockup_dir):
        return ""
    png_files = sorted(glob.glob(os.path.join(mockup_dir, "*.png")))
    if not png_files:
        return ""
    lines = ["### DESIGN MOCKUPS (READ BEFORE CODING)"]
    lines.append("Use `read_file` to examine each mockup BEFORE writing the page.")
    for fpath in png_files:
        lines.append(f"- `{fpath}`")
    return "\n".join(lines)

def _build_bdd_section(
    project_dir: str,
    kwargs: dict,
    config: dict,
) -> str:
    """Build BDD section — acceptance scenarios from bdd-scenarios.md."""
    if not config.get("bdd_scenarios"):
        return ""
    # If bdd_specs provided via kwargs, use those instead of file
    bdd_specs = kwargs.get("bdd_specs", [])
    if bdd_specs:
        return ""  # TDD section handles kwargs bdd_specs
    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.isfile(bdd_path):
        return ""
    try:
        with open(bdd_path, "r", encoding="utf-8") as f:
            bdd_content = f.read()
    except (IOError, OSError):
        return ""
    if not bdd_content.strip():
        return ""
    # Filter by requirement_ids if provided
    requirement_ids = kwargs.get("requirement_ids", [])
    if requirement_ids:
        sections = []
        current_section: list[str] = []
        for line in bdd_content.splitlines():
            stripped = line.lstrip('#').lstrip()
            if stripped.startswith("Feature:"):
                if current_section:
                    sections.append("\n".join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        if current_section:
            sections.append("\n".join(current_section))
        matching = [s for s in sections if any(rid in s for rid in requirement_ids)]
        if matching:
            bdd_content = "\n\n".join(matching)
    if len(bdd_content) > 4000:
        bdd_content = bdd_content[:4000] + "\n...(truncated)"
    return (
        "### BDD ACCEPTANCE CRITERIA\n"
        "Each THEN clause defines what \"done\" means. Tests must verify these.\n\n"
        f"{bdd_content}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# WP-5: Integration Wiring + Dependency Context Sections
# ═══════════════════════════════════════════════════════════════════════════


def _build_integration_wiring_section(
    project_dir: str,
    config: dict,
    requirement_ids: list[str],
) -> str:
    """Build INTEGRATION WIRING REQUIREMENTS section from dependency graph.

    Reads docs/dependency-graph.json and generates 'MUST import from' mandates
    for modules relevant to the current delegation's requirement IDs.

    Args:
        project_dir: Path to the project directory.
        config: Section config dict (needs 'integration_wiring': True).
        requirement_ids: REQ-IDs assigned to this delegation.

    Returns:
        Formatted section string, or "" if disabled/unavailable.
    """
    if not config.get("integration_wiring"):
        return ""

    import json as _json
    import os as _os

    graph_path = _os.path.join(project_dir, "docs", "dependency-graph.json")
    if not _os.path.isfile(graph_path):
        return ""

    try:
        with open(graph_path, "r") as f:
            dep_graph = _json.load(f)
    except (IOError, _json.JSONDecodeError):
        return ""

    modules = dep_graph.get("modules", {})
    if not modules:
        return ""

    # Find modules relevant to this delegation's requirement IDs
    req_set = set(requirement_ids) if requirement_ids else set()
    lines = ["### INTEGRATION WIRING REQUIREMENTS"]

    for mod_path, mod_info in modules.items():
        if not isinstance(mod_info, dict):
            continue
        mod_reqs = set(mod_info.get("req_guids", []))
        if not req_set or mod_reqs & req_set:
            imports = mod_info.get("imports", [])
            for imp in imports:
                lines.append(
                    f"- **{mod_path}** MUST import from **{imp}**"
                )

    # Also include page-API bindings
    bindings = dep_graph.get("page_api_bindings", [])
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        page = binding.get("page", "")
        api = binding.get("api", "")
        method = binding.get("method", "GET")
        # Include if page or api module has relevant req_guids
        page_info = modules.get(page, {})
        api_info = modules.get(api, {})
        page_reqs = set(page_info.get("req_guids", []) if isinstance(page_info, dict) else [])
        api_reqs = set(api_info.get("req_guids", []) if isinstance(api_info, dict) else [])
        if not req_set or page_reqs & req_set or api_reqs & req_set:
            lines.append(
                f"- **{page}** MUST call {method} **{api}**"
            )

    if len(lines) <= 1:
        return ""

    lines.append("")
    lines.append("🔴 After implementation, verify each import above exists. If ANY is missing, task is INCOMPLETE.")

    return "\n".join(lines)


def _build_dependency_context_section(
    project_dir: str,
    config: dict,
    requirement_ids: list[str],
) -> str:
    """Build DEPENDENCY CONTEXT section from dependency graph.

    Tells the code agent which other modules will consume its exports,
    so it understands the downstream impact of its implementation.

    Args:
        project_dir: Path to the project directory.
        config: Section config dict (needs 'dependency_context': True).
        requirement_ids: REQ-IDs assigned to this delegation.

    Returns:
        Formatted section string, or "" if disabled/unavailable.
    """
    if not config.get("dependency_context"):
        return ""

    import json as _json
    import os as _os

    graph_path = _os.path.join(project_dir, "docs", "dependency-graph.json")
    if not _os.path.isfile(graph_path):
        return ""

    try:
        with open(graph_path, "r") as f:
            dep_graph = _json.load(f)
    except (IOError, _json.JSONDecodeError):
        return ""

    modules = dep_graph.get("modules", {})
    if not modules:
        return ""

    req_set = set(requirement_ids) if requirement_ids else set()
    lines = ["### DEPENDENCY CONTEXT (from dependency-graph.json)"]
    found_any = False

    for mod_path, mod_info in modules.items():
        if not isinstance(mod_info, dict):
            continue
        mod_reqs = set(mod_info.get("req_guids", []))
        if not req_set or mod_reqs & req_set:
            called_by = mod_info.get("called_by", [])
            imports = mod_info.get("imports", [])

            if called_by or imports:
                found_any = True
                lines.append(f"\n**{mod_path}**:")
                if called_by:
                    lines.append("  This module will be imported by:")
                    for caller in called_by:
                        lines.append(f"  - `{caller}`")
                if imports:
                    lines.append("  This module imports from:")
                    for imp in imports:
                        lines.append(f"  - `{imp}`")

    if not found_any:
        return ""

    return "\n".join(lines)


def _build_phase_prerequisite_section(
    project_dir: str,
    config: dict,
    current_phase_seq: str = "",
) -> str:
    """Build PHASE PREREQUISITES section from decomposition-index.json.

    RCA-475 Fix 6: Tells the code agent which predecessor phases have
    completed (and which haven't), so it understands what code/files
    already exist and what dependencies may be missing.

    Args:
        project_dir: Path to the project directory.
        config: Section config dict (needs 'phase_prerequisites': True).
        current_phase_seq: The seq ID of the current phase being delegated.

    Returns:
        Formatted section string, or "" if disabled/unavailable.
    """
    if not config.get("phase_prerequisites"):
        return ""

    if not current_phase_seq:
        return ""

    import json as _json
    import os as _os

    index_path = _os.path.join(project_dir, "docs", "decomposition-index.json")
    if not _os.path.isfile(index_path):
        return ""

    try:
        with open(index_path, "r") as f:
            decomp_index = _json.load(f)
    except (IOError, _json.JSONDecodeError):
        return ""

    phases = decomp_index.get("phases", [])
    if not phases:
        return ""

    # Parse current phase seq for comparison
    try:
        current_seq_float = float(current_phase_seq)
    except (ValueError, TypeError):
        return ""

    # Find predecessor phases (seq < current)
    predecessors = []
    for phase in phases:
        try:
            phase_seq = float(phase.get("seq", ""))
        except (ValueError, TypeError):
            continue
        if phase_seq < current_seq_float:
            predecessors.append(phase)

    if not predecessors:
        return ""

    lines = [f"### PHASE PREREQUISITES (for Phase {current_phase_seq})"]
    has_warning = False

    for pred in sorted(predecessors, key=lambda p: float(p.get("seq", 0))):
        seq = pred.get("seq", "?")
        name = pred.get("name", "Unnamed")
        status = pred.get("status", "unknown")

        if status == "completed":
            lines.append(f"- **Phase {seq}** ({name}): ✅ completed")
        elif status in ("pending", "not_started"):
            lines.append(
                f"- **Phase {seq}** ({name}): ⚠️ **WARNING — NOT COMPLETED** (status: {status})"
            )
            has_warning = True
        else:
            lines.append(f"- **Phase {seq}** ({name}): {status}")

    if has_warning:
        lines.append(
            "\n> **WARNING**: Some predecessor phases are not yet completed. "
            "Their deliverables may not exist on disk. Do NOT assume files "
            "from pending phases are available."
        )

    return "\n".join(lines)