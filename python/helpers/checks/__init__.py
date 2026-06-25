"""
Gate integration checks — domain-specific sub-modules.

DESIGN: Fail-Open Gate Architecture
All check functions follow the convention: return None = pass, return ctx.block(...) = block.
If a check function raises an exception, the gate runner catches it and returns None (pass).
This is INTENTIONAL — false blocks are worse than missed checks in production.
See: agix-devdocs/docs/audits/silent_failure_audit_2026_05_24.md

Importing this package triggers auto-registration of all check functions
into the global CHECK_REGISTRY and ADVISORY_REGISTRY via @register_check
and @register_advisory decorators.

Module layout:
  structural.py   — manifest, blueprint, scaffold, boilerplate, build, npm, TDD
  content.py      — landing page, placeholder, BDD, mock data, dev server, routes
  verification.py — browser UAT, quality eval, LIT, README, response quality, E2E
  quality.py      — env template, error boundaries, CSS, Prisma, lib tests, stubs
  requirements.py — PDV, contract assertions, .env.example, form routes, health, theme
"""

# Import all sub-modules to trigger @register_check / @register_advisory decoration.
# The order of imports here does NOT affect check execution order — that is
# determined by the `order` parameter passed to each decorator.
from python.helpers.checks import structural      # noqa: F401
from python.helpers.checks import content         # noqa: F401
from python.helpers.checks import verification    # noqa: F401
from python.helpers.checks import quality         # noqa: F401
from python.helpers.checks import requirements    # noqa: F401
from python.helpers.checks import advanced_quality  # noqa: F401
from python.helpers.checks import bdd_quality        # noqa: F401
from python.helpers.checks import tdd_semantic_quality     # noqa: F401
from python.helpers.checks import tdd_red_green       # noqa: F401  ITR-33 FIX-B
from python.helpers.checks import tdd_integration_coverage  # noqa: F401  I-3
from python.helpers.checks import non_web                     # noqa: F401  P2-B
