"""
Line-Item Expander — Quantity, Compound, and Sub-Feature Decomposition

Functions that expand aggregated requirements into atomic sub-requirements:
- expand_quantity_requirements() — "3-email drip" → 3 sub-items
- expand_quantity_weighted_candidates() — same for WeightedCandidate
- expand_compound_sub_features() — "A + B + C" → 3 sub-items
- _try_split_compound() — compound separator detection
"""

import logging
from typing import List, Optional

from python.helpers.line_item_patterns import (
    _AND_SEP_RE,
    _COMPOUND_EXEMPT_CATEGORIES,
    _COMPOUND_MIN_LENGTH,
    _COMPOUND_MIN_PARTS,
    _COMPOUND_PART_MIN_LENGTH,
    _ENUM_AFTER_MARKER_RE,
    _PLUS_SEP_RE,
    _SEMI_SEP_RE,
    MAX_EXPANSION,
    QUANTITY_PATTERN,
)

logger = logging.getLogger("agix.prompt_line_item_extractor")


# Import LineItem type for annotations — use TYPE_CHECKING to avoid
# circular imports since LineItem lives in the hub file.
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from python.helpers.prompt_line_item_extractor import LineItem as _LineItem


def expand_quantity_requirements(items: "List[_LineItem]") -> "List[_LineItem]":
    """Expand requirements with quantity patterns into N sub-requirements.

    F-5 (ITR-21): When a requirement says "3-email drip sequence", this function
    expands it into 3 sub-requirements (Email 1 of 3, Email 2 of 3, Email 3 of 3)
    so the architect treats each as a separate work package.

    Items without quantity patterns pass through unchanged. Quantity of 1 is
    not expanded. Quantities above MAX_EXPANSION are capped with a warning.

    Args:
        items: List of LineItem objects from extract_line_items().

    Returns:
        New list with quantity-bearing items expanded into sub-requirements.
    """
    # Import here to get the actual class (not just annotation)
    from python.helpers.prompt_line_item_extractor import LineItem

    expanded: list = []
    for item in items:
        match = QUANTITY_PATTERN.search(item.text)
        if match and int(match.group(1)) > 1:
            raw_count = int(match.group(1))
            count = min(raw_count, MAX_EXPANSION)
            if raw_count > MAX_EXPANSION:
                logger.warning(
                    f"[F-5] Quantity {raw_count} for '{item.text[:60]}' "
                    f"capped at {MAX_EXPANSION}"
                )
            item_type = match.group(2)
            for i in range(1, count + 1):
                sub = LineItem(
                    id=f"{item.id}.{i}",
                    text=f"{item_type.capitalize()} {i} of {count}: {item.text}",
                    category=item.category,
                    source_line=item.source_line,
                    priority=item.priority,
                    confidence=item.confidence,
                    extrapolated=item.extrapolated,
                    parent_id=item.id,
                    sub_index=i,
                )
                expanded.append(sub)
        else:
            expanded.append(item)
    return expanded


def expand_quantity_weighted_candidates(
    candidates: "List['WeightedCandidate']",
) -> "List['WeightedCandidate']":
    """Expand weighted candidates with quantity patterns into N sub-candidates.

    SS-3 (ITR-23): extract_weighted_candidates() is a standalone 17-pass pipeline
    that never calls expand_quantity_requirements(). This function bridges that
    gap by applying the same QUANTITY_PATTERN regex to WeightedCandidate.text
    and expanding N-quantity items into N sub-candidates.

    Items without quantity patterns pass through unchanged. Quantity of 1 is
    not expanded. Quantities above MAX_EXPANSION are capped with a warning.

    Args:
        candidates: List of WeightedCandidate objects.

    Returns:
        New list with quantity-bearing candidates expanded into sub-candidates.
    """
    from python.helpers.weighted_candidate import WeightedCandidate

    expanded: list = []
    for candidate in candidates:
        match = QUANTITY_PATTERN.search(candidate.text)
        if match and int(match.group(1)) > 1:
            raw_count = int(match.group(1))
            count = min(raw_count, MAX_EXPANSION)
            if raw_count > MAX_EXPANSION:
                logger.warning(
                    f"[SS-3] Quantity {raw_count} for '{candidate.text[:60]}' "
                    f"capped at {MAX_EXPANSION}"
                )
            item_type = match.group(2)
            for i in range(1, count + 1):
                sub = WeightedCandidate(
                    text=f"{item_type.capitalize()} {i} of {count}: {candidate.text}",
                    category=candidate.category,
                    confidence=candidate.confidence,
                    source_line=candidate.source_line,
                    structural_markers={
                        **candidate.structural_markers,
                        "is_quantity_expanded": True,
                        "parent_text": candidate.text[:80],
                        "sub_index": i,
                        "total_count": count,
                    },
                )
                expanded.append(sub)
        else:
            expanded.append(candidate)
    return expanded


