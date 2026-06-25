"""
Requirements Seeding & Gate Failure Management

Extracted from requirements_ledger.py during P4 modularization (Phase 1.4).
Contains requirement seeding from goal state, line items, prompt supplement,
contract features, requirement ID validation, gate failure recording/resolution,
and pre-delivery coverage audit.

Functions:
    seed_from_goal_state        — Seed from GoalState.success_criteria
    merge_line_items_into_ledger — Merge LineItems with dedup
    supplement_from_prompt       — Auto-extract and supplement from prompt
    seed_features_into_ledger    — Merge contract features into ledger
    validate_requirement_ids     — Validate delegation has requirement IDs
    record_gate_failure          — Record a gate check failure
    get_gate_failures            — Get all gate failures
    resolve_gate_failure         — Mark gate failures as resolved
    clear_delegation_failures    — Remove gate failures for a delegation
    get_active_gate_failures     — Get unresolved gate failures
    get_remediation_tasks        — Get structured remediation tasks
    pre_delivery_coverage_audit  — Proactive pre-delivery coverage check
"""

import logging
from typing import Any, Dict, List, Optional

from python.helpers.requirements_persistence import (
    _dedup_hash,
    _ensure_ledger,
    persist_ledger_to_project,
)
from python.helpers.requirements_crud import (
    _generate_req_id,
    _infer_category,
    init_requirements,
    is_antipattern_requirement,
)

logger = logging.getLogger("agix.requirements_ledger")


def seed_from_goal_state(
    agent_data: dict,
    success_criteria: List[str],
) -> None:
    """Seed the requirements ledger from GoalState.success_criteria.

    This is the single entry point for requirements extraction. GoalState
    already uses an LLM to extract success criteria from the user prompt.
    Instead of duplicating that extraction, the ledger consumes GoalState's
    criteria as its requirement list.

    Categories are inferred from text content using _infer_category() so
    verifiers can apply the correct verification strategy per requirement.

    Idempotent: if requirements already exist, this is a no-op.

    Args:
        agent_data: The agent.data dict
        success_criteria: List of success criteria strings from GoalState
    """
    ledger = _ensure_ledger(agent_data)

    # Idempotent guard — don't re-seed if requirements already populated
    if ledger.get("requirements"):
        return

    if not success_criteria:
        return

    requirements = [
        {"text": criterion, "category": _infer_category(criterion)}
        for criterion in success_criteria
        if criterion and isinstance(criterion, str)
    ]

    init_requirements(agent_data, requirements)
    logger.info(
        f"[REQUIREMENTS LEDGER] Seeded {len(requirements)} requirements "
        f"from GoalState.success_criteria"
    )


