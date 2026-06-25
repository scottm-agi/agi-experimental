"""
Requirements Persistence & Initialization

Extracted from requirements_ledger.py during P4 modularization (Phase 1.1).
Contains ledger initialization, disk persistence, crash recovery rehydration,
legacy migration, and delegation deduplication hashing.

Functions:
    _dedup_hash            — Content hash for delegation deduplication
    _ensure_ledger         — Initialize/validate ledger structure in agent.data
    _rebuild_dedup_hashes  — Rebuild dedup map from existing delegations
    _sanitize_all_strings  — Deep recursive string sanitization for API keys
    persist_ledger_to_project — Write clean ledger snapshot to disk
    load_ledger_from_project  — Load full ledger from disk (crash recovery)
    rehydrate_requirements_ledger — Selective requirements-only rehydration
    migrate_legacy_ledger  — Migrate old _delegation_task_ledger format
    get_delegation_ledger_for_gate — Backward-compat flat list for gate code
"""

import json
import logging
import os
from typing import Dict, List

from python.helpers.planning_paths import get_path as _planning_path
from python.helpers.requirements_stage import ensure_stage_status

logger = logging.getLogger("agix.requirements_ledger")


# ─── Delegation Deduplication ────────────────────────────────────────────


def _dedup_hash(profile: str, message: str) -> str:
    """Generate a content hash for delegation deduplication.

    Uses profile + first 200 chars of message (matching the truncation
    used for storage) to create a deterministic fingerprint.
    """
    from python.helpers.hashing import content_hash
    content = f"{profile}:{message[:200]}"
    return content_hash(content)


# ─── Ledger Initialization ──────────────────────────────────────────────


def _ensure_ledger(agent_data: dict) -> dict:
    """Ensure _requirements_ledger exists in agent_data and return it."""
    if "_requirements_ledger" not in agent_data:
        agent_data["_requirements_ledger"] = {
            "requirements": [],
            "delegations": [],
            "gate_failures": [],
            "dedup_hashes": {},
        }
    ledger = agent_data["_requirements_ledger"]
    if not isinstance(ledger, dict):
        agent_data["_requirements_ledger"] = {
            "requirements": [],
            "delegations": [],
            "gate_failures": [],
            "dedup_hashes": {},
        }
        ledger = agent_data["_requirements_ledger"]
    if "requirements" not in ledger:
        ledger["requirements"] = []
    if "delegations" not in ledger:
        ledger["delegations"] = []
    if "gate_failures" not in ledger:
        ledger["gate_failures"] = []
    if "dedup_hashes" not in ledger:
        ledger["dedup_hashes"] = {}
    # F-13: Auto-rebuild dedup hashes when delegations exist but hashes are empty.
    # This catches the context-reconstruction case where delegations survive
    # a disk round-trip but dedup_hashes were missing.
    if not ledger["dedup_hashes"] and ledger["delegations"]:
        _rebuild_dedup_hashes(agent_data)

    # RCA-ITR41 F-2: Auto-rehydrate requirements from disk when in-memory
    # ledger has empty requirements but a project directory is available.
    # This wires the existing rehydrate_requirements_ledger() function
    # (which was built in ITR-39 FIX-8 with 8 passing tests but had ZERO
    # production callers — 100% dead code until this wiring).
    if not ledger["requirements"]:
        project_dir = agent_data.get("_active_project_dir", "")
        if project_dir:
            try:
                rehydrated = rehydrate_requirements_ledger(agent_data, project_dir)
                if rehydrated:
                    logger.warning(
                        "[REQUIREMENTS LEDGER] ITR41-F2: Auto-rehydrated "
                        "requirements via _ensure_ledger (was dead code, now wired)"
                    )
            except Exception as rh_err:
                logger.debug(
                    f"[REQUIREMENTS LEDGER] ITR41-F2: Rehydration failed "
                    f"(non-fatal): {rh_err}"
                )

    # ADR-086: Migrate all requirements to stage-keyed status model.
    # This runs on every _ensure_ledger() call — idempotent because
    # ensure_stage_status() is a no-op when stage_status already exists.
    for req in ledger.get("requirements", []):
        ensure_stage_status(req)

    return ledger


