"""
Deterministic Error Classification & Retry Strategy for Agent Delegations

Issue: #1161 (GAP-6)

Provides:
- Error classification: maps error strings to categories (transient, permanent_*, logic)
- Retry configs: max retries, delays, and backoff per category
- Retry prompt builder: injects error context into subordinate retry prompts
- Async wait: exponential backoff with jitter

Works with DelegationResult (#1160 GAP-1) for deterministic retry decisions
instead of LLM-based guessing.
"""
from __future__ import annotations

import asyncio
import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("agix.retry_strategy")

# ── ERROR PATTERNS ──
# Order matters: first match wins. More specific patterns go first.

_TRANSIENT_PATTERNS = [
    re.compile(r"(?:timed?\s*out|timeout)", re.IGNORECASE),
    re.compile(r"(?:429|too\s*many\s*requests|rate.?limit)", re.IGNORECASE),
    re.compile(r"(?:50[2-4]|bad\s*gateway|service\s*unavailable|gateway\s*timeout)", re.IGNORECASE),
    re.compile(r"(?:connection\s*refused|ECONNREFUSED)", re.IGNORECASE),
    re.compile(r"(?:dns\s*resolution\s*failed|ENOTFOUND)", re.IGNORECASE),
    re.compile(r"(?:network\s*error|socket\s*hang\s*up)", re.IGNORECASE),
]

# ISS-0: 401/403 are transient on proxy-based providers (OpenRouter).
# Retry a few times before giving up. Truly permanent auth errors
# (invalid key, permission denied) are classified separately below.
_TRANSIENT_AUTH_PATTERNS = [
    re.compile(r"(?:401|unauthorized)", re.IGNORECASE),
    re.compile(r"(?:403|forbidden)", re.IGNORECASE),
]

_PERMANENT_AUTH_PATTERNS = [
    re.compile(r"(?:invalid\s*api\s*key)", re.IGNORECASE),
    re.compile(r"(?:permission\s*denied)", re.IGNORECASE),
    # F-12 (RCA-EVAL-1): "User not found" is permanent, not transient.
    # OpenRouter returns 401 "User not found" for budget exhaustion or
    # deleted accounts. ITR-42: 139 wasted retries because this matched
    # the generic 401 pattern in _TRANSIENT_AUTH_PATTERNS. Adding here
    # ensures it's caught FIRST (permanent patterns are checked before
    # transient patterns in classify_error()).
    re.compile(r"(?:user\s*not\s*found)", re.IGNORECASE),
    re.compile(r"(?:account\s*not\s*found)", re.IGNORECASE),
    re.compile(r"(?:(?:user|account)\s*does\s*not\s*exist)", re.IGNORECASE),
]

_PERMANENT_LOOP_PATTERNS = [
    re.compile(r"(?:loop\s*detected|recursive\s*loop)", re.IGNORECASE),
    re.compile(r"(?:max\s*depth|depth\s*exceeded)", re.IGNORECASE),
    re.compile(r"(?:circuit\s*breaker)", re.IGNORECASE),
    re.compile(r"(?:iteration\s*limit|ITERATION_LIMIT)", re.IGNORECASE),
]

_PERMANENT_CONTEXT_PATTERNS = [
    re.compile(r"(?:context\s*overflow|token\s*limit)", re.IGNORECASE),
    re.compile(r"(?:maximum\s*context\s*length|too\s*many\s*tokens)", re.IGNORECASE),
]


@dataclass
class RetryConfig:
    """Retry configuration for a specific error category.
    
    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retry)
        initial_delay_seconds: Base delay before first retry (0 = immediate)
        backoff_multiplier: Each subsequent retry waits delay * multiplier^attempt
        max_delay_seconds: Cap on computed delay to prevent absurd waits
    """
    max_retries: int = 2
    initial_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0


# ── CATEGORY → CONFIG MAP ──
_RETRY_CONFIGS = {
    "transient": RetryConfig(max_retries=2, initial_delay_seconds=2.0, backoff_multiplier=2.0),
    "transient_auth": RetryConfig(max_retries=3, initial_delay_seconds=2.0, backoff_multiplier=2.0),  # ISS-0: proxy 401/403 — retry a few times then fail
    "logic": RetryConfig(max_retries=1, initial_delay_seconds=0.0),  # Retry once, no delay
    "permanent_auth": RetryConfig(max_retries=0),
    "permanent_loop": RetryConfig(max_retries=0),
    "permanent_context": RetryConfig(max_retries=0),
}



# ── SS-11: RATE-LIMIT-SPECIFIC RETRY CONFIG ──
# Separate from the GAP-6 transient retry config because rate-limit (429)
# errors need more retries and guaranteed minimum delays.
# The old code used calculate_retry_delay() from rate_limiting.py with
# "full jitter" (random.uniform(0, ...)) which allowed near-zero delays.
RATE_LIMIT_RETRY_CONFIG = RetryConfig(
    max_retries=10,
    initial_delay_seconds=2.0,
    backoff_multiplier=2.0,
    max_delay_seconds=60.0,
)


