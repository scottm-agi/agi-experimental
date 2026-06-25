"""
Loop Detection Engine — Phases 2-4

Centralized loop detection functions used by both:
- agent_process_tools.py (Layer 1: tool dedup guard)
- _45_time_based_supervisor.py (Layer 2: supervisor)

Functions:
- detect_no_progress_streak(history) → int (Phase 2)
- detect_ping_pong_streak(history) → int (Phase 3)
- is_research_tool(tool_name) → bool (Phase 4)
- get_tool_thresholds(tool_name) → dict (Phase 4)
- get_supervisor_threshold(tool_name) → int (Phase 4)
"""

from __future__ import annotations

from typing import Any, Dict, List


# =====================================================================
# Phase 4: Research Tools — higher thresholds for legitimate deep work
# =====================================================================

RESEARCH_TOOLS = frozenset({
    "search_engine",
    "perplexity-ask",
    "perplexity_ask",
    "scrape_url",
    "web_search",
})

# Default thresholds (Layer 1: tool dedup guard in agent_process_tools.py)
# These are the FIRST line of defense — catch obvious duplicates fast
DEFAULT_THRESHOLDS = {
    "max_consecutive": 5,
    "max_total": 10,
    "hard_break": 25,   # Absolute circuit breaker — very high, last resort
}

# Research tool thresholds — much higher to allow deep research
# Researchers legitimately make many calls with similar args
RESEARCH_THRESHOLDS = {
    "max_consecutive": 10,
    "max_total": 20,
    "hard_break": 40,   # Absolute circuit breaker — very high, last resort
}

# MCP tool thresholds — tighter than default because:
# 1. External API calls are expensive (money + latency)
# 2. If 2 identical calls returned the same result, a 3rd won't help
# 3. Context7 specifically loops 5x — catching at 2 saves 3 wasted calls
# Iteration 91: context7.resolve-library-id looped 5x with identical args
# Iteration 109: lowered max_consecutive 3→2 (MCP results are deterministic)
MCP_THRESHOLDS = {
    "max_consecutive": 2,   # Block at 2nd identical call (was 3, iteration 109)
    "max_total": 6,         # Block at 6th total (was 10 default)
    "hard_break": 15,       # Circuit breaker (was 25 default)
}

# Supervisor thresholds (Layer 2: _45_time_based_supervisor.py)
# These fire AFTER Layer 1 has already warned/blocked. Much higher.
# Supervisor soft warn — triggers LLM-based redirect
DEFAULT_SUPERVISOR_THRESHOLD = 10
RESEARCH_SUPERVISOR_THRESHOLD = 20
MCP_SUPERVISOR_THRESHOLD = 6  # MCP tools get tighter supervisor watch too

# §12 TDD Mode Thresholds — elevated to allow productive TDD cycles.
# TDD red-green-refactor cycles legitimately repeat test/build commands
# 10-15 times. Without elevated thresholds, loop detection kills
# productive cycles at iteration 5.
TDD_THRESHOLDS = {
    "max_consecutive": 15,
    "max_total": 30,
    "hard_break": 50,
}
TDD_SUPERVISOR_THRESHOLD = 30

# File editing tools (deterministic). If a diff/replace fails with identical
# args, it will ALWAYS fail. Block it immediately on the 2nd identical try.
FILE_EDIT_THRESHOLDS = {
    "max_consecutive": 2,
    "max_total": 6,
    "hard_break": 15,
}

FILE_EDIT_TOOLS = frozenset({
    "apply_diff",
    "replace_in_file",
    "write_to_file",
    "multi_replace_file_content",
})

def is_file_edit_tool(tool_name: str) -> bool:
    """Check if a tool is a deterministic file editing tool."""
    return tool_name.strip().lower() in FILE_EDIT_TOOLS


def is_research_tool(tool_name: str) -> bool:
    """Check if a tool is a research tool that gets higher loop thresholds."""
    return tool_name.strip().lower() in RESEARCH_TOOLS


