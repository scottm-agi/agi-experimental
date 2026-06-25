"""
Requirements Stage & Phase Lifecycle Helpers

ADR-086: Stage-keyed status tracking for the BDD → TDD → Code pipeline.
ADR-089: Centralized phase completion validation.

Extracted from requirements_ledger.py during P4 modularization.
All functions maintain identical signatures and behavior.
"""

import json
import logging
import os

from python.helpers.status_constants import (
    VALID_STAGES,
    STAGE_DONE_STATUSES,
    STAGE_STATUS_PRIORITY,
)

logger = logging.getLogger("agix.requirements_ledger")


# ─── ADR-086: Stage-Keyed Status Helpers ─────────────────────────────────
#
# The PDV pipeline has 3 stages: BDD → TDD → Code.
# Each requirement tracks independent status per stage via stage_status dict.
# The top-level `status` field is a backward-compatible computed property
# equal to the MIN priority stage status.


def compute_overall_status(stage_status: dict) -> str:
    """Compute overall status from stage statuses.

    Returns the status with the MINIMUM priority across all stages.
    This means: if ANY stage is pending, overall is pending.

    Rules:
    - If ANY stage is "pending" → overall is "pending"
    - If ANY stage is "assigned" → overall is "assigned"
    - If ALL stages are "completed" or "verified" → overall is "completed"
    - Special: "delegation_returned" = code stage awaiting proof
    """
    stages = stage_status or {}
    if not stages:
        return "pending"

    min_priority = min(
        STAGE_STATUS_PRIORITY.get(s, 0) for s in stages.values()
    )
    REVERSE = {
        0: "pending",
        1: "assigned",
        2: "delegation_returned",
        2.5: "partial",            # partial/failed both have priority 2.5
        3: "completed",
        4: "verified",
    }
    result = REVERSE.get(min_priority, "pending")
    # Post-min disambiguation: if any stage is literally "failed",
    # the overall status is "failed" (not "partial"). Both share
    # priority 2.5 but failed is semantically stronger.
    if result == "partial" and any(s == "failed" for s in stages.values()):
        return "failed"
    return result


def get_stage_status(req: dict, stage: str) -> str:
    """Get status of a specific stage for a requirement.

    Args:
        req: Requirement dict.
        stage: Stage name ("bdd", "tdd", or "code").

    Returns:
        The stage's status string, defaulting to "pending".
    """
    ensure_stage_status(req)
    return req.get("stage_status", {}).get(stage, "pending")


def _get_or_create_stage_sm(req: dict, stage: str) -> "RequirementStageSM":
    """Get or create a RequirementStageSM for a req+stage combo.

    RCA-475: SM instances live in req["_stage_sms"][stage].
    On first access the SM is seeded with the current stage status.

    RCA-479 Fix: Handles corrupted SM entries from JSON round-trip.
    """
    from python.helpers.state_machines.requirement_stage_sm import RequirementStageSM

    sms = req.setdefault("_stage_sms", {})
    existing = sms.get(stage)
    if not isinstance(existing, RequirementStageSM):
        current = req.get("stage_status", {}).get(stage, "pending")
        sms[stage] = RequirementStageSM(
            status=current,
            entity_id=f"{req.get('id', '?')}.{stage}",
        )
    return sms[stage]


def set_stage_status(req: dict, stage: str, status: str) -> None:
    """Set status of a specific stage and recompute overall status.

    This is the ONLY correct way to update stage status. Never write
    req["status"] directly for stage changes — use this function.

    RCA-475: Also validates via RequirementStageSM (wrap, not replace).
    Invalid transitions log WARNING but NEVER block (migration mode).

    Args:
        req: Requirement dict (mutated in place).
        stage: Stage name ("bdd", "tdd", or "code").
        status: New status value.

    Raises:
        ValueError: If stage is not in VALID_STAGES.
    """
    if stage not in VALID_STAGES:
        raise ValueError(
            f"Invalid stage: {stage}. Must be one of {VALID_STAGES}"
        )
    if "stage_status" not in req:
        req["stage_status"] = {
            "bdd": "pending",
            "tdd": "pending",
            "code": "pending",
        }

    # ── RCA-475: SM validation (wrap — warn-only during migration) ──
    # Create/get SM BEFORE the assignment so it seeds with the OLD status.
    sm = _get_or_create_stage_sm(req, stage)

    req["stage_status"][stage] = status
    req["status"] = compute_overall_status(req["stage_status"])

    # Skip if SM already at target (e.g. re-setting "pending" on a new req)
    if sm.status == status:
        return
    ok, msg = sm.transition(
        status,
        reason=f"set_stage_status({stage}, {status})",
        source="requirements_stage.py",
    )
    if not ok:
        logger.warning(f"[STAGE SM] {msg} — status set anyway (migration mode)")
        # Force-set SM to match actual state
        sm.transition(
            status,
            reason=f"force-sync: {msg}",
            source="requirements_stage.py",
            force=True,
        )



