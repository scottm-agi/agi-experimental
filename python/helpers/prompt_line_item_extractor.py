"""
Prompt Line-Item Extractor — Deterministic, Zero-LLM Feature Extraction

Parses a user prompt and extracts every discrete deliverable as a LineItem.
This runs alongside GoalState's LLM-based extraction to catch everything
the LLM might generalize away.

Key properties:
- Deterministic: Same prompt always produces same line items (no LLM variance)
- Additive: Runs alongside GoalState extraction, not replacing it
- Over-extracts: Better to have 30 items with some noise than 5 items missing 15
- Dedup-aware: Content-hash dedup prevents duplicate items
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Set

logger = logging.getLogger("agix.prompt_line_item_extractor")

# ─── Import constants and classifiers from extracted modules ─────────────
# All constants, regex patterns, and classifier functions have been
# extracted to dedicated modules for maintainability. They are re-exported
# here so all existing imports from this module continue to work.
from python.helpers.line_item_constants import (  # noqa: F401
    MAX_LINE_LENGTH,
    QUANTITY_PATTERN,
    MAX_EXPANSION,
    _QUANTITY_WORD_MAP,
    _INTEGRATION_PHRASE_RE,
    _INTEGRATION_VERB_RE,
    _INTEGRATION_VIA_RE,
    _INTEGRATION_FOR_RE,
    _PACKAGE_RE,
    _STANDALONE_PRODUCT_RE,
    _URL_RE,
    _ROUTE_RE,
    _FILE_EXT_RE,
    _ROUTE_STOP_WORDS,
    _ENV_RE,
    _QUOTED_RE,
    _NEGATION_CONTEXT_RE,
    _ANTI_EXAMPLE_CATEGORIES,
    _PRICE_RE,
    _COMPETITOR_CONTEXT_RE,
    _COMPETITOR_PRICE_SIGNAL_RE,
    _COMMON_PAGE_HINT_RE,
    _NUMBERED_RE,
    _INLINE_NUMBERED_POS_RE,
    _WORKFLOW_ROUTING_RE,
    _CONDITIONAL_FLOW_RE,
    _STEP_SEQUENCE_RE,
    _BULLETED_RE,
    _PRIORITY_TIER_RE,
    _PAGE_KEYWORDS_RE,
    _CHECKLIST_RE,
    _QUALIFIER_RE,
    _STRATEGY_NORMALIZE,
    _FORMAT_CONSTRAINT_RE,
    _DEPLOYMENT_RE,
    _DEPLOYMENT_VCS_RE,
    _COMPLIANCE_IMPLICATION_PATTERNS,
    _NOISE_PATTERNS,
    _INVALID_PAGE_WORDS,
    _CONTENT_CONSTRAINT_RE,
    _UI_ELEMENT_RE,
    _UI_SURFACE_SIGNALS,
    UI_SURFACE_THRESHOLD,
    _SIGNAL_KEYWORDS,
    _STOP_WORDS,
    _COMPOUND_EXEMPT_CATEGORIES,
    _COMPOUND_MIN_LENGTH,
    _COMPOUND_MIN_PARTS,
    _COMPOUND_PART_MIN_LENGTH,
    _PLUS_SEP_RE,
    _AND_SEP_RE,
    _SEMI_SEP_RE,
    _ENUM_AFTER_MARKER_RE,
    _CONFIDENCE_MAP,
)
from python.helpers.line_item_classifier import (  # noqa: F401
    classify_requirement_text,
    is_valid_page_path,
    score_ui_surface,
    _derive_page_name,
    _content_hash,
    _safe_split_lines,
)


@dataclass
class LineItem:
    """A single extractable requirement from a user prompt."""
    id: str            # LI-001, LI-002, ...
    text: str          # The raw text of the requirement
    category: str      # url | integration | page | model | copy | feature | config | deployment
    source_line: int   # Line number in original prompt (for traceability)
    priority: str = "immediate"  # immediate | near_term | growth | phased | action_needed
    # F-4 (ITR-15): Confidence and extrapolation metadata
    confidence: float = 1.0       # 0.0-1.0: how confident we are in this extraction
    extrapolated: bool = False    # True if inferred (e.g., feature→page promotion)
    # F-5 (ITR-21): Quantity decomposition metadata
    parent_id: str = ""           # Parent LineItem ID when this is a sub-requirement
    sub_index: int = 0            # 1-based index within the parent (0 = not a sub-req)
    # SS-7: Format and strategy metadata
    format_constraint: str = ""   # "plain text", "HTML", "markdown", etc.
    implementation_strategy: str = ""  # "lazy", "eager", "on-demand", "event-driven"



def expand_quantity_requirements(items: List[LineItem]) -> List[LineItem]:
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
    expanded: List[LineItem] = []
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


# ─── Fix 4 (ISS-03): Compound Sub-Feature Decomposition ────────────────────
# When a feature description contains multiple sub-features separated by
# +, 'and', semicolons, or enumerated after — or :, break them into
# individual atomic requirements with parent_id links.

# Categories exempt from compound decomposition (prose with commas is normal)
_COMPOUND_EXEMPT_CATEGORIES = {"url", "copy", "copy_anti_example", "config",
                                "deployment", "content_constraint", "compliance"}

# Minimum text length to attempt compound decomposition
_COMPOUND_MIN_LENGTH = 15

# Minimum number of sub-parts required to trigger decomposition
_COMPOUND_MIN_PARTS = 2

# Minimum length of each sub-part to be considered valid
_COMPOUND_PART_MIN_LENGTH = 4

# Regex for " + " separator (with whitespace padding)
_PLUS_SEP_RE = re.compile(r'\s*\+\s*')

# Regex for " and " as a separator between features (word boundary)
_AND_SEP_RE = re.compile(r'\s+and\s+', re.IGNORECASE)

# Regex for "; " separator
_SEMI_SEP_RE = re.compile(r'\s*;\s*')

# Regex for "— item, item, item" or ": item, item, item" enumeration
# after em-dash or colon, followed by comma-separated items
_ENUM_AFTER_MARKER_RE = re.compile(
    r'(?:—|–|:|\s-\s)\s*(.+)$',
    re.IGNORECASE,
)


def expand_compound_sub_features(items: List[LineItem]) -> List[LineItem]:
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
    expanded: List[LineItem] = []

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


def _try_split_compound(text: str) -> "Optional[List[str]]":
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


def extract_line_items(prompt: str) -> List[LineItem]:
    """Extract all discrete deliverables from a user prompt.

    Returns a list of LineItem objects, one per extracted requirement.
    Deterministic: same prompt always produces same results.
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    items: List[LineItem] = []
    seen_hashes: Set[str] = set()
    counter = 0

    def _add(text: str, category: str, source_line: int,
             confidence: float = 1.0, extrapolated: bool = False) -> None:
        nonlocal counter
        h = _content_hash(text)
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        counter += 1
        items.append(LineItem(
            id=f"LI-{counter:03d}",
            text=text.strip(),
            category=category,
            source_line=source_line,
            confidence=confidence,
            extrapolated=extrapolated,
        ))

    lines = _safe_split_lines(prompt)

    # ── Pass 1: URLs ──────────────────────────────────────────────────
    for line_no, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,;:")
            _add(url, "url", line_no)

    # ── Pass 2: Named Integrations (universal detection) ─────────────
    for line_no, line in enumerate(lines):
        context = line.strip()
        if len(context) > 120:
            context = context[:120]

        # F-10 (ITR-16): Skip lines that are in competitor/strategy context.
        # Lines mentioning competitor products (e.g. "NiceJob is cheaper") should
        # NOT be classified as integration requirements. The competitor context
        # regex catches these patterns and excludes them from integration detection.
        if _COMPETITOR_CONTEXT_RE.search(line) or _COMPETITOR_PRICE_SIGNAL_RE.search(line):
            continue

        # F-9 (ITR-45): Check negation context for integrations
        _is_neg_integration = bool(_NEGATION_CONTEXT_RE.search(line))
        _int_cat = 'integration_anti_example' if _is_neg_integration else 'integration'

        # Pattern 1: CapitalizedWord + integration keyword
        for match in _INTEGRATION_PHRASE_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:  # Skip single chars
                _add(f"{name}: {context}", _int_cat, line_no)

        # Pattern 2: "integrate with X"
        for match in _INTEGRATION_VERB_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:
                _add(f"{name}: {context}", _int_cat, line_no)

        # Pattern 3: @scoped/package references
        for match in _PACKAGE_RE.finditer(line):
            pkg = match.group(1) or match.group(2)
            if pkg:
                _add(f"{pkg}: {context}", _int_cat, line_no)

        # Pattern 4: "use/set up/configure X" (capitalized product name)
        for match in _STANDALONE_PRODUCT_RE.finditer(line):
            name = match.group(1).strip()
            # Filter common false positives (generic English words)
            if name.lower() in ("the", "a", "an", "it", "this", "that", "your",
                                 "my", "our", "new", "all", "any", "each",
                                 "both", "every", "same", "next", "other"):
                continue
            if len(name) > 1:
                _add(f"{name}: {context}", _int_cat, line_no)

        # Pattern 2b: "via/through/using/powered by X" connectors (F-1)
        for match in _INTEGRATION_VIA_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:
                _add(f"{name}: {context}", _int_cat, line_no)

        # Pattern 2c: "X for Y" connectors (F-1 ITR-25)
        for match in _INTEGRATION_FOR_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:
                _add(f"{name}: {context}", _int_cat, line_no)

    # ── Pass 3: Routes / API endpoints ────────────────────────────────
    for line_no, line in enumerate(lines):
        # F-9 (ITR-45): Check negation context for routes/pages
        _is_neg_route = bool(_NEGATION_CONTEXT_RE.search(line))
        _route_cat = 'page_anti_example' if _is_neg_route else 'page'
        for match in _ROUTE_RE.finditer(line):
            route = match.group(1)
            # Filter out file paths (e.g., /src/app/page.tsx)
            if _FILE_EXT_RE.search(route):
                continue
            # F-4: Filter out English stop-words that look like routes
            route_base = route.lstrip('/').split('/')[0]
            if route_base.lower() in _ROUTE_STOP_WORDS:
                continue
            _add(f"{route} endpoint", _route_cat, line_no)

    # ── Pass 4: Page keywords ─────────────────────────────────────────
    for line_no, line in enumerate(lines):
        # F-9 (ITR-45): Check negation context for page keywords
        _is_neg_page = bool(_NEGATION_CONTEXT_RE.search(line))
        _page_cat = 'page_anti_example' if _is_neg_page else 'page'
        for match in _PAGE_KEYWORDS_RE.finditer(line):
            page_name = match.group(1)
            context = line.strip()
            if len(context) > 120:
                context = context[:120]
            _add(f"{page_name}: {context}", _page_cat, line_no)

    # ── Pass 5: Quoted strings (copy text) ────────────────────────────
    for line_no, line in enumerate(lines):
        # F-1 (ITR-15): Check if the entire line has negation context.
        # If so, quoted strings are anti-examples, not final copy.
        is_negation_context = bool(_NEGATION_CONTEXT_RE.search(line))
        for match in _QUOTED_RE.finditer(line):
            quoted = match.group(1)
            # Skip very generic quoted strings
            if quoted.lower() in ("...", "none", "n/a", "todo", "tbd"):
                continue
            category = "copy_anti_example" if is_negation_context else "copy"
            _add(quoted, category, line_no)

    # ── Pass 6: Pricing ───────────────────────────────────────────────
    for line_no, line in enumerate(lines):
        for match in _PRICE_RE.finditer(line):
            price = match.group(0)
            if price == "$0":
                continue  # Skip free tier
            context = line.strip()
            # F-2 + SS-8: Tag competitor/comparison prices so LLM can distinguish
            # Check both explicit competitor context AND behavioral signals
            is_competitor = bool(
                _COMPETITOR_CONTEXT_RE.search(context)
                or _COMPETITOR_PRICE_SIGNAL_RE.search(context)
            )
            tag = "competitor-price" if is_competitor else "product-price"
            _add(f"Pricing ({tag}): {price} — {context}", "copy", line_no)

    # ── Pass 7: Environment variables / config ────────────────────────
    for line_no, line in enumerate(lines):
        for match in _ENV_RE.finditer(line):
            var_name = match.group(1)
            # Filter out common false positives
            if var_name in (
                "HTTP", "HTTPS", "HTML", "CSS", "JSON", "REST", "API",
                "URL", "URI", "SQL", "ORM", "CTA", "SEO", "SSR", "SSG",
                "CRUD", "CORS", "JWT", "TOML", "YAML", "SCSS", "LESS",
                "README", "TODO", "FIXME", "HACK", "NOTE", "TRUE", "FALSE",
                "NULL", "NONE", "MUST", "GOOD", "ONLY", "WITH", "EVERY",
                "BAD", "ALL", "NOT", "THE", "FOR", "AND",
            ):
                continue
            # Must look like an env var (has underscore or known suffix)
            if "_" not in var_name and not var_name.endswith(("KEY", "SECRET", "TOKEN", "PORT")):
                continue
            context = line.strip()
            _add(f"{var_name}: {context}", "config", line_no)

    # ── Pass 8: Numbered features ─────────────────────────────────────
    for match in _NUMBERED_RE.finditer(prompt):
        feature_text = match.group(2).strip()
        if len(feature_text) > 10:  # Skip very short items
            line_no = prompt[:match.start()].count("\n")
            # F-9 (ITR-45): Check negation context for numbered features
            _feat_line = lines[line_no] if line_no < len(lines) else ''
            _feat_cat = 'feature_anti_example' if _NEGATION_CONTEXT_RE.search(_feat_line) else 'feature'
            _add(feature_text, _feat_cat, line_no)

    # ── Pass 9: Bulleted features ─────────────────────────────────────
    for match in _BULLETED_RE.finditer(prompt):
        feature_text = match.group(1).strip()
        if len(feature_text) > 10:  # Skip very short items
            line_no = prompt[:match.start()].count("\n")
            # F-9 (ITR-45): Check negation context for bulleted features
            _feat_line = lines[line_no] if line_no < len(lines) else ''
            _feat_cat = 'feature_anti_example' if _NEGATION_CONTEXT_RE.search(_feat_line) else 'feature'
            _add(feature_text, _feat_cat, line_no)

    # ── Pass 10: Checklist / compliance markers ───────────────────────
    for match in _CHECKLIST_RE.finditer(prompt):
        item_text = match.group(1).strip()
        if len(item_text) > 5:
            line_no = prompt[:match.start()].count("\n")
            # F-9 (ITR-45): Check negation context for checklist items
            _feat_line = lines[line_no] if line_no < len(lines) else ''
            _feat_cat = 'feature_anti_example' if _NEGATION_CONTEXT_RE.search(_feat_line) else 'feature'
            _add(item_text, _feat_cat, line_no)

    # ── Pass 11: Deployment / infrastructure instructions ─────────────
    for match in _DEPLOYMENT_RE.finditer(prompt):
        target = match.group(1).strip()
        if len(target) > 2:
            line_no = prompt[:match.start()].count("\n")
            lines_list = prompt.split("\n")
            context = lines_list[line_no].strip() if line_no < len(lines_list) else target
            _add(f"Deploy: {context[:120]}", "deployment", line_no)

    # ── Pass 11b: VCS Deployment (F-2, RCA-343 ISSUE-2) ───────────────
    # Catch GitHub/GitLab push, repo creation, and CI/CD directives that
    # the generic _DEPLOYMENT_RE misses.
    for match in _DEPLOYMENT_VCS_RE.finditer(prompt):
        matched_text = match.group(0).strip().rstrip(".,")
        if len(matched_text) > 5:
            line_no = prompt[:match.start()].count("\n")
            lines_list = prompt.split("\n")
            context = lines_list[line_no].strip() if line_no < len(lines_list) else matched_text
            _add(f"Deploy: {context[:120]}", "deployment", line_no)

    # ── Pass 12: Compliance Implications (RCA-341) ────────────────────
    # When the prompt mentions a compliance framework or outreach pattern,
    # infer implied pages that are industry-standard but never explicitly
    # requested. This prevents an entire CLASS of extraction gaps.
    # Dedup by page path: /privacy from SOC2 and HIPAA → only inject once.
    _seen_compliance_paths: Set[str] = set()
    for pattern, implications in _COMPLIANCE_IMPLICATION_PATTERNS:
        if pattern.search(prompt):
            # Find the first line where the compliance keyword appears
            for line_no, line in enumerate(lines):
                if pattern.search(line):
                    for impl_text, impl_category in implications:
                        # Extract the /path prefix for dedup (e.g., "/privacy")
                        page_path = impl_text.split(" ")[0] if impl_text.startswith("/") else impl_text
                        if page_path in _seen_compliance_paths:
                            continue
                        _seen_compliance_paths.add(page_path)
                        _add(impl_text, impl_category, line_no, confidence=0.7)
                    break  # Only inject once per pattern

    # ── Pass 14: Product Workflow Detection (RCA-354 F-2) ─────────────
    # Catch inline numbered items, → routing patterns, conditional flows,
    # and step sequences that the standard numbered/bulleted passes miss.
    # These patterns capture the CORE PRODUCT workflows that are most
    # critical to extract correctly.

    # Pass 14a: Inline numbered items (position-based splitting)
    inline_matches = list(_INLINE_NUMBERED_POS_RE.finditer(prompt))
    for i, match in enumerate(inline_matches):
        start = match.end()
        end = inline_matches[i + 1].start() if i + 1 < len(inline_matches) else len(prompt)
        feature_text = prompt[start:end].strip().rstrip(',. ')
        if len(feature_text) > 8:
            line_no = prompt[:match.start()].count("\n")
            _add(feature_text, "feature", line_no)

    # Pass 14b: Workflow routing (X → Y patterns)
    for match in _WORKFLOW_ROUTING_RE.finditer(prompt):
        workflow_text = match.group(1).strip().rstrip(',')
        if len(workflow_text) > 10:
            line_no = prompt[:match.start()].count("\n")
            _add(f"Workflow: {workflow_text}", "feature", line_no)

    # Pass 14c: Conditional flows (if X then Y)
    for match in _CONDITIONAL_FLOW_RE.finditer(prompt):
        condition = match.group(1).strip()
        action = match.group(2).strip()
        if len(condition) > 5 and len(action) > 5:
            line_no = prompt[:match.start()].count("\n")
            _add(f"Conditional: if {condition} then {action}", "feature", line_no)

    # Pass 14d: Step sequences (Step 1 — description)
    for match in _STEP_SEQUENCE_RE.finditer(prompt):
        step_text = match.group(2).strip()
        if len(step_text) > 8:
            line_no = prompt[:match.start()].count("\n")
            _add(f"Step {match.group(1)}: {step_text}", "feature", line_no)

    # ── Pass 15: Content Constraints (ITR-12 F-11) ─────────────────────
    # Extract content rules: "under 80 words", "max 3 retries", etc.
    for line_no, line in enumerate(lines):
        for match in _CONTENT_CONSTRAINT_RE.finditer(line):
            amount = match.group(1)
            unit = match.group(2)
            constraint_text = f"Content constraint: {match.group(0).strip()}"
            # Include broader context from the line
            context = line.strip()
            if len(context) > 120:
                context = context[:120]
            _add(f"{constraint_text} — {context}", "content_constraint", line_no)

    # ── Pass 16: UI Element Specifications (ITR-12 F-11) ───────────────
    # Extract specific UI component mentions: banner, drawer, modal, toast, etc.
    for line_no, line in enumerate(lines):
        for match in _UI_ELEMENT_RE.finditer(line):
            element_text = match.group(1).strip()
            if len(element_text) > 3:  # Skip very short matches
                context = line.strip()
                if len(context) > 120:
                    context = context[:120]
                _add(f"UI element: {element_text} — {context}", "ui_element", line_no)

    # ── Pass 13: Feature→Page Promotion (F-5) ─────────────────────────
    # Post-process features from Pass 8/9 and score them for UI-surface
    # probability. Features above UI_SURFACE_THRESHOLD get a COMPANION
    # page requirement added. This prevents the class of gap where
    # features like "outreach pipeline with filterable table" imply a UI
    # page but are never promoted to category="page".
    # Collect existing page route names for dedup
    _existing_page_names: Set[str] = set()
    for item in items:
        if item.category == "page":
            # Extract the first significant word from page text for dedup
            page_words = re.findall(r'[a-zA-Z]+', item.text.lower())
            for pw in page_words:
                if pw not in _STOP_WORDS and len(pw) > 2:
                    _existing_page_names.add(pw)
                    break

    for item in list(items):  # iterate over a copy since we may add items
        if item.category == "feature":
            ui_score = score_ui_surface(item.text)
            if ui_score >= UI_SURFACE_THRESHOLD:
                page_name = _derive_page_name(item.text)
                # Dedup: don't add if a page with this name already exists
                if page_name not in _existing_page_names:
                    _add(
                        f"/{page_name} — {page_name.title()} page "
                        f"(extrapolated from feature: {item.text[:80]})",
                        "page",
                        item.source_line,
                        confidence=ui_score,
                        extrapolated=True,
                    )
                    _existing_page_names.add(page_name)

    # ── Pass 17: Common Page Hint Detection (SS-7, ITR-15) ────────────
    # Scan each line for behavioral page signals (pricing page, contact form,
    # dashboard view, etc.). If matched and no explicit page route already
    # exists for that concept, add a page LineItem with low confidence.
    for line_no, line in enumerate(lines):
        for match in _COMMON_PAGE_HINT_RE.finditer(line):
            hint_text = match.group(0).strip()
            # Derive page name from the hint (first significant word)
            page_name = _derive_page_name(hint_text)
            if page_name not in _existing_page_names:
                context = line.strip()
                if len(context) > 120:
                    context = context[:120]
                _add(
                    f"/{page_name} — {page_name.title()} page "
                    f"(page_hint: {hint_text})",
                    "page",
                    line_no,
                    confidence=0.6,
                    extrapolated=True,
                )
                _existing_page_names.add(page_name)

    # ── Priority-Tier Tagging Pass (U-1, RCA-2 through RCA-6) ─────────
    # Walk through the prompt lines to find priority-tier section headers,
    # then tag each LineItem with the priority of the section it falls under.
    # Priority is METADATA only — all items remain in-scope regardless of tier.
    _priority_boundaries: list = []  # (line_no, priority_label)
    for line_no, line in enumerate(lines):
        m = _PRIORITY_TIER_RE.search(line)
        if m:
            if m.group('immediate'):
                _priority_boundaries.append((line_no, 'immediate'))
            elif m.group('near_term') or m.group('week'):
                _priority_boundaries.append((line_no, 'near_term'))
            elif m.group('growth'):
                _priority_boundaries.append((line_no, 'growth'))
            elif m.group('phased'):
                _priority_boundaries.append((line_no, 'phased'))
            elif m.group('action_needed'):
                _priority_boundaries.append((line_no, 'action_needed'))

    if _priority_boundaries:
        # Sort boundaries by line number
        _priority_boundaries.sort(key=lambda x: x[0])
        for item in items:
            # Find the closest preceding priority boundary
            assigned_priority = 'immediate'  # default
            for boundary_line, priority_label in _priority_boundaries:
                if item.source_line >= boundary_line:
                    assigned_priority = priority_label
                else:
                    break
            item.priority = assigned_priority

    logger.info(
        f"[PROMPT LINE-ITEM EXTRACTOR] Extracted {len(items)} line items "
        f"from prompt ({len(prompt)} chars)"
    )

    # ── F-5 (ITR-21): Quantity-Aware Decomposition ─────────────────────
    # Expand items like "3-email drip sequence" into 3 sub-requirements
    # BEFORE returning, so downstream consumers see individual items.
    items = expand_quantity_requirements(items)

    # ── Fix 4 (ISS-03): Compound Sub-Feature Decomposition ─────────────
    # Expand compound features like "A + B + C" or "features: X, Y, Z"
    # into atomic sub-requirements with parent_id links.
    items = expand_compound_sub_features(items)

    # ── F-9 (ITR-45): Filter anti-example items from main results ──────
    # Items tagged with '_anti_example' suffix are negative examples that
    # should NOT be treated as positive requirements. Exclude them from the
    # main return list. They are logged for audit/debugging.
    anti_items = [i for i in items if i.category in _ANTI_EXAMPLE_CATEGORIES]
    if anti_items:
        logger.info(
            f"[PROMPT LINE-ITEM EXTRACTOR] Filtered {len(anti_items)} "
            f"anti-example items: {[(i.id, i.category, i.text[:60]) for i in anti_items]}"
        )
    items = [i for i in items if i.category not in _ANTI_EXAMPLE_CATEGORIES]

    return items


