"""
Post-Serve Server Health Gate (RCA-233, U-1).

Verifies that the dev server is actually serving healthy content AFTER
the agent starts `next dev` / `npm run dev`. This catches fatal errors
hidden behind HTTP 200 responses (MODULE_NOT_FOUND, statusCode:500, etc.)

Integrates with:
- is_server_error_content() (Layer 1 body detection, route_reachability.py)
- evidence_persistence.py (persists health results to disk)
- orchestrator_gate_integration_checks.py (registered at order 9.0)

Usage in orchestrator:
    from python.helpers.server_health import check_server_health

    @register_check(9.0, "Server health", critical=True,
                    requires=["Dev server started"])
    def _check_server_health(ctx):
        result = check_server_health(port, ctx.project_dir)
        if not result["healthy"]:
            return ctx.block(f"Server unhealthy: {result['errors']}")
        return None
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.helpers.validators.route_reachability import is_server_error_content
from python.helpers.evidence_persistence import write_evidence

logger = logging.getLogger("agix.server_health")

# Default routes to check if no sitemap available
_DEFAULT_ROUTES = ["/"]


def check_server_health(
    port: int,
    project_dir: str,
    routes: Optional[List[str]] = None,
    _mock_curl_responses: Optional[Dict[str, Dict]] = None,
) -> Dict[str, Any]:
    """Check whether the dev server is serving healthy content.

    Performs curl requests to all specified routes (or just "/" by default)
    and runs Layer 1 body error detection on each response.

    Args:
        port: Dev server port number.
        project_dir: Project directory (for evidence persistence).
        routes: List of route paths to check (defaults to ["/"]).
        _mock_curl_responses: For testing — dict of route -> response data.
            Each response is {"status": int, "body": str, "error"?: str}.

    Returns:
        dict with:
            healthy: bool — True if ALL routes pass
            routes_checked: int — number of routes checked
            errors: list[str] — error messages for failed routes
            timestamp: str — ISO timestamp of check
    """
    if routes is None:
        routes = list(_DEFAULT_ROUTES)

    errors: List[str] = []
    routes_checked = 0

    for route in routes:
        routes_checked += 1

        # Get response (mock or real)
        if _mock_curl_responses is not None:
            resp = _mock_curl_responses.get(route, {"status": 0, "body": "", "error": "No mock"})
        else:
            resp = _real_curl(port, route)

        # Check for connection failure
        if resp.get("error"):
            errors.append(f"Route {route}: {resp['error']}")
            continue

        status = resp.get("status", 0)
        if status == 0:
            errors.append(f"Route {route}: Connection failed (status 0)")
            continue

        # Check body for hidden errors (Layer 1)
        body = resp.get("body", "")
        error_pattern = is_server_error_content(body)
        if error_pattern:
            errors.append(
                f"Route {route}: HTTP {status} but body contains "
                f"fatal error — {error_pattern}"
            )

    result = {
        "healthy": len(errors) == 0,
        "routes_checked": routes_checked,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Persist to disk for cross-agent access
    try:
        write_evidence(project_dir, "health_evidence", result)
    except Exception as e:
        logger.warning(f"[HEALTH] Failed to persist evidence: {e}")

    return result


def _real_curl(port: int, route: str) -> Dict[str, Any]:
    """Execute a real curl request against the dev server.

    Args:
        port: Server port.
        route: Route path.

    Returns:
        dict with status, body, and optional error.
    """
    import subprocess

    url = f"http://localhost:{port}{route}"
    try:
        proc = subprocess.run(
            ["curl", "-s", "-o", "-", "-w", "\n%{http_code}", "--max-time", "10", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = proc.stdout
        # Last line is the HTTP status code
        parts = output.rsplit("\n", 1)
        if len(parts) == 2:
            body = parts[0]
            try:
                status = int(parts[1].strip())
            except ValueError:
                status = 0
        else:
            body = output
            status = 0

        return {"status": status, "body": body}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return {"status": 0, "body": "", "error": str(e)}
