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



@dataclass
class ProjectContext:
    """All project artifacts loaded once from disk."""

    manifest: Optional[dict] = None
    decomp_index: Optional[dict] = None
    architect_plan: Optional[dict] = None
    design_tokens: Optional[dict] = None
    requirements: Optional[dict] = None
    env_vars: dict = field(default_factory=dict)
    research_docs: list = field(default_factory=list)
    existing_files: list = field(default_factory=list)
    route_map: dict = field(default_factory=dict)

def _read_json(project_dir: str, filename: str) -> Optional[dict]:
    """Read a JSON file from project dir, return None on any error."""
    path = os.path.join(project_dir, filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, IOError, OSError):
        return None

def _read_env(project_dir: str) -> dict[str, str]:
    """Read .env.local and return key→value dict (values are existence markers)."""
    env_path = os.path.join(project_dir, ".env.local")
    if not os.path.isfile(env_path):
        return {}
    result = {}
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key = line.split("=", 1)[0].strip()
                    if key:
                        result[key] = "configured"
    except (IOError, OSError):
        pass
    return result

def _list_research_docs(project_dir: str) -> list[str]:
    """List research doc filenames in docs/."""
    docs_dir = os.path.join(project_dir, "docs")
    if not os.path.isdir(docs_dir):
        return []
    result = []
    try:
        for f in os.listdir(docs_dir):
            if f.lower().startswith("research") and f.endswith(".md"):
                result.append(f)
    except (IOError, OSError):
        pass
    return sorted(result)

def _extract_routes(project_dir: str) -> dict[str, str]:
    """Extract route map from architect plan + decomposition index."""
    routes: dict[str, str] = {}

    # From architect_plan.json — canonical docs/ path
    plan = _read_json(project_dir, PLANNING_PATHS["architect_plan"])
    if plan and isinstance(plan, dict):
        pages = plan.get("pages", [])
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    route = page.get("route", "")
                    name = page.get("name", "")
                    if route and route not in routes:
                        routes[route] = name
        # Also check route_map key
        rmap = plan.get("route_map", plan.get("routes", []))
        if isinstance(rmap, list):
            for entry in rmap:
                if isinstance(entry, dict):
                    route = entry.get("route", entry.get("path", ""))
                    name = entry.get("name", entry.get("label", ""))
                    if route and route not in routes:
                        routes[route] = name
        elif isinstance(rmap, dict):
            for route, name in rmap.items():
                if route not in routes:
                    routes[route] = str(name) if not isinstance(name, str) else name

    # From decomposition_index.json — canonical docs/ path
    decomp = _read_json(project_dir, PLANNING_PATHS["decomposition_index"])
    if decomp and isinstance(decomp, dict):
        phases = decomp.get("phases", [])
        if isinstance(phases, list):
            for phase in phases:
                if isinstance(phase, dict):
                    route = phase.get("route", "")
                    name = phase.get("name", phase.get("title", ""))
                    if route and route not in routes:
                        routes[route] = name

    return routes

def invalidate_project_context_cache() -> None:
    """Invalidate the project context LRU cache.

    #9 (P1): After build failures, the cached project context may contain
    stale data (old types, missing files). Call this after detecting a
    build failure so subsequent delegations get fresh project state.

    This is a public API for external modules (e.g., build_loop_detector,
    completion_gate) to trigger cache invalidation without reaching into
    private LRU internals.
    """
    _load_project_context.cache_clear()

@functools.lru_cache(maxsize=8)
def _load_project_context(project_dir: str) -> ProjectContext:
    """Load all project artifacts once, return structured context.

    Cached per project_dir. Call cache_clear() when project files change.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return ProjectContext()

    # WB-5 FIX: Materialize vault secrets → .env.local BEFORE reading
    try:
        from python.helpers.pre_delegation_env_bridge import ensure_env_before_delegation
        env_result = ensure_env_before_delegation(
            project_dir=project_dir,
            project_name=os.path.basename(project_dir),
        )
        if env_result and hasattr(env_result, 'written_keys') and env_result.written_keys:
            logger.info(f"[DELEGATION BRIEF] Env bridge: {len(env_result.written_keys)} keys written")
    except Exception as e:
        logger.debug(f"[DELEGATION BRIEF] Env bridge failed (non-fatal): {e}")

    return ProjectContext(
        manifest=_read_json(project_dir, PLANNING_PATHS["content_manifest"]),
        decomp_index=_read_json(project_dir, PLANNING_PATHS["decomposition_index"]),
        architect_plan=_read_json(project_dir, PLANNING_PATHS["architect_plan"]),
        design_tokens=_read_json(project_dir, PLANNING_PATHS["design_tokens"]),
        requirements=_read_json(project_dir, PLANNING_PATHS["requirements_ledger"]),
        env_vars=_read_env(project_dir),
        research_docs=_list_research_docs(project_dir),
        route_map=_extract_routes(project_dir),
    )