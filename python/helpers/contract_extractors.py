"""
Contract Extractors — Deterministic extraction functions for prompt contracts.

Extracted from prompt_contract_parser.py during modularization.
Contains all extraction logic: assertions, features, behaviors, env vars,
compliance requirements, user journeys, checklist items, and implied features.

All extraction uses regex patterns — no LLM calls, sub-second execution.

These extractors are consumed by prompt_contract_parser.py (the facade)
and use helpers from contract_text_utils.py.
"""

import logging
import re
from typing import Dict, List, Set

from python.helpers.contract_text_utils import (
    _normalize_prompt_text,
    _clean_url,
    _is_valid_person_name,
    _detect_section_context,
    _compute_price_confidence,
    _name_to_route,
    _classify_feature_category,
    _COMPARISON_SECTION_KEYWORDS,
)

logger = logging.getLogger("agix.prompt_contract_parser")


# ─── Extraction Patterns ──────────────────────────────────────────────

# URLs: full URLs and naked domains with paths
_URL_FULL_RE = re.compile(
    r'https?://[^\s)>"\'<,]+',
    re.IGNORECASE,
)
_URL_NAKED_RE = re.compile(
    r'(?<!\w)([a-zA-Z0-9-]+\.(?:com|io|dev|app|org|net|co|ai)(?:/[^\s)>"\'<,]*)?)',
    re.IGNORECASE,
)

# Prices: $N, $N.NN, $N/mo, $N/month, $N/yr, etc.
_PRICE_RE = re.compile(
    r'\$\d+(?:,\d{3})*(?:\.\d{1,2})?(?:/(?:mo|month|yr|year|week|day|hr|hour|user|seat)\w*)?',
    re.IGNORECASE,
)

# Price context keywords — prices near these get HIGH confidence
_PRICE_CONTEXT_KEYWORDS = re.compile(
    r'(?:pricing|price|cost|plan|tier|monthly|annual|yearly|prepaid|'
    r'subscription|billing|charge|fee|package|rate)',
    re.IGNORECASE,
)

# Model names: Claude, GPT, Gemini, Llama, Mistral + version info
_MODEL_NAME_RE = re.compile(
    r'(?:Claude\s+(?:Sonnet|Opus|Haiku)\s*\d*(?:\.\d+)?'
    r'|GPT-?\d+(?:\.\d+)?(?:\s*(?:Turbo|Mini|o\d*))?'
    r'|Gemini\s+(?:\d+\.\d+\s+)?(?:Pro|Ultra|Flash|Nano)(?:\s+\d+)?'
    r'|Llama\s*\d+(?:\.\d+)?'
    r'|Mistral\s*\w*'
    r'|o\d+-(?:mini|preview))',
    re.IGNORECASE,
)

# Email addresses
_EMAIL_RE = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
)

# Person names: Title Case words near context clues (SAME LINE only)
# Uses [ \t] instead of \s to prevent cross-line matching.
# Context keyword uses re.IGNORECASE; name capture requires Title Case
# via a post-match validation (not regex flag).
_PERSON_CONTEXT_RE = re.compile(
    r'(?:founder|ceo|cto|owner|manager|author|creator)[ \t]*:[ \t]*'
    r'([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+)',
    re.IGNORECASE,
)


# ─── Assertion Extraction ─────────────────────────────────────────────


def extract_assertions(prompt: str) -> List[dict]:
    """Extract all verifiable assertions from a raw prompt.

    Returns a list of assertion dicts, each with:
        - id: unique identifier (e.g., "URL-001")
        - type: category (url, price, model_name, email, person_name)
        - value: the literal string to check for
        - immutable: whether this value must NOT be changed
        - category: human-readable category
    """
    # RCA-259: Normalize literal escape sequences before extraction
    prompt = _normalize_prompt_text(prompt)

    assertions = []
    seen_values = set()  # Deduplicate
    counters = {}

    def _add(atype: str, value: str, immutable: bool = True,
             category: str = "", confidence: float = 0.9):
        val_key = (atype, value.lower().strip())
        if val_key in seen_values:
            return
        seen_values.add(val_key)
        counters[atype] = counters.get(atype, 0) + 1
        prefix = atype.upper().replace("_", "-")
        assertions.append({
            "id": f"{prefix}-{counters[atype]:03d}",
            "type": atype,
            "value": value.strip(),
            "immutable": immutable,
            "category": category or atype,
            "confidence": round(confidence, 2),
        })

    # Extract URLs (with RCA-259 post-processing)
    for m in _URL_FULL_RE.finditer(prompt):
        url = _clean_url(m.group(0))
        if url:  # Skip if cleaning emptied it
            _add("url", url, immutable=True, category="link", confidence=1.0)

    for m in _URL_NAKED_RE.finditer(prompt):
        domain = _clean_url(m.group(1))
        if not domain:
            continue
        # Skip if already captured as full URL
        if not any(domain in a["value"] for a in assertions if a["type"] == "url"):
            _add("url", domain, immutable=True, category="link", confidence=0.8)

    # Extract prices (with context-based confidence scoring)
    for m in _PRICE_RE.finditer(prompt):
        conf = _compute_price_confidence(prompt, m.start(), m.end(), m.group(0))
        _add("price", m.group(0), immutable=True, category="pricing", confidence=conf)

    # Extract model names (with U-9 API slug enrichment)
    for m in _MODEL_NAME_RE.finditer(prompt):
        _add("model_name", m.group(0), immutable=True, category="ai_model",
             confidence=0.95)

    # U-9 ROOT CAUSE FIX: Enrich model_name assertions with resolved API slugs.
    # This ensures downstream consumers (proactive injection, contract runner)
    # know the EXACT OpenRouter slug to use/search for in code.
    try:
        from python.helpers.model_resolver import resolve_model_slug
        for assertion in assertions:
            if assertion.get("type") == "model_name" and "resolved_slug" not in assertion:
                slug = resolve_model_slug(assertion["value"], catalog={})
                if slug:
                    assertion["resolved_slug"] = slug
    except Exception as e:
        logger.warning(f"[PROMPT CONTRACT] Model slug resolution failed: {e}")

    # Extract emails
    for m in _EMAIL_RE.finditer(prompt):
        _add("email", m.group(0), immutable=True, category="contact",
             confidence=0.95)

    # Extract person names (near context clues)
    for m in _PERSON_CONTEXT_RE.finditer(prompt):
        name = m.group(1).strip()
        # Reject cross-line matches (newline in captured name)
        if "\n" in name or "\r" in name:
            continue
        # Must be proper Title Case and 2+ words
        if not _is_valid_person_name(name):
            continue
        _add("person_name", name, immutable=True, category="identity",
             confidence=0.9)

    # Phase 4, Fix F: Final URL deduplication pass.
    # Even with inline dedup at extraction time, edge cases can slip through:
    # e.g., "mainstreet-review.com" vs "https://mainstreet-review.com" if
    # _clean_url strips the protocol for one but not the other.
    assertions = _dedup_url_assertions(assertions)

    return assertions


