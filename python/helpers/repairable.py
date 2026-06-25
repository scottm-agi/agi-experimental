"""
RepairableGuard — MD5-fingerprinted loop protection for tool call self-correction.

Inspired by AGI Experimental v1.5's RepairableException pattern, but with MD5-based
progress hashing to guarantee loop protection.

When an MCP tool call fails, the guard:
1. MD5-hashes the error (normalized: timestamps/UUIDs/line-numbers stripped)
2. Tracks retry count per tool+error fingerprint
3. Returns should_retry=True up to max_retries, then False
4. Builds structured warning messages that escalate with attempts

Reuses: python.helpers.hashing.dedup_hash_short() for fingerprinting.

Usage:
    from python.helpers.repairable import RepairableGuard
    
    guard = RepairableGuard(max_retries=3)
    
    # In tool execution:
    if error_detected:
        if guard.should_retry(tool_name, error_text):
            attempt = guard.get_attempt_count(tool_name, error_text)
            warning = guard.build_warning(tool_name, error_text, attempt)
            agent.hist_add_warning(warning)
            # Continue loop — LLM will see warning and self-correct
        else:
            # Max retries exhausted — return error normally
            pass
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set


def _normalize_error(tool_name: str, error_text: str) -> str:
    """
    Normalize an error message for stable fingerprinting.
    
    Strips volatile content (timestamps, UUIDs, line numbers, standalone numbers)
    and lowercases, so semantically identical errors produce the same hash.
    """
    if not error_text:
        return f"{tool_name}::"
    
    normalized = error_text
    # Strip timestamps (ISO 8601 variants)
    normalized = re.sub(
        r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s"]*',
        '[TS]',
        normalized,
    )
    # Strip UUIDs
    normalized = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '[UUID]',
        normalized,
        flags=re.IGNORECASE,
    )
    # Strip standalone numbers (line numbers, error codes with context preserved)
    normalized = re.sub(r'\b\d+\b', '[N]', normalized)
    # Normalize whitespace
    normalized = ' '.join(normalized.lower().split())
    
    return f"{tool_name}::{normalized}"


def _fingerprint(tool_name: str, error_text: str) -> str:
    """
    Generate an MD5 fingerprint for a tool+error combination.
    
    Uses dedup_hash_short from our centralized hashing module.
    Falls back to built-in hashlib if import fails (test isolation).
    """
    normalized = _normalize_error(tool_name, error_text)
    try:
        from python.helpers.hashing import dedup_hash_short
        return dedup_hash_short(normalized, length=16)
    except ImportError:
        # Fallback for test isolation
        from python.helpers.hashing import content_hash_short
        return content_hash_short(str(normalized), length=16)


class RepairableGuard:
    """
    MD5-fingerprinted loop protection for tool call self-correction.
    
    Tracks error fingerprints per tool+error combo. Allows up to max_retries
    of the same error before blocking further retries.
    
    Args:
        max_retries: Maximum times the same error can trigger a retry.
                     After this count, should_retry returns False.
    """
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        # MD5 fingerprint -> attempt count
        self._error_hashes: Dict[str, int] = {}
        # tool_name -> set of fingerprints (for reset_tool)
        self._tool_fingerprints: Dict[str, Set[str]] = {}
    
    def should_retry(self, tool_name: str, error_text: str) -> bool:
        """
        Check if a tool error should trigger a self-correction retry.
        
        Increments the counter for this error fingerprint and returns
        True if under max_retries, False if exhausted.
        
        Args:
            tool_name: Name of the tool that failed
            error_text: The error message text
            
        Returns:
            True if retry is allowed, False if max retries exhausted
        """
        fp = _fingerprint(tool_name, error_text)
        count = self._error_hashes.get(fp, 0)
        self._error_hashes[fp] = count + 1
        
        # Track tool -> fingerprint association for reset_tool
        if tool_name not in self._tool_fingerprints:
            self._tool_fingerprints[tool_name] = set()
        self._tool_fingerprints[tool_name].add(fp)
        
        return count + 1 <= self.max_retries
    
    def get_attempt_count(self, tool_name: str, error_text: str) -> int:
        """
        Get the current attempt count for a tool+error combo.
        
        Returns 0 if this error hasn't been seen yet.
        """
        fp = _fingerprint(tool_name, error_text)
        return self._error_hashes.get(fp, 0)
    
    def reset(self) -> None:
        """Reset all error counters."""
        self._error_hashes.clear()
        self._tool_fingerprints.clear()
    
    def reset_tool(self, tool_name: str) -> None:
        """
        Reset error counters for a specific tool only.
        
        Uses the tool->fingerprint reverse mapping to efficiently
        remove only the fingerprints associated with the given tool.
        """
        fps_to_remove = self._tool_fingerprints.get(tool_name, set())
        for fp in fps_to_remove:
            self._error_hashes.pop(fp, None)
        self._tool_fingerprints.pop(tool_name, None)
    
    def build_warning(self, tool_name: str, error_text: str, attempt: int) -> str:
        """
        Build a structured warning message for the agent.
        
        Messages escalate with attempt count:
        - Attempts 1-(max-1): Informational, suggest correcting params
        - Attempt = max_retries: Strong warning, suggest different approach
        - Attempt > max_retries: Exhaustion notice, tell agent to skip
        
        Args:
            tool_name: Name of the tool that failed
            error_text: The error message
            attempt: Current attempt number (1-based)
            
        Returns:
            Structured warning message string
        """
        if attempt > self.max_retries:
            return (
                f"⛔ [REPAIR EXHAUSTED] Tool '{tool_name}' has failed {attempt} times "
                f"with the same error. You MUST skip this tool and use a different approach. "
                f"Do NOT retry '{tool_name}' again.\n"
                f"Error: {error_text}"
            )
        
        if attempt >= self.max_retries:
            return (
                f"⚠️ [REPAIR WARNING {attempt}/{self.max_retries}] Tool '{tool_name}' "
                f"has failed {attempt} times with the same error. "
                f"This is your LAST attempt — try a completely different approach or alternative tool.\n"
                f"Error: {error_text}"
            )
        
        return (
            f"🔧 [REPAIR ATTEMPT {attempt}/{self.max_retries}] Tool '{tool_name}' "
            f"returned an error. Review and correct your parameters.\n"
            f"Error: {error_text}"
        )

    def build_warning_with_schema(
        self,
        tool_name: str,
        error_text: str,
        attempt: int,
        input_schema: Optional[Dict[str, Any]] = None,
        actual_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build a schema-enriched warning for -32602 errors.
        
        ROOT CAUSE FIX: Previous build_warning() was schema-blind — it echoed
        back the raw error but never showed the agent the tool's actual
        inputSchema. This method injects correct parameter names, types, and
        marks them as required/optional so the agent can self-correct.
        
        For non -32602 errors, falls back to standard build_warning().
        
        Args:
            tool_name: Name of the tool that failed
            error_text: The error message
            attempt: Current attempt number (1-based)
            input_schema: Tool's inputSchema dict (JSON Schema format)
            actual_args: The args the agent actually sent (for diff display)
            
        Returns:
            Structured warning with schema details for -32602, or generic for other errors
        """
        is_32602 = bool(re.search(r'-32602|Invalid arguments', error_text, re.IGNORECASE))
        
        # Non-32602 errors or missing schema: fall back to generic
        if not is_32602 or not input_schema:
            return self.build_warning(tool_name, error_text, attempt)
        
        # Build schema-enriched hint
        schema_section = self._format_schema_hint(input_schema, actual_args)
        
        if attempt > self.max_retries:
            return (
                f"⛔ [REPAIR EXHAUSTED] Tool '{tool_name}' has failed {attempt} times "
                f"with MCP error -32602 (invalid arguments). "
                f"You MUST skip this tool and use a different approach.\n"
                f"Error: {error_text}\n"
                f"{schema_section}"
            )
        
        prefix = (
            f"🔧 [REPAIR ATTEMPT {attempt}/{self.max_retries}] "
            f"Tool '{tool_name}' returned error -32602.\n"
            f"❌ Error: {error_text}\n"
        )
        
        return f"{prefix}{schema_section}"

    @staticmethod
    def _format_schema_hint(
        input_schema: Dict[str, Any],
        actual_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Format a tool's inputSchema into a human-readable parameter listing.
        
        Shows each parameter with type, required/optional status, and description.
        If actual_args are provided, highlights which sent params are NOT valid.
        
        Args:
            input_schema: JSON Schema dict with 'properties' and 'required'
            actual_args: What the agent actually sent (optional)
            
        Returns:
            Formatted multi-line string with parameter details
        """
        properties = input_schema.get("properties", {})
        required_params = set(input_schema.get("required", []))
        
        if not properties:
            return "📋 No parameter schema available."
        
        lines = ["📋 Correct parameter schema:"]
        for param_name, param_def in properties.items():
            param_type = param_def.get("type", "any")
            description = param_def.get("description", "")
            req_marker = "(required)" if param_name in required_params else "(optional)"
            desc_part = f" — {description}" if description else ""
            lines.append(f"  - {param_name}: {param_type} {req_marker}{desc_part}")
        
        # Highlight wrong params if actual_args provided
        if actual_args and isinstance(actual_args, dict):
            valid_params = set(properties.keys())
            sent_params = set(actual_args.keys())
            invalid_params = sent_params - valid_params
            
            if invalid_params:
                invalid_list = ", ".join(f"'{p}'" for p in sorted(invalid_params))
                valid_suggestions = ", ".join(f"'{p}'" for p in sorted(valid_params))
                lines.append(
                    f"❌ You passed: {invalid_list} — "
                    f"NOT valid parameter(s). "
                    f"Use: {valid_suggestions} instead."
                )
        
        return "\n".join(lines)
