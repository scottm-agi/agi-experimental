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


from python.helpers.delegation_brief_config import (
    get_phase_aware_config, detect_delegation_phase, phase_aware_context_filter,
    _PROFILE_TOOLS, _AUTH_CATEGORIES, _AUTH_KEYWORDS, _MAX_DOMAIN_CONTEXT_CHARS,
    PROFILE_CONTEXT_CONFIG, _DEFAULT_CONFIG, _PHASE_RESTRICTED_OVERRIDES,
    _FRAMEWORK_PITFALL_RULES, check_type_coherence
)
from python.helpers.delegation_brief_context import ProjectContext, _read_json, _read_env, _list_research_docs, _extract_routes, invalidate_project_context_cache, _load_project_context

from python.helpers.delegation_brief_sections import (
    _build_who_section, _build_why_section, _build_what_section, _build_with_section, _flatten_token_dict, _build_how_section, _build_where_section,
    _build_route_map_section, _build_integration_section, _build_mandates_section, _build_dedup_advisory_section, _build_component_spec_section,
    _build_mockup_refs_section, _build_bdd_section, _build_research_docs_inline_section, _build_schema_lock_section, _build_codebase_state_section,
    _build_infrastructure_fast_pass_section, _build_error_relay_section, _build_gate_failure_section, _build_verification_section,
    _build_fidelity_section, _build_manifest_values_section, _build_tdd_section, _build_acceptance_section, detect_orm_and_build_seed_mandate, _build_database_seed_mandate_section,
    _build_tsc_mandate_section, _build_scaffold_mandate_section, _build_tools_section, _build_dev_server_url_section, _build_budget_section,
    _build_skill_section, _build_auth_prohibition_section, _detect_framework, _build_framework_pitfalls_section, _build_domain_context_section,
    _build_compliance_section, _build_integration_wiring_section, _build_dependency_context_section,
    _build_phase_prerequisite_section
)