def _dedup_url_assertions(assertions: list) -> list:
    """Remove URL assertions whose value is a substring of another URL assertion.

    This handles cases like:
    - "example.com" and "https://example.com" → keep only the full URL
    - "api.example.com" and "https://api.example.com/v1" → keep only the longer one

    Non-URL assertions are returned unchanged.
    """
    urls = [a for a in assertions if a["type"] == "url"]
    non_urls = [a for a in assertions if a["type"] != "url"]

    if len(urls) <= 1:
        return assertions

    keep = []
    for url in urls:
        # Skip if this URL's value is a substring of another URL's value
        is_subset = any(
            url["value"] != other["value"] and url["value"] in other["value"]
            for other in urls
        )
        if not is_subset:
            keep.append(url)

    return non_urls + keep


# ─── Feature Extraction ───────────────────────────────────────────────
#
# Universal page/dashboard/form/integration detection from raw prompts.
# Produces structured feature dicts used for requirements seeding and
# feature registry classification. (PDV gate was removed in ITR-44 RCA.)

# Page/dashboard/form surface keywords
_FEATURE_SURFACE_RE = re.compile(
    r'\b([A-Z][\w\s]{1,40}?)\s+'
    r'(page|dashboard|form|panel|view|screen|tab|modal|wizard|portal)\b',
    re.IGNORECASE,
)

# Integration features: "X integration", "integrate with X"
_FEATURE_INTEGRATION_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9.]+(?:\s+[A-Z][a-zA-Z0-9.]*)*)\s+'
    r'(?:integration|api|sdk|client|setup|checkout|billing|payment)',
    re.IGNORECASE,
)

_FEATURE_INTEGRATION_VERB_RE = re.compile(
    r'(?:integrate|connect|hook|wire)\s+(?:with|to|into)\s+'
    r'([A-Z][a-zA-Z0-9.]+)',
    re.IGNORECASE,
)

# ─── F-7 (RCA-343 ISSUE-7): Functional Capability Extraction ─────────
# Catches verb-based functional requirements that aren't tied to a UI surface.
# "schedule email campaigns", "score leads", "track opens", "generate reports",
# "filter prospects", "search by name", "export data", "automate workflows"
_FEATURE_CAPABILITY_RE = re.compile(
    r'\b(schedule|score|track|generate|filter|search|export|import|automate|'
    r'analyze|monitor|sync|validate|aggregate|calculate|notify|detect|'
    r'classify|rank|prioritize|archive|backup|restore|verify|audit|log|'
    r'enrich|deduplicate|merge|transform|parse|crawl|scrape|index)'
    r'\s+([a-z][a-z0-9\s]{2,40}?)'
    r'(?:\s+(?:by|based\s+on|from|to|in|for|with|using)\s+|\.|,|$)',
    re.IGNORECASE,
)