def merge_line_items_into_ledger(
    agent_data: dict,
    line_items: list,
    source_metadata: dict = None,
    project_dir: str = None,
) -> int:
    """Merge deterministic LineItems into the requirements ledger.

    Runs AFTER seed_from_goal_state. Uses content-hash dedup to avoid
    duplicating requirements that overlap with GoalState criteria.

    Args:
        agent_data: The agent.data dict
        line_items: List of LineItem objects from prompt_line_item_extractor
        source_metadata: Optional dict mapping item text → source attribution
                         (e.g. "regex", "llm", "both", "llm_unverified").
                         When provided by the hybrid pipeline validator,
                         overrides the default "line_item_extractor" source.

    Returns:
        Number of new requirements added (after dedup)
    """
    from python.helpers.hashing import content_hash_short

    ledger = _ensure_ledger(agent_data)
    existing_reqs = ledger.get("requirements", [])

    # Build dedup set from existing requirement texts
    existing_hashes = set()
    for req in existing_reqs:
        text = req.get("text", "")
        normalized = text.strip().lower()
        existing_hashes.add(content_hash_short(normalized, length=12))

    added = 0

    for item in line_items:
        # Content-hash dedup against existing requirements
        normalized = item.text.strip().lower()
        ch = content_hash_short(normalized, length=12)
        if ch in existing_hashes:
            continue
        existing_hashes.add(ch)

        # F-12 (ITR-18): Quality filter — classify BEFORE adding to ledger.
        # F-5 (ITR-15) wired classify_requirement_text but only as metadata.
        # F-12 upgrades this to an actual filter: context items are EXCLUDED.
        from python.helpers.prompt_line_item_extractor import classify_requirement_text
        actionability = classify_requirement_text(item.text)
        if actionability == 'context':
            logger.debug(
                f"[REQUIREMENTS LEDGER] F-12: Filtered context item: "
                f"'{item.text[:60]}...'"
            )
            continue

        # F-12: Minimum length filter — skip very short fragments
        if len(item.text.strip()) < 20:
            logger.debug(
                f"[REQUIREMENTS LEDGER] F-12: Filtered short item "
                f"({len(item.text)} chars): '{item.text}'"
            )
            continue

        # F-12: Sentence boundary filter — must start with capital or special char
        stripped = item.text.strip()
        if stripped and stripped[0].islower() and not stripped.startswith(('http', '$', '/', '@')):
            logger.debug(
                f"[REQUIREMENTS LEDGER] F-12: Filtered non-sentence fragment: "
                f"'{stripped[:60]}'"
            )
            continue

        # Determine extraction source
        extraction_source = "line_item_extractor"
        if source_metadata and item.text in source_metadata:
            extraction_source = source_metadata[item.text]

        req_id = _generate_req_id(item.text)

        ledger["requirements"].append({
            "id": req_id,
            "text": item.text,
            "category": item.category,
            "status": "pending",
            "assigned_to": [],
            "source": extraction_source if source_metadata else "line_item_extractor",
            "extraction_source": extraction_source,
            "source_line": item.source_line,
            "priority": getattr(item, 'priority', 'immediate'),
            "actionability": actionability,
            # F-4 (ITR-15): Persist confidence and extrapolation metadata
            "confidence": getattr(item, 'confidence', 1.0),
            "extrapolated": getattr(item, 'extrapolated', False),
        })

        added += 1

    if added:
        logger.info(
            f"[REQUIREMENTS LEDGER] Merged {added} line items "
            f"(from {len(line_items)} extracted, {len(line_items) - added} deduped)"
        )

    if project_dir:
        persist_ledger_to_project(agent_data, project_dir)

    return added


