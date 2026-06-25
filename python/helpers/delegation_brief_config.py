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



def detect_delegation_phase(message: str, agent_data: dict | None = None) -> int | float | None:
    """Detect the current phase from delegation message or agent.data.

    Priority:
    1. Explicit 'Phase N' in message text (most reliable)
    1.5. Fix-mode keyword signals → inferred Phase 6 (RCA-ITR36 RC-3)
    2. _delivery_attempted flag → inferred Phase 5+ (RCA-ITR36 RC-3)
    3. agent.data['_current_phase'] (fallback)

    Returns phase number (0-7) or None if not detected.
    """
    import re

    # Priority 1: Regex match in message text (case-insensitive)
    if message:
        match = re.search(r'phase\s*(\d+(?:\.\d+)?)', message, re.IGNORECASE)
        if match:
            val = match.group(1)
            return float(val) if '.' in val else int(val)

    # Priority 1.5: Fix-mode keyword detection (RCA-ITR36 RC-3)
    # When the orchestrator says "fix", "debug", "surgical", etc. but
    # doesn't include "Phase N" text, infer Phase 6 (fix phase).
    # This prevents code agents from receiving full build-phase context.
    if message:
        msg_lower = message.lower()
        _FIX_KEYWORDS = (
            'surgical fix', 'fix the following', 'fix these issues',
            'fix this error', 'verification failure', 'additive only',
            'do not rewrite', 'prohibited changes', 'fix mode',
            'resolve this', 'resolve the', 'debug this', 'debug the',
            'fix the specific', 'fix only', 'patch the', 'hotfix',
            'regression fix', 'quick fix', 'targeted fix',
        )
        if any(kw in msg_lower for kw in _FIX_KEYWORDS):
            return 6

    # Priority 2: _delivery_attempted → we're past Phase 4 build
    if agent_data and isinstance(agent_data, dict):
        if agent_data.get('_delivery_attempted', False):
            return 5  # At minimum Phase 5

    # Priority 3: Fallback to agent_data
    if agent_data and isinstance(agent_data, dict):
        phase_val = agent_data.get('_current_phase')
        if phase_val is not None:
            try:
                return int(phase_val)
            except (ValueError, TypeError):
                pass

    return None

def get_phase_aware_config(profile: str, phase: int | float | None) -> dict:
    """Get profile config with phase-aware overrides.

    Phase 0-1 (scaffold/planning): suppress artifacts not yet created.
    Phase 2-4 (build): full config.
    Phase >= 5 (verification/fix): restrict context to prevent scope creep.

    Args:
        profile: Agent profile name
        phase: Current phase number (0-7) or None

    Returns:
        Config dict (may be original or restricted copy)
    """
    original = PROFILE_CONTEXT_CONFIG.get(profile, _DEFAULT_CONFIG)

    # No phase detected → full config (backward compat)
    if phase is None:
        return original

    # ── RCA-ITR42 RC-6: Phase 0-1 (scaffold/planning) overrides ──
    # Suppress artifacts that don't exist yet during early phases.
    # Keep tdd_mandate (for infra tests) and scaffold_minimal (for rules).
    if is_planning_phase(phase) or is_scaffold_phase(phase):
        scaffold_overrides = {
            'design_tokens': False,
            'bdd_scenarios': False,
            'mockup_refs': False,
            'component_specs': False,
            'acceptance_criteria': False,
            'navigation_mandate': False,
            'design_system_mandate': False,
        }
        return {**original, **scaffold_overrides}

    # Phase 2-4 (build phases) → full config
    if phase < 5:
        return original

    # Phase >= 5 → apply restrictions, preserving fix-relevant keys
    restricted = {**original, **_PHASE_RESTRICTED_OVERRIDES}

    # Restore preserved keys from original config
    for key in _PHASE_RESTRICTED_PRESERVE_KEYS:
        if key in original:
            restricted[key] = original[key]

    return restricted

