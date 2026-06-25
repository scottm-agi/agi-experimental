"""Content Manifest Parsing module — extracted from skeleton_generator.py.

Part of P0-3 decomposition: isolates all manifest-related functions and
constants that were previously embedded in the monolithic skeleton_generator.

Provides:
  - ContentManifest dataclass (System 5 / ADR-82) — typed schema contract
  - parse_manifest() — single JSON parser for all consumers
  - Manifest path discovery (_find_manifest_path)
  - Manifest loading as flat literals or raw dict
  - Recursive JSON string extraction
  - Text-based and category-based literal matching
  - Scoped (context-window) literal matching for per-requirement relevance

All functions and constants are copied EXACTLY from skeleton_generator.py
to maintain behavioral parity.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.manifest_parser")


# ─── F-10 (ITR-12): Category → Manifest Key Mapping ─────────────────────
# Maps requirement categories to relevant manifest section keys.
# Used by _match_literals_by_category to extract concrete values from
# the correct manifest sections based on requirement category, instead
# of searching abstract requirement text for concrete manifest values.
# RCA-461 R-1: LLMs generate manifest keys with varying names. Common aliases:
#   'identity' → 'branding' (MSR smoke test used this)
#   'outreach_scenarios' → 'scenarios' (MSR used this for copy/feature content)
#   'secrets_required' → 'integrations' (lists API key names)
_CATEGORY_MANIFEST_KEYS = {
    'branding': ['branding', 'founder', 'identity'],
    'url': ['urls', 'pricing'],  # ITR-14 I-3: URLs may be in pricing dict after normalization
    'compliance': ['email_rules', 'compliance'],
    'copy': ['scenarios', 'outreach_scenarios', 'email_rules', 'branding', 'identity'],
    'integration': ['integrations', 'secrets_required', 'urls', 'pricing', 'models'],  # ITR-33: removed tech_stack (garbage), added integrations[]; RCA-470 F-3: added models[] for verified_slug extraction
    'feature': ['scenarios', 'outreach_scenarios', 'branding', 'identity'],
    'page': ['branding', 'identity', 'urls', 'pricing'],  # ITR-14 I-3: pricing URLs relevant for pages
    'config': ['tech_stack'],
    'content_constraint': ['email_rules'],
}


# F-1 (ITR-13): Shared manifest path resolution. The requirements tool saves
# content_manifest.json at the project root, but some older flows save it
# under docs/. Check both locations AND both naming conventions for
# universal compatibility. RCA-457: canonical convention from
# planning_paths.py uses hyphens (content-manifest.json).
_MANIFEST_SEARCH_PATHS = [
    os.path.join("docs", "content-manifest.json"),           # Canonical (planning_paths.py)
    "content_manifest.json",                                 # Project root (legacy)
    "content-manifest.json",                                 # Project root (hyphen)
    os.path.join("docs", "content_manifest.json"),           # docs/ (legacy underscore)
    os.path.join(".agix.proj", "content_manifest.json"),   # .agix.proj/ (internal)
    os.path.join(".agix.proj", "content-manifest.json"),   # .agix.proj/ (hyphen)
]


def _find_manifest_path(project_dir: str) -> Optional[str]:
    """Find content_manifest.json in the project directory.

    Checks multiple locations in priority order:
      1. project_root/content_manifest.json (requirements tool default)
      2. project_root/docs/content_manifest.json (backward compat)
      3. project_root/.agix.proj/content_manifest.json (internal)

    Returns:
        Absolute path to the manifest file, or None if not found.
    """
    for relative_path in _MANIFEST_SEARCH_PATHS:
        full_path = os.path.join(project_dir, relative_path)
        if os.path.exists(full_path):
            return full_path
    return None


# ─── System 5 / ADR-82: ContentManifest Dataclass ────────────────────────
# Single typed schema contract for ALL manifest consumers. Every field has
# a defined type. Replaces raw dict[str, Any] access across 6+ readers.

@dataclass
class ContentManifest:
    """Typed representation of content_manifest.json.

    This is the SINGLE schema contract for all manifest consumers.
    Every reader MUST use this dataclass instead of raw dict access.

    Fields:
        branding: Visual identity and brand metadata.
        founder: Founder/owner information.
        urls: Dict of URL keys/values (also receives normalized 'links').
        pricing: Flat dict of pricing keys/values.
        integrations: ALWAYS a list (never a dict) — resolves the live bug.
        scenarios: List of scenario dicts.
        tech_stack: Technology stack information.
        domain: Top-level domain string.
        ai_model: AI model identifier.
        email_rules: Dict of email rule constraints.
        compliance: Dict of compliance flags.
        secrets: Merged from api_keys + secrets + secrets_provided sections.
        config: Arbitrary configuration dict.
    """
    branding: dict = field(default_factory=dict)
    founder: dict = field(default_factory=dict)
    urls: dict = field(default_factory=dict)
    pricing: dict = field(default_factory=dict)
    integrations: list = field(default_factory=list)  # ALWAYS list — never dict
    scenarios: list = field(default_factory=list)
    tech_stack: dict = field(default_factory=dict)
    domain: str = ""
    ai_model: str = ""
    email_rules: dict = field(default_factory=dict)
    compliance: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)  # merged from api_keys + secrets + secrets_provided
    config: dict = field(default_factory=dict)


# ─── System 5 / ADR-82: parse_manifest() — Single Parser ────────────────

def parse_manifest(project_dir: str) -> ContentManifest:
    """Parse content_manifest.json into a typed ContentManifest.

    This is the SINGLE json.load for the manifest — all readers MUST
    call this function instead of implementing their own parsing.

    Handles:
      - Path discovery (3-location search via _find_manifest_path)
      - JSON parsing with proper error handling
      - Schema normalization via requirements._normalize_manifest_schema
      - Secret section merging (api_keys + secrets + secrets_provided → secrets)
      - Integrations dict → list normalization
      - 'links' → 'urls' aliasing

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        ContentManifest instance. If no manifest exists or parsing fails,
        returns a default-initialized ContentManifest with empty fields.
    """
    path = _find_manifest_path(project_dir)
    if not path:
        return ContentManifest()

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"[CONTENT MANIFEST] Failed to parse {path}: {e}")
        return ContentManifest()

    # Guard: must be a dict
    if not isinstance(raw, dict):
        logger.warning(
            f"[CONTENT MANIFEST] Expected dict, got {type(raw).__name__}"
        )
        return ContentManifest()

    # Normalize using existing normalizer (if available)
    try:
        from python.tools.requirements import _normalize_manifest_schema
        raw = _normalize_manifest_schema(raw)
    except ImportError:
        pass

    # ── Merge secret sections ──
    secrets: Dict[str, Any] = {}
    for key in ('api_keys', 'secrets', 'secrets_provided'):
        section = raw.pop(key, None)
        if isinstance(section, dict):
            for sk, sv in section.items():
                if isinstance(sv, str) and sv.strip():
                    # P1-5: Strip embedded newlines/tabs that LLM may inject
                    cleaned = sv.strip().replace('\n', '').replace('\r', '').replace('\t', '')
                    if cleaned:
                        secrets[sk] = cleaned

    # ── Normalize integrations to list ──
    integrations = raw.get('integrations', [])
    if isinstance(integrations, dict):
        integrations = [
            {'name': k, **v} if isinstance(v, dict) else {'name': k, 'value': v}
            for k, v in integrations.items()
        ]
    elif not isinstance(integrations, list):
        integrations = []

    # ── Normalize 'links' → 'urls' ──
    urls = raw.get('urls', {})
    if not isinstance(urls, dict):
        urls = {}
    links = raw.get('links')
    if isinstance(links, dict) and not urls:
        urls = links

    # ── Extract integration API keys into secrets (mirrors env_bridge logic) ──
    for entry in integrations:
        if not isinstance(entry, dict):
            continue
        api_key = entry.get('api_key', '')
        name = entry.get('name', '')
        if api_key and isinstance(api_key, str) and name and isinstance(name, str):
            # Derive env var name: "Resend" → "RESEND_API_KEY"
            env_name = f"{name.upper().replace(' ', '_').replace('-', '_')}_API_KEY"
            # P1-5: Strip embedded newlines/tabs
            cleaned_key = api_key.strip().replace('\n', '').replace('\r', '').replace('\t', '')
            # Explicit api_keys/secrets sections take priority
            if env_name not in secrets and cleaned_key:
                secrets[env_name] = cleaned_key

    return ContentManifest(
        branding=raw.get('branding', {}) if isinstance(raw.get('branding'), dict) else {},
        founder=raw.get('founder', {}) if isinstance(raw.get('founder'), dict) else {},
        urls=urls,
        pricing=raw.get('pricing', {}) if isinstance(raw.get('pricing'), dict) else {},
        integrations=integrations,
        scenarios=raw.get('scenarios', []) if isinstance(raw.get('scenarios'), list) else [],
        tech_stack=raw.get('tech_stack', {}) if isinstance(raw.get('tech_stack'), (dict, list)) else {},
        domain=raw.get('domain', '') if isinstance(raw.get('domain'), str) else '',
        ai_model=raw.get('ai_model', '') if isinstance(raw.get('ai_model'), str) else '',
        email_rules=raw.get('email_rules', {}) if isinstance(raw.get('email_rules'), dict) else {},
        compliance=raw.get('compliance', {}) if isinstance(raw.get('compliance'), dict) else {},
        secrets=secrets,
        config=raw.get('config', {}) if isinstance(raw.get('config'), dict) else {},
    )


def _load_manifest_literals(project_dir: str) -> List[str]:
    """Load content_manifest.json and flatten all string values into a list.

    Recursively traverses dicts and lists to extract every string value.
    Used to cross-reference requirement text against known manifest literals.

    F-1 (ITR-13): Checks multiple locations — project root first (where the
    requirements tool saves it), then docs/ (backward compat).

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of string values found in the manifest. Empty if no manifest.
    """
    manifest_path = _find_manifest_path(project_dir)
    if not manifest_path:
        return []

    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[MANIFEST PARSER] Failed to load content_manifest.json: {e}")
        return []

    literals: List[str] = []
    _extract_strings(data, literals)
    return literals


def _extract_strings(obj: Any, out: List[str]) -> None:
    """Recursively extract all string values from a JSON-like structure."""
    if isinstance(obj, str):
        if obj.strip():  # Skip empty strings
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_strings(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _extract_strings(item, out)


def _match_literals(text: str, literals: List[str]) -> List[str]:
    """Return manifest literal strings that appear in the requirement text.

    Args:
        text: The requirement text to search in.
        literals: List of manifest string values to look for.

    Returns:
        List of literals found in text. Empty if none match.
    """
    return [lit for lit in literals if lit in text]


# ── U-8 (ITR-29): Per-Requirement Scoped Literal Matching ───────────────
# Instead of assigning the same global literals to every requirement, this
# uses a sliding window around where the requirement text appears in the
# original prompt to extract only contextually-relevant literals.

# Patterns for extracting structural literals from text
_LITERAL_PATTERNS = [
    re.compile(r'\$\d+(?:,\d{3})*(?:\.\d{1,2})?(?:/(?:mo|month|yr|year|week|day|hr|hour|user|seat)\w*)?', re.IGNORECASE),  # Prices
    re.compile(r'https?://[^\s)>"\'<,\]]+', re.IGNORECASE),  # URLs
    re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'),  # Emails
    re.compile(r'\b\d+\s*(?:miles?|km|meters?|feet|ft)\b', re.IGNORECASE),  # Distances
]

_SCOPED_WINDOW_SIZE = 500  # chars on each side of the match


# Maximum expected_literals per requirement (ISS-3 cap)
_MAX_LITERALS_PER_REQ = 8


def _match_literals_scoped(req_text: str, prompt: str) -> List[str]:
    """Match literals scoped to the paragraph context of the requirement.

    Uses a sliding window of ~500 chars around where the requirement text
    appears in the original prompt. Only includes structural literals
    (prices, URLs, emails, distances) found within that window.

    This prevents a pricing requirement from getting email literals and
    vice versa — each requirement gets only the literals from its own
    context in the prompt.

    Args:
        req_text: The requirement text to locate in the prompt.
        prompt: The full original user prompt text.

    Returns:
        List of structural literals found in the scoped window.
        Empty list if req_text is not found in the prompt.
    """
    if not req_text or not prompt:
        return []

    # Find the requirement text in the prompt (case-insensitive)
    prompt_lower = prompt.lower()
    req_lower = req_text.lower()

    # Try to find meaningful words from the req in the prompt
    # Use the longest substring match (at least 20 chars) for positioning
    match_pos = -1

    # Strategy 1: Direct substring match
    idx = prompt_lower.find(req_lower)
    if idx >= 0:
        match_pos = idx
    else:
        # Strategy 2: Find the section with the most keyword matches.
        # We pre-compute sections, then score each one by how many
        # keywords from the requirement appear in it.
        pass  # match_pos set below via section scoring

    # Extract the scoped window using paragraph/section boundaries
    # Find the nearest section headers (## headings or double newlines)
    section_breaks = [0]
    for m in re.finditer(r'\n(?:##?\s|\n)', prompt):
        section_breaks.append(m.start())
    section_breaks.append(len(prompt))

    # If we got a direct match, find which section contains it
    if match_pos >= 0:
        section_start = 0
        section_end = len(prompt)
        for i in range(len(section_breaks) - 1):
            if section_breaks[i] <= match_pos < section_breaks[i + 1]:
                section_start = section_breaks[i]
                section_end = section_breaks[i + 1]
                break
    else:
        # Strategy 2 continued: Score each section by keyword density
        words = [w for w in req_lower.split() if len(w) >= 4]
        best_score = 0
        section_start = 0
        section_end = len(prompt)
        for i in range(len(section_breaks) - 1):
            s_start = section_breaks[i]
            s_end = section_breaks[i + 1]
            section_text = prompt_lower[s_start:s_end]
            score = sum(1 for w in words if w in section_text)
            if score > best_score:
                best_score = score
                section_start = s_start
                section_end = s_end
        if best_score < 2:
            return []  # Not enough keyword matches

    scoped_text = prompt[section_start:section_end]

    # Extract structural literals from the scoped window
    literals: List[str] = []
    for pattern in _LITERAL_PATTERNS:
        for m in pattern.finditer(scoped_text):
            val = m.group().rstrip('.,;:!?)"\'>')
            if val and val not in literals:
                literals.append(val)

    return literals[:_MAX_LITERALS_PER_REQ]


def _load_manifest_dict(project_dir: str) -> Dict:
    """Load content_manifest.json and return the raw dict.

    Unlike _load_manifest_literals which flattens all values into a list,
    this returns the structured dict so category-based mapping can extract
    values from specific sections.

    F-1 (ITR-13): Checks multiple locations — project root first (where the
    requirements tool saves it), then docs/ (backward compat).

    Args:
        project_dir: Path to the project directory.

    Returns:
        Manifest dict, or empty dict if no manifest exists.
    """
    manifest_path = _find_manifest_path(project_dir)
    if not manifest_path:
        return {}

    try:
        with open(manifest_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[MANIFEST PARSER] Failed to load content_manifest.json: {e}")
        return {}


def _match_literals_by_category(
    category: str, manifest: Dict, req_text: str = ""
) -> List[str]:
    """Match manifest literals to a requirement based on its category.

    Two-pass approach (ISS-3 fix):
    1. Extract ALL literals from the category's manifest sections (category gate)
    2. Filter by text-relevance to the specific requirement (per-req filter)
    3. If no text-relevant literals found, fall back to category-level (ensures
       every req gets at least some literals)
    4. Cap at _MAX_LITERALS_PER_REQ to prevent literal flooding

    Universal: works for any project with a content_manifest.json.

    Args:
        category: The requirement category (e.g., 'branding', 'url').
        manifest: The full content_manifest.json dict.
        req_text: The requirement's text for per-requirement relevance filtering.

    Returns:
        List of string values from the matched manifest sections,
        filtered by relevance and capped.
    """
    keys = _CATEGORY_MANIFEST_KEYS.get(category, [])
    all_literals: List[str] = []
    for key in keys:
        section = manifest.get(key)
        if section:
            _extract_strings(section, all_literals)

    if not all_literals:
        return []

    # ISS-3: Per-requirement text relevance filter
    # Extract meaningful words from requirement text (>= 3 chars, not stopwords)
    if req_text:
        _STOPWORDS = {
            'the', 'and', 'for', 'with', 'from', 'that', 'this', 'are', 'was',
            'will', 'has', 'have', 'not', 'all', 'can', 'should', 'must',
            'each', 'every', 'any', 'their', 'they', 'when', 'into', 'also',
        }
        req_words = {
            w.lower()
            for w in req_text.split()
            if len(w) >= 3 and w.lower() not in _STOPWORDS
        }

        req_text_lower = req_text.lower()

        # Score each literal by word overlap with requirement text
        scored: List[tuple] = []
        for lit in all_literals:
            lit_lower = lit.lower()
            # Direct substring match (strongest signal)
            if any(w in lit_lower for w in req_words if len(w) >= 4):
                scored.append((lit, 2))  # High relevance
            # URL/price/email patterns — only medium if domain words match req
            elif any(p in lit for p in ('$', 'http', '@', '.com', '.io')):
                # Extract domain words from URL/email for relevance check
                lit_domain = lit_lower.replace('https://', '').replace('http://', '').split('/')[0]
                # Strip email prefix (user@domain → domain)
                if '@' in lit_domain:
                    lit_domain = lit_domain.split('@')[-1]
                domain_parts = [p for p in lit_domain.split('.') if len(p) > 3]
                if any(part in req_text_lower for part in domain_parts):
                    scored.append((lit, 1))  # Medium: domain word in req text
                elif '$' in lit:
                    scored.append((lit, 1))  # Price literals always medium
                else:
                    scored.append((lit, 0))  # Low: unrelated URL/email
            else:
                scored.append((lit, 0))  # Low relevance

        # Take high-relevance first, then medium, then low
        scored.sort(key=lambda x: -x[1])

        # If we have any high/medium relevance matches, prefer those
        relevant = [lit for lit, score in scored if score > 0]
        if relevant:
            return relevant[:_MAX_LITERALS_PER_REQ]
        # No high/medium matches — fall back to category literals BUT exclude
        # URL-like literals that scored 0 (prevents URL flooding)
        _URL_LIKE = ('http', '.com', '.io', '.org', '.net', '@')
        non_url = [lit for lit, score in scored
                   if not any(p in lit for p in _URL_LIKE)]
        if non_url:
            return non_url[:_MAX_LITERALS_PER_REQ]
        return []

    # Fallback (no req_text): cap the full category literals
    return all_literals[:_MAX_LITERALS_PER_REQ]
