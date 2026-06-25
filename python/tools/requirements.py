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



from python.tools.requirements_config import _ensure_active_project_dir, _MANDATORY_PHASES, _PHASE_ARTIFACT_MAP, _reconciler_warnings
from python.tools.requirements_manifest import _normalize_manifest_schema, _validate_manifest_model_name, _enrich_tech_stack_from_research, _normalize_seq, _seq_less_than
from python.tools.requirements_sync import _validate_mandatory_phases, _reconcile_mandatory_phase_status, detect_artifact_gaps, _sync_decomp_assignments_to_ledger, _sync_decomposition_plan_to_agent_data, _reconcile_decomp_statuses, _preserve_ledger_progress, _filter_deferred_decomposition_phases
from python.tools.requirements_actions import _handle_list, _handle_coverage, _handle_check_coverage, _handle_suggest, _handle_init, _handle_save_bdd_scenarios, _handle_save_manifest, _handle_update, _handle_mark_complete, _handle_set_iteration_budget, _handle_complete_phase, record_phase_dispatched, _sweep_stale_assignments

# ─── Historical notes (from pre-modularization) ────────────────────────
# The implementation code has been extracted to sub-modules (see imports
# at lines 48-51). The following are architectural decisions preserved
# for audit trail:
#
# ITR-22: _reconciler_warnings list → moved to requirements_config.py
# ITR-18: _MANDATORY_PHASES dict → moved to requirements_config.py
# F-2:    _PHASE_ARTIFACT_MAP → moved to requirements_config.py
# RCA-ITR54: _guard_implementation_phase_inflation() DELETED (false positive)
# SS-7 (ITR-344): _auto_promote_implementation_phases() DELETED (global file count)
# See: agix-devdocs/docs/rca/ for full RCA history.