def supplement_from_prompt(agent_data: dict, prompt: str) -> int:
    """Run the regex extractor on the original prompt and merge any missing items.

    RCA-ITR4-001 Fix: The orchestrator's LLM-based extraction in `requirements init`
    often misses requirements that the automated regex extractor would catch (drip
    automation, physical address, GitHub push, review capture, etc.). This function
    runs the deterministic extractor AFTER init and supplements the ledger.

    Uses content-hash dedup (same as merge_line_items_into_ledger) to avoid
    duplicating requirements that the LLM already extracted.

    Args:
        agent_data: The agent.data dict (must already have _requirements_ledger)
        prompt: The original user prompt text

    Returns:
        Number of new requirements added
    """
    if not prompt or not prompt.strip():
        return 0

    ledger = _ensure_ledger(agent_data)
    existing_reqs = ledger.get("requirements", [])

    # Build dedup set from existing requirement texts (keyword-level)
    existing_keywords = set()
    for req in existing_reqs:
        text = req.get("text", "").lower()
        # Extract significant keywords for fuzzy dedup
        for word in text.split():
            clean = word.strip(".,;:!?()[]{}\"'")
            if len(clean) > 3:
                existing_keywords.add(clean)

    # Also build content-hash dedup set
    from python.helpers.hashing import content_hash_short

    existing_hashes = set()
    for req in existing_reqs:
        normalized = req.get("text", "").strip().lower()
        try:
            existing_hashes.add(content_hash_short(normalized, length=12))
        except ImportError:
            # Fallback: use simple hash
            existing_hashes.add(hash(normalized))

    # Run the weighted candidate extractor
    try:
        from python.helpers.prompt_line_item_extractor import extract_weighted_candidates
        candidates = extract_weighted_candidates(prompt)
    except ImportError:
        logger.warning("[REQUIREMENTS LEDGER] Could not import extractor for supplement")
        return 0
    except Exception as e:
        logger.warning(f"[REQUIREMENTS LEDGER] Extractor failed during supplement: {e}")
        return 0

    if not candidates:
        return 0

    added = 0

    for candidate in candidates:
        # Content-hash dedup
        normalized = candidate.text.strip().lower()
        try:
            ch = content_hash_short(normalized, length=12)
        except ImportError:
            ch = hash(normalized)
        if ch in existing_hashes:
            continue

        # ITR-39 SYSTEM 2: Lineage-aware dedup — expanded candidates bypass keyword overlap.
        # Quantity-expanded candidates share keywords with parent BY DESIGN.
        # Content-hash dedup (above) handles true duplicates.
        is_expanded = (
            hasattr(candidate, 'structural_markers')
            and candidate.structural_markers.get("is_quantity_expanded")
        )

        # Build candidate keyword set (needed for both dedup check and keyword tracking)
        candidate_words = set()
        for word in normalized.split():
            clean = word.strip(".,;:!?()[]{}\"'")
            if len(clean) > 3:
                candidate_words.add(clean)

        if not is_expanded:
            # Keyword overlap dedup — if ≥50% of candidate's significant keywords
            # already appear in existing requirements, it's likely a duplicate
            if candidate_words:
                overlap = len(candidate_words & existing_keywords) / len(candidate_words)
                if overlap >= 0.5:
                    continue

        existing_hashes.add(ch)
        # Add new keywords to the set for subsequent dedup
        existing_keywords.update(candidate_words)

        # F-12 (ITR-18): Quality filter for supplement_from_prompt.
        # Same filter as merge_line_items_into_ledger — skip context noise,
        # short fragments, and lowercase sentence fragments.
        try:
            from python.helpers.prompt_line_item_extractor import classify_requirement_text
            actionability = classify_requirement_text(candidate.text)
            if actionability == 'context':
                logger.debug(
                    f"[REQUIREMENTS LEDGER] F-12: Filtered context supplement: "
                    f"'{candidate.text[:60]}...'"
                )
                continue
        except (ImportError, AttributeError) as e:
            logger.debug(
                f"[REQUIREMENTS LEDGER] classify_requirement_text unavailable: {e}. "
                f"Skipping F-12 filter for supplement_from_prompt."
            )

        # ITR-344 SS-3: Filter anti-pattern requirements from auto-extractor
        if is_antipattern_requirement(candidate.text):
            logger.debug(
                f"[REQUIREMENTS LEDGER] SS-3: Filtered anti-pattern supplement: "
                f"'{candidate.text[:60]}'"
            )
            continue

        if len(candidate.text.strip()) < 20:
            logger.debug(
                f"[REQUIREMENTS LEDGER] F-12: Filtered short supplement "
                f"({len(candidate.text)} chars): '{candidate.text}'"
            )
            continue

        stripped_supp = candidate.text.strip()
        if (stripped_supp and stripped_supp[0].islower()
                and not stripped_supp.startswith(('http', '$', '/', '@'))
                and candidate.category not in ('integration', 'url', 'config')):
            logger.debug(
                f"[REQUIREMENTS LEDGER] F-12: Filtered non-sentence supplement: "
                f"'{stripped_supp[:60]}'"
            )
            continue

        req_id = _generate_req_id(candidate.text)
        ledger["requirements"].append({
            "id": req_id,
            "text": candidate.text,
            "category": candidate.category,
            "status": "pending",
            "assigned_to": [],
            "source": "auto_extractor",
        })

        added += 1

    if added:
        logger.info(
            f"[REQUIREMENTS LEDGER] Auto-supplemented {added} requirements "
            f"from prompt (from {len(candidates)} candidates, "
            f"{len(candidates) - added} deduped)"
        )

    return added


# ─── Contract Feature Seeding ───────────────────────────────────────────


