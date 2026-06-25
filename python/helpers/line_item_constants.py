"""
Line-Item Extractor Constants — Regex Patterns & Configuration

All compiled regex patterns, word sets, and constant maps used by the
prompt_line_item_extractor pipeline. Extracted from the monolithic
prompt_line_item_extractor.py for maintainability.
"""

import logging
import re
from typing import List, Tuple

logger = logging.getLogger("agix.prompt_line_item_extractor")

# Maximum characters per line before splitting. Prevents catastrophic regex
# backtracking on very long single-line inputs (e.g. 170K chars). Real-world
# prompts rarely exceed a few hundred chars per line, so 2000 is generous.
MAX_LINE_LENGTH = 2000

# ─── F-5 (ITR-21): Quantity-Aware Decomposition ───────────────────────────
# Auto-expand requirements with quantity patterns (e.g., "3-email drip
# sequence") into N sub-requirements so the architect treats each as a
# separate work package.
QUANTITY_PATTERN = re.compile(
    r'(\d+)[- ]?(email|step|stage|phase|message|page|screen|slide|section|form|template)s?',
    re.IGNORECASE
)
MAX_EXPANSION = 10  # Cap to prevent ridiculous expansions

# Map of word-number strings to integers for potential future "three-email" support
_QUANTITY_WORD_MAP = {
    'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
}


# ─── Universal Integration Detection ──────────────────────────────────────
# Instead of hardcoding 34 vendor names, detect integrations by LINGUISTIC
# patterns. Any capitalized product name + integration keyword is caught.

# Pattern 1: CapitalizedWord + STRONG integration keyword
# Catches: "Mailjet email integration", "Plaid API setup", "Sentry SDK"
#
# SS-6 (ITR-15): Split into strong/weak tiers. Only STRONG keywords remain
# in the phrase regex. Weak signals (email, payment, search, etc.) are domain
# feature words that cause 43% false-positive integration classification.
# Weak signals are still caught by _INTEGRATION_VERB_RE and _INTEGRATION_VIA_RE
# when they appear with proper contextual markers ("integrate with", "via", etc.).
_INTEGRATION_PHRASE_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9.]+(?:\s+[A-Z][a-zA-Z0-9.]*)*)\s+'
    r'(?:integration|api|sdk|client|library|package|'
    r'plugin|module|driver|adapter|connector|provider|'
    r'service|setup|config(?:uration)?|'
    r'engine|builder|form|system|'
    r'(?:campaign|subscription|workflow|tracking|capture)\s+'
    r'(?:management|service|setup|system|engine|form|dashboard|builder|integration|api))',
    re.IGNORECASE
)

# Pattern 2: "integrate/connect/hook with X" verb patterns
# Catches: "integrate with Plaid", "connect to Twilio", "hook into Sentry"
# Also handles past tense/gerund: "integrated with", "connecting to"
_INTEGRATION_VERB_RE = re.compile(
    r'(?:integrat|connect|hook|wir|link|sync|plug)(?:e|ed|es|ing)?\s+'
    r'(?:with|to|into)\s+([A-Z][a-zA-Z0-9.]+)',
    re.IGNORECASE
)

# Pattern 2b: "X via/through/powered by Provider" connectors (F-1)
# Catches: "model via OpenRouter", "email through Resend", "powered by Stripe"
_INTEGRATION_VIA_RE = re.compile(
    r'(?:via|through|using|powered\s+by)\s+([A-Z][a-zA-Z0-9.]+)',
    re.IGNORECASE
)