def is_stage_complete(req: dict, stage: str) -> bool:
    """Check if a specific stage is done.

    Args:
        req: Requirement dict.
        stage: Stage name ("bdd", "tdd", or "code").

    Returns:
        True if the stage's status is in STAGE_DONE_STATUSES.
    """
    return get_stage_status(req, stage) in STAGE_DONE_STATUSES


# ─── ADR-089: Centralized Phase Completion ────────────────────────────────
#
# Phase category → required stages that must be complete.
# Categories are inferred from phase TITLE (not number) via infer_phase_category().
# This ensures the system is categorical, not numeric.

CATEGORY_REQUIRED_STAGES: dict = {
    "planning":       [],                   # No stage check
    "research":       [],                   # No stage check
    "design":         ["bdd"],              # BDD scenarios produced
    "implementation": ["tdd", "code"],      # Tests + real code
    "verification":   ["code"],             # Code at least completed
    "deployment":     ["code"],             # Can't deploy without code
    "unknown":        [],                   # Graceful fallback
}


def _get_requirements_for_phase(phase: dict, all_requirements: list) -> list:
    """Get requirements assigned to a phase by req_guids.

    Args:
        phase: Phase dict with optional 'req_guids' key.
        all_requirements: List of all requirement dicts.

    Returns:
        Filtered list of requirement dicts matching this phase's req_guids.
    """
    guids = set(phase.get("req_guids", []))
    if not guids:
        return []
    return [
        r for r in all_requirements
        if r.get("id", r.get("req_id", "")) in guids
    ]


def set_phase_completed(
    phase: dict,
    requirements: list,
    project_dir: str,
    force: bool = False,
) -> tuple:
    """Validate and set phase completion status (ADR-089).

    Centralized entry point for ALL phase completion. Validates that
    requirements assigned to this phase have their pipeline stages done
    based on the phase's CATEGORY (inferred from title, not number).

    Args:
        phase: Phase dict from decomposition_index.json.
        requirements: List of requirement dicts assigned to this phase.
        project_dir: Project directory path.
        force: If True, bypass validation (deadlock escape hatch).

    Returns:
        Tuple of (allowed: bool, reason: str).
    """
    phase_title = phase.get("title", phase.get("name", ""))
    phase_seq = phase.get("seq", "?")

    if force:
        phase["status"] = "completed"
        logger.warning(
            f"[ADR-089] Phase {phase_seq} force-completed (escape hatch). "
            f"Title: {phase_title}"
        )
        return (True, f"Force-completed (escape hatch)")

    category = infer_phase_category(phase_title)
    required_stages = CATEGORY_REQUIRED_STAGES.get(category, [])

    if not required_stages:
        # No stage validation needed for this category
        phase["status"] = "completed"
        return (True, f"Category '{category}' requires no stage checks")

    if not requirements:
        # No requirements assigned — allow completion
        phase["status"] = "completed"
        return (True, "No requirements assigned to this phase")

    # Validate each requirement's required stages
    failures = []
    for req in requirements:
        req_id = req.get("id", req.get("req_id", "?"))
        for stage in required_stages:
            if not is_stage_complete(req, stage):
                current = get_stage_status(req, stage)
                failures.append(f"{req_id} {stage}={current}")

    if failures:
        reason = (
            f"Phase {phase_seq} ({category}) cannot complete: "
            + ", ".join(failures)
        )
        logger.info(f"[ADR-089] {reason}")
        return (False, reason)

    # All validations passed
    phase["status"] = "completed"
    logger.info(
        f"[ADR-089] Phase {phase_seq} ({category}) completed. "
        f"{len(requirements)} requirements validated."
    )
    return (True, f"All {len(requirements)} requirements have required stages done")


def try_phase_completed(
    phase: dict,
    project_dir: str,
    note: str = "",
    force: bool = False,
) -> tuple:
    """Convenience wrapper: load requirements from disk, then validate completion.

    ADR-089 + ADR-086 §12: ALL reconcilers MUST call this instead of
    doing `phase["status"] = "completed"` directly. This loads the
    requirements ledger from disk, finds requirements for this phase,
    and delegates to set_phase_completed() for category-aware validation.

    Args:
        phase: Phase dict from decomposition_index.json.
        project_dir: Absolute path to the project directory.
        note: Optional note to set on the phase if completion is allowed.
        force: If True, bypass validation (escape hatch).

    Returns:
        Tuple of (allowed: bool, reason: str).
    """
    import json as _json
    from python.helpers.planning_paths import get_path as _planning_path

    # Load requirements from ledger on disk
    requirements = []
    ledger_paths = [
        os.path.join(project_dir, "docs", "requirements-ledger.json"),
        os.path.join(project_dir, ".agix.proj", "requirements_ledger.json"),
        os.path.join(project_dir, ".agix.proj", "requirements-ledger.json"),
        os.path.join(project_dir, "requirements_ledger.json"),
        os.path.join(project_dir, "requirements-ledger.json"),
    ]
    ledger_path = None
    for path in ledger_paths:
        if os.path.isfile(path):
            ledger_path = path
            break

    try:
        if ledger_path:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger_data = _json.load(f)
            all_reqs = ledger_data.get("requirements", [])
            requirements = _get_requirements_for_phase(phase, all_reqs)
    except (Exception) as e:
        logger.debug(f"[ADR-089] Could not load requirements for phase validation: {e}")

    allowed, reason = set_phase_completed(phase, requirements, project_dir, force=force)

    if allowed and note and not phase.get("note"):
        phase["note"] = note

    return (allowed, reason)

