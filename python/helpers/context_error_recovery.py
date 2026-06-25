from __future__ import annotations
"""
Context Error Recovery Module

Provides automatic detection and recovery from context window overflow errors
across multiple LLM providers (OpenAI, Anthropic, Bedrock, Google).

Key Components:
- detect_context_error(): Detects if an exception is a context overflow error
- ContextRecoveryHandler: Handles automatic recovery with retry logic
- with_context_recovery(): Decorator for automatic recovery on LLM calls
- auto_snapshot_before_compression(): Deterministic pre-compression snapshot (zero-LLM)
- get_post_compression_nudge(): Post-compression nudge message for memory bank update

Usage:
    from python.helpers.context_error_recovery import with_context_recovery
    
    @with_context_recovery("chat_model")
    async def call_chat_model(agent, messages):
        return await llm.invoke(messages)
"""

import logging
import os
import re
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, TypeVar

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.context_recovery")


# =============================================================================
# Error Types
# =============================================================================

class ContextErrorType(Enum):
    """Types of context window errors."""
    TOKEN_LIMIT = "token_limit"
    CONTEXT_LENGTH = "context_length"
    INPUT_TOO_LONG = "input_too_long"
    UNKNOWN = "unknown"


# =============================================================================
# Error Patterns
# =============================================================================

# Provider-specific error patterns
# Each pattern is a tuple of (regex_pattern, error_type)
ERROR_PATTERNS: Dict[str, List[Tuple[str, ContextErrorType]]] = {
    "openai": [
        (r"context_length_exceeded", ContextErrorType.CONTEXT_LENGTH),
        (r"maximum context length", ContextErrorType.CONTEXT_LENGTH),
        (r"This model's maximum context length is \d+ tokens", ContextErrorType.TOKEN_LIMIT),
        (r"resulted in \d+ tokens", ContextErrorType.TOKEN_LIMIT),
    ],
    "anthropic": [
        (r"prompt is too long", ContextErrorType.INPUT_TOO_LONG),
        (r"context window exceeded", ContextErrorType.CONTEXT_LENGTH),
        (r"maximum.*tokens", ContextErrorType.TOKEN_LIMIT),
    ],
    "bedrock": [
        (r"Input is too long", ContextErrorType.INPUT_TOO_LONG),
        (r"ValidationException.*token", ContextErrorType.TOKEN_LIMIT),
        (r"input token count.*exceeds", ContextErrorType.TOKEN_LIMIT),
    ],
    "google": [
        (r"RESOURCE_EXHAUSTED", ContextErrorType.TOKEN_LIMIT),
        (r"context length", ContextErrorType.CONTEXT_LENGTH),
    ],
    "generic": [
        (r"token limit", ContextErrorType.TOKEN_LIMIT),
        (r"context.*exceeded", ContextErrorType.CONTEXT_LENGTH),
        (r"too many tokens", ContextErrorType.TOKEN_LIMIT),
        (r"input.*too long", ContextErrorType.INPUT_TOO_LONG),
    ],
}


# =============================================================================
# Error Detection
# =============================================================================

def detect_context_error(error: Exception) -> Optional[Tuple[ContextErrorType, str]]:
    """
    Detect if an exception is a context window error.
    
    Checks the error message against known patterns from multiple providers.
    
    Args:
        error: The exception to analyze
        
    Returns:
        Tuple of (error_type, provider) if context error detected, None otherwise
        
    Example:
        >>> error = Exception("context_length_exceeded: maximum context length is 128000")
        >>> result = detect_context_error(error)
        >>> result
        (ContextErrorType.CONTEXT_LENGTH, 'openai')
    """
    error_str = str(error)
    error_type_str = type(error).__name__
    
    # Check all providers in order (specific providers first, generic last)
    provider_order = ["openai", "anthropic", "bedrock", "google", "generic"]
    
    for provider in provider_order:
        patterns = ERROR_PATTERNS.get(provider, [])
        for pattern, error_type in patterns:
            # Check error message
            if re.search(pattern, error_str, re.IGNORECASE):
                return (error_type, provider)
            # Check error type name
            if re.search(pattern, error_type_str, re.IGNORECASE):
                return (error_type, provider)
    
    return None


# =============================================================================
# Pre-Compression Auto-Snapshot (Iteration 159)
# =============================================================================