def extract_features(prompt: str) -> List[Dict[str, str]]:
    """Extract structural features (pages, dashboards, forms, integrations).

    Each feature is a dict with:
        - name: Human-readable feature name
        - expected_route: Inferred URL route (e.g., /outreach-dashboard)
        - category: page | dashboard | form | api | integration

    Deterministic: same prompt always produces same features.
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    prompt = _normalize_prompt_text(prompt)
    features: List[Dict[str, str]] = []
    seen_names: Set[str] = set()

    def _add_feature(name: str, category: str, surface: str = "") -> None:
        """Add a feature if not already seen (content-hash dedup).

        RCA-358 F-3: Normalize embedded whitespace (\\n, \\r, tabs)
        to single spaces before storage.

        RCA-358 F-1: Route intelligence — regex is a HELPER, not the
        decision-maker. Only UI surface categories (page, dashboard, form)
        get candidate routes. Integration and capability features get
        expected_route=None because the architect LLM decides their
        routes following web platform best practices. The gate system
        should validate against the architect's page_map, not regex.
        """
        # F-3: Normalize ALL whitespace (including embedded \n, \r) to spaces
        name = re.sub(r'\s+', ' ', name).strip()
        normalized = name.lower()
        if normalized in seen_names or len(normalized) < 3:
            return
        # Filter generic false positives
        if normalized in (
            "the", "a", "an", "this", "that", "your", "my", "our",
            "new", "main", "home", "app", "application",
        ):
            return
        seen_names.add(normalized)
        full_name = f"{name} {surface}".strip() if surface else name

        # F-1: Only UI surface categories get candidate routes.
        # Capabilities and integrations are behavioral/architectural —
        # the architect LLM decides their routing, not regex.
        _UI_SURFACE_CATEGORIES = {"page", "dashboard", "form"}
        if category in _UI_SURFACE_CATEGORIES:
            candidate_route = _name_to_route(full_name)
        else:
            candidate_route = None

        features.append({
            "name": full_name,
            "expected_route": candidate_route,
            "category": category,
        })

    # ── Pass 1: Named UI surfaces (page, dashboard, form, etc.) ──
    for m in _FEATURE_SURFACE_RE.finditer(prompt):
        raw_name = m.group(1).strip()
        surface = m.group(2).strip()
        category = _classify_feature_category(surface)
        _add_feature(raw_name, category, surface)

    # ── Pass 2: Integration features ──
    for m in _FEATURE_INTEGRATION_RE.finditer(prompt):
        name = m.group(1).strip()
        if len(name) > 1:
            _add_feature(name, "integration")

    for m in _FEATURE_INTEGRATION_VERB_RE.finditer(prompt):
        name = m.group(1).strip()
        if len(name) > 1:
            _add_feature(name, "integration")

    # ── Pass 3: Functional capabilities (F-7, RCA-343 ISSUE-7) ──
    # Catch verb-based requirements: "schedule emails", "score leads", etc.
    for m in _FEATURE_CAPABILITY_RE.finditer(prompt):
        verb = m.group(1).strip()
        obj = m.group(2).strip()
        # Build a capability name: "schedule email campaigns"
        cap_name = f"{verb} {obj}"
        if len(cap_name) > 5:
            _add_feature(cap_name, "capability")

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(features)} features "
        f"from prompt ({len(prompt)} chars)"
    )

    return features


# ─── Behavior Extraction ──────────────────────────────────────────────
#
# Detects behavioral requirements: temporal constraints, data operations,
# sequences, and conditional logic.

# Temporal constraints
_BEHAVIOR_TEMPORAL_RE = re.compile(
    r'(?:business[\s-]*hours?|scheduled?|cron|queued?|delayed?|'
    r'daily|weekly|monthly|interval|timed|recurring|automated)'
    r'[^\n\r]{0,40}'
    r'(?:delivery|send|email|notification|processing|execution|sync|update|'
    r'run|task|job|check|report|follow[\s-]*up|reminder|outreach|campaign)',
    re.IGNORECASE,
)

# Data operation keywords
_BEHAVIOR_DATA_OPS_RE = re.compile(
    r'(?:filter(?:able|ed|ing)?|sort(?:able|ed|ing)?|search(?:able)?|'
    r'paginated?|pagination|full[\s-]*text[\s-]*search)',
    re.IGNORECASE,
)

# Sequence/multi-step patterns
_BEHAVIOR_SEQUENCE_RE = re.compile(
    r'(?:(\d+)[\s-]*(?:step|email|stage|phase|message)[\s-]*'
    r'(?:sequence|drip|flow|wizard|pipeline|funnel|chain|process)'
    r'|(?:sequence|drip|flow|wizard|pipeline|funnel|chain)\s+'
    r'(?:of\s+)?(\d+))',
    re.IGNORECASE,
)

# Configurability/scalability patterns (SS-9, RCA-340 Phase 5)
# Captures language that implies "don't hardcode this" requirements.
_BEHAVIOR_CONFIGURABLE_RE = re.compile(
    r'(?:configur(?:able|ed?|ation)'
    r'|dynamic(?:ally)?'
    r'|expand(?:able|ed|ing)?'
    r'|systematic(?:ally)?'
    r'|scal(?:able|e|ing)'
    r'|progressive(?:ly)?'
    r'|env(?:ironment)?[\s-]*(?:var(?:iable)?|driven|based)'
    r'|database[\s-]*driven'
    r'|api[\s-]*driven'
    r'|parameteriz(?:e|ed|able)'
    r'|customiz(?:e|ed|able))',
    re.IGNORECASE,
)


def extract_behaviors(prompt: str) -> List[Dict[str, str]]:
    """Extract behavioral requirements from a raw prompt.

    Each behavior is a dict with:
        - name: Description of the behavioral requirement
        - verify_pattern: Regex pattern to verify in source code

    Deterministic: same prompt always produces same behaviors.
    """
    if not prompt or len(prompt.strip()) < 15:
        return []

    prompt = _normalize_prompt_text(prompt)
    behaviors: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # F-2 (RCA-358): Generic verify_pattern used by Pass 4 configurable behaviors.
    # Behaviors matched with this pattern get verified=False because the pattern
    # is too broad (matches ANY project), providing zero verification value.
    _GENERIC_CONFIGURABLE_PATTERN = (
        r'(?:process\.env|config|getenv|os\.environ|DATABASE_URL|'
        r'dynamicConfig|Settings|\.env|env\()'
    )

    def _add_behavior(name: str, verify_pattern: str, verified: bool = True) -> None:
        """Add a behavior if not already seen.

        RCA-358 F-3: Normalize embedded whitespace (\\n, \\r, tabs)
        to single spaces. Regex captures can span lines — sanitize
        before storage so downstream consumers get clean names.

        RCA-358 F-2: Minimum word count check — reject behaviors with
        <3 words. Single-word matches like 'scale' from narrative prose
        are noise with zero verification value.
        """
        # F-3: Normalize ALL whitespace (including embedded \n, \r) to spaces
        name = re.sub(r'\s+', ' ', name).strip()

        # F-2: Reject behaviors with fewer than 3 words, BUT only for
        # unverified/generic captures (Pass 4). Passes 1-3 have specific
        # verify_patterns (cron, filter, sort, etc.) that provide real
        # verification value even with short names like 'filterable'.
        word_count = len(name.split())
        if not verified and word_count < 3:
            return

        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        entry = {
            "name": name,
            "verify_pattern": verify_pattern,
        }
        # F-2: Flag behaviors with generic verify_patterns as unverified
        # so downstream consumers know this is a candidate, not confirmed.
        if not verified:
            entry["verified"] = False
        behaviors.append(entry)

    # ── Pass 1: Temporal constraints ──
    for m in _BEHAVIOR_TEMPORAL_RE.finditer(prompt):
        text = m.group(0).strip()
        _add_behavior(
            text,
            r'(?:cron|schedule|queue|setTimeout|setInterval|business.?hours)',
        )

    # ── Pass 2: Data operations ──
    for m in _BEHAVIOR_DATA_OPS_RE.finditer(prompt):
        text = m.group(0).strip()
        # Build verify pattern from the specific operation
        op = text.lower()
        if "filter" in op:
            _add_behavior(text, r'(?:filter|where|query|criteria)')
        elif "sort" in op:
            _add_behavior(text, r'(?:sort|order[Bb]y|orderBy|ORDER BY)')
        elif "search" in op:
            _add_behavior(text, r'(?:search|fulltext|full.text|LIKE|ilike|contains)')
        elif "pagina" in op:
            _add_behavior(text, r'(?:pagina|page.?size|limit|offset|cursor|skip|take)')

    # ── Pass 3: Sequences ──
    for m in _BEHAVIOR_SEQUENCE_RE.finditer(prompt):
        count = m.group(1) or m.group(2)
        text = m.group(0).strip()
        _add_behavior(
            text,
            rf'(?:sequence|step|stage|phase|drip|{count})',
        )

    # ── Pass 4: Configurability/Scalability (SS-9, RCA-340 Phase 5) ──
    # F-2 (RCA-358): Capture surrounding context (±sentence) so the behavior
    # name is meaningful, not just a standalone keyword like 'scale'.
    for m in _BEHAVIOR_CONFIGURABLE_RE.finditer(prompt):
        # Capture the sentence containing the match for context.
        # Find sentence boundaries (period, newline, or string boundary).
        sent_start = max(
            prompt.rfind('.', 0, m.start()) + 1,
            prompt.rfind('\n', 0, m.start()) + 1,
            0,
        )
        sent_end = len(prompt)
        for delim in ('.', '\n'):
            pos = prompt.find(delim, m.end())
            if pos != -1 and pos < sent_end:
                sent_end = pos
        # Use the sentence as the behavior name (trimmed, max 120 chars)
        context_text = prompt[sent_start:sent_end].strip()[:120]
        if not context_text:
            context_text = m.group(0).strip()
        _add_behavior(
            context_text,
            _GENERIC_CONFIGURABLE_PATTERN,
            verified=False,  # F-2: generic pattern → unverified candidate
        )

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(behaviors)} behaviors "
        f"from prompt ({len(prompt)} chars)"
    )

    return behaviors


# ─── Env Var Extraction ───────────────────────────────────────────────
#
# Maps service/integration mentions in prompts to required env vars.
# Universal: supports common SaaS integrations seen across projects.

# Service name patterns → list of (ENV_VAR_NAME, example_value) tuples
_ENV_VAR_MAP: Dict[str, List[tuple]] = {
    r'\bstripe\b': [
        ("STRIPE_SECRET_KEY", "sk_" + "test_xxx"),
        ("STRIPE_WEBHOOK_SECRET", "whsec_xxx"),
    ],
    r'\bresend\b': [
        ("RESEND_API_KEY", "re_xxx"),
    ],
    r'\bopenrouter\b': [
        ("OPENROUTER_API_KEY", "sk-or-xxx"),
    ],
    r'\b(?:prisma|postgres(?:ql)?|supabase)\b': [
        ("DATABASE_URL", "postgresql://user:pass@localhost:5432/dbname"),
    ],
    r'\bgoogle\s*places?\b': [
        ("GOOGLE_PLACES_API_KEY", "AIza_xxx"),
    ],
    r'\bperplexity\b': [
        ("PERPLEXITY_API_KEY", "pplx-xxx"),
    ],
    r'\bcal\.com\b': [
        ("CALCOM_API_KEY", "cal_live_xxx"),
    ],
    r'\bclerk\b': [
        ("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_xxx"),
        ("CLERK_SECRET_KEY", "sk_" + "test_xxx"),
    ],
    r'\bsendgrid\b': [
        ("SENDGRID_API_KEY", "SG.xxx"),
    ],
    r'\btwilio\b': [
        ("TWILIO_ACCOUNT_SID", "ACxxx"),
        ("TWILIO_AUTH_TOKEN", "xxx"),
    ],
    r'\bcloudinary\b': [
        ("CLOUDINARY_URL", "cloudinary://key:secret@cloud"),
    ],
    r'\bredis\b': [
        ("REDIS_URL", "redis://localhost:6379"),
    ],
    r'\b(?:next.?auth|auth\.js)\b': [
        ("NEXTAUTH_SECRET", "xxx"),
        ("NEXTAUTH_URL", "http://localhost:3000"),
    ],
}


def extract_env_vars(prompt: str) -> List[Dict[str, str]]:
    """Extract required env vars from prompt based on integration mentions.

    Each env var is a dict with:
        - name: ENV_VAR_NAME (e.g., STRIPE_SECRET_KEY)
        - service: originating service name
        - example: placeholder example value

    Deterministic: same prompt always produces same env vars.
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    prompt_lower = prompt.lower()
    env_vars: List[Dict[str, str]] = []
    seen_names: Set[str] = set()

    for pattern, vars_list in _ENV_VAR_MAP.items():
        if re.search(pattern, prompt, re.IGNORECASE):
            for var_name, example in vars_list:
                if var_name not in seen_names:
                    seen_names.add(var_name)
                    # Extract the service name from the pattern
                    service = re.sub(r'[\\b()?|]', '', pattern).strip().split('\\')[0]
                    service = re.sub(r'[^a-z.]', '', service)
                    env_vars.append({
                        "name": var_name,
                        "service": service,
                        "example": example,
                    })

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(env_vars)} env vars "
        f"from prompt ({len(prompt)} chars)"
    )
    return env_vars