# Pattern 2c: "X for Y" connectors (F-1 ITR-25)
# Catches: "Resend for email", "Stripe for payments", "Redis for caching"
# Domain keywords are constrained to tech/infrastructure domains to avoid
# false positives from common English phrases like "Ideas for the meeting".
_INTEGRATION_FOR_RE = re.compile(
    r'([A-Z][a-zA-Z0-9.]+)\s+for\s+'
    r'(?i:email|payment|payments|auth|cache|caching|search|storage|database|analytics|'
    r'monitoring|logging|queue|notification|messaging|video|image|'
    r'file|chat|sms|push|map|geo|ai|ml|hosting|deploy|deployment|'
    r'frontend|backend|api|cdn|dns|ssl|ci|cd|testing)'
)

# Pattern 3: @scoped/package or "npm install X" references

# Catches: "@tanstack/react-query", "npm install prisma"
_PACKAGE_RE = re.compile(r'(@[\w-]+/[\w-]+)|(?:npm|pip|gem|yarn)\s+(?:install|add)\s+(\S+)')

# Backward-compat: standalone capitalized product names near tech context
# Catches single-word product names like "Stripe", "Firebase" when preceded/followed
# by tech verbs ("use", "set up", "configure", "add")
_STANDALONE_PRODUCT_RE = re.compile(
    r'(?:use|set\s+up|configure|add|install|enable|deploy)\s+'
    r'([A-Z][a-zA-Z0-9.]+)',
    re.IGNORECASE
)

# URL pattern
_URL_RE = re.compile(r"https?://[^\s\)\"'>\]]+")

# Route pattern — UNIVERSAL: any /path/segment (lowercase, hyphens allowed)
# Excludes file extensions (.tsx, .js, .css, etc.) to avoid false positives
_ROUTE_RE = re.compile(
    r'(?:^|\s)(/[a-z\[<:][a-z0-9_<>:{}\[\]-]*(?:/[a-z0-9_<>:{}\[\]-]+)*)',
    re.IGNORECASE
)

# File extension exclusion — routes ending in known extensions are NOT routes
_FILE_EXT_RE = re.compile(r'\.\w{1,5}$')

# Common English words that look like routes when preceded by / (F-4)
_ROUTE_STOP_WORDS = {
    'follow', 'scenario', 'use', 'each', 'every', 'their',
    'your', 'our', 'the', 'for', 'with', 'from', 'into',
    'about', 'after', 'before', 'between', 'through', 'during',
    'without', 'within', 'along', 'among', 'across', 'behind',
}

# Env var pattern: ALL_CAPS_WITH_UNDERSCORES
_ENV_RE = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")

# Quoted strings (copy text)
_QUOTED_RE = re.compile(r'"([^"]{3,80})"')

# F-1 (ITR-15) + F-9 (ITR-45): Negation context markers for ALL extraction passes.
# When a line contains these markers, extracted items are anti-examples
# (things the user wants to AVOID or has CHANGED FROM), not positive requirements.
# The tool tags them with '_anti_example' suffix (e.g., 'feature_anti_example').
_NEGATION_CONTEXT_RE = re.compile(
    r'(?:deleted|removed|changed\s+to|too\s+aggressive|sounds\s+like|'
    r'reframed\s+as|don\'t\s+(?:say|use|write|build)|wrong|avoid|'
    r'instead\s+of|not\s+this|replaced\s+with|dropped|'
    # F-9 (ITR-45): Expanded negation patterns
    r'never|should\s+not|must\s+not|'
    r'bad\s+example|incorrect|do\s+not\s+(?:build|use|add|include)|'
    r'old\s+version|deprecated|legacy|'
    r'anti-pattern|unlike|as\s+opposed\s+to)',
    re.IGNORECASE
)

# F-9 (ITR-45): Categories ending in '_anti_example' are negative examples.
# These items are excluded from the main extract_line_items() return list.
# They represent things the user explicitly does NOT want built.
_ANTI_EXAMPLE_CATEGORIES = frozenset({
    'copy_anti_example',
    'feature_anti_example',
    'page_anti_example',
    'integration_anti_example',
    'config_anti_example',
    'deployment_anti_example',
    'url_anti_example',
})

