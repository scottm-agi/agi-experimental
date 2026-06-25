"""
Requirements Management Tool

Gives the orchestrator LLM direct access to the requirements ledger:
  - init: Bootstrap the ledger from extracted prompt requirements (Phase 0)
  - list: Show all tracked requirements with their status
  - coverage: Show coverage statistics (total/assigned/completed/unassigned)
  - suggest: Return unassigned requirement IDs ready for delegation
  - update: Add new requirements dynamically
  - mark_complete: Mark a requirement as completed
  - save_manifest: Persist content_manifest.json, decomposition_index.json, or requirements_ledger.json

This replaces all write_to_file usage for planning artifacts.
Multiagentdev should NEVER use write_to_file — all writes go through this tool.

Architecture: Thin tool wrapper around python.helpers.requirements_ledger.
"""
from __future__ import annotations

import glob
import json
import os
import logging
from typing import Any

from python.helpers.tool import Tool, Response
from python.helpers.planning_paths import get_path as _planning_path
from python.helpers.requirements_ledger import (
    get_coverage,
    get_unassigned_requirements,
    add_requirement,
    mark_requirement_complete,
    check_assignment_coverage,
    init_requirements,
    supplement_from_prompt,
    _ensure_ledger,
)

logger = logging.getLogger("agix.requirements_tool")

# Module-level import for project resolution — used by _ensure_active_project_dir
# and all action handlers. Imported here so it can be patched cleanly in tests.
from python.helpers import projects
from python.helpers.projects import get_decomp_index_path



def _ensure_active_project_dir(agent_data: dict, agent_context=None) -> str | None:
    """Resolve project_dir and persist it to agent_data["_active_project_dir"].

    GAP-1 FIX: All requirements.py action handlers resolve project_dir from
    agent_context but NEVER write it back to agent_data. This means downstream
    consumers in call_subordinate.py (build_delegation_package, BDD injection,
    env bridge, etc.) are gated by `if project_dir:` and get nothing because
    agent.data["_active_project_dir"] was never set.

    This centralized helper is called at the TOP of every handler to ensure
    the key is set as early as possible (Phase 0 onward).

    Behavior:
      - If _active_project_dir is already set (non-empty), returns existing value
        without overwriting (idempotent, prevents context drift).
      - If not set, resolves from agent_context and sets it.
      - Fail-open: returns None on any exception (non-fatal).

    Args:
        agent_data: The agent.data dict (mutated in-place).
        agent_context: The agent.context for project resolution (may be None).

    Returns:
        The resolved project_dir string, or None if unresolvable.
    """
    # Priority 1: Return existing value if already set
    existing = agent_data.get("_active_project_dir", "")
    if existing:
        return existing

    # Priority 2: Resolve from agent_context
    if agent_context is None:
        return None

    try:
        project_name = projects.get_context_project_name(agent_context)
        if not project_name:
            return None

        project_dir = projects.get_project_folder(project_name)
        if project_dir:
            # System 7 (ITR-44): Clear project-scoped state if project changed
            old_project = agent_data.get("_active_project_dir", "")
            if old_project and old_project != project_dir:
                from python.helpers.agent_data_keys import invalidate_project_scoped_keys
                invalidate_project_scoped_keys(agent_data, project_dir)
            else:
                agent_data["_active_project_dir"] = project_dir
            logger.info(
                f"[REQUIREMENTS TOOL] GAP-1 FIX: Set _active_project_dir="
                f"'{project_dir}' from agent_context (was missing)"
            )
            return project_dir
        return None
    except Exception as e:
        logger.debug(
            f"[REQUIREMENTS TOOL] _ensure_active_project_dir failed (non-fatal): {e}"
        )
        return None

_reconciler_warnings: list[str] = []

_MANDATORY_PHASES = {
    "0.5": {
        "title": "Research & Docs Pre-fetch",
        "agent": "researcher",
        "status": "pending",
        "wave": 1,
        "req_guids": [],
    },
    "2.3": {
        "title": "UI/UX Design (Mockups + Tokens)",
        "agent": "frontend",
        "status": "pending",
        "wave": 2,
        "req_guids": [],
    },
    # RCA-ITR355 RC-C: LLM drops validation phases when it rewrites decomposition.
    # These must be auto-injected to ensure the planning pipeline is complete.
    "2.5": {
        "title": "Validate Decomposition Index",
        "agent": "orchestrator",
        "status": "pending",
        "wave": 2,
        "req_guids": [],
    },
    "2.6": {
        "title": "Architect-Researcher Cross-Check",
        "agent": "researcher",
        "status": "pending",
        "wave": 2,
        "req_guids": [],
    },
    "2.7": {
        "title": "Skeleton Validation",
        "agent": "code",
        "status": "pending",
        "wave": 2,
        "req_guids": [],
    },
    # RCA-470 Fix 1: Mandatory Homepage Phase.
    # Root cause: LLM decomposition dropped the homepage phase, leaving
    # page.tsx as "Create Next App" scaffold default. The homepage is the
    # FIRST thing a user sees — it must always have a dedicated phase.
    "3.0": {
        "title": "Homepage / Landing Page",
        "agent": "code",
        "status": "pending",
        "wave": 3,
        "req_guids": [],
    },
}

_PHASE_ARTIFACT_MAP = {
    # Planning phases
    "0.5": ["docs/framework-research.md"],
    # Design phases
    "1": ["package.json"],
    "2": ["docs/architecture.md", "docs/dependency-graph.json"],
    "2.3": ["docs/design-tokens.json"],  # RCA-ITR355: Removed "deliverables/" — it's shared by ALL agents and causes false completion
    "2.4": ["src/app/globals.css", "tailwind.config.ts", "tailwind.config.js"],  # F-8: Wire Design Tokens into Config
    "2.5": ["docs/test-skeleton.json"],
    "2.7": ["docs/tdd/"],
    # Implementation phases — src/ files checked by implementation_completion_validator
    # (no artifact map needed — the validator uses file delta, not specific artifacts)
    # Integration phases
    "4": [".next/", "build/", "dist/", "out/"],  # Build output directories
    # Verification phases
    "5": ["docs/verification-matrix.json"],
    "5.1": ["docs/verification-results.json"],
    # Delivery phases
    "6": ["docs/iteration-report.md"],
}
