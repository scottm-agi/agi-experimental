import re

# ─── Category-Specific THEN Clauses (U-12 Prevention) ───────────────────
# Categories with known structural requirements get concrete THEN clauses
# instead of generic [FILL — architect enriches] stubs. This ensures the
# code agent's TDD test enforces content density at WRITE time, not just
# file existence.
_CATEGORY_THEN_CLAUSES = {
    "page": (
        "    Then the page file contains real content sections (hero, features, CTAs, footer)\n"
        "    And the page does NOT contain placeholder text (Lorem ipsum, TODO, TBD)\n"
        "    And the page contains multiple semantic HTML sections with descriptive content"
    ),
    "integration": (
        "    Then the source file imports the integration SDK or uses an HTTP client for API calls\n"
        "    And the source file reads the API key via environment variables or secret management (NOT hardcoded)\n"
        "    And the integration test verifies the import exists and env var is referenced\n"
        "    And the test does NOT mock the entire API response — it verifies real SDK usage\n"
        "    And the integration handles error responses gracefully"
    ),
    "integration_endpoint": (
        "    Then the API route handler returns a valid response matching the shared type contract\n"
        "    And the route handles missing/invalid parameters with appropriate error codes"
    ),
    # T-1: URL requirements must assert presence in source code
    "url": (
        "    Then the URL value from content_manifest.json appears in at least one source file\n"
        "    And the URL is wired as a clickable link, button action, or environment variable reference\n"
        "    And the URL is NOT only in .env — it must be consumed by a UI component"
    ),
    # T-1: Scaffold cleanup must assert boilerplate is removed
    "scaffold_cleanup": (
        "    Then the project config 'name' is NOT a scaffold default ('scaffold-temp', 'my-app', etc.)\n"
        "    And the project config 'name' matches the slugified project name from content_manifest.json\n"
        "    And .env files do NOT contain default credentials ('johndoe', 'randompassword', 'mydb')\n"
        "    And README.md does NOT contain scaffold boilerplate ('Create Next App', 'bootstrapped with')"
    ),
    # T-1: Delivery standards must assert project-specific content
    "delivery": (
        "    Then README.md contains the project name from content_manifest.json\n"
        "    And README.md contains setup instructions and tech stack description\n"
        "    And README.md does NOT contain 'Create Next App' or scaffold boilerplate"
    ),
    # T-1: Design token requirements must assert consumption in source
    "design": (
        "    Then the global styles file contains CSS custom properties matching design-tokens.json keys\n"
        "    And no hardcoded color values appear in page-level components\n"
        "    And at least one source file imports or references design-tokens.json"
    ),
    # F-13 (ITR-12): Complete THEN clauses for all missing categories
    "feature": (
        "    Then the feature implementation handles the core use case correctly\n"
        "    And the feature handles edge cases (empty input, invalid data, boundary values)\n"
        "    And the feature has unit tests covering happy path and error cases"
    ),
    "branding": (
        "    Then the brand element (name, logo, colors, copy) matches the content_manifest.json exactly\n"
        "    And no placeholder brand values remain (Company Name, Your Brand, etc.)\n"
        "    And the brand element is visible on the appropriate page(s)"
    ),
    "copy": (
        "    Then the copy text matches the content_manifest.json specification\n"
        "    And the copy does NOT contain placeholder text (Lorem ipsum, TODO, TBD)\n"
        "    And the tone/voice matches the brand guidelines from the manifest"
    ),
    "content_constraint": (
        "    Then the content respects the specified constraint (word count, character limit, format)\n"
        "    And the constraint is enforced programmatically via validation logic\n"
        "    And validation errors are shown when the constraint is violated"
    ),
    "ui_element": (
        "    Then the UI element is visible and interactive on the specified page\n"
        "    And the element has proper styling matching design-tokens.json\n"
        "    And the element is accessible (proper ARIA labels, keyboard navigation)"
    ),
    "deployment": (
        "    Then the deployment configuration is complete and valid\n"
        "    And environment variables are documented in .env.example\n"
        "    And the build command succeeds without errors"
    ),
    # F-12-L1 (ITR-12): Compliance THEN clause with data persistence
    "compliance": (
        "    Then the compliance requirement is enforced in both UI and backend\n"
        "    And the compliance state is persisted in the database (e.g., opt-out records stored)\n"
        "    And the system prevents further contact after opt-out\n"
        "    And compliance audit trail is maintained"
    ),
    # WB-4: Compliance sub-type THEN clauses — regulation-specific acceptance criteria
    "compliance_email": (
        "    Then all marketing/transactional emails include a physical mailing address\n"
        "    And every email contains a working unsubscribe link\n"
        "    And sender identification (From name and address) is accurate and not deceptive\n"
        "    And opt-out requests are honored within 10 business days\n"
        "    And email subject lines are not deceptive or misleading\n"
        "    And the opt-out mechanism is clearly visible (not hidden in fine print)"
    ),
    "compliance_privacy": (
        "    Then a privacy policy page exists and is linked from the footer\n"
        "    And the privacy policy describes what data is collected and how it is used\n"
        "    And a cookie consent banner is displayed to new visitors\n"
        "    And a data deletion mechanism exists (e.g., account deletion or erasure request form)\n"
        "    And user data is not shared with third parties without explicit consent\n"
        "    And data collection is limited to what is necessary for the stated purpose"
    ),
    "compliance_accessibility": (
        "    Then all images have descriptive alt text attributes\n"
        "    And the entire application is keyboard navigable (Tab, Enter, Escape)\n"
        "    And color contrast meets WCAG AA standards (4.5:1 for normal text, 3:1 for large)\n"
        "    And all form inputs have associated label elements\n"
        "    And ARIA landmarks are used for major page sections (nav, main, footer)\n"
        "    And interactive elements have visible focus indicators"
    ),
    # F-2 (ITR-15): Feature sub-category THEN clauses
    "feature_api": (
        "    Then the API/service endpoint returns structured data\n"
        "    And the endpoint reads secrets from environment variables (NOT hardcoded)\n"
        "    And the endpoint handles error responses gracefully (rate limits, timeouts, 4xx/5xx)\n"
        "    And the endpoint has integration tests verifying real SDK/HTTP usage"
    ),
    "feature_workflow": (
        "    Then the workflow executes steps in the specified sequence\n"
        "    And the workflow handles edge cases (missing inputs, timeouts, partial failures)\n"
        "    And the workflow produces observable side effects (DB writes, API calls, emails sent)\n"
        "    And the workflow timing/scheduling matches the specification"
    ),
    "feature_ui": (
        "    Then the UI component renders all required data from props or API\n"
        "    And the component is responsive and accessible\n"
        "    And the component uses design tokens from design-tokens.json (no hardcoded color values)\n"
        "    And the component has visual states for loading, empty, error, and success"
    ),
    "feature_data": (
        "    Then the calculation/scoring produces correct results for known inputs\n"
        "    And the algorithm handles edge cases (zero values, missing data, boundary conditions)\n"
        "    And the data processing is deterministic (same input → same output)\n"
        "    And the results are stored/cached appropriately for downstream consumers"
    ),
    # F1-a: UI shell — shared navigation/layout for multi-page apps
    "ui_shell": (
        "    Then the root layout includes a shared navigation component\n"
        "    And the navigation contains links to all application routes\n"
        "    And the navigation component is visible on every page\n"
        "    And the layout provides consistent structure across all pages\n"
        "    And individual page.tsx files do NOT duplicate the navigation component\n"  # F-11 negative
    ),
    # F-3 (ITR-25): Infrastructure requirements — build, config, env coherence
    "infra": (
        "    Then layout.tsx/layout.jsx does NOT contain 'use client' directive\n"
        "    And all @/ import aliases in tsconfig.json resolve to existing directories\n"
        "    And every process.env.X reference in src/ has a corresponding .env entry\n"
        "    And package.json contains all packages imported in src/ files\n"
        "    And tailwind.config content paths include src/**/*.{ts,tsx}\n"
        "    And the build command (npm run build) exits with code 0"
    ),
    # F-11: Navigation exclusivity — nav lives in root layout only
    "nav_exclusivity": (
        "    Then the root layout contains exactly one <nav> element\n"
        "    And page.tsx files do NOT contain <nav> elements\n"
        "    And the navigation is defined ONLY in the root layout component\n"
    ),
    # F-11: Color mode consistency across all pages
    "theme_consistency": (
        "    Then ALL page.tsx files use the SAME color mode (all light OR all dark)\n"
        "    And the color mode matches the architecture document declaration\n"
        "    And no page uses conflicting dark:/light: utility classes\n"
    ),
    # F-10: Design token consumption — components use tokens, not raw Tailwind
    "design_token_consumption": (
        "    Then component files use design token classes (bg-primary-*, text-accent-*)\n"
        "    And component files do NOT use default Tailwind colors (slate-*, gray-*, zinc-*)\n"
        "    And global styles file contains CSS custom properties from design-tokens.json\n"
        "    And tailwind.config maps token names to custom properties\n"
    ),
    # F-12: Cross-cutting files — error, loading, not-found boundaries
    "cross_cutting_files": (
        "    Then the project contains error.tsx for error boundaries\n"
        "    And the project contains loading.tsx for loading states\n"
        "    And the project contains not-found.tsx for 404 pages\n"
        "    And these files are in the app root directory\n"
    ),
    # F-13: Dashboard data sourcing — no hardcoded statistics
    "dynamic_data": (
        "    Then dashboard statistics come from database queries (Prisma/fetch)\n"
        "    And dashboard page files do NOT contain hardcoded large numbers in JSX\n"
        "    And data loading uses server components or API routes\n"
    ),
    # RCA-470 F-3: Model slug verification — verified_slug must appear in source
    "model_slug": (
        "    Then the source code contains the verified model slug from content_manifest.json\n"
        "    And the model slug is NOT a stale/deprecated identifier (e.g., claude-3.5-sonnet vs claude-sonnet-4)\n"
        "    And the model identifier is read from configuration, NOT hardcoded inline\n"
        "    And the model slug matches the API provider's current naming convention"
    ),
}