def seed_features_into_ledger(
    agent_data: dict,
    features: list,
) -> int:
    """Merge contract features into the requirements ledger.

    Runs AFTER seed_from_goal_state and merge_line_items_into_ledger.
    Uses content-hash dedup to avoid duplicating requirements that overlap
    with GoalState criteria or line items.

    Each feature carries an expected_route, which the feature registry
    uses for classification.

    Args:
        agent_data: The agent.data dict
        features: List of feature dicts from extract_features()
                  Each has: name, expected_route, category

    Returns:
        Number of new requirements added (after dedup)
    """
    if not features:
        return 0

    ledger = _ensure_ledger(agent_data)
    existing_reqs = ledger.get("requirements", [])

    from python.helpers.hashing import content_hash_short

    # Build dedup set from existing requirement texts
    existing_hashes = set()
    for req in existing_reqs:
        text = req.get("text", "")
        normalized = text.strip().lower()
        existing_hashes.add(content_hash_short(normalized, length=12))

    added = 0

    for feature in features:
        name = feature.get("name", "")
        if not name:
            continue

        # Content-hash dedup against existing requirements
        normalized = name.strip().lower()
        ch = content_hash_short(normalized, length=12)
        if ch in existing_hashes:
            continue
        existing_hashes.add(ch)

        req_id = _generate_req_id(name)
        ledger["requirements"].append({
            "id": req_id,
            "text": name,
            "category": feature.get("category", "feature"),
            "status": "pending",
            "assigned_to": [],
            "source": "prompt_contract_features",
            "expected_route": feature.get("expected_route", ""),
        })

        added += 1

    if added:
        logger.info(
            f"[REQUIREMENTS LEDGER] Seeded {added} contract features "
            f"(from {len(features)} extracted, {len(features) - added} deduped)"
        )

    return added


# ─── Requirement IDs Validation ─────────────────────────────────────────


def validate_requirement_ids(
    agent_data: dict,
    requirement_ids: List[str],
) -> Optional[str]:
    """Validate that requirement_ids are provided for a delegation.

    Hard enforcement: when requirements exist in the ledger and a
    delegation is missing requirement_ids, return a warning message
    that the tool should inject into its response.

    Returns None if validation passes (no warning needed).

    Args:
        agent_data: The agent.data dict
        requirement_ids: The requirement_ids from the delegation call

    Returns:
        Warning message string if validation fails, None if OK
    """
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return None  # No requirements registered — skip validation

    reqs = ledger.get("requirements", [])
    if not reqs:
        return None  # No requirements — skip validation

    if requirement_ids:
        return None  # requirement_ids provided — validation passes

    # Requirements exist but no requirement_ids in delegation
    unassigned = [r for r in reqs if r["status"] == "pending"]
    unassigned_preview = "; ".join(
        f"{r['id']}: {r['text'][:60]}" for r in unassigned[:3]
    )

    return (
        f"⚠️ MISSING requirement_ids: The requirements ledger has "
        f"{len(reqs)} tracked requirements but this delegation did not "
        f"include requirement_ids. Add requirement_ids=[...] to link this "
        f"delegation to specific requirements. "
        f"Unassigned: {unassigned_preview}"
    )


# ─── Gate Failure Recording ──────────────────────────────────────────────


def record_gate_failure(
    agent_data: dict,
    check_name: str,
    reason: str,
    affected_delegation_ids: Optional[List[str]] = None,
) -> None:
    """Record a gate check failure back into the requirements ledger.

    This closes the feedback loop: gate failures update the ledger so the
    orchestrator can delegate targeted remediation instead of broad rework.

    Args:
        agent_data: The agent.data dict
        check_name: Name of the failing check (e.g., 'hardcoded_secrets')
        reason: Description of the failure
        affected_delegation_ids: List of delegation IDs affected by this failure
    """
    ledger = _ensure_ledger(agent_data)
    affected_delegation_ids = affected_delegation_ids or []

    ledger["gate_failures"].append({
        "check_name": check_name,
        "reason": reason,
        "affected_delegation_ids": list(affected_delegation_ids),
    })

    # Mark affected delegations as 'failed' and clear their dedup hashes
    dedup_hashes = ledger.get("dedup_hashes", {})
    for delegation in ledger["delegations"]:
        if delegation["id"] in affected_delegation_ids:
            delegation["status"] = "failed"
            # Clear dedup hash so the same delegation can be retried
            content_hash = _dedup_hash(
                delegation["profile"], delegation["message_summary"]
            )
            dedup_hashes.pop(content_hash, None)

    logger.warning(
        f"[REQUIREMENTS LEDGER] Gate failure recorded: {check_name} — "
        f"{reason} (affects {len(affected_delegation_ids)} delegations)"
    )


def get_gate_failures(agent_data: dict) -> List[Dict]:
    """Get all recorded gate failures (resolved and unresolved).

    Returns:
        List of failure dicts with keys: check_name, reason, affected_delegation_ids
    """
    ledger = _ensure_ledger(agent_data)
    return list(ledger.get("gate_failures", []))


