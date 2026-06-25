"""
LIT Layer 2 Content Examination — Response Body Checker.

F-10 Fix: LIT (Live Integration Test) only checks HTTP status codes (Layer 1).
An endpoint returning 200 with an error body passes LIT but fails E2E.

This module provides Layer 2 content examination:
1. DETERMINISTIC Layer 1 check: regex-based error pattern detection
2. OPTIONAL Layer 2 semantic check: placeholder for future LLM examination
3. Qualified verdicts: 'Layer 1 PASS, Layer 2 PENDING' when only status was checked

Architecture follows the 2-Layer Detection Architecture mandate:
- Layer 1 (fast, deterministic): regex patterns for error indicators
- Layer 2 (semantic, optional): LLM content review (future integration)
"""

import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("agix.lit_layer2")

# ─── Error Indicator Patterns ───────────────────────────────────────────

# Patterns that indicate error responses in the body content.
# Applied to the first 1000 characters for performance.
_ERROR_PATTERNS = [
    re.compile(r'Internal Server Error', re.IGNORECASE),
    re.compile(r'\bError:\s', re.IGNORECASE),
    re.compile(r'\bNot Found\b', re.IGNORECASE),
    re.compile(r'"error"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"error"\s*:\s*"', re.IGNORECASE),
    re.compile(r'\b500\b.*\b[Ss]erver\b'),
    re.compile(r'"status"\s*:\s*500'),
]

# Patterns for trivially invalid API responses
_TRIVIAL_INVALID_PATTERNS = [
    re.compile(r'^\s*undefined\s*$', re.IGNORECASE),
    re.compile(r'^\s*null\s*$', re.IGNORECASE),
]

# HTML detection (for API endpoints returning HTML = bad)
_HTML_INDICATOR = re.compile(r'<!DOCTYPE\s+html|<html[\s>]', re.IGNORECASE)

# Maximum characters to examine from response body
_MAX_EXAMINE_CHARS = 1000


def check_response_body(
    url: str,
    response_text: Optional[str],
    endpoint_type: str = "api",
) -> Dict[str, Any]:
    """Examine response body for error indicators (Layer 2 check).

    Performs deterministic pattern matching on the response body to detect
    error indicators that a simple HTTP status code check would miss.

    Args:
        url: The URL that was requested.
        response_text: The response body text. None or empty string
            indicates an empty/missing response.
        endpoint_type: Either 'api' (expects JSON) or 'page' (expects HTML).
            Determines which patterns are errors.

    Returns:
        Dict with keys:
            passed (bool): Whether the response body looks valid
            layer (int): Always 2 (this is a Layer 2 check)
            evidence (str): Description of what was found/checked
            confidence (float): 0.0-1.0 confidence in the verdict
    """
    # Handle None/empty responses
    if response_text is None or response_text == "":
        if endpoint_type == "api":
            return {
                "passed": False,
                "layer": 2,
                "evidence": "Empty/None response body on API endpoint",
                "confidence": 0.95,
            }
        else:
            return {
                "passed": False,
                "layer": 2,
                "evidence": "Empty/None response body on page endpoint",
                "confidence": 0.8,
            }

    # Truncate to first N chars for performance
    body = response_text[:_MAX_EXAMINE_CHARS]

    # ─── Check 1: Trivially invalid responses ───────────────────────
    for pattern in _TRIVIAL_INVALID_PATTERNS:
        if pattern.match(body):
            return {
                "passed": False,
                "layer": 2,
                "evidence": f"Trivially invalid response: '{body.strip()[:50]}'",
                "confidence": 0.98,
            }

    # ─── Check 2: Error pattern detection ───────────────────────────
    for pattern in _ERROR_PATTERNS:
        match = pattern.search(body)
        if match:
            return {
                "passed": False,
                "layer": 2,
                "evidence": f"Error indicator detected: '{match.group()[:80]}'",
                "confidence": 0.9,
            }

    # ─── Check 3: HTML in API responses ─────────────────────────────
    if endpoint_type == "api" and _HTML_INDICATOR.search(body):
        return {
            "passed": False,
            "layer": 2,
            "evidence": (
                "HTML content (<!DOCTYPE html>) returned for API endpoint — "
                "expected JSON/data response"
            ),
            "confidence": 0.92,
        }

    # ─── All checks passed ──────────────────────────────────────────
    return {
        "passed": True,
        "layer": 2,
        "evidence": f"No error indicators found in {len(body)} chars examined",
        "confidence": 0.85,
    }


def build_layer2_verdict(
    status_code: int,
    layer2_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a combined Layer 1 + Layer 2 verdict.

    Combines HTTP status code check (Layer 1) with response body check
    (Layer 2) into a qualified verdict.

    Args:
        status_code: HTTP response status code (Layer 1)
        layer2_result: Result from check_response_body(), or None if
            Layer 2 wasn't performed.

    Returns:
        Dict with keys:
            layer1_passed (bool): Whether status code indicates success
            layer2_checked (bool): Whether Layer 2 was performed
            layer2_passed (bool|None): Layer 2 result, None if not checked
            overall_passed (bool): Combined verdict
            verdict (str): Human-readable verdict string
    """
    layer1_passed = 200 <= status_code < 400

    if not layer1_passed:
        return {
            "layer1_passed": False,
            "layer2_checked": layer2_result is not None,
            "layer2_passed": layer2_result.get("passed") if layer2_result else None,
            "overall_passed": False,
            "verdict": f"Layer 1 FAIL (HTTP {status_code})",
        }

    if layer2_result is None:
        return {
            "layer1_passed": True,
            "layer2_checked": False,
            "layer2_passed": None,
            "overall_passed": True,  # Tentatively pass, but flag as incomplete
            "verdict": "Layer 1 PASS, Layer 2 PENDING",
        }

    layer2_passed = layer2_result.get("passed", True)
    return {
        "layer1_passed": True,
        "layer2_checked": True,
        "layer2_passed": layer2_passed,
        "overall_passed": layer2_passed,  # Layer 2 has veto power
        "verdict": (
            "Layer 1 PASS, Layer 2 PASS"
            if layer2_passed
            else f"Layer 1 PASS, Layer 2 FAIL — {layer2_result.get('evidence', 'unknown')}"
        ),
    }