# Pricing pattern
_PRICE_RE = re.compile(r"\$\d+(?:\.\d{2})?(?:/\w+)?")

# F-2 + F-10 (ITR-16): Competitor/comparison/strategy context — product names
# in these contexts are references, NOT integration requirements to build
_COMPETITOR_CONTEXT_RE = re.compile(
    r'\b(?:competitor|competing|competition|rival|alternative|compared|versus|vs|'
    r'other\s+(?:businesses|companies|services|platforms)|market\s+rate|'
    r'industry\s+(?:average|standard|norm)|'
    # F-10 (ITR-16): Broader strategy/analysis patterns
    r'cheaper|more\s+expensive|outperform|unlike|better\s+than|worse\s+than|'
    r'beat(?:s|ing)?|replace(?:s|ment)?|gap\s+in|our\s+advantage|'
    r'differentiator|benchmark|incumbent|legacy\s+(?:system|platform|tool))\b',
    re.IGNORECASE
)

# SS-8 (ITR-15): Behavioral competitor pricing signals — catches structural
# patterns that indicate reference/competitor pricing without explicit keywords
# like "competitor". Detects: "charges $X", "priced at $X",
# "they/their ... $X", "currently pay/spend/charge".
#
# NOTE: "costs $X" is intentionally excluded as a standalone signal because it's
# ambiguous — "Our product costs $200/mo" is product pricing, not competitor.
# Instead, "costs" only triggers when preceded by third-party context
# (e.g., "it costs $X", "that costs $X").
_COMPETITOR_PRICE_SIGNAL_RE = re.compile(
    r'(?:'
    r'charges?\s+\$\d|'              # "charges $10" — someone else's price
    r'(?:it|that)\s+costs?\s+\$\d|'  # "it costs $50" — third-party reference
    r'priced\s+at\s+\$\d|'           # "priced at $200" — reference pricing
    r'(?:they|their|them)\s+.*\$\d|' # "they charge $50" — third party
    r'currently\s+(?:pay|spend|charge)'  # "currently pay" — status quo, not our product
    r')',
    re.IGNORECASE
)

# ─── SS-7 (ITR-15): Common Page Hint Detection ────────────────────────────
# Detects BEHAVIORAL signals suggesting a page should exist, without requiring
# explicit /route syntax. These are advisory hints — when matched, items get
# tagged with has_page_hint metadata so the architect can decide.
_COMMON_PAGE_HINT_RE = re.compile(
    r'\b(?:landing\s+page|home\s*page|pricing\s+(?:page|plan|tier)|'
    r'contact\s+(?:page|form|us)|about\s+(?:page|us|section)|'
    r'sign[\s-]?up\s+(?:page|form|flow)|login\s+(?:page|form|screen)|'
    r'dashboard\s+(?:page|view|screen)|settings\s+(?:page|panel)|'
    r'profile\s+(?:page|section)|checkout\s+(?:page|flow|screen)|'
    r'onboarding\s+(?:page|flow|screen|wizard))\b',
    re.IGNORECASE
)

# Numbered list item: "1. Something", "2) Something" (start-of-line only)
_NUMBERED_RE = re.compile(r"^\s*(\d+)[.\)]\s+(.+)$", re.MULTILINE)

# ─── Pass 14: Inline Numbered Items (RCA-354 F-2) ─────────────────────────
# Catches numbered items embedded INLINE within a paragraph, e.g.:
# "Businesses pay $200/mo for: 1. Review capture 2. AI drafts 3. Analytics"
# The standard _NUMBERED_RE uses ^ anchor and misses these.
# Uses position-based splitting: find all `N. ` positions, extract text between them.
_INLINE_NUMBERED_POS_RE = re.compile(
    r'(?:(?<=\s)|(?<=:)\s*|(?<=^))(\d+)[.)\u00b7]\s+',
)