def build_delegation_package(
    profile: str,
    message: str,
    kwargs: dict,
    project_dir: str,
    agent: "Agent",
    subordinate: "Agent",
    *,
    is_batch: bool = False,
    phase: int | None = None,
) -> str:
    """Build the complete delegation package for a subordinate agent.

    This is the consolidated single-call API that replaces 25 separate
    inject_* functions. It reads all sources (disk + agent.data + kwargs),
    decides what's relevant per profile config, and outputs ONE structured
    document.

    Three tiers:
      Tier 1: Project Context (from disk, LRU-cached)
      Tier 2: Runtime State (from agent.data, conditional)
      Tier 3: Operational (lightweight, always computed)

    Args:
        profile: Agent profile name (code, frontend, e2e, architect, etc.)
        message: Original delegation message from orchestrator
        kwargs: Delegation kwargs (may contain requirement_ids, bdd_specs, etc.)
        project_dir: Absolute path to the project directory
        agent: The parent/orchestrator agent (for runtime state in agent.data)
        subordinate: The target subordinate agent (for tracking writes)
        is_batch: Whether this is a batch delegation (same output, for parity)
        phase: Current phase number (0-7) or None. If None, auto-detected
            from message text and agent.data['_current_phase'].

    Returns:
        Message with package prepended. If no context is available, returns
        message unchanged.
    """
    if not kwargs:
        kwargs = {}

    config = PROFILE_CONTEXT_CONFIG.get(profile.lower() if profile else "", _DEFAULT_CONFIG)
    agent_data = getattr(agent, "data", {}) if agent else {}

    # ── RCA-ITR36: Phase-aware config override ──
    # When phase >= 5 (verification/fix), restrict context injection
    # to prevent diagnosis scope creep.
    if phase is None:
        phase = detect_delegation_phase(message, agent_data)
    if phase is not None:
        config = get_phase_aware_config(profile.lower() if profile else '', phase)

    # ── Fix-2: Pre-Phase-5 Full Project Snapshot ──
    # Snapshot the project before verification agents can damage it.
    if phase == 5 and project_dir and os.path.isdir(project_dir):
        try:
            from python.helpers.pre_phase5_snapshot import snapshot_exists, create_snapshot
            if not snapshot_exists(project_dir):
                snap_result = create_snapshot(project_dir)
                if snap_result.get("success"):
                    logger.info(
                        "[SNAPSHOT] Pre-Phase-5 snapshot created at %s",
                        snap_result["snapshot_path"],
                    )
                else:
                    logger.warning("[SNAPSHOT] Pre-Phase-5 snapshot creation failed")
        except Exception as exc:
            logger.warning("[SNAPSHOT] Pre-Phase-5 snapshot failed (non-fatal): %s", exc)

    # ── ISS-F: Invalidate project context cache for verify/fix stages ──
    # _load_project_context uses @lru_cache. After builds modify project
    # files, later delegations need fresh data, not stale build-phase artifacts.
    if phase is not None and is_verification_or_later(phase):
        _load_project_context.cache_clear()

    sections = []

    # ── ISS-A: Fix-mode frame as FIRST section (RCA-ITR36) ──
    # The scope guard must run BEFORE any context sections so the agent
    # reads the operational mode directive FIRST. LLMs weight earlier
    # context more heavily — putting "FIX / ADDITIVE ONLY" at the end
    # of a 3000-word brief was ineffective (primacy effect).
    try:
        from python.helpers.delegation_scope_guard import get_fix_mode_frame
        fix_frame = get_fix_mode_frame(message)
        if fix_frame:
            sections.append(fix_frame)
    except Exception:
        pass  # Fail-open — scope guard is defensive, not blocking

    # ── ISSUE-6: PROJECT ROOT preamble (absolute path anchor) ──
    # Subordinate agents read this FIRST so they know the absolute path
    # prefix for all file operations. Defense-in-depth: even if individual
    # section builders miss a path, the LLM sees the root upfront.
    if project_dir and os.path.isdir(project_dir):
        sections.append(
            f"### \U0001f5c2\ufe0f PROJECT ROOT: `{project_dir}`\n"
            f"All file paths below are relative to this root. "
            f"Use this absolute path prefix for all file operations."
        )

    # ── TIER 1: Project Context (from disk, cached) ──
    if project_dir and os.path.isdir(project_dir):
        ctx = _load_project_context(project_dir)

        who = _build_who_section(profile, kwargs, ctx)
        if who:
            sections.append(who)

        why = _build_why_section(profile, kwargs, ctx)
        if why:
            sections.append(why)

        what = _build_what_section(profile, kwargs, ctx, config)
        if what:
            sections.append(what)

        with_vals = _build_with_section(profile, kwargs, ctx, config)
        if with_vals:
            sections.append(with_vals)

        how = _build_how_section(profile, ctx, config)
        if how:
            sections.append(how)

        where = _build_where_section(profile, ctx, config, project_dir=project_dir)
        if where:
            sections.append(where)

        route_map = _build_route_map_section(profile, ctx, config)
        if route_map:
            sections.append(route_map)

        integration = _build_integration_section(profile, kwargs, ctx, config)
        if integration:
            sections.append(integration)

        # WP-5: Integration wiring mandates from dependency graph
        req_ids = kwargs.get("requirement_ids", [])
        wiring = _build_integration_wiring_section(project_dir, config, req_ids)
        if wiring:
            sections.append(wiring)

        dep_ctx = _build_dependency_context_section(project_dir, config, req_ids)
        if dep_ctx:
            sections.append(dep_ctx)

        # RCA-475 Fix 6: Phase prerequisite context
        phase_seq = kwargs.get("phase_seq", "")
        prereqs = _build_phase_prerequisite_section(project_dir, config, current_phase_seq=phase_seq)
        if prereqs:
            sections.append(prereqs)

        # F-7: Auth prohibition for code agents when no auth reqs
        auth_prohibition = _build_auth_prohibition_section(profile, kwargs, ctx, config)
        if auth_prohibition:
            sections.append(auth_prohibition)

        mandates = _build_mandates_section(profile, ctx, config)
        if mandates:
            sections.append(mandates)

        # SS-5: Dedup advisory (code profile only)
        dedup = _build_dedup_advisory_section(profile, config)
        if dedup:
            sections.append(dedup)

        # New Tier 1 sections (absorbed from delegation_message.py)
        comp = _build_component_spec_section(project_dir, config)
        if comp:
            sections.append(comp)

        mockups = _build_mockup_refs_section(project_dir, config)
        if mockups:
            sections.append(mockups)

        bdd = _build_bdd_section(project_dir, kwargs, config)
        if bdd:
            sections.append(bdd)

        # WB-4: Compliance sub-types (CAN-SPAM, GDPR, ADA) when present in requirements
        compliance = _build_compliance_section(kwargs, ctx)
        if compliance:
            sections.append(compliance)

        research = _build_research_docs_inline_section(project_dir, config)
        if research:
            sections.append(research)

        codebase = _build_codebase_state_section(project_dir, config)
        if codebase:
            sections.append(codebase)

        # SS-4: Schema lock injection for type coherence
        schema_lock = _build_schema_lock_section(project_dir, config)
        if schema_lock:
            sections.append(schema_lock)

        # ITR-44: Infrastructure fast-pass — skip re-setup when infra is verified
        infra_fast_pass = _build_infrastructure_fast_pass_section(project_dir, profile.lower() if profile else "", config)
        if infra_fast_pass:
            sections.append(infra_fast_pass)
    else:
        ctx = ProjectContext()

    # ── F-9 (ITR-25): Domain Context (from agent.data, conditional) ──
    if agent_data is not None and config.get("domain_context"):
        domain_ctx = _build_domain_context_section(agent_data, project_dir or "")
        if domain_ctx:
            sections.append(domain_ctx)

    # ── F-3 (ITR-25): Framework Anti-Pattern Pitfalls (from package.json) ──
    if project_dir and os.path.isdir(project_dir):
        pitfalls = _build_framework_pitfalls_section(project_dir, config)
        if pitfalls:
            sections.append(pitfalls)

    # ── TIER 2: Runtime State (from agent.data, conditional) ──
    if agent_data is not None:
        errors = _build_error_relay_section(agent_data, project_dir or "", config)
        if errors:
            sections.append(errors)

        gate = _build_gate_failure_section(agent_data, config)
        if gate:
            sections.append(gate)

        verif = _build_verification_section(agent_data, config)
        if verif:
            sections.append(verif)

        fidelity = _build_fidelity_section(agent_data, config)
        if fidelity:
            sections.append(fidelity)

        # F-8 (RCA-461): Inject manifest values at Phase 3+ so code agents
        # see brand names, URLs, prices BEFORE writing code.
        if phase is None or (isinstance(phase, (int, float)) and phase >= 3):
            manifest_vals = _build_manifest_values_section(agent_data, phase_id=str(phase or ""))
            if manifest_vals:
                sections.append(manifest_vals)

        tdd = _build_tdd_section(agent_data, kwargs, config, ctx, project_dir or "", phase=phase)
        if tdd:
            sections.append(tdd)

        acceptance = _build_acceptance_section(agent_data, kwargs, config)
        if acceptance:
            sections.append(acceptance)

    # ── TIER 2b: Conditional mandates (need project_dir) ──
    if project_dir and os.path.isdir(project_dir):
        tsc = _build_tsc_mandate_section(project_dir, config)
        if tsc:
            sections.append(tsc)

        scaffold = _build_scaffold_mandate_section(kwargs, config, phase=phase, message=message)
        if scaffold:
            sections.append(scaffold)

        seed_mandate = _build_database_seed_mandate_section(project_dir, config)
        if seed_mandate:
            sections.append(seed_mandate)

    # ── TIER 3: Operational (lightweight) ──
    tools = _build_tools_section(profile, config)
    if tools:
        sections.append(tools)

    # RCA-FIX: Inject verified dev server URL so agents don't guess port 3000
    dev_url = _build_dev_server_url_section(profile, config)
    if dev_url:
        sections.append(dev_url)

    if agent and subordinate:
        budget = _build_budget_section(agent, subordinate, kwargs, config)
        if budget:
            sections.append(budget)

    if agent:
        skill = _build_skill_section(agent, profile, config)
        if skill:
            sections.append(skill)

    # ── Assemble ──
    if not sections:
        return message

    brief = "\n\n".join(sections)

    return (
        f"## 📋 Delegation Package (Auto-Generated — READ FIRST)\n\n"
        f"{brief}\n\n"
        f"---\n\n"
        f"{message}"
    )

