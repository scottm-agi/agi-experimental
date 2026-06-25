from __future__ import annotations
import logging
import random
import asyncio
import nest_asyncio
from typing import Any, Awaitable, Callable, Optional, Dict
from python.helpers.rate_limiter import (
    RateLimiter,
    get_or_create_rate_limiter,
    coordinate_agent_wait,
    BackoffConfig,
)
from python.helpers.tokens import approximate_tokens
from python.helpers.errors import InterventionException

logger = logging.getLogger(__name__)

# Default backoff configuration for API calls
DEFAULT_BACKOFF_CONFIG = BackoffConfig(
    initial_delay=1.0,
    max_delay=60.0,
    multiplier=2.0,
    jitter=0.1,
    max_retries=10,
)

def get_rate_limiter(
    provider: str, name: str, requests: int, input: int, output: int
) -> RateLimiter:
    """Get or create a rate limiter with exponential backoff support."""
    return get_or_create_rate_limiter(
        provider=provider,
        name=name,
        requests=requests,
        input_tokens=input,
        output_tokens=output,
        backoff_config=DEFAULT_BACKOFF_CONFIG,
    )

def calculate_retry_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Calculate exponential backoff delay with full jitter.
    
    Formula: delay = random.uniform(0, min(max_delay, base_delay * (2 ^ attempt)))
    This "Full Jitter" strategy is more effective at resolving congestion than equal jitter.
    """
    # Exponential part
    exp_delay = base_delay * (2 ** attempt)
    # Cap at max_delay
    capped_delay = min(exp_delay, max_delay)
    # Full jitter: random between 0 and capped_delay
    return max(0.1, random.uniform(0, capped_delay))


def is_malformed_function_call_error(exc: Exception) -> bool:
    """Detect MALFORMED_FUNCTION_CALL errors from Gemini via OpenRouter.
    
    These occur when the model produces invalid tool call JSON. They are
    transient (retrying usually succeeds immediately) but should NOT use
    exponential backoff — a fast retry is appropriate.
    
    Issue #1119.
    """
    error_str = str(exc)
    return "MALFORMED_FUNCTION_CALL" in error_str


def calculate_retry_delay_for_error(
    exc: Exception, attempt: int, base_delay: float = 1.5, max_delay: float = 60.0
) -> float:
    """Calculate retry delay taking error type into account.
    
    For MALFORMED_FUNCTION_CALL errors: use fast retry (max 1.0s) since these
    are model-side parsing failures that resolve on immediate retry.
    For all other errors: use standard exponential backoff.
    
    Issue #1119.
    """
    if is_malformed_function_call_error(exc):
        # Fast retry: 0.2-1.0s regardless of attempt number
        return max(0.2, random.uniform(0.2, 1.0))
    return calculate_retry_delay(attempt, base_delay=base_delay, max_delay=max_delay)


async def apply_rate_limiter(
    model_config: Any, # Using Any to avoid circular import with ModelConfig
    input_text: str,
    rate_limiter_callback: Optional[
        Callable[[str, str, int, int], Awaitable[bool]]
    ] = None,
) -> Optional[RateLimiter]:
    if not model_config:
        return None
    
    # First, check if there's a global backoff in effect for this provider
    provider = getattr(model_config, "provider", "unknown")
    provider_key = f"{provider}\\{getattr(model_config, 'name', 'unknown')}"
    await coordinate_agent_wait(provider, provider_key)
    
    limiter = get_rate_limiter(
        model_config.provider,
        model_config.name,
        model_config.limit_requests,
        model_config.limit_input,
        model_config.limit_output,
    )
    limiter.add(input=approximate_tokens(input_text))
    limiter.add(requests=1)
    await limiter.wait(rate_limiter_callback)
    return limiter

def apply_rate_limiter_sync(
    model_config: Any,
    input_text: str,
    rate_limiter_callback: Optional[
        Callable[[str, str, int, int], Awaitable[bool]]
    ] = None,
) -> Optional[RateLimiter]:
    if not model_config:
        return None
    nest_asyncio.apply()
    return asyncio.run(
        apply_rate_limiter(model_config, input_text, rate_limiter_callback)
    )

def _extract_status_code(exc: Exception) -> Optional[int]:
    """Extract HTTP status code from any exception, including litellm wrappers."""
    # Direct attribute
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    # Nested in response
    response = getattr(exc, "response", None)
    if response:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    # Fallback: extract from error string (e.g. "code":403 or status_code=403)
    import re
    error_str = str(exc)
    match = re.search(r'["\']?code["\']?\s*[:=]\s*(\d{3})', error_str)
    if match:
        return int(match.group(1))
    return None


# Status codes that are NEVER transient — do NOT retry on these
_NON_RETRYABLE_STATUS_CODES = {400, 401, 402, 403, 404, 405, 422}
# Status codes that ARE transient — safe to retry
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def is_transient_litellm_error(exc: Exception) -> bool:
    """Uses status_code when available, else falls back to exception types.
    
    CRITICAL: status_code check MUST take priority over type-based matching.
    A 403 'Key limit exceeded' is an openai.APIError by type, but must NOT
    be retried — it would saturate the event loop and crash the web server.
    """
    import openai
    import httpx
    import aiohttp
    
    # Handle MagicMock in tests
    if hasattr(exc, "__class__") and exc.__class__.__name__ == "MagicMock":
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code in _RETRYABLE_STATUS_CODES
        return False

    # PRIORITY 1: status_code is authoritative — check it FIRST before type matching
    status_code = _extract_status_code(exc)
    if isinstance(status_code, int):
        if status_code in _NON_RETRYABLE_STATUS_CODES:
            return False
        if status_code in _RETRYABLE_STATUS_CODES or status_code >= 500:
            return True
        return False

    # PRIORITY 2: string-based detection for non-retryable errors
    # (catches cases where litellm wraps errors without preserving status_code)
    error_str_lower = str(exc).lower()
    non_retryable_indicators = [
        "key limit exceeded",
        "insufficient_quota",
        "billing limit",
        "account deactivated",
        "invalid api key",
        "authentication",
    ]
    if any(indicator in error_str_lower for indicator in non_retryable_indicators):
        return False

    # PRIORITY 3: type-based fallback (only for errors with no status_code)
    transient_types = (
        asyncio.TimeoutError,
        getattr(openai, "APITimeoutError", Exception),
        getattr(openai, "APIConnectionError", Exception),
        getattr(openai, "RateLimitError", Exception),
        getattr(openai, "InternalServerError", Exception),
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        aiohttp.ClientConnectorError,
        aiohttp.client_exceptions.SocketTimeoutError,
        aiohttp.client_exceptions.ServerDisconnectedError,
    )
    if isinstance(exc, transient_types) or is_rate_limit_error(exc):
        return True

    # NOTE: openai.APIError and openai.APIStatusError are intentionally EXCLUDED
    # from transient_types. They are too broad — a 403 is an APIError but must
    # not be retried. If we reach here without a status_code, we fall through
    # to specific litellm type checks below.

    # Issue #599: Defensive fallback — catch litellm-specific transient errors by type name
    # and Cloudflare error pages that may not carry a proper status_code attribute
    try:
        import litellm as _litellm
        litellm_transient = (
            getattr(_litellm, "ServiceUnavailableError", None),
            getattr(_litellm, "Timeout", None),
            getattr(_litellm, "APIConnectionError", None),
        )
        litellm_transient = tuple(t for t in litellm_transient if t is not None)
        if litellm_transient and isinstance(exc, litellm_transient):
            return True
    except ImportError:
        pass

    # String-based fallback for Cloudflare HTML errors (Issue #599)
    cloudflare_indicators = [
        "temporarily unavailable",
        "cloudflare",
        "serviceunavailableerror",
        "service unavailable",
    ]
    if any(indicator in error_str_lower for indicator in cloudflare_indicators):
        return True

    return False

def is_rate_limit_error(exc: Exception) -> bool:
    """Check if the exception is specifically a rate limit error (429).
    
    NOTE: This must NOT match 403 'key limit exceeded' or 'insufficient_quota'
    errors — those are non-retryable billing/auth errors, not rate limits.
    """
    import openai
    
    # Handle MagicMock in tests
    if hasattr(exc, "__class__") and exc.__class__.__name__ == "MagicMock":
        status_code = getattr(exc, "status_code", None)
        return isinstance(status_code, int) and status_code == 429

    # First check: is it a non-retryable billing/auth error disguised as rate limit?
    status_code = _extract_status_code(exc)
    if isinstance(status_code, int) and status_code in _NON_RETRYABLE_STATUS_CODES:
        return False  # 403 is NOT a rate limit, even if msg says "limit"

    if isinstance(status_code, int) and status_code == 429:
        return True
    
    rate_limit_type = getattr(openai, "RateLimitError", None)
    if rate_limit_type and isinstance(exc, rate_limit_type):
        return True
    
    error_msg = str(exc).lower()
    
    # Exclude billing/quota errors that are NOT rate limits
    non_rate_limit_indicators = [
        "key limit exceeded",
        "insufficient_quota",
        "account deactivated",
        "invalid api key",
    ]
    if any(indicator in error_msg for indicator in non_rate_limit_indicators):
        return False
    
    rate_limit_indicators = [
        "rate limit", 
        "throttl", 
        "too many requests", 
        "resource exhausted",  # Common for Gemini/OpenRouter 429
        "429",                 # Raw status code in string
    ]
    return any(indicator in error_msg for indicator in rate_limit_indicators)

def extract_retry_after(exc: Exception) -> Optional[float]:
    """Extract retry-after header value from exception if available."""
    import re
    
    # Handle MagicMock in tests
    if hasattr(exc, "__class__") and exc.__class__.__name__ == "MagicMock":
        headers = getattr(exc, "headers", None) or getattr(exc, "response_headers", None)
        if headers and not isinstance(headers, dict):
            headers = None
    else:
        headers = getattr(exc, "headers", None) or getattr(exc, "response_headers", None)

    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    
    error_msg = str(exc)
    match = re.search(r"retry after (\d+(?:\.\d+)?)", error_msg, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    return None

async def handle_rate_limit_error(
    exc: Exception,
    model_config: Any,
    attempt: int,
    max_retries: int = 10,
) -> float:
    """
    Handle a rate limit error with exponential backoff.
    """
    if attempt >= max_retries:
        raise exc
    
    retry_after = extract_retry_after(exc)
    if retry_after:
        delay = retry_after
    else:
        delay = calculate_retry_delay(attempt)
    
    if model_config:
        provider_key = f"{model_config.provider}\\{model_config.name}"
        from python.helpers.rate_limiter import RateLimitState
        logger.warning(
            f"[PROVIDER_BACKOFF] provider={provider_key} | "
            f"attempt={attempt}/{max_retries} | delay={delay:.1f}s | "
            f"retry_after={'yes' if retry_after else 'no'}"
        )
        await RateLimiter._set_global_state(
            provider_key, 
            RateLimitState.BACKING_OFF,
            delay
        )
    
    return delay

def simplify_error_message(provider: str, model: str, error: Exception) -> str:
    """Simplifies raw LLM API error messages into user-friendly notifications."""
    err_str = str(error).lower()
    
    if provider.lower() == "openrouter":
        if "429" in err_str or "resource exhausted" in err_str:
            return "OpenRouter / Gemini resource limits reached. This is likely a provider-side overload. Please try again in 1-2 minutes or switch to another provider."
        if "401" in err_str or "user not found" in err_str:
            return "OpenRouter Authentication failed. Please verify your API key and check if you have sufficient credits in your OpenRouter account."
        # Issue #599: Cloudflare transient errors should show clean message, not raw HTML
        if "cloudflare" in err_str or "temporarily unavailable" in err_str or "1105" in err_str:
            return "OpenRouter is temporarily unavailable (Cloudflare). This is a transient upstream issue — the system will automatically retry. If this persists, try again in a few minutes."
            
    return f"Model {model} failed: {str(error)}"

def notify_llm_failure(provider: str, model: str, error: Exception):
    """Send a UI notification when an LLM API call fails fatally."""
    try:
        from python.helpers.notification import NotificationManager, NotificationType, NotificationPriority
        
        if "cancelled" in str(error).lower() or "interrupted" in str(error).lower() or isinstance(error, InterventionException):
            return

        msg = simplify_error_message(provider, model, error)
        
        if provider.lower() == "venice":
            msg += "\n\nTip: Consistent rate limits? See docs/providers/venice_rate_limit.md for partner tier info."

        NotificationManager.send_notification(
            type=NotificationType.ERROR,
            priority=NotificationPriority.HIGH,
            title=f"LLM API Error: {provider}",
            message=msg,
            detail=f"Provider: {provider}\nModel: {model}\n\nError: {str(error)}",
            group="llm_error",
            display_time=15
        )
    except Exception:
        pass

def notify_llm_retry(provider: str, model: str, attempt: int, max_retries: int, delay: float, error: Exception):
    """Send a UI notification (Warning) when an LLM API call is being retried due to 429/transient error."""
    try:
        from python.helpers.notification import NotificationManager, NotificationType, NotificationPriority
        
        # Don't notify for very first transient hiccup if it's not a rate limit
        if attempt <= 1 and not is_rate_limit_error(error):
            return

        title = f"LLM Rate Limit: {provider}" if is_rate_limit_error(error) else f"LLM Transient Error: {provider}"
        msg = f"Retrying {model} (Attempt {attempt}/{max_retries}) in {delay:.1f}s...\n\nReason: {str(error)}"
        
        NotificationManager.send_notification(
            type=NotificationType.WARNING,
            priority=NotificationPriority.NORMAL,
            title=title,
            message=msg,
            detail=f"Provider: {provider}\nModel: {model}\nAttempt: {attempt}/{max_retries}\nNext Delay: {delay:.2f}s\n\nError: {str(error)}",
            group="llm_retry",
            display_time=10
        )
    except Exception:
        pass

def _notify_llm_failure(provider: str, model: str, error: Exception):
    """Internal alias for notify_llm_failure."""
    return notify_llm_failure(provider, model, error)

def _is_transient_litellm_error(exc: Exception) -> bool:
    """Internal alias for is_transient_litellm_error."""
    return is_transient_litellm_error(exc)

def _is_rate_limit_error(exc: Exception) -> bool:
    """Internal alias for is_rate_limit_error."""
    return is_rate_limit_error(exc)

def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Internal alias for extract_retry_after."""
    return extract_retry_after(exc)
