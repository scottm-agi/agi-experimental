from __future__ import annotations
"""
Response Fidelity Gate — tool_execute_after extension for 'response' tool

When the 'response' tool fires, checks if the response text contains
fabricated data by comparing against stored data anchors. If hallucination
is detected:
1. First attempt: clears break_loop, injects warning with real values
2. Second attempt (one-shot): if STILL fabricated, surgically replaces
   the fabricated quotes in the response text with real values
3. Anchors are preserved between attempts for re-verification

This replaces the message_loop_end fidelity check which fired TOO LATE
(after the response was already sent via break_loop).

Issue #789: Structural fix for agent fabricating verbatim quotes.
Issue #828: Fixed false-positive hallucination detection via precise
            key-value matching and max-block counter.
"""

import logging
import re
from typing import Any, List

from python.helpers.extension import Extension
from python.helpers.hashing import dedup_hash_short
from python.helpers.phase_category import PhaseCategory
from python.helpers.tool import Response

logger = logging.getLogger("agix.response_fidelity_gate")

# Maximum number of times the gate will block a response when the agent
# is NOT making progress (same response hash). Prevents infinite retry
# loops while allowing legitimate big-data retries (e.g., Google Chat
# crawling across many spaces). Only increments when the response MD5
# hash matches the previously blocked response — if agent rewrites or
# gets new data, counter resets.
# Issue #828: Without this, the agent retried 30+ times.
MAX_FIDELITY_BLOCKS = 3


