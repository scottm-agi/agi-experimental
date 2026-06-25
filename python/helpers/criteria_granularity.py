"""
Criteria Granularity — Compound Criteria Detection & Splitting
================================================================

Deterministic post-check that ensures requirements are granular enough
for machine verification. Detects criteria that contain multiple features
lumped together with 'and', commas, etc., and can split them into
independently-verifiable atomic criteria.

Used by:
- goal_state_manager.py (post-extraction validation)
- _22_multiagentdev_completion_gate.py (seeding quality check)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Patterns that indicate compound criteria (two+ features in one)
_COMPOUND_PATTERNS = [
    # "manage X and generate Y" — two verbs connected by 'and'
    re.compile(
        r'\b(manage|create|build|implement|generate|capture|add|deploy|enable|configure)'
        r'\b.*\band\b.*'
        r'(manage|create|build|implement|generate|capture|add|deploy|enable|configure)\b',
        re.IGNORECASE,
    ),
    # "X management and Y generation" — two domain nouns connected by 'and'
    # Catches: "Review management and response generation"
    # Catches: "Review management and AI response generation"
    re.compile(
        r'\b\w+\s+(management|generation|integration|processing|tracking|automation|configuration|validation|monitoring)\b'
        r'\s+and\s+'
        r'(?:\w+\s+){0,2}(management|generation|integration|processing|tracking|automation|configuration|validation|monitoring)\b',
        re.IGNORECASE,
    ),
    # Comma-separated features: "X, Y, and Z integration"
    re.compile(
        r',\s*\w+[\w\s]*,\s*(and\s+)?\w+',
        re.IGNORECASE,
    ),
]

# Patterns that are NATURAL 'and' usage (not compound)
_NATURAL_AND_PATTERNS = [
    re.compile(r'\bsearch\s+and\s+filter\b', re.IGNORECASE),
    re.compile(r'\bdrag\s+and\s+drop\b', re.IGNORECASE),
    re.compile(r'\bcopy\s+and\s+paste\b', re.IGNORECASE),
    re.compile(r'\bread\s+and\s+write\b', re.IGNORECASE),
    re.compile(r'\brequest\s+and\s+response\b', re.IGNORECASE),
    re.compile(r'\bname\s+and\s+address\b', re.IGNORECASE),
    re.compile(r'\bup\s+and\s+running\b', re.IGNORECASE),
]


def detect_compound_criteria(criteria: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect criteria that contain multiple features lumped together.

    Args:
        criteria: List of dicts with at least 'id' and 'text' keys.

    Returns:
        List of criteria dicts that are flagged as compound (should be split).
    """
    compounds = []
    for criterion in criteria:
        text = criterion.get("text", "")
        if _is_compound(text):
            compounds.append(criterion)
    return compounds


def _is_compound(text: str) -> bool:
    """Check if a criterion text is compound (contains multiple features)."""
    # First check natural 'and' patterns — if matched, it's NOT compound
    for nat_pat in _NATURAL_AND_PATTERNS:
        if nat_pat.search(text):
            return False

    # Then check compound patterns
    for comp_pat in _COMPOUND_PATTERNS:
        if comp_pat.search(text):
            return True

    return False


def split_compound_criterion(criterion: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Split a compound criterion into granular ones.

    Args:
        criterion: Dict with 'id' and 'text' keys.

    Returns:
        List of split criteria. If not compound, returns [criterion] unchanged.
    """
    text = criterion.get("text", "")
    parent_id = criterion.get("id", "REQ-000")

    if not _is_compound(text):
        return [criterion]

    # Strategy 1: Split on comma-separated lists: "X, Y, and Z"
    # Remove "and" from the last item
    normalized = re.sub(r',\s+and\s+', ', ', text)
    # Also handle "X and Y" without commas
    parts = re.split(r',\s*', normalized)

    if len(parts) < 2:
        # Try splitting on 'and' directly
        parts = re.split(r'\s+and\s+', text)

    if len(parts) < 2:
        return [criterion]

    # Clean up parts and generate new IDs
    splits = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue

        # Generate a stable sub-ID
        from python.helpers.hashing import content_hash_short
        sub_hash = content_hash_short(part, length=6)
        sub_id = f"REQ-{sub_hash}"

        splits.append({
            "id": sub_id,
            "text": part,
            "parent_id": parent_id,
        })

    return splits if splits else [criterion]


def validate_no_compound_criteria(criteria: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate a list of criteria and return a report.

    Returns:
        Dict with:
        - is_valid: bool (True if no compounds found)
        - compounds: list of compound criteria
        - suggested_splits: dict of id -> list of split criteria
    """
    compounds = detect_compound_criteria(criteria)

    suggested_splits = {}
    for comp in compounds:
        suggested_splits[comp["id"]] = split_compound_criterion(comp)

    return {
        "is_valid": len(compounds) == 0,
        "compounds": compounds,
        "suggested_splits": suggested_splits,
    }