# ─── Pass 14b: Workflow Routing Patterns (RCA-354 F-2) ─────────────────────
# Catches "X → Y", "X -> Y", "route to", "redirect to" workflow descriptions
_WORKFLOW_ROUTING_RE = re.compile(
    r'((?:[\w\s]+?)\s*(?:→|->|-->|⟶)\s*(?:[\w\s]+?)'
    r'(?:,|\.|$))',
    re.IGNORECASE,
)

# ─── Pass 14c: Conditional Flow Patterns (RCA-354 F-2) ─────────────────────
# Catches "if X then Y", "when X → Y" business logic
_CONDITIONAL_FLOW_RE = re.compile(
    r'(?:if|when)\s+(.+?)(?:,\s*|\s*→\s*|\s*->\s*|,?\s+then\s+)'
    r'(.+?)(?:\.|,|;|$)',
    re.IGNORECASE,
)

# ─── Pass 14d: Step Sequence Patterns (RCA-354 F-2) ────────────────────────
# Catches "Step 1 — description", "Phase 1: description"
_STEP_SEQUENCE_RE = re.compile(
    r'(?:step|phase|stage)\s+(\d+)\s*[—:\-–]\s*(.+?)(?:\.|$)',
    re.IGNORECASE,
)

# Bulleted list item: "- Something", "* Something"
_BULLETED_RE = re.compile(r"^\s*[-*•]\s+(.+)$", re.MULTILINE)

# ─── Priority-Tier Detection (U-1, RCA-2 through RCA-6) ──────────────────
# Detects section headers that indicate timeline/priority tiers.
# Timeline labels are METADATA only — all items remain in-scope.
_PRIORITY_TIER_RE = re.compile(
    r'^\s*(?:#+\s*)?'
    r'(?:'
    r'(?P<immediate>Immediate\b)'
    r'|(?P<near_term>Near[\s-]Term\b)'
    r'|(?P<growth>Growth\b)'
    r'|(?P<phased>Phase\s+\d+\b)'
    r'|(?P<action_needed>What\s+needs\s+to\s+happen\b)'
    r'|(?P<week>Weeks?\s+\d+\b)'
    r')',
    re.IGNORECASE | re.MULTILINE,
)

# Page keywords — UNIVERSAL: any "X page/section/panel/view/screen/tab/form"
# Instead of hardcoding 14 specific page names, catch ANY noun + UI-surface keyword
_PAGE_KEYWORDS_RE = re.compile(
    r'\b(\w[\w\s]{1,30}?)\s+'
    r'(?:page|section|panel|view|screen|tab|modal|dialog|form|widget|'
    r'dashboard|landing|sidebar|drawer|toolbar|overlay)\b',
    re.IGNORECASE,
)

# Checklist markers: ⬜, ☐, - [ ], [ ], To add:, Required.
# U-2 (RCA-8): Also match ✅ markers when followed by TODO context words
# ("need to add", "required", "must", "to be implemented", "to add").
# A ✅ without these context words is treated as "already complete" and skipped.
_CHECKLIST_RE = re.compile(
    r'(?:^|\n)\s*(?:'
    r'[⬜☐]|[-*]\s*\[[\sx]\]|\[\s*\]|To\s+add:|Required\.' 
    r'|✅\s*(?=.*?(?:need\s+to\s+add|required|must\b|to\s+be\s+implemented|to\s+add))'
    r')\s*(.+?)(?:\n|$)',
    re.MULTILINE | re.IGNORECASE,
)

