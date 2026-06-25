"""
Requirement ID Normalizer — Canonicalize REQ IDs to REQ-NNN format.

Root Cause (RCA-MSR-Smoke-1779240828): The orchestrator generates REQ IDs
with variable padding (REQ-1, REQ-01, REQ-001) depending on which code path
creates them. The delegation message injection and acceptance criteria injector
do exact-match lookups against the ledger. When the padding doesn't match,
the lookup silently fails and the subordinate gets ZERO requirements context.

Fix: All numeric REQ IDs normalized to 3-digit zero-padded format (REQ-NNN).
GUID-style IDs (REQ-a1b2c3d4) pass through unchanged.

Used by:
- acceptance_criteria_injector.py (req_map building)
- delegation_message.py (requirements block injection)
- requirements_ledger.py (storage normalization)
"""

from __future__ import annotations

import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("agix.req_id_normalizer")

# Pattern: REQ- followed by purely numeric digits (1, 01, 001, etc.)
_NUMERIC_REQ_PATTERN = re.compile(r'^(REQ)-(\d+)$', re.IGNORECASE)


def normalize_req_id(req_id: str) -> str:
    """Normalize a requirement ID to canonical REQ-NNN format.

    Rules:
    - REQ-1 → REQ-001 (3-digit zero-padded)
    - REQ-01 → REQ-001
    - REQ-001 → REQ-001 (idempotent)
    - REQ-1234 → REQ-1234 (no truncation for 4+ digits)
    - REQ-a1b2c3d4 → REQ-a1b2c3d4 (GUID passthrough)
    - req-1 → REQ-001 (case normalize prefix)
    - Empty string → empty string
    - Non-REQ IDs → passthrough

    Args:
        req_id: The requirement ID string to normalize.

    Returns:
        The normalized requirement ID.
    """
    if not req_id:
        return req_id

    match = _NUMERIC_REQ_PATTERN.match(req_id.strip())
    if not match:
        return req_id

    prefix = "REQ"  # Always uppercase
    number = int(match.group(2))
    # Pad to at least 3 digits
    return f"{prefix}-{number:03d}"


def normalize_req_ids(req_ids: Optional[List[str]]) -> List[str]:
    """Normalize a list of requirement IDs.

    Args:
        req_ids: List of requirement IDs, or None.

    Returns:
        List of normalized IDs, or empty list if input was None/empty.
    """
    if not req_ids:
        return []
    return [normalize_req_id(rid) for rid in req_ids]


def build_normalized_req_map(requirements: List[Dict]) -> Dict[str, Dict]:
    """Build a requirement lookup map that handles all ID format variants.

    Creates entries for BOTH the canonical form AND common short forms,
    so lookups work regardless of whether the caller uses REQ-1, REQ-01,
    or REQ-001.

    Args:
        requirements: List of requirement dicts with 'id' key.

    Returns:
        Dict mapping all possible ID formats to the requirement dict.
    """
    req_map: Dict[str, Dict] = {}

    for req in requirements:
        raw_id = req.get("id", "")
        if not raw_id:
            continue

        # 1. Store by canonical normalized form
        canonical = normalize_req_id(raw_id)
        req_map[canonical] = req

        # 2. Store by original raw form (in case it's already the lookup key)
        req_map[raw_id] = req

        # 3. For numeric IDs, also store short forms so any format works
        match = _NUMERIC_REQ_PATTERN.match(raw_id.strip())
        if match:
            number = int(match.group(2))
            # Store all padding variants: REQ-1, REQ-01, REQ-001
            for width in range(1, max(4, len(str(number)) + 1)):
                variant = f"REQ-{number:0{width}d}"
                if variant not in req_map:
                    req_map[variant] = req

    return req_map