def calculate_rate_limit_backoff(attempt: int) -> float:
    """Calculate exponential backoff delay for rate-limit (429) errors.

    Uses "additive jitter" strategy: delay = base + random(0, base/2).
    This guarantees a minimum delay of the FULL base at every attempt,
    unlike "full jitter" which can return near-zero, or "equal jitter"
    which only guarantees base/2.

    Formula:
        base_delay = min(initial * multiplier^attempt, max_delay)
        delay = base_delay + random.uniform(0, base_delay / 2)

    With initial=2s, multiplier=2:
        attempt 0: base=2s,  delay in [2.0, 3.0]   — minimum 2.0s
        attempt 1: base=4s,  delay in [4.0, 6.0]   — minimum 4.0s
        attempt 2: base=8s,  delay in [8.0, 12.0]  — minimum 8.0s
        attempt 3: base=16s, delay in [16.0, 24.0]  — minimum 16.0s
        ...
        capped at max_delay=60s base (delay up to 90s with jitter)

    Args:
        attempt: 0-indexed attempt number

    Returns:
        Delay in seconds (>= initial_delay_seconds * multiplier^attempt)
    """
    import random as _random
    config = RATE_LIMIT_RETRY_CONFIG
    base = min(
        config.initial_delay_seconds * (config.backoff_multiplier ** attempt),
        config.max_delay_seconds,
    )
    # Additive jitter: full base + up to half base extra
    jitter = _random.uniform(0, base / 2.0)
    delay = base + jitter
    # Floor at initial_delay to guarantee the advertised minimum
    return max(delay, config.initial_delay_seconds)


async def wait_for_rate_limit_retry(attempt: int) -> float:
    """Async wait with rate-limit-specific exponential backoff.

    SS-11: Replaces the inline calculate_retry_delay() call in
    _run_subordinate_with_coordination() which used full jitter and
    could produce near-zero delays.

    Args:
        attempt: 0-indexed attempt number

    Returns:
        The actual delay waited (in seconds)
    """
    delay = calculate_rate_limit_backoff(attempt)
    if delay > 0:
        logger.info(
            f"SS-11 rate-limit backoff: waiting {delay:.1f}s before attempt {attempt + 1}"
        )
        await asyncio.sleep(delay)
    return delay


def classify_error(error_string: str) -> str:
    """Classify an error string into a category.
    
    Returns one of:
        "transient" — Retryable with delay (timeouts, 5xx, rate limits)
        "transient_auth" — Retryable with limited ceiling (401, 403 proxy errors)
        "permanent_auth" — Never retry (invalid API key, permission denied)
        "permanent_loop" — Never retry (loop detected, max depth, circuit breaker)
        "permanent_context" — Never retry (context overflow, token limit)
        "logic" — Retry once without delay (bad args, unknown errors)
    """
    # Check permanent patterns first (truly unrecoverable)
    for pattern in _PERMANENT_AUTH_PATTERNS:
        if pattern.search(error_string):
            return "permanent_auth"

    for pattern in _PERMANENT_LOOP_PATTERNS:
        if pattern.search(error_string):
            return "permanent_loop"

    for pattern in _PERMANENT_CONTEXT_PATTERNS:
        if pattern.search(error_string):
            return "permanent_context"

    for pattern in _TRANSIENT_PATTERNS:
        if pattern.search(error_string):
            return "transient"

    # ISS-0: 401/403 are transient on proxy providers — retry a few times
    for pattern in _TRANSIENT_AUTH_PATTERNS:
        if pattern.search(error_string):
            return "transient_auth"

    # Default: assume logic error (unknown category gets 1 retry)
    return "logic"


def get_retry_config(error_string: str) -> RetryConfig:
    """Get the RetryConfig for a given error string.
    
    Classifies the error first, then returns the appropriate config.
    """
    category = classify_error(error_string)
    return _RETRY_CONFIGS.get(category, _RETRY_CONFIGS["logic"])


def should_retry(error_string: str, attempt: int) -> bool:
    """Determine if a retry should be attempted.
    
    Args:
        error_string: The error message to classify
        attempt: Current retry attempt number (0-indexed, so attempt=0 means first retry)
    
    Returns:
        True if the error is retryable and we haven't exceeded max retries.
    """
    config = get_retry_config(error_string)
    return attempt < config.max_retries


def build_retry_prompt(
    original_prompt: str,
    error_summary: str,
    error_context: str = "",
) -> str:
    """Build a retry prompt with error context injected.
    
    The retry prompt wraps the original task with information about what went
    wrong, so the subordinate can take a DIFFERENT strategy on retry.
    
    Args:
        original_prompt: The original delegation message
        error_summary: Short description of the error (truncated to 500 chars)
        error_context: Optional additional context (truncated to 300 chars)
    
    Returns:
        New prompt string with error context prepended.
    """
    # Truncate to prevent context bloat
    error_summary = error_summary[:500]
    if error_context:
        error_context = error_context[:300]

    parts = [
        "## ⚠️ Previous Attempt Failed — RETRY WITH DIFFERENT STRATEGY",
        "",
        f"**Error:** {error_summary}",
    ]

    if error_context:
        parts.append(f"**Context:** {error_context}")

    parts.extend([
        "",
        "You MUST use a DIFFERENT strategy than the previous attempt.",
        "The same approach will likely produce the same error.",
        "",
        "---",
        "",
        "## Original Task",
        original_prompt,
    ])

    return "\n".join(parts)


async def wait_before_retry(error_string: str, attempt: int) -> None:
    """Async wait with exponential backoff before retrying.
    
    Logic errors have 0 delay. Transient errors use exponential backoff
    with the formula: min(initial * multiplier^attempt, max_delay).
    
    Args:
        error_string: The error message (used to look up config)
        attempt: Current attempt number (0-indexed)
    """
    config = get_retry_config(error_string)
    if config.initial_delay_seconds <= 0:
        return

    delay = min(
        config.initial_delay_seconds * (config.backoff_multiplier ** attempt),
        config.max_delay_seconds,
    )

    if delay > 0:
        logger.info(f"Retry strategy: waiting {delay:.1f}s before attempt {attempt + 1}")
        await asyncio.sleep(delay)
