"""
ToolFrequencyDetector — Memory Bank Loop Fix

Detects when a single tool is called too frequently within a lookback window,
regardless of argument variation. This catches patterns where the same tool
is called with different args each time (so sig-based detection misses it).

Configurable per-tool thresholds allow relaxed limits for tools that
legitimately need frequent calls (e.g., code_execution_tool) while
catching obsessive patterns on others (e.g., maintain_memory_bank).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from python.helpers.detectors.base import AgentState, DetectedPattern, PatternDetector
from python.helpers.loop_prevention import PatternType

logger = logging.getLogger(__name__)


class ToolFrequencyDetector(PatternDetector):
    """
    Detects when a single tool is called too frequently within a lookback window,
    regardless of argument variation.
    
    This catches patterns like maintain_memory_bank being called 14 times
    with different file_name/content/mode args — each call has a unique 
    signature, so RepetitiveActionDetector and detect_no_progress_streak miss it.
    """

    # Default: max calls of same tool in lookback window before triggering
    DEFAULT_THRESHOLD = 5
    LOOKBACK = 10

    # Per-tool threshold overrides (calls within LOOKBACK window)
    TOOL_THRESHOLDS: Dict[str, int] = {
        "maintain_memory_bank": 4,   # Memory bank: max 4 in 10 calls (allows 1 full batch: read+progress+context+lessons)
        "scheduler": 3,             # Scheduler: max 3 in 10 calls
        "code_execution_tool": 8,   # Code exec: higher limit (legitimate use)
        "search_engine": 8,         # Search: higher limit (research tasks)
        # ── RCA-2 (2026-04-25): Sequential thinking loop breaker ──
        # Agents get stuck calling sequential_thinking 11+ times with identical
        # or near-identical args. A threshold of 3 catches this 73% earlier.
        # Planning tools should NEVER need more than 3 calls in a 10-call window.
        # Both bare names and dotted MCP names are listed for explicit coverage,
        # but _get_threshold() also normalizes via basename extraction.
        "sequential_thinking": 3,                       # Bare server name
        "sequentialthinking": 3,                        # Bare tool name
        "sequential_thinking.sequentialthinking": 3,    # Dotted MCP format (underscore)
        "sequential-thinking.sequentialthinking": 3,    # Dotted MCP format (hyphen)
    }

    # Tools exempt from frequency limiting (always legitimate)
    EXEMPT_TOOLS = frozenset({
        "response",            # Must always be allowed
        "call_subordinate",    # Delegation is fine
    })

    def __init__(
        self,
        default_threshold: Optional[int] = None,
        lookback: Optional[int] = None,
        tool_thresholds: Optional[Dict[str, int]] = None,
    ):
        if default_threshold is not None:
            self.DEFAULT_THRESHOLD = default_threshold
        if lookback is not None:
            self.LOOKBACK = lookback
        if tool_thresholds is not None:
            self.TOOL_THRESHOLDS = {**self.TOOL_THRESHOLDS, **tool_thresholds}

    @property
    def pattern_type(self) -> PatternType:
        return PatternType.REPETITIVE_ACTION

    def _get_threshold(self, tool_name: str) -> int:
        """Get the frequency threshold for a tool, with MCP basename fallback.

        MCP tools flow through as dotted names (e.g. 'sequential_thinking.sequentialthinking').
        This checks: exact name → basename (after dot) → server name (before dot) → default.
        """
        # 1. Exact match
        if tool_name in self.TOOL_THRESHOLDS:
            return self.TOOL_THRESHOLDS[tool_name]
        # 2. Basename match for dotted MCP tools (e.g. 'sequentialthinking' from 'server.sequentialthinking')
        if "." in tool_name:
            parts = tool_name.split(".")
            basename = parts[-1]
            server = parts[0]
            if basename in self.TOOL_THRESHOLDS:
                return self.TOOL_THRESHOLDS[basename]
            if server in self.TOOL_THRESHOLDS:
                return self.TOOL_THRESHOLDS[server]
        return self.DEFAULT_THRESHOLD

    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        tool_calls = state.recent_tool_calls[-self.LOOKBACK:]

        if not tool_calls:
            return None

        # Count calls by tool name
        tool_counts: Dict[str, int] = {}
        for call in tool_calls:
            tool_name = call.get("tool_name", "unknown")
            if tool_name in self.EXEMPT_TOOLS:
                continue
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Check each tool against its threshold
        for tool_name, count in tool_counts.items():
            threshold = self._get_threshold(tool_name)
            if count > threshold:
                # Determine severity based on how far over threshold
                ratio = count / threshold
                if ratio >= 2.0:
                    severity = "high"
                else:
                    severity = "medium"

                confidence = min(0.95, 0.85 + (count - threshold) * 0.02)

                return self._create_pattern(
                    state,
                    confidence=confidence,
                    severity=severity,
                    description=(
                        f"Tool '{tool_name}' called {count} times in last "
                        f"{len(tool_calls)} tool calls (threshold: {threshold}). "
                        f"This indicates obsessive tool use regardless of argument variation."
                    ),
                    metadata={
                        "tool_name": tool_name,
                        "call_count": count,
                        "threshold": threshold,
                        "lookback_size": len(tool_calls),
                        "suggestion": (
                            f"Stop calling '{tool_name}' and focus on the primary task. "
                            f"Memory bank updates should be batched, not per-step."
                        ),
                    },
                )

        return None
