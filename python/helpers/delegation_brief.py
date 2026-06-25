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
    _build_fidelity_section, _build_tdd_section, _build_acceptance_section, detect_orm_and_build_seed_mandate, _build_database_seed_mandate_section,
    _build_tsc_mandate_section, _build_scaffold_mandate_section, _build_tools_section, _build_dev_server_url_section, _build_budget_section,
    _build_skill_section, _build_auth_prohibition_section, _detect_framework, _build_framework_pitfalls_section, _build_domain_context_section,
    _build_compliance_section
)

# Re-export orchestration functions
from python.helpers.delegation_brief_builders import (
    build_delegation_package,
    build_delegation_brief,
    build_scoped_fix_delegation,
    build_remediation_brief
) # noqa: F401


# Default for unknown profiles — safe minimum

# ═══════════════════════════════════════════════════════════════
# Phase-Aware Delegation (RCA-ITR36)
# ═══════════════════════════════════════════════════════════════
# When phase >= 5 (verification/fix), restrict context injection
# to prevent diagnosis scope creep — a diagnosis agent should only
# see the failing route's context, not the full manifest/requirements.
# Root cause: build_delegation_package() had ZERO phase awareness,
# causing a Phase 6 fix agent to see ALL BDD/mockups/requirements
# and audit working pages, destroying an 85%-done site.




# Keys to PRESERVE in restricted mode (needed for fixing/diagnosing)

# Keys to RESTRICT (set to False or 'assigned') in phases >= 5
# RCA-ITR36: These keys provide "build this" context that fix agents
# misinterpret as instructions to rebuild from scratch. Fix agents
# should ONLY see error context, verification findings, and codebase state.




# Profile → Available Tools mapping (from delegation_message.py PROFILE_TOOLS)


# ═══════════════════════════════════════════════════════════════
# Project Context Dataclass
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# Project Context Loader (cached)
# ═══════════════════════════════════════════════════════════════














# ═══════════════════════════════════════════════════════════════
# Section Builders
# ═══════════════════════════════════════════════════════════════























# ═══════════════════════════════════════════════════════════════
# Tier 1 — New Section Builders (absorbed from delegation_message.py)
# ═══════════════════════════════════════════════════════════════










# SS-4: type_coherence validator was deleted — always None









# ═══════════════════════════════════════════════════════════════
# Tier 2 — Runtime State Section Builders
# ═══════════════════════════════════════════════════════════════













# ═══════════════════════════════════════════════════════════════
# Tier 2b — Conditional Mandate Section Builders
# ═══════════════════════════════════════════════════════════════











# ═══════════════════════════════════════════════════════════════
# Tier 3 — Operational Section Builders
# ═══════════════════════════════════════════════════════════════










# ═══════════════════════════════════════════════════════════════
# F-7: Auth Prohibition Section Builder
# ═══════════════════════════════════════════════════════════════

# Categories that indicate auth-related requirements




# ═══════════════════════════════════════════════════════════════
# F-3 (ITR-25): Framework Anti-Pattern Section
# ═══════════════════════════════════════════════════════════════
# Agents generate `export const dynamic = 'force-dynamic'` in 'use client'
# files, causing ALL routes to 500. This section warns about known
# anti-patterns AND provides solutions (not just warnings).
#
# User feedback: "anti-patterns are fine, but the agents / llms also need
# solutions & ability to dynamically adapt — ensure we're not overfitting
# and guide them to use researcher to get updated solutions."



# Framework-specific pitfall rules.
# Each rule has: warning (what can go wrong), pattern (code pattern to avoid),
# and solution (the correct approach).
# NOTE: These are ADVISORY — not blockers. The agent should also use RESEARCHER
# for the latest framework docs, since APIs change across versions.




# ═══════════════════════════════════════════════════════════════
# F-9 (ITR-25): Domain Context Section
# ═══════════════════════════════════════════════════════════════

# Maximum characters for the user prompt in the domain context section.
# Long prompts are truncated to prevent brief bloat.






# ═══════════════════════════════════════════════════════════════
# Main Entry Points
# ═══════════════════════════════════════════════════════════════





# ═══════════════════════════════════════════════════════════════
# FIX-013: Phase-Aware Delegation Context (R-1)
# ═══════════════════════════════════════════════════════════════




# ═══════════════════════════════════════════════════════════════
# Remediation Brief Builder (Incremental Fix Re-Delegation T2)
# ═══════════════════════════════════════════════════════════════





