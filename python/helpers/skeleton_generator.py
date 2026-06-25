"""
Test Skeleton Generator — Slim Orchestrator

P0-3 Decomposition: This module was refactored from a 3,251-line monolith into
a slim orchestrator (~500 LOC) that delegates to 3 extracted modules:

  - bdd_generator.py:   BDD scenario generation, coverage gates, validation
  - tdd_generator.py:   TDD stub generation, language detection, README
  - manifest_parser.py: Content manifest loading, literal matching

All public symbols from the extracted modules are re-exported here for
backward compatibility — existing callers (requirements.py, call_subordinate.py,
test files) can continue to import from skeleton_generator without changes.

Architecture:
  Prompt → Decompose → Requirements → Testability Skeletons → Code → Validate → Loop

The skeleton maps each REQ-ID to:
  - Suggested test type (unit, integration, e2e, literal, config)
  - Whether BDD scenarios are needed (weighted from category + project type)
  - Suggested test description
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from python.helpers.gate_config import BDD_COVERAGE_THRESHOLD
from python.helpers.planning_paths import get_path as _planning_path

logger = logging.getLogger("agix.skeleton_generator")


# ═══════════════════════════════════════════════════════════════════════════
# RE-EXPORTS — Backward Compatibility Layer
# All public symbols from extracted modules are re-exported here so that
# existing callers (requirements.py, call_subordinate.py, test files) can
# continue to import from skeleton_generator without changes.
# ═══════════════════════════════════════════════════════════════════════════

# ── BDD Generator Module ────────────────────────────────────────────────
from python.helpers.bdd_generator_constants import (
    # Constants
    _CATEGORY_THEN_CLAUSES,
    _FEATURE_SUBTYPE_PATTERNS,
    _COMPLIANCE_SUBTYPE_PATTERNS,
    _BDD_CATEGORIES,
    _ERROR_PATH_KEYWORDS,
    _HAPPY_TRIGGERS,
    _UNHAPPY_TRIGGERS,
    _PUBLIC_DESTINATIONS,
    _PRIVATE_DESTINATIONS,
    _SCENARIO_BLOCK_RE,
    _THEN_LINE_RE,
    _WHEN_LINE_RE,
    _STOP_WORDS,
    _BOILERPLATE_PATTERNS,
)

from python.helpers.bdd_generator import (  # noqa: F401
    # Functions
    _classify_feature_subtype,
    _classify_compliance_subtype,
    check_bdd_coverage,
    check_bdd_error_paths,
    validate_bdd_prices,
    enforce_bdd_req_traceability,
    validate_bdd_scenario_input,
    assemble_bdd_from_structured,
    generate_bdd_skeleton,
    generate_feature_files,
    generate_scenario_manifest,
    validate_bdd_literals,
    validate_bdd_behavioral_consistency,
    _extract_manifest_conditions,
    _classify_sentiment,
    _classify_destination,
    _extract_key_nouns,
    _extract_bdd_steps,
    _is_boilerplate_scenario,
    check_bdd_content_coverage,
    validate_bdd_conditional_completeness,
)

# ── TDD Generator Module ────────────────────────────────────────────────
from python.helpers.tdd_generator import (  # noqa: F401
    detect_project_language,
    generate_project_readme,
    generate_tdd_tests,
    _generate_typescript_stubs,
    _generate_python_stubs,
    _generate_universal_stubs,
    _write_stubs_to_test_dir,
    _escape_docstring,
)

# ── Manifest Parser Module ──────────────────────────────────────────────
from python.helpers.manifest_parser import (  # noqa: F401
    _MANIFEST_SEARCH_PATHS,
    _MAX_LITERALS_PER_REQ,
    _LITERAL_PATTERNS,
    _SCOPED_WINDOW_SIZE,
    _CATEGORY_MANIFEST_KEYS,
    _find_manifest_path,
    _load_manifest_literals,
    _extract_strings,
    _match_literals,
    _match_literals_scoped,
    _load_manifest_dict,
    _match_literals_by_category,
)


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — Core Functions (kept in this module)
# These functions form the skeleton generation pipeline and depend on all
# 3 extracted modules. They remain here as the central orchestrator.
# ═══════════════════════════════════════════════════════════════════════════

# ─── Category → Test Type Mapping ────────────────────────────────────────

# Maps requirement categories to suggested test types.
# Priority: most specific first. "feature" is the default fallback.
_CATEGORY_TEST_TYPE = {
    "url": "literal",
    "content_constraint": "literal",
    "config": "config",
    "compliance": "literal",
    "integration": "integration",
    "integration_endpoint": "integration",
    "data_fidelity": "integration",  # F-2: data source verification
    "scaffold_cleanup": "e2e",  # F-1: post-scaffold normalization verification
    "ui_shell": "e2e",  # F1-a: shared navigation/layout verification
    "model": "unit",
    "page": "e2e",
    "feature": "unit",
    "infra": "unit",
    "copy": "literal",  # RCA-464: content-fidelity string-match tests
    "content": "literal",  # RCA-464: content-fidelity string-match tests
    "branding": "literal",  # RCA-464: brand element fidelity tests
}

# ─── ITR-30: Untestable Requirement Filter ───────────────────────────────

import re as _re_untestable

_UNTESTABLE_PATTERNS = [
    # Redacted secrets
    _re_untestable.compile(r'§§(?:secret|REDACTED)', _re_untestable.IGNORECASE),
    # Raw API key strings: 30+ chars of base64-like content (no spaces)
    _re_untestable.compile(r'^[A-Za-z0-9_\\-]{30,}$'),
    # Example dynamic-route audit URLs: /r/<slug>/audit
    _re_untestable.compile(r'/r/[a-z0-9\\-]+/audit', _re_untestable.IGNORECASE),
]

# SS-5: Extrapolated page pattern — only applies to page category
_EXTRAPOLATED_PAGE_PATTERN = _re_untestable.compile(
    r'\(extrapolated from', _re_untestable.IGNORECASE
)

# Minimum text length for copy/branding requirements to be considered testable
_MIN_TESTABLE_COPY_LENGTH = 25


def _is_untestable_requirement(req: dict) -> bool:
    """Determine if a requirement is inherently untestable.

    ITR-30 FIX: Prevents noisy/invalid requirements from inflating the
    bdd_needed count and dragging BDD coverage below 100%.

    Universal patterns — work for ANY project:
      1. Text contains §§secret() or §§REDACTED (redacted API key placeholders)
      2. Text is a raw API key string (30+ alphanumeric chars, no spaces)
      3. Text contains an example dynamic-route URL (/r/<slug>/audit)
      4. Text is a very short copy/branding fragment (< 25 chars)
      5. Text contains '(extrapolated from' — LLM-hallucinated page (SS-5)

    Args:
        req: Requirement dict with 'text' and 'category' keys.

    Returns:
        True if the requirement is untestable and should have bdd_needed=False.
    """
    text = req.get("text", "").strip()
    category = req.get("category", "")

    if not text:
        return True

    # Check against pattern list
    for pattern in _UNTESTABLE_PATTERNS:
        if pattern.search(text):
            return True

    # Short copy/branding fragments are noise
    if category in ("copy", "branding") and len(text) < _MIN_TESTABLE_COPY_LENGTH:
        return True

    # SS-5: Extrapolated pages — only for page category
    if category == "page" and _EXTRAPOLATED_PAGE_PATTERN.search(text):
        return True

    return False


# ─── F-1 (ITR-11): Category → Priority Mapping ──────────────────────────
_CATEGORY_PRIORITY = {
    'url': 'p0', 'compliance': 'p0', 'integration': 'p0',
    'integration_endpoint': 'p0', 'content_constraint': 'p0',
    'page': 'p1', 'feature': 'p1', 'data_fidelity': 'p1',
    'config': 'p2', 'model': 'p2', 'scaffold_cleanup': 'p2',
    'delivery': 'p1', 'design': 'p1',
}


# ─── Delivery Standard Requirements ─────────────────────────────────────

_DELIVERY_STANDARDS = [
    {
        "req_id": "REQ-DELIVERY-001",
        "text": "Project-specific README.md with setup instructions, tech stack, and usage",
        "category": "delivery",
        "test_type": "e2e",
        "bdd_needed": True,  # T-2: changed from False — must generate BDD test
        "suggested_test": "[REQ-DELIVERY-001] Verify project README.md exists and is project-specific (not boilerplate)",
    },
    {
        "req_id": "REQ-DELIVERY-002",
        "text": "Every import statement in src/ must resolve to an existing file",
        "category": "delivery",
        "test_type": "e2e",
        "bdd_needed": True,
        "suggested_test": "[REQ-DELIVERY-002] Verify all imports resolve — type/build checker exits 0",
    },
    {
        "req_id": "REQ-DELIVERY-003",
        "text": "When design-tokens.json exists, source code must import or reference design tokens",
        "category": "design",
        "test_type": "e2e",
        "bdd_needed": True,
        "suggested_test": "[REQ-DELIVERY-003] Verify design-tokens.json is consumed by globals.css or component files",
    },
    {
        "req_id": "REQ-SCAFFOLD-001",
        "text": "All scaffold boilerplate (default titles, placeholder logos, sample content) must be replaced with project-specific content",
        "category": "scaffold_cleanup",
        "test_type": "e2e",
        "bdd_needed": True,
        "suggested_test": "[REQ-SCAFFOLD-001] Verify no scaffold boilerplate remains (no 'Create Next App', no default logos, no sample data)",
    },
    # ── ITR-31: Substrate Infrastructure Standards ───────────────────
    {
        "req_id": "REQ-INFRA-BUILD-001",
        "text": "npm run build must exit with code 0 — no TypeScript errors, no missing modules, no ESLint failures",
        "category": "infra",
        "test_type": "e2e",
        "bdd_needed": True,
        "suggested_test": "[REQ-INFRA-BUILD-001] Verify `npm run build` exits 0 with no compilation errors",
        "expected_literals": [],
        "acceptance_criteria": _CATEGORY_THEN_CLAUSES.get("infra", ""),
        "priority": "p2",
    },
    {
        "req_id": "REQ-INFRA-TSCONFIG-001",
        "text": "tsconfig.json must have coherent paths — all @/ aliases resolve, exclude includes node_modules and tmp/",
        "category": "infra",
        "test_type": "unit",
        "bdd_needed": True,
        "suggested_test": "[REQ-INFRA-TSCONFIG-001] Verify tsconfig paths resolve and exclude patterns are correct",
        "expected_literals": [],
        "acceptance_criteria": _CATEGORY_THEN_CLAUSES.get("infra", ""),
        "priority": "p2",
    },
    {
        "req_id": "REQ-INFRA-ROUTE-001",
        "text": "All routes defined in navigation and sitemap must be reachable, return 200 OK, and render real content (not error pages or blank pages)",
        "category": "infra",
        "test_type": "e2e",
        "bdd_needed": True,
        "suggested_test": "[REQ-INFRA-ROUTE-001] Verify all nav/sitemap routes are reachable and return real content",
        "expected_literals": [],
        "acceptance_criteria": _CATEGORY_THEN_CLAUSES.get("infra", ""),
        "priority": "p2",
    },

]


# Keep the REQ-DEL prefix alias used by some test files
_DELIVERY_STANDARDS_BY_ID = {s["req_id"]: s for s in _DELIVERY_STANDARDS}

# Also support the legacy REQ-DEL prefix (some older code references this)
for _std in _DELIVERY_STANDARDS:
    if _std["req_id"].startswith("REQ-DELIVERY-"):
        _legacy_id = _std["req_id"].replace("REQ-DELIVERY-", "REQ-DEL-")
        _DELIVERY_STANDARDS_BY_ID[_legacy_id] = _std


def _suggest_test_description(req: Dict) -> str:
    """Generate a human-readable test description from a requirement.

    Args:
        req: Requirement dict with 'text' and 'category' keys.

    Returns:
        A suggested test description string.
    """
    text = req.get("text", "")
    category = req.get("category", "feature")
    req_id = req.get("id", "") or req.get("req_id", "") or _generate_skeleton_req_id(req)

    # Truncate to keep it reasonable
    short_text = text[:100] + ("..." if len(text) > 100 else "")

    prefix_map = {
        "url": "Verify URL is present",
        "literal": "Verify literal value appears in output",
        "config": "Verify environment variable is configured",
        "integration": "Verify integration is functional",
        "integration_endpoint": "Verify API endpoint responds correctly",
        "page": "Verify page renders correctly",
        "model": "Verify data model structure",
        "compliance": "Verify compliance requirement is met",
        "content_constraint": "Verify content matches specification",
    }

    prefix = prefix_map.get(category, "Verify feature works")
    return f"[{req_id}] {prefix}: {short_text}"


def _generate_skeleton_req_id(req: Dict) -> str:
    """Generate a deterministic fallback REQ-ID from requirement text.

    Used when a requirement dict has no 'id' or 'req_id' field. Produces
    a stable hash-based ID so the same text always yields the same REQ-ID.

    Args:
        req: Requirement dict with at least a 'text' field.

    Returns:
        A string like 'REQ-a1b2c3d4' derived from the text hash.
    """
    text = req.get("text", "")
    hash_hex = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"REQ-{hash_hex}"


def generate_test_skeleton(
    project_dir: str,
    original_prompt: str = "",
    phase_req_ids: list = None,
) -> Dict[str, Any]:
    """Generate test expectations skeleton from requirements ledger.

    For each requirement, determines:
      - What type of test is needed (unit, integration, e2e, literal, config)
      - Whether BDD is applicable (based on requirement category)
      - Suggested test description
      - F-1: expected_literals (cross-ref from content_manifest.json)
      - F-1: acceptance_criteria (from _CATEGORY_THEN_CLAUSES)
      - F-1: priority (p0/p1/p2 from _CATEGORY_PRIORITY)
      - U-8: When original_prompt is provided, uses scoped literal matching
             for contextually-relevant literals per requirement.

    Args:
        project_dir: Path to the project directory.
        original_prompt: Optional original user prompt text. When provided,
            enables scoped literal matching (U-8) for per-requirement context.
        phase_req_ids: Optional list of requirement IDs to include. When
            provided, only requirements whose id/req_id matches are included.
            This enables per-phase stub generation at delegation time.

    Returns:
        Skeleton dict with 'requirements' list. Each entry has:
          req_id, text, category, test_type, bdd_needed, suggested_test,
          expected_literals, acceptance_criteria, priority
    """
    # Try to load the requirements ledger
    ledger = _load_ledger(project_dir)
    if not ledger:
        return {"requirements": []}

    requirements = ledger.get("requirements", [])

    # Phase-scoped filtering: when phase_req_ids is provided, only include
    # requirements whose id/req_id is in the list. This enables per-phase
    # TDD stub generation at delegation time (RCA: TDD spiral fix).
    if phase_req_ids is not None:
        phase_set = set(phase_req_ids)
        requirements = [
            r for r in requirements
            if (r.get("id", "") or r.get("req_id", "")) in phase_set
        ]

    # F-1 (ITR-11): Load manifest literals for cross-referencing
    manifest_literals = _load_manifest_literals(project_dir)
    # F-10 (ITR-12): Load manifest as dict for category-based mapping
    manifest_dict = _load_manifest_dict(project_dir)

    # RCA-475 GAP-1: Extract weighted terms from original prompt once
    # (business + integration terms feed into expected_literals per req)
    weighted_terms = None  # type: Optional[dict]
    if original_prompt:
        try:
            from python.helpers.validators.semantic_fidelity import (
                _weighted_extract_key_terms,
            )
            weighted_terms = _weighted_extract_key_terms(original_prompt)
        except Exception:
            weighted_terms = None

    # RCA-475 GAP-2: Generate anti-fabrication hints once
    anti_fab = generate_anti_fabrication_hints(original_prompt=original_prompt)

    skeleton_reqs = []
    for req in requirements:
        # F-5 (ITR-18): Fallback chain for req_id resolution.
        # Ledger uses 'id', but raw dicts may use 'req_id' or neither.
        req_id = req.get("id", "") or req.get("req_id", "") or _generate_skeleton_req_id(req)
        category = req.get("category", "feature")
        text = req.get("text", "")
        data_source = req.get("data_source", "static")

        test_type = _CATEGORY_TEST_TYPE.get(category, "unit")
        bdd_needed = category in _BDD_CATEGORIES

        # ITR-30: Filter untestable requirements — API keys, example URLs,
        # copy fragments should not inflate bdd_needed count.
        if bdd_needed and _is_untestable_requirement(req):
            bdd_needed = False

        # F-10 (ITR-12): Use category-based literal mapping when manifest dict
        # is available. Fall back to text-based matching for backward compat.
        # RCA-461 R-1: Manifest and prompt are SUPPLEMENTAL, not mutually exclusive.
        # When manifest yields 0 literals for a requirement (e.g., category maps to
        # keys that don't exist in this manifest), fall back to prompt extraction.
        if manifest_dict:
            expected_literals = _match_literals_by_category(category, manifest_dict, req_text=text)
            # R-1: Supplement — if manifest yielded nothing, try prompt-based extraction
            if not expected_literals and original_prompt:
                expected_literals = _match_literals_scoped(text, original_prompt)
        else:
            # U-8 (ITR-29): Use scoped literal matching when original prompt
            # is available — extracts only contextually-relevant literals.
            # Falls back to global matching when prompt is unavailable.
            if original_prompt:
                expected_literals = _match_literals_scoped(text, original_prompt)
            else:
                expected_literals = _match_literals(text, manifest_literals)

        # RCA-475 GAP-1: Merge high-weight terms into expected_literals
        if weighted_terms and expected_literals is not None:
            high_weight = (
                weighted_terms.get("business", [])
                + weighted_terms.get("integration", [])
            )
            if high_weight:
                merged = list(expected_literals) + high_weight
                # Deduplicate (case-insensitive)
                seen = set()  # type: set
                deduped = []  # type: list
                for lit in merged:
                    key = lit.lower().strip()
                    if key and key not in seen:
                        seen.add(key)
                        deduped.append(lit)
                expected_literals = deduped

        skeleton_reqs.append({
            "req_id": req_id,
            "text": text,
            "category": category,
            "test_type": test_type,
            "bdd_needed": bdd_needed,
            "suggested_test": _suggest_test_description(req),
            # F-1 (ITR-11): Enrichment fields for richer BDD/TDD
            "expected_literals": expected_literals,
            # F-2 (ITR-15): For feature requirements, try sub-type THEN clause
            # first, fall back to generic 'feature' clause.
            # WB-4: Same for compliance — try sub-type first, fall back to generic.
            "acceptance_criteria": (
                _CATEGORY_THEN_CLAUSES.get(
                    _classify_feature_subtype(text), ""
                ) if category == "feature"
                else _CATEGORY_THEN_CLAUSES.get(
                    _classify_compliance_subtype(text), ""
                ) if category == "compliance"
                else ""
            ) or _CATEGORY_THEN_CLAUSES.get(category, ""),
            "priority": _CATEGORY_PRIORITY.get(category, "p1"),
            # RCA-475 GAP-2: Anti-fabrication inverse assertions
            "forbidden_literals": anti_fab,
        })

        # F-2: Inject data fidelity companion requirement for dynamic data sources
        if data_source in ("api", "database"):
            skeleton_reqs.append({
                "req_id": f"{req_id}-FETCH",
                "text": f"Data for '{text}' must be fetched dynamically from {data_source}, not hardcoded",
                "category": "data_fidelity",
                "test_type": "integration",
                "bdd_needed": True,
                "suggested_test": (
                    f"[{req_id}-FETCH] Verify data is fetched from {data_source}, "
                    f"not hardcoded arrays"
                ),
            })

    # Inject delivery standard REQs (dedup by req_id)
    # Skip when phase_req_ids is set — per-phase generation should only
    # include the phase's actual requirements, not global delivery standards.
    if phase_req_ids is None:
        _inject_delivery_standards(skeleton_reqs)

        # F1-b: Inject REQ-SHELL-001 when >=2 page requirements exist
        _inject_shell_standard(skeleton_reqs, requirements)

    skeleton = {"requirements": skeleton_reqs}

    # Persist to project docs/
    _write_skeleton(project_dir, skeleton)

    logger.info(
        f"[TEST SKELETON] Generated skeleton for {len(skeleton_reqs)} requirements "
        f"({len(_DELIVERY_STANDARDS)} delivery standards)"
    )
    return skeleton


# ─── RCA-475 GAP-2: Anti-Fabrication Hints ────────────────────────────────


# Common fabrication patterns — placeholder values that agents tend to invent
_COMMON_FABRICATION_PATTERNS = [
    # Placeholder prices
    "$99/mo", "$9.99/mo", "$19.99", "$49.99", "$199",
    # Placeholder names
    "Acme Corp", "Lorem Ipsum", "John Doe", "Jane Smith",
    "Foo Bar", "Test User", "Sample Inc",
    # Placeholder URLs
    "example.com", "placeholder.com", "test.example.com",
    # Placeholder emails
    "user@example.com", "test@test.com", "admin@admin.com",
    # Placeholder phone numbers
    "555-0100", "555-0123",
]


def generate_anti_fabrication_hints(
    original_prompt: str = "",
) -> List[str]:
    """Generate list of common fabrication patterns to forbid.

    RCA-475 GAP-2: Returns a list of placeholder values that agents
    commonly fabricate. Values that appear in the original prompt are
    excluded (they're real, not fabricated).

    Args:
        original_prompt: The user's original prompt text. Values found
            in the prompt are excluded from the forbidden list.

    Returns:
        List of forbidden literal strings.
    """
    prompt_lower = original_prompt.lower() if original_prompt else ""

    forbidden = []
    for pattern in _COMMON_FABRICATION_PATTERNS:
        # Skip if this pattern appears in the user's actual prompt
        if prompt_lower and pattern.lower() in prompt_lower:
            continue
        forbidden.append(pattern)

    return forbidden


# ─── Delivery Standard Injection ─────────────────────────────────────────


def _inject_delivery_standards(skeleton_reqs: List[Dict]) -> None:
    """Inject universal delivery standard REQs into skeleton.

    Dedup: if a delivery REQ-ID already exists in the list, skip it.
    This ensures re-generation doesn't create duplicates.

    Args:
        skeleton_reqs: Mutable list of skeleton requirement dicts.
    """
    existing_ids = {r["req_id"] for r in skeleton_reqs}
    for standard in _DELIVERY_STANDARDS:
        if standard["req_id"] not in existing_ids:
            skeleton_reqs.append(dict(standard))  # Defensive copy
            existing_ids.add(standard["req_id"])


def _inject_shell_standard(
    skeleton_reqs: List[Dict],
    raw_requirements: List[Dict],
) -> None:
    """Conditionally inject REQ-SHELL-001 when >=2 page requirements exist.

    Multi-page apps need a shared navigation component so users can move
    between pages. Single-page apps do not need this.

    F1-b: Auto-generates REQ-SHELL-001 delivery standard when the ledger
    contains two or more 'page' category requirements.

    Args:
        skeleton_reqs: Mutable list of skeleton requirement dicts.
        raw_requirements: Original requirements from the ledger (to count pages).
    """
    # Count page-category requirements in the original ledger
    page_count = sum(
        1 for r in raw_requirements
        if r.get("category", "") == "page"
    )

    if page_count < 2:
        return  # Single-page or no-page app — no shared nav needed

    # Dedup: skip if already present
    existing_ids = {r["req_id"] for r in skeleton_reqs}
    if "REQ-SHELL-001" in existing_ids:
        return

    skeleton_reqs.append({
        "req_id": "REQ-SHELL-001",
        "text": "Shared navigation component exists and is imported by root layout",
        "category": "ui_shell",
        "test_type": "e2e",
        "bdd_needed": True,
        "suggested_test": (
            "[REQ-SHELL-001] Verify shared navigation component exists "
            "and is imported by root layout"
        ),
        "expected_literals": [],
        "acceptance_criteria": _CATEGORY_THEN_CLAUSES.get("ui_shell", ""),
        "priority": "p1",
    })


# ─── Internal Helpers ────────────────────────────────────────────────────


def _load_ledger(project_dir: str) -> Optional[Dict]:
    """Load requirements ledger from project directory.

    Checks multiple locations:
      1. .agix.proj/requirements_ledger.json
      2. requirements_ledger.json (project root)

    Returns:
        Ledger dict or None if not found.
    """
    candidates = [
        _planning_path(project_dir, "requirements_ledger"),
        os.path.join(project_dir, ".agix.proj", "requirements_ledger.json"),
        os.path.join(project_dir, ".agix.proj", "requirements-ledger.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[TEST SKELETON] Failed to load ledger: {e}")
                return None
    return None


def _is_web_project(project_dir: str) -> bool:
    """Detect if this is a web/fullstack project.

    Heuristic: package.json exists in project root.
    """
    return os.path.exists(os.path.join(project_dir, "package.json"))


def _write_skeleton(project_dir: str, skeleton: Dict) -> None:
    """Persist test skeleton JSON to project docs/."""
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    skeleton_path = os.path.join(docs_dir, "test-skeleton.json")
    try:
        with open(skeleton_path, "w") as f:
            json.dump(skeleton, f, indent=2)
    except Exception as e:
        logger.warning(f"[TEST SKELETON] Failed to write skeleton to {skeleton_path}: {e}")

# Backward-compat alias: generate_tdd_stubs was relocated to tdd_generator.generate_tdd_tests
try:
    from python.helpers.tdd_generator import generate_tdd_tests as generate_tdd_stubs  # noqa: F401
except ImportError:
    generate_tdd_stubs = None  # type: ignore