class RequirementsTool(Tool):
    """Query, update, and manage the requirements ledger during orchestration.

    Actions:
      - init: Bootstrap ledger from extracted requirements (Phase 0)
      - list: Show all requirements with status
      - coverage: Coverage statistics
      - check_coverage: Verify every requirement has a decomposition assignment (Phase 2.5 gate)
      - suggest: Unassigned requirement IDs for delegation
      - update: Add new requirements dynamically
      - mark_complete: Mark a requirement as completed (auto-persists to disk)
      - complete_phase: Mark a decomposition phase as completed (RCA-345)
      - save_manifest: Save planning artifacts (content_manifest.json, etc.)
    """
    def _extract_original_prompt(self) -> str:
        """Extract the original user prompt from agent history.

        RCA-ITR1 Fix: Uses agent.last_raw_user_message as the primary source
        (always set by hist_add_user_message), falling back to history parsing
        which may fail due to message format mismatches.
        """
        try:
            # Primary: use the raw user message stored by agent_history
            raw = getattr(self.agent, 'last_raw_user_message', None)
            if raw and isinstance(raw, str) and len(raw) > 50:
                return raw

            # Secondary: check agent.data for stored prompt (_03_prompt_capture)
            stored = self.agent.data.get('_original_user_prompt', '')
            if stored and isinstance(stored, str) and len(stored) > 50:
                return stored

            # RCA-ITR4 FIX-3: Fallback to _raw_user_prompt (_05_raw_prompt_capture)
            # _05 runs at user_message_ui and always captures successfully, while
            # _03 (message_loop_start) often fails because last_raw_user_message
            # is None on first loop. This ensures prompt extraction always works.
            raw_stored = self.agent.data.get('_raw_user_prompt', '')
            if raw_stored and isinstance(raw_stored, str) and len(raw_stored) > 50:
                return raw_stored

            # Tertiary: walk history topics
            history = self.agent.history
            if not history:
                return ""

            # history is an object with .topics list or a dict with 'topics' key
            topics = []
            if hasattr(history, 'topics'):
                topics = history.topics
            elif isinstance(history, dict):
                topics = history.get('topics', [])

            for topic in topics:
                msgs = topic.get('messages', []) if isinstance(topic, dict) else []
                for msg in msgs:
                    if msg.get('ai', False):
                        continue
                    content = msg.get('content', '')
                    # Handle dict format: {"user_message": "..."}
                    if isinstance(content, dict):
                        text = content.get('user_message', '')
                        if isinstance(text, str) and len(text) > 50:
                            return text
                    # Handle string format
                    elif isinstance(content, str) and len(content) > 50:
                        return content
                    # Handle list format: [{"type": "text", "text": "..."}]
                    elif isinstance(content, list):
                        parts = [p.get("text", "") for p in content
                                 if isinstance(p, dict) and p.get("type") == "text"]
                        text = " ".join(parts)
                        if len(text) > 50:
                            return text
        except Exception as e:
            logger.debug(f"[REQUIREMENTS TOOL] Could not extract prompt: {e}")
        return ""

    async def execute(self, **kwargs) -> Response:
        action = self.args.get("action", "list").lower().strip()

        # Actions that take extra args
        crud_handlers = {
            "init": _handle_init,
            "update": _handle_update,
            "mark_complete": _handle_mark_complete,
            "complete_phase": _handle_complete_phase,
            "save_manifest": _handle_save_manifest,
            "save_bdd_scenarios": _handle_save_bdd_scenarios,
            # RC-E: check_coverage needs agent_context for auto-sync from decomp on disk
            "check_coverage": _handle_check_coverage,
            # RCA-362 Layer 1: Smart dynamic budget
            "set_iteration_budget": _handle_set_iteration_budget,
        }

        # Read-only actions
        readonly_handlers = {
            "list": _handle_list,
            "coverage": _handle_coverage,
            "suggest": _handle_suggest,
        }

        # ── Action-level profile enforcement ──────────────────────────────
        # Canonical ownership (multiagentdev prompt lines 580-582):
        #   - multiagentdev: full CRUD (init, update, mark_complete, etc.)
        #   - architect: save_bdd_scenarios (BDD authoring) + read-only
        #   - all agents: read-only (list, coverage, suggest)
        #
        # Profiles with only `requirements_read` category (no `orchestration`
        # or `design_spec`) are restricted to read-only actions.
        if action in crud_handlers and action not in readonly_handlers:
            profile = getattr(self.agent.config, "profile", "default")
            has_orchestration = False
            has_design_spec = False
            try:
                from python.helpers.tool_selector import ToolSelector
                selector = ToolSelector.get_instance()
                ontology = selector._ontology
                profile_cats = ontology.get("profiles", {}).get(profile, [])
                has_orchestration = "orchestration" in profile_cats
                has_design_spec = "design_spec" in profile_cats
            except Exception:
                pass  # Graceful degradation — don't crash requirements tool

            if action == "save_bdd_scenarios":
                # BDD authoring: architect (design_spec) + orchestrators
                if not has_orchestration and not has_design_spec:
                    return Response(
                        message=(
                            f"🚫 **ACTION BLOCKED** — `save_bdd_scenarios` is restricted to "
                            f"the `architect` or `multiagentdev` profiles.\n\n"
                            f"**Your profile:** `{profile}` (read-only requirements access)\n"
                            f"**What to do:** Read BDD scenarios from `docs/bdd-scenarios.md` "
                            f"instead. The architect creates BDD scenarios in Phase 2; your "
                            f"job is to implement TDD tests from them.\n\n"
                            f"Available actions for your profile: `list`, `coverage`, `suggest`"
                        ),
                        break_loop=False,
                    )
            elif not has_orchestration:
                # All other CRUD actions: orchestrators only
                return Response(
                    message=(
                        f"🚫 **ACTION BLOCKED** — `{action}` is restricted to orchestrator "
                        f"profiles (`multiagentdev`).\n\n"
                        f"**Your profile:** `{profile}` (read-only requirements access)\n"
                        f"**Available actions:** `list`, `coverage`, `suggest`\n\n"
                        f"Use `requirements(action='list')` to read the current requirements "
                        f"ledger, or `requirements(action='coverage')` to check test coverage."
                    ),
                    break_loop=False,
                )

        if action in crud_handlers:
            if action == "init":
                # Extract original prompt from agent history for auto-supplement
                original_prompt = self._extract_original_prompt()
                result = crud_handlers[action](
                    self.agent.data, self.args,
                    agent_context=self.agent.context,
                    original_prompt=original_prompt,
                )
            elif action == "save_manifest":
                result = crud_handlers[action](
                    self.agent.data, self.args, agent_context=self.agent.context
                )
            else:
                result = crud_handlers[action](
                    self.agent.data, self.args, agent_context=self.agent.context
                )
            return Response(message=result, break_loop=False)

        handler = readonly_handlers.get(action)
        if not handler:
            valid = ", ".join(list(readonly_handlers.keys()) + list(crud_handlers.keys()))
            return Response(
                message=f"Unknown action '{action}'. Valid actions: {valid}",
                break_loop=False,
            )

        result = handler(self.agent.data)
        return Response(message=result, break_loop=False)