def ensure_stage_status(req: dict) -> dict:
    """Migration: ensure stage_status exists on a requirement.

    Backward compatibility: if stage_status doesn't exist,
    infer it from the flat status field.

    Args:
        req: Requirement dict (mutated in place).

    Returns:
        The stage_status dict (for convenience).
    """
    if "stage_status" not in req:
        flat = req.get("status", "pending")
        if flat in ("completed", "verified", "done", "complete"):
            req["stage_status"] = {"bdd": flat, "tdd": flat, "code": flat}
        elif flat == "delegation_returned":
            req["stage_status"] = {
                "bdd": "completed",
                "tdd": "completed",
                "code": "delegation_returned",
            }
        elif flat == "assigned":
            req["stage_status"] = {
                "bdd": "pending",
                "tdd": "pending",
                "code": "pending",
            }
        else:
            req["stage_status"] = {
                "bdd": "pending",
                "tdd": "pending",
                "code": "pending",
            }
    return req["stage_status"]


# ─── F-8: Category-Based Phase Status Reconciliation — Category Inference ─

# Keyword → category mapping. Each entry is (keywords_set, category_name).
# Order matters: first match wins. More specific patterns first.
_CATEGORY_KEYWORDS = [
    # Research must come before planning (research IS a planning sub-stage
    # but gets its own category per user direction)
    ({"research", "docs pre-fetch", "documentation pre-fetch"}, "research"),
    # Planning
    # NOTE: 'audit' removed — too ambiguous, matched 'Self-Service Audit Page'
    # feature titles causing false completions (ITR-55 Phase 3.4 bug).
    ({"planning", "manifest", "setup", "feature classification",
      "wave prioritization"}, "planning"),
    # Implementation must come before design because titles like
    # "Implementation (TDD + BDD)" contain "tdd"/"bdd" which would
    # otherwise match design keywords.
    ({"implementation", "implement", "scaffold cleanup", "post-scaffold",
      "build verification", "cache invalidation", "boomerang",
      "build", "api route", "code", "feature", "tdd + bdd",
      "compliance", "unsubscribe", "privacy", "legal",
      # ITR-55: Common implementation phase title nouns that were causing 'unknown'
      "shell", "layout", "nav", "footer", "dashboard", "pipeline",
      "scoring", "page", "queue", "outreach", "engine", "model",
      "auth", "calendar", "sequence", "notification", "webhook",
      "crud", "endpoint", "middleware", "migration"}, "implementation"),
    # Design (includes architecture, BDD/TDD design, mockups, tokens)
    ({"design", "architect", "mockup", "tokens", "ui/ux", "bdd", "tdd",
      "skeleton", "validate decomposition", "schema lock", "cross-check",
      "enrichment"}, "design"),
    # Verification (testing, E2E, integration, smoke checks)
    ({"verification", "verify", "integration", "e2e", "smoke", "wiring",
      "route map", "css/config", "build-freeze", "test aggregation",
      "design review", "iteration"}, "verification"),
    # Deployment
    ({"deploy", "publish", "release", "version control", "publication",
      "summary"}, "deployment"),
]


def infer_phase_category(title: str) -> str:
    """Infer a phase's lifecycle category from its title/description.

    F-8: Category inference is ALWAYS from the title text, NEVER from
    the phase number. Categories are:
      planning, research, design, implementation, verification, deployment

    Args:
        title: The phase title or description string.

    Returns:
        Category string (lowercase), or 'unknown' if no match.
    """
    if not title:
        return "unknown"

    title_lower = title.lower()

    # ITR-55 FIX: Prefix override — titles starting with "Implementation:"
    # are ALWAYS implementation, regardless of other keyword matches.
    # Root cause: 'audit' in planning matched 'Implementation: Self-Service
    # Audit Page' before 'implementation' was checked, because planning
    # comes before implementation in _CATEGORY_KEYWORDS.
    if title_lower.startswith("implementation"):
        return "implementation"

    for keywords, category in _CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in title_lower:
                return category

    return "unknown"


# F-14: Profile → compatible phase categories.
# When a delegation completes, it should only auto-complete phases whose
# category is compatible with the delegation's agent profile.
# Root cause: ITR-43 frontend delegation (design) falsely completed Phase 3.3/3.4
# (implementation) because they shared req_guids.
_PROFILE_PHASE_COMPATIBILITY = {
    "code":       {"planning", "implementation", "verification"},
    "frontend":   {"design"},
    "architect":  {"design", "research"},
    "researcher": {"research", "planning"},
    "e2e":        {"verification"},
    "browser":    {"verification"},
    # Orchestrator can complete any category (it delegates, not implements)
    "orchestrator": {"planning", "research", "design", "implementation", "verification", "deployment"},
    # Unknown profiles get no auto-completion (safe default)
}
