"""
Same-Message → L1 Signal Bridge
================================

Bridges the legacy ``fw.msg_repeat`` same-message detection (exact string
equality in agent.py line 696) into the L1/L2 supervisor escalation pipeline.

Root Cause (Iteration 158 RCA):
    The ``fw.msg_repeat`` warning was the MOST accurate loop detector — it
    caught the 45-minute Agent B loop early. But it was a dead-end: the warning
    was injected into chat history but never fed into ``_l2_escalation_signals``,
    so the L2 supervisor had no visibility. The agent ignored the text warning
    and kept looping.

Fix:
    1. ``bridge_same_message_to_l1()`` — creates an L1 signal dict and appends
       it to ``agent.data["_l2_escalation_signals"]``.
    2. ``should_hard_stop_same_message()`` — after SAME_MESSAGE_HARD_CAP (3)
       consecutive same-message events, the agent is forcibly stopped.
    3. ``reset_same_message_counter()`` — resets the counter when the agent
       produces a different message, avoiding false positives.

Usage in agent.py:
    Called from the same-message detection block (line 696) right after
    ``hist_add_warning()``.
"""

from __future__ import annotations

import hashlib
import json as _json_module
import logging
import re
import time

logger = logging.getLogger("agix.same_message_bridge")

# After this many consecutive identical messages, hard-stop the agent.
SAME_MESSAGE_HARD_CAP = 3

# Session-wide cumulative same-message threshold (RCA-252, RCA-260).
# Tracks total same-message events across the entire session,
# regardless of whether they are consecutive. Escalates severity
# to 'critical' when this count is exceeded.
#
# RCA-260: Raised from 8 → 15. Long-running Code agents accumulate
# 10+ legitimate repetitions (build commands, git operations, npm scripts)
# that falsely triggered the cumulative hard-stop. The decay mechanism
# (maybe_decay_cumulative_counter) further prevents false positives.
CUMULATIVE_SAME_MESSAGE_THRESHOLD = 15

# RCA-263: Separate cumulative threshold for SEMANTIC same-message detection.
# Semantic matches (same tool_name + tool_args, different thoughts) are less
# precise than exact-match detection and fire more often during legitimate
# re-delegation recovery (orchestrator re-delegating with improved instructions).
# Set higher than exact-match threshold to reduce false-positive hard-stops.
SEMANTIC_CUMULATIVE_THRESHOLD = 20

# Number of distinct consecutive tools required to trigger cumulative decay
_DISTINCT_TOOLS_FOR_DECAY = 4

# Amount to decay the cumulative counter when progress is detected
_CUMULATIVE_DECAY_AMOUNT = 3