# SS-3 (ITR-24): Qualifier markers — MUST:, NICE-TO-HAVE:, behavioral strategy
# Moved to module level for testability (was inline in Pass 10).
# Extended with strategy group: LAZY, LAZILY, EAGER, ON-DEMAND, EVENT-DRIVEN.
_QUALIFIER_RE = re.compile(
    r'^(?:'
    r'(?P<must>MUST(?:\s*[-:])?|REQUIRED(?:\s*[-:])?|CRITICAL(?:\s*[-:])?)'
    r'|(?P<nice>NICE[-\s]TO[-\s]HAVE(?:\s*[-:])?|OPTIONAL(?:\s*[-:])?|BONUS(?:\s*[-:])?)'
    r'|(?P<strategy>LAZY(?:\s*[-:])?|LAZILY(?:\s*[-:])?|EAGER(?:\s*[-:])?'
    r'|ON[\s-]DEMAND(?:\s*[-:])?|EVENT[\s-]DRIVEN(?:\s*[-:])?)'
    r')\s*',
    re.IGNORECASE,
)

# SS-3: Normalize raw strategy group matches to canonical lowercase values
_STRATEGY_NORMALIZE = {
    'LAZY': 'lazy', 'LAZY:': 'lazy', 'LAZY-': 'lazy',
    'LAZILY': 'lazy', 'LAZILY:': 'lazy', 'LAZILY-': 'lazy',
    'EAGER': 'eager', 'EAGER:': 'eager', 'EAGER-': 'eager',
    'ON-DEMAND': 'on-demand', 'ON-DEMAND:': 'on-demand', 'ON-DEMAND-': 'on-demand',
    'ON DEMAND': 'on-demand',
    'EVENT-DRIVEN': 'event-driven', 'EVENT-DRIVEN:': 'event-driven',
    'EVENT DRIVEN': 'event-driven',
}

# SS-7: Format constraint detection — "plain text", "HTML format", "markdown format"
_FORMAT_CONSTRAINT_RE = re.compile(
    r'(?:in\s+|be\s+(?:in\s+)?|must\s+be\s+)?'
    r'(?P<fmt>plain\s+text|HTML|markdown|rich\s+text|JSON|XML|CSV)'
    r'(?:\s+(?:format|only))?',
    re.IGNORECASE,
)

_DEPLOYMENT_RE = re.compile(
    r'(?:push\s+(?:to|the\s+[\w\s]+?\s+to)|deploy\s+(?:to|the)|'
    r'create\s+(?:a\s+)?(?:private\s+)?repo|'
    r'host\s+on|publish\s+to|ship\s+to|'
    r'upload\s+to|set\s+up\s+(?:CI|CD|CI/CD|pipeline))\s+'
    r'(.+?)(?:\.|,|\n|$)',
    re.IGNORECASE,
)

# ─── F-2 (RCA-343 ISSUE-2): VCS Deployment Pattern ───────────────────
# Catches VCS/push directives the existing _DEPLOYMENT_RE misses.
# "create a GitHub repository", "push to GitLab", "set up GitHub Actions"
# "git init and push to origin", "initialize a git repo"
_DEPLOYMENT_VCS_RE = re.compile(
    r'(?:'
    r'create\s+(?:a\s+)?(?:private\s+)?(?:GitHub|GitLab|Bitbucket)\s+'
    r'(?:repo(?:sitory)?)|'
    r'(?:push|commit)\s+(?:all\s+)?(?:code\s+|files\s+)?to\s+'
    r'(?:a\s+)?(?:private\s+)?(?:GitHub|GitLab|Bitbucket|origin|remote)|'
    r'set\s+up\s+(?:GitHub|GitLab)\s+Actions|'
    r'(?:initialize|init)\s+(?:a\s+)?git\s+repo(?:sitory)?|'
    r'push\s+to\s+(?:a\s+)?(?:private\s+)?(?:GitHub|GitLab)\s+repo'
    r')\b.*?(?:\.|,|\n|$)',
    re.IGNORECASE,
)