def get_tool_thresholds(
    tool_name: str,
    agent_data: dict | None = None,
) -> Dict[str, int]:
    """Get loop detection thresholds for a tool (profile-aware).

    Priority: TDD (if active) > research > MCP > default.
    Research tools get high limits (deep research is legitimate).
    MCP tools get low limits (external APIs are expensive).
    Default tools get moderate limits.
    TDD mode elevates limits for code_execution tools.

    Args:
        tool_name: Name of the tool.
        agent_data: Optional agent.data dict. If TDD cycle is active
                    and tool is code_execution, returns TDD thresholds.
    """
    # §12: TDD mode check — elevated thresholds for code execution tools
    if agent_data is not None:
        from python.helpers.tdd_cycle_detector import is_tdd_active
        if is_tdd_active(agent_data):
            if tool_name.lower() in ("code_execution_tool", "code_execution"):
                return TDD_THRESHOLDS.copy()
    if is_research_tool(tool_name):
        return RESEARCH_THRESHOLDS.copy()
    if is_mcp_tool(tool_name):
        return MCP_THRESHOLDS.copy()
    if is_file_edit_tool(tool_name):
        return FILE_EDIT_THRESHOLDS.copy()
    return DEFAULT_THRESHOLDS.copy()


def get_supervisor_threshold(
    tool_name: str,
    agent_data: dict | None = None,
) -> int:
    """Get the supervisor repeat threshold for a tool.

    Args:
        tool_name: Name of the tool.
        agent_data: Optional agent.data dict. TDD mode returns higher threshold.
    """
    # §12: TDD mode — elevated supervisor threshold
    if agent_data is not None:
        from python.helpers.tdd_cycle_detector import is_tdd_active
        if is_tdd_active(agent_data):
            if tool_name.lower() in ("code_execution_tool", "code_execution"):
                return TDD_SUPERVISOR_THRESHOLD
    if is_research_tool(tool_name):
        return RESEARCH_SUPERVISOR_THRESHOLD
    if is_mcp_tool(tool_name):
        return MCP_SUPERVISOR_THRESHOLD
    return DEFAULT_SUPERVISOR_THRESHOLD


# =====================================================================
# Phase 5: Same-Tool Streak Detection (args-independent)
# =====================================================================

# Same-tool streak thresholds — catches repeated calls to the same tool
# even with different arguments. MCP tools (external APIs) get lower
# thresholds because they're expensive and if 3 calls with different
# args didn't yield what the agent needs, the approach is wrong.

SAME_TOOL_DEFAULT_THRESHOLDS = {
    "warn": 4,    # Warn after 4 consecutive calls to same tool
    "block": 7,   # Hard block after 7
}

SAME_TOOL_MCP_THRESHOLDS = {
    "warn": 3,    # Warn after 3 consecutive MCP calls
    "block": 5,   # Hard block after 5
}

# Batch-safe tools — tools that legitimately need many sequential calls
# with different arguments. E.g., secret_set called 7 times to store 7 API keys
# from a customer prompt. Without elevated thresholds, the streak detector
# would warn at 4 and HARD BLOCK at 7, silently preventing secrets from
# being stored. (RCA: MainStreet iteration 131-132)
#
# NOTE: The MD5 sig-based dedup (Phase 1-2) already protects against truly
# identical calls (same args → same hash). This Phase 5 guard only fires on
# same tool NAME regardless of args. For batch-safe tools, each call has
# legitimately different args (different key/value), so the sig is unique.
# The elevated thresholds here prevent the name-only guard from blocking
# legitimate batch operations.
_BATCH_SAFE_ELEVATED = frozenset({
    "secret_set",       # 7+ secrets per customer prompt
    "parameter_set",    # Batch config parameters
    "settings_set",     # Batch settings configuration
})

SAME_TOOL_BATCH_SAFE_THRESHOLDS = {
    "warn": 10,   # Warn only after 10 consecutive calls
    "block": 50,  # Absolute circuit breaker — last resort safety net
}


