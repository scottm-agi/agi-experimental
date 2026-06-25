"""
Boomerang Context — Reliable Completion Signaling Across Delegation Chains

When a parent agent delegates work via call_subordinate or call_subordinate_batch,
the original user's completion requirements (markers, format instructions, etc.)
are lost at each delegation boundary. This module provides a one-shot reminder
that is appended to delegation tool results, ensuring the parent re-reads and
honours the original user's completion instructions.

The context is READ-ONLY text appended to the tool result string. It does NOT
trigger any additional processing or loops.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.boomerang_context")

# Maximum characters to extract from the user's original message tail.
# Completion instructions (e.g., "end with [[SMOKE_TEST_COMPLETE]]") are
# almost always in the last paragraph.
_TAIL_CHARS = 500

# Unique marker used to detect existing boomerang blocks for dedup.
_BOOMERANG_MARKER = "⚠️ **BOOMERANG — ORIGINAL USER COMPLETION REQUIREMENTS:**"


# Orchestrator agents that should NOT get "Do NOT delegate further"
# - multiagentdev/alex: top-level orchestrators
# - default: sub-orchestrator that delegates to code/frontend/researcher
#   (iter 71 fix: Default was told to stop after first sub-delegation,
#    killing the entire build pipeline before any code was written)
# FIX-020: Use centralized profile registry instead of hardcoded names
from python.helpers.profile_registry import ORCHESTRATOR_PROFILES
_ORCHESTRATOR_AGENTS = ORCHESTRATOR_PROFILES


def _build_action_block(calling_agent_name: str = "", all_tasks_succeeded: bool = True, agent_data: dict = None) -> str:
    """
    Build the action block for the boomerang context based on agent type
    and batch task results.

    Args:
        calling_agent_name: Name of the agent receiving the boomerang.
        all_tasks_succeeded: Whether all batch tasks passed (used to decide
                             if orchestrators should re-delegate or compile results).
        agent_data: Optional agent.data dict — when provided, checks the
                    requirements ledger for incomplete items and appends a
                    warning if requirements are still outstanding.

    Returns:
        Action instruction string.
    """
    agent_name_lower = calling_agent_name.lower().strip()
    is_orchestrator = any(orch in agent_name_lower for orch in _ORCHESTRATOR_AGENTS)

    # ── Ledger-Awareness: Check for unfulfilled requirements ──
    # RCA-ITR5 ISSUE-3: Skip in planning-only mode — implementation requirements
    # are EXPECTED to be incomplete during planning phases (0–2.7).
    ledger_warning = ""
    planning_only = agent_data.get("_planning_only", False) if agent_data else False
    if agent_data and is_orchestrator and not planning_only:
        try:
            from python.helpers.requirements_ledger import get_incomplete_requirements
            incomplete = get_incomplete_requirements(agent_data)
            if incomplete:
                ids = ", ".join(r["id"] for r in incomplete[:10])
                ledger_warning = (
                    f"\n\n⚠️ REQUIREMENTS UNFULFILLED: {len(incomplete)} requirements "
                    f"are NOT yet completed: {ids}. "
                    f"You MUST delegate remaining work before calling response. "
                    f"Do NOT report SUCCESSFULLY when requirements remain outstanding."
                )
        except Exception as e:
            logger.warning(f"[BOOMERANG] Ledger completeness check failed: {e}")

    if is_orchestrator:
        if all_tasks_succeeded:
            if ledger_warning:
                # ── F-3 FIX: Requirements INCOMPLETE → do NOT say "compile FINAL" ──
                # The previous code said "Compile your FINAL response" as the base
                # and then appended a contradicting warning "MUST delegate remaining".
                # This contradiction caused the LLM to sometimes compile a FINAL
                # response even with outstanding requirements (F-3 premature delivery).
                # Now the base message itself says "continue delegating".
                base = (
                    "Subordinate tasks completed, but REQUIREMENTS ARE STILL OUTSTANDING. "
                    "You MUST continue delegating to specialist agents to fulfill the "
                    "remaining requirements.\n"
                    "**Do NOT call `response` yet.** Do NOT re-run tasks that already succeeded."
                )
                return base + ledger_warning
            else:
                # All tasks passed AND all requirements fulfilled — compile the final answer.
                base = (
                    "All subordinate tasks completed SUCCESSFULLY. "
                    "Compile your FINAL response now using ALL the results above.\n"
                    "**Call `response` with your synthesized answer.** Do NOT re-run "
                    "tasks that already succeeded."
                )
                return base
        else:
            # Some tasks failed — orchestrator should re-delegate failed ones only.
            base = (
                "Some subordinate tasks FAILED. Review the errors above and "
                "re-delegate ONLY the failed tasks to specialist agents.\n"
                "**Do NOT re-run tasks that already succeeded.** Focus on fixing "
                "the failures, then compile your final response."
            )
            return base + ledger_warning

    else:
        return (
            "All subordinate work is complete. You MUST now compose your FINAL "
            "answer using the results above.\n"
            "**Do NOT delegate further.** Call the `response` tool directly with "
            "your synthesized answer."
        )


def get_boomerang_context(
    agent: "Agent",
    calling_agent_name: str = "",
    all_tasks_succeeded: bool = True
) -> str:
    """
    Walk up the superior chain to the root agent and extract the original
    user's completion requirements from the tail of their first message.

    For orchestrator agents, the boomerang tells them to either compile
    results (if all tasks succeeded) or continue re-delegating failed tasks.

    Args:
        agent: The current agent requesting boomerang context.
        calling_agent_name: Name of the calling agent (for orchestrator detection).
        all_tasks_succeeded: Whether all batch tasks completed successfully.

    Returns a formatted reminder string to append to tool results, or ""
    if no user message is found.
    """
    # Walk up to the root agent (the one that received the user's prompt)
    root = agent
    depth = 0
    max_depth = 10  # Safety: prevent infinite loops in malformed chains
    while depth < max_depth:
        superior = root.get_data("_superior")
        if superior is None:
            break
        root = superior
        depth += 1

    # Extract the LATEST non-AI message from root's history.
    # Completion instructions (markers, format) are in the most recent user
    # message, not the first. Using the first caused stale boomerang context
    # in multi-turn conversations (root cause of duplicate response issue).
    latest_user_content = _get_latest_user_message(root)
    if not latest_user_content:
        return ""

    # Take the tail — completion instructions live at the end
    tail = latest_user_content[-_TAIL_CHARS:] if len(latest_user_content) > _TAIL_CHARS else latest_user_content

    logger.debug(
        f"Boomerang context extracted from root agent "
        f"(depth={depth}, tail_len={len(tail)})"
    )

    action_block = _build_action_block(
        calling_agent_name=calling_agent_name,
        all_tasks_succeeded=all_tasks_succeeded,
        agent_data=getattr(agent, 'data', None),
    )

    return (
        "\n\n---\n"
        "⚠️ **BOOMERANG — ORIGINAL USER COMPLETION REQUIREMENTS:**\n"
        f"{action_block}\n\n"
        "The user's original request ended with:\n"
        f"> {tail}\n\n"
        "Include any requested markers, format, or sign-off from the original "
        "request in your response.\n"
        "---"
    )


def get_original_user_message(agent: "Agent") -> str:
    """
    Walk up the superior chain to the root agent and return the FULL content
    of the first user message. This is used to forward full context to
    orchestrator profiles during delegation.

    Returns the full message string or "" if not found.
    """
    # Walk up to root
    root = agent
    depth = 0
    max_depth = 10
    while depth < max_depth:
        superior = root.get_data("_superior")
        if superior is None:
            break
        root = superior
        depth += 1

    return _get_first_user_message(root)


def _get_first_user_message(agent: "Agent") -> str:
    """
    Extract the content of the first user (non-AI) message from the agent's
    history. Returns the string content or "" if not found.
    
    Used by get_original_user_message() for full task context forwarding.
    NOT used for boomerang completion requirements (use _get_latest_user_message).
    """
    try:
        for msg in agent.history.messages_all:
            if not msg.ai:
                return _extract_message_content(msg.content)
    except Exception as e:
        logger.warning(f"Failed to extract first user message: {e}")
    return ""


def _is_framework_warning(content: str) -> bool:
    """Check if a message is a framework-injected warning, not a real user message.

    These messages pollute the boomerang tail when captured as "latest user
    message," causing the orchestrator to misinterpret framework warnings as
    user instructions (root cause of iteration 213 re-delegation loop).

    Framework warning patterns:
    - system_warning dict/JSON: ``{'system_warning': '...'}``
    - SUPERVISOR NUDGE: ``[SUPERVISOR NUDGE]`` prefix
    - Same-message warning: ``You have sent the same message again``
    - Stall detected: ``STALL DETECTED:`` or ``STILL STALLED:``
    - Supervisor recovery: ``SUPERVISOR RECOVERY:``
    """
    if not content:
        return False
    # Fast checks first (most common patterns)
    content_lower = content.strip().lower()
    # system_warning dict notation: {'system_warning': '...'}
    if content_lower.startswith(("{'system_warning'", '{"system_warning"')):
        return True
    # SUPERVISOR NUDGE messages
    if content.strip().startswith("[SUPERVISOR"):
        return True
    # Same-message framework warning
    if "you have sent the same message again" in content_lower:
        return True
    # Dead-agent stall nudges
    if content_lower.startswith(("stall detected:", "still stalled:", "supervisor recovery:")):
        return True
    # Supervisor re-delegation requests
    if "⚠️ SUPERVISOR RE-DELEGATION REQUEST:" in content:
        return True
    return False


def _get_latest_user_message(agent: "Agent") -> str:
    """
    Extract the content of the LATEST genuine user (non-AI) message from the
    agent's history. Returns the string content or "" if not found.

    Used by get_boomerang_context() to extract completion requirements from
    the most recent user message, not the stale first message.

    IMPORTANT: Skips framework-injected warnings (system_warning, SUPERVISOR
    NUDGE, same-message warnings) that pollute the boomerang tail. Only genuine
    user messages are captured.

    Root cause (Iteration 213): The boomerang captured system_warning text
    "You have sent the same message again. You have to do something else!" as
    the "original user request," which confused the model into re-delegating
    instead of calling response.
    """
    try:
        latest = ""
        for msg in agent.history.messages_all:
            if not msg.ai:
                content = _extract_message_content(msg.content)
                if content and not _is_framework_warning(content):
                    latest = content
        return latest
    except Exception as e:
        logger.warning(f"Failed to extract latest user message: {e}")
    return ""


def _extract_message_content(content) -> str:
    """Extract string content from a message content field.
    
    Handles str, list (multi-part), and dict content types.
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]))
        return "\n".join(parts) if parts else ""
    elif isinstance(content, dict) and "text" in content:
        return str(content["text"])
    elif content is not None:
        return str(content)
    return ""


