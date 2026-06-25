"""TDD Stub Generation — Re-export Facade.

This module is the backward-compatible entry point for all TDD stub
generation. The implementation has been decomposed into:

  - tdd_generator_constants.py: Frozen sets and SDK dictionaries
  - tdd_generator_helpers.py:   Route/SDK extraction, detection, escaping
  - tdd_generator_creation.py:  Stub generation and pipeline functions

All public names are re-exported here so existing callers remain unchanged.
"""

import logging

logger = logging.getLogger("tdd_generator")

# ── Re-export constants ──────────────────────────────────────────────────
from python.helpers.tdd_generator_constants import (  # noqa: F401
    _DEFERRED_CATEGORIES,
    _GARBAGE_LITERALS,
    _SDK_NAMES,
    _HIDDEN_ELEMENT_FILTER_JS,
)

# ── Re-export helper functions ───────────────────────────────────────────
from python.helpers.tdd_generator_helpers import (  # noqa: F401
    _extract_route,
    _extract_sdk_name,
    _load_design_tokens,
    _load_bdd_scenarios,
    _embed_bdd_context,
    detect_project_language,
    detect_test_framework,
    _get_test_import_line,
    _escape_docstring,
    _parse_navigation_map,
    detect_route_convention,
    _ROUTE_CONVENTIONS,
)

# ── Re-export creation/pipeline functions ────────────────────────────────
from python.helpers.tdd_generator_creation import (  # noqa: F401
    _write_test_config,
    generate_project_readme,
    _generate_typescript_stubs,
    _generate_python_stubs,
    _generate_universal_stubs,
    _write_stubs_to_test_dir,
    verify_stub_integrity,
    generate_wiring_test_stubs,
    _generate_wiring_typescript_stubs,
    _generate_wiring_python_stubs,
    _generate_sdk_import_stubs,
    _generate_lifecycle_wiring_stubs,
    _LIFECYCLE_PATTERNS,
    generate_tdd_tests,
)

# Backward-compat alias: generate_tdd_stubs was renamed to generate_tdd_tests (ITR-20)
generate_tdd_stubs = generate_tdd_tests