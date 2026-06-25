from __future__ import annotations
import logging
import re
from difflib import SequenceMatcher
from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.response_tool")

# Issue #1105: Near-duplicate similarity threshold
# Responses with >85% text similarity to the last response are treated as
# near-duplicates and rejected, preventing the agent from reformulating
# the same content with minor whitespace/formatting changes to bypass
# exact-match duplicate detection.
NEAR_DUPLICATE_THRESHOLD = 0.95  # RCA-340: raised from 0.85 — targeted fixes should pass

# RCA-332: Maximum consecutive response rejections before force-accepting.
# When an agent has genuinely completed its work and keeps submitting the same
# completion report, blocking it forever just burns tokens. After this many
# consecutive rejections, force-accept the response to let the loop exit.
# RCA-355: Lowered from 3 to 2. With 3 rejections, each LLM call between
# rejections (60-120s) could exceed the 120s idle timeout before force-accept
# fires. With 2, the agent only needs one LLM call between rejections,
# staying well within the idle window even without LLM heartbeats.
MAX_CONSECUTIVE_RESPONSE_REJECTIONS = 2

# RCA-462: Import cumulative rejection limit
from python.helpers.gate_config import MAX_TOTAL_RESPONSE_REJECTIONS

# Pattern to match {{verbatim:ANCHOR_ID}} placeholders
VERBATIM_PATTERN = re.compile(r'\{\{verbatim:([a-zA-Z0-9_-]+)\}\}')