def expand_compound_sub_features(items: "List[_LineItem]") -> "List[_LineItem]":
    """Decompose compound feature descriptions into atomic sub-requirements.

    Fix 4 (ISS-03): When a feature description contains multiple sub-features,
    e.g., "review capture + review responses + reputation protection",
    this function breaks them into individual LineItems with parent_id links.

    Separators detected:
    - " + " (plus sign with spaces)
    - " and " (word boundary)
    - "; " (semicolons)
    - "— item, item, item" or ": item, item, item" (enumeration after marker)

    Only applies to category='feature' (and similar buildable categories).
    Items in exempt categories (url, copy, config, deployment) pass through.

    Args:
        items: List of LineItem objects.

    Returns:
        New list with compound items expanded into sub-requirements.
    """
    from python.helpers.prompt_line_item_extractor import LineItem

    expanded: list = []

    for item in items:
        # Skip exempt categories and short text
        if item.category in _COMPOUND_EXEMPT_CATEGORIES:
            expanded.append(item)
            continue

        if len(item.text) < _COMPOUND_MIN_LENGTH:
            expanded.append(item)
            continue

        # Skip items that already have a parent (sub-requirements from quantity expansion)
        if item.parent_id:
            expanded.append(item)
            continue

        parts = _try_split_compound(item.text)
        if parts and len(parts) >= _COMPOUND_MIN_PARTS:
            # Filter out tiny fragments
            valid_parts = [p.strip() for p in parts if len(p.strip()) >= _COMPOUND_PART_MIN_LENGTH]
            if len(valid_parts) >= _COMPOUND_MIN_PARTS:
                for i, part in enumerate(valid_parts, 1):
                    sub = LineItem(
                        id=f"{item.id}.{i}",
                        text=part,
                        category=item.category,
                        source_line=item.source_line,
                        priority=item.priority,
                        confidence=item.confidence,
                        extrapolated=item.extrapolated,
                        parent_id=item.id,
                        sub_index=i,
                    )
                    expanded.append(sub)
                logger.info(
                    f"[FIX-4] Expanded compound feature '{item.text[:60]}' into "
                    f"{len(valid_parts)} sub-requirements"
                )
                continue

        # Not compound — pass through unchanged
        expanded.append(item)

    return expanded


def _try_split_compound(text: str) -> Optional[List[str]]:
    """Try to split compound text into sub-parts using various separators.

    Returns None if no compound pattern detected.
    Returns list of parts if a compound separator is found.

    Priority order (most specific to least):
    1. Plus sign separator (" + ")
    2. Semicolons (" ; ")
    3. Enumeration after marker ("— a, b, c" or ": a, b, c")
    4. "and" separator (only when 2+ 'and' present or combined with comma)
    """
    # 1. Plus sign: "A + B + C"
    if '+' in text:
        parts = _PLUS_SEP_RE.split(text)
        if len(parts) >= _COMPOUND_MIN_PARTS:
            return parts

    # 2. Semicolons: "A; B; C"
    if ';' in text:
        parts = _SEMI_SEP_RE.split(text)
        if len(parts) >= _COMPOUND_MIN_PARTS:
            return parts

    # 3. Enumeration after — or : marker: "3-email sequence — intro, follow-up, final"
    enum_match = _ENUM_AFTER_MARKER_RE.search(text)
    if enum_match:
        after_marker = enum_match.group(1)
        # Split by commas
        parts = [p.strip() for p in after_marker.split(',')]
        if len(parts) >= _COMPOUND_MIN_PARTS:
            return parts

    # 4. "and" separator: "A and B and C" or "A, B, and C"
    # Only split on "and" if text contains 2+ "and" instances OR
    # "and" combined with commas (indicating a list)
    and_count = len(_AND_SEP_RE.findall(text))
    comma_count = text.count(',')
    if and_count >= 2 or (and_count >= 1 and comma_count >= 1):
        # Split on both commas and "and"
        # First replace " and " with "," then split
        normalized = _AND_SEP_RE.sub(', ', text)
        parts = [p.strip() for p in normalized.split(',') if p.strip()]
        if len(parts) >= _COMPOUND_MIN_PARTS:
            return parts

    return None
