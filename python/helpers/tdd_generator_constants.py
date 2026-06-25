"""TDD Stub Generation module — extracted from skeleton_generator.py.

Part of P0-3 decomposition: this module contains all TDD stub generation
functions that were previously in skeleton_generator.py (F-9, ITR-12).

Functions:
  - detect_project_language: 3-priority language detection
  - generate_project_readme: Project-specific README from manifest
  - _generate_typescript_stubs: vitest describe/it/expect stubs
  - _generate_python_stubs: unittest TestCase stubs
  - _generate_universal_stubs: Markdown pseudocode stubs (fallback)
  - _write_stubs_to_test_dir: Copy stubs to test runner directory
  - generate_tdd_tests: End-to-end pipeline with SS-4 idempotency
  - _escape_docstring: Escape text for Python docstrings
  - _parse_navigation_map: Parse navigation-map.md into route dicts
  - generate_wiring_test_stubs: Generate API route completeness test stubs
"""

# ITR-32 F-6: Categories whose requirements are out-of-scope for Phase 3 TDD.
# Stubs for these categories get a DEFERRED marker instead of TODO throw/raise,
# so they don't appear as test failures for features not yet implemented.
_DEFERRED_CATEGORIES = frozenset({
    "infra", "delivery", "scaffold_cleanup", "design", "deploy", "devops",
})

# ITR-33 FIX-A1: Language/framework names that appear in tech_stack but prove
# nothing about implementation. If ALL expected_literals are garbage, the
# generator falls back to structural assertions instead.
_GARBAGE_LITERALS = frozenset({
    "TypeScript", "JavaScript", "Python", "React", "Next.js",
    "Node.js", "npm", "yarn", "pnpm", "Vite", "Tailwind CSS",
    "Prisma", "SQLite", "PostgreSQL", "MongoDB", "HTML", "CSS",
})

# ITR-33 FIX-A4: Known SDK package names for structural assertions.
# Maps lowercase keywords in requirement text → npm package name.
_SDK_NAMES = {
    'resend': 'resend',
    'stripe': '@stripe/stripe-js',
    'clerk': '@clerk/nextjs',
    'prisma': '@prisma/client',
    'openai': 'openai',
    'calendly': 'calendly',
    'google places': '@googlemaps/google-maps-services-js',
    'google maps': '@googlemaps/google-maps-services-js',
    'supabase': '@supabase/supabase-js',
    'firebase': 'firebase',
    'twilio': 'twilio',
    'sendgrid': '@sendgrid/mail',
    'mailgun': 'mailgun-js',
    'cloudinary': 'cloudinary',
    'aws': '@aws-sdk/client-s3',
    'sentry': '@sentry/nextjs',
}

# SS-6 Anti-Gaming: JavaScript snippet that filters out hidden elements
# from source content before checking for literal presence.
# This prevents the code agent from satisfying tests by inserting
# <div className="hidden"> blocks containing required strings.
_HIDDEN_ELEMENT_FILTER_JS = (
    "// SS-6: Strip hidden elements to prevent gaming with hidden divs\n"
    "function stripHidden(src: string): string {\n"
    "  // Remove className=\"hidden\" / className='hidden' blocks\n"
    "  let filtered = src.replace(/<[^>]*className=[\"'][^\"']*\\bhidden\\b[^\"']*[\"'][^>]*>[\\s\\S]*?<\\/[^>]+>/gi, '');\n"
    "  // Remove style=\"display:none\" blocks\n"
    "  filtered = filtered.replace(/<[^>]*style=[\"'][^\"']*display\\s*:\\s*none[^\"']*[\"'][^>]*>[\\s\\S]*?<\\/[^>]+>/gi, '');\n"
    "  // Remove aria-hidden=\"true\" blocks\n"
    "  filtered = filtered.replace(/<[^>]*aria-hidden=[\"']true[\"'][^>]*>[\\s\\S]*?<\\/[^>]+>/gi, '');\n"
    "  return filtered;\n"
    "}\n"
)