def is_mcp_tool(tool_name: str) -> bool:
    """Check if a tool is an MCP tool (external API call).

    MCP tools are identified by having a '.' in their name, e.g.:
    - google-chat.google_chat_list_messages
    - github.list_issues
    - slack.send_message

    Standard tools (code_execution_tool, response, maintain_memory_bank)
    never have dots in their names.
    """
    if not tool_name:
        return False
    return "." in tool_name.strip()


def get_same_tool_thresholds(tool_name: str) -> Dict[str, int]:
    """Get same-tool streak thresholds.

    Priority: MCP (tightest) > default > batch-safe (loosest).

    Batch-safe tools (secret_set, parameter_set) legitimately need many
    sequential calls with different arguments. Without elevated thresholds,
    the MainStreet prompt's 7 API keys would be hard-blocked at call #7.
    """
    if is_mcp_tool(tool_name):
        return SAME_TOOL_MCP_THRESHOLDS.copy()
    if tool_name in _BATCH_SAFE_ELEVATED:
        return SAME_TOOL_BATCH_SAFE_THRESHOLDS.copy()
    return SAME_TOOL_DEFAULT_THRESHOLDS.copy()


# =====================================================================
# Phase 2: No-Progress Streak Detection
# =====================================================================

def detect_no_progress_streak(history: List[Dict[str, Any]]) -> int:
    """
    Detect consecutive identical tool calls that produce identical results.
    
    Walks backward through history. A "no-progress" entry has the same
    `sig` (tool+args hash) AND same `result_hash` as the one after it.
    
    Returns the length of the current no-progress streak (from the tail).
    
    Rules:
    - Same sig + same result_hash = no progress, streak continues
    - Same sig + different result_hash = progress (environment changed), streak = 1
    - Different sig = different tool/args, streak resets to 1
    - Missing result_hash = treated as unique (pending call), streak resets to 1
    """
    if not history:
        return 0
    
    streak = 1  # Current entry always counts as 1
    
    # Walk backward from the tail
    for i in range(len(history) - 1, 0, -1):
        current = history[i]
        previous = history[i - 1]
        
        current_sig = current.get("sig")
        previous_sig = previous.get("sig")
        current_result = current.get("result_hash")
        previous_result = previous.get("result_hash")
        
        # Both must have result hashes and matching sigs + results
        if (current_sig == previous_sig 
                and current_result is not None 
                and previous_result is not None
                and current_result == previous_result):
            streak += 1
        else:
            break
    
    return streak


# =====================================================================
# Phase 3: Ping-Pong Detection
# =====================================================================

def detect_ping_pong_streak(history: List[Dict[str, Any]]) -> int:
    """
    Detect alternating A→B→A→B patterns in tool call history.
    
    Looks at pairs from the tail of history. If consecutive pairs
    alternate between the same two sigs, that's a ping-pong.
    
    Returns the number of A-B pair repetitions detected.
    Returns 0 if no ping-pong pattern found.
    """
    if len(history) < 2:
        return 0
    
    sigs = [entry.get("sig") for entry in history]
    
    # Need the last two to be different for alternating pattern
    if sigs[-1] == sigs[-2]:
        return 0
    
    pair_a = sigs[-2]
    pair_b = sigs[-1]
    
    # Walk backward in pairs to count how many times A-B repeats
    pairs_found = 0
    idx = len(sigs) - 2  # Start of the last pair
    
    while idx >= 0 and idx + 1 < len(sigs):
        if sigs[idx] == pair_a and sigs[idx + 1] == pair_b:
            pairs_found += 1
            idx -= 2
        else:
            break
    
    # Only count as ping-pong if at least 2 pairs (A→B→A→B)
    # A single A→B pair is not a ping-pong, it's just two different calls
    if pairs_found < 2:
        return pairs_found  # Return 1 for a single pair, caller decides threshold
    
    return pairs_found


# =====================================================================
# Same-Tool Streak Detection (Memory Bank Loop Fix)
# =====================================================================