# ─── F-2 (ITR-15): Feature Sub-Category Classification ──────────────────
# Universal keyword patterns to sub-classify features into specific types.

_FEATURE_SUBTYPE_PATTERNS = {
    "feature_api": re.compile(
        r'\b(?:api|endpoint|fetch(?:ing)?|search(?:ing)?|engine|query|scrap(?:e|ing)|scan(?:ning)?|webhook)\b',
        re.IGNORECASE,
    ),
    "feature_workflow": re.compile(
        r'\b(?:sequence|automation|queue|schedul(?:e|ing)|drip|pipeline|cron|trigger|batch)\b',
        re.IGNORECASE,
    ),
    "feature_ui": re.compile(
        r'\b(?:page|dashboard|table|display(?:ing)?|view|form|button|interface|component|render(?:ing)?)\b',
        re.IGNORECASE,
    ),
    "feature_data": re.compile(
        r'\b(?:scor(?:e|ing)|calculat(?:e|ion)|algorithm|analytics|metrics|aggregat(?:e|ion)|rank(?:ing)?|rate)\b',
        re.IGNORECASE,
    ),
}





# ─── WB-4: Compliance Sub-Category Classification ───────────────────────

_COMPLIANCE_SUBTYPE_PATTERNS = {
    "compliance_email": re.compile(
        r'\b(?:CAN[- ]?SPAM|email\s+complian|unsubscribe|physical\s+address|mailing\s+address|sender\s+identif)\b',
        re.IGNORECASE,
    ),
    "compliance_privacy": re.compile(
        r'\b(?:GDPR|CCPA|privacy\s+polic|data\s+protect|cookie\s+consent|right\s+to\s+erasure|data\s+retention)\b',
        re.IGNORECASE,
    ),
    "compliance_accessibility": re.compile(
        r'\b(?:ADA|WCAG|accessib|screen\s+reader|alt\s+text|keyboard\s+nav|aria|section\s+508)\b',
        re.IGNORECASE,
    ),
}