def resolve_gate_failure(
    agent_data: dict,
    check_name: Optional[str] = None,
    delegation_id: Optional[str] = None,
) -> int:
    """Mark matching gate failures as resolved.

    SS-1 Fix: Gate failures were append-only. When remediation agent B
    fixed what agent A broke, the gate still saw A's failures because
    they were never cleared. This function marks failures as resolved
    so get_active_gate_failures() excludes them.

    At least one of check_name or delegation_id must be provided.
    If both are provided, a failure must match EITHER criterion.

    Args:
        agent_data: The agent.data dict
        check_name: Resolve all failures for this check name
        delegation_id: Resolve all failures linked to this delegation ID

    Returns:
        Number of failures resolved

    Raises:
        ValueError: If neither check_name nor delegation_id is provided
    """
    if not check_name and not delegation_id:
        raise ValueError(
            "resolve_gate_failure requires at least one of "
            "check_name or delegation_id"
        )

    ledger = _ensure_ledger(agent_data)
    resolved_count = 0

    for failure in ledger.get("gate_failures", []):
        if failure.get("resolved"):
            continue  # Already resolved — idempotent

        match = False
        if check_name and failure.get("check_name") == check_name:
            match = True
        if delegation_id and delegation_id in failure.get("affected_delegation_ids", []):
            match = True

        if match:
            failure["resolved"] = True
            resolved_count += 1

    if resolved_count > 0:
        logger.info(
            f"[REQUIREMENTS LEDGER] Resolved {resolved_count} gate failure(s) "
            f"(check_name={check_name}, delegation_id={delegation_id})"
        )

    return resolved_count


def clear_delegation_failures(
    agent_data: dict,
    delegation_id: str,
) -> int:
    """Remove all gate failures associated with a delegation.

    SS-1 Fix: When a remediation delegation completes successfully,
    this function removes the original failures entirely (not just
    marking them resolved). Use this for hard cleanup; use
    resolve_gate_failure for soft resolution.

    Args:
        agent_data: The agent.data dict
        delegation_id: The delegation ID whose failures to remove

    Returns:
        Number of failures removed
    """
    ledger = _ensure_ledger(agent_data)
    original = ledger.get("gate_failures", [])
    filtered = [
        f for f in original
        if delegation_id not in f.get("affected_delegation_ids", [])
    ]
    removed = len(original) - len(filtered)
    ledger["gate_failures"] = filtered

    if removed > 0:
        logger.info(
            f"[REQUIREMENTS LEDGER] Cleared {removed} gate failure(s) "
            f"for delegation {delegation_id}"
        )

    return removed


def get_active_gate_failures(agent_data: dict) -> List[Dict]:
    """Get only unresolved gate failures.

    SS-1 Fix: Unlike get_gate_failures() which returns ALL failures
    (for audit/history), this returns only failures that have NOT
    been resolved. Use this in gate checks to avoid re-blocking on
    already-fixed issues.

    Returns:
        List of unresolved failure dicts
    """
    ledger = _ensure_ledger(agent_data)
    return [
        f for f in ledger.get("gate_failures", [])
        if not f.get("resolved")
    ]



def get_remediation_tasks(agent_data: dict) -> List[Dict]:
    """Get structured remediation tasks for failed delegations.

    Returns one remediation entry per failed delegation, including the
    check name and failure reason. This gives the orchestrator targeted
    information to delegate specific fixes instead of broad rework.

    Returns:
        List of dicts with keys: delegation_id, profile, message_summary,
        check_name, failure_reason
    """
    ledger = _ensure_ledger(agent_data)
    tasks = []

    # Build failure lookup: delegation_id → (check_name, reason)
    failure_lookup: Dict[str, Dict] = {}
    for failure in ledger.get("gate_failures", []):
        for did in failure.get("affected_delegation_ids", []):
            failure_lookup[did] = {
                "check_name": failure["check_name"],
                "failure_reason": failure["reason"],
            }

    for delegation in ledger.get("delegations", []):
        if delegation["status"] == "failed" and delegation["id"] in failure_lookup:
            tasks.append({
                "delegation_id": delegation["id"],
                "profile": delegation["profile"],
                "message_summary": delegation["message_summary"],
                **failure_lookup[delegation["id"]],
            })

    return tasks