# ─── Compliance Implication Mapping (RCA-341) ─────────────────────────────
# When the prompt mentions a compliance framework or outreach pattern,
# infer the industry-standard pages/features that are ALWAYS required
# but users never explicitly request. Each key is a regex pattern;
# each value is a list of (implied_text, category) tuples to inject.
#
# DESIGN PRINCIPLE (ISS-1, RCA-ITR2): ALL items within compliance
# implication patterns MUST have category="compliance". Items triggered
# by a compliance framework are legally mandated — they are NOT optional
# features. The text may say "(CAN-SPAM required)" — that means compliance.
_COMPLIANCE_IMPLICATION_PATTERNS = [
    # CAN-SPAM / email outreach → privacy + terms pages + functional requirements (U-2, RCA-7/8)
    (re.compile(
        r'\b(?:CAN-SPAM|CAN\s+SPAM|cold\s+email|email\s+outreach|'
        r'opt-out|unsubscribe|marketing\s+email|outreach\s+email)\b',
        re.IGNORECASE,
    ), [
        ("/privacy — Privacy Policy page (implied by email/CAN-SPAM compliance)", "compliance"),
        ("/terms — Terms of Service page (implied by email/CAN-SPAM compliance)", "compliance"),
        ("Unsubscribe endpoint /api/unsubscribe with opt-out tracking (CAN-SPAM required)", "compliance"),
        ("Physical mailing address in email signatures (CAN-SPAM required)", "compliance"),
    ]),
    # GDPR / data protection → privacy + cookie policy + functional requirements (U-2)
    (re.compile(
        r'\b(?:GDPR|data\s+protection|cookie\s+consent|'
        r'data\s+privacy|right\s+to\s+be\s+forgotten)\b',
        re.IGNORECASE,
    ), [
        ("/privacy — Privacy Policy page (implied by GDPR compliance)", "compliance"),
        ("/terms — Terms of Service page (implied by GDPR compliance)", "compliance"),
        ("Data deletion/erasure endpoint /api/data-deletion (GDPR right-to-erasure required)", "compliance"),
    ]),
    # TCPA / SMS compliance → privacy + terms + consent
    (re.compile(
        r'\b(?:TCPA|SMS\s+compliance|text\s+consent|'
        r'SMS\s+marketing|text\s+message\s+outreach)\b',
        re.IGNORECASE,
    ), [
        ("/privacy — Privacy Policy page (implied by TCPA/SMS compliance)", "compliance"),
        ("/terms — Terms of Service page (implied by TCPA/SMS compliance)", "compliance"),
    ]),
    # SOC2 → security + privacy pages
    (re.compile(
        r'\b(?:SOC\s*2|SOC2|SOC\s+2\s+Type\s+(?:I|II|1|2)|'
        r'service\s+organization\s+control)\b',
        re.IGNORECASE,
    ), [
        ("/security — Security Policy page (implied by SOC2 compliance)", "compliance"),
        ("/privacy — Privacy Policy page (implied by SOC2 compliance)", "compliance"),
    ]),
    # HIPAA → privacy + terms + security pages
    (re.compile(
        r'\b(?:HIPAA|health\s+insurance\s+portability|'
        r'protected\s+health\s+information|PHI\b|'
        r'electronic\s+health\s+record|EHR\b)\b',
        re.IGNORECASE,
    ), [
        ("/privacy — Privacy Policy page (implied by HIPAA compliance)", "compliance"),
        ("/terms — Terms of Service page (implied by HIPAA compliance)", "compliance"),
        ("/security — Security Policy page (implied by HIPAA compliance)", "compliance"),
    ]),
    # ADA / WCAG → accessibility statement page
    (re.compile(
        r'\b(?:ADA\s+complian\w*|WCAG|Web\s+Content\s+Accessibility|'
        r'accessibility\s+standard|Section\s+508|'
        r'a11y\s+complian\w*)',
        re.IGNORECASE,
    ), [
        ("/accessibility — Accessibility Statement page (implied by ADA/WCAG compliance)", "compliance"),
    ]),
    # PCI-DSS → privacy + terms pages
    (re.compile(
        r'\b(?:PCI[\s-]*DSS|PCI\s+compliance|'
        r'payment\s+card\s+industry|'
        r'PCI\s+Level\s+[1-4])\b',
        re.IGNORECASE,
    ), [
        ("/privacy — Privacy Policy page (implied by PCI-DSS compliance)", "compliance"),
        ("/terms — Terms of Service page (implied by PCI-DSS compliance)", "compliance"),
    ]),
]