# ─── Compliance Extraction (U-4, RCA-302) ─────────────────────────────
#
# Detects regulatory/legal requirements: CAN-SPAM, GDPR, TCPA, CCPA,
# HIPAA, SOC2, PCI. Uses enhanced deterministic regex with linguistic
# awareness (obligation verb sets, compliance action patterns).
# All compliance items get confidence=1.0 (never skipped by threshold).

# Explicit compliance framework names
_COMPLIANCE_FRAMEWORK_RE = re.compile(
    r'\b(CAN[\s-]*SPAM|GDPR|TCPA|CCPA|HIPAA|SOC[\s-]*2|PCI[\s-]*DSS?|FERPA|COPPA)\b',
    re.IGNORECASE,
)

# Compliance action patterns — keywords that imply regulatory requirements
_COMPLIANCE_ACTION_RE = re.compile(
    r'\b('
    r'unsubscrib(?:e|ing|ed)'
    r'|opt(?:ing|ed)?[\s-]*out'
    r'|privacy[\s-]*policy'
    r'|cookie[\s-]*consent'
    r'|data[\s-]*(?:deletion|erasure|portability|retention|protection)'
    r'|terms[\s-]*(?:of[\s-]*service|and[\s-]*conditions)'
    r'|consent[\s-]*(?:banner|form|mechanism|management)'
    r'|do[\s-]*not[\s-]*(?:sell|track|share)'
    r'|right[\s-]*to[\s-]*(?:be[\s-]*forgotten|deletion|erasure|access)'
    r'|age[\s-]*verification'
    r'|parental[\s-]*consent'
    r')\b',
    re.IGNORECASE,
)

