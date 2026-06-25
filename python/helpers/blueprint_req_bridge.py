"""
Blueprint → Requirements Ledger Bridge
========================================

Reads architect_plan.json and generates machine-verifiable file_specs
that link blueprint engineering decisions to requirements in the ledger.

This is the core innovation: instead of the verifier guessing what to
check from vague requirement text, it reads explicit acceptance criteria
generated from the architect's structured blueprint.

UNIVERSAL DESIGN: This module is framework-agnostic. It reads file
paths, export names, and acceptance criteria from the architect's
blueprint — which IS framework-specific by nature. The bridge itself
has NO hardcoded framework assumptions (no Next.js paths, no React
conventions, etc.).

Architecture:
    generate_file_specs(project_dir)         → List[FileSpec]
    link_specs_to_requirements(specs, data)  → List[FileSpec with linked_reqs]

Auto-generation logic:
    planned_routes    → file specs using blueprint-provided file paths
    api_contracts     → file specs with must_contain from response_shape
    service_libs      → file specs with must_export from exports[]
    component_bindings → acceptance criteria linking pages to data sources
"""

from __future__ import annotations

import json
import logging
from python.helpers.planning_paths import get_path as _planning_path
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.blueprint_req_bridge")


# ──────────────────────────────────────────────────────────────────────
# File spec generation from architect_plan.json
# ──────────────────────────────────────────────────────────────────────


