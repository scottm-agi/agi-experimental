"""
Requirements CRUD Operations

Extracted from requirements_ledger.py during P4 modularization (Phase 1.3).
Contains requirement creation, initialization, filtering, verification,
and category inference.

Functions:
    is_antipattern_requirement — Detect anti-pattern (negative) requirements
    _generate_req_id          — Content-addressable requirement ID generation
    init_requirements         — Bulk-initialize requirements from extraction
    verify_seeding            — Verify all extracted reqs were seeded
    add_requirement           — Add a single requirement dynamically
    _infer_category           — Infer requirement category from text content
"""

import json
import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger("agix.requirements_ledger")


# ─── ITR-344 SS-3: Anti-Pattern / Negation Filter ────────────────────────
#
# Root cause: The LLM-based requirement extractor captures anti-patterns
# ("Don't use X", "Avoid Y") as positive requirements. In the MainStreet
# run, 4/39 extracted requirements were anti-patterns that should have been
# filtered: "Do NOT use placeholder images" became "Use placeholder images".
#
# This deterministic Layer 1 filter catches negation prefixes and filters
# them BEFORE they enter the ledger. Double negation ("Do not forget to
# include X") is preserved as positive intent.

# Negation prefixes that indicate anti-patterns (what NOT to do)
_ANTIPATTERN_PREFIX_RE = re.compile(
    r"(?:^|\b)"
    r"(?:"
    r"don[\u2019']?t\s+"
    r"|do\s+not\s+"
    r"|avoid\s+"
    r"|never\s+"
    r"|no\s+"
    r"|ensure\s+no\s+"
    r"|please\s+don[\u2019']?t\s+"
    r"|please\s+do\s+not\s+"
    r"|please\s+avoid\s+"
    r")",
    re.IGNORECASE,
)

# Double-negation markers that indicate POSITIVE intent despite negation
# prefix. "Do not forget" = "remember", "Don't miss" = "include".
_DOUBLE_NEGATION_RE = re.compile(
    r"(?:"
    r"don[\u2019']?t\s+(?:forget|miss|leave\s+out|skip|overlook|neglect|omit)"
    r"|do\s+not\s+(?:forget|miss|leave\s+out|skip|overlook|neglect|omit)"
    r"|never\s+(?:forget|miss|leave\s+out|skip|overlook|neglect|omit)"
    r")",
    re.IGNORECASE,
)


def is_antipattern_requirement(text: str) -> bool:
    """Detect if a requirement text is an anti-pattern (negative constraint).

    ITR-344 SS-3 Fix: Deterministic Layer 1 filter that catches negation
    prefixes like 'don't', 'do not', 'avoid', 'never', 'no' and prevents
    them from entering the requirements ledger as positive requirements.

    Double negation is preserved as positive intent:
        - "Do not forget to include X" → NOT an anti-pattern (positive intent)
        - "Don't miss the footer" → NOT an anti-pattern (positive intent)
        - "Don't leave out mobile responsive" → NOT an anti-pattern

    Args:
        text: The requirement text to check.

    Returns:
        True if the text is an anti-pattern (should be FILTERED).
        False if the text is a positive requirement (should be KEPT).
    """
    if not text or not text.strip():
        return False

    stripped = text.strip()

    # Check for double negation FIRST — these are positive intent
    if _DOUBLE_NEGATION_RE.search(stripped):
        return False

    # Check for negation prefixes
    if _ANTIPATTERN_PREFIX_RE.search(stripped):
        return True

    return False


def _generate_req_id(text: str) -> str:
    """Generate a content-addressable requirement ID from text.

    Uses MD5 short hash to produce deterministic, collision-resistant IDs
    that match the decomposition_index.json GUID format (RCA-361).

    Format: REQ-{first 8 chars of MD5 hash}

    This replaces the old sequential REQ-001, REQ-002... format which
    caused a critical disconnection with decomposition_index GUIDs.

    Args:
        text: The requirement description text

    Returns:
        Content-addressable ID like 'REQ-a1b2c3d4'
    """
    from python.helpers.task_hash import compute_task_guid
    return compute_task_guid(text)