def detect_same_tool_streak(history: List[Dict[str, Any]]) -> int:
    """
    Count consecutive calls of the same tool name from the tail,
    IGNORING argument differences.
    
    Unlike detect_no_progress_streak (which requires both same sig AND
    same result_hash), this function only compares tool_name. This catches
    patterns like:
    
        maintain_memory_bank(read, progress.md)
        → maintain_memory_bank(append, progress.md, "Cat 1 done")
        → maintain_memory_bank(overwrite, activeContext.md, "Working on Cat 2")
        → maintain_memory_bank(read, lessons-learned.md)
    
    All four have different sigs, but it's the same tool being over-used.
    Streak = 4.
    
    Returns the length of the consecutive same-tool streak from the tail.
    Returns 0 for empty history, 1 for a single entry.
    """
    if not history:
        return 0
    
    streak = 1
    last_tool = history[-1].get("tool_name")
    
    if last_tool is None:
        return 1
    
    for i in range(len(history) - 2, -1, -1):
        if history[i].get("tool_name") == last_tool:
            streak += 1
        else:
            break
    
    return streak


# =====================================================================
# Per-Tool Cooldown Enforcement (Memory Bank Loop Fix)
# =====================================================================

# Minimum iteration gap between calls for tools prone to loop patterns
COOLDOWN_TOOLS: Dict[str, int] = {
    "maintain_memory_bank": 5,  # Min 5 iterations between calls
    "scheduler": 3,             # Min 3 iterations between calls
}


def check_tool_cooldown(tool_name: str, history: List[Dict[str, Any]], current_iteration: int) -> bool:
    """
    Check if a tool is in cooldown (called too recently).
    
    Returns True if the tool should be blocked/warned — i.e., it was called
    within the last N iterations (where N = COOLDOWN_TOOLS[tool_name]).
    
    Returns False if:
    - Tool is not in COOLDOWN_TOOLS (no cooldown configured)
    - Tool has never been called
    - Sufficient iterations have passed since last call
    """
    cooldown = COOLDOWN_TOOLS.get(tool_name)
    if not cooldown:
        return False
    
    # Find most recent call of this tool (search from tail)
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("tool_name") == tool_name:
            last_iteration = history[i].get("iteration", 0)
            gap = current_iteration - last_iteration
            if gap < cooldown:
                return True  # Still in cooldown
            break
    
    return False


# =====================================================================
# Same-Error Streak Detection (Iteration 23 — Generalized Guardrail)
# =====================================================================

# Universal error indicators — framework-agnostic patterns
_ERROR_INDICATORS = [
    "error:",
    "error ",
    "traceback",
    "exception:",
    "failed",
    "cannot find",
    "cannot resolve",
    "cannot initialize",
    "not found",
    "enoent",
    "eacces",
    "permission denied",
    "syntax error",
    "type error",
    "reference error",
    "module not found",
    "import error",
    "compilation error",
    "build failed",
    "exited with code 1",
    "exited with code 2",
    "fatal:",
    "panic:",
    "segmentation fault",
    "url_invalid",
    "connection refused",
]


def _extract_error_signature(text: str) -> str:
    """Extract a normalized error signature from tool result text.

    Strips file paths, line numbers, timestamps, and UUIDs to produce
    a canonical error fingerprint. Two results with the same logical
    error but different file locations will produce the same signature.

    Returns empty string if no error pattern detected in text.
    """
    import re
    if not text:
        return ""

    text_lower = text.lower()

    # Check if any error indicator is present
    has_error = any(indicator in text_lower for indicator in _ERROR_INDICATORS)
    if not has_error:
        return ""

    # Find lines containing error indicators
    error_lines = []
    for line in text.split("\n"):
        line_lower = line.lower().strip()
        if any(indicator in line_lower for indicator in _ERROR_INDICATORS):
            error_lines.append(line.strip())

    if not error_lines:
        return ""

    # Normalize: take the most informative error line (usually the longest)
    # and strip volatile parts
    signature = max(error_lines, key=len)

    # Strip file paths (Unix and Windows)
    signature = re.sub(r'[/\\][\w./\\-]+\.\w+', '[PATH]', signature)
    # Strip line:column numbers (e.g., :42, :17:5)
    signature = re.sub(r':\d+(?::\d+)?', ':[N]', signature)
    # Strip timestamps
    signature = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '[TS]', signature)
    # Strip UUIDs
    signature = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '[UUID]', signature, flags=re.IGNORECASE)
    # Strip standalone numbers (but keep words)
    signature = re.sub(r'\b\d+\b', '[N]', signature)
    # Normalize whitespace
    signature = ' '.join(signature.lower().split())

    return signature