def phase_aware_context_filter(
    brief: str,
    phase: int | None,
) -> str:
    """Filter a delegation brief based on the current pipeline phase.

    Per architecture §13.3:
    - Phase <= 3.5: Full brief (no filtering) — build phase needs everything
    - Phase 4+: Scoped brief — only error context + affected files
    - Phase 5+: Surgical brief — only the specific failure context

    Args:
        brief: The full delegation brief string.
        phase: Current phase number (0-7) or None.

    Returns:
        Filtered brief (may be original if no filtering needed).
    """
    # No phase → full brief (backward compat)
    if phase is None or phase <= 3:
        return brief

    # Phase 4+: Remove build-phase sections that cause scope creep
    _BUILD_PHASE_HEADERS = [
        "### WHAT TO BUILD",
        "### MANIFEST",
        "### CONTENT MANIFEST",
        "### REQUIREMENTS",
        "### BDD SCENARIOS",
        "### DESIGN TOKENS",
        "### COMPONENT SPECS",
        "### NAVIGATION MANDATE",
        "### MOCKUP REFERENCES",
    ]

    if phase >= 4:
        filtered_lines = []
        skip_section = False
        for line in brief.split("\n"):
            # Check if this line starts a build-phase section
            line_upper = line.strip().upper()
            if any(line_upper.startswith(header.upper()) for header in _BUILD_PHASE_HEADERS):
                skip_section = True
                continue

            # Stop skipping when we hit a new section header
            if skip_section and line.strip().startswith("###"):
                skip_section = False

            if not skip_section:
                filtered_lines.append(line)

        brief = "\n".join(filtered_lines)

    # Phase 5+: Add surgical-mode header
    if is_verification_or_later(phase):
        surgical_header = (
            "⚠️ **SURGICAL FIX MODE** (Phase 5+) — "
            "Only fix the specific issue. Do NOT expand scope.\n\n"
        )
        brief = surgical_header + brief

    return brief