def bridge_same_message_to_l1(
    agent_data: dict,
    repeat_count: int,
    tool_name: str | None = None,
) -> dict:
    """Create an L1 escalation signal from same-message detection.

    Args:
        agent_data: The agent's ``self.data`` dict (mutable, modified in place).
        repeat_count: How many times the same message has been repeated
                      consecutively (1 = first repeat).
        tool_name: Optional name of the repeated tool. Planning tools
                   (e.g., sequential_thinking) are exempt from cumulative
                   counter inflation because repeating planning steps is
                   normal behavior during complex tasks. L1 signals still
                   fire for monitoring visibility.

    Returns:
        The signal dict that was appended to ``_l2_escalation_signals``.
    """
    # === Cumulative counter (RCA-252, RCA-280) ===
    # Tracks total same-message events across the entire session,
    # not just consecutive ones. This catches agents that alternate
    # between two identical patterns (A→B→A→B) to dodge the
    # consecutive counter.
    #
    # RCA-280: Planning tools (sequential_thinking) are exempt from
    # cumulative counter inflation. While should_hard_stop already
    # exempts them from the hard-stop DECISION, the counter was still
    # rising. After 15+ legitimate planning iterations, the agent would
    # hit CUMULATIVE_SAME_MESSAGE_THRESHOLD on its FIRST non-planning
    # tool call and get falsely hard-stopped.
    # FIX-014 §12: TDD cycle exemption — don't inflate cumulative counter
    # during active TDD cycles. The TDD cycle detector handles progress
    # tracking independently via L1/L2 detection layers.
    from python.helpers.tdd_cycle_detector import is_tdd_active
    if is_tdd_active(agent_data):
        cumulative = agent_data.get("_same_message_cumulative_count", 0)
        # Do NOT increment — TDD cycle detector handles this
    elif is_planning_tool(tool_name):
        cumulative = agent_data.get("_same_message_cumulative_count", 0)
        # Do NOT increment — planning tools are exempt
    else:
        cumulative = agent_data.get("_same_message_cumulative_count", 0) + 1
        agent_data["_same_message_cumulative_count"] = cumulative

    # P0-1: Budget-driven severity (replaces old counter thresholds)
    from python.helpers.retry_budget_bridge import decide_same_message
    budget_decision = decide_same_message(agent_data, repeat_count, tool_name)

    if budget_decision.action in ("escalate", "force_complete", "terminal") or repeat_count >= 3 or cumulative >= CUMULATIVE_SAME_MESSAGE_THRESHOLD:
        severity = "critical"
    elif repeat_count >= 2:
        severity = "high"
    else:
        severity = "warning"

    # === OVL-1d: Wire to repetition_recovery escalation ladder ===
    # The bridge DETECTS, the recovery ACTS. Each bridge fire increments
    # the recovery attempt counter and selects the appropriate escalation
    # layer (text_hint → temp_bump → condense → truncate → hard_stop).
    from python.helpers.repetition_recovery import (
        increment_attempt as _rr_increment,
        RepetitionRecoveryManager as _RRM,
    )
    recovery_attempt = _rr_increment(agent_data)
    _recovery_strategy = _RRM().get_recovery_strategy(recovery_attempt)

    signal = {
        "detector": "same_message_repeat",
        "severity": severity,
        "detail": (
            f"Agent sent the same message {repeat_count} time(s) consecutively "
            f"(cumulative session-wide: {cumulative}). "
            f"This indicates a stuck loop where the agent is repeating identical output."
        ),
        "repeat_count": repeat_count,
        "cumulative_count": cumulative,
        "recovery_action": _recovery_strategy["action"],
        "recovery_advice": _recovery_strategy.get("advice", ""),
        "recovery_attempt": recovery_attempt,
        "ts": time.time(),
    }

    # Append to the L2 escalation signals list
    if "_l2_escalation_signals" not in agent_data:
        agent_data["_l2_escalation_signals"] = []
    agent_data["_l2_escalation_signals"].append(signal)

    logger.warning(
        "Same-message L1 bridge fired: repeat_count=%d cumulative=%d severity=%s recovery=%s (attempt=%d)",
        repeat_count,
        cumulative,
        severity,
        _recovery_strategy["action"],
        recovery_attempt,
    )

    return signal