class ResponseTool(Tool):

    def _resolve_verbatim(self, text: str) -> str:
        """Resolve all {{verbatim:ANCHOR_ID}} placeholders with real anchored data.
        
        Issue #1124: Replaces the "copy-paste and police" model with
        "make it impossible to fail". The agent uses placeholders,
        and we inject the real data at render time.
        
        Returns the text with all placeholders resolved.
        """
        if not text or "{{verbatim:" not in text:
            return text
        
        # Get stored anchors from agent data
        anchors = []
        if self.agent:
            anchors = self.agent.data.get("tool_data_anchors", [])
        
        # Build anchor_id → anchor lookup
        anchor_map = {}
        for anchor in anchors:
            aid = anchor.get("anchor_id")
            if aid:
                anchor_map[aid] = anchor
        
        def replace_match(match):
            anchor_id = match.group(1)
            anchor = anchor_map.get(anchor_id)
            
            if anchor is None:
                logger.warning(
                    f"[VERBATIM] Anchor '{anchor_id}' not found in stored anchors. "
                    f"Available: {list(anchor_map.keys())}"
                )
                return f"[Data anchor '{anchor_id}' not found — data may have expired]"
            
            key_values = anchor.get("key_values", [])
            if not key_values:
                return f"[Anchor '{anchor_id}' contains no data]"
            
            # Format the key_values for inline use
            # Single value → inline, multiple → bulleted list
            if len(key_values) == 1:
                rendered = key_values[0]
            else:
                rendered = "\n".join(f"- {v}" for v in key_values)
            
            logger.info(
                f"[VERBATIM] Resolved {{{{verbatim:{anchor_id}}}}} → "
                f"{len(key_values)} values from {anchor.get('tool_name', 'unknown')}"
            )
            return rendered
        
        resolved = VERBATIM_PATTERN.sub(replace_match, text)
        
        # Count how many were resolved
        original_count = len(VERBATIM_PATTERN.findall(text))
        if original_count > 0:
            logger.info(
                f"[VERBATIM] Resolved {original_count} verbatim placeholder(s) in response"
            )
        
        return resolved

    async def execute(self, **kwargs):
        # Robust argument extraction — LLMs pass args in many formats:
        # {"text": "..."}, {"message": "..."}, {"message": {"text": "..."}}, etc.
        try:
            if "text" in self.args:
                text = self.args["text"]
            elif "message" in self.args:
                text = self.args["message"]
            else:
                # Fallback: take the first string value from args
                text = next((v for v in self.args.values() if isinstance(v, str)), "")
            # If text ended up as a dict (nested args), extract the string
            if isinstance(text, dict):
                text = text.get("text", text.get("message", str(text)))
            text = str(text) if text else ""
        except Exception as e:
            logger.warning(f"[RESPONSE] Failed to extract text from args {type(self.args)}: {e}")
            text = str(self.args) if self.args else ""

        # Issue #1124: Resolve {{verbatim:ANCHOR_ID}} placeholders BEFORE
        # any other processing. This injects real anchored data into the
        # response, eliminating copy-paste errors and fidelity gate retries.
        if self.agent and text:
            text = self._resolve_verbatim(text)

        # P0-3: Build-pass warning injection for subordinate agents.
        # When a subordinate calls response directly (bypassing mark_complete),
        # and the build was never verified, prepend a structured warning so the
        # orchestrator can classify this as potentially incomplete work.
        # The response is NOT blocked (to prevent infinite loops).
        # RCA-Ph3-R2: Skip sentinel when budget is expiring (gate spiral prevention)
        # or when the build-pass gate budget is exhausted (prevents the
        # orchestrator from re-delegating endlessly due to BUILD_NOT_VERIFIED).
        if self.agent and self.agent.number != 0:
            budget_expiring = self.agent.data.get("_budget_expiring", False)
            if not budget_expiring:
                project_dir = self.agent.data.get("_active_project_dir", "")
                if project_dir and not self.agent.data.get("_build_pass_verified", False):
                    from python.helpers.build_loop_detector import BuildLoopDetector
                    from python.helpers.universal_gate_budget import gate_check
                    detector = self.agent.data.get("_build_loop_detector")
                    # Only inject sentinel if build actually failed (not just untested)
                    # AND the gate budget hasn't been exhausted (prevents spiral)
                    if (isinstance(detector, BuildLoopDetector)
                            and detector.get_failure_count(project_dir) > 0
                            and not gate_check(self.agent.data, "build_not_verified_sentinel", threshold=2)):
                        text = (
                            "[BUILD_NOT_VERIFIED] Build was never verified as "
                            "passing for this project. " + (text or "")
                        )
                        logger.warning(
                            f"[RESPONSE] {self.agent.agent_name}: Build not verified "
                            f"for {project_dir} — prepending warning sentinel"
                        )

        # Issue #1105: Duplicate / near-duplicate response detection
        # Prevents monologue loops where agent resends same/similar content
        # RCA-340 ISSUE-2: Skip near-dup check when gate retry is active —
        # the agent is responding to quality gate feedback, so similar content
        # is expected. This prevents the destructive interaction between the
        # near-dup detector and quality gate rejection loops.
        if self.agent:
            # RCA-ITR37 FIX-2b: When _budget_expiring is set (timeout grace
            # window active), skip ALL near-dup/duplicate checks. The agent
            # is wrapping up before hard timeout — let it deliver immediately.
            if self.agent.data.get("_budget_expiring", False):
                logger.info(
                    f"[RESPONSE] {self.agent.agent_name}: _budget_expiring=True — "
                    f"skipping near-dup checks, force-accepting wrap-up response"
                )
                self.agent.data["_last_response_content"] = text
                self.agent.data["_consecutive_response_rejections"] = 0
                return Response(message=text, break_loop=True)

            gate_retry_active = self.agent.data.get("_gate_retry_active", False)
            _last_response = self.agent.data.get("_last_response_content")
            if not gate_retry_active and _last_response is not None and text:

                is_duplicate = False
                rejection_msg = ""

                # Exact duplicate check
                if _last_response == text:
                    is_duplicate = True
                    rejection_msg = (
                        "DUPLICATE RESPONSE REJECTED: You already sent this exact response. "
                        "Provide new, different content or use a different approach."
                    )
                else:
                    # Near-duplicate check (similarity-based)
                    similarity = SequenceMatcher(None, _last_response, text).ratio()
                    if similarity >= NEAR_DUPLICATE_THRESHOLD:
                        is_duplicate = True
                        rejection_msg = (
                            f"NEAR-DUPLICATE RESPONSE REJECTED (similarity: {similarity:.0%}): "
                            f"Your response is too similar to the one you just sent. "
                            f"Either take a different action to address the gate's feedback, "
                            f"or provide substantially different content."
                        )

                if is_duplicate:
                    # RCA-332: Track consecutive rejections and force-accept after limit.
                    # Without this, agents that have genuinely completed their work but
                    # can't produce a sufficiently different completion report will burn
                    # 30+ LLM calls in an infinite rejection loop.
                    rej_count = self.agent.data.get("_consecutive_response_rejections", 0) + 1
                    self.agent.data["_consecutive_response_rejections"] = rej_count

                    # RCA-462 Fix 1: Increment CUMULATIVE counter (NEVER resets).
                    # Unlike _consecutive_response_rejections which resets on force-accept,
                    # this counter persists across force-accept cycles to prevent the
                    # infinite spiral: force-accept → fresh dup → reject → force-accept.
                    total_rej = self.agent.data.get("_total_response_rejections", 0) + 1
                    self.agent.data["_total_response_rejections"] = total_rej



                    # RCA-462 Fix 1: If cumulative rejections exceed limit,
                    # set _budget_expiring so the next response call skips
                    # all dup checks (line 134 guard) and the completion gate's
                    # sentinel guard stands down immediately.
                    if total_rej >= MAX_TOTAL_RESPONSE_REJECTIONS:
                        logger.warning(
                            f"[RESPONSE_BUDGET_EXPIRE] {self.agent.agent_name}: "
                            f"Cumulative response rejections ({total_rej}) >= "
                            f"MAX_TOTAL_RESPONSE_REJECTIONS ({MAX_TOTAL_RESPONSE_REJECTIONS}). "
                            f"Setting _budget_expiring=True to break infinite spiral."
                        )
                        self.agent.data["_budget_expiring"] = True
                        # Force-accept immediately
                        self.agent.data["_consecutive_response_rejections"] = 0
                        self.agent.data["_last_response_content"] = text
                        return Response(message=text, break_loop=True)

                    if rej_count >= MAX_CONSECUTIVE_RESPONSE_REJECTIONS:
                        logger.warning(
                            f"[RESPONSE_FORCE_ACCEPT] {self.agent.agent_name}: "
                            f"{rej_count} consecutive response rejections. "
                            f"Force-accepting to prevent infinite burn loop."
                        )
                        self.agent.data["_consecutive_response_rejections"] = 0
                        # RCA-462: Do NOT reset _total_response_rejections here
                        self.agent.data["_last_response_content"] = text
                        # U-13: Prepend sentinel so orchestrator can classify
                        # this as incomplete work, not a successful completion.
                        # Without this tag, the orchestrator re-delegates the
                        # same task, causing a scaffold death spiral.
                        sentinel_text = f"[FORCE_ACCEPTED_INCOMPLETE] {text}"
                        return Response(message=sentinel_text, break_loop=True)

                    return Response(message=rejection_msg, break_loop=False)

            # Store for next check
            self.agent.data["_last_response_content"] = text
            # RCA-332: Reset rejection counter on successful (accepted) response
            self.agent.data["_consecutive_response_rejections"] = 0

        return Response(message=text, break_loop=True)

    async def before_execution(self, **kwargs):
        # self.log = self.agent.context.log.log(type="response", heading=f"{self.agent.agent_name}: Responding", content=self.args.get("text", ""))
        # don't log here anymore, we have the live_response extension now
        pass

    async def after_execution(self, response, **kwargs):
        # ── RCA-279 FIX: Fallback LogItem creation ──
        # ROOT CAUSE: _20_live_response.py creates the UI LogItem during LLM
        # streaming, but this depends on DirtyJson.parse_string() successfully
        # parsing the partial JSON stream. When streaming fails (parse timing,
        # non-streaming calls, auto-injected responses, gate force-delivery),
        # no LogItem is created, and the response is silently lost from the UI.
        #
        # 5-WHY:
        # 1. UI doesn't show final response → no LogItem of type="response" exists
        # 2. LogItem is created by streaming handler → but streaming can fail
        # 3. after_execution had NO FALLBACK → silent loss
        # 4. Architecture assumed streaming always works → no defense-in-depth
        # 5. FIX: Create LogItem here if missing and response was accepted
        if (
            response.break_loop
            and self.loop_data
            and "log_item_response" not in self.loop_data.params_temporary
            and self.agent
            and hasattr(self.agent, "context")
            and self.agent.context
            and hasattr(self.agent.context, "log")
            and self.agent.context.log
        ):
            text = self.args.get("text", self.args.get("message", ""))
            log_item = self.agent.context.log.log(
                type="response",
                heading=f"icon://chat {self.agent.agent_name}: Responding",
                content=text,
            )
            self.loop_data.params_temporary["log_item_response"] = log_item
            log_item.update(finished=True)
            logger.info(
                f"[RESPONSE_FALLBACK] Created response LogItem for "
                f"{self.agent.agent_name} (streaming missed, {len(text)} chars)"
            )

        # Issue #1107: Ghost bubble cleanup
        # At this point, tool_execute_after hooks (incl. orchestrator gate) have
        # already run. If the gate set break_loop=False, the response content was
        # already streamed to the UI by _20_live_response.py but the response was
        # rejected. Clean up the ghost bubble so the user doesn't see it.
        if self.loop_data and "log_item_response" in self.loop_data.params_temporary:
            log = self.loop_data.params_temporary["log_item_response"]
            if response.break_loop:
                # Normal accepted response — mark as finished
                log.update(finished=True)
            else:
                # Gate-blocked response — clear the ghost bubble content
                # and remove from params_temporary so next iteration creates
                # a fresh LogItem instead of updating the cleared one
                log.update(
                    content="",
                    heading="icon://refresh Response blocked by quality gate — retrying...",
                    type="info",
                    finished=True
                )
                del self.loop_data.params_temporary["log_item_response"]
                # 5-WHY Fix #3: Clear near-duplicate state when gate blocks
                # The agent is forced to retry by a quality gate. Its next
                # response will naturally be similar (same data, same task).
                # Without this, the near-duplicate detector incorrectly
                # penalizes the retry for ≥85% similarity, creating a cascade
                # of blocks: gate block → near-dup block → near-dup block.
                if self.agent:
                    self.agent.data.pop("_last_response_content", None)

        # ── RC-2 FIX: Gate rejection context-bloat cap ──
        # After 3 consecutive gate rejections, compress new rejection messages
        # into a 1-liner to prevent context pollution that causes the 24x
        # response loop. See: docs/rca/rca_rc2_response_loop.md
        if self.agent:
            if response.break_loop:
                # Successful response — reset supervisor redirect counter
                from python.helpers.supervisor_redirect_cap import (
                    reset_redirect_counter,
                )
                reset_redirect_counter(self.agent.data)
            elif response.message:
                # Gate blocked — track rejection count for context compression
                count = self.agent.data.get("_gate_rejection_count", 0) + 1
                self.agent.data["_gate_rejection_count"] = count
                if count > 3:
                    # Compress: replace full JSON block with short counter
                    inject_message = (
                        f"⛔ Gate rejection #{count}. Same issues persist. "
                        f"Take a DIFFERENT approach."
                    )
                    logger.info(
                        f"[CONTEXT_CAP] Compressed gate rejection #{count} "
                        f"(original: {len(response.message)} chars → {len(inject_message)} chars)"
                    )
                else:
                    # Full injection for first N rejections
                    inject_message = response.message

                # F-1 (ITR-21): Append UniversalErrorManager guidance to
                # gate rejection messages. This gives the agent actionable
                # context about what's failing and how to change approach.
                # Without this, the agent retries the same strategy endlessly.
                try:
                    from python.helpers.universal_error_manager import UniversalErrorManager
                    uem = UniversalErrorManager(self.agent)
                    retry_info = uem.get_retry_decision(response.message[:500])
                    guidance = retry_info.get("guidance", "")
                    if guidance:
                        inject_message = inject_message + f"\n\n💡 **Error Manager Guidance**: {guidance}"
                except Exception:
                    pass  # Don't crash response flow if UEM fails

                # 5-WHY Fix #4: Inject gate block message into conversation history
                # ROOT CAUSE: When the gate set break_loop=False, response.message
                # contained the structured block feedback (passed_checks, failing check,
                # targeted instructions) but it was NEVER added to the LLM's history.
                # The LLM's next turn had NO context about WHY it was blocked, causing
                # full re-delegation instead of targeted fixes.
                # FIX: Add the block message as a tool result so the LLM sees it.
                await self.agent.hist_add_tool_result(
                    self.name,
                    inject_message,
                    success=False,
                )
                # RCA-321: Inject a SEPARATE warning to reinforce the rejection.
                # ROOT CAUSE: The tool result alone is insufficient — the LLM
                # interprets tool_name="response" + result text as a normal
                # tool output, not a REJECTION. After 3 identical retries, the
                # semantic repeat detector triggers → escape hatch.
                # FIX: A distinct warning via fw.warning.md gives the LLM an
                # unambiguous structural signal that its response was REJECTED.
                # This is why the supervisor redirect (which uses hist_add_warning)
                # succeeds where the gate block alone fails.
                await self.agent.hist_add_warning(
                    message=(
                        "⛔ YOUR RESPONSE WAS REJECTED by the quality gate. "
                        "Your attempt to deliver was NOT accepted. "
                        "You MUST take a DIFFERENT action before calling "
                        "the response tool again. Do NOT repeat the same "
                        "response content. Read the gate feedback above and "
                        "address the specific failing check."
                    )
                )
                return  # Skip the old injection below

        # Fallback: inject full message if agent is not available (shouldn't happen)
        if not response.break_loop and self.agent and response.message:
            await self.agent.hist_add_tool_result(
                self.name,
                response.message,
                success=False,
            )