PROFILE_CONTEXT_CONFIG: dict[str, dict[str, Any]] = {
    "architect": {
        # Tier 1: Project Context
        "manifest": "full",
        "design_tokens": False,
        "route_map": True,
        "requirements": "all",
        "api_docs": "reference",
        "integration": True,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": False,
        "mockup_refs": False,
        "codebase_state": False,
        "bdd_scenarios": False,
        "research_docs_inline": False,
        # Tier 2: Runtime State
        "error_relay": False,
        "gate_failures": True,
        "verification_findings": False,
        "fidelity_violations": False,
        "tdd_mandate": False,
        "acceptance_criteria": False,
        # Tier 3: Operational
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": True,
        "attachments": True,
    },
    "code": {
        # Tier 1
        "manifest": "full",
        "design_tokens": True,
        "route_map": True,
        "requirements": "assigned",
        "api_docs": "critical",
        "integration": True,
        "navigation_mandate": True,
        "design_system_mandate": True,
        "component_specs": True,
        "mockup_refs": True,
        "codebase_state": True,
        "bdd_scenarios": True,
        "research_docs_inline": True,
        # Tier 2
        "error_relay": True,
        "gate_failures": True,
        "verification_findings": True,
        "fidelity_violations": True,
        "tdd_mandate": True,
        "acceptance_criteria": True,
        "tsc_mandate": True,
        "scaffold_minimal": True,
        "tdd_stub_wiring": True,
        "domain_context": True,  # F-9 (ITR-25): user's business narrative
        "framework_pitfalls": True,  # F-3 (ITR-25): framework anti-pattern warnings
        "database_seed_mandate": True,  # Fix-9: seed data mandate for ORM projects
        "dedup_advisory": True,  # SS-5: cross-file dedup advisory for data writes
        "infrastructure_fast_pass": True,  # ITR-44: skip infra re-verification when already green
        "integration_wiring": True,  # WP-5: import mandates from dependency graph
        "dependency_context": True,  # WP-5: callers/consumers from dependency graph
        "phase_prerequisites": True,  # RCA-475 Fix 6: predecessor phase status
        # Tier 3
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": True,
        "attachments": True,
        "dev_server_url": True,  # RCA-FIX: inject actual dev server URL
    },
    "frontend": {
        # Tier 1
        "manifest": "visual",
        "design_tokens": True,
        "route_map": False,
        "requirements": False,
        "api_docs": False,
        "integration": False,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": False,
        "mockup_refs": False,
        "codebase_state": False,
        "bdd_scenarios": False,
        "research_docs_inline": False,
        # Tier 2
        "error_relay": False,
        "gate_failures": True,
        "verification_findings": False,
        "fidelity_violations": False,
        "tdd_mandate": False,
        "acceptance_criteria": False,
        "domain_context": True,  # F-9 (ITR-25): user's business narrative
        "framework_pitfalls": True,  # F-3 (ITR-25): framework anti-pattern warnings
        "infrastructure_fast_pass": True,  # ITR-44: skip infra re-verification when already green
        # Tier 3
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": True,
        "attachments": True,
    },
    "frontend_designer": {
        # Same as frontend
        "manifest": "visual",
        "design_tokens": True,
        "route_map": False,
        "requirements": False,
        "api_docs": False,
        "integration": False,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": False,
        "mockup_refs": False,
        "codebase_state": False,
        "bdd_scenarios": False,
        "research_docs_inline": False,
        "error_relay": False,
        "gate_failures": True,
        "verification_findings": False,
        "fidelity_violations": False,
        "tdd_mandate": False,
        "acceptance_criteria": False,
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": True,
        "attachments": True,
        "domain_context": True,  # G-11: designer needs business narrative for contextual designs
    },
    "e2e": {
        # Tier 1
        "manifest": "full",
        "design_tokens": True,
        "route_map": True,
        "requirements": "all",
        "api_docs": "reference",  # F-7 (ITR-28): was False — E2E needs API contracts to test POST/PUT endpoints
        "integration": True,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": False,
        "mockup_refs": True,
        "codebase_state": False,
        "bdd_scenarios": True,
        "research_docs_inline": False,
        # Tier 2
        "error_relay": True,
        "gate_failures": True,
        "verification_findings": False,
        "fidelity_violations": True,
        "tdd_mandate": False,
        "acceptance_criteria": True,
        # Tier 3
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": False,
        "attachments": True,
        "dev_server_url": True,  # RCA-FIX: inject actual dev server URL
    },
    "researcher": {
        # Tier 1: Almost nothing
        "manifest": False,
        "design_tokens": False,
        "route_map": False,
        "requirements": "assigned",
        "api_docs": False,
        "integration": False,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": False,
        "mockup_refs": False,
        "codebase_state": False,
        "bdd_scenarios": False,
        "research_docs_inline": False,
        # Tier 2: Nothing
        "error_relay": False,
        "gate_failures": True,
        "verification_findings": False,
        "fidelity_violations": False,
        "tdd_mandate": False,
        "acceptance_criteria": False,
        # Tier 3: Minimal
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": False,
        "attachments": True,
    },
    "debug": {
        # Tier 1
        "manifest": "full",
        "design_tokens": False,
        "route_map": True,
        "requirements": "all",
        "api_docs": "reference",  # G-10: debug agent needs API route table for API issue diagnosis
        "integration": True,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": False,
        "mockup_refs": False,
        "codebase_state": True,
        "bdd_scenarios": False,
        "research_docs_inline": False,
        # Tier 2: EVERYTHING
        "error_relay": True,
        "gate_failures": True,
        "verification_findings": True,
        "fidelity_violations": True,
        "tdd_mandate": False,
        "acceptance_criteria": False,
        # Tier 3
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": False,
        "attachments": True,
    },
    "review": {
        # Tier 1
        "manifest": "full",
        "design_tokens": True,
        "route_map": True,
        "requirements": "all",
        "api_docs": False,
        "integration": True,
        "navigation_mandate": False,
        "design_system_mandate": False,
        "component_specs": True,
        "mockup_refs": True,
        "codebase_state": True,
        "bdd_scenarios": True,
        "research_docs_inline": False,
        # Tier 2
        "error_relay": False,
        "gate_failures": True,
        "verification_findings": True,
        "fidelity_violations": True,
        "tdd_mandate": True,  # G-14: reviewer needs TDD requirements to assess test quality
        "acceptance_criteria": True,
        # Tier 3
        "available_tools": True,
        "turn_budget": True,
        "skill_reference": False,
        "attachments": True,
    },
}

