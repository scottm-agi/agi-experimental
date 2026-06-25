"""
Blueprint Verifier — F-7: Route Purpose Separation.

Validates that the architect's blueprint correctly separates:
- API routes (data endpoints, /api/*)
- Page routes (UI pages, rendered by the framework)

RCA: rca_ss7_route_purpose_separation.md
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.blueprint_verifier")


# ── Route Purpose Separation (F-7) ────────────────────────────────────────────


def check_route_purpose_separation(blueprint: Dict[str, Any]) -> List[str]:
    """Verify that the blueprint separates API routes from page routes.

    RCA-SS7: Architects sometimes conflate /api/* routes with page routes,
    causing code agents to produce incorrect route handler implementations.
    The blueprint must explicitly list api_routes and page_routes (or
    page_map / planned_api_routes) as separate keys.

    Args:
        blueprint: Parsed architect blueprint dict.

    Returns:
        List of violation strings. Empty list means the blueprint is valid.
    """
    violations: List[str] = []

    has_api = (
        "api_routes" in blueprint
        or "planned_api_routes" in blueprint
        or any("api" in str(k).lower() and "route" in str(k).lower() for k in blueprint)
    )
    has_page = (
        "page_routes" in blueprint
        or "page_map" in blueprint
        or any("page" in str(k).lower() and ("route" in str(k).lower() or "map" in str(k).lower()) for k in blueprint)
    )

    if not has_api:
        violations.append(
            "Blueprint missing api_routes / planned_api_routes section. "
            "All API endpoints must be explicitly listed."
        )
    if not has_page:
        violations.append(
            "Blueprint missing page_routes / page_map section. "
            "All UI pages must be explicitly listed."
        )

    # Check for mixed routes (API paths appearing inside page_routes etc.)
    page_routes = blueprint.get("page_routes") or blueprint.get("page_map") or {}
    if isinstance(page_routes, dict):
        for route_key in page_routes:
            if str(route_key).startswith("/api/"):
                violations.append(
                    f"API route '{route_key}' found inside page_routes. "
                    "API routes and page routes must be separated."
                )
    elif isinstance(page_routes, list):
        for route in page_routes:
            path = route if isinstance(route, str) else route.get("path", "")
            if str(path).startswith("/api/"):
                violations.append(
                    f"API route '{path}' found inside page_routes. "
                    "API routes and page routes must be separated."
                )

    return violations


def verify_blueprint(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """Run all blueprint verification checks.

    Returns a dict with 'passed', 'violations', and 'checks_run'.
    """
    violations: List[str] = []

    # F-7: Route purpose separation
    route_violations = check_route_purpose_separation(blueprint)
    violations.extend(route_violations)

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "checks_run": ["route_purpose_separation"],
    }