def should_hard_stop_same_message(
    repeat_count: int,
    tool_name: str | None = None,
    cumulative_count: int = 0,
    test_output_changed: bool | None = None,
    agent_data: dict | None = None,
) -> bool:
    """Return True if the agent should be forcibly stopped due to same-message loop.

    Args:
        repeat_count: Current consecutive same-message count.
        tool_name: Optional name of the repeated tool. Planning tools
                   (e.g., sequential_thinking) are exempt from hard-stops
                   because repeating planning steps is normal behavior.
        cumulative_count: Session-wide cumulative same-message count.
                          Tracks total events regardless of whether they
                          are consecutive. Added in RCA-256 to catch agents
                          that alternate between commands to reset the
                          consecutive counter.
        test_output_changed: If True, the test command's output is changing
                             between runs (agent is making progress). Exempt
                             from hard-stop. If False, output is identical
                             (agent is stuck). If None (default), no test
                             output info is available — fall through to
                             existing logic.
        agent_data: Optional agent data dict. If provided, uses budget-driven
                    decision (P0-1). If None, falls back to old counter logic.

    RCA-245 (MSR_Smoke_1777601043):
        The orchestrator was killed after 6 messages because it called
        sequential_thinking with thoughtNumber=1 three times during
        normal Phase 0 planning. Planning tools are now exempt from
        hard-stops. L1 signals still fire for monitoring visibility.

    RCA-256 (MSR_Smoke_1777761379):
        Code agent looped 10+ iters repeating `cat .gitignore && gh auth status`.
        The bridge fired severity=critical (cumulative=10) but the hard-stop
        function only checked the consecutive counter (which reset each time
        the agent switched commands). Fix: also check cumulative counter.

    why_agents_cant_fix_code.md A-1:
        Test commands (npm test, vitest, jest, pytest) with CHANGING output
        are exempt from hard-stop. The agent is making progress fixing
        failures. Only identical output triggers hard-stop.
    """
    if is_planning_tool(tool_name):
        return False
    # A-1: If test output is progressing, exempt from hard-stop
    if test_output_changed is True:
        return False
    # FIX-014 §12: TDD cycle exemption — let TDD cycle detector handle
    # stop decisions. Only exempt if TDD mode is active AND the stuck
    # counter hasn't reached the L2-confirmed maximum.
    if agent_data is not None:
        from python.helpers.tdd_cycle_detector import is_tdd_active, TDD_MAX_STUCK_CONSECUTIVE
        if is_tdd_active(agent_data):
            tdd_state = agent_data.get("_tdd_cycle_state", {})
            if tdd_state.get("stuck_count", 0) < TDD_MAX_STUCK_CONSECUTIVE:
                return False  # Let TDD cycle detector handle this
    # P0-1: If agent_data available, use budget
    if agent_data is not None:
        from python.helpers.retry_budget_bridge import decide_same_message
        decision = decide_same_message(agent_data, repeat_count, tool_name)
        return decision.action in ("escalate", "force_complete", "terminal")
    # Fallback to old logic if no agent_data
    return (
        repeat_count >= SAME_MESSAGE_HARD_CAP
        or cumulative_count >= CUMULATIVE_SAME_MESSAGE_THRESHOLD
    )


def reset_same_message_counter(agent_data: dict) -> None:
    """Reset the consecutive same-message repeat counter.

    NOTE (RCA-252): This resets only the CONSECUTIVE counters.
    The CUMULATIVE counters are intentionally preserved to track
    session-wide repeat events.

    why_agents_cant_fix_code.md A-1: Also resets semantic repeat counter
    since both track consecutive events and should be zeroed when the
    agent produces genuinely different output.

    Args:
        agent_data: The agent's ``self.data`` dict (mutable, modified in place).
    """
    agent_data["_same_message_repeat_count"] = 0
    agent_data["_semantic_repeat_count"] = 0
    # Do NOT reset _same_message_cumulative_count — it tracks session-wide totals