_DEFAULT_CONFIG: dict[str, Any] = {
    "manifest": False,
    "design_tokens": False,
    "route_map": False,
    "requirements": False,
    "api_docs": False,
    "integration": False,
    "navigation_mandate": False,
    "design_system_mandate": False,
    "component_specs": False,
    "mockup_refs": False,
    "codebase_state": False,
    "bdd_scenarios": False,
    "research_docs_inline": False,
    "error_relay": False,
    "gate_failures": False,
    "verification_findings": False,
    "fidelity_violations": False,
    "tdd_mandate": False,
    "acceptance_criteria": False,
    "tsc_mandate": False,
    "scaffold_minimal": False,
    "tdd_stub_wiring": False,
    "database_seed_mandate": False,
    "available_tools": True,
    "turn_budget": True,
    "skill_reference": False,
    "attachments": True,
}

_PHASE_RESTRICTED_PRESERVE_KEYS = frozenset({
    'error_relay',
    'verification_findings',
    'codebase_state',
    'route_map',
    'gate_failures',
    'fidelity_violations',
    # Tier 3 operational keys — always keep
    'available_tools',
    'turn_budget',
    'skill_reference',
    'attachments',
    'dev_server_url',
})

_PHASE_RESTRICTED_OVERRIDES: dict[str, Any] = {
    'manifest': False,
    'requirements': 'assigned',
    'bdd_scenarios': False,
    'mockup_refs': False,
    'design_tokens': False,
    'component_specs': False,
    'navigation_mandate': False,
    'design_system_mandate': False,
    'research_docs_inline': False,
    'tdd_mandate': False,
    'acceptance_criteria': False,
    'tsc_mandate': False,
    'scaffold_minimal': False,
    'tdd_stub_wiring': False,
    'domain_context': False,
    'framework_pitfalls': False,
    'database_seed_mandate': False,
    # RC-6 (RCA-ITR36): integration and api_docs gave fix agents
    # full "build these endpoints" context, causing scope explosion
    'integration': False,
    'api_docs': False,
}

_PROFILE_TOOLS: dict[str, list[str]] = {
    'code': [
        'code_execution_tool', 'write_to_file', 'replace_in_file', 'apply_diff',
        'read_file', 'list_dir', 'sequential_thinking',
        'secret_get', 'secret_set', 'frontend_kb', 'response', 'resolve_literals',
    ],
    'architect': [
        'sequential_thinking', 'read_file', 'list_dir', 'save_deliverable',
        'response', 'generate_guid', 'requirements',
    ],
    'researcher': [
        'search', 'browser', 'read_file', 'list_dir', 'response',
        'sequential_thinking',
    ],
    'frontend': [
        'sequential_thinking', 'read_file', 'list_dir', 'generate_image',
        'save_deliverable', 'response',
    ],
    'frontend_designer': [
        'sequential_thinking', 'read_file', 'list_dir', 'generate_image',
        'save_deliverable', 'response',
    ],
    'review': [
        'read_file', 'list_dir', 'sequential_thinking', 'response',
    ],
    'debug': [
        'code_execution_tool', 'read_file', 'list_dir', 'sequential_thinking',
        'response',
    ],
    'e2e': [
        'browser', 'read_file', 'response', 'sequential_thinking',
        'services_mgt', 'code_execution_tool', 'scrape_url',
    ],
    'multiagentdev': [
        'call_subordinate', 'call_subordinate_batch', 'sequential_thinking',
        'read_file', 'requirements', 'generate_guid', 'response',
        'maintain_memory_bank', 'save_deliverable', 'fan_out_subordinates',
    ],
}

from python.helpers.type_coherence import check_type_coherence

_AUTH_CATEGORIES = frozenset({"auth", "authentication", "authorization", "login", "signup", "registration"})

_AUTH_KEYWORDS = frozenset({"auth", "login", "password", "signup", "sign-up", "registration", "oauth", "jwt", "session"})

