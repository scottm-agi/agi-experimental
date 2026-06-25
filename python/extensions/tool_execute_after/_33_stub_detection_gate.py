"""
Stub Detection Gate — tool_execute_after extension.

Fires after `call_subordinate` (with break_loop=True) OR `response` (always).
Scans the response text for stub indicators: TODO placeholders, unimplemented
functions, and skeleton code that doesn't deliver real implementation.

The gate injects a warning into the agent's history when stubs are detected,
prompting the orchestrator to request a full implementation before accepting
the result.

Hooks into: tool_execute_after (order 33 — before antipattern scanner)

TDD fix: gate now accepts BOTH tool_name="call_subordinate" AND tool_name="response".
  - call_subordinate: requires break_loop=True (only fire on delivery, not delegation)
  - response: always fires (response IS the delivery — no break_loop check needed)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.stub_detection_gate")

# ── Stub Indicator Patterns ────────────────────────────────────────────
# Patterns that indicate unimplemented/skeleton code was delivered.
# (Imported from shared module — DUP-2 consolidation)
from python.helpers.stub_patterns import UNIVERSAL_STUB_PATTERNS

STUB_PATTERNS = [p.pattern for p in UNIVERSAL_STUB_PATTERNS]

_STUB_RE = re.compile("|".join(STUB_PATTERNS), re.IGNORECASE)


# Tools that deliver responses
_DELIVERY_TOOLS = ("call_subordinate", "response")


class StubDetectionGate(Extension):
    # Context-aware: code+orchestrator, response and delegation
    PROFILES = {"code", "multiagentdev", "alex", "default"}
    TOOLS = frozenset({"response", "call_subordinate"})

    """Detect stub/skeleton responses at delivery time and warn the orchestrator.

    Accepts both call_subordinate (with break_loop=True) and response tool.
    """

    async def execute(self, tool_name: str = "", tool_args: dict | None = None,
                      tool_response: str = "", **kwargs: Any) -> None:
        """Scan delivered responses for stub indicators."""
        if tool_args is None:
            tool_args = {}

        # ── Tool filter: only fire on delivery tools ──
        if tool_name not in _DELIVERY_TOOLS:
            return

        # ── break_loop filter: only for call_subordinate ──
        # For tool_name="response", always fire (response IS the delivery).
        # For tool_name="call_subordinate", only fire when break_loop=True.
        if tool_name == "call_subordinate" and not tool_args.get("break_loop", False):
            return

        # response tool always fires — no break_loop check needed
        if tool_name == "response":
            pass  # fall through to stub scan

        # ── Scan response for stubs ──
        response_text = tool_response or ""
        if not response_text:
            return

        if not _STUB_RE.search(response_text):
            return

        # ── Warn orchestrator ──
        logger.warning(
            "[STUB GATE] Stub indicators detected in %s response. "
            "Injecting warning.",
            tool_name,
        )

        warning = (
            "⚠️ **STUB DETECTION WARNING**\n"
            "The delivered response contains TODO/FIXME/placeholder markers or "
            "unimplemented functions. This indicates skeleton code was delivered "
            "instead of a real implementation.\n\n"
            "**Required Action**: Request the subordinate to replace all stubs with "
            "working, tested implementations before accepting this phase as complete. "
            "Do NOT mark the phase as completed until all TODOs are resolved."
        )

        self.agent.hist_add_warning(warning)

    def _check_stubs(self, scan_result: dict, response: Any, block_count: int = 0) -> None:
        """FIX-003: Prepend stub detection warning to response.message (not replace).

        This is the direct invocation interface for testing and for cases where
        the caller already has a response object and a scan_result dict.

        Args:
            scan_result: Dict with 'total_stubs' (int) and 'stubs' (list of dicts
                         with 'file', 'line', 'text' keys).
            response: Response object with a mutable .message attribute.
            block_count: Current block count (unused, for future escalation).
        """
        total_stubs = scan_result.get("total_stubs", 0)
        if total_stubs == 0:
            return

        stubs = scan_result.get("stubs", [])
        stub_list = "\n".join(
            f"  - {s.get('file', '?')}:{s.get('line', '?')} — {s.get('text', '')}"
            for s in stubs[:5]  # Show up to 5 examples
        )

        gate_message = (
            f"⚠️ **STUB DETECTION GATE**: {total_stubs} stub/placeholder markers detected.\n"
            f"{stub_list}\n\n"
            "Replace ALL stubs with working implementations before completing this phase. "
            "Do NOT mark the phase as done until every TODO/FIXME/placeholder is resolved.\n\n"
        )

        original = response.message or ""
        response.message = gate_message + original