# Obligation context — verbs that indicate mandatory requirements
_OBLIGATION_CONTEXT_RE = re.compile(
    r'\b(?:must|required|shall|need(?:s|ed)?[\s]+to|mandatory|obligat(?:ed|ory)'
    r'|not[\s]+optional|legally[\s]+required|compliance|comply|compliant)\b',
    re.IGNORECASE,
)

# Natural language opt-out patterns (catches "give recipients a way to stop")
_NATURAL_OPTOUT_RE = re.compile(
    r'\b(?:stop[\s]+receiving|stop[\s]+getting|way[\s]+to[\s]+(?:stop|cancel|remove)'
    r'|cease[\s]+(?:sending|receiving|communication)'
    r'|remove[\s]+(?:from[\s]+(?:list|mailing|email))'
    r'|no[\s]+longer[\s]+(?:receive|want|wish)'
    r'|withdraw[\s]+consent'
    r'|revoke[\s]+(?:consent|permission|access))\b',
    re.IGNORECASE,
)


# Mapping: compliance framework → verify_pattern regex + description
_FRAMEWORK_VERIFY_MAP = {
    "can-spam": {
        "verify_pattern": r"(?i)(?:unsubscribe|opt[\s._-]*out|STOP|remove[\s._-]*from[\s._-]*list|email[\s._-]*preference)",
        "description": "unsubscribe/opt-out mechanism",
    },
    "gdpr": {
        "verify_pattern": r"(?i)(?:privacy[\s._-]*policy|gdpr|data[\s._-]*(?:protection|deletion|erasure|portability)|cookie[\s._-]*consent|consent[\s._-]*(?:banner|form))",
        "description": "privacy policy and data protection",
    },
    "tcpa": {
        "verify_pattern": r"(?i)(?:opt[\s._-]*out|STOP|tcpa|sms[\s._-]*consent|text[\s._-]*(?:opt|stop)|do[\s._-]*not[\s._-]*call)",
        "description": "SMS/phone opt-out mechanism",
    },
    "ccpa": {
        "verify_pattern": r"(?i)(?:do[\s._-]*not[\s._-]*sell|ccpa|privacy[\s._-]*(?:policy|rights)|data[\s._-]*(?:deletion|access))",
        "description": "consumer privacy rights",
    },
    "hipaa": {
        "verify_pattern": r"(?i)(?:hipaa|phi|protected[\s._-]*health|baa|business[\s._-]*associate)",
        "description": "health data protection",
    },
    "soc-2": {
        "verify_pattern": r"(?i)(?:soc[\s._-]*2|audit[\s._-]*log|access[\s._-]*control|encryption)",
        "description": "security controls and audit",
    },
    "pci": {
        "verify_pattern": r"(?i)(?:pci|credit[\s._-]*card|payment[\s._-]*(?:security|compliance)|tokeniz)",
        "description": "payment card security",
    },
}

# Mapping: compliance action → verify_pattern + framework inference
_ACTION_VERIFY_MAP = {
    "unsubscrib": {
        "verify_pattern": r"(?i)(?:unsubscribe|opt[\s._-]*out|STOP|remove[\s._-]*from[\s._-]*list)",
        "framework": "CAN-SPAM",
        "name": "unsubscribe mechanism",
    },
    "opt": {
        "verify_pattern": r"(?i)(?:opt[\s._-]*out|unsubscribe|STOP|preference)",
        "framework": "CAN-SPAM",
        "name": "opt-out mechanism",
    },
    "privacy": {
        "verify_pattern": r"(?i)(?:privacy[\s._-]*policy|privacy|terms)",
        "framework": "GDPR",
        "name": "privacy policy",
    },
    "cookie": {
        "verify_pattern": r"(?i)(?:cookie[\s._-]*(?:consent|banner|policy|notice)|consent[\s._-]*banner)",
        "framework": "GDPR",
        "name": "cookie consent",
    },
    "data": {
        "verify_pattern": r"(?i)(?:data[\s._-]*(?:deletion|erasure|portability|protection|access)|delete[\s._-]*(?:my|user)[\s._-]*data)",
        "framework": "GDPR",
        "name": "data protection rights",
    },
    "terms": {
        "verify_pattern": r"(?i)(?:terms[\s._-]*(?:of[\s._-]*service|and[\s._-]*conditions)|tos)",
        "framework": "Legal",
        "name": "terms of service",
    },
    "consent": {
        "verify_pattern": r"(?i)(?:consent[\s._-]*(?:banner|form|mechanism|management)|user[\s._-]*consent)",
        "framework": "GDPR",
        "name": "consent management",
    },
    "do": {
        "verify_pattern": r"(?i)(?:do[\s._-]*not[\s._-]*(?:sell|track|share))",
        "framework": "CCPA",
        "name": "do-not-sell/track",
    },
    "right": {
        "verify_pattern": r"(?i)(?:right[\s._-]*to[\s._-]*(?:be[\s._-]*forgotten|deletion|erasure|access))",
        "framework": "GDPR",
        "name": "right to erasure/access",
    },
    "age": {
        "verify_pattern": r"(?i)(?:age[\s._-]*(?:verification|gate|check)|13[\s._-]*(?:years|and[\s._-]*under))",
        "framework": "COPPA",
        "name": "age verification",
    },
    "parental": {
        "verify_pattern": r"(?i)(?:parental[\s._-]*consent|parent[\s._-]*(?:approval|permission))",
        "framework": "COPPA",
        "name": "parental consent",
    },
}


