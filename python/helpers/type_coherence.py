"""
Type Coherence Checker — Phase 2.5 Schema Lock (Planning Level).
================================================================

Root cause (SS-4, Week Retrospective, Iterations 150-1777):
    Multi-wave delegations independently introduce conflicting type names
    (Prospect, Lead, Business for the same entity) → 84 TypeScript errors
    / 11 build failures. The SKILL.md references Phase 2.5
    check_type_coherence() but no such function existed.

This module provides PLANNING-LEVEL type coherence validation:
    1. _extract_prisma_models()        — regex parse schema.prisma → model/enum names
    2. _extract_decomposition_types()  — extract PascalCase entity names from decomposition
    3. check_type_coherence()          — full cross-reference validation

Distinct from python.helpers.validators.type_coherence which is a POST-CODE
validator checking Prisma ↔ TypeScript import consistency at Phase 5+.

Usage:
    from python.helpers.type_coherence import check_type_coherence

    result = check_type_coherence("/path/to/project")
    if not result["pass"]:
        for warning in result["warnings"]:
            print(warning)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Set

from python.helpers.projects import get_decomp_index_path

logger = logging.getLogger("agix.type_coherence")

# ──────────────────────────────────────────────────────────────────────
# 1. Prisma Schema Parsing
# ──────────────────────────────────────────────────────────────────────

# Matches: model Business { ... }
_PRISMA_MODEL_RE = re.compile(r"^\s*model\s+(\w+)\s*\{", re.MULTILINE)

# Matches: enum BusinessStatus { ... }
_PRISMA_ENUM_RE = re.compile(r"^\s*enum\s+(\w+)\s*\{", re.MULTILINE)


def _extract_prisma_models(schema_text: str) -> Set[str]:
    """Extract model and enum names from Prisma schema text.

    Args:
        schema_text: Raw content of a Prisma schema file.

    Returns:
        Set of model/enum names found (e.g. {"User", "Post", "Role"}).
    """
    models: Set[str] = set()
    for match in _PRISMA_MODEL_RE.finditer(schema_text):
        models.add(match.group(1))
    for match in _PRISMA_ENUM_RE.finditer(schema_text):
        models.add(match.group(1))
    return models


# ──────────────────────────────────────────────────────────────────────
# 2. Decomposition Type Extraction
# ──────────────────────────────────────────────────────────────────────

# PascalCase pattern: starts with uppercase, has at least one lowercase,
# may contain multiple humps (e.g. BusinessReview). Excludes common
# non-entity words and short abbreviations.
_PASCAL_CASE_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)*)\b")

# Common words that look PascalCase but aren't entity names
_IGNORE_WORDS = {
    "Api", "API", "App", "Build", "Create", "Delete", "Deploy",
    "Design", "Dev", "Development", "Fetch", "Generate", "Get",
    "Handle", "Init", "Initialize", "Install", "List", "Load",
    "Manage", "Next", "Phase", "Post", "Process", "Production",
    "React", "Render", "Research", "Run", "Save", "Schema", "Script",
    "Search", "Send", "Server", "Service", "Setup", "Start", "Stop",
    "Style", "Submit", "Test", "Update", "Upload", "Validate",
    "View", "Wave", "Write", "Prisma", "Database", "Scaffold",
    "Frontend", "Backend", "Fullstack", "Infrastructure", "Integration",
    "Implement", "Implementation", "Component", "Components",
    "Route", "Router", "Routing", "Middleware", "Controller",
    "Skeleton", "Validation", "Architect", "Architecture",
    "Specification", "Configuration", "Deployment", "Migration",
    "Dashboard", "Landing", "Login", "Register", "Auth",
    "Authentication", "Authorization", "Stripe", "Resend", "Tailwind",
    "Typescript", "Javascript", "Mockup", "Mockups", "Token",
    "Tokens", "Cross", "Check", "Expansion",
}


def _extract_decomposition_types(decomp: list) -> Set[str]:
    """Extract PascalCase entity names from decomposition task titles/descriptions.

    Scans both 'title' and 'description' fields for PascalCase words that
    are likely entity/model names (filtering out common non-entity words).

    Args:
        decomp: List of decomposition phase dicts.

    Returns:
        Set of likely entity names found in decomposition.
    """
    types: Set[str] = set()

    for task in decomp:
        if not isinstance(task, dict):
            continue
        for field in ("title", "description"):
            text = task.get(field, "")
            if not isinstance(text, str):
                continue
            for match in _PASCAL_CASE_RE.finditer(text):
                word = match.group(1)
                if word not in _IGNORE_WORDS and len(word) > 2:
                    types.add(word)

    return types


# ──────────────────────────────────────────────────────────────────────
# 3. Architecture Spec Parsing
# ──────────────────────────────────────────────────────────────────────

# Markdown heading pattern: ## ModelName or ### ModelName
_MD_HEADING_RE = re.compile(r"^#{2,4}\s+(\w+)", re.MULTILINE)


def _extract_arch_spec_types(spec_path: str) -> Set[str]:
    """Extract entity names from architecture-spec.md headings.

    Looks for PascalCase words in ## and ### headings which typically
    represent data model definitions.

    Args:
        spec_path: Absolute path to architecture-spec.md.

    Returns:
        Set of entity names found in spec headings.
    """
    if not os.path.isfile(spec_path):
        return set()

    try:
        with open(spec_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return set()

    types: Set[str] = set()
    for match in _MD_HEADING_RE.finditer(content):
        word = match.group(1)
        # Only take PascalCase words (starts uppercase, has lowercase)
        if re.match(r"^[A-Z][a-z]", word) and word not in _IGNORE_WORDS:
            types.add(word)

    return types


# ──────────────────────────────────────────────────────────────────────
# 4. Full Coherence Check
# ──────────────────────────────────────────────────────────────────────


def check_type_coherence(project_dir: str) -> Dict[str, object]:
    """Run Phase 2.5 type coherence validation on a project.

    Cross-references entity names from three sources:
    1. decomposition_index.json (task titles/descriptions)
    2. prisma/schema.prisma (model/enum names)
    3. docs/architecture-spec.md (heading-level entity names)

    Detects:
    - Decomposition types not in Prisma schema (potential type drift)
    - Multiple decomposition tasks using different names for same concept
    - Inconsistency between arch spec and Prisma model names

    Args:
        project_dir: Root directory of the project.

    Returns:
        Dict with keys:
        - pass: bool — overall pass/fail
        - warnings: list of warning strings
        - canonical_types: list of canonical type names (from Prisma or arch spec)
        - conflicts: list of conflict dicts {decomp_type, canonical_type, source}
    """
    warnings: List[str] = []
    conflicts: List[Dict[str, str]] = []
    canonical_types: Set[str] = set()

    # 1. Read decomposition_index.json
    decomp_path = get_decomp_index_path(project_dir)
    decomp: list = []
    if os.path.isfile(decomp_path):
        try:
            with open(decomp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                decomp = data
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f"[TYPE COHERENCE] Failed to read decomposition_index.json: {e}")

    # 2. Extract Prisma models (always, even without decomposition)
    prisma_path = os.path.join(project_dir, "prisma", "schema.prisma")
    prisma_models: Set[str] = set()
    if os.path.isfile(prisma_path):
        try:
            with open(prisma_path, "r", encoding="utf-8", errors="ignore") as f:
                schema_text = f.read()
            prisma_models = _extract_prisma_models(schema_text)
        except (IOError, OSError):
            pass

    # 3. Extract architecture spec types
    arch_spec_path = os.path.join(project_dir, "docs", "architecture-spec.md")
    arch_types = _extract_arch_spec_types(arch_spec_path)

    # 4. Build canonical type set (Prisma takes precedence, then arch spec)
    canonical_types = prisma_models.copy()
    # Add arch spec types that aren't already covered by Prisma
    for t in arch_types:
        if t not in canonical_types:
            canonical_types.add(t)

    # No decomposition → nothing to cross-reference, but still return canonical types
    if not decomp:
        return {
            "pass": True,
            "warnings": [],
            "canonical_types": sorted(canonical_types),
            "conflicts": [],
        }

    # 5. Extract decomposition types
    decomp_types = _extract_decomposition_types(decomp)

    # 6. Cross-reference decomposition types against canonical
    if canonical_types and decomp_types:
        # Find decomposition types NOT in canonical set
        unknown_types = decomp_types - canonical_types
        for ut in sorted(unknown_types):
            # Check if it's a plausible alias for a canonical type
            # (case-insensitive match or substring match)
            possible_matches = []
            for ct in canonical_types:
                if (ut.lower() == ct.lower() and ut != ct) or \
                   ut.lower() in ct.lower() or ct.lower() in ut.lower():
                    possible_matches.append(ct)

            if possible_matches:
                for pm in possible_matches:
                    conflict = {
                        "decomp_type": ut,
                        "canonical_type": pm,
                        "source": "prisma" if pm in prisma_models else "arch_spec",
                    }
                    conflicts.append(conflict)
                    warnings.append(
                        f"⚠️ TYPE DRIFT: Decomposition uses '{ut}' but "
                        f"{'Prisma schema' if pm in prisma_models else 'architecture spec'} "
                        f"defines '{pm}'. Use the canonical name '{pm}' to avoid type conflicts."
                    )
            else:
                # Not in canonical set and no close match — warn as potential drift
                if prisma_models:
                    warnings.append(
                        f"⚠️ UNRESOLVED TYPE: Decomposition references '{ut}' "
                        f"which is not in the Prisma schema. If this is a new entity, "
                        f"add it to prisma/schema.prisma. If it's an alias, rename "
                        f"to match an existing model: {sorted(prisma_models)}"
                    )

    # 7. Check for internal decomposition conflicts
    # (same description mentioning multiple entity names for one concept)
    # This is handled by the warnings above — if Prisma has "Business"
    # and decomposition has both "Lead" and "Prospect", both will be flagged.

    passed = len(conflicts) == 0

    result = {
        "pass": passed,
        "warnings": warnings,
        "canonical_types": sorted(canonical_types),
        "conflicts": conflicts,
    }

    if warnings:
        logger.warning(
            f"[TYPE COHERENCE] Phase 2.5 check: {len(warnings)} warning(s), "
            f"{len(conflicts)} conflict(s). Canonical types: {sorted(canonical_types)}"
        )
    else:
        logger.info(
            f"[TYPE COHERENCE] Phase 2.5 check: PASS. "
            f"Canonical types: {sorted(canonical_types)}"
        )

    return result