# ─── F-14 (ITR-12): Noise Detection Patterns ──────────────────────────────
# These patterns detect text that is CONTEXT (pricing rationale, competitive
# analysis, sales projections, daily ops, legal background) — NOT buildable
# requirements. Used by classify_requirement_text() as an L1 deterministic filter.
_NOISE_PATTERNS = [
    # Pricing rationale / business justification
    re.compile(r'(?:why\s+this\s+matters|pricing\s+rationale|automation\s+means)', re.I),
    # Competitive analysis
    re.compile(r'(?:where\s+we(?:\s+are|\x27re)?\s+(?:win|lose|vulnerable)|competitor\s+analysis)', re.I),
    # Sales projections / business goals
    re.compile(r'(?:close\s+rate|paying\s+clients|target:\s*\d+\s*clients)', re.I),
    # Daily operations / workflow time descriptions
    re.compile(r'(?:morning\s*\(\d+|midday\s*\(\d+|weekly\s*\(\d+|current\s+workflow)', re.I),
    # Legal context (not actionable requirements)
    re.compile(r'(?:is\s+legal\s+under|does\s+not\s+require\s+prior\s+consent|different\s+animal)', re.I),
    # Copy iteration history
    re.compile(r'(?:reframed\s+as|sound\s+like\s+a\s+free)', re.I),
    # Current workflow descriptions ("Current: Current workflow...")
    re.compile(r'(?:^|\W)current:\s+current\s', re.I),
    # F-5 (ITR-13): Business narrative patterns — context, NOT requirements
    # Iteration retrospectives
    re.compile(r'(?:what\s+we\s+iterated|what\s+we\s+(?:did|learned|tested|changed))', re.I),
    # Revenue / close rate projections
    re.compile(r'(?:at\s+\d+%\s+close\s+rate|recurring\s+revenue)', re.I),
    # Marginal cost / unit economics analysis
    re.compile(r'(?:marginal\s+cost|unit\s+economics|per\s+additional\s+client)', re.I),
    # Competitive vulnerability assessments (broader than existing pattern)
    re.compile(r'(?:NiceJob|Birdeye|Podium|GatherUp)\s+is\s+(?:cheaper|better|faster)', re.I),
]


# ─── F-17 (ITR-12): Page Path Validation ───────────────────────────────────
# Common English verbs/prepositions that are NOT real page names.
# These get extracted as /verb by the route detection pass when they appear
# in prose like "Use a compliant platform" → /use (phantom page).
_INVALID_PAGE_WORDS = {
    'use', 'follow', 'web', 'set', 'get', 'add', 'run', 'see', 'try',
    'can', 'will', 'may', 'should', 'must', 'has', 'have', 'do',
    'make', 'take', 'give', 'keep', 'let', 'put', 'say', 'go',
    'know', 'think', 'come', 'want', 'look', 'turn', 'start', 'show',
    'need', 'move', 'live', 'find', 'tell', 'ask', 'work', 'call',
    'read', 'grow', 'open', 'walk', 'win', 'lose', 'pay', 'meet',
    'play', 'feel', 'send', 'fall', 'sit', 'hold', 'cut', 'led',
}


# ─── F-11 (ITR-12): Content Constraint Extraction ─────────────────────────
# Catches content rules like "under 80 words", "max 3 retries",
# "limit to 140 characters", "at most 5 images".
_CONTENT_CONSTRAINT_RE = re.compile(
    r'(?:under|max(?:imum)?|limit(?:\s+to)?|at\s+most|no\s+more\s+than)'
    r'\s+(\d+)\s+(\w+)',
    re.I,
)