def _load_architect_plan(project_dir: str) -> Optional[Dict]:
    """Load architect_plan.json from the project directory."""
    plan_path = _planning_path(project_dir, "architect_plan")
    if not os.path.isfile(plan_path):
        # Fallback 1: try root-level architect_plan.json (legacy / unit test)
        plan_path = os.path.join(project_dir, "architect_plan.json")
    if not os.path.isfile(plan_path):
        # Fallback 2: try root-level architect-plan.json
        plan_path = os.path.join(project_dir, "architect-plan.json")
    if not os.path.isfile(plan_path):
        return None
    try:
        with open(plan_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[BLUEPRINT BRIDGE] Failed to load architect_plan.json: {e}")
        return None


def _specs_from_planned_routes(plan: Dict) -> List[Dict]:
    """Generate file specs from planned_routes.

    The architect blueprint is expected to include the actual file path
    for each route (e.g., "file": "src/app/pricing/page.tsx"). This
    module does NOT assume any framework — the path comes from the
    blueprint's own framework-aware decisions.
    """
    specs = []
    for route in plan.get("planned_routes", []):
        path = route.get("path", "")
        component = route.get("component", "")
        file_path = route.get("file", "")  # Architect provides the actual file path

        if not file_path and not path:
            continue

        # If no explicit file path, fall back to using the path as a
        # search hint (the verifier will look for it in project files)
        if not file_path:
            file_path = path.strip("/")
            logger.debug(
                f"[BLUEPRINT BRIDGE] Route {path} has no explicit 'file' field; "
                f"using route path as search hint: {file_path}"
            )

        spec = {
            "path": file_path,
            "must_exist": True,
            "min_bytes": 100,
            "must_contain": [],
            "acceptance": [f"Page component for route {path}"],
            "source": "planned_routes",
        }
        if component:
            spec["acceptance"].append(f"Contains {component} component")
        specs.append(spec)
    return specs


def _specs_from_api_contracts(plan: Dict) -> List[Dict]:
    """Generate file specs from api_contracts.

    Uses the blueprint-provided 'file' field for each API endpoint.
    Falls back to the endpoint path as a search hint if no file is given.
    """
    specs = []
    for contract in plan.get("api_contracts", []):
        endpoint = contract.get("endpoint", "")
        method = contract.get("method", "GET")
        response_shape = contract.get("response_shape", {})
        file_path = contract.get("file", "")  # Architect provides

        if not endpoint:
            continue

        # Fall back to endpoint as search hint
        if not file_path:
            file_path = endpoint.strip("/")

        must_contain = []
        # Extract response shape keys as must_contain
        if isinstance(response_shape, dict):
            for key in response_shape:
                must_contain.append(key)

        spec = {
            "path": file_path,
            "must_exist": True,
            "min_bytes": 50,
            "must_contain": must_contain,
            "acceptance": [f"{method} {endpoint} API route"],
            "source": "api_contracts",
        }
        specs.append(spec)
    return specs


def _specs_from_service_libs(plan: Dict) -> List[Dict]:
    """Generate file specs from service_libs.

    Service libs already include explicit file paths and export names
    from the architect blueprint — no framework assumptions needed.
    """
    specs = []
    for lib in plan.get("service_libs", []):
        file_path = lib.get("file", "")
        name = lib.get("name", "")
        exports = lib.get("exports", [])

        if not file_path:
            continue

        spec = {
            "path": file_path,
            "must_exist": True,
            "min_bytes": 50,
            "must_export": list(exports),
            "must_contain": [name] if name else [],
            "acceptance": [f"Service library: {name}"],
            "source": "service_libs",
        }
        specs.append(spec)
    return specs


def _specs_from_component_bindings(plan: Dict) -> List[Dict]:
    """Generate acceptance criteria from component_bindings.

    Uses the blueprint-provided 'file' field if available. These
    augment existing route specs with data source information.
    """
    specs = []
    for binding in plan.get("component_bindings", []):
        data_source = binding.get("data_source", "")
        file_path = binding.get("file", "")
        route = binding.get("route", "")

        # Must have either a file or route to generate a spec
        if not file_path and not route:
            continue

        # If no file, use route as search hint
        if not file_path:
            file_path = route.strip("/")

        acceptance = []
        must_import = []

        if data_source:
            # Extract the module name from "stripe.getProducts" → "stripe"
            parts = data_source.split(".")
            if len(parts) >= 1:
                must_import.append(parts[0])
            acceptance.append(f"Uses {data_source} for data")

        spec = {
            "path": file_path,
            "must_exist": True,
            "must_import": must_import,
            "acceptance": acceptance,
            "source": "component_bindings",
        }
        specs.append(spec)
    return specs


def _merge_specs(specs_list: List[List[Dict]]) -> List[Dict]:
    """Merge specs from multiple sources, combining by file path.

    When multiple sources generate specs for the same file,
    merge their criteria (must_contain, must_import, acceptance, etc.).
    """
    merged: Dict[str, Dict] = {}

    for specs in specs_list:
        for spec in specs:
            path = spec.get("path", "")
            if not path:
                continue
            if path not in merged:
                merged[path] = {
                    "path": path,
                    "must_exist": True,
                    "min_bytes": 0,
                    "must_contain": [],
                    "must_import": [],
                    "must_export": [],
                    "acceptance": [],
                    "source": [],
                }
            target = merged[path]
            # Merge fields
            target["min_bytes"] = max(target["min_bytes"], spec.get("min_bytes", 0))
            for field in ("must_contain", "must_import", "must_export", "acceptance"):
                for item in spec.get(field, []):
                    if item not in target[field]:
                        target[field].append(item)
            source = spec.get("source", "")
            if source and source not in target["source"]:
                target["source"].append(source)

    return list(merged.values())


def generate_file_specs(project_dir: str) -> List[Dict]:
    """Generate machine-verifiable file specs from architect_plan.json.

    This is the primary bridge function. It reads the architect's
    structured plan and produces file-level acceptance criteria.

    UNIVERSAL: All file paths come from the blueprint itself — this
    function does NOT assume any particular framework or convention.

    Args:
        project_dir: Root directory of the project

    Returns:
        List of file spec dicts, each with:
        - path: relative file path (from blueprint)
        - must_exist: bool
        - min_bytes: minimum file size
        - must_contain: list of strings that must appear
        - must_import: list of import patterns
        - must_export: list of export patterns
        - acceptance: human-readable criteria
    """
    plan = _load_architect_plan(project_dir)
    if not plan:
        return []

    specs_groups = [
        _specs_from_planned_routes(plan),
        _specs_from_api_contracts(plan),
        _specs_from_service_libs(plan),
        _specs_from_component_bindings(plan),
    ]

    # Also read inline file_specs if the architect already wrote them
    inline_specs = plan.get("file_specs", {})
    if isinstance(inline_specs, dict):
        inline_list = []
        for path, criteria in inline_specs.items():
            if isinstance(criteria, dict):
                inline_list.append({
                    "path": path,
                    "must_exist": True,
                    **criteria,
                    "source": "inline_file_specs",
                })
        specs_groups.append(inline_list)

    return _merge_specs(specs_groups)


# ──────────────────────────────────────────────────────────────────────
# Linking specs to requirements
# ──────────────────────────────────────────────────────────────────────


def link_specs_to_requirements(
    specs: List[Dict],
    agent_data: dict,
) -> List[Dict]:
    """Link file specs to REQ-IDs from the requirements ledger.

    Uses fuzzy text matching between requirement text and spec criteria
    to establish links. For each spec, finds requirements whose text
    mentions the spec's path, service name, or acceptance criteria.

    Args:
        specs: List of file spec dicts from generate_file_specs()
        agent_data: The agent.data dict containing the requirements ledger

    Returns:
        The same specs list, each augmented with 'linked_reqs' list
    """
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return specs

    reqs = ledger.get("requirements", [])
    if not reqs:
        return specs

    for spec in specs:
        linked = []
        spec_path = spec.get("path", "").lower()
        spec_contains = [c.lower() for c in spec.get("must_contain", [])]
        spec_imports = [i.lower() for i in spec.get("must_import", [])]
        spec_acceptance = " ".join(spec.get("acceptance", [])).lower()

        for req in reqs:
            req_text = req.get("text", "").lower()
            req_category = req.get("category", "").lower()

            # Match by path fragments
            path_parts = spec_path.replace("/", " ").replace(".", " ").split()
            path_match = any(part in req_text for part in path_parts if len(part) > 3)

            # Match by must_contain terms
            contain_match = any(term in req_text for term in spec_contains if len(term) > 2)

            # Match by service name (imports)
            import_match = any(imp in req_text for imp in spec_imports)

            if path_match or contain_match or import_match:
                linked.append(req["id"])

        spec["linked_reqs"] = linked

    return specs