def maybe_decay_cumulative_counter(
    agent_data: dict,
    current_tool: str,
) -> None:
    """Decay the cumulative same-message counter when the agent demonstrates progress.

    Progress is defined as calling ``_DISTINCT_TOOLS_FOR_DECAY`` (4) distinct
    tools in a row. This proves the agent is making genuine forward progress,
    not stuck in a loop, so we reduce the cumulative counter to give it more
    runway.

    RCA-260 (MSR_Smoke_1777809361):
        Long-running Code agents accumulated 16+ cumulative same-message events
        over normal operation (build, git, npm commands). Even though consecutive
        count was only 1, the cumulative threshold (8) fired hard-stop. This
        decay mechanism prevents false positives by rewarding forward progress.

    Args:
        agent_data: The agent's ``self.data`` dict (mutable, modified in place).
        current_tool: Name of the tool being called now.
    """
    if not current_tool:
        return

    # Track recent distinct tools
    if "_recent_distinct_tools" not in agent_data:
        agent_data["_recent_distinct_tools"] = []

    distinct_tools = agent_data["_recent_distinct_tools"]

    # Only add if different from the last entry (distinct consecutive)
    if not distinct_tools or distinct_tools[-1] != current_tool:
        distinct_tools.append(current_tool)
    
    # Cap tracker length to avoid unbounded growth
    if len(distinct_tools) > 10:
        agent_data["_recent_distinct_tools"] = distinct_tools[-10:]
        distinct_tools = agent_data["_recent_distinct_tools"]

    # Check if we have enough distinct tools for decay
    unique_recent = set(distinct_tools[-_DISTINCT_TOOLS_FOR_DECAY:])
    if len(distinct_tools) >= _DISTINCT_TOOLS_FOR_DECAY and len(unique_recent) >= _DISTINCT_TOOLS_FOR_DECAY:
        # Agent is making progress — decay the cumulative counter
        cumulative = agent_data.get("_same_message_cumulative_count", 0)
        if cumulative > 0:
            new_cumulative = max(0, cumulative - _CUMULATIVE_DECAY_AMOUNT)
            agent_data["_same_message_cumulative_count"] = new_cumulative
            logger.info(
                "Cumulative same-message counter decayed: %d → %d "
                "(agent called %d distinct tools: %s)",
                cumulative,
                new_cumulative,
                len(unique_recent),
                ", ".join(unique_recent),
            )
        # Reset distinct tools tracker after decay
        agent_data["_recent_distinct_tools"] = [current_tool]


def extract_tool_signature(message: str) -> str | None:
    """Extract a canonical tool signature from a JSON tool-call message.

    Returns a deterministic string of ``tool_name|sorted(tool_args)`` that
    ignores the ``thoughts`` key. Two messages with different ``thoughts``
    but identical ``tool_name`` and ``tool_args`` will produce the same
    signature.

    For ``sequential_thinking`` calls, the volatile ``thought`` content is
    also stripped so that repeated planning calls (same thoughtNumber but
    different wording) are correctly detected as semantic repeats.

    Returns None if the message is not a valid JSON tool call.

    Root cause (Iteration 213): The orchestrator rewrote its ``thoughts``
    text slightly between re-delegations, producing a different hash for
    exact-string comparison. This reset the same-message counter. The tool
    signature strips thoughts so semantic duplicates are caught.

    Root cause (RCA 217): The code agent called ``sequential_thinking``
    with ``thoughtNumber: 1`` repeatedly, each time with slightly different
    ``thought`` text ("I will re-create..." → "I will implement..."). The
    signature included the thought text, so each call appeared unique and
    the semantic repeat checker never fired. Stripping ``thought`` from
    the signature means the structural args (thoughtNumber, totalThoughts,
    nextThoughtNeeded) determine uniqueness — re-planning from thought 1
    is correctly caught as a loop.
    """
    # RCA-452 FIX: Use json_parse_dirty() instead of json.loads().
    # json.loads() fails on markdown-fenced JSON (```json ... ```) and
    # JSON with prefix text, but process_tools_impl uses json_parse_dirty_all()
    # which handles these formats. This parser asymmetry meant tools executed
    # successfully but the loop detector was blind — the agent could run the
    # same command 19+ times without triggering semantic repeat detection.
    # Now both paths use the same dirty parser from extract_tools.
    from python.helpers.extract_tools import json_parse_dirty

    parsed = json_parse_dirty(message)
    if parsed is None:
        return None

    if not isinstance(parsed, dict):
        return None

    tool_name = parsed.get("tool_name")
    if not tool_name:
        return None

    tool_args = parsed.get("tool_args", {})

    # RCA 217 + RCA-291: For sequential_thinking, strip volatile content.
    # The planning text changes between calls even when the agent is stuck
    # re-planning the same task. Only structural args matter for loop detection:
    # - thoughtNumber: Is the agent progressing (1→2→3) or stuck (1→1→1)?
    # - totalThoughts: Is the agent refining scope (5→3) or stuck (3→3)?
    # - nextThoughtNeeded: Completion state
    # Stripped (volatile):
    # - thought: The planning text (varies with each re-plan, even when stuck)
    # RCA-291: totalThoughts is NO LONGER stripped. Changing totalThoughts
    # (e.g., 5→3) represents legitimate plan refinement. Only truly stuck
    # loops (same thoughtNumber AND same totalThoughts) should trigger.
    SEQUENTIAL_THINKING_VOLATILE = {"thought"}
    if tool_name == "sequential_thinking" and isinstance(tool_args, dict):
        tool_args = {k: v for k, v in tool_args.items() if k not in SEQUENTIAL_THINKING_VOLATILE}

    # RCA-ITR27 (RC-3): For file-editing tools, only keep the file path in
    # the signature. The search/replace/content fields change between
    # iterations (because the file evolves after each edit), but the INTENT
    # is the same: "edit this file". Without this normalization, every
    # iteration appears unique and the semantic repeat detector never fires
    # on file-editing loops.
    #
    # Normalized: path (or file_path) only.
    # Stripped (volatile): search, replace, content, new_str, old_str, etc.
    FILE_EDIT_TOOLS = {"replace_in_file", "write_to_file", "insert_content", "search_and_replace"}
    if tool_name in FILE_EDIT_TOOLS and isinstance(tool_args, dict):
        tool_args = {"path": tool_args.get("path", tool_args.get("file_path", ""))}

    # RCA-316c: For delegation tools (call_subordinate, etc.), strip dynamic
    # context from the 'message' field. Error relay, progress summaries, task
    # tracking metadata, turn budget, and boomerang context are injected by
    # the delegation pipeline and change each retry — but the core task is
    # the same. Without stripping, each re-delegation appears unique.
    # Same pattern as sequential_thinking volatile stripping (RCA-217).
    DELEGATION_TOOLS = {"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"}
    if tool_name in DELEGATION_TOOLS and isinstance(tool_args, dict):
        from python.helpers.task_hash import strip_delegation_context
        tool_args = dict(tool_args)  # Don't mutate the original
        if "message" in tool_args and isinstance(tool_args["message"], str):
            tool_args["message"] = strip_delegation_context(tool_args["message"])
        if "task" in tool_args and isinstance(tool_args["task"], str):
            tool_args["task"] = strip_delegation_context(tool_args["task"])

    # Sort args for deterministic comparison
    import json as _json
    try:
        args_canonical = _json.dumps(tool_args, sort_keys=True)
    except (TypeError, ValueError):
        args_canonical = str(tool_args)

    return f"{tool_name}|{args_canonical}"



