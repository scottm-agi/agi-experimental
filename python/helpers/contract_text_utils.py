"""
Contract Text Utilities — Text processing helpers for prompt contract parsing.

Extracted from prompt_contract_parser.py during modularization.
Contains normalization, URL cleaning, name validation, section detection,
price confidence scoring, route generation, and feature categorization.

These utilities are consumed by contract_extractors.py and
prompt_contract_parser.py (the facade).
"""

import re

# ─── Prompt Normalization (RCA-259) ───────────────────────────────────


def _normalize_prompt_text(prompt: str) -> str:
    """Normalize prompt text by replacing literal escape sequences with real whitespace.

    RCA-259: Prompts captured from WebSocket/JSON payloads may contain literal
    two-character sequences like backslash-n instead of actual newline characters.
    This causes URL regex to gobble through line boundaries, producing corrupted
    assertions like 'https://example.com/page\\\\nNextLineText'.

    This function replaces literal \\\\r\\\\n, \\\\n, \\\\r, \\\\t with their actual
    whitespace equivalents BEFORE regex extraction runs.
    """
    # Replace literal escape sequences (two-character backslash+letter)
    # Order matters: \r\n before \n to avoid double-replacement
    prompt = prompt.replace("\\r\\n", "\n")
    prompt = prompt.replace("\\n", "\n")
    prompt = prompt.replace("\\r", "\r")
    prompt = prompt.replace("\\t", "\t")
    return prompt


def _clean_url(url: str) -> str:
    """Post-process an extracted URL to remove newline artifacts.

    Belt-and-suspenders: even after normalization, strip anything after
    a newline, carriage return, or whitespace that might have leaked through.
    Also strip common trailing punctuation.
    """
    # Truncate at any whitespace/control character
    for ch in ("\n", "\r", "\t", " "):
        if ch in url:
            url = url[:url.index(ch)]
    # Strip trailing punctuation that regex might have grabbed
    url = url.rstrip(".,;:!?)")
    return url


# Validate that a captured name is actually Title Case (not just IGNORECASE artifact)
def _is_valid_person_name(name: str) -> bool:
    """Check if name looks like a proper noun (Title Case, 2+ words)."""
    words = name.split()
    if len(words) < 2:
        return False
    return all(w[0].isupper() and w[1:].islower() for w in words if len(w) > 1)


# ─── Section Context Detection ────────────────────────────────────────

# F-8 (ITR-11): Section-awareness for competitor price filtering
_COMPARISON_SECTION_KEYWORDS = [
    'competitive', 'comparison', 'landscape', 'alternatives',
    'competitor', 'vs', 'versus',
]

# Matches markdown headings (# ...), ALL-CAPS lines with colon, or Title Case lines (2+ words) with colon
_SECTION_HEADING_RE = re.compile(
    r'^(?:#{1,6}\s+(.+)|([A-Z][A-Z\s]{2,}):|([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s*:)\s*$',
    re.MULTILINE,
)


def _detect_section_context(prompt: str, char_pos: int) -> str:
    """Detect the nearest preceding section heading for a character position.

    F-8 (ITR-11): Returns the heading text of the most recent section above
    the given character position. Recognizes two heading styles:
      - Markdown: lines starting with # (e.g., '# Competitive Landscape')
      - ALL-CAPS: lines in uppercase followed by colon (e.g., 'COMPETITIVE LANDSCAPE:')

    Returns:
        The heading text (stripped) if found, or '' if no heading precedes this position.
    """
    # Only look at text BEFORE the character position
    preceding_text = prompt[:char_pos]

    best_heading = ''
    best_pos = -1

    for m in _SECTION_HEADING_RE.finditer(preceding_text):
        heading = (m.group(1) or m.group(2) or m.group(3) or '').strip()
        if heading and m.start() > best_pos:
            best_heading = heading
            best_pos = m.start()

    return best_heading


# ─── Price Confidence Scoring ─────────────────────────────────────────

# Structural pricing keywords — must appear as labeling context (e.g., "Pricing:", "cost:")
_PRICE_STRUCTURAL_KEYWORDS = re.compile(
    r'(?:pricing|price|cost|tier|monthly|annual|yearly|prepaid|'
    r'subscription|billing|fee|package)\s*[:=]',
    re.IGNORECASE,
)

# Weaker pricing signal — price with period suffix like /mo, /year
_PRICE_PERIOD_SUFFIX = re.compile(r'/(?:mo|month|yr|year|week)\b', re.IGNORECASE)

# Anti-signals: comparison/example context reduces confidence
_COMPARISON_CONTEXT = re.compile(
    r'(?:comparison|example|roughly|losing|lose|spend|roi|if\s+they)',
    re.IGNORECASE,
)


def _compute_price_confidence(prompt: str, match_start: int, match_end: int, value: str) -> float:
    """Compute confidence for a price assertion based on LINE-LEVEL context.

    High confidence: price on same/adjacent line as structural pricing keyword.
    Low confidence: price in narrative/comparison text.
    """
    # F-8 (ITR-11): Section-awareness — check if price is in a comparison section
    section_heading = _detect_section_context(prompt, match_start)
    if section_heading:
        heading_lower = section_heading.lower()
        if any(kw in heading_lower for kw in _COMPARISON_SECTION_KEYWORDS):
            return 0.15  # Competitor price — below any reasonable threshold

    # Split into lines and find which line this price is on
    lines = prompt.split("\n")
    char_pos = 0
    match_line_idx = 0
    for i, line in enumerate(lines):
        line_end = char_pos + len(line)
        if char_pos <= match_start <= line_end:
            match_line_idx = i
            break
        char_pos = line_end + 1  # +1 for the \n

    # Check +/- 2 lines for structural pricing keywords
    line_window_start = max(0, match_line_idx - 2)
    line_window_end = min(len(lines), match_line_idx + 3)  # exclusive
    context_lines = "\n".join(lines[line_window_start:line_window_end])

    has_structural = _PRICE_STRUCTURAL_KEYWORDS.search(context_lines)
    has_comparison = _COMPARISON_CONTEXT.search(context_lines)
    has_period_suffix = _PRICE_PERIOD_SUFFIX.search(value)

    # Small dollar amounts (< $10) without strong context = very low
    raw_amount = value.lstrip("$").split("/")[0].replace(",", "")
    try:
        amount = float(raw_amount)
    except ValueError:
        amount = 0

    # Structural keyword on same/adjacent line = high confidence
    if has_structural and not has_comparison:
        return 0.9

    # Comparison/example context = low confidence regardless
    if has_comparison:
        return 0.3

    # Small amount with no structural context = very low
    if amount < 10:
        return 0.2

    # No structural context = low confidence
    return 0.4


# ─── Route & Category Helpers ─────────────────────────────────────────

# Route slug generation: "Outreach Dashboard" -> "/outreach-dashboard"
def _name_to_route(name: str) -> str:
    """Convert a feature name to an expected route slug."""
    slug = re.sub(r'[^a-z0-9\s-]', '', name.lower()).strip()
    slug = re.sub(r'\s+', '-', slug)
    return f"/{slug}" if slug else "/unknown"


def _classify_feature_category(surface_type: str) -> str:
    """Map a surface keyword to a standardized category."""
    surface_lower = surface_type.lower()
    if surface_lower in ("dashboard", "panel", "portal"):
        return "dashboard"
    if surface_lower in ("form", "wizard", "modal"):
        return "form"
    if surface_lower in ("page", "view", "screen", "tab"):
        return "page"
    return "page"