def strip_boomerang(text: str) -> str:
    """
    Remove ALL boomerang blocks from a text string.
    Used to prevent accumulation when results pass through multiple
    delegation boundaries.
    """
    if _BOOMERANG_MARKER not in text:
        return text
    # Split on the boomerang separator and keep only the first part
    # Each boomerang starts with "\n\n---\n⚠️ **BOOMERANG"
    import re
    # Remove all boomerang blocks (the marker through the closing ---)
    pattern = r'\n*---\n' + re.escape(_BOOMERANG_MARKER) + r'.*?(?=\n---\n|$)'
    cleaned = re.sub(pattern, '', text, flags=re.DOTALL)
    # Also clean trailing ---\n that may remain
    cleaned = re.sub(r'\n---\s*$', '', cleaned)
    return cleaned.rstrip()


def has_boomerang(text: str) -> bool:
    """Check if text already contains a boomerang block."""
    return _BOOMERANG_MARKER in text


def strip_completion_markers(text: str) -> str:
    """
    Remove [[COMPLETION_MARKER]] style tokens from subordinate results.

    These markers (e.g., [[SMOKE_TEST_COMPLETE]], [[DONE]]) are instructions
    for the *root* agent's final response. When subordinate results leak them
    back through the delegation chain, the test runner or user may detect
    them prematurely.

    Only strips double-bracket markers: [[WORD_CHARS]].
    Single brackets like [list] are preserved.
    """
    if not text:
        return text
    import re
    # Match [[UPPER_CASE_WORDS]] — typical completion markers
    cleaned = re.sub(r'\[\[[A-Z_]+\]\]', '', text)
    # Clean up any leftover blank lines from removed markers
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def is_error_result(result, structured: bool = False):
    """
    Detect known error patterns in batch task results.

    RCA-358 V-7: Refactored to L1/L2 helper pattern.
    - L1 (deterministic): Pattern matching detects known error signals
    - L2 (contextual): Checks result length + structure to prevent false positives
      on long valid results that mention errors in context (e.g., reporting a fix)

    Sentinels ([CANCELLED], [ITERATION_LIMIT], etc.) are ALWAYS errors regardless
    of result length — these are Tier 1 (structural) signals.

    Args:
        result: The subordinate's result string.
        structured: If True, returns a dict with {is_error, confidence, reason, patterns_matched}
                    If False (default), returns bool for backward compatibility.

    Returns:
        bool (default) or dict (when structured=True).
    """
    def _verdict(is_error: bool, confidence: float, reason: str, patterns: list):
        if structured:
            return {
                "is_error": is_error,
                "confidence": confidence,
                "reason": reason,
                "patterns_matched": patterns,
            }
        return is_error

    # ── Null/empty: Always error ──
    if result is None:
        return _verdict(True, 1.0, "Result is None", [])
    if not isinstance(result, str):
        result = str(result)
    stripped = result.strip()
    if not stripped:
        return _verdict(True, 1.0, "Result is empty", [])

    # ── Very short error tokens ──
    if stripped.lower() in ("none", "null", "undefined", "false"):
        return _verdict(True, 1.0, f"Error token: '{stripped}'", [stripped.lower()])

    # ── L1: Pattern detection (signals, not decisions) ──
    # Tier 1 patterns — ALWAYS errors regardless of context (structural sentinels)
    # U-13 Fix: Use centralized sentinel_registry instead of hardcoded list
    from python.helpers.sentinel_registry import get_error_sentinels
    SENTINEL_PATTERNS = get_error_sentinels() + [
        "Hard-stopped after",
        "⚠️ Model returned empty responses after multiple retry cycles",
    ]
    matched_sentinels = [p for p in SENTINEL_PATTERNS if p in stripped]
    if matched_sentinels:
        return _verdict(True, 0.99, f"Lifecycle sentinel detected", matched_sentinels)

    # Tier 3 patterns — these are signals that need L2 contextual check
    CONTEXTUAL_PATTERNS = [
        "is required for mode:",     # maintain_memory_bank errors
        "ERROR:",                     # generic tool errors
        "FAILED:",                    # explicit failure markers
        "Traceback (most recent",    # Python tracebacks
    ]
    matched_contextual = [p for p in CONTEXTUAL_PATTERNS if p in stripped]

    # ── L2: Contextual analysis (prevents false positives) ──
    if matched_contextual:
        # Tracebacks are almost always genuine errors — they're multi-line
        # structured output, not something an agent mentions in a report
        if "Traceback (most recent" in stripped:
            # Check if the traceback IS the result (starts near the beginning)
            traceback_pos = stripped.find("Traceback (most recent")
            # If traceback is in the first 200 chars, it's likely the primary content
            if traceback_pos < 200:
                return _verdict(True, 0.95, "Traceback at start of result", matched_contextual)

        # For ERROR:/FAILED: patterns, check if the result is substantive
        # A long result (>300 chars) with structure (headers, lists) is likely
        # a valid report that MENTIONS errors, not an error itself
        result_length = len(stripped)

        # Short results (<200 chars) with error patterns are genuine errors
        if result_length < 200:
            return _verdict(True, 0.9, f"Short result with error pattern", matched_contextual)

        # Medium results (200-800 chars): check if error pattern is the dominant content
        if result_length < 800:
            # Count how many lines contain error patterns vs total lines
            lines = stripped.split("\n")
            error_lines = sum(1 for line in lines if any(p in line for p in matched_contextual))
            total_lines = len(lines)
            error_ratio = error_lines / max(total_lines, 1)

            if error_ratio > 0.5:
                return _verdict(True, 0.85, f"Majority of lines contain errors ({error_lines}/{total_lines})", matched_contextual)
            else:
                return _verdict(False, 0.6, f"Medium result with incidental error mentions ({error_lines}/{total_lines} error lines)", matched_contextual)

        # Long results (>800 chars): Almost certainly a valid report mentioning errors
        # The agent produced substantial work — error patterns are incidental
        return _verdict(False, 0.7, f"Long result ({result_length} chars) with incidental error mentions", matched_contextual)

    # No patterns matched — not an error
    return _verdict(False, 0.95, "No error patterns detected", [])

