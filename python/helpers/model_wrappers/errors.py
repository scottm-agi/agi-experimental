"""
Error classes and error handling utilities for model wrappers.
"""
from .rate_limiting import _notify_llm_failure

class ProviderConfigurationError(Exception):
    """Raised when a selected model provider is not usable in the current runtime."""

def is_bedrock_missing_dependency_error(exc: Exception, _depth: int = 0) -> bool:
    """Return True if *exc* (or one of its causes) is a Bedrock/boto3 issue."""
    if _depth > 3:
        return False

    raw_msg = getattr(exc, "message", None)
    msg = (raw_msg if isinstance(raw_msg, str) and raw_msg else str(exc)).lower()

    if "missing boto3" in msg and "bedrock" in msg:
        return True
    if "no module named 'botocore'" in msg and "bedrock" in msg:
        return True
    if "no module named 'boto3'" in msg and "bedrock" in msg:
        return True

    exc_type = type(exc).__name__.lower()
    if "modulenotfounderror" in exc_type and ("botocore" in msg or "boto3" in msg):
        return True
    if "apiconnectionerror" in exc_type and "boto3" in msg:
        return True
    if "apimissingdependency" in exc_type and ("botocore" in msg or "boto3" in msg):
        return True

    for inner in (getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if isinstance(inner, Exception) and is_bedrock_missing_dependency_error(inner, _depth + 1):
            return True

    return False