# File path patterns: absolute (/path/to/file.ext) and relative (dir/file.ext)
_FILE_PATH_RE = re.compile(
    r'(?:^|[\s"\':,\(])('                    # start boundary
    r'(?:/[\w.\-]+)+\.\w+'                    # absolute: /foo/bar.ext
    r'|'
    r'(?:[\w.\-]+/)+[\w.\-]+\.\w+'           # relative: foo/bar.ext
    r'|'
    r'[\w.\-]+\.(?:py|ts|tsx|js|jsx|json|md|css|html|yaml|yml|toml|sh|sql|prisma|env)(?![\w])'  # bare: file.ext (word boundary)
    r')',
    re.MULTILINE,
)

# Error signature patterns
_ERROR_PATTERNS = [
    re.compile(r'(?:Error|Exception|Failed|FAILED):\s*.+', re.IGNORECASE),
    re.compile(r'Traceback \(most recent call last\):.*?(?:\w+Error|\w+Exception):\s*.+', re.DOTALL),
    re.compile(r'SyntaxError:\s*.+', re.IGNORECASE),
    re.compile(r'Failed to compile\..*', re.DOTALL),
]


def _get_message_content(msg) -> str:
    """Safely extract text content from any message type.

    Handles dict messages, Message objects (with attributes), and
    gracefully returns empty string for None, strings, or other types.

    F-8 Fix: Previously called msg.get() which fails on non-dict Message
    objects with AttributeError: 'Message' object has no attribute 'get'.
    """
    if msg is None:
        return ""
    if isinstance(msg, dict):
        content = msg.get("content", "") or ""
        return str(content)
    # Handle Message objects or any object with a 'content' attribute
    if hasattr(msg, "content"):
        content = getattr(msg, "content", "") or ""
        return str(content)
    # Unknown type — return empty string
    return ""


def _get_message_attr(msg, attr: str, default=""):
    """Safely get an attribute from a message (dict or object).

    F-8 Fix: Works with both dict messages and Message objects.
    """
    if msg is None:
        return default
    if isinstance(msg, dict):
        return msg.get(attr, default)
    return getattr(msg, attr, default)


def _extract_file_paths(messages: List[dict]) -> List[str]:
    """Extract unique file paths from message content.
    
    Uses regex to find absolute paths, relative paths with directories,
    and bare filenames with known extensions. Zero LLM calls.
    
    Args:
        messages: List of message dicts with 'content' field.
        
    Returns:
        Deduplicated list of file paths found.
    """
    seen: set[str] = set()
    result: list[str] = []
    
    for msg in messages:
        text = _get_message_content(msg)
        if not text:
            continue
        for match in _FILE_PATH_RE.finditer(text):
            path = match.group(1).strip()
            if path and path not in seen:
                seen.add(path)
                result.append(path)
    
    return result