# ─── F3-A: Pre-Delivery Coverage Audit ──────────────────────────────────
#
# RCA 214: Coverage was only checked AT delivery time (when the response
# tool fired). This meant the agent wasted an exhaustion counter slot
# discovering missing requirements. This function can run BEFORE the
# response tool to proactively check coverage.


def pre_delivery_coverage_audit(agent_data: dict) -> Dict[str, Any]:
    """Check if all requirements are covered before delivery.

    This proactive check identifies uncovered requirements BEFORE the
    orchestrator calls response, so it can delegate missing work without
    consuming gate exhaustion budget.

    A requirement is "covered" if it:
    - Has been assigned to a delegation (has assigned_to), OR
    - Has a terminal status (completed, verified, partial, failed, etc.)

    Args:
        agent_data: The agent.data dict

    Returns:
        Dict with:
            - ready_for_delivery: bool
            - unassigned: list of requirement dicts (id, text, category)
            - total_requirements: int
            - coverage_pct: int (0-100)
    """
    from python.helpers.status_constants import REQ_DONE_STATUSES

    ledger = _ensure_ledger(agent_data)
    reqs = ledger.get("requirements", [])

    if not reqs:
        return {
            "ready_for_delivery": True,
            "unassigned": [],
            "total_requirements": 0,
            "coverage_pct": 100,
        }

    # A req is unassigned only if it has no delegation AND is not in a
    # terminal status. Partial/failed/completed/verified are "done trying".
    unassigned = [
        {"id": r["id"], "text": r["text"], "category": r.get("category", "general")}
        for r in reqs
        if not r.get("assigned_to") and r.get("status", "pending") not in REQ_DONE_STATUSES
    ]

    total = len(reqs)
    # Coverage = assigned OR done (terminal status)
    covered = sum(
        1 for r in reqs
        if r.get("assigned_to") or r.get("status", "pending") in REQ_DONE_STATUSES
    )
    pct = int(covered / max(total, 1) * 100)

    return {
        "ready_for_delivery": len(unassigned) == 0,
        "unassigned": unassigned,
        "total_requirements": total,
        "coverage_pct": pct,
    }


# ═══════════════════════════════════════════════════════════════════════════
# WP-3: Integration Requirements Seeding from Dependency Graph
# ═══════════════════════════════════════════════════════════════════════════

def seed_integration_requirements(
    agent_data: dict,
    dep_graph: dict,
) -> int:
    """Auto-generate and seed REQ-INT-xxx integration requirements from dependency graph.

    Reads the architect's dependency-graph.json, generates integration
    requirements for every import edge and page-API binding, and merges
    them into the requirements ledger with dedup.

    Each integration requirement maps a specific cross-module wiring that
    the code agent MUST implement. These generate TDD stubs downstream.

    Args:
        agent_data: The agent.data dict (contains _requirements_ledger).
        dep_graph: Parsed dependency-graph.json from the architect.

    Returns:
        Number of new integration requirements added (after dedup).
    """
    if not dep_graph:
        return 0

    from python.helpers.budget_cost_model import generate_integration_requirements

    int_reqs = generate_integration_requirements(dep_graph)
    if not int_reqs:
        return 0

    ledger = _ensure_ledger(agent_data)
    existing_ids = {r.get("id") for r in ledger.get("requirements", [])}

    added = 0
    for req in int_reqs:
        if req["id"] in existing_ids:
            continue

        ledger["requirements"].append({
            "id": req["id"],
            "text": req["text"],
            "category": "integration",
            "status": "pending",
            "assigned_to": [],
            "source": "dependency_graph",
            "source_module": req.get("source_module", ""),
            "target_module": req.get("target_module", ""),
            "parent_req_guids": req.get("parent_req_guids", []),
        })
        existing_ids.add(req["id"])
        added += 1

    if added:
        logger.info(
            f"[REQUIREMENTS LEDGER] Seeded {added} integration requirements "
            f"from dependency graph ({len(int_reqs)} total, "
            f"{len(int_reqs) - added} deduped)"
        )

    return added