def extract_compliance_requirements(prompt: str) -> List[Dict]:
    """Extract regulatory/compliance requirements from a raw prompt.

    Uses enhanced deterministic regex with linguistic patterns:
    - Explicit framework detection (CAN-SPAM, GDPR, TCPA, etc.)
    - Compliance action patterns (unsubscribe, opt-out, privacy policy)
    - Obligation context detection (must, required, mandatory)
    - Natural language opt-out patterns

    Each compliance item is a dict with:
        - name: Description of the compliance requirement
        - type: "compliance"
        - confidence: 1.0 (always enforced, never skipped)
        - verify_pattern: Regex pattern for source code verification
        - hard_requirement: True (always blocking)
        - framework: Regulatory framework (CAN-SPAM, GDPR, etc.)
        - source_sentence: The sentence that triggered extraction

    Returns:
        List of compliance requirement dicts. Empty list if no
        compliance requirements found.
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    prompt = _normalize_prompt_text(prompt)
    # Strip checkbox markers so detection works regardless of formatting
    prompt_clean = re.sub(r'[⬜✅☐☑✓✗✘□■●○]', '', prompt)

    requirements: List[Dict] = []
    seen_keys: Set[str] = set()

    def _add_requirement(
        name: str,
        framework: str,
        verify_pattern: str,
        source_sentence: str,
    ) -> None:
        """Add a compliance requirement if not already seen."""
        key = f"{framework.lower()}:{name.lower()}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        requirements.append({
            "name": f"{framework}: {name}",
            "type": "compliance",
            "confidence": 1.0,
            "verify_pattern": verify_pattern,
            "hard_requirement": True,
            "framework": framework,
            "source_sentence": source_sentence.strip()[:200],
        })

    # ── Pass 1: Explicit framework mentions ──
    # Look for named frameworks (CAN-SPAM, GDPR, etc.)
    for m in _COMPLIANCE_FRAMEWORK_RE.finditer(prompt_clean):
        framework_raw = m.group(1).strip()
        # Normalize framework name
        framework_key = re.sub(r'[\s-]+', '-', framework_raw).lower()

        # Extract the surrounding sentence for context
        start = max(0, prompt_clean.rfind('.', 0, m.start()) + 1)
        end = prompt_clean.find('.', m.end())
        if end == -1:
            end = min(len(prompt_clean), m.end() + 150)
        sentence = prompt_clean[start:end].strip()

        # Look up framework verify pattern
        verify_info = _FRAMEWORK_VERIFY_MAP.get(framework_key, {})
        verify_pattern = verify_info.get(
            "verify_pattern",
            rf"(?i)(?:{re.escape(framework_raw)})",
        )
        description = verify_info.get("description", "compliance")

        _add_requirement(
            name=description,
            framework=framework_raw.upper(),
            verify_pattern=verify_pattern,
            source_sentence=sentence,
        )

    # ── Pass 2: Compliance action patterns ──
    # Look for action keywords (unsubscribe, privacy policy, etc.)
    for m in _COMPLIANCE_ACTION_RE.finditer(prompt_clean):
        action_text = m.group(1).strip()

        # Extract surrounding sentence
        start = max(0, prompt_clean.rfind('.', 0, m.start()) + 1)
        end = prompt_clean.find('.', m.end())
        if end == -1:
            end = min(len(prompt_clean), m.end() + 150)
        sentence = prompt_clean[start:end].strip()

        # Find matching action verify info
        action_key = action_text.split()[0].lower()[:6]  # First word, truncated
        for key, info in _ACTION_VERIFY_MAP.items():
            if action_key.startswith(key[:4]):
                _add_requirement(
                    name=info["name"],
                    framework=info["framework"],
                    verify_pattern=info["verify_pattern"],
                    source_sentence=sentence,
                )
                break

    # ── Pass 3: Natural language opt-out patterns ──
    # Catches sentences like "give recipients a way to stop receiving messages"
    for m in _NATURAL_OPTOUT_RE.finditer(prompt_clean):
        start = max(0, prompt_clean.rfind('.', 0, m.start()) + 1)
        end = prompt_clean.find('.', m.end())
        if end == -1:
            end = min(len(prompt_clean), m.end() + 150)
        sentence = prompt_clean[start:end].strip()

        # Check if there's obligation context nearby
        context_start = max(0, m.start() - 100)
        context_end = min(len(prompt_clean), m.end() + 100)
        context = prompt_clean[context_start:context_end]

        if _OBLIGATION_CONTEXT_RE.search(context):
            _add_requirement(
                name="opt-out mechanism",
                framework="CAN-SPAM",
                verify_pattern=r"(?i)(?:unsubscribe|opt[\s._-]*out|STOP|stop[\s._-]*receiving|remove[\s._-]*from)",
                source_sentence=sentence,
            )

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(requirements)} compliance "
        f"requirements from prompt ({len(prompt)} chars)"
    )

    return requirements


# ─── User Journey Extraction (U-3, RCA-302) ──────────────────────────
#
# Detects user journey flows: actor → action → outcome patterns,
# conditional routing (happy/unhappy paths), and sequential steps.

# Actor nouns — who performs the journey
_JOURNEY_ACTOR_RE = re.compile(
    r'\b(customer|user|client|visitor|recipient|subscriber|reviewer|buyer|seller|admin|owner)\b',
    re.IGNORECASE,
)

# Conditional routing patterns
_JOURNEY_CONDITIONAL_RE = re.compile(
    r'(?:'
    r'(?:if|when|for|based[\s]+on|depending[\s]+on)[\s]+(?:.*?)'
    r'(?:positive|negative|happy|unhappy|good|bad|high|low|satisfied|unsatisfied|4|5[\s-]*star)'
    r'[\s\w-]*(?:review|rating|score|feedback|sentiment|experience)'
    r'|'
    r'(?:positive|negative|happy|unhappy|good|bad|high|low|satisfied|unsatisfied|4|5[\s-]*star)'
    r'[\s\w-]*(?:review|rating|score|feedback|sentiment|experience)'
    r'[\s\w-]*(?:redirect|route|send|direct|take|show|display|link|navigate)'
    r')',
    re.IGNORECASE,
)

# Routing destination patterns
_JOURNEY_DESTINATION_RE = re.compile(
    r'(?:redirect|route|send|direct|take|link|navigate)[\s]+(?:(?:them|user|customer|client)[\s]+)?'
    r'(?:to|toward|towards)[\s]+'
    r'((?:google|yelp|facebook|trustpilot|private|internal|feedback|review|form|page|survey|complaint)'
    r'[\w\s-]*)',
    re.IGNORECASE,
)

# Sequential step markers
_JOURNEY_SEQUENCE_RE = re.compile(
    r'(?:step[\s]*\d|first[\s,]+|then[\s,]+|next[\s,]+|after[\s]+(?:that|which)[\s,]+'
    r'|once[\s]+|finally[\s,]+|lastly[\s,]+)',
    re.IGNORECASE,
)


def extract_user_journeys(prompt: str) -> List[Dict]:
    """Extract user journey flows from a raw prompt.

    Uses enhanced deterministic regex with linguistic patterns:
    - Actor-action-outcome chains
    - Conditional routing (happy/unhappy paths)
    - Sequential step markers

    Each journey item is a dict with:
        - name: Description of the journey step
        - type: "journey"
        - verify_pattern: Regex pattern for source code verification
        - step_order: Ordinal position in the journey (0 if unknown)
        - condition: Conditional context (e.g., "positive sentiment")

    Returns:
        List of journey dicts. Empty list if no journeys found.
    """
    if not prompt or len(prompt.strip()) < 15:
        return []

    prompt = _normalize_prompt_text(prompt)
    journeys: List[Dict] = []
    seen_keys: Set[str] = set()
    step_counter = 0

    def _add_journey(
        name: str,
        verify_pattern: str,
        condition: str = "",
    ) -> None:
        nonlocal step_counter
        key = name.strip().lower()
        if key in seen_keys:
            return
        seen_keys.add(key)
        step_counter += 1
        journeys.append({
            "name": name.strip(),
            "type": "journey",
            "verify_pattern": verify_pattern,
            "step_order": step_counter,
            "condition": condition,
        })

    # ── Pass 1: Conditional routing patterns ──
    for m in _JOURNEY_CONDITIONAL_RE.finditer(prompt):
        text = m.group(0).strip()

        # Determine if happy or unhappy path
        is_positive = bool(re.search(
            r'(?:positive|happy|good|high|satisfied|4[\s-]*star|5[\s-]*star)',
            text, re.IGNORECASE,
        ))
        condition = "positive sentiment" if is_positive else "negative sentiment"

        # Look for routing destination nearby
        context_end = min(len(prompt), m.end() + 200)
        nearby_text = prompt[m.start():context_end]
        dest_match = _JOURNEY_DESTINATION_RE.search(nearby_text)

        if dest_match:
            destination = dest_match.group(1).strip()
            _add_journey(
                name=f"{'happy' if is_positive else 'unhappy'} path → {destination}",
                verify_pattern=rf"(?i)(?:{re.escape(destination.split()[0])}|redirect|route|{'positive' if is_positive else 'negative'}|{'happy' if is_positive else 'unhappy'})",
                condition=condition,
            )
        else:
            _add_journey(
                name=f"{'happy' if is_positive else 'unhappy'} path routing",
                verify_pattern=r"(?i)(?:redirect|route|condition|if|switch|positive|negative|happy|unhappy|sentiment|rating)",
                condition=condition,
            )

    # ── Pass 2: Routing destinations without explicit conditionals ──
    for m in _JOURNEY_DESTINATION_RE.finditer(prompt):
        destination = m.group(1).strip()
        # Skip if already captured in Pass 1
        dest_key = f"→ {destination}".lower()
        if any(dest_key in k for k in seen_keys):
            continue

        start = max(0, prompt.rfind('.', 0, m.start()) + 1)
        end = prompt.find('.', m.end())
        if end == -1:
            end = min(len(prompt), m.end() + 100)
        sentence = prompt[start:end].strip()

        _add_journey(
            name=f"route to {destination}",
            verify_pattern=rf"(?i)(?:{re.escape(destination.split()[0])}|redirect|route|navigate|link)",
            condition="",
        )

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(journeys)} user journeys "
        f"from prompt ({len(prompt)} chars)"
    )

    return journeys


# ─── FIX-13: Implied Feature Inference (Golden-vs-AGIX) ────────────
#
# Post-extraction pass that detects requirements implied by feature patterns.
# E.g., dashboard pages imply auth is needed, email features imply unsubscribe.
# Universal: applies to ALL projects, not project-specific patches.

# Implication rules: (trigger_pattern, trigger_categories, implied_feature)
_IMPLICATION_RULES = [
    {
        "trigger_pattern": r"(?i)(?:dashboard|admin|settings|account|profile.*edit|my[\s_-]*account)",
        "trigger_categories": {"dashboard", "page"},
        "implied_name": "Authentication system (login/signup)",
        "implied_route": "/login",
        "implied_category": "page",
        "dedup_keywords": {"auth", "login", "signup", "sign-in", "signin"},
    },
    {
        "trigger_pattern": r"(?i)(?:email|outreach|campaign|newsletter|mailing|drip|send.*mail)",
        "trigger_categories": {"capability", "integration", "page"},
        "implied_name": "Unsubscribe/opt-out page",
        "implied_route": "/unsubscribe",
        "implied_category": "page",
        "dedup_keywords": {"unsubscribe", "opt-out", "opt_out"},
        "dedup_compliance_keywords": {"unsubscribe", "opt-out", "opt out"},
    },
    {
        "trigger_pattern": r"(?i)(?:stripe|payment|checkout|billing|subscription|purchase|buy)",
        "trigger_categories": {"integration", "capability", "page"},
        "implied_name": "Pricing page",
        "implied_route": "/pricing",
        "implied_category": "page",
        "dedup_keywords": {"pricing", "price", "plan"},
    },
    {
        "trigger_pattern": r"(?i)(?:profile|contact.*form|registration|sign.*up|user.*data|collect.*info)",
        "trigger_categories": {"page", "form", "capability"},
        "implied_name": "Privacy policy page",
        "implied_route": "/privacy",
        "implied_category": "page",
        "dedup_keywords": {"privacy", "privacy-policy", "privacy_policy"},
        "dedup_compliance_keywords": {"privacy"},
    },
]


def infer_implied_features(
    features: List[Dict],
    compliance: List[Dict],
) -> List[Dict]:
    """FIX-13: Infer implied requirements from existing feature patterns.

    Scans the extracted features list for patterns that imply additional
    requirements. E.g., a dashboard page implies authentication is needed.
    Returns ONLY the newly implied features (does not include originals).

    Deduplication: If the implied feature is already present in features
    or compliance, it is NOT added.

    Args:
        features: List of feature dicts from extract_features().
        compliance: List of compliance dicts from extract_compliance_requirements().

    Returns:
        List of implied feature dicts (each with implied=True marker).
    """
    implied: List[Dict] = []
    seen_implied: Set[str] = set()

    # Build a set of existing feature names for dedup
    existing_names = {f["name"].lower() for f in features}
    existing_compliance_names = {
        c.get("name", "").lower() for c in compliance
    }

    for rule in _IMPLICATION_RULES:
        # Check if ANY feature matches this rule
        matched = False
        for feat in features:
            cat = feat.get("category", "")
            name = feat.get("name", "")
            if cat in rule["trigger_categories"] and re.search(
                rule["trigger_pattern"], name
            ):
                matched = True
                break

        if not matched:
            continue

        # Check dedup: is the implied feature already present?
        dedup_kw = rule.get("dedup_keywords", set())
        already_present = any(
            kw in existing_names or any(kw in n for n in existing_names)
            for kw in dedup_kw
        )
        if already_present:
            continue

        # Check compliance dedup
        compliance_kw = rule.get("dedup_compliance_keywords", set())
        if compliance_kw:
            already_in_compliance = any(
                any(kw in cn for cn in existing_compliance_names)
                for kw in compliance_kw
            )
            if already_in_compliance:
                continue

        # Add implied feature (dedup by name)
        implied_key = rule["implied_name"].lower()
        if implied_key not in seen_implied:
            seen_implied.add(implied_key)
            implied.append({
                "name": rule["implied_name"],
                "expected_route": rule["implied_route"],
                "category": rule["implied_category"],
                "implied": True,
            })

    if implied:
        logger.info(
            f"[PROMPT CONTRACT PARSER] Inferred {len(implied)} implied features: "
            f"{', '.join(f['name'] for f in implied)}"
        )

    return implied


# ─── FIX-14: Checklist Items as Mandatory (Golden-vs-AGIX) ─────────
#
# Treats ALL checkbox/checklist items as mandatory requirements regardless
# of checked/unchecked state. Prompt authors use ⬜/☐ to indicate TODOs,
# but these are still requirements the generated app must satisfy.

# Unicode checkbox markers
_CHECKBOX_UNICODE_RE = re.compile(
    r'^\s*[⬜✅☐☑✓✗✘□■●○]\s*(.+)$',
    re.MULTILINE,
)

# Markdown-style checkboxes: - [ ] item, - [x] item
_CHECKBOX_MARKDOWN_RE = re.compile(
    r'^\s*[-*]\s*\[[ xX]\]\s*(.+)$',
    re.MULTILINE,
)


def extract_checklist_items(prompt: str) -> List[Dict]:
    """FIX-14: Extract ALL checklist/checkbox items as mandatory requirements.

    Every checklist item — regardless of checked (✅/☑/[x]) or unchecked
    (⬜/☐/[ ]) state — is treated as a hard requirement with confidence=1.0.

    This catches the common pattern where prompts include CAN-SPAM or
    compliance checklists with unchecked items marked "To add" — these
    are still requirements the app must implement.

    Args:
        prompt: Raw user prompt text.

    Returns:
        List of checklist requirement dicts. Each has:
        - name: The checklist item text
        - type: "checklist"
        - confidence: 1.0
        - hard_requirement: True
        - source_marker: The original checkbox character
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    items: List[Dict] = []
    seen: Set[str] = set()

    def _add_item(text: str) -> None:
        """Add a checklist item if not duplicate."""
        # Clean up common prefixes
        cleaned = re.sub(
            r'^(?:To\s+add\s*[-—:]?\s*|Need\s+to\s+add\s*[-—:]?\s*)',
            '', text, flags=re.IGNORECASE,
        ).strip()
        if len(cleaned) < 3:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        items.append({
            "name": cleaned,
            "type": "checklist",
            "confidence": 1.0,
            "hard_requirement": True,
        })

    # Pass 1: Unicode checkbox markers
    for m in _CHECKBOX_UNICODE_RE.finditer(prompt):
        _add_item(m.group(1).strip())

    # Pass 2: Markdown-style checkboxes
    for m in _CHECKBOX_MARKDOWN_RE.finditer(prompt):
        _add_item(m.group(1).strip())

    if items:
        logger.info(
            f"[PROMPT CONTRACT PARSER] Extracted {len(items)} checklist items "
            f"as mandatory requirements"
        )

    return items
