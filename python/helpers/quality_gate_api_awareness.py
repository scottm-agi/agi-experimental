"""
Quality Gate API Awareness — Differentiate frontend vs API route health.

Root cause (5-Why from MainStreet destruction):
  1. Quality gate curl-checks ALL routes the same way
  2. API routes return 500 (no seed data) → gate treats as build failure
  3. Gate rejects delivery → orchestrator spawns debug agent
  4. Debug agent deletes .next → build actually breaks
  5. Death spiral until iteration limit

Fix: Frontend routes must return 200 (or redirect). API routes returning
400-599 are ACCEPTABLE — the route handler exists and responds, just with
no data or validation errors. Only connection refused / timeout is a
true failure for any route.

Usage:
    from python.helpers.quality_gate_api_awareness import (
        classify_route_health, RouteHealthStatus, is_api_route
    )
"""

from __future__ import annotations

import enum
import re
import logging
from typing import Optional

logger = logging.getLogger("agix.quality_gate_api_awareness")


class RouteHealthStatus(enum.Enum):
    """Health classification for a route check result.

    HEALTHY:    Route responds correctly (200 for frontend, 200 for API)
    ACCEPTABLE: Route responds but with expected non-success codes
                (400/404/500 on API routes — means the handler EXISTS)
    WARNING:    Route has issues but not blocking (500 on API)
    FAILURE:    Route is truly broken (connection refused, timeout,
                404/500 on frontend routes)
    """
    HEALTHY = "healthy"
    ACCEPTABLE = "acceptable"
    WARNING = "warning"
    FAILURE = "failure"

    def is_passing(self) -> bool:
        """Returns True if this status should NOT block delivery."""
        return self in (
            RouteHealthStatus.HEALTHY,
            RouteHealthStatus.ACCEPTABLE,
            RouteHealthStatus.WARNING,
        )


# Pattern: /api/ followed by anything (but NOT /api-docs, /api-reference, etc.)
_API_ROUTE_PATTERN = re.compile(r"^/api(?:/|$)")


def is_api_route(route_path: str) -> bool:
    """Determine if a route path is an API endpoint.

    API routes start with /api/ (not /api-docs, /api-reference, etc.)
    This includes:
      - /api/reviews
      - /api/health
      - /api/v1/users/123
      - /api/trpc/review.list

    This excludes:
      - /api-docs (a page, not an API)
      - /api-reference
      - /about

    Args:
        route_path: The URL path (e.g., "/api/reviews", "/about")

    Returns:
        True if the route is an API endpoint.
    """
    return bool(_API_ROUTE_PATTERN.match(route_path))


def classify_route_health(
    route_path: str,
    status_code: Optional[int],
) -> RouteHealthStatus:
    """Classify the health of a route based on its HTTP status code.

    Different rules for frontend vs API routes:

    Frontend routes (/, /about, /dashboard):
      - 200-399: HEALTHY (including redirects)
      - 400+: FAILURE
      - None (connection refused): FAILURE

    API routes (/api/*):
      - 200-399: HEALTHY
      - 400-499: ACCEPTABLE (route exists, no data / auth required)
      - 500-599: WARNING (server error, but route exists — not a build failure)
      - None (connection refused): FAILURE

    Args:
        route_path: The URL path being checked.
        status_code: HTTP status code, or None for connection refused/timeout.

    Returns:
        RouteHealthStatus classification.
    """
    # Connection refused / timeout is always a failure
    if status_code is None:
        return RouteHealthStatus.FAILURE

    api = is_api_route(route_path)

    if api:
        # API route classification
        if 200 <= status_code < 400:
            return RouteHealthStatus.HEALTHY
        elif 400 <= status_code < 500:
            # 400/401/403/404 = route exists, handler works, just no data or auth
            logger.info(
                f"[API GATE] {route_path} returned {status_code} — "
                f"ACCEPTABLE (route handler exists)"
            )
            return RouteHealthStatus.ACCEPTABLE
        elif 500 <= status_code < 600:
            # 500/503 = server error, but route EXISTS — not a build failure
            logger.warning(
                f"[API GATE] {route_path} returned {status_code} — "
                f"WARNING (server error, but not a build failure)"
            )
            return RouteHealthStatus.WARNING
        else:
            return RouteHealthStatus.FAILURE
    else:
        # Frontend route classification
        if 200 <= status_code < 400:
            return RouteHealthStatus.HEALTHY
        else:
            logger.warning(
                f"[FRONTEND GATE] {route_path} returned {status_code} — FAILURE"
            )
            return RouteHealthStatus.FAILURE
