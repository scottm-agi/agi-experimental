"""
Universal Error Ledger — Centralized error tracking for agent awareness.

Provides a per-context error store that agents can write to (from tool failures,
LLM errors, scheduler errors, delegation failures) and read from (via prompt
injection) to enable 5-Why course-correction.

Usage:
    from python.helpers.error_ledger import get_error_ledger, ErrorEntry

    ledger = get_error_ledger()
    ledger.record("ctx-123", ErrorEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="tool",
        severity="high",
        summary="HTTP 404 from wrong endpoint",
        details="GET https://wrong.example.com returned 404",
        tool_name="code_execution",
        five_why_hint="Use the correct API domain",
    ))

    # In extension — inject into prompt if errors exist
    prompt_text = ledger.render_prompt_injection("ctx-123")
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from python.helpers.strings import truncate_text_by_ratio


@dataclass
class ErrorEntry:
    """A single recorded error with structured metadata.
    
    F-ERR-1: Enhanced with category, domain, fix tracking, and repeat detection.
    ITR-48: Added resolution tracking — errors can be marked resolved when the
    agent fixes them (e.g., build passes after "module not found" error).
    All new fields have defaults for backward compatibility.
    """
    timestamp: str          # ISO 8601
    source: str             # "tool", "llm", "scheduler", "delegation"
    severity: str           # "low", "medium", "high", "critical"
    summary: str            # 1-line human-readable
    details: str            # Full error text (truncated on render)
    tool_name: str = ""     # If tool-related
    five_why_hint: str = "" # Prescriptive guidance for course-correction
    # F-ERR-1: New fields — all have defaults for backward compat
    error_category: str = ""       # "build", "test", "runtime", "auth", "timeout", "dependency"
    domain: str = ""               # "nextjs", "python", "npm", "docker", "git"
    attempted_fixes: list = field(default_factory=list)  # What was tried
    occurrence_count: int = 1      # How many times this exact error repeated
    suggested_alternative: str = ""  # Concrete next step
    is_same_as_previous: bool = False  # True if repeat of last error
    # ITR-48: Resolution tracking — errors marked resolved when success signals
    # are detected (e.g., build passes → resolve all "build" category errors).
    resolved: bool = False         # True when the error has been fixed
    resolved_at: str = ""          # ISO 8601 timestamp of resolution
    resolved_by: str = ""          # What resolved it: "build_success", "test_pass", etc.


# Maximum errors stored per context
_DEFAULT_MAX_PER_CONTEXT = 10

# Maximum errors rendered into prompt (to control token budget)
_DEFAULT_RENDER_LIMIT = 5

# Maximum characters for the rendered prompt injection
_MAX_RENDER_CHARS = 4000


class ErrorLedger:
    """
    Per-context error store for agent awareness.

    Thread-safe. In-memory only (no file I/O per turn).
    Errors are keyed by context_id to prevent cross-context leaking.
    """

    def __init__(self, max_per_context: int = _DEFAULT_MAX_PER_CONTEXT):
        self._store: Dict[str, deque[ErrorEntry]] = defaultdict(
            lambda: deque(maxlen=max_per_context)
        )
        self._lock = threading.Lock()
        self._max_per_context = max_per_context

    def record(self, context_id: str, entry: ErrorEntry) -> None:
        """Record an error for a given context. FIFO eviction when full."""
        with self._lock:
            self._store[context_id].append(entry)

    # F-13b: TTL increased from 5min (300s) to 30min (1800s).
    # Short TTL caused errors to expire before orchestrator could act
    # in multi-subordinate flows taking 20+ minutes.
    _DEFAULT_TTL_SECONDS = 1800

    def get_recent(
        self,
        context_id: str,
        limit: int = _DEFAULT_RENDER_LIMIT,
        ttl_seconds: int | None = None,
    ) -> List[ErrorEntry]:
        """
        Get the most recent N errors for a context.

        Args:
            ttl_seconds: If > 0, exclude entries older than this many seconds.
                         0 or None returns all entries (backward compat).
        """
        with self._lock:
            entries = list(self._store.get(context_id, []))

        # Apply TTL filter — exclude stale entries (RCA-262 Error 3)
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._DEFAULT_TTL_SECONDS
        if effective_ttl > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=effective_ttl)
            entries = [
                e for e in entries
                if self._parse_timestamp(e.timestamp) >= cutoff
            ]

        return entries[-limit:]

    def get_unresolved(
        self,
        context_id: str,
        limit: int = _DEFAULT_RENDER_LIMIT,
        ttl_seconds: int | None = None,
    ) -> List[ErrorEntry]:
        """Get only UNRESOLVED errors — excludes errors already fixed.

        ITR-48: This is the primary method for delegation result building.
        Resolved errors should not be reported to the orchestrator as they
        cause hallucinated Recovery tasks for already-fixed problems.
        """
        all_recent = self.get_recent(context_id, limit=limit * 2, ttl_seconds=ttl_seconds)
        unresolved = [e for e in all_recent if not e.resolved]
        return unresolved[-limit:]

    def resolve_by_category(
        self,
        context_id: str,
        category: str,
        reason: str = "",
    ) -> int:
        """Mark all unresolved errors of a category as resolved.

        ITR-48: Called when a success signal is detected (e.g., build passes
        → resolve all 'build' and 'dependency' category errors).

        Args:
            context_id: The agent context to resolve errors in.
            category: Error category to resolve (e.g., "build", "test", "dependency").
            reason: What resolved it (e.g., "build_success", "npm_install_success").

        Returns:
            Number of errors resolved.
        """
        resolved_count = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            entries = self._store.get(context_id, [])
            for entry in entries:
                if (
                    entry.error_category == category
                    and not entry.resolved
                ):
                    entry.resolved = True
                    entry.resolved_at = now
                    entry.resolved_by = reason
                    resolved_count += 1
        return resolved_count

    def resolve_all(
        self,
        context_id: str,
        reason: str = "",
    ) -> int:
        """Mark ALL unresolved errors as resolved.

        Called when a comprehensive success signal is detected (e.g.,
        full build + tests pass = everything is resolved).
        """
        resolved_count = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            entries = self._store.get(context_id, [])
            for entry in entries:
                if not entry.resolved:
                    entry.resolved = True
                    entry.resolved_at = now
                    entry.resolved_by = reason
                    resolved_count += 1
        return resolved_count

    def clear(self, context_id: str) -> None:
        """Clear all errors for a context (e.g. after successful recovery)."""
        with self._lock:
            if context_id in self._store:
                del self._store[context_id]

    def has_errors(self, context_id: str) -> bool:
        """Check if a context has any recorded errors."""
        with self._lock:
            return bool(self._store.get(context_id))

    def has_unresolved_errors(self, context_id: str) -> bool:
        """Check if a context has any UNRESOLVED errors."""
        with self._lock:
            entries = self._store.get(context_id, [])
            return any(not e.resolved for e in entries)

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime:
        """Parse an ISO 8601 timestamp string to a timezone-aware datetime."""
        try:
            # Handle 'Z' suffix and '+00:00' suffix
            normalized = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except (ValueError, AttributeError):
            # Unparseable → treat as epoch-0 (will always be expired)
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    def render_prompt_injection(
        self,
        context_id: str,
        limit: int = _DEFAULT_RENDER_LIMIT,
        ttl_seconds: int | None = None,
    ) -> str:
        """
        Render a compact prompt injection string for the agent.

        Returns "" if no errors exist (zero overhead on clean runs).
        Includes 5-Why guidance framework for course-correction.
        Respects TTL — expired entries are excluded (RCA-262).
        """
        entries = self.get_recent(context_id, limit=limit, ttl_seconds=ttl_seconds)
        if not entries:
            return ""

        lines = [
            "## ⚠️ Recent Errors — 5-Why Course-Correction Required",
            "",
            "Before retrying any failed operation, you MUST:",
            "1. **WHY** did the error occur? (identify the symptom)",
            "2. **WHY** does that root cause exist? (dig deeper — don't stop at the first why)",
            "3. **WHAT** must change in your approach? (fix the ROOT CAUSE, not the symptom)",
            "",
            "DO NOT retry the same operation with the same arguments. Adjust your approach.",
            "",
        ]

        for i, entry in enumerate(entries, 1):
            # ITR-48: Skip resolved errors in prompt injection — they're fixed
            if getattr(entry, "resolved", False):
                continue
            source_label = f"tool: {entry.tool_name}" if entry.tool_name else entry.source
            # F-ERR-1: Include category and domain in header
            header_parts = [f"Error {i} ({source_label})"]
            if getattr(entry, "error_category", ""):
                header_parts.append(f"[{entry.error_category}]")
            if getattr(entry, "domain", ""):
                header_parts.append(f"domain={entry.domain}")
            header = " ".join(header_parts)
            lines.append(f"### {header}")
            lines.append(f"**Summary:** {truncate_text_by_ratio(entry.summary, 200)}")
            if entry.details:
                lines.append(f"**Details:** {truncate_text_by_ratio(entry.details, 300)}")
            if entry.five_why_hint:
                lines.append(f"**Guidance:** {truncate_text_by_ratio(entry.five_why_hint, 200)}")
            # F-ERR-1: Show suggested alternative if available
            if getattr(entry, "suggested_alternative", ""):
                lines.append(f"**Alternative:** {entry.suggested_alternative}")
            # F-ERR-1: Show occurrence count if repeated
            if getattr(entry, "occurrence_count", 1) > 1:
                lines.append(f"**Repeated:** {entry.occurrence_count}x — CHANGE APPROACH")
            lines.append("")

        result = "\n".join(lines)

        # Hard cap on total length — middle-out keeps head+tail for context
        if len(result) > _MAX_RENDER_CHARS:
            result = truncate_text_by_ratio(result, _MAX_RENDER_CHARS)

        return result


# ── Global singleton ──────────────────────────────────

_global_ledger: Optional[ErrorLedger] = None
_global_lock = threading.Lock()


def get_error_ledger() -> ErrorLedger:
    """Get the global ErrorLedger singleton."""
    global _global_ledger
    with _global_lock:
        if _global_ledger is None:
            _global_ledger = ErrorLedger()
        return _global_ledger


def reset_error_ledger() -> None:
    """Reset the global ErrorLedger (mainly for testing)."""
    global _global_ledger
    with _global_lock:
        _global_ledger = None