def _rebuild_dedup_hashes(agent_data: dict) -> None:
    """Rebuild the dedup_hashes map from existing delegations.

    F-13: After context reconstruction (disk round-trip), dedup_hashes
    may be empty. This causes record_delegation to re-append entries that
    already exist, leading to inflation.

    R3 Fix: Prefers stored content_hash in delegation records when available,
    falling back to recomputation from profile+message_summary. This makes
    the rebuild resilient to changes in the hash function.

    Fix: Walk existing delegations and rebuild the dedup map.
    Must be called after _ensure_ledger guarantees structure.

    Args:
        agent_data: The agent.data dict containing _requirements_ledger
    """
    # Use direct access — do NOT call _ensure_ledger here to avoid recursion
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return

    # Ensure _dedup_hashes key exists
    if "dedup_hashes" not in ledger:
        ledger["dedup_hashes"] = {}

    delegations = ledger.get("delegations", [])
    hashes = ledger["dedup_hashes"]

    for delegation in delegations:
        profile = delegation.get("profile", "")
        message_summary = delegation.get("message_summary", "")
        delegation_id = delegation.get("id", "")
        status = delegation.get("status", "")
        # Skip failed/escalated delegations — their dedup hashes were
        # intentionally cleared by record_gate_failure/mark_delegation_escalated
        # to allow retries. Rebuilding them would prevent re-delegation.
        if status in ("failed", "escalated"):
            continue
        if profile and message_summary and delegation_id:
            # R3 Fix: Prefer stored content_hash when available.
            # This avoids recomputation fragility if _dedup_hash() changes.
            stored_hash = delegation.get("content_hash")
            if stored_hash:
                content_hash = stored_hash
            else:
                content_hash = _dedup_hash(profile, message_summary)
            hashes[content_hash] = delegation_id

    logger.info(
        f"[REQUIREMENTS LEDGER] Rebuilt {len(hashes)} dedup hashes "
        f"from {len(delegations)} existing delegations"
    )


# ─── ISS-1: Deep String Sanitizer ────────────────────────────────────────


def _sanitize_all_strings(obj):
    """Recursively sanitize all string values in a dict/list structure.

    ISS-1 FIX: Walks the entire snapshot and applies API key pattern
    redaction to every string value. This catches keys in delegation
    message_summary, response_summary, gate_failures, and any future
    fields — without needing to know the schema.

    Also strips §§secret() and §§REDACTED_PATTERN markers.

    Args:
        obj: A dict, list, or primitive value.

    Returns:
        A new structure with all string values sanitized.
    """
    import re  # noqa: F811
    from python.helpers.requirements_sanitizer import (
        _API_KEY_PATTERNS,
        _REDACTION_TOKEN_PATTERN,
    )

    if isinstance(obj, dict):
        return {k: _sanitize_all_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_all_strings(item) for item in obj]
    elif isinstance(obj, str):
        text = obj
        # Pass 1: Strip §§secret() markers
        text = _REDACTION_TOKEN_PATTERN.sub("", text)
        # Pass 2: Strip API key patterns
        for pattern in _API_KEY_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text
    else:
        return obj


# ─── Disk Persistence ────────────────────────────────────────────────────


def persist_ledger_to_project(agent_data: dict, project_dir: str = None) -> None:
    """Persist a clean JSON snapshot of the ledger to the project directory.

    Writes requirements, delegations, and dedup_hashes (excluding internal
    fields starting with _) to {project_dir}/requirements_ledger.json.

    Args:
        agent_data: The agent.data dict containing _requirements_ledger
        project_dir: Absolute path to the project directory. Skipped if
                     None or empty string.
    """
    if not project_dir:
        return

    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return

    # Build clean snapshot excluding internal fields (keys starting with _)
    clean_snapshot = {
        key: value
        for key, value in ledger.items()
        if not key.startswith("_")
    }

    # ISS-1 FIX (P0 CRITICAL): Sanitize ALL string values before writing to disk.
    # Root cause: persist_ledger_to_project() wrote raw ledger data without
    # sanitization. The sanitizer only existed in _handle_save_manifest() path
    # in requirements.py, leaving this direct-write path unsanitized.
    # Fix: Apply sanitize_ledger() to requirements AND deep-scan all strings
    # for API key patterns. This is the universal write gate — ALL code paths
    # that write requirements_ledger.json go through this function.
    try:
        from python.helpers.requirements_sanitizer import sanitize_ledger
        # Sanitize requirements list (handles text field + §§secret markers)
        if "requirements" in clean_snapshot and isinstance(clean_snapshot["requirements"], list):
            clean_snapshot["requirements"] = sanitize_ledger(clean_snapshot["requirements"])
        # Deep-sanitize ALL string values (catches keys in delegations,
        # response_summary, message_summary, and any future fields)
        clean_snapshot = _sanitize_all_strings(clean_snapshot)
    except Exception as san_err:
        logger.warning(
            f"[REQUIREMENTS LEDGER] ISS-1 sanitization failed (non-fatal, "
            f"proceeding with raw data): {san_err}"
        )

    output_path = _planning_path(project_dir, "requirements_ledger")
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(clean_snapshot, f, indent=2, default=str)
        logger.info(
            f"[REQUIREMENTS LEDGER] Persisted ledger to {output_path} "
            f"({len(clean_snapshot.get('requirements', []))} requirements, "
            f"{len(clean_snapshot.get('delegations', []))} delegations)"
        )
    except Exception as e:
        agent_data["_ledger_persist_failures"] = agent_data.get("_ledger_persist_failures", 0) + 1
        logger.warning(
            f"[REQUIREMENTS LEDGER] Failed to persist ledger to "
            f"{output_path}: {e} (failure #{agent_data['_ledger_persist_failures']})"
        )