class ResponseFidelityGate(Extension):
    """Intercept response tool to validate data fidelity before sending."""

    # Context-aware: only fire during phases that produce verifiable output
    CATEGORIES = {
        PhaseCategory.IMPLEMENTATION,
        PhaseCategory.INTEGRATION,
        PhaseCategory.VERIFICATION,
        PhaseCategory.DELIVERY,
    }

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        """After tool execution, validate response data if it's the response tool."""
        if not tool_name or response is None:
            return

        # F-17c: Stop-command UNCONDITIONAL bypass — no content check needed.
        # User said stop → ANY response goes through. Period.
        if self.agent.data.get("_user_stop_directive"):
            logger.info(
                "[FIDELITY GATE] UNCONDITIONAL stop-command bypass: "
                "_user_stop_directive=True — letting response through"
            )
            return  # Allow ANY response through when user said stop

        # ITR-45: Phase cap forced completion bypass.
        if self.agent.data.get("_phase_cap_reached"):
            logger.info(
                "[FIDELITY GATE] Phase cap bypass: "
                "_phase_cap_reached=True — letting response through"
            )
            return  # Allow ANY response through when phase cap reached


        # Only intercept the 'response' tool
        if tool_name.lower() != "response":
            # ── TOOL RE-CALL GUARD ──
            # When the fidelity gate has warned the agent, block re-calls to
            # MCP tools that ALREADY returned successfully (have an anchor).
            # This prevents agents from re-fetching data they already have
            # instead of just fixing their response text.
            # DOES NOT block:
            #   - Tools with no existing anchor (new/different calls, e.g. crawling space 2)
            #   - Non-MCP tools (code_execution, etc.)
            #   - Any tool when fidelity hasn't been triggered
            if self.agent.data.get("_fidelity_warned_this_turn", False):
                from python.extensions.tool_execute_after._15_tool_output_fidelity import ToolOutputFidelity
                tool_lower = tool_name.lower()
                is_mcp = any(tool_lower.startswith(p) for p in ToolOutputFidelity.MCP_PREFIXES)
                if is_mcp and isinstance(response, Response):
                    # Only block if this SPECIFIC tool already has a stored anchor
                    existing_anchors = self.agent.data.get("tool_data_anchors", [])
                    has_anchor = any(a.get("tool_name") == tool_name for a in existing_anchors)
                    if has_anchor:
                        logger.warning(
                            f"[RESPONSE FIDELITY GATE] BLOCKED re-call of '{tool_name}' "
                            f"during fidelity retry — data already anchored. "
                            f"Agent must use 'response' tool, not re-fetch."
                        )
                        response.message = (
                            f"⚠️ TOOL RE-CALL BLOCKED: You already have successful data from '{tool_name}'. "
                            f"The fidelity gate flagged your PREVIOUS response for using fabricated quotes. "
                            f"DO NOT re-call this tool. Instead, use the 'response' tool and copy-paste "
                            f"the EXACT values from your earlier tool result into your response text. "
                            f"The data you already have is correct — just quote it accurately."
                        )
                        return
            # Not the response tool — skip fidelity checking
            return


        logger.info("[RESPONSE FIDELITY GATE] Response tool intercepted, checking anchors...")

        # Get stored data anchors
        anchors = self.agent.data.get("tool_data_anchors", [])
        if not anchors:
            # FIX-4: Orchestrators never call MCP tools, so they never have
            # tool_data_anchors. Build synthetic anchors from the cumulative
            # delegation_result_ledger so we can cross-reference the
            # orchestrator's response against what subordinates actually reported.
            try:
                from python.helpers.delegation_result_processing import (
                    build_orchestrator_anchors_from_ledger,
                )
                ledger_anchors = build_orchestrator_anchors_from_ledger(
                    self.agent.data
                )
                if ledger_anchors:
                    anchors = ledger_anchors
                    logger.info(
                        f"[RESPONSE FIDELITY GATE] No MCP anchors — using "
                        f"{len(ledger_anchors)} delegation ledger anchors"
                    )
                else:
                    logger.info(
                        "[RESPONSE FIDELITY GATE] No anchors stored "
                        "and no delegation ledger, skipping check"
                    )
                    return
            except Exception as e:
                logger.debug(
                    f"[RESPONSE FIDELITY GATE] Delegation anchor build "
                    f"failed (non-fatal): {e}"
                )
                return



        # Check max-block counter — prevent infinite retry loops (Issue #828)
        # Only counts blocks where the response is identical (no progress).
        block_count = self.agent.data.get("_fidelity_block_count", 0)
        if block_count >= MAX_FIDELITY_BLOCKS:
            logger.warning(
                f"[RESPONSE FIDELITY GATE] Max blocks ({MAX_FIDELITY_BLOCKS}) reached "
                f"with no progress (same response hash). Letting through."
            )
            self.agent.data["tool_data_anchors"] = []
            self.agent.data["_fidelity_block_count"] = 0
            self.agent.data["_fidelity_warned_this_turn"] = False
            self.agent.data["_fidelity_last_blocked_hash"] = ""
            return

        is_retry = self.agent.data.get("_fidelity_warned_this_turn", False)
        logger.info(
            f"[RESPONSE FIDELITY GATE] Found {len(anchors)} anchors to check "
            f"(retry={is_retry}, blocks={block_count})"
        )

        # Get the response text
        response_text = ""
        if isinstance(response, Response) and response.message:
            response_text = response.message
        elif hasattr(response, "message") and response.message:
            response_text = response.message

        if not response_text or len(response_text) < 10:
            return

        # Check each anchor against the response
        violations: List[dict] = []

        # NEW: Check for fabricated quoted content first (Issue #827 follow-up)
        # This catches cases where the agent includes real space IDs but
        # fabricates the actual quoted message text.
        quote_violations = self._check_fabricated_quotes(response_text, anchors)
        if quote_violations:
            violations.extend(quote_violations)
            logger.warning(
                f"[RESPONSE FIDELITY GATE] Fabricated quote(s) detected: "
                f"{[v['tool_name'] for v in quote_violations]}"
            )

        # Also run the existing anchor-based threshold check
        for anchor in anchors:
            anchor_tool = anchor.get("tool_name", "")
            key_values = anchor.get("key_values", [])
            anchor_hash = anchor.get("hash", "")

            if not key_values:
                continue

            # Issue #828 FIX: Use precise key-value presence matching instead
            # of broad category-word matching. Only trigger when the response
            # actually references THIS tool's specific data values.
            tool_mentioned = self._is_tool_data_referenced(
                anchor_tool, key_values, response_text
            )

            logger.info(
                f"[RESPONSE FIDELITY GATE] Anchor '{anchor_tool}': "
                f"mentioned={tool_mentioned}, hash={anchor_hash[:8] if anchor_hash else 'N/A'}, "
                f"values={key_values[:2]}"
            )

            if not tool_mentioned:
                continue

            # Skip if this anchor already has a quote violation (avoid double-counting)
            if any(v.get("tool_name") == anchor_tool and v.get("type") == "fabricated_quote" for v in violations):
                continue

            # Count how many real values appear in the response
            found = 0
            missing: List[str] = []
            for val in key_values[:5]:
                if len(val) < 4:
                    continue
                if val in response_text:
                    found += 1
                else:
                    missing.append(val)

            checkable = len([v for v in key_values[:5] if len(v) >= 4])
            if checkable > 0 and found < checkable * 0.4:
                violations.append({
                    "tool_name": anchor_tool,
                    "found": found,
                    "checkable": checkable,
                    "missing": missing[:3],
                    "real_values": key_values[:5],
                    "hash": anchor_hash,
                })

        if violations:
            if is_retry:
                # SECOND ATTEMPT STILL FABRICATED — surgically fix the response
                logger.warning(
                    f"[RESPONSE FIDELITY GATE] Retry STILL fabricated! "
                    f"Surgically replacing quotes for: "
                    f"{[v['tool_name'] for v in violations]}"
                )
                if isinstance(response, Response) and response.message:
                    response.message = self._surgical_fix(
                        response.message, violations
                    )
                    logger.info(
                        "[RESPONSE FIDELITY GATE] Response text surgically corrected"
                    )
                # Clean up — we're done
                self.agent.data["tool_data_anchors"] = []
                self.agent.data["_fidelity_warned_this_turn"] = False
                self.agent.data["_fidelity_block_count"] = 0
            else:
                # FIRST ATTEMPT — block and warn
                if isinstance(response, Response):
                    response.break_loop = False

                # Update the already-streamed response in-place for seamless UX
                # Instead of removing and recreating, we clear content and show retry
                # heading so _20_live_response.py reuses the same LogItem (Issue #862)
                try:
                    loop_data = self.agent.loop_data
                    if loop_data and "log_item_response" in loop_data.params_temporary:
                        log_item = loop_data.params_temporary["log_item_response"]
                        log_item.update(
                            content="",
                            heading=f"icon://refresh {self.agent.agent_name}: Retrying (fidelity check)...",
                        )
                        logger.info(
                            "[RESPONSE FIDELITY GATE] Updated response log item in-place for retry"
                        )
                except Exception as e:
                    logger.warning(
                        f"[RESPONSE FIDELITY GATE] Could not update log item: {e}"
                    )

                warning_parts = [
                    "⚠️ DATA FIDELITY VIOLATION: Your response contains FABRICATED data.",
                    "The values you quoted do NOT match the actual tool output.",
                    "Here are the REAL values — you MUST rewrite your response using these EXACTLY:",
                    "",
                ]
                for v in violations:
                    warning_parts.append(f"Tool: {v['tool_name']}")
                    if v.get("hash"):
                        warning_parts.append(f"  Anchor hash: {v['hash'][:16]}")
                    warning_parts.append("  REAL values (copy-paste these EXACTLY):")
                    for rv in v["real_values"]:
                        warning_parts.append(f"    - {rv}")
                    warning_parts.append("")

                warning_parts.append(
                    "ONLY rewrite your response text using the REAL values above. "
                    "DO NOT re-run any tools. DO NOT re-delegate to subordinates. "
                    "DO NOT call call_subordinate or call_subordinate_batch again. "
                    "The tool results you already have are correct — "
                    "just fix the quoted text in your response to match them EXACTLY. "
                    "Do NOT use any values from your previous response — they were fabricated."
                )

                warning_msg = "\n".join(warning_parts)

                logger.warning(
                    f"[RESPONSE FIDELITY GATE] Hallucination BLOCKED in response tool: "
                    f"{[v['tool_name'] for v in violations]}"
                )

                await self.agent.hist_add_warning(message=warning_msg)

                # Keep anchors for retry verification! Don't clear them.
                self.agent.data["_fidelity_warned_this_turn"] = True

                # Hash-based progress detection (Issue #828):
                # Only increment block counter if the response is identical
                # to the previous blocked response (agent is stuck, not making
                # progress). If response changed → agent got new data → reset.
                response_hash = dedup_hash_short(response_text[:2048])
                last_hash = self.agent.data.get("_fidelity_last_blocked_hash", "")
                if last_hash and response_hash == last_hash:
                    # Same response → no progress → increment counter
                    self.agent.data["_fidelity_block_count"] = block_count + 1
                    logger.info(
                        f"[RESPONSE FIDELITY GATE] No progress (same hash {response_hash[:8]}), "
                        f"block count: {block_count + 1}/{MAX_FIDELITY_BLOCKS}"
                    )
                else:
                    # Different response → agent is making progress → reset counter
                    self.agent.data["_fidelity_block_count"] = 1
                    logger.info(
                        f"[RESPONSE FIDELITY GATE] Progress detected (new hash {response_hash[:8]}), "
                        f"block count reset to 1/{MAX_FIDELITY_BLOCKS}"
                    )
                self.agent.data["_fidelity_last_blocked_hash"] = response_hash
        else:
            if anchors:
                logger.info(
                    "[RESPONSE FIDELITY GATE] Response fidelity verified — real values present"
                )
            self.agent.data["tool_data_anchors"] = []
            self.agent.data["_fidelity_warned_this_turn"] = False
            self.agent.data["_fidelity_block_count"] = 0
            self.agent.data["_fidelity_last_blocked_hash"] = ""

    def _is_tool_data_referenced(
        self,
        tool_name: str,
        key_values: List[str],
        response_text: str,
    ) -> bool:
        """Check if the response actually references this tool's data.

        Issue #828 FIX: Instead of broad word matching ("chat" in response),
        use precise checks:
        1. Exact tool name appears in response (normalized)
        2. Any key_value (>= 4 chars) appears in response

        This prevents false positives where the response mentions "Google Chat"
        as a category name without actually quoting any tool data.
        """
        tool_lower = tool_name.lower().replace("-", "_")
        response_lower = response_text.lower().replace("-", "_")

        # Check 1: Exact tool name match (full or suffix)
        if tool_lower in response_lower:
            return True
        tool_suffix = tool_name.split(".")[-1].lower().replace("-", "_")
        if len(tool_suffix) >= 8 and tool_suffix in response_lower:
            return True

        # Check 2: Any key_value (>= 4 chars) appears verbatim in response
        for val in key_values:
            if len(val) >= 4 and val in response_text:
                return True

        return False

    def _extract_quoted_strings(self, text: str) -> List[str]:
        """Extract quoted strings from response text.

        Finds text inside double quotes and single quotes that are
        likely to be claimed verbatim content (>= 10 chars).
        Filters out common short phrases that aren't verbatim claims.
        """
        quotes: List[str] = []

        # Double-quoted strings
        double_quoted = re.findall(r'"([^"]{10,})"', text)
        quotes.extend(double_quoted)

        # Single-quoted strings (but not apostrophes in words)
        single_quoted = re.findall(r"(?<![a-zA-Z])'([^']{10,})'(?![a-zA-Z])", text)
        quotes.extend(single_quoted)

        return quotes

    def _check_fabricated_quotes(
        self,
        response_text: str,
        anchors: List[dict],
    ) -> List[dict]:
        """Check if quoted text in the response is fabricated.

        Extracts text inside quotation marks and verifies that each quote
        matches at least one key_value from the anchors. If a quote claims
        to be verbatim content but doesn't match any anchor → fabricated.

        Only checks anchors from tools that are likely to contain message
        content (google-chat, forgejo comments, etc.).

        Returns list of violation dicts for fabricated quotes.
        """
        # Only check MCP tools that return message/text content
        content_tool_prefixes = ["google-chat", "google_chat", "forgejo"]
        content_anchors = [
            a for a in anchors
            if any(a.get("tool_name", "").lower().startswith(p) for p in content_tool_prefixes)
            and a.get("key_values")
        ]
        if not content_anchors:
            return []

        # Extract quoted strings from response
        quotes = self._extract_quoted_strings(response_text)
        if not quotes:
            return []

        # Collect ALL key_values from ALL content anchors
        all_key_values: List[str] = []
        for anchor in content_anchors:
            all_key_values.extend(anchor.get("key_values", []))

        violations: List[dict] = []

        # Build proximity keywords for each content anchor
        # A quote is only suspicious if it appears near tool-related text
        anchor_proximity_keywords: dict = {}
        for anchor in content_anchors:
            tool_name = anchor.get("tool_name", "")
            keywords = set()
            if "google" in tool_name.lower() or "chat" in tool_name.lower():
                keywords.update(["google chat", "google-chat", "google_chat", "spaces/", "gchat"])
            if "forgejo" in tool_name.lower():
                keywords.update(["forgejo", "issue", "comment"])
            # Always include the full tool name
            keywords.add(tool_name.lower())
            anchor_proximity_keywords[tool_name] = keywords

        for quote in quotes:
            quote_clean = quote.strip()
            if len(quote_clean) < 10:
                continue

            # Check if this quote matches ANY key_value from ANY anchor
            # Uses substring overlap: quote contains a key_value substring
            # OR a key_value contains the quote as a substring
            is_real = False
            for kv in all_key_values:
                if len(kv) < 8:
                    continue
                # Check if key_value is a substantial substring of the quote
                # or the quote is a substantial substring of the key_value
                if kv in quote_clean or quote_clean in kv:
                    is_real = True
                    break
                # Also check for significant word overlap (>= 60% of words match)
                kv_words = set(kv.lower().split())
                quote_words = set(quote_clean.lower().split())
                if len(kv_words) >= 3 and len(quote_words) >= 3:
                    overlap = len(kv_words & quote_words)
                    if overlap >= len(kv_words) * 0.6:
                        is_real = True
                        break

            if not is_real:
                # CONTEXTUAL PROXIMITY CHECK: Only flag as fabricated if this
                # quote appears near a reference to the tool category.
                # E.g., "Example Domain" near "Browser" shouldn't trigger
                # a Google Chat fabrication violation.
                quote_pos = response_text.find(quote_clean)
                if quote_pos == -1:
                    continue

                # Check if any anchor's keywords appear within 200 chars of the quote
                nearby_text = response_text[max(0, quote_pos - 200):quote_pos + len(quote_clean) + 200].lower()

                matched_anchor = None
                for anchor in content_anchors:
                    tool_name = anchor.get("tool_name", "")
                    keywords = anchor_proximity_keywords.get(tool_name, set())
                    if any(kw in nearby_text for kw in keywords):
                        matched_anchor = anchor
                        break

                if matched_anchor is None:
                    # Quote is not near any content-tool reference → not a fabrication
                    # It's from a different category (Browser, SearxNG, etc.)
                    continue

                violations.append({
                    "tool_name": matched_anchor.get("tool_name", "unknown"),
                    "type": "fabricated_quote",
                    "fabricated_text": quote_clean[:100],
                    "real_values": matched_anchor.get("key_values", [])[:5],
                    "hash": matched_anchor.get("hash", ""),
                    "found": 0,
                    "checkable": 1,
                    "missing": [quote_clean[:50]],
                })
                logger.warning(
                    f"[RESPONSE FIDELITY GATE] Fabricated quote detected: "
                    f"'{quote_clean[:50]}...' does not match any anchor key_value"
                )

        return violations

    def _surgical_fix(self, text: str, violations: List[dict]) -> str:
        """Replace fabricated quoted text with real values from anchors."""
        for v in violations:
            real_values = v.get("real_values", [])
            # Find the best real value to use as the quote (longest non-ID value)
            best_quote = ""
            for rv in real_values:
                if len(rv) > len(best_quote) and not rv.startswith("spaces/"):
                    best_quote = rv

            if not best_quote:
                continue

            # Find quoted strings near tool/category mentions and replace them
            # Pattern: find text in quotes that doesn't match any real value
            quote_patterns = [
                # "quoted text" after Quote/quote keyword — use group(1)
                r'[Qq]uote[^"]*"([^"]{5,})"',
                # > "quoted text" blockquote pattern — use group(1)
                r'(?:>\s*\\?"|\\\\")\s*([^"\\]{5,}?)(?:\\?"|\\\")',
                # Generic "some fabricated text" — use group(1)
                r'"([^"]{10,}?)"',
            ]

            for pattern in quote_patterns:
                matches = list(re.finditer(pattern, text))
                for match in matches:
                    matched_text = (match.group(1) if match.lastindex else match.group(0)).strip()
                    # Check if this quoted text is NOT a real value
                    is_real = any(
                        rv in matched_text or matched_text in rv
                        for rv in real_values
                        if len(rv) >= 4
                    )
                    if not is_real and len(matched_text) > 5:
                        text = text.replace(matched_text, best_quote, 1)
                        logger.info(
                            f"[RESPONSE FIDELITY GATE] Replaced fabricated "
                            f"'{matched_text[:40]}...' with real value"
                        )
                        break  # Only replace the first match per violation
                break  # Only try the first matching pattern

        return text
