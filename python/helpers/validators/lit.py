"""
LIT (Logical Integration Testing) Validators

Validates that the agent created and executed a Logical Integration Test plan
covering 4 mandatory patterns:

1. API Route Testing — curl API routes with test data, verify response shape
2. Integration Smoke — verify env vars / external service wiring
3. Data Flow — end-to-end form→API→service→response pipeline
4. Error Paths — invalid input / missing config → proper error handling

The LIT plan (lit_plan.json) is auto-generated from verification_sitemap.json
during the TDD/architect phase, then executed via curl during verification.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.validators.lit")

# The 4 mandatory LIT test patterns
REQUIRED_PATTERNS = [
    "api_route_testing",
    "integration_smoke",
    "data_flow",
    "error_paths",
]

# Keywords that indicate LIT execution evidence in delegation responses
LIT_EVIDENCE_KEYWORDS = [
    "lit test",
    "lit plan",
    "lit_plan",
    "logical integration test",
    "api_route_testing",
    "integration_smoke",
    "data_flow",
    "error_paths",
    "integration test passed",
    "integration test failed",
    "patterns passed",
    "patterns failed",
    "curl -x post",
    "curl -x get",
    "returned 200",
    "returned 400",
    "returned 500",
    "returned {",
    'returned {"',
    "status: 200",
    "status: 400",
    "status 200",
    "status 400",
    "error_paths verified",
    "api route testing",
]


def check_lit_plan_exists(project_dir: str) -> Dict[str, Any]:
    """Check whether lit_plan.json exists in the project root.

    Returns:
        dict with keys:
            exists (bool): Whether the file exists and is valid JSON
            plan (dict|None): Parsed plan data if exists
            test_count (int): Total number of test cases across all patterns
    """
    plan_path = os.path.join(project_dir, "lit_plan.json")
    if not os.path.isfile(plan_path):
        return {"exists": False, "plan": None, "test_count": 0}

    try:
        with open(plan_path) as f:
            plan = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[LIT] Failed to parse lit_plan.json: {e}")
        return {"exists": False, "plan": None, "test_count": 0}

    # Count total test cases across all patterns
    test_count = 0
    patterns = plan.get("test_patterns", {})
    for pattern_name, tests in patterns.items():
        if isinstance(tests, list):
            test_count += len(tests)

    return {"exists": True, "plan": plan, "test_count": test_count}


def validate_lit_plan_structure(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Validate that a LIT plan has all 4 required test pattern categories.

    Each pattern must exist AND contain at least one test case.

    Args:
        plan: Parsed lit_plan.json data.

    Returns:
        dict with keys:
            valid (bool): Whether all 4 patterns are present and non-empty
            missing_patterns (list[str]): Names of missing/empty patterns
    """
    patterns = plan.get("test_patterns", {})
    missing = []

    for required in REQUIRED_PATTERNS:
        tests = patterns.get(required)
        if not tests or (isinstance(tests, list) and len(tests) == 0):
            missing.append(required)

    return {"valid": len(missing) == 0, "missing_patterns": missing}


def detect_lit_execution_evidence(response_msg: str) -> bool:
    """Detect whether a delegation response contains LIT execution evidence.

    Looks for keywords indicating the agent actually ran LIT tests
    (curl commands, test results, pattern names, status codes).

    Args:
        response_msg: The text of a delegation response.

    Returns:
        True if LIT execution evidence is detected.
    """
    if not response_msg:
        return False

    msg_lower = response_msg.lower()

    # Check for LIT-specific keywords (need at least 2 matches for confidence)
    matches = sum(1 for kw in LIT_EVIDENCE_KEYWORDS if kw in msg_lower)
    return matches >= 2


def generate_lit_plan_from_sitemap(project_dir: str) -> Optional[Dict[str, Any]]:
    """Auto-generate a lit_plan.json from verification_sitemap.json.

    Reads the sitemap's api_routes to generate test cases for all 4 patterns.
    The agent is expected to fill in more specific test data, but this provides
    a skeleton with the right structure.

    Args:
        project_dir: Path to the project directory.

    Returns:
        A complete LIT plan dict, or None if no sitemap exists.
    """
    sitemap_path = os.path.join(project_dir, "verification_sitemap.json")
    if not os.path.isfile(sitemap_path):
        return None

    try:
        with open(sitemap_path) as f:
            sitemap = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    base_url = sitemap.get("base_url", "http://0.0.0.0:3000")
    api_routes = sitemap.get("api_routes", [])

    if not api_routes:
        return None

    # Pattern 1: API Route Testing — one test per API route
    api_tests = []
    for route in api_routes:
        path = route.get("path", "")
        method = route.get("method", "GET").upper()
        test = {
            "endpoint": path,
            "method": method,
            "expected_status": 200,
            "description": f"{method} {path} returns valid response",
        }
        if method == "POST":
            test["body"] = {"_placeholder": True}
            test["expected_shape"] = {"_note": "agent should define expected response keys"}
        api_tests.append(test)

    # Pattern 2: Integration Smoke — check for env vars referenced by the project
    smoke_tests = [
        {
            "check": "env_var_audit",
            "description": "Verify all required environment variables are set for external integrations",
        }
    ]

    # Pattern 3: Data Flow — POST routes get a data flow test
    data_flow_tests = []
    for route in api_routes:
        method = route.get("method", "GET").upper()
        if method == "POST":
            path = route.get("path", "")
            data_flow_tests.append({
                "endpoint": path,
                "method": "POST",
                "body": {"_placeholder": True},
                "verify_pipeline": "form → API → service → response",
                "description": f"Data flow test for {path}",
            })
    # Ensure at least one data flow test exists
    if not data_flow_tests:
        data_flow_tests.append({
            "endpoint": api_routes[0].get("path", "/api/health"),
            "method": api_routes[0].get("method", "GET"),
            "verify_pipeline": "request → handler → response",
            "description": "Basic request-response data flow",
        })

    # Pattern 4: Error Paths — POST routes get empty-body error tests
    error_tests = []
    for route in api_routes:
        method = route.get("method", "GET").upper()
        if method == "POST":
            path = route.get("path", "")
            error_tests.append({
                "endpoint": path,
                "method": "POST",
                "body": {},
                "expected_status": 400,
                "expected_error": "required",
                "description": f"Empty body to {path} returns 400 validation error",
            })
    # Ensure at least one error test exists
    if not error_tests:
        error_tests.append({
            "endpoint": api_routes[0].get("path", "/api/health"),
            "method": "GET",
            "body": None,
            "expected_status": 404,
            "description": "Invalid resource returns proper error",
        })

    plan = {
        "base_url": base_url,
        "generated_from": "verification_sitemap.json",
        "test_patterns": {
            "api_route_testing": api_tests,
            "integration_smoke": smoke_tests,
            "data_flow": data_flow_tests,
            "error_paths": error_tests,
        },
    }

    return plan