def _extract_tool_calls(messages: List[dict]) -> List[dict]:
    """Extract tool call names and abbreviated results from messages.
    
    Looks for both assistant tool_calls (name) and tool-role results.
    Results are truncated to 500 chars max to keep the snapshot small.
    
    Args:
        messages: List of message dicts.
        
    Returns:
        List of dicts with 'name' and optional 'result' keys.
    """
    MAX_RESULT_LEN = 500
    calls: list[dict] = []
    
    for msg in messages:
        if msg is None:
            continue
        role = _get_message_attr(msg, "role", "")
        
        # Assistant messages with tool_calls
        if role == "assistant":
            tool_calls = _get_message_attr(msg, "tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get("function", {})
                        name = func.get("name", "")
                    else:
                        func = getattr(tc, "function", None) or {}
                        name = func.get("name", "") if isinstance(func, dict) else getattr(func, "name", "")
                    if name:
                        calls.append({"name": name})
        
        # Tool-role result messages
        elif role == "tool":
            name = _get_message_attr(msg, "name", "unknown")
            result = _get_message_content(msg)
            if result and len(result) > MAX_RESULT_LEN:
                result = result[:MAX_RESULT_LEN]
            calls.append({"name": name, "result": result})
    
    return calls


def _extract_errors(messages: List[dict]) -> List[str]:
    """Extract error messages and stack traces from message content.
    
    Scans all messages for common error patterns: Error:, Exception:,
    Traceback, SyntaxError, Failed to compile, etc. Zero LLM calls.
    
    Args:
        messages: List of message dicts.
        
    Returns:
        List of error strings found.
    """
    errors: list[str] = []
    
    for msg in messages:
        text = _get_message_content(msg)
        if not text:
            continue
        for pattern in _ERROR_PATTERNS:
            for match in pattern.finditer(text):
                error_text = match.group(0).strip()
                if error_text and len(error_text) > 10:  # Skip trivially short matches
                    errors.append(error_text[:500])  # Cap length
    
    return errors


def _extract_last_user_instruction(messages: List[dict]) -> Optional[str]:
    """Extract the last user-role message content.
    
    Useful for preserving the current task description across compression.
    Truncates to 1000 chars max.
    
    Args:
        messages: List of message dicts.
        
    Returns:
        Last user message content, or None if no user messages found.
    """
    MAX_LEN = 1000
    
    for msg in reversed(messages):
        if msg is None:
            continue
        if _get_message_attr(msg, "role", "") == "user":
            content = _get_message_content(msg)
            if content:
                return content[:MAX_LEN] if len(content) > MAX_LEN else content
    
    return None


def auto_snapshot_before_compression(
    messages: List[dict],
    memory_bank_path: str,
) -> Optional[str]:
    """Create a deterministic snapshot of critical context before compression.
    
    This function uses ZERO LLM calls — purely regex/string extraction.
    It saves the snapshot to memory-bank/pre_compression_snapshot_<timestamp>.md
    so that the agent can later use it to update its memory bank.
    
    Args:
        messages: The messages that are about to be compressed.
        memory_bank_path: Absolute path to the memory-bank directory.
        
    Returns:
        Path to the snapshot file, or None on failure.
    """
    try:
        # Ensure directory exists
        os.makedirs(memory_bank_path, exist_ok=True)
        
        # Extract all sections
        file_paths = _extract_file_paths(messages)
        tool_calls = _extract_tool_calls(messages)
        errors = _extract_errors(messages)
        last_instruction = _extract_last_user_instruction(messages)
        
        # Build snapshot content
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%f")
        lines = [
            f"# Pre-Compression Context Snapshot",
            f"**Generated**: {datetime.utcnow().isoformat()}Z (auto, zero-LLM)",
            f"**Purpose**: Critical context preserved before history condensation.",
            "",
        ]
        
        # Section 1: Last User Instruction
        if last_instruction:
            lines.extend([
                "## Last User Instruction",
                last_instruction,
                "",
            ])
        
        # Section 2: File Paths
        if file_paths:
            lines.extend([
                "## File Paths Referenced",
                *[f"- `{p}`" for p in file_paths],
                "",
            ])
        
        # Section 3: Tool Calls
        if tool_calls:
            lines.extend(["## Tool Calls"])
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                result = tc.get("result", "")
                if result:
                    lines.append(f"- **{name}**: {result[:200]}")
                else:
                    lines.append(f"- **{name}**")
            lines.append("")
        
        # Section 4: Errors
        if errors:
            lines.extend([
                "## Errors & Failures (CRITICAL — DO NOT REPEAT THESE)",
                *[f"- {e[:300]}" for e in errors],
                "",
            ])
        
        # Write to timestamped file
        filename = f"pre_compression_snapshot_{timestamp}.md"
        filepath = os.path.join(memory_bank_path, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        logger.info(f"Pre-compression snapshot saved: {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"Failed to write pre-compression snapshot: {e}")
        return None


def get_post_compression_nudge() -> str:
    """Return the post-compression nudge message for history injection.
    
    This message is injected into the agent's history after compression
    to inform the agent that context was lost and it should update its
    memory bank.
    
    Returns:
        The nudge message string.
    """
    return (
        "⚠️ **Context was compressed** due to context window overflow. "
        "Some earlier conversation details have been condensed into summaries. "
        "Critical data has been saved to `memory-bank/pre_compression_snapshot_*.md`. "
        "Review the snapshot and update your memory bank with `maintain_memory_bank` "
        "to preserve important task context, decisions, and error history."
    )


def _get_memory_bank_path(agent: "Agent") -> Optional[str]:
    """Resolve the memory-bank directory path for an agent.
    
    Uses the agent's file system helper to resolve the absolute path.
    Falls back to a relative path if the helper is unavailable.
    
    Args:
        agent: The agent instance.
        
    Returns:
        Absolute path to memory-bank directory, or None.
    """
    try:
        from python.helpers import files
        return os.path.join(files.get_abs_path("memory-bank"))
    except Exception:
        return None





# =============================================================================
# Recovery Handler
# =============================================================================

class ContextRecoveryHandler:
    """
    Handles automatic recovery from context window errors.
    
    Strategy:
    1. Detect context overflow error
    2. Condense history by reduction_percent (default 25%)
    3. Retry the failed operation
    4. Repeat up to max_retries times
    
    Args:
        max_retries: Maximum number of retry attempts (default 3)
        reduction_percent: Target reduction percentage per condensation (default 25.0)
        
    Example:
        handler = ContextRecoveryHandler(max_retries=3)
        
        async def my_llm_call():
            return await llm.invoke(messages)
        
        try:
            result = await my_llm_call()
        except Exception as e:
            result = await handler.handle_error(agent, e, my_llm_call, "chat")
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        reduction_percent: float = 25.0,
    ):
        self.max_retries = max_retries
        self.reduction_percent = reduction_percent
        self._retry_counts: Dict[str, int] = {}
    
    async def handle_error(
        self,
        agent: "Agent",
        error: Exception,
        retry_func: Callable[[], Any],
        context_id: str = "default",
    ) -> Any:
        """
        Handle a potential context error with automatic recovery.
        
        Args:
            agent: The agent that encountered the error
            error: The exception that was raised
            retry_func: Async function to retry after recovery
            context_id: Identifier for tracking retries per context
            
        Returns:
            Result of retry_func if recovery succeeds
            
        Raises:
            Original error if not a context error or max retries exceeded
        """
        # Check if this is a context error
        detection = detect_context_error(error)
        if detection is None:
            # Not a context error - re-raise immediately
            raise error
        
        error_type, provider = detection
        
        # Check retry count
        retry_key = f"{context_id}:{getattr(agent, 'number', 0)}"
        current_retries = self._retry_counts.get(retry_key, 0)
        
        if current_retries >= self.max_retries:
            logger.error(
                f"Max retries ({self.max_retries}) exceeded for context recovery. "
                f"Error type: {error_type.value}, Provider: {provider}"
            )
            raise error
        
        # Increment retry count
        self._retry_counts[retry_key] = current_retries + 1
        
        logger.warning(
            f"Context error detected ({error_type.value} from {provider}). "
            f"Attempting recovery {current_retries + 1}/{self.max_retries}"
        )
        
        # Perform condensation
        await self._condense_for_recovery(agent)
        
        # Retry the operation
        try:
            result = await retry_func()
            # Success - reset retry count
            self._retry_counts[retry_key] = 0
            logger.info("Context recovery successful")
            return result
        except Exception as retry_error:
            # Check if it's still a context error
            # AND ensure we don't recurse too deep (prevent RecursionError)
            if detect_context_error(retry_error) and current_retries < self.max_retries:
                # Recursive retry
                return await self.handle_error(
                    agent, retry_error, retry_func, context_id
                )
            else:
                # Different error or max retries reached - raise it
                raise retry_error
    
    async def _condense_for_recovery(self, agent: "Agent", error: Exception | str | None = None) -> None:
        """
        Perform aggressive condensation for error recovery.
        
        Targets reduction_percent reduction in context size,
        or the specific limit extracted from the error message.
        Continues compressing until target is reached or no more compression possible.
        
        Args:
            agent: The agent whose history to condense
            error: The optional error object or string to parse for limits
        """
        if not hasattr(agent, 'history'):
            logger.warning("Agent has no history attribute - cannot condense")
            return
        
        history = agent.history
        
        # Get current token count
        before_tokens = history.get_tokens()
        if before_tokens == 0:
            logger.debug("History is empty - nothing to condense")
            return

        # ── Pre-Compression Auto-Snapshot (Iteration 159) ──
        # Save critical context BEFORE compression destroys it.
        # This is zero-LLM — purely deterministic regex/string extraction.
        mb_path = _get_memory_bank_path(agent)
        if mb_path and hasattr(history, 'messages'):
            auto_snapshot_before_compression(history.messages, mb_path)
        else:
            logger.debug("Skipping auto-snapshot: no memory-bank path or no messages")

        # Attempt to extract limits from the error if available (Issue #416)
        error_limit = None
        if error:
            error_str = str(error)
            # Look for common patterns: "requested about 5701765 tokens (5701765 of text input). ... maximum context length is 2000000 tokens"
            import re
            requested = re.search(r"requested (?:about )?(\d+)", error_str)
            limit = re.search(r"maximum (?:context )?length is (\d+)", error_str) or re.search(r"limit is (\d+)", error_str)
            
            if limit:
                error_limit = int(limit.group(1))
                logger.info(f"Extracted model limit from error: {error_limit:,}")
            elif requested:
                # If we don't have limit but have requested, we know we need to go LOWER than requested.
                # Use model default if possible.
                pass
        
        target_tokens = int(before_tokens * (1 - self.reduction_percent / 100))
        
        # If we detect a massive overflow (e.g. from the error message context), 
        # we should be much more aggressive.
        model_limit = error_limit or history.get_ctx_limit()
        if before_tokens > model_limit or error_limit:
            # We are OVER the model's known limit. Target 90% of model limit.
            target_tokens = min(target_tokens, int(model_limit * 0.9))

        logger.info(
            f"Recovery condensation: {before_tokens:,} tokens -> "
            f"target {target_tokens:,} tokens (Model Limit: {model_limit:,})"
        )
        
        # Compress until we hit target or can't compress more
        iterations = 0
        max_iterations = 10  # Safety limit
        
        while history.get_tokens() > target_tokens and iterations < max_iterations:
            compressed = await history.compress()
            if not compressed:
                logger.debug(f"No more compression possible after {iterations} iterations")
                break
            iterations += 1
        
        # Brute-force fallback (Issue #416 Fix)
        # If intelligent compression didn't reach target, force prune
        if history.get_tokens() > target_tokens:
            logger.warning(f"Intelligent compression insufficient ({history.get_tokens():,} > {target_tokens:,}). Scaling to hard prune...")
            history.prune_to_tokens(target_tokens)
        
        after_tokens = history.get_tokens()
        if before_tokens > 0:
            actual_reduction = (before_tokens - after_tokens) / before_tokens * 100
        else:
            actual_reduction = 0
        
        logger.info(
            f"Recovery condensation complete: {before_tokens:,} -> {after_tokens:,} tokens "
            f"({actual_reduction:.1f}% reduction in {iterations} iterations)"
        )




# =============================================================================
# Decorator
# =============================================================================

T = TypeVar('T')


def with_context_recovery(context_id: str = "llm_call") -> Callable:
    """
    Decorator that adds automatic context error recovery to async functions.
    
    The decorated function must have an agent as its first argument.
    
    Args:
        context_id: Identifier for tracking retries (default "llm_call")
        
    Returns:
        Decorated function with automatic recovery
        
    Example:
        @with_context_recovery("chat")
        async def call_chat_model(agent, messages):
            return await llm.invoke(messages)
        
        # If context error occurs, will automatically condense and retry
        result = await call_chat_model(agent, messages)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(agent: "Agent", *args: Any, **kwargs: Any) -> Any:
            handler = get_recovery_handler()
            
            async def retry() -> Any:
                return await func(agent, *args, **kwargs)
            
            try:
                return await func(agent, *args, **kwargs)
            except Exception as e:
                return await handler.handle_error(
                    agent, e, retry, context_id
                )
        
        return wrapper
    return decorator


# =============================================================================
# Global Handler
# =============================================================================

_recovery_handler: Optional[ContextRecoveryHandler] = None


def get_recovery_handler() -> ContextRecoveryHandler:
    """
    Get or create the global recovery handler singleton.
    
    Returns:
        ContextRecoveryHandler instance with default configuration
    """
    global _recovery_handler
    if _recovery_handler is None:
        _recovery_handler = ContextRecoveryHandler()
    return _recovery_handler


def reset_recovery_handler() -> None:
    """
    Reset the global recovery handler.
    
    Useful for testing or reconfiguration.
    """
    global _recovery_handler
    _recovery_handler = None


def configure_recovery_handler(
    max_retries: int = 3,
    reduction_percent: float = 25.0,
) -> ContextRecoveryHandler:
    """
    Configure and return the global recovery handler.
    
    Args:
        max_retries: Maximum retry attempts
        reduction_percent: Target reduction per condensation
        
    Returns:
        Configured ContextRecoveryHandler instance
    """
    global _recovery_handler
    _recovery_handler = ContextRecoveryHandler(
        max_retries=max_retries,
        reduction_percent=reduction_percent,
    )
    return _recovery_handler
