"""
Response Dedup Detector (RSP-001).

Detects when an agent sends 2+ identical `response` tool outputs in succession,
indicating a false-completion loop where the agent is copy-pasting the same
DONE message without addressing supervisor feedback.

Fires a RESPONSE_LOOP signal with severity=critical to trigger immediate
supervisor intervention.

Root Cause: Error Class 7 from Launchpad Smoke Audit Iteration 1.
The `response` tool has no verification gate — when the LLM's context is poisoned
by its own prior DONE attempt, it pattern-completes and reproduces the exact same
message on every retry, ignoring supervisor redirects.
"""

import re
from typing import Optional

from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class ResponseDedupDetector(PatternDetector):
    """RSP-001: Detects repeated identical response tool outputs."""

    # Minimum number of identical responses to trigger detection
    MIN_DUPLICATES = 2

    @property
    def pattern_type(self) -> PatternType:
        return PatternType.RESPONSE_LOOP

    @staticmethod
    def _normalize_and_hash(content: str) -> str:
        """
        Normalize response content and return a hash.
        
        Strips whitespace variations so that minor formatting changes
        (extra spaces, trailing newlines) don't defeat dedup.
        """
        # Lowercase, collapse whitespace, strip
        from python.helpers.hashing import content_hash_short
        normalized = re.sub(r'\s+', ' ', content.strip().lower())
        return content_hash_short(normalized, length=16)

    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        """
        Detect if the agent has sent 2+ identical response tool outputs.
        
        Scans recent_tool_calls for 'response' tool calls and hashes their
        message content. If 2+ hashes are identical, fires RESPONSE_LOOP.
        """
        if not state.recent_tool_calls:
            return None

        # Extract all response tool messages in order
        response_hashes = []
        response_contents = []
        for tc in state.recent_tool_calls:
            tool_name = tc.get("tool_name", "")
            if tool_name != "response":
                continue

            # Get the message content from arguments
            args = tc.get("arguments", tc.get("tool_args", {}))
            message = args.get("message", "")
            if not message:
                continue

            content_hash = self._normalize_and_hash(message)
            response_hashes.append(content_hash)
            response_contents.append(message)

        if len(response_hashes) < 2:
            return None

        # Count the most frequent hash
        hash_counts = {}
        for h in response_hashes:
            hash_counts[h] = hash_counts.get(h, 0) + 1

        max_hash = max(hash_counts, key=hash_counts.get)
        max_count = hash_counts[max_hash]

        if max_count >= self.MIN_DUPLICATES:
            # Find a preview of the duplicated content
            preview = ""
            for i, h in enumerate(response_hashes):
                if h == max_hash:
                    preview = response_contents[i][:200]
                    break

            return self._create_pattern(
                state,
                confidence=0.95,
                severity="critical",
                description=(
                    f"Agent sent {max_count} identical response tool outputs. "
                    f"This indicates a false-completion loop where the agent is "
                    f"copy-pasting the same DONE message without addressing feedback."
                ),
                metadata={
                    "pattern_id": "RSP-001",
                    "duplicate_count": max_count,
                    "content_hash": max_hash,
                    "content_preview": preview,
                },
            )

        return None