def is_structural_repeat(msg_a: str, msg_b: str) -> bool:
    """Check if two messages are structurally identical (full action tuple).

    Uses MD5 hashing of the complete message content (text + tool_name +
    tool_args — including thoughts) to detect exact same output. This is
    STRICTER than is_semantic_repeat: it requires every field to match.

    Use this as the L1 bridge gate to avoid false-positive same-message
    alerts. The semantic repeat detector (extract_tool_signature) handles
    the softer "same tool, different wording" case for monitoring.

    For non-JSON messages, falls back to exact string comparison.

    RCA-251: ~180 false-positive same-message L1 signals per run were
    caused by the bridge firing on messages where only the tool signature
    matched but the thoughts/context differed. The structural repeat
    check requires the FULL action tuple to match.
    """
    from python.helpers.hashing import content_hash

    if not msg_a or not msg_b:
        return False

    hash_a = content_hash(msg_a)
    hash_b = content_hash(msg_b)
    return hash_a == hash_b


def is_semantic_repeat(msg_a: str, msg_b: str) -> bool:
    """Check if two messages are semantically identical tool calls.

    Two messages are semantic repeats if they have the same tool_name and
    tool_args, regardless of differences in thoughts, formatting, or
    whitespace.

    Returns False if either message is not a parseable tool call.
    """
    sig_a = extract_tool_signature(msg_a)
    sig_b = extract_tool_signature(msg_b)
    if sig_a is None or sig_b is None:
        return False
    return sig_a == sig_b


# ── Planning Tool Classification (RCA-245) ──