def detect_same_error_streak(history: List[Dict[str, Any]]) -> int:
    """Detect consecutive tool results containing the same error pattern.

    Unlike detect_no_progress_streak (which compares tool *input* signatures),
    this function compares tool *output error* signatures. It catches the
    universal loop pattern where an agent makes different code changes but
    the environment keeps returning the same error message.

    Process:
    1. Walk backward through history
    2. Extract a normalized error signature from each result_text
    3. Count consecutive entries with the same non-empty error signature

    Returns:
        int: Length of the current same-error streak from the tail.
             Returns 0 if no error detected in the most recent entry,
             or if history is empty.
    """
    if not history:
        return 0

    # Extract error signatures for all entries (from latest)
    signatures = []
    for entry in reversed(history):
        result_text = entry.get("result_text", "")
        sig = _extract_error_signature(result_text)
        signatures.append(sig)

    # The most recent entry must have an error for a streak to exist
    if not signatures or not signatures[0]:
        return 0

    # Count consecutive same-error from the tail
    streak = 1
    target_sig = signatures[0]

    for i in range(1, len(signatures)):
        if signatures[i] == target_sig:
            streak += 1
        else:
            break

    return streak


# =====================================================================
# Phase 6: Cumulative Tool Failure Counter (F-8)
# =====================================================================
# Unlike consecutive streak detection, this tracks TOTAL failures per
# tool across the entire conversation. The generate_image 1,684-call
# amplification happened because the agent interleaved image gen calls
# with thinking/response calls, resetting the consecutive streak counter.
# This cumulative counter is NOT reset by interleaving.

_TOOL_FAILURE_TOTALS_KEY = "_tool_failure_totals"

# Default threshold: block after 5 total failures of the same tool
DEFAULT_CUMULATIVE_FAILURE_THRESHOLD = 5


def record_tool_failure(agent_data: dict, tool_name: str) -> None:
    """Record a tool failure in the cumulative counter.
    
    Args:
        agent_data: The agent's data dict (agent.data or equivalent)
        tool_name: Name of the tool that failed
    """
    if _TOOL_FAILURE_TOTALS_KEY not in agent_data:
        agent_data[_TOOL_FAILURE_TOTALS_KEY] = {}
    
    totals = agent_data[_TOOL_FAILURE_TOTALS_KEY]
    totals[tool_name] = totals.get(tool_name, 0) + 1


def should_block_tool(agent_data: dict, tool_name: str, threshold: int = DEFAULT_CUMULATIVE_FAILURE_THRESHOLD) -> bool:
    """Check if a tool should be blocked due to cumulative failures.
    
    Args:
        agent_data: The agent's data dict
        tool_name: Name of the tool to check
        threshold: Number of failures before blocking (default: 5)
    
    Returns:
        True if the tool has reached the failure threshold and should be blocked.
    """
    totals = agent_data.get(_TOOL_FAILURE_TOTALS_KEY, {})
    return totals.get(tool_name, 0) >= threshold


def get_tool_failure_count(agent_data: dict, tool_name: str) -> int:
    """Get the current cumulative failure count for a tool."""
    totals = agent_data.get(_TOOL_FAILURE_TOTALS_KEY, {})
    return totals.get(tool_name, 0)


def reset_tool_failures(agent_data: dict, tool_name: str) -> None:
    """Reset the failure counter for a tool (e.g., after a success)."""
    totals = agent_data.get(_TOOL_FAILURE_TOTALS_KEY, {})
    if tool_name in totals:
        del totals[tool_name]