def init_requirements(agent_data: dict, requirements: List[Dict[str, str]], project_dir: str = None) -> None:
    """Populate the requirements ledger from extracted prompt requirements.

    Each requirement dict should have at minimum:
        - "text": The requirement description
        - "category": Free-form category (url, integration, feature, etc.)

    Args:
        agent_data: The agent.data dict
        requirements: List of requirement dicts with "text" and "category"
    """
    from python.helpers.requirements_persistence import (
        _ensure_ledger,
        persist_ledger_to_project,
    )
    ledger = _ensure_ledger(agent_data)

    filtered_count = 0
    for i, req in enumerate(requirements, start=1):
        req_text = req.get("text", f"requirement-{i}")

        # ITR-344 SS-3: Filter anti-pattern requirements before they enter the ledger
        if is_antipattern_requirement(req_text):
            filtered_count += 1
            logger.info(
                f"[REQUIREMENTS LEDGER] SS-3: Filtered anti-pattern requirement: "
                f"'{req_text[:80]}'"
            )
            continue

        req_id = _generate_req_id(req_text)
        ledger["requirements"].append({
            "id": req_id,
            "text": req.get("text", ""),
            "category": req.get("category", "general"),
            "data_source": req.get("data_source", "static"),  # F-2: static|api|database|computed
            "status": "pending",
            "assigned_to": [],
        })

    if filtered_count:
        logger.info(
            f"[REQUIREMENTS LEDGER] SS-3: Filtered {filtered_count} anti-pattern "
            f"requirements from {len(requirements)} total"
        )
    logger.info(
        f"[REQUIREMENTS LEDGER] Initialized {len(requirements) - filtered_count} requirements"
        f" ({filtered_count} anti-patterns filtered)"
    )

    if project_dir:
        persist_ledger_to_project(agent_data, project_dir)


def verify_seeding(
    agent_data: dict,
    extracted_requirements: List[Dict[str, str]],
    project_dir: str = None,
) -> Dict[str, Any]:
    """Verify that all extracted requirements were successfully seeded into the ledger.

    GAP 1 FIX: After init_requirements(), compare the extraction output against
    the ledger state. Any requirements that were dropped (by dedup, anti-pattern
    filter, or other mechanism) are flagged.

    Args:
        agent_data: The agent.data dict
        extracted_requirements: The original extraction output (before init_requirements)
        project_dir: If provided, persist verification result to disk

    Returns:
        Dict with:
          - seeded: int — number successfully seeded
          - dropped: int — number dropped
          - dropped_items: List[Dict] — details of each dropped requirement
          - recovered: int — number auto-recovered (reserved for future use)
    """
    from python.helpers.requirements_persistence import _ensure_ledger
    ledger = _ensure_ledger(agent_data)
    ledger_reqs = ledger.get("requirements", [])

    # Build set of requirement texts currently in ledger (normalized)
    ledger_texts = set()
    ledger_ids = set()
    for req in ledger_reqs:
        text = req.get("text", "").strip().lower()
        if text:
            ledger_texts.add(text)
        req_id = req.get("id", "")
        if req_id:
            ledger_ids.add(req_id)

    seeded = 0
    dropped = 0
    dropped_items = []

    for req in extracted_requirements:
        text = req.get("text", "").strip()
        if not text:
            continue

        # Check by text match (normalized) OR by generated req_id
        text_lower = text.lower()
        req_id = _generate_req_id(text)

        if text_lower in ledger_texts or req_id in ledger_ids:
            seeded += 1
        else:
            dropped += 1
            dropped_items.append({
                "text": text,
                "category": req.get("category", ""),
                "expected_id": req_id,
                "reason": "filtered_or_deduped",
            })
            logger.warning(
                f"[REQUIREMENTS LEDGER] P0-3 GAP 1: Requirement DROPPED during seeding: "
                f"'{text[:80]}' (expected ID: {req_id})"
            )

    result = {
        "seeded": seeded,
        "dropped": dropped,
        "dropped_items": dropped_items,
        "recovered": 0,
    }

    if dropped > 0:
        logger.warning(
            f"[REQUIREMENTS LEDGER] P0-3 SEEDING VERIFICATION: "
            f"{dropped}/{seeded + dropped} requirements were DROPPED. "
            f"Check dropped_items for details."
        )
    else:
        logger.info(
            f"[REQUIREMENTS LEDGER] P0-3 SEEDING VERIFICATION: "
            f"All {seeded} requirements successfully seeded."
        )

    # Persist verification result to disk
    if project_dir:
        try:
            proj_dir = os.path.join(project_dir, ".agix.proj")
            os.makedirs(proj_dir, exist_ok=True)
            output_path = os.path.join(proj_dir, "seeding_verification.json")
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            logger.info(
                f"[REQUIREMENTS LEDGER] Persisted seeding verification to {output_path}"
            )
        except Exception as e:
            logger.warning(
                f"[REQUIREMENTS LEDGER] Failed to persist seeding verification: {e}"
            )

    return result