# Tools that are meta-cognitive / planning rather than actions.
# These tools are expected to repeat during complex planning phases and
# should NOT trigger hard-stops. L1 signals still fire for monitoring.
PLANNING_TOOLS = frozenset({
    "sequential_thinking",
})


def is_planning_tool(tool_name: str | None) -> bool:
    """Return True if tool_name is a meta-cognitive planning tool.

    Planning tools (e.g., sequential_thinking) are expected to repeat
    during complex planning phases. They should not trigger hard-stops
    because the agent is legitimately replanning, not action-looping.

    RCA-245: The orchestrator was killed during Phase 0 planning because
    sequential_thinking calls with the same thoughtNumber were treated as
    action loops. Planning ≠ looping.
    """
    if not tool_name:
        return False
    return tool_name in PLANNING_TOOLS


def extract_tool_name_from_response(response: str) -> str | None:
    """Extract just the tool_name from a JSON agent response string.

    Returns None if the response is not valid JSON or has no tool_name.
    Used by agent.py to pass the current tool name to
    should_hard_stop_same_message() for planning-tool exemption.
    """
    import json as _json
    try:
        parsed = _json.loads(response)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get("tool_name") or None


# ── Semantic Repeat Separate Counter (RCA-263) ──


def bridge_semantic_repeat_to_l1(
    agent_data: dict,
    repeat_count: int,
) -> dict:
    """Create an L1 escalation signal from SEMANTIC same-message detection.

    RCA-263: This function is separate from bridge_same_message_to_l1() to
    maintain independent cumulative counters. Exact-match events should NOT
    inflate the semantic cumulative counter, and vice versa.

    Args:
        agent_data: The agent's ``self.data`` dict (mutable, modified in place).
        repeat_count: How many times the same semantic signature has been
                      repeated consecutively.

    Returns:
        The signal dict that was appended to ``_l2_escalation_signals``.
    """
    # Increment SEMANTIC-specific cumulative counter
    cumulative = agent_data.get("_semantic_cumulative_count", 0) + 1
    agent_data["_semantic_cumulative_count"] = cumulative

    # P0-1: Budget-driven severity
    from python.helpers.retry_budget_bridge import decide_semantic_repeat
    budget_decision = decide_semantic_repeat(agent_data, repeat_count)

    if budget_decision.action in ("escalate", "force_complete", "terminal"):
        severity = "critical"
    elif repeat_count >= 2:
        severity = "high"
    else:
        severity = "warning"

    # === OVL-1d: Wire to repetition_recovery escalation ladder ===
    from python.helpers.repetition_recovery import (
        increment_attempt as _rr_increment,
        RepetitionRecoveryManager as _RRM,
    )
    recovery_attempt = _rr_increment(agent_data)
    _recovery_strategy = _RRM().get_recovery_strategy(recovery_attempt)

    signal = {
        "detector": "semantic_same_message_repeat",
        "severity": severity,
        "detail": (
            f"Agent sent semantically identical tool call {repeat_count} time(s) consecutively "
            f"(semantic cumulative session-wide: {cumulative}). "
            f"Tool signature matches but thoughts/reasoning differ."
        ),
        "repeat_count": repeat_count,
        "semantic_cumulative_count": cumulative,
        "recovery_action": _recovery_strategy["action"],
        "recovery_advice": _recovery_strategy.get("advice", ""),
        "recovery_attempt": recovery_attempt,
        "ts": time.time(),
    }

    if "_l2_escalation_signals" not in agent_data:
        agent_data["_l2_escalation_signals"] = []
    agent_data["_l2_escalation_signals"].append(signal)

    logger.warning(
        "Semantic repeat L1 bridge fired: repeat_count=%d semantic_cumulative=%d severity=%s recovery=%s (attempt=%d)",
        repeat_count,
        cumulative,
        severity,
        _recovery_strategy["action"],
        recovery_attempt,
    )

    return signal



