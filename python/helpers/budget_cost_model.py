"""
Budget-Based Feature Cost Estimation Model.

Replaces static MAX_REQUIREMENTS_PER_DELEGATION=5 with a cost-aware model
that estimates iteration cost per requirement based on:
  - Category-based base costs (branding=5, feature=25, complex=60)
  - Complexity multipliers from requirement text signals (cron, auth, payment)
  - Integration overhead from dependency graph edges
  - Fixed TDD overhead per requirement

Architecture Doc: agix-devdocs/docs/architecture/budget-based-delegation-system.md
Companion to: agix-devdocs/docs/architecture/decomposition-delegation-integration.md
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Cost Model Constants
# ═══════════════════════════════════════════════════════════════════════════

# Base iteration cost by requirement category.
# These are starting estimates — the self-calibrating system (future WP)
# will adjust based on historical actual vs. estimated.
BASE_COSTS: dict[str, int] = {
    "branding": 5,       # Set title, favicon, colors — minimal code
    "content": 8,        # Static content, CAN-SPAM page, privacy policy
    "config": 10,        # Environment variables, package.json, tsconfig
    "design": 15,        # CSS changes, design token wiring, layout
    "url": 5,            # Route registration, nav link
    "feature": 25,       # Default feature cost — page + API route
    "integration": 15,   # Wire internal/external integration
}

# Fixed overhead per requirement for TDD (write test + verify green)
TEST_OVERHEAD: int = 10

# Iterations per import edge in the dependency graph
_ITERATIONS_PER_IMPORT: int = 5

# Maximum complexity multiplier (prevents runaway estimates)
_MAX_COMPLEXITY_MULTIPLIER: float = 3.0

# Complexity signal groups — each group adds to the multiplier
_COMPLEXITY_SIGNALS: list[tuple[list[str], float]] = [
    (["cron", "schedule", "queue", "pipeline", "batch", "job"], 0.5),
    (["auth", "login", "session", "token", "oauth", "jwt"], 0.5),
    (["real-time", "realtime", "websocket", "stream", "sse"], 0.7),
    (["payment", "stripe", "billing", "subscription", "checkout"], 0.5),
    (["upload", "file", "blob", "storage", "s3"], 0.3),
    (["email", "smtp", "resend", "sendgrid", "notification"], 0.3),
]


# ═══════════════════════════════════════════════════════════════════════════
# FeatureCostEstimate Dataclass
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FeatureCostEstimate:
    """Cost estimate for a single requirement.

    Fields:
        req_id: The requirement's unique ID (e.g., "REQ-001")
        base_cost: Base iteration cost from category lookup
        complexity_multiplier: 1.0-3.0 based on text complexity signals
        integration_overhead: Extra iterations for cross-module wiring
        test_overhead: Fixed TDD overhead
        total_iterations: Final estimated iterations
        confidence: 0.0-1.0 estimation confidence
    """
    req_id: str
    base_cost: int
    complexity_multiplier: float
    integration_overhead: int
    test_overhead: int
    total_iterations: int
    confidence: float = 0.7


# ═══════════════════════════════════════════════════════════════════════════
# Cost Estimation Functions
# ═══════════════════════════════════════════════════════════════════════════

def _estimate_complexity_multiplier(text: str) -> float:
    """Estimate complexity multiplier from requirement text.

    Scans the text for complexity signal words (cron, auth, payment, etc.)
    and adds to a base multiplier of 1.0. Capped at 3.0.

    Args:
        text: The requirement's descriptive text.

    Returns:
        Float in [1.0, 3.0].
    """
    multiplier = 1.0
    text_lower = text.lower()

    for keywords, addition in _COMPLEXITY_SIGNALS:
        if any(kw in text_lower for kw in keywords):
            multiplier += addition

    return min(_MAX_COMPLEXITY_MULTIPLIER, multiplier)


def _find_module_for_req(req_id: str, dep_graph: dict) -> str | None:
    """Find the module in the dependency graph that owns this requirement.

    Args:
        req_id: Requirement ID to search for.
        dep_graph: The structured dependency graph from the architect.

    Returns:
        Module path string if found, else None.
    """
    modules = dep_graph.get("modules", {})
    for module_path, module_info in modules.items():
        if not isinstance(module_info, dict):
            continue
        req_guids = module_info.get("req_guids", [])
        if req_id in req_guids:
            return module_path
    return None


def _estimate_integration_overhead(req_id: str, dep_graph: dict) -> int:
    """Calculate extra iterations for cross-module wiring.

    Each import edge adds ~5 iterations (read target, add import, wire call, test).

    Args:
        req_id: Requirement ID.
        dep_graph: Structured dependency graph.

    Returns:
        Integration overhead in iterations.
    """
    module = _find_module_for_req(req_id, dep_graph)
    if not module:
        return 0

    modules = dep_graph.get("modules", {})
    module_info = modules.get(module, {})
    import_count = len(module_info.get("imports", []))

    return import_count * _ITERATIONS_PER_IMPORT


def estimate_feature_cost(
    req: dict,
    dep_graph: dict,
) -> FeatureCostEstimate:
    """Calculate total iteration cost for a single requirement.

    Combines category base cost, complexity multiplier, integration
    overhead, and TDD overhead into a total estimate.

    Args:
        req: Requirement dict with keys: id, text, category.
        dep_graph: Structured dependency graph (may be empty).

    Returns:
        FeatureCostEstimate with all cost breakdowns.
    """
    req_id = req.get("id", "UNKNOWN")
    text = req.get("text", "")
    category = req.get("category", "feature")

    # Base cost from category — unknown categories fall back to "feature"
    base = BASE_COSTS.get(category, BASE_COSTS["feature"])

    # Complexity multiplier from text signals
    multiplier = _estimate_complexity_multiplier(text)

    # Integration overhead from dependency graph
    integration = _estimate_integration_overhead(req_id, dep_graph)

    # Total = base × multiplier + integration + TDD
    total = int(base * multiplier) + integration + TEST_OVERHEAD

    return FeatureCostEstimate(
        req_id=req_id,
        base_cost=base,
        complexity_multiplier=multiplier,
        integration_overhead=integration,
        test_overhead=TEST_OVERHEAD,
        total_iterations=total,
        confidence=0.7,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Wave Planning
# ═══════════════════════════════════════════════════════════════════════════

def _sort_by_dependency_order(
    estimates: list[FeatureCostEstimate],
    dep_graph: dict,
) -> list[FeatureCostEstimate]:
    """Sort estimates so that depended-upon requirements come first.

    Uses topological sort based on the dependency graph: if module A
    imports from module B, then B's requirements should be processed first.

    Args:
        estimates: List of cost estimates.
        dep_graph: Structured dependency graph.

    Returns:
        Sorted list of estimates (depended-upon first).
    """
    if not dep_graph or "modules" not in dep_graph:
        return estimates

    modules = dep_graph.get("modules", {})

    # Build req_id → module mapping
    req_to_module: dict[str, str] = {}
    for mod_path, mod_info in modules.items():
        if isinstance(mod_info, dict):
            for rid in mod_info.get("req_guids", []):
                req_to_module[rid] = mod_path

    # Build module → set of dependent modules (who imports this module)
    dependents_count: dict[str, int] = {}
    for mod_path, mod_info in modules.items():
        if isinstance(mod_info, dict):
            dependents_count.setdefault(mod_path, 0)
            for imported in mod_info.get("imports", []):
                dependents_count[imported] = dependents_count.get(imported, 0) + 1

    # Score each estimate: more dependents = lower score = processed first
    def sort_key(est: FeatureCostEstimate) -> tuple:
        mod = req_to_module.get(est.req_id, "")
        dep_score = -(dependents_count.get(mod, 0))  # More dependents → earlier
        return (dep_score, est.req_id)

    return sorted(estimates, key=sort_key)


def plan_delegation_waves(
    requirements: list[dict],
    dep_graph: dict,
    budget_iterations: int,
) -> list[list[FeatureCostEstimate]]:
    """Split requirements into budget-aware waves.

    Uses greedy bin-packing: add requirements to current wave until
    budget is exceeded, then start a new wave. Requirements are sorted
    by dependency order (depended-upon first).

    Args:
        requirements: List of requirement dicts with id, text, category.
        dep_graph: Structured dependency graph.
        budget_iterations: Max iterations per wave.

    Returns:
        List of waves, each wave is a list of FeatureCostEstimate.
    """
    if not requirements:
        return []

    # Estimate cost for each requirement
    estimates = [estimate_feature_cost(r, dep_graph) for r in requirements]

    # Sort by dependency order
    estimates = _sort_by_dependency_order(estimates, dep_graph)

    # Greedy bin-packing into waves
    waves: list[list[FeatureCostEstimate]] = []
    current_wave: list[FeatureCostEstimate] = []
    current_cost: int = 0

    for est in estimates:
        # If adding this estimate would exceed budget AND we have items, start new wave
        if current_cost + est.total_iterations > budget_iterations and current_wave:
            waves.append(current_wave)
            current_wave = []
            current_cost = 0

        current_wave.append(est)
        current_cost += est.total_iterations

    if current_wave:
        waves.append(current_wave)

    return waves


# ═══════════════════════════════════════════════════════════════════════════
# Integration Requirements Generator
# ═══════════════════════════════════════════════════════════════════════════

def _short_path(path: str, segments: int = 2) -> str:
    """Shorten a module path to its last N segments for human-readable text.

    'src/app/dashboard/page.tsx' → 'dashboard/page.tsx'
    'src/lib/cron.ts' → 'lib/cron.ts'
    'cron.ts' → 'cron.ts'
    """
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[-segments:]) if len(parts) > segments else path


def _stable_int_id(source: str, target: str) -> str:
    """Generate a stable REQ-INT-xxx ID from source and target modules.

    Uses a hash of the concatenated module paths to produce a
    deterministic, collision-resistant 3-character hex suffix.

    Args:
        source: Source module path.
        target: Target module path.

    Returns:
        String like "REQ-INT-a3f".
    """
    h = hashlib.md5(f"{source}|{target}".encode()).hexdigest()[:3]
    return f"REQ-INT-{h}"


def generate_integration_requirements(
    dep_graph: dict,
) -> list[dict]:
    """Auto-generate integration requirements from dependency graph.

    Creates REQ-INT-xxx entries for:
    1. Each imports edge in the module graph (module A imports from module B)
    2. Each page_api_binding (page fetches from API route)

    Args:
        dep_graph: Structured dependency graph from the architect.
                   Expected format:
                   {
                     "modules": {module_path: {imports, exports, called_by, req_guids}},
                     "page_api_bindings": [{page, api, method}]
                   }

    Returns:
        List of integration requirement dicts with:
            id, text, category, source_module, target_module, parent_req_guids
    """
    if not dep_graph:
        return []

    reqs: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Module import edges
    modules = dep_graph.get("modules", {})
    for source_mod, mod_info in modules.items():
        if not isinstance(mod_info, dict):
            continue
        imports = mod_info.get("imports", [])
        parent_guids = mod_info.get("req_guids", [])
        source_basename = _short_path(source_mod)

        for target_mod in imports:
            req_id = _stable_int_id(source_mod, target_mod)
            if req_id in seen_ids:
                continue
            seen_ids.add(req_id)

            target_basename = _short_path(target_mod)

            reqs.append({
                "id": req_id,
                "text": f"{source_basename} MUST import from {target_basename}",
                "category": "integration",
                "source_module": source_mod,
                "target_module": target_mod,
                "parent_req_guids": list(parent_guids),
            })

    # 2. Page-API bindings
    bindings = dep_graph.get("page_api_bindings", [])
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        page = binding.get("page", "")
        api = binding.get("api", "")
        method = binding.get("method", "GET")

        if not page or not api:
            continue

        req_id = _stable_int_id(page, api)
        if req_id in seen_ids:
            continue
        seen_ids.add(req_id)

        page_basename = _short_path(page)
        api_basename = _short_path(api)

        reqs.append({
            "id": req_id,
            "text": f"{page_basename} MUST call {method} {api_basename}",
            "category": "integration",
            "source_module": page,
            "target_module": api,
            "parent_req_guids": binding.get("req_guids", []),
        })

    return reqs


# ═══════════════════════════════════════════════════════════════════════════
# Dependency Graph Validation
# ═══════════════════════════════════════════════════════════════════════════

_REQUIRED_MODULE_KEYS = {"imports", "exports", "called_by", "req_guids"}


def validate_dependency_graph(graph: dict) -> list[str]:
    """Validate structural integrity of the architect's dependency graph.

    Called during Phase 2.5 validation to ensure the architect produced
    a well-formed dependency graph that downstream systems can consume.

    Args:
        graph: The parsed dependency-graph.json content.

    Returns:
        List of error strings. Empty list = valid graph.
    """
    errors: list[str] = []

    if not graph:
        errors.append("Dependency graph is empty or None")
        return errors

    # 1. Must have 'modules' key
    modules = graph.get("modules")
    if modules is None:
        errors.append("Missing required key: 'modules'")
        return errors

    if not isinstance(modules, dict):
        errors.append("'modules' must be a dict mapping module paths to info dicts")
        return errors

    # 2. Each module must have required keys
    for mod_path, mod_info in modules.items():
        if not isinstance(mod_info, dict):
            errors.append(f"Module '{mod_path}': value must be a dict, got {type(mod_info).__name__}")
            continue

        for key in _REQUIRED_MODULE_KEYS:
            if key not in mod_info:
                errors.append(f"Module '{mod_path}': missing required key '{key}'")

    # 3. page_api_bindings validation
    bindings = graph.get("page_api_bindings", [])
    if isinstance(bindings, list):
        for i, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                errors.append(f"page_api_bindings[{i}]: must be a dict")
                continue
            if not binding.get("page"):
                errors.append(f"page_api_bindings[{i}]: missing 'page' field")
            if not binding.get("api"):
                errors.append(f"page_api_bindings[{i}]: missing 'api' field")

    return errors


# ═══════════════════════════════════════════════════════════════════════════
# WP-4: Integration TDD Stub Generation
# ═══════════════════════════════════════════════════════════════════════════

def _sanitize_test_name(name: str) -> str:
    """Convert a module path to a valid test function name component.

    'src/lib/cron.ts' → 'cron'
    'src/app/dashboard/page.tsx' → 'dashboard_page'
    """
    import re
    # Remove extension
    name = re.sub(r'\.\w+$', '', name)
    # Take last 2 path segments
    parts = name.replace("\\", "/").split("/")
    short = "_".join(parts[-2:]) if len(parts) > 1 else parts[-1]
    # Replace non-alphanumeric with underscore
    return re.sub(r'[^a-zA-Z0-9_]', '_', short).strip('_').lower()


def generate_integration_test_stubs(
    dep_graph: dict,
) -> list[dict]:
    """Generate structured integration test stub descriptors from dependency graph.

    Each stub describes a specific assertion the code agent should implement:
    - import_exists: Verify module A imports from module B
    - fetch_exists: Verify page component calls API route

    Args:
        dep_graph: Structured dependency graph from the architect.

    Returns:
        List of stub dicts with: test_name, assertion_type, source_file, target, req_id
    """
    if not dep_graph:
        return []

    stubs: list[dict] = []
    seen_names: set[str] = set()

    # 1. Import edge stubs
    modules = dep_graph.get("modules", {})
    for source_mod, mod_info in modules.items():
        if not isinstance(mod_info, dict):
            continue
        imports = mod_info.get("imports", [])
        req_guids = mod_info.get("req_guids", [])

        for target_mod in imports:
            source_name = _sanitize_test_name(source_mod)
            target_name = _sanitize_test_name(target_mod)
            test_name = f"test_{source_name}_imports_{target_name}"

            if test_name in seen_names:
                continue
            seen_names.add(test_name)

            req_id = _stable_int_id(source_mod, target_mod)

            stubs.append({
                "test_name": test_name,
                "assertion_type": "import_exists",
                "source_file": source_mod,
                "target": target_mod,
                "req_id": req_id,
                "description": f"Verify {source_mod} imports from {target_mod}",
            })

    # 2. Page-API binding stubs
    bindings = dep_graph.get("page_api_bindings", [])
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        page = binding.get("page", "")
        api = binding.get("api", "")
        method = binding.get("method", "GET")

        if not page or not api:
            continue

        page_name = _sanitize_test_name(page)
        api_name = _sanitize_test_name(api)
        test_name = f"test_{page_name}_calls_{api_name}"

        if test_name in seen_names:
            continue
        seen_names.add(test_name)

        req_id = _stable_int_id(page, api)

        stubs.append({
            "test_name": test_name,
            "assertion_type": "fetch_exists",
            "source_file": page,
            "target": api,
            "req_id": req_id,
            "description": f"Verify {page} calls {method} {api}",
        })

    return stubs