# ─── F-11 (ITR-12): UI Element Extraction ──────────────────────────────────
# Catches specific UI component mentions: banner, drawer, modal, toast, etc.
_UI_ELEMENT_RE = re.compile(
    r'\b((?:\w+\s+)?(?:banner|drawer|modal|toast|popup|sidebar|widget|'
    r'accordion|carousel|tooltip|popover|snackbar)(?:\s+\w+)?)\b',
    re.I,
)

# ─── F-5: UI-Surface Signal Scoring for Feature→Page Promotion ────────────
# Features like "outreach pipeline with filterable table" imply a UI page but
# are never promoted because _PAGE_KEYWORDS_RE only matches explicit patterns.
# This weighted scoring system detects UI-surface signals and promotes features
# above the threshold by adding a companion page requirement.

_UI_SURFACE_SIGNALS = {
    # Data display signals (strong)
    "table": 0.30,
    "list": 0.20,
    "grid": 0.25,
    "chart": 0.30,
    "graph": 0.25,
    "display": 0.20,
    "show": 0.15,
    "view": 0.25,
    "visualize": 0.30,
    "render": 0.20,
    # CRUD / interaction signals (medium-strong)
    "manage": 0.25,
    "management": 0.25,
    "edit": 0.20,
    "create": 0.15,
    "delete": 0.15,
    "update": 0.15,
    "queue": 0.25,
    "filterable": 0.35,
    "sortable": 0.30,
    "searchable": 0.25,
    # Dashboard / admin signals (strong)
    "dashboard": 0.40,
    "admin": 0.30,
    "panel": 0.30,
    "interface": 0.25,
    "console": 0.25,
    "portal": 0.25,
    # Pipeline / flow signals (medium)
    "pipeline": 0.20,
    "workflow": 0.20,
    "engine": 0.15,
    "wizard": 0.30,
    "form": 0.25,
    "builder": 0.25,
    # Output signals (medium)
    "report": 0.25,
    "summary": 0.20,
    "overview": 0.25,
    "analytics": 0.30,
    "metrics": 0.25,
    "status": 0.20,
    "monitor": 0.20,
    # Business / review signals (F-25, RCA-358)
    "review": 0.25,
    "capture": 0.25,
}

UI_SURFACE_THRESHOLD = 0.40  # Need at least 2 medium signals or 1 strong

# Signal keywords used by _derive_page_name to strip signal words from feature text
_SIGNAL_KEYWORDS = set(_UI_SURFACE_SIGNALS.keys())

# Common stop words to skip when deriving page name
_STOP_WORDS = {
    "a", "an", "the", "with", "and", "or", "for", "to", "in", "on",
    "of", "that", "this", "from", "by", "at", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "can",
    "shall", "must", "it", "its", "finds", "uses", "provides",
}



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


# ─── Phase 2: Confidence Scoring & Weighted Candidates ───────────────────

# Confidence map per regex pass — how reliable is each pattern type?
_CONFIDENCE_MAP = {
    "url": 1.0,           # URLs are unambiguous
    "config": 0.95,       # ALL_CAPS_UNDERSCORE is very specific
    "pricing": 0.95,      # $ pattern is unambiguous
    "integration": 0.9,   # CapitalizedWord + integration keyword
    "route": 0.85,        # Structural /path/segment pattern
    "page_keyword": 0.85, # Explicit UI-surface keyword match
    "quoted": 0.8,        # Quoted strings with explicit delimiters
    "deployment": 0.8,    # Explicit verb pattern (push to, deploy to)
    "checklist": 0.75,    # Structured marker (⬜, [ ])
    "compliance": 0.7,    # Implied from compliance framework keywords (RCA-341)
    "numbered": 0.7,      # Common format but could be narrative
    "bulleted": 0.6,      # Very common, could be narrative prose
}