_FRAMEWORK_PITFALL_RULES: dict[str, list[dict[str, str]]] = {
    "nextjs": [
        {
            "warning": "NEVER use `export const dynamic = 'force-dynamic'` in 'use client' files",
            "pattern": "export const dynamic = 'force-dynamic'",
            "solution": (
                "Route segment config (dynamic, revalidate, runtime) belongs in SERVER "
                "components or route.ts files ONLY. In 'use client' files, use "
                "client-side data fetching (useEffect + fetch, or SWR/React Query) instead. "
                "If you need dynamic server rendering, split into a server component parent "
                "that passes data as props to the client component."
            ),
        },
        {
            "warning": "Do NOT mix 'use client' with server-only exports (generateMetadata, generateStaticParams)",
            "pattern": "'use client' + generateMetadata",
            "solution": (
                "Metadata generation is server-only. Keep generateMetadata and "
                "generateStaticParams in server components (no 'use client' directive). "
                "If you need client interactivity on the same page, extract the interactive "
                "part into a separate client component and import it."
            ),
        },
        {
            "warning": "Do NOT import server-only modules (fs, path, crypto) in 'use client' files",
            "pattern": "import fs from 'fs' in 'use client'",
            "solution": (
                "Server-only Node.js modules (fs, path, crypto, etc.) cannot be imported "
                "in client components. Move file/server operations to API routes "
                "(app/api/), server actions ('use server'), or server components. "
                "Use fetch() from client components to call your API routes."
            ),
        },
        {
            "warning": "Do NOT use `cookies()` or `headers()` in 'use client' files",
            "pattern": "cookies() in 'use client'",
            "solution": (
                "next/headers functions (cookies, headers) are server-only. Access them "
                "in server components or route handlers, then pass values as props to "
                "client components. For client-side cookies, use `document.cookie` or "
                "a library like `js-cookie`."
            ),
        },
        {
            "warning": "Avoid using `useRouter` from 'next/router' in App Router (use 'next/navigation')",
            "pattern": "import { useRouter } from 'next/router'",
            "solution": (
                "In Next.js App Router (app/ directory), import from 'next/navigation' "
                "instead of 'next/router'. The Pages Router API is incompatible: "
                "`import { useRouter, usePathname, useSearchParams } from 'next/navigation'`."
            ),
        },
    ],
    "nuxtjs": [
        {
            "warning": "Do NOT use `definePageMeta` in non-page components",
            "pattern": "definePageMeta in components/",
            "solution": (
                "definePageMeta is only valid inside pages/ directory files. "
                "For shared layout metadata, use useHead() composable or "
                "nuxt.config.ts app.head configuration."
            ),
        },
    ],
    "remix": [
        {
            "warning": "Do NOT import server-only loaders/actions in client components directly",
            "pattern": "import { loader } from",
            "solution": (
                "In Remix, loaders and actions run on the server. Use useLoaderData() "
                "and useActionData() hooks in route components to access server data. "
                "Do not import loader/action functions directly in client utilities."
            ),
        },
    ],
    "vite": [
        {
            "warning": "Do NOT use process.env for client-side env vars (use import.meta.env)",
            "pattern": "process.env.VITE_",
            "solution": (
                "Vite exposes environment variables via import.meta.env, not process.env. "
                "Prefix client-side env vars with VITE_ and access as import.meta.env.VITE_*. "
                "Server-only secrets should not have the VITE_ prefix."
            ),
        },
    ],
    "sveltekit": [
        {
            "warning": "Do NOT use $env/static/private in client-side code",
            "pattern": "$env/static/private in +page.svelte",
            "solution": (
                "Private env vars ($env/static/private, $env/dynamic/private) are "
                "server-only. Use them in +page.server.ts, +server.ts, or hooks.server.ts. "
                "For client-accessible values, use $env/static/public."
            ),
        },
    ],
    "astro": [
        {
            "warning": "Do NOT use Astro.request in client:* components",
            "pattern": "Astro.request in client component",
            "solution": (
                "Astro.request is only available in .astro files during SSR. "
                "Client-hydrated components (client:load, client:visible) cannot "
                "access Astro.request. Pass data as props from the .astro parent."
            ),
        },
    ],
}

_MAX_DOMAIN_CONTEXT_CHARS = 800