def build_delegation_brief(
    profile: str,
    message: str,
    kwargs: dict,
    project_dir: str,
    *,
    is_batch: bool = False,
) -> str:
    """Backward-compatible alias for build_delegation_package().

    This function is called by code that hasn't been updated to use
    the new build_delegation_package() signature. It creates a minimal
    mock agent to satisfy the new API.

    For new code, use build_delegation_package() directly.
    """
    # Create minimal mock objects for backward compatibility
    from unittest.mock import MagicMock
    mock_agent = MagicMock()
    mock_agent.data = {}
    mock_agent._absolute_turns = 0
    mock_agent.get_max_turns.return_value = 0
    mock_agent.get_data.return_value = None
    mock_subordinate = MagicMock()
    mock_subordinate.data = {}

    return build_delegation_package(
        profile=profile,
        message=message,
        kwargs=kwargs,
        project_dir=project_dir,
        agent=mock_agent,
        subordinate=mock_subordinate,
        is_batch=is_batch,
    )

def build_scoped_fix_delegation(
    failing_test: str,
    source_file: str,
    error_message: str,
    additional_context: str = "",
) -> str:
    """Build a minimal, scoped delegation brief for fix-mode delegations.

    Sends ONLY the failing test, source file, and error — nothing else.
    This prevents fix agents from receiving full build-phase context
    (manifest, requirements, BDD scenarios) which causes them to
    rebuild from scratch instead of making surgical fixes.

    Per architecture §13.3: Fix delegations should contain the minimum
    context needed to diagnose and fix the specific failure.

    Args:
        failing_test: Path/name of the failing test file.
        source_file: Path/name of the source file under test.
        error_message: The error/assertion message from the test failure.
        additional_context: Optional extra context (e.g., stack trace snippet).

    Returns:
        A minimal delegation brief string.
    """
    sections = [
        "### SCOPED FIX DELEGATION",
        "",
        "**Mode**: Surgical fix — address ONLY the specific failure below.",
        "Do NOT rewrite, restructure, or expand scope beyond this fix.",
        "",
        "#### Failing Test",
        f"File: `{failing_test}`",
        "",
        "#### Source File",
        f"File: `{source_file}`",
        "",
        "#### Error",
        "```",
        error_message,
        "```",
    ]

    if additional_context:
        sections.extend([
            "",
            "#### Additional Context",
            additional_context,
        ])

    sections.extend([
        "",
        "#### Instructions",
        "1. Read the failing test to understand what is expected",
        "2. Read the source file to understand current implementation",
        "3. Make the MINIMUM change to fix the failure",
        "4. Do NOT modify any other files unless directly required",
    ])

    return "\n".join(sections)

