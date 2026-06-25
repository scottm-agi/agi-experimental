"""
Contract Patterns — Regex patterns and utility functions for prompt contract parsing.

Extracted from prompt_contract_parser.py during modularization.
Contains all compiled regex patterns and small helper functions used
by the extraction modules.
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

# Validate that a captured name is actually Title Case (not just IGNORECASE artifact)
def _is_valid_person_name(name: str) -> bool:
    """Check if name looks like a proper noun (Title Case, 2+ words)."""
    words = name.split()
    if len(words) < 2:
        return False
    return all(w[0].isupper() and w[1:].islower() for w in words if len(w) > 1)



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


# ─── Feature Extraction Patterns ──────────────────────────────────────

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

# Route slug generation: "Outreach Dashboard" -> "/outreach-dashboard"
def _name_to_route(name: str) -> str:
    """Convert a feature name to an expected route slug."""
    slug = re.sub(r'[^a-z0-9\s-]', '', name.lower()).strip()
    slug = re.sub(r'\s+', '-', slug)
    return f"/{slug}" if slug else "/unknown"


# ─── F-7 (RCA-343 ISSUE-7): Functional Capability Extraction ─────────
# Catches verb-based functional requirements that aren't tied to a UI surface.
_FEATURE_CAPABILITY_RE = re.compile(
    r'\b(schedule|score|track|generate|filter|search|export|import|automate|'
    r'analyze|monitor|sync|validate|aggregate|calculate|notify|detect|'
    r'classify|rank|prioritize|archive|backup|restore|verify|audit|log|'
    r'enrich|deduplicate|merge|transform|parse|crawl|scrape|index)'
    r'\s+([a-z][a-z0-9\s]{2,40}?)'
    r'(?:\s+(?:by|based\s+on|from|to|in|for|with|using)\s+|\.|,|$)',
    re.IGNORECASE,
)


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


# ─── Behavior Extraction Patterns ─────────────────────────────────────

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


# ─── Compliance Patterns ──────────────────────────────────────────────

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


# ─── Env Var Patterns ─────────────────────────────────────────────────

# Service name patterns → list of (ENV_VAR_NAME, example_value) tuples
_ENV_VAR_MAP = {
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


# ─── User Journey Patterns ────────────────────────────────────────────

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


# ─── Checklist Patterns ───────────────────────────────────────────────

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


# ─── Implied Feature Rules ────────────────────────────────────────────

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


# ─── URL Verification Constants ───────────────────────────────────────

# File extensions to search when verifying URL presence
_URL_VERIFY_EXTENSIONS = {
    '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
    '.py', '.html', '.css', '.scss', '.vue', '.svelte',
    '.json', '.env', '.yaml', '.yml', '.toml', '.md',
}

# Directories to skip during URL source search
_URL_VERIFY_SKIP_DIRS = {
    'node_modules', '.next', '.nuxt', 'dist', '.git', '__pycache__',
    '.turbo', '.cache', '.vercel', '.output', 'coverage', '.svelte-kit',
    'build', '.expo', '.parcel-cache',
}