def load_ledger_from_project(agent_data: dict, project_dir: str = None) -> bool:
    """Load the full requirements ledger from disk into agent.data.

    Unlike rehydrate_requirements_ledger() which only restores requirements,
    this loads the ENTIRE ledger (requirements + delegations + gate_failures
    + dedup_hashes) from the project's requirements_ledger.json.

    Used for P2-8 crash recovery when agent.data is completely empty.

    Args:
        agent_data: The agent.data dict to populate
        project_dir: Absolute path to project directory

    Returns:
        True if ledger was successfully loaded from disk, False otherwise
    """
    if not project_dir:
        return False

    ledger_paths = [
        _planning_path(project_dir, "requirements_ledger"),
        os.path.join(project_dir, "requirements_ledger.json"),
        os.path.join(project_dir, "requirements-ledger.json"),
    ]

    disk_data = None
    for path in ledger_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
                break
            except (json.JSONDecodeError, IOError, OSError) as e:
                logger.warning(
                    f"[REQUIREMENTS LEDGER] P2-8: Could not read "
                    f"{path}: {e}"
                )
                continue

    if not disk_data or not isinstance(disk_data, dict):
        return False

    # Load into existing ledger structure
    ledger = _ensure_ledger(agent_data)

    # Only load requirements if memory is empty
    disk_reqs = disk_data.get("requirements", [])
    if disk_reqs and not ledger.get("requirements"):
        ledger["requirements"] = disk_reqs

    # Only load delegations if memory is empty
    disk_dels = disk_data.get("delegations", [])
    if disk_dels and not ledger.get("delegations"):
        ledger["delegations"] = disk_dels

    # Always load dedup hashes from disk (they don't survive chat.json)
    disk_hashes = disk_data.get("dedup_hashes", {})
    if disk_hashes:
        ledger["dedup_hashes"] = disk_hashes

    # Load gate failures if present on disk
    disk_failures = disk_data.get("gate_failures", [])
    if disk_failures and not ledger.get("gate_failures"):
        ledger["gate_failures"] = disk_failures

    logger.warning(
        f"[REQUIREMENTS LEDGER] P2-8 CRASH RECOVERY: Loaded full ledger "
        f"from disk ({len(ledger.get('requirements', []))} requirements, "
        f"{len(ledger.get('delegations', []))} delegations, "
        f"{len(ledger.get('dedup_hashes', {}))} dedup hashes)"
    )
    return True