# ─── Categories that suggest BDD scenarios are valuable ──────────────────
_BDD_CATEGORIES = {
    "page", "feature", "integration", "integration_endpoint",
    "data_fidelity", "url", "delivery", "scaffold_cleanup", "design",
    "ui_shell",  # F1-a: shared navigation BDD scenarios
    "infra",  # F-3 (ITR-25): infrastructure requirements
    "nav_exclusivity",  # F-11: nav lives in root layout only
    "theme_consistency",  # F-11: color mode consistency
    "design_token_consumption",  # F-10: token consumption enforcement
    "cross_cutting_files",  # F-12: error/loading/not-found boundaries
    "dynamic_data",  # F-13: database-sourced dashboard stats
    "copy",  # RCA-464: content-fidelity BDD scenarios for quoted text
    "content",  # RCA-464: content requirements with exact-text assertions
    "branding",  # RCA-464: brand element fidelity assertions
    "model_slug",  # RCA-470 F-3: model/API slug verification
}


# ─── BDD Coverage Gate (F-2, ITR-11) ────────────────────────────────────



# ─── F-4 (ITR-18): BDD Error-Path Coverage Check ────────────────────────

# Keywords that indicate error-path coverage in BDD text
_ERROR_PATH_KEYWORDS = re.compile(
    r'\b(?:error|fail|failure|bounce|rate\s*limit|timeout|invalid|'
    r'500|4xx|401|403|429|unavailable|retry|exception|denied|rejected)\b',
    re.IGNORECASE,
)