def add_requirement(
    agent_data: dict,
    text: str,
    category: str = "feature",
) -> str:
    """Add a single requirement dynamically to the ledger.

    Uses content-hash dedup to prevent duplicate requirements. If a requirement
    with the same text (case-insensitive, whitespace-normalized) already exists,
    returns the existing REQ ID instead of creating a new one.

    RCA-357 Fix: Previously this was a blind append, causing the LLM's
    'update' action calls to create duplicates when it tried to "align"
    requirements with custom GUIDs.

    Args:
        agent_data: The agent.data dict
        text: The requirement description
        category: Requirement category (feature, integration, url, etc.)

    Returns:
        The requirement ID — either existing (if duplicate) or new
    """
    from python.helpers.requirements_persistence import _ensure_ledger
    ledger = _ensure_ledger(agent_data)
    existing = ledger.get("requirements", [])

    # RCA-357: Content-hash dedup — check if identical text already exists
    normalized_new = text.strip().lower()
    if normalized_new:
        for req in existing:
            existing_text = req.get("text", "").strip().lower()
            if existing_text == normalized_new:
                logger.info(
                    f"[REQUIREMENTS LEDGER] Dedup: '{text[:60]}' already exists "
                    f"as {req['id']}. Returning existing ID."
                )
                return req["id"]

    req_id = _generate_req_id(text)

    ledger["requirements"].append({
        "id": req_id,
        "text": text,
        "category": category,
        "status": "pending",
        "assigned_to": [],
    })

    logger.info(
        f"[REQUIREMENTS LEDGER] Added dynamic requirement {req_id}: {text[:80]}"
    )
    return req_id


def _infer_category(text: str) -> str:
    """Infer requirement category from text content.

    Examines each success criterion and assigns the best-fit category
    so verifiers can apply the right verification strategy.

    Priority order (first match wins):
        url                  — text contains a URL
        config               — text mentions env vars, keys, secrets, configuration
        deployment           — text mentions deploy/push/host/CI-CD (RCA-340)
        compliance           — text mentions legal/regulatory constraints (RCA-232)
        content_constraint   — text specifies exact/literal content (RCA-232)
        integration_endpoint — text references a specific API endpoint (RCA-232)
        integration          — text mentions integration/api/sdk/connect keywords
        model                — text mentions data model, schema, entity, database
        page                 — text mentions page, route, section, panel, screen, view
        feature              — default for anything else

    Returns:
        Category string.
    """
    text_lower = text.lower()

    # URL check — highest priority
    if re.search(r'https?://', text):
        return "url"

    # Config keywords — env vars, secrets, API keys
    if re.search(r'\b(?:env(?:ironment)?|config(?:uration)?|secret|'
                 r'api[_\s]?key|variable|\.env|credential)\b', text_lower):
        return "config"

    # ── RCA-340 Fix: Deployment — infrastructure/hosting instructions ──
    # Must come before compliance/integration to prevent "deploy to Vercel"
    # from being categorized as "integration" or "feature".
    if re.search(r'\b(?:deploy|push\s+to|host\s+on|publish|ship|'
                 r'ci[/\s]?cd|pipeline|github\s+(?:repo|push)|'
                 r'vercel|netlify|railway|heroku|'
                 r'docker|kubernetes|k8s)\b', text_lower):
        return "deployment"

    # ── RCA-232 Fix 1: Compliance — legal/regulatory constraints ──
    # Must come before integration/page to prevent CAN-SPAM, GDPR, TCPA
    # requirements from being silently dropped as "feature".
    if re.search(r'\b(?:can[_\s-]?spam|tcpa|gdpr|ccpa|'
                 r'privacy(?:\s+policy)?|unsubscribe|'
                 r'opt[_\s-]?(?:out|in)|consent|'
                 r'terms\s+of\s+service|cookie|'
                 r'data\s+(?:protection|retention)|'
                 r'legal|regulatory)\b', text_lower):
        return "compliance"

    # ── RCA-232 Fix 1: Content constraint — exact/literal text requirements ──
    # Must come before page to prevent "footer must display exact text" from
    # being categorized as just "page".
    if re.search(r'\b(?:must\s+(?:display|show|contain|include)\s+'
                 r'(?:exact|literal))\b', text_lower) or \
       re.search(r'©|copyright', text_lower):
        return "content_constraint"

    # ── RCA-232 Fix 1: Integration endpoint — specific API route references ──
    # Must come before generic integration to distinguish "webhook at /api/x"
    # from generic "Stripe integration". Endpoints need route verification.
    if re.search(r'\b(?:webhook|callback\s*(?:url|endpoint)?)\b', text_lower) or \
       re.search(r'/api/', text_lower):
        return "integration_endpoint"

    # Integration keywords — any external service/API/SDK reference
    if re.search(r'\b(?:integrat|api\b|sdk|connect|'
                 r'third[_\s-]?party|external\s+service)\w*\b', text_lower):
        return "integration"

    # Data model keywords — schemas, entities, database structures
    if re.search(r'\b(?:model|schema|entity|table|database|field|'
                 r'column|migration|relation(?:ship)?)\b', text_lower):
        return "model"

    # Page/route keywords — UI surfaces
    if re.search(r'\b(?:page|route|section|panel|screen|view|'
                 r'tab|modal|dialog|form|dashboard|landing)\b', text_lower):
        return "page"

    # Default
    return "feature"