def rehydrate_requirements_ledger(
    agent_data: dict,
    project_dir: str,
) -> bool:
    """Rehydrate in-memory requirements ledger from on-disk file after crash.

    ITR-39 FIX-8: When an agent context is restored after a crash, the
    in-memory _requirements_ledger may be empty because the crash happened
    before the ledger was persisted to chat.json. This function detects
    that condition and loads requirements from the project's on-disk
    requirements_ledger.json file.

    IMPORTANT: Only rehydrates the 'requirements' list. Delegations,
    gate_failures, and dedup_hashes that survived in agent.data are
    preserved — we do NOT overwrite them from disk since they may be
    more current than the disk snapshot.

    Args:
        agent_data: The agent.data dict containing _requirements_ledger.
        project_dir: Absolute path to the project directory containing
                     requirements_ledger.json.

    Returns:
        True if rehydration occurred (memory was empty, disk had data).
        False if no rehydration was needed or possible.
    """
    # Step 1: Check if in-memory ledger already has requirements
    ledger = _ensure_ledger(agent_data)
    if ledger.get("requirements"):
        # Memory already has data — no rehydration needed
        return False

    # Step 2: Check if disk file exists
    if not project_dir or not os.path.isdir(project_dir):
        return False

    ledger_path = _planning_path(project_dir, "requirements_ledger")
    if not os.path.isfile(ledger_path):
        return False

    # Step 3: Read and validate disk file
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(
            f"[REQUIREMENTS LEDGER] FIX-8: Could not read "
            f"requirements_ledger.json for rehydration: {e}"
        )
        return False

    if not isinstance(disk_data, dict):
        return False

    disk_requirements = disk_data.get("requirements", [])
    if not disk_requirements or not isinstance(disk_requirements, list):
        return False

    # Step 4: Rehydrate ONLY the requirements list — preserve everything else
    ledger["requirements"] = disk_requirements
    logger.warning(
        f"[REQUIREMENTS LEDGER] FIX-8 CRASH RECOVERY: Rehydrated "
        f"{len(disk_requirements)} requirements from disk "
        f"(in-memory ledger was empty after restart)"
    )
    return True


# ─── Legacy Migration ───────────────────────────────────────────────────


def migrate_legacy_ledger(agent_data: dict) -> None:
    """Migrate old flat _delegation_task_ledger to new format.

    Old format: [{"profile": "...", "message_summary": "...", "status": "..."}]
    New format: stored under _requirements_ledger.delegations with added fields.

    After migration, the old _delegation_task_ledger key is removed.

    RCA-451 defense-in-depth: If _requirements_ledger already has delegations,
    this is a NO-OP (just deletes the old key). This prevents the feedback loop
    where get_delegation_ledger_for_gate() writes entries back to
    _delegation_task_ledger, then this function re-appends them, doubling the
    count on every restart.
    """
    old_ledger = agent_data.get("_delegation_task_ledger")
    if not old_ledger or not isinstance(old_ledger, list):
        return

    ledger = _ensure_ledger(agent_data)

    # RCA-451: If _requirements_ledger already has delegations, this is NOT
    # a true legacy migration — it's the backward-compat writeback recreating
    # the old key. Skip the append to prevent exponential inflation.
    existing_delegations = ledger.get("delegations", [])
    if existing_delegations:
        logger.info(
            f"[REQUIREMENTS LEDGER] Skipping legacy migration — "
            f"_requirements_ledger already has {len(existing_delegations)} "
            f"delegations. Deleting stale _delegation_task_ledger "
            f"({len(old_ledger)} entries)."
        )
        del agent_data["_delegation_task_ledger"]
        return

    for i, entry in enumerate(old_ledger, start=1):
        if not isinstance(entry, dict):
            continue
        ledger["delegations"].append({
            "id": f"delegation-{i}",
            "profile": entry.get("profile", "unknown"),
            "message_summary": entry.get("message_summary", ""),
            "status": entry.get("status", "completed"),
            "requirement_ids": [],  # Legacy entries have no requirement tracking
            "response_summary": "",
        })

    # Remove old key
    del agent_data["_delegation_task_ledger"]

    logger.info(
        f"[REQUIREMENTS LEDGER] Migrated {len(old_ledger)} legacy ledger entries"
    )


# ─── Backward-Compatible Ledger Access ──────────────────────────────────


def get_delegation_ledger_for_gate(agent_data: dict) -> List[Dict]:
    """Get delegation entries in the format expected by existing gate code.

    The gate reads _delegation_task_ledger as a flat list of
    {profile, message_summary, status}. This function returns the new
    ledger's delegations in that same format for backward compatibility.

    Returns:
        List of dicts compatible with the old ledger format
    """
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        # Fall back to old format if it exists
        old = agent_data.get("_delegation_task_ledger", [])
        return old if isinstance(old, list) else []

    return [
        {
            "profile": d.get("profile", "unknown"),
            "message_summary": d.get("message_summary", ""),
            "status": d.get("status", "in_progress"),
        }
        for d in ledger.get("delegations", [])
    ]