# ─── F-7 (ITR-18): BDD Price Cross-Reference Validation ─────────────────



# ─── BDD REQ-ID Enforcement (ITR-14 ISS-2 ROOT CAUSE FIX) ───────────────



# ─── BDD Structured Tool (ITR-14 ISS-2 CORRECT FIX) ─────────────────────





# ─── BDD Skeleton Generation ────────────────────────────────────────────



# ─── ITR-31 Fix A: Feature Files + Scenario Manifest ─────────────────────





# ─── ITR-14 I-1: BDD Literal Cross-Checker (L1) ────────────────────────





# ─── F-6 (ITR-42): BDD Price Literal Auto-Correction ───────────────────



# ─── F-3 (ITR-22): BDD Behavioral Consistency Validator ─────────────────

# Sentiment triggers in BDD — maps to happy/unhappy
_HAPPY_TRIGGERS = re.compile(
    r'\b(?:happy|positive|5[\s-]*star|4[\s-]*star|good|satisfied|high)\b',
    re.IGNORECASE,
)
_UNHAPPY_TRIGGERS = re.compile(
    r'\b(?:unhappy|negative|1[\s-]*star|2[\s-]*star|bad|unsatisfied|low|complaint)\b',
    re.IGNORECASE,
)

# Destination classifiers in THEN clause text
_PUBLIC_DESTINATIONS = re.compile(
    r'\b(?:google|yelp|facebook|trustpilot|public|external)\b',
    re.IGNORECASE,
)
_PRIVATE_DESTINATIONS = re.compile(
    r'\b(?:private|internal|feedback\s+form|survey|complaint)\b',
    re.IGNORECASE,
)

# Parse scenario blocks from BDD text
_SCENARIO_BLOCK_RE = re.compile(
    r'Scenario:\s*(?P<name>[^\n]+)\n(?P<body>(?:(?!(?:Scenario:|Feature:))[\s\S])*?)'
    r'(?=Scenario:|Feature:|\Z)',
    re.MULTILINE,
)

# Extract THEN clause lines
_THEN_LINE_RE = re.compile(
    r'^\s*Then\s+(.+)$',
    re.MULTILINE | re.IGNORECASE,
)

# Extract WHEN clause lines (for trigger context)
_WHEN_LINE_RE = re.compile(
    r'^\s*When\s+(.+)$',
    re.MULTILINE | re.IGNORECASE,
)














# ─── U-9: BDD Content Coverage Gate ─────────────────────────────────────

_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "are", "was", "were", "been", "being", "will", "would", "could",
    "should", "shall", "may", "might", "must", "does", "did", "doing",
    "each", "every", "all", "any", "some", "such", "than", "then",
    "when", "where", "which", "while", "about", "into", "through",
    "during", "before", "after", "above", "below", "between",
    "feature", "scenario", "given", "then", "when", "but", "not",
    "also", "very", "just", "only", "more", "most", "other",
    "using", "used", "user", "page", "system",
})

_BOILERPLATE_PATTERNS = re.compile(
    r"(?:the feature (?:works|is (?:implemented|tested|complete)))"
    r"|(?:the (?:feature|system|application) is (?:ready|available|set up))"
    r"|(?:(?:everything|it) (?:works|functions) (?:correctly|properly|as expected))"
    r"|(?:the (?:feature|requirement) is (?:verified|validated|confirmed))",
    re.IGNORECASE,
)