def extract_weighted_candidates(prompt: str) -> "List['WeightedCandidate']":
    """Extract weighted candidates from a user prompt with confidence scoring.

    This is the Layer 1 output of the hybrid pipeline. It wraps
    extract_line_items() and enriches each result with a confidence score
    and structural metadata.

    Args:
        prompt: The raw user prompt text.

    Returns:
        List of WeightedCandidate objects with confidence scores and
        structural markers.
    """
    from python.helpers.weighted_candidate import WeightedCandidate

    if not prompt or len(prompt.strip()) < 10:
        return []

    candidates: List[WeightedCandidate] = []
    seen_hashes: Set[str] = set()
    lines = _safe_split_lines(prompt)

    def _add_candidate(text: str, category: str, source_line: int,
                       confidence: float, markers: dict = None) -> None:
        h = _content_hash(text)
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        candidates.append(WeightedCandidate(
            text=text.strip(),
            category=category,
            confidence=confidence,
            source_line=source_line,
            structural_markers=markers or {},
        ))

    # ── Pass 1: URLs (confidence=1.0) ─────────────────────────────────
    for line_no, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,;:")
            _add_candidate(url, "url", line_no, _CONFIDENCE_MAP["url"],
                           {"is_url": True})

    # ── Pass 2: Named Integrations (confidence=0.9) ───────────────────
    for line_no, line in enumerate(lines):
        context = line.strip()[:120]

        # F-10 (ITR-16): Skip lines in competitor/strategy context
        if _COMPETITOR_CONTEXT_RE.search(line) or _COMPETITOR_PRICE_SIGNAL_RE.search(line):
            continue

        for match in _INTEGRATION_PHRASE_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:
                _add_candidate(f"{name}: {context}", "integration", line_no,
                               _CONFIDENCE_MAP["integration"],
                               {"is_integration": True, "pattern": "phrase"})

        for match in _INTEGRATION_VERB_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:
                _add_candidate(f"{name}: {context}", "integration", line_no,
                               _CONFIDENCE_MAP["integration"],
                               {"is_integration": True, "pattern": "verb"})

        for match in _PACKAGE_RE.finditer(line):
            pkg = match.group(1) or match.group(2)
            if pkg:
                _add_candidate(f"{pkg}: {context}", "integration", line_no,
                               _CONFIDENCE_MAP["integration"],
                               {"is_integration": True, "pattern": "package"})

        for match in _STANDALONE_PRODUCT_RE.finditer(line):
            name = match.group(1).strip()
            if name.lower() in ("the", "a", "an", "it", "this", "that", "your",
                                 "my", "our", "new", "all", "any", "each",
                                 "both", "every", "same", "next", "other"):
                continue
            if len(name) > 1:
                _add_candidate(f"{name}: {context}", "integration", line_no,
                               _CONFIDENCE_MAP["integration"],
                               {"is_integration": True, "pattern": "standalone"})

        # Pattern 2b: "via/through/using/powered by X" connectors (F-1)
        for match in _INTEGRATION_VIA_RE.finditer(line):
            name = match.group(1).strip()
            if len(name) > 1:
                _add_candidate(f"{name}: {context}", "integration", line_no,
                               _CONFIDENCE_MAP["integration"],
                               {"is_integration": True, "pattern": "via"})

    # ── Pass 3: Routes (confidence=0.85) ──────────────────────────────
    for line_no, line in enumerate(lines):
        for match in _ROUTE_RE.finditer(line):
            route = match.group(1)
            if _FILE_EXT_RE.search(route):
                continue
            # F-4: Filter out English stop-words that look like routes
            route_base = route.lstrip('/').split('/')[0]
            if route_base.lower() in _ROUTE_STOP_WORDS:
                continue
            _add_candidate(f"{route} endpoint", "page", line_no,
                           _CONFIDENCE_MAP["route"],
                           {"is_route": True})

    # ── Pass 4: Page keywords (confidence=0.85) ───────────────────────
    for line_no, line in enumerate(lines):
        for match in _PAGE_KEYWORDS_RE.finditer(line):
            page_name = match.group(1)
            context = line.strip()[:120]
            _add_candidate(f"{page_name}: {context}", "page", line_no,
                           _CONFIDENCE_MAP["page_keyword"],
                           {"is_page": True, "keyword_match": True})

    # ── Pass 5: Quoted strings (confidence=0.8) ───────────────────────
    for line_no, line in enumerate(lines):
        for match in _QUOTED_RE.finditer(line):
            quoted = match.group(1)
            if quoted.lower() in ("...", "none", "n/a", "todo", "tbd"):
                continue
            _add_candidate(quoted, "copy", line_no,
                           _CONFIDENCE_MAP["quoted"],
                           {"is_quoted": True})

    # ── Pass 6: Pricing (confidence=0.95) ─────────────────────────────
    for line_no, line in enumerate(lines):
        for match in _PRICE_RE.finditer(line):
            price = match.group(0)
            if price == "$0":
                continue
            context = line.strip()
            # F-2 + SS-8: Tag competitor/comparison prices so LLM can distinguish
            # Check both explicit competitor context AND behavioral signals
            is_competitor = bool(
                _COMPETITOR_CONTEXT_RE.search(context)
                or _COMPETITOR_PRICE_SIGNAL_RE.search(context)
            )
            tag = "competitor-price" if is_competitor else "product-price"
            _add_candidate(f"Pricing ({tag}): {price} — {context}", "copy", line_no,
                           _CONFIDENCE_MAP["pricing"],
                           {"is_pricing": True, "price_context": tag})

    # ── Pass 7: Env vars (confidence=0.95) ────────────────────────────
    for line_no, line in enumerate(lines):
        for match in _ENV_RE.finditer(line):
            var_name = match.group(1)
            if var_name in (
                "HTTP", "HTTPS", "HTML", "CSS", "JSON", "REST", "API",
                "URL", "URI", "SQL", "ORM", "CTA", "SEO", "SSR", "SSG",
                "CRUD", "CORS", "JWT", "TOML", "YAML", "SCSS", "LESS",
                "README", "TODO", "FIXME", "HACK", "NOTE", "TRUE", "FALSE",
                "NULL", "NONE", "MUST", "GOOD", "ONLY", "WITH", "EVERY",
                "BAD", "ALL", "NOT", "THE", "FOR", "AND",
            ):
                continue
            if "_" not in var_name and not var_name.endswith(("KEY", "SECRET", "TOKEN", "PORT")):
                continue
            context = line.strip()
            _add_candidate(f"{var_name}: {context}", "config", line_no,
                           _CONFIDENCE_MAP["config"],
                           {"is_env_var": True})

    # ── Pass 8: Numbered features (confidence=0.7) ────────────────────
    for match in _NUMBERED_RE.finditer(prompt):
        feature_text = match.group(2).strip()
        if len(feature_text) > 10:
            line_no = prompt[:match.start()].count("\n")
            _add_candidate(feature_text, "feature", line_no,
                           _CONFIDENCE_MAP["numbered"],
                           {"is_numbered": True, "number": int(match.group(1))})

    # ── Pass 9: Bulleted features (confidence=0.6) ────────────────────
    for match in _BULLETED_RE.finditer(prompt):
        feature_text = match.group(1).strip()
        if len(feature_text) > 10:
            line_no = prompt[:match.start()].count("\n")
            _add_candidate(feature_text, "feature", line_no,
                           _CONFIDENCE_MAP["bulleted"],
                           {"is_bullet": True})

    # ── Pass 10: Checklist markers (confidence=0.75) ──────────────────
    # G-7 (ITR-24): Parse qualifier markers (MUST:, NICE-TO-HAVE:, etc.)
    # SS-3: Extended with strategy group (LAZY, EAGER, ON-DEMAND, EVENT-DRIVEN)
    for match in _CHECKLIST_RE.finditer(prompt):
        item_text = match.group(1).strip()
        if len(item_text) > 5:
            line_no = prompt[:match.start()].count("\n")
            # Parse qualifier from item text
            qualifier = None
            strategy = None
            clean_text = item_text
            q_match = _QUALIFIER_RE.match(item_text)
            if q_match:
                if q_match.group('must'):
                    qualifier = 'must-have'
                elif q_match.group('nice'):
                    qualifier = 'nice-to-have'
                elif q_match.group('strategy'):
                    raw_strategy = q_match.group('strategy').strip()
                    strategy = _STRATEGY_NORMALIZE.get(
                        raw_strategy.upper().replace(' ', '-'),
                        raw_strategy.lower(),
                    )
                clean_text = item_text[q_match.end():].strip()
            # SS-7: Detect format constraints in item text
            fmt_marker = {"is_checklist": True}
            fc_match = _FORMAT_CONSTRAINT_RE.search(item_text)
            if fc_match:
                fmt_marker["format_constraint"] = fc_match.group('fmt').strip().lower()
            if strategy:
                fmt_marker["implementation_strategy"] = strategy
            candidate = WeightedCandidate(
                text=clean_text if clean_text else item_text,
                category="feature",
                confidence=_CONFIDENCE_MAP["checklist"],
                source_line=line_no,
                structural_markers=fmt_marker,
                qualifier=qualifier,
            )
            h = _content_hash(candidate.text)
            if h not in seen_hashes:
                seen_hashes.add(h)
                candidates.append(candidate)

    # ── Pass 11: Deployment (confidence=0.8) ──────────────────────────
    for match in _DEPLOYMENT_RE.finditer(prompt):
        target = match.group(1).strip()
        if len(target) > 2:
            line_no = prompt[:match.start()].count("\n")
            lines_list = prompt.split("\n")
            context = lines_list[line_no].strip() if line_no < len(lines_list) else target
            _add_candidate(f"Deploy: {context[:120]}", "deployment", line_no,
                           _CONFIDENCE_MAP["deployment"],
                           {"is_deployment": True})

    # ── Pass 12: Compliance Implications (RCA-341, confidence=0.7) ────
    for pattern, implications in _COMPLIANCE_IMPLICATION_PATTERNS:
        if pattern.search(prompt):
            for line_no, line in enumerate(lines):
                if pattern.search(line):
                    for impl_text, impl_category in implications:
                        _add_candidate(impl_text, impl_category, line_no,
                                       _CONFIDENCE_MAP["compliance"],
                                       {"is_implied": True, "compliance_source": pattern.pattern[:40]})
                    break

    # ── Pass 14: Product Workflow Detection (RCA-354 F-2, confidence=0.80) ──
    # Catch inline numbered items, → routing, conditional flows, step sequences

    # Pass 14a: Inline numbered items (position-based splitting)
    inline_matches_w = list(_INLINE_NUMBERED_POS_RE.finditer(prompt))
    for i, match in enumerate(inline_matches_w):
        start = match.end()
        end = inline_matches_w[i + 1].start() if i + 1 < len(inline_matches_w) else len(prompt)
        feature_text = prompt[start:end].strip().rstrip(',. ')
        if len(feature_text) > 8:
            line_no = prompt[:match.start()].count("\n")
            _add_candidate(feature_text, "feature", line_no, 0.80,
                           {"is_inline_numbered": True,
                            "number": int(match.group(1))})

    # Pass 14b: Workflow routing (X → Y)
    for match in _WORKFLOW_ROUTING_RE.finditer(prompt):
        workflow_text = match.group(1).strip().rstrip(',')
        if len(workflow_text) > 10:
            line_no = prompt[:match.start()].count("\n")
            _add_candidate(f"Workflow: {workflow_text}", "feature", line_no, 0.80,
                           {"is_workflow": True, "pattern": "routing"})

    # Pass 14c: Conditional flows (if X then Y)
    for match in _CONDITIONAL_FLOW_RE.finditer(prompt):
        condition = match.group(1).strip()
        action = match.group(2).strip()
        if len(condition) > 5 and len(action) > 5:
            line_no = prompt[:match.start()].count("\n")
            _add_candidate(
                f"Conditional: if {condition} then {action}",
                "feature", line_no, 0.80,
                {"is_workflow": True, "pattern": "conditional"},
            )

    # Pass 14d: Step sequences
    for match in _STEP_SEQUENCE_RE.finditer(prompt):
        step_text = match.group(2).strip()
        if len(step_text) > 8:
            line_no = prompt[:match.start()].count("\n")
            _add_candidate(
                f"Step {match.group(1)}: {step_text}",
                "feature", line_no, 0.80,
                {"is_workflow": True, "pattern": "step_sequence",
                 "step_number": int(match.group(1))},
            )

    # ── Pass 15: Content Constraints (ITR-12 F-11, confidence=0.85) ────
    for line_no, line in enumerate(lines):
        for match in _CONTENT_CONSTRAINT_RE.finditer(line):
            constraint_text = f"Content constraint: {match.group(0).strip()}"
            context = line.strip()[:120]
            _add_candidate(
                f"{constraint_text} — {context}",
                "content_constraint", line_no, 0.85,
                {"is_content_constraint": True,
                 "amount": match.group(1), "unit": match.group(2)},
            )

    # ── Pass 16: UI Element Specifications (ITR-12 F-11, confidence=0.75) ─
    for line_no, line in enumerate(lines):
        for match in _UI_ELEMENT_RE.finditer(line):
            element_text = match.group(1).strip()
            if len(element_text) > 3:
                context = line.strip()[:120]
                _add_candidate(
                    f"UI element: {element_text} — {context}",
                    "ui_element", line_no, 0.75,
                    {"is_ui_element": True, "element": element_text},
                )

    # ── Pass 13: Feature→Page Promotion (F-5, confidence=0.75) ────────
    # Post-process feature candidates and score for UI-surface signals.
    # Add ui_surface_score to structural_markers for ALL feature candidates.
    # Promote features above threshold to companion page candidates.
    _existing_page_names_w: Set[str] = set()
    for c in candidates:
        if c.category == "page":
            page_words = re.findall(r'[a-zA-Z]+', c.text.lower())
            for pw in page_words:
                if pw not in _STOP_WORDS and len(pw) > 2:
                    _existing_page_names_w.add(pw)
                    break

    for c in list(candidates):  # iterate over a copy
        if c.category == "feature":
            ui_score = score_ui_surface(c.text)
            # Enrich structural_markers with ui_surface_score
            c.structural_markers["ui_surface_score"] = ui_score
            if ui_score >= UI_SURFACE_THRESHOLD:
                page_name = _derive_page_name(c.text)
                if page_name not in _existing_page_names_w:
                    _add_candidate(
                        f"/{page_name} — {page_name.title()} page "
                        f"(extrapolated from feature: {c.text[:80]})",
                        "page",
                        c.source_line,
                        0.75,  # Derived confidence — between bulleted (0.6) and page_keyword (0.85)
                        {"is_promoted": True, "ui_surface_score": ui_score,
                         "source_feature": c.text[:80]},
                    )
                    _existing_page_names_w.add(page_name)

    # ── Pass 17: Common Page Hint Detection (SS-7, ITR-15, confidence=0.6) ──
    for line_no, line in enumerate(lines):
        for match in _COMMON_PAGE_HINT_RE.finditer(line):
            hint_text = match.group(0).strip()
            page_name = _derive_page_name(hint_text)
            if page_name not in _existing_page_names_w:
                context = line.strip()[:120]
                _add_candidate(
                    f"/{page_name} — {page_name.title()} page "
                    f"(page_hint: {hint_text})",
                    "page",
                    line_no,
                    0.6,
                    {"is_page_hint": True, "hint_text": hint_text},
                )
                _existing_page_names_w.add(page_name)

    # ── SS-3 (ITR-23): Quantity Expansion for Weighted Candidates ──
    # Apply the same QUANTITY_PATTERN expansion that extract_line_items() uses
    # via expand_quantity_requirements(). Without this, prompts like
    # "3-email drip sequence" produce 1 candidate instead of 3.
    candidates = expand_quantity_weighted_candidates(candidates)

    logger.info(
        f"[PROMPT LINE-ITEM EXTRACTOR] Extracted {len(candidates)} weighted candidates "
        f"from prompt ({len(prompt)} chars)"
    )

    return candidates
