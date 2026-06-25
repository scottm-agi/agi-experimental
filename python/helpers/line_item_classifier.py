"""
Line-Item Classifier Functions — Requirement Classification & Validation

Classifier and helper functions extracted from the monolithic
prompt_line_item_extractor.py for maintainability. Includes:
- classify_requirement_text(): noise vs buildable classification
- is_valid_page_path(): page path validation
- score_ui_surface(): UI surface signal scoring
- _derive_page_name(): page route name derivation
- _content_hash(): deduplication hash
- _safe_split_lines(): safe line splitting
"""

import re
from typing import List

from python.helpers.line_item_constants import (
    MAX_LINE_LENGTH,
    _INVALID_PAGE_WORDS,
    _NOISE_PATTERNS,
    _ROUTE_STOP_WORDS,
    _SIGNAL_KEYWORDS,
    _STOP_WORDS,
    _UI_SURFACE_SIGNALS,
)

def classify_requirement_text(text: str) -> str:
    """Classify whether extracted text is a buildable requirement or noise/context.

    Layer 1 (deterministic): Uses regex patterns to detect common noise patterns
    in business documents — pricing rationale, competitive analysis, sales
    projections, daily operations descriptions, and legal background.

    The LLM agent (Layer 2) can override this classification.

    Args:
        text: The extracted text to classify.

    Returns:
        'buildable' if the text describes a concrete implementation requirement,
        'context' if the text is background information / noise.
    """
    for pattern in _NOISE_PATTERNS:
        if pattern.search(text):
            return 'context'
    return 'buildable'


def is_valid_page_path(path: str, source_text: str) -> bool:
    """Validate that a derived page path is a real page, not a prose artifact.

    L1 deterministic check. Rejects paths derived from common English words
    that are verbs, prepositions, or function words — NOT real page names.

    Args:
        path: The page path (e.g., '/audit', '/web', '/use').
        source_text: The source text this path was derived from (for context).

    Returns:
        True if the path is likely a real page, False if it's a prose artifact.
    """
    # Strip leading/trailing slashes, get the first segment
    clean = path.strip('/').lower()

    # Handle multi-segment paths (e.g., /api/unsubscribe) — check first segment only
    segments = clean.split('/')
    first_segment = segments[0] if segments else clean

    # Check against invalid word list
    if first_segment in _INVALID_PAGE_WORDS:
        return False

    # Too short to be a real page name (less than 3 chars)
    if len(first_segment) < 3:
        return False

    return True


def score_ui_surface(text: str) -> float:
    """Score how likely a feature text implies a UI page.

    Returns 0.0-1.0. Features scoring above UI_SURFACE_THRESHOLD
    should get a companion page requirement.
    """
    text_lower = text.lower()
    score = 0.0

    for keyword, weight in _UI_SURFACE_SIGNALS.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', text_lower):
            score += weight

    # Cap at 1.0
    return min(score, 1.0)


def _derive_page_name(feature_text: str) -> str:
    """Derive a page route name from a feature description.

    Returns the first significant word that is NOT a signal keyword
    or stop word. Falls back to the first word if nothing else matches.

    Examples:
        "outreach pipeline with filterable table" → "outreach"
        "discovery engine finds businesses" → "discovery"
        "analytics dashboard with metrics" → "analytics"
    """
    words = re.findall(r'[a-zA-Z]+', feature_text.lower())
    for word in words:
        if word not in _SIGNAL_KEYWORDS and word not in _STOP_WORDS and word not in _ROUTE_STOP_WORDS and len(word) > 2:
            return word
    # Fallback: first word with length > 2
    for word in words:
        if len(word) > 2:
            return word
    return words[0] if words else "page"


def _content_hash(text: str) -> str:
    """Generate a content hash for deduplication."""
    from python.helpers.hashing import content_hash_short
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return content_hash_short(normalized, length=12)


def _safe_split_lines(prompt: str) -> List[str]:
    """Split prompt into lines, capping each at MAX_LINE_LENGTH.

    Very long lines (>MAX_LINE_LENGTH chars) are split at sentence
    boundaries ('. ') to prevent catastrophic regex backtracking in
    patterns like _INTEGRATION_PHRASE_RE which exhibit O(n²) behavior.
    """
    raw_lines = prompt.split("\n")
    result: List[str] = []
    for line in raw_lines:
        if len(line) <= MAX_LINE_LENGTH:
            result.append(line)
        else:
            # Split at sentence boundaries for long lines
            segments = line.split(". ")
            buf = ""
            for seg in segments:
                candidate = (buf + ". " + seg) if buf else seg
                if len(candidate) > MAX_LINE_LENGTH and buf:
                    result.append(buf)
                    buf = seg
                else:
                    buf = candidate
            if buf:
                result.append(buf)
    return result


