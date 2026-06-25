"""
Auth Error Escape Hatch — ITR-29b

Detects when an agent is stuck retrying unfixable API authentication errors
(401, 403, invalid API key) and injects a hard "SKIP and move on" directive.

This closes the gap between:
- tool_failure_tracker: classifies 401 as permanent but only blocks the TOOL
- tool_failure_reset: gives generic "change strategy" which agent interprets
  as "try a different approach to fix the key" instead of "skip this"
- delegation_topic_dedup: only catches CROSS-delegation loops, not intra-agent

This module catches the INTRA-AGENT loop: code agent spending 20+ iterations
trying to fix an unfixable API key within a single delegation.

Wired into: message_loop_start (via _06_tool_failure_reset.py enhancement)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("agix.auth_error_detector")

# ── Auth error patterns ──
# Layer 1: HTTP-level auth errors (original patterns)
# Layer 2: ENV-VAR-ABSENCE patterns (RCA-451) — the agent sees these upstream
#   of the actual 401. The tool output contains "API_KEY is undefined" or
#   ".env not found", not "HTTP 401". These are the root-cause indicators.
_AUTH_PATTERNS = [
    # HTTP-level auth errors
    re.compile(r"\b401\b", re.IGNORECASE),
    re.compile(r"\b403\b.*(?:forbidden|denied)", re.IGNORECASE),
    re.compile(r"(?:unauthorized|forbidden)\b", re.IGNORECASE),
    re.compile(r"invalid\s*(?:api[_ ]?key|credentials?|token|auth)", re.IGNORECASE),
    re.compile(r"authentication\s*(?:required|failed|error)", re.IGNORECASE),
    re.compile(r"(?:api[_ ]?key|credentials?)\s*(?:invalid|expired|revoked|wrong|missing)", re.IGNORECASE),
    # RCA-451: ENV-VAR-ABSENCE patterns — upstream cause of auth failures
    # Catches: "PERPLEXITY_API_KEY is undefined", "token not set", etc.
    re.compile(r"(?:api[_ ]?key|secret|token|credentials?)\s*(?:is\s*)?(?:undefined|not\s*(?:set|found|configured|defined))", re.IGNORECASE),
    # Catches: "Missing environment variable STRIPE_SECRET_KEY"
    re.compile(r"missing\s*(?:environment|env)\s*(?:variable|var)", re.IGNORECASE),
    # Catches: "process.env.OPENAI_API_KEY is undefined"
    re.compile(r"\bprocess\.env\.\w+.*(?:undefined|null|not\s*set)", re.IGNORECASE),
    # Catches: ".env file not found", ".env not found"
    re.compile(r"\.env\s*(?:file)?\s*not\s*found", re.IGNORECASE),
    # Catches ALL_CAPS env var names containing KEY/SECRET/TOKEN that are undefined/not set
    # e.g. "STRIPE_SECRET_KEY is undefined", "NEXT_PUBLIC_STRIPE_KEY is undefined"
    re.compile(r"\b[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN)\b\s*(?:is\s*)?(?:undefined|not\s*(?:set|found|configured|defined)|missing)", re.IGNORECASE),
]

# ── Dynamic service extraction ──
# M-HC-8 / Systems Audit: Replaced hardcoded 11-service list with dynamic extraction.
# Extracts service name from env var patterns (e.g., STRIPE_SECRET_KEY → stripe,
# OPENAI_API_KEY → openai). Falls back to "unknown service" if no pattern matches.
_ENV_VAR_SERVICE_PATTERN = re.compile(
    r"\b([A-Z][A-Z0-9]*?)_(?:API[_ ]?KEY|SECRET[_ ]?KEY|TOKEN|CREDENTIALS?)\b"
)


def is_auth_error(text: str | None) -> bool:
    """Check if text contains an API authentication error pattern.

    Args:
        text: Tool output or error message to check.

    Returns:
        True if the text matches known auth error patterns.
    """
    if not text:
        return False
    return any(p.search(text) for p in _AUTH_PATTERNS)


def _extract_service(text: str) -> str:
    """Extract the service name from an error message.
    
    Dynamically extracts from env var naming convention
    (e.g., STRIPE_SECRET_KEY → stripe, NEXT_PUBLIC_OPENAI_KEY → openai).
    No hardcoded service list needed.
    """
    # Try to extract from env var pattern
    match = _ENV_VAR_SERVICE_PATTERN.search(text)
    if match:
        # Get the prefix, strip common prefixes like NEXT_PUBLIC_
        service = match.group(1).lower()
        for prefix in ("next_public_", "react_app_", "vite_"):
            if service.startswith(prefix):
                service = service[len(prefix):]
        return service or "unknown service"
    # Fallback: extract capitalized word before "API", "service", or "key"
    _plain_service_pattern = re.compile(
        r"\b([A-Za-z][A-Za-z0-9]+)\s+(?:API|service|key)\b", re.IGNORECASE
    )
    m2 = _plain_service_pattern.search(text)
    if m2:
        word = m2.group(1).lower()
        # Skip generic words
        if word not in {"invalid", "the", "an", "api", "secret", "auth", "missing"}:
            return word
    return "unknown service"


class AuthErrorTracker:
    """Track consecutive auth errors to detect stuck agents.

    When an agent encounters N consecutive auth/API key errors,
    produces a hard "SKIP and move on" directive.

    Args:
        threshold: Number of consecutive auth errors before triggering.
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._count: int = 0
        self._errors: list[str] = []

    def record(self, error_text: str) -> Optional[str]:
        """Record a tool error and return escape directive if auth loop detected.

        Args:
            error_text: The error message from a tool execution.

        Returns:
            None if no auth loop, escape directive string if threshold reached.
        """
        if is_auth_error(error_text):
            self._count += 1
            self._errors.append(error_text[:200])
        else:
            # Non-auth error breaks the streak
            self._count = 0
            self._errors.clear()
            return None

        if self._count >= self.threshold:
            service = _extract_service(" ".join(self._errors))
            return self._build_escape_directive(service)

        return None

    def reset(self) -> None:
        """Reset the tracker (called on successful tool execution)."""
        self._count = 0
        self._errors.clear()

    def _build_escape_directive(self, service: str) -> str:
        """Build the escape hatch directive message."""
        return (
            f"## 🛑 AUTH ERROR ESCAPE HATCH — {self._count} consecutive auth failures\n\n"
            f"**Service**: {service}\n"
            f"**Pattern**: {self._count} consecutive API authentication errors "
            f"(401/403/invalid key)\n\n"
            f"This is an **environmental constraint**, NOT a code bug. "
            f"The API key is invalid, missing, or a test placeholder. "
            f"You CANNOT fix this by editing .env files or searching for keys.\n\n"
            f"### ⛔ MANDATORY ACTION — Do this NOW:\n"
            f"1. **STOP** trying to fix the API key — it is BLOCKED\n"
            f"2. **Mark** this feature/requirement as `BLOCKED: invalid API key for {service}`\n"
            f"3. **Skip** to your next task item immediately\n"
            f"4. **Move on** — do NOT return to this topic\n\n"
            f"The orchestrator will report this as a PARTIAL completion. "
            f"This is the correct behavior for unfixable environmental constraints.\n"
        )