def should_hard_stop_semantic_repeat(
    repeat_count: int,
    tool_name: str | None = None,
    cumulative_count: int = 0,
    test_output_changed: bool | None = None,
) -> bool:
    """Return True if the agent should be forcibly stopped due to semantic repeat loop.

    RCA-263: Uses SEMANTIC_CUMULATIVE_THRESHOLD (20) instead of the exact-match
    CUMULATIVE_SAME_MESSAGE_THRESHOLD (15). Semantic matches are less precise
    and fire more during legitimate re-delegations, so the threshold is higher.

    Args:
        repeat_count: Current consecutive semantic repeat count.
        tool_name: Optional tool name for planning-tool exemption.
        cumulative_count: Semantic-specific cumulative count.
        test_output_changed: If True, the test command's output is changing
                             (agent is making progress). Exempt from hard-stop.
    """
    if is_planning_tool(tool_name):
        return False
    # A-1: If test output is progressing, exempt from hard-stop
    if test_output_changed is True:
        return False
    return (
        repeat_count >= SAME_MESSAGE_HARD_CAP
        or cumulative_count >= SEMANTIC_CUMULATIVE_THRESHOLD
    )


# ── Progress-Aware Test Output Tracking (why_agents_cant_fix_code.md A-1) ──
#
# When agents run test commands (npm test, vitest, jest, pytest), the loop
# detector needs to distinguish between:
#   - STUCK: identical test output across runs (agent is not fixing anything)
#   - PROGRESS: changing test output (more passing, fewer failing)
#
# These functions track test command outputs via MD5 hashes in agent.data
# and provide a simple is-output-changing check for the hard-stop functions.

_TEST_COMMAND_PATTERNS = re.compile(
    r'\b(npm\s+(test|run\s+test)|npx\s+(vitest|jest)|pytest|python\s+-m\s+pytest)\b',
    re.IGNORECASE,
)

# Maximum number of output hashes to retain (bounded history)
_MAX_TEST_OUTPUT_HISTORY = 10


def is_test_command_output(message: str) -> bool:
    """Return True if the message is a code_execution_tool call running tests.

    Checks for: npm test, npm run test, npx vitest, npx jest, pytest,
    python -m pytest.

    Args:
        message: The JSON agent response string.
    """
    try:
        parsed = _json_module.loads(message)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("tool_name") != "code_execution_tool":
        return False
    tool_args = parsed.get("tool_args", {})
    if not isinstance(tool_args, dict):
        return False
    code = tool_args.get("code", "") or tool_args.get("command", "") or ""
    return bool(_TEST_COMMAND_PATTERNS.search(code))


def record_test_output(agent_data: dict, output: str | None) -> None:
    """Record a test run's output hash for progress tracking.

    Stores MD5 hashes of test outputs in a bounded list in agent.data.
    Used by has_test_output_changed() to determine if test results are
    improving between runs.

    Args:
        agent_data: The agent's ``self.data`` dict (mutable).
        output: The test command's stdout/stderr output. Can be None.
    """
    output_str = output or ""
    output_hash = hashlib.md5(output_str.encode("utf-8", errors="replace")).hexdigest()

    if "_test_output_hashes" not in agent_data:
        agent_data["_test_output_hashes"] = []

    agent_data["_test_output_hashes"].append(output_hash)

    # Bound the history
    if len(agent_data["_test_output_hashes"]) > _MAX_TEST_OUTPUT_HISTORY:
        agent_data["_test_output_hashes"] = agent_data["_test_output_hashes"][-_MAX_TEST_OUTPUT_HISTORY:]


def has_test_output_changed(agent_data: dict) -> bool:
    """Return True if the most recent test output differs from the previous one.

    Returns True (progress) if:
    - There is only one recorded output (first run = always progress)
    - The latest hash differs from the previous hash

    Returns False (stuck) if:
    - The latest hash equals the previous hash (identical output)

    Args:
        agent_data: The agent's ``self.data`` dict.
    """
    hashes = agent_data.get("_test_output_hashes", [])
    if len(hashes) <= 1:
        return True  # First run = always progress
    return hashes[-1] != hashes[-2]