def build_remediation_brief(
    phase_seq: str,
    attempt_history: dict,
    project_dir: str,
    agent_data: dict,
    config: Optional[dict] = None,
) -> str:
    """Build a scoped remediation brief for re-delegation.

    This is NOT the full phase brief. It contains:
    1. What was already done (files created, features implemented)
    2. What SPECIFICALLY is broken (exact stubs, exact build errors)
    3. Instructions to fix ONLY those issues
    4. Explicit "DO NOT rewrite files that already work"
    5. PLUS relevant context sections (manifest, design tokens, route map,
       BDD scenarios, codebase state) so the agent can actually fix issues

    The KEY difference from build_scoped_fix_delegation():
        - build_scoped_fix_delegation strips ALL context
        - build_remediation_brief KEEPS context but scopes the TASK

    The remediation brief is structurally different from the normal
    delegation brief (starts with REMEDIATION header instead of
    Delegation Package) so topic dedup doesn't fire.

    Args:
        phase_seq: Phase sequence identifier (e.g. "3.1").
        attempt_history: Dict from phase_attempt_ledger with "attempts"
            list and "total_attempts" count.
        project_dir: Absolute path to the project directory.
        agent_data: The agent's data dictionary.
        config: Profile context config dict. Defaults to
            PROFILE_CONTEXT_CONFIG["code"].

    Returns:
        Scoped remediation brief string, or empty string if there are
        no issues to fix.
    """
    if config is None:
        config = PROFILE_CONTEXT_CONFIG.get("code", _DEFAULT_CONFIG)

    # ── Collect issues from latest attempt ──
    attempts = attempt_history.get("attempts", [])
    if not attempts:
        return ""

    latest = attempts[-1]
    stubs = latest.get("stubs_remaining", [])
    build_errors = latest.get("build_errors", [])
    test_failures = latest.get("test_failures", [])

    # If no issues, nothing to remediate
    if not stubs and not build_errors and not test_failures:
        return ""

    total_attempts = attempt_history.get("total_attempts", len(attempts))
    next_attempt = total_attempts + 1

    # ── Collect completed files across all attempts ──
    completed_files = []
    seen_files = set()
    for att in attempts:
        for f in att.get("files_created", []):
            if f not in seen_files:
                seen_files.add(f)
                completed_files.append(f)

    # ══════════════════════════════════════════════════════════
    # Build TASK section (scoped to specific issues)
    # ══════════════════════════════════════════════════════════
    sections = []

    # Header — structurally different from normal brief to avoid topic dedup
    sections.append(
        f"### 🔧 REMEDIATION DELEGATION — Phase {phase_seq} (Attempt #{next_attempt})\n\n"
        f"**Mode**: Surgical fix — address ONLY the specific issues listed below."
    )

    # Already completed files
    if completed_files:
        completed_lines = ["#### ✅ Already Completed (DO NOT TOUCH)"]
        for f in completed_files:
            completed_lines.append(f"- `{f}`")
        sections.append("\n".join(completed_lines))

    # Issues to fix — numbered
    issue_lines = ["#### 🔴 Fix These ONLY\nWrite complete, working code — NOT stubs, TODOs, or placeholders."]
    issue_num = 0

    for stub in stubs:
        issue_num += 1
        file_ref = stub.get("file", "unknown")
        line_ref = stub.get("line", "?")
        content = stub.get("content", "")
        issue_lines.append(
            f"{issue_num}. **Incomplete implementation at `{file_ref}:{line_ref}`**\n"
            f"   ```\n"
            f"   {content}\n"
            f"   ```\n"
            f"   → Replace with actual implementation"
        )

    for error in build_errors:
        issue_num += 1
        error_str = error if isinstance(error, str) else str(error)
        issue_lines.append(
            f"{issue_num}. **Build error**\n"
            f"   ```\n"
            f"   {error_str}\n"
            f"   ```\n"
            f"   → Fix the build error"
        )

    for failure in test_failures:
        issue_num += 1
        test_file = failure.get("test_file", "unknown")
        assertion = failure.get("assertion", "")
        error = failure.get("error", "")
        issue_lines.append(
            f"{issue_num}. **Test failure: `{test_file}`**\n"
            f"   ```\n"
            f"   {assertion}: {error}\n"
            f"   ```\n"
            f"   → Fix the failing test"
        )

    sections.append("\n".join(issue_lines))

    # Scope fence
    sections.append(
        f"#### 🚫 Scope Fence\n"
        f"- Fix ONLY the {issue_num} issue{'s' if issue_num != 1 else ''} above\n"
        f"- DO NOT modify files listed under \"Already Completed\"\n"
        f"- DO NOT restructure, rename, or expand scope\n"
        f"- After fixing, run `npm run build` to verify"
    )

    # ══════════════════════════════════════════════════════════
    # Build CONTEXT sections (same as normal delegation brief)
    # ══════════════════════════════════════════════════════════
    if project_dir and os.path.isdir(project_dir):
        ctx = _load_project_context(project_dir)

        # Manifest values (pricing, business name, URLs)
        with_vals = _build_with_section("code", {}, ctx, config)
        if with_vals:
            sections.append(with_vals)

        # Design tokens
        how = _build_how_section("code", ctx, config)
        if how:
            sections.append(how)

        # Route map
        route_map = _build_route_map_section("code", ctx, config)
        if route_map:
            sections.append(route_map)

        # BDD scenarios
        bdd = _build_bdd_section(project_dir, {}, config)
        if bdd:
            sections.append(bdd)

        # Codebase state
        codebase = _build_codebase_state_section(project_dir, config)
        if codebase:
            sections.append(codebase)

    return "\n\n".join(sections)