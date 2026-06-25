"""
Delegation guard checks extracted from call_subordinate.py (P1.1 modularization).

Contains pre-execution validation logic that determines whether a delegation
should proceed or be blocked. Each guard returns a Response if blocked, or
None to proceed.
"""
from __future__ import annotations
import logging
import os
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.subordinate")


# ── SS-5b: Capability Mismatch Escalation ──────────────────────────────────
# After N consecutive blocks on the SAME profile, auto-correct to 'code'
# instead of blocking again. Root cause (MSR Phase 3): orchestrator got
# blocked 12x on frontend because the SS-5 blocker (check_ontology_capability_guard)
# returned an error message but the orchestrator didn't learn.
# SS-12 auto-corrector (check_tool_capability_mismatch) runs AFTER SS-5
# in the call_subordinate pipeline and is NEVER reached when SS-5 blocks.
CAPABILITY_MISMATCH_AUTOCORRECT_THRESHOLD = 1


def should_autocorrect_capability_mismatch(
    agent_data: dict, profile: str
) -> bool:
    """Return True if this profile has been blocked enough times to auto-correct."""
    counts = agent_data.get("_capability_mismatch_counts", {})
    return counts.get(profile, 0) >= CAPABILITY_MISMATCH_AUTOCORRECT_THRESHOLD


def increment_capability_mismatch_count(
    agent_data: dict, profile: str
) -> int:
    """Increment the mismatch counter for a profile and return the new count."""
    if "_capability_mismatch_counts" not in agent_data:
        agent_data["_capability_mismatch_counts"] = {}
    count = agent_data["_capability_mismatch_counts"].get(profile, 0) + 1
    agent_data["_capability_mismatch_counts"][profile] = count
    return count


def reset_capability_mismatch_count(
    agent_data: dict, profile: str
) -> None:
    """Reset the mismatch counter for a profile (called on successful delegation)."""
    if "_capability_mismatch_counts" in agent_data:
        agent_data["_capability_mismatch_counts"][profile] = 0


# ── SS-5b: Word-boundary tool name matcher ────────────────────────────────
# Root cause (MSR Phase 3): substring matching caused "requirements" in
# natural language ("Read the requirements document") to match the tool
# name `requirements`. This caused 12 false-positive blocks on frontend.
_TOOL_REFERENCE_PATTERNS = [
    # Backtick-wrapped: `requirements`
    r'`{tool}`',
    # JSON tool_name: "requirements"
    r'"tool_name"\s*:\s*"{tool}"',
    # Explicit tool reference: "the requirements tool", "use requirements to"
    r'\b(?:the\s+|use\s+){tool}\s+(?:tool|function|command)\b',
    r'\buse\s+{tool}\s+to\b',
    r'\bcall\s+{tool}\b',
]


# Pre-position negation patterns — negation keyword followed by optional
# filler words (use, call, attempt, invoke, etc.) before the tool name.
# Uses (?:\s+\w+){0,3} to allow up to 3 words between negation and tool name.
_PRE_NEGATION_RE = re.compile(
    r'(?:do\s+not|don\'t|cannot|can\'t|should\s+not|shouldn\'t|'
    r'must\s+not|mustn\'t|never|avoid|without)'
    r'(?:\s+\w+){0,3}\s*$',
    re.IGNORECASE
)

# Post-position negation patterns (after the tool name)
# e.g., "write_to_file is blocked", "write_to_file is prohibited"
_POST_NEGATION_RE = re.compile(
    r'\s+(?:is\s+)?(?:blocked|prohibited|disabled)',
    re.IGNORECASE
)

# "lacks write_to_file" — negation word immediately before
_LACKS_NEGATION_RE = re.compile(
    r'(?:lacks)(?:\s+\w+){0,2}\s*$',
    re.IGNORECASE
)


def _has_negation_context(message_lower: str, match_start: int, match_end: int) -> bool:
    """Check if a tool name match is in a negation context.

    Looks backwards from the match position (up to 30 chars) for negation
    keywords like 'do not', 'cannot', 'never', 'avoid', etc. Also checks
    forward from the match end for post-position negation like 'is blocked'.

    This prevents instructions like "Do NOT use write_to_file" from being
    treated as positive tool references.

    Root cause: 27 BLOCKED events consuming ~13 iterations were caused by
    the guard matching tool names in negation contexts, contributing to
    budget exhaustion.
    """
    # Look at up to 30 chars before the match position
    window_start = max(0, match_start - 30)
    preceding_text = message_lower[window_start:match_start]

    # Pre-position negation (before the tool name)
    if _PRE_NEGATION_RE.search(preceding_text):
        return True

    # Post-position negation (after the tool name)
    after_text = message_lower[match_end:match_end + 25]
    if _POST_NEGATION_RE.match(after_text):
        return True

    # "lacks" negation (before the tool name)
    if _LACKS_NEGATION_RE.search(preceding_text):
        return True

    return False


def _has_research_context(text: str, start: int, end: int) -> bool:
    """Check if a compound tool name appears in a research/documentation context.

    Research-intent means the task is asking to STUDY or DOCUMENT the tool,
    not to INVOKE it. This prevents the ontology guard from blocking legitimate
    research tasks (e.g., Phase 0.5 docs pre-fetch).

    RCA-webhook-20260612: _is_tool_reference() was blocking researcher profiles
    because compound names (repository_automation) were always treated as
    invocation intent, even in 'research the tool parameters' context.
    """
    # Look at the 80 chars before the tool name for research-intent signals
    prefix = text[max(0, start - 80):start].lower()
    RESEARCH_SIGNALS = [
        "research", "look up", "look-up", "lookup",
        "verify", "investigate", "document",
        "pre-fetch", "pre fetch", "prefetch",
        "parameters", "documentation",
        "framework research", "phase 0.5",
        "research & docs", "research and docs",
        "how .* works",  # "how the tool works"
        "study", "examine", "inspect",
    ]
    for signal in RESEARCH_SIGNALS:
        if signal in prefix:
            return True
    return False


# F-2 (SS-2): Zone marker pattern for included document content.
# Research docs, §§include() expansions, and other inlined content are
# wrapped in these markers so the capability guard can skip tool name
# scanning inside referenced documents.
_INCLUDED_DOC_START_RE = re.compile(
    r'<!--\s*INCLUDED_DOCUMENT_START[^>]*-->',
    re.IGNORECASE,
)
_INCLUDED_DOC_END_RE = re.compile(
    r'<!--\s*INCLUDED_DOCUMENT_END\s*-->',
    re.IGNORECASE,
)


def _has_included_document_context(message: str, match_start: int) -> bool:
    """Check if a tool name match is inside an INCLUDED_DOCUMENT zone.

    Returns True when the match position is between a
    <!-- INCLUDED_DOCUMENT_START --> and <!-- INCLUDED_DOCUMENT_END --> pair.

    F-2 (SS-2) RCA: §§include() content and inlined research docs may
    contain tool names in Sources/attribution sections. These are NOT
    invocation intent — the researcher is documenting what tools it used,
    not instructing the downstream agent to invoke them.
    """
    # Find the nearest INCLUDED_DOCUMENT_START before match_start
    text_before = message[:match_start]
    starts = list(_INCLUDED_DOC_START_RE.finditer(text_before))
    if not starts:
        return False

    # The most recent start marker
    last_start = starts[-1]
    start_end_pos = last_start.end()

    # Check if there's a corresponding END marker between the start and match
    text_between = message[start_end_pos:match_start]
    if _INCLUDED_DOC_END_RE.search(text_between):
        return False  # The zone was closed before the match — not in zone

    # Check if there's an END marker after the match
    text_after = message[match_start:]
    if _INCLUDED_DOC_END_RE.search(text_after):
        return True  # Match is between START and END — inside zone

    return False


def _is_tool_reference(tool_name: str, message: str) -> bool:
    """Check if a tool name is referenced AS A TOOL (not natural language).

    Returns True when the message references the tool name in a way that
    implies tool invocation:
    - Backtick-wrapped: `requirements`
    - JSON: "tool_name": "requirements"
    - Explicit: "use requirements tool", "call requirements"
    - Compound names (with _ or -): tool reference UNLESS in exempted context

    Returns False for:
    - Natural language uses of simple tool names like
      "the requirements document" or "Read requirements_ledger.json".
    - Compound tool names in negation context like
      "Do NOT use write_to_file" or "write_to_file is blocked".
    - Tool names inside INCLUDED_DOCUMENT zone markers (F-2 SS-2 fix).
    """
    message_lower = message.lower()
    tool_lower = tool_name.lower()

    # Compound tool names (with underscores or hyphens like "browser_agent",
    # "code_execution_tool") are tool references — these are never natural
    # language words. BUT: check exemption contexts first.
    if "_" in tool_lower or "-" in tool_lower:
        pattern = r'\b' + re.escape(tool_lower) + r'\b'
        match = re.search(pattern, message_lower)
        if match:
            if _has_negation_context(message_lower, match.start(), match.end()):
                return False
            # RCA-webhook-20260612: Research context exemption
            if _has_research_context(message_lower, match.start(), match.end()):
                return False
            # F-2 (SS-2): Included document content exemption
            if _has_included_document_context(message_lower, match.start()):
                return False
            return True

    # Simple names (like "requirements", "run") need explicit tool syntax
    for pattern_template in _TOOL_REFERENCE_PATTERNS:
        pattern = pattern_template.replace("{tool}", re.escape(tool_lower))
        if re.search(pattern, message_lower):
            return True
    return False


def check_terminal_profile_guard(current_profile: str) -> Optional[str]:
    """Block delegation from terminal profiles (e.g., browser) that should execute directly.

    Returns error message string if blocked, None to proceed.
    """
    TERMINAL_PROFILES = {"browser"}
    if current_profile in TERMINAL_PROFILES:
        return (
            f"ERROR: You are a '{current_profile}' agent. You must NOT delegate to subordinates. "
            f"Use your own tools directly (e.g., 'browser_agent' tool for web browsing). "
            f"Do NOT call call_subordinate."
        )
    return None


def check_same_profile_guard(current_profile: str, requested_profile: str, message: str = "") -> Optional[str]:
    """Block pointless same-profile delegation (#875).

    Returns error message string if blocked, None to proceed.
    """
    if requested_profile and requested_profile == current_profile:
        logger.warning(
            f"SAME-PROFILE GUARD: '{current_profile}' agent attempted to delegate "
            f"to another '{requested_profile}' agent. Blocking self-delegation."
        )
        return (
            f"⚠️ SAME-PROFILE SELF-DELEGATION BLOCKED: You are a '{current_profile}' agent "
            f"attempting to delegate to another '{requested_profile}' agent. This creates "
            f"pointless nesting. Execute the task directly with your own tools instead. "
            f"Original task: {message[:500]}"
        )
    return None


def check_circuit_breaker(agent: "Agent", requested_profile: str, message: str = "") -> Optional[str]:
    """Detect delegation loops and excessive depth.

    Walks up the agent hierarchy counting profile occurrences.
    Returns error message string if blocked, None to proceed.
    """
    from python.agent import Agent

    MAX_DELEGATION_DEPTH = 3
    profile_chain = []
    walker = agent
    while walker is not None:
        walker_profile = getattr(walker.config, "profile", "default") or "default"
        profile_chain.append(walker_profile)
        walker = walker.get_data(Agent.DATA_NAME_SUPERIOR)

    # Count occurrences of the requested profile in the chain
    if requested_profile:
        profile_count = profile_chain.count(requested_profile)
        if profile_count >= MAX_DELEGATION_DEPTH:
            chain_str = " → ".join(reversed(profile_chain))
            logger.warning(
                f"CIRCUIT BREAKER: delegation loop detected! "
                f"Profile '{requested_profile}' already appears {profile_count}x in chain: {chain_str}"
            )
            return (
                f"⚠️ DELEGATION LOOP DETECTED: Cannot delegate to profile '{requested_profile}' — "
                f"it already appears {profile_count} times in the agent chain: {chain_str}. "
                f"This indicates a routing loop. You MUST handle this task directly using your "
                f"own tools instead of delegating. The original task was: {message[:500]}"
            )

    # Also check total delegation depth — lowered from 5→4 to save ~30s
    # of wasted LLM calls hitting depth limits
    if len(profile_chain) >= 4:
        chain_str = " → ".join(reversed(profile_chain))
        logger.warning(f"CIRCUIT BREAKER: max delegation depth reached ({len(profile_chain)}): {chain_str}")
        return (
            f"⚠️ MAX DELEGATION DEPTH ({len(profile_chain)}) REACHED. "
            f"Chain: {chain_str}. Handle this task directly with your own tools."
        )

    return None


def check_redelegation_guard_wrapper(agent_data: dict, requested_profile: str, message: str) -> Optional[str]:
    """Check the hard re-delegation guard for same profile+check failures.

    Returns error message string if blocked, None to proceed.
    Also records the attempt if proceeding.
    """
    if not requested_profile:
        return None

    from python.helpers.redelegation_guard import check_redelegation_guard, record_redelegation_attempt

    guard_msg = check_redelegation_guard(agent_data, requested_profile, message)
    if guard_msg:
        logger.warning(
            f"REDELEGATION GUARD: blocked '{requested_profile}' for "
            f"check '{agent_data.get('_last_gate_failing_check', '?')}'"
        )
        return guard_msg
    # Record this attempt BEFORE spawning subordinate
    record_redelegation_attempt(agent_data, requested_profile)
    return None


# ── Phase number extraction patterns ──────────────────────────────────
# Matches: "Phase 3", "Phase 3.1", "Phase 3.1.0", "phase3", "Phase 4"
_PHASE_PATTERN = re.compile(
    r"(?:Phase|PHASE)\s*(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
# Matches: "Wave 1", "Wave 2" — Wave-based delegation is always Phase 3+
_WAVE_PATTERN = re.compile(r"(?:Wave|WAVE)\s*(\d+)", re.IGNORECASE)

# Max violations before escalation
_MAX_PLANNING_VIOLATIONS = 3


def _extract_phase_number(message: str) -> float:
    """Extract the phase sequence number from a delegation message.

    Scans for "Phase N.M.P" or "Wave N" patterns. Wave-based delegations
    are always implementation work (>= 3.0).

    Returns:
        The phase number as a float (e.g., 3.0, 2.5, 4.0).
        Returns 0.0 if no phase pattern found (allows delegation).
    """
    # Check for Wave pattern first — always Phase 3+
    wave_match = _WAVE_PATTERN.search(message)
    if wave_match:
        return 3.0  # All waves are implementation

    # Check for Phase pattern
    phase_match = _PHASE_PATTERN.search(message)
    if phase_match:
        seq_str = phase_match.group(1)
        try:
            # Handle semver-style: "3.1.0" → extract major version
            parts = seq_str.split(".")
            return float(parts[0])
        except (ValueError, IndexError):
            return 0.0

    return 0.0


def check_planning_only_guard(
    agent_data: dict, message: str
) -> Optional[str]:
    """Block Phase 3+ delegations when in planning-only mode.

    Fix B: Deterministic guard that reads agent.data["_planning_only"]
    and parses the delegation message for phase/wave numbers. Blocks
    implementation phases (>= 3.0) with retry-with-guidance.

    Fix C: Tracks violation count. After 3 violations, escalates with
    STOP_AND_DELIVER to force the agent to deliver planning results.

    Args:
        agent_data: The agent's data dict (agent.data).
        message: The delegation message being dispatched.

    Returns:
        Error/guidance message string if blocked, None to proceed.
    """
    # Skip if not in planning-only mode
    if not agent_data.get("_planning_only", False):
        return None

    # Extract phase number from the delegation message
    phase_num = _extract_phase_number(message)

    # Allow planning phases: 0.x and 2.x only.
    # Phase 1.x (scaffold/setup) creates real files — NOT a planning artifact.
    # Phase 3+ is implementation. Both must be blocked in planning-only mode.
    if phase_num < 1.0 or (phase_num >= 2.0 and phase_num < 3.0):
        return None

    # ── BLOCKED: Phase 3+ in planning-only mode ──

    # Increment violation counter
    count = agent_data.get("_planning_only_violation_count", 0) + 1
    agent_data["_planning_only_violation_count"] = count

    logger.warning(
        f"PLANNING-ONLY GUARD: Blocked Phase {phase_num} delegation "
        f"(violation #{count}). Message: {message[:200]}"
    )

    # After 3 violations → escalate with STOP_AND_DELIVER
    if count >= _MAX_PLANNING_VIOLATIONS:
        logger.error(
            f"PLANNING-ONLY GUARD: {count} violations — escalating "
            f"to STOP_AND_DELIVER"
        )
        return (
            f"🛑 STOP_AND_DELIVER: Planning-only mode has blocked "
            f"{count} Phase 3+ delegation attempts. You MUST stop "
            f"attempting implementation work and call the `response` "
            f"tool NOW to deliver the planning summary. Planning "
            f"phases (0-2.7) are complete. No further delegations "
            f"are allowed."
        )

    # Normal violation — retry with guidance
    return (
        f"⚠️ PLANNING-ONLY MODE: Cannot dispatch Phase {phase_num} work. "
        f"Planning phases (0-2.7) are complete. You are in planning-only "
        f"mode — implementation phases (3+) are not permitted. Call the "
        f"`response` tool to deliver the planning summary to the user. "
        f"(Violation {count}/{_MAX_PLANNING_VIOLATIONS} — after "
        f"{_MAX_PLANNING_VIOLATIONS} attempts, this will escalate to "
        f"STOP_AND_DELIVER)"
    )


def check_ontology_capability_guard(
    requested_profile: str,
    message: str = "",
) -> Optional[str]:
    """U-10 (RCA-313), SS-5: Pre-flight ontology capability check for delegation.

    Scans the task ``message`` for references to known tool names, then
    checks whether ``requested_profile`` has access to those tools via
    the ontology.  Returns a **blocking** error string when the
    profile lacks key capabilities, or ``None`` when everything looks
    fine (or when analysis isn't possible).

    SS-5 FIX: Changed from advisory to BLOCKING. The original advisory
    approach failed in production — the orchestrator always ignored the
    warning and delegated anyway, causing E2E agents to receive code
    tasks they couldn't execute (wasting ~40% of delegations).

    5-Why RCA (RCA-313, U-10, SS-5):
        1. FAILED count is ~6K in container logs
        2. ~40% of FAILEDs are from subordinates that couldn't execute
        3. Those subordinates lacked the right tool categories
        4. call_subordinate delegated despite advisory warning
        5. ROOT: Advisory warnings are ignored by LLMs under pressure

    Returns:
        Blocking error string if capability mismatch detected,
        None if no mismatch or analysis impossible.
    """
    if not requested_profile or not message:
        return None

    try:
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        allowed_tools = selector.get_allowed_tools(requested_profile)
    except Exception:
        return None  # Graceful fallback — don't crash delegation

    if not allowed_tools:
        return None  # No ontology data — can't check

    # Build a set of all known tool names across ALL categories
    ontology = selector._ontology
    all_tool_names: set[str] = set()
    for cat_tools in ontology.get("categories", {}).values():
        all_tool_names.update(cat_tools)

    if not all_tool_names:
        return None

    # Extract tool references from the message — SS-5b: use word-boundary
    # matching instead of substring matching to prevent false positives.
    # Root cause (MSR Phase 3): substring matching caused "requirements" in
    # "Read the requirements document" to match tool name `requirements`,
    # producing 12 false-positive blocks on the frontend profile.
    referenced_tools: set[str] = set()
    for tool_name in all_tool_names:
        if _is_tool_reference(tool_name, message):
            referenced_tools.add(tool_name)

    if not referenced_tools:
        return None  # No recognizable tool references found

    # Normalize both sets for comparison (hyphen ↔ underscore)
    allowed_norm = {selector._normalize(t) for t in allowed_tools}
    missing_tools = [
        t for t in referenced_tools
        if selector._normalize(t) not in allowed_norm
    ]

    if not missing_tools:
        return None  # Profile has all referenced tools

    # SS-5: Build BLOCKING error (not advisory)
    missing_str = ", ".join(sorted(missing_tools)[:5])
    profile_categories = ontology.get("profiles", {}).get(requested_profile, [])
    return (
        f"🚫 CAPABILITY MISMATCH (BLOCKED): Profile '{requested_profile}' "
        f"(categories: {', '.join(profile_categories)}) lacks tools "
        f"referenced in the task: [{missing_str}]. Delegate to a "
        f"profile with the appropriate tool categories instead."
    )


def validate_profile_exists(agent_profile: str) -> Optional[str]:
    """Validate that a requested profile directory exists.

    Returns error message string if invalid, None if valid.
    """
    if not agent_profile:
        return None

    from python.helpers import files
    profile_dir = files.get_abs_path("agents", agent_profile)
    if not os.path.isdir(profile_dir):
        valid_profiles = sorted([
            d for d in os.listdir(files.get_abs_path("agents"))
            if os.path.isdir(files.get_abs_path("agents", d))
            and not d.startswith(".") and d != "_example"
        ])
        return (
            f"⚠️ INVALID PROFILE: '{agent_profile}' does not exist. "
            f"Valid profiles: {', '.join(valid_profiles)}. "
            f"Check the profile name and try again."
        )
    return None


def check_rework_cycle_guard(agent: "Agent") -> Optional[str]:
    """ITR-31: Block delegation when Phase 5↔6 rework budget is exhausted.

    Prevents infinite oscillation where the verification gate and code agent
    trade blocks endlessly. After MAX_REWORK_CYCLES attempts, the orchestrator
    must deliver best-effort results instead of continuing to loop.

    The rework count is stored in agent.data["_rework_cycle_count"] and is
    incremented by the Phase 5/6 loop logic each time a verification failure
    triggers a re-delegation to the code agent.

    Args:
        agent: The agent instance (reads agent.data for rework count).

    Returns:
        Error message string if rework budget exhausted, None to proceed.
    """
    from python.helpers.gate_config import MAX_REWORK_CYCLES

    rework_count = agent.data.get("_rework_cycle_count", 0)

    if rework_count >= MAX_REWORK_CYCLES:
        logger.warning(
            f"REWORK CYCLE GUARD: Blocked — {rework_count} rework cycles "
            f"(budget: {MAX_REWORK_CYCLES}). Delivering best-effort results."
        )
        return (
            f"🛑 REWORK BUDGET EXHAUSTED: {rework_count}/{MAX_REWORK_CYCLES} "
            f"Phase 5↔6 rework cycles used. You MUST deliver best-effort "
            f"results NOW via the `response` tool. Do NOT attempt further "
            f"fix-and-verify cycles. Document known issues in the response."
        )

    return None


def increment_rework_cycle(agent_data: dict) -> None:
    """Increment the Phase 5↔6 rework cycle counter.

    Called by the orchestrator gate or completion gate when a verification
    failure triggers a re-delegation to the code agent. This counter is
    read by check_rework_cycle_guard() to enforce the rework budget.

    Args:
        agent_data: The agent's data dict (agent.data).
    """
    current = agent_data.get("_rework_cycle_count", 0)
    agent_data["_rework_cycle_count"] = current + 1
    logger.info(
        f"REWORK CYCLE: Incremented to {current + 1} "
        f"(budget: see gate_config.MAX_REWORK_CYCLES)"
    )


# ── FIX-009: Structural Phase Abandonment (GAP-1, F-1, IL-1) ──────────────
# Architecture ref: §11.5, §13.2
# After repeated failed delegations of the same phase, the orchestrator
# should abandon that phase and proceed to delivery instead of looping
# infinitely. Pre-implementation phases (0-3) are NEVER abandoned.

PHASE_ATTEMPT_KEY = "_phase_delegation_attempts"
MAX_PHASE_ATTEMPTS = 3

# Phases 0-3.9 are pre-implementation and must NEVER be abandoned.
# These phases build the foundation — without them, nothing works.
PRE_IMPLEMENTATION_CEILING = 3.9


def track_phase_attempt(agent_data: dict, phase: str) -> None:
    """Record a failed delegation attempt for a specific phase.

    Called after a subordinate delegation fails. Increments the per-phase
    failure count used by should_abandon_phase() to decide whether to
    give up on a phase.

    Args:
        agent_data: The agent's data dict (agent.data).
        phase: The phase identifier (e.g., "4", "5", "6.1").
    """
    attempts = agent_data.setdefault(PHASE_ATTEMPT_KEY, {})
    attempts[phase] = attempts.get(phase, 0) + 1
    logger.info(
        f"PHASE ATTEMPT: Phase {phase} attempt count is now {attempts[phase]} "
        f"(max: {MAX_PHASE_ATTEMPTS})"
    )


def get_phase_attempt_count(agent_data: dict, phase: str) -> int:
    """Get the number of failed delegation attempts for a phase.

    Args:
        agent_data: The agent's data dict (agent.data).
        phase: The phase identifier.

    Returns:
        Number of failed attempts for this phase.
    """
    return agent_data.get(PHASE_ATTEMPT_KEY, {}).get(phase, 0)


def _parse_phase_number(phase: str) -> float:
    """Parse a phase string to a numeric value.

    Handles formats like "4", "5.1", "Phase 6", etc.

    Args:
        phase: The phase string.

    Returns:
        The numeric phase value, or 99.0 if unparseable
        (treated as post-implementation).
    """
    # Strip "Phase " prefix if present
    cleaned = re.sub(r"^[Pp]hase\s*", "", str(phase).strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        # If we can't parse, treat as post-implementation (safe default)
        return 99.0


def should_abandon_phase(
    agent_data: dict, phase: str
) -> tuple:
    """Check if a phase should be abandoned due to repeated failures.

    Rules:
    - Phases 0-3.9 (pre-implementation): NEVER abandon. These phases
      build requirements, architecture, BDD, TDD — without them the
      project has no foundation.
    - Phases 4+ (post-implementation): Abandon after MAX_PHASE_ATTEMPTS
      (3) failed delegations of the same phase. The orchestrator should
      document known issues and proceed to delivery.

    Args:
        agent_data: The agent's data dict (agent.data).
        phase: The phase identifier (e.g., "4", "5", "6.1").

    Returns:
        Tuple of (should_abandon: bool, message: str).
        - (False, "") if the phase should continue.
        - (True, <message>) if the phase should be abandoned.
    """
    phase_num = _parse_phase_number(phase)

    # Pre-implementation phases: NEVER abandon
    if phase_num <= PRE_IMPLEMENTATION_CEILING:
        return False, ""

    # Post-implementation: check attempt count
    attempts = agent_data.get(PHASE_ATTEMPT_KEY, {})
    count = attempts.get(phase, 0)

    if count >= MAX_PHASE_ATTEMPTS:
        msg = (
            f"🛑 PHASE ABANDONED: Phase {phase} has failed {count} delegation "
            f"attempts (max: {MAX_PHASE_ATTEMPTS}). Marking as abandoned and "
            f"proceeding to delivery. Document ALL known issues in the response."
        )
        logger.warning(f"PHASE ABANDONMENT: {msg}")
        return True, msg

    return False, ""


def reset_phase_attempts(agent_data: dict, phase: str) -> None:
    """Reset the attempt count for a phase (on success).

    Called when a phase delegation succeeds, clearing its failure count.

    Args:
        agent_data: The agent's data dict (agent.data).
        phase: The phase identifier.
    """
    attempts = agent_data.get(PHASE_ATTEMPT_KEY, {})
    if phase in attempts:
        attempts[phase] = 0
        logger.info(f"PHASE ATTEMPT: Reset count for phase {phase}")


# ── F-10: Profile Selection Pre-Validator ──────────────────────────────────
# Pre-delegation intent-based check. Detects what CAPABILITY a task needs
# (file_write, code_execution) via regex patterns on the task message, then
# checks whether the selected profile has that capability. Runs BEFORE SS-5
# (ontology guard) as a faster Layer 1 check.
#
# Root cause: Orchestrator selects profile 'frontend' for tasks that need
# write_to_file/replace_in_file. Subordinate hits PROFILE_ENFORCEMENT blocks,
# wastes turns, and SS-5b eventually auto-corrects. Wastes ~600s per cycle.

_TASK_INTENT_PATTERNS = {
    'file_write': {
        'patterns': [
            r'\b(?:create|write|generate|implement|build|add|modify|edit|update)\s+(?:the\s+)?(?:file|component|page|route|api|endpoint|module|function|class|test)',
            r'\b(?:save|output|produce)\s+(?:the\s+)?(?:file|code|implementation)',
            r'\bPhase\s+3',  # Phase 3 = Implementation = needs file write
            r'\bimplementation\b',
        ],
        'required_capability': 'file_write',
        'valid_profiles': {'code', 'hacker', 'security_auditor', 'mcp_builder'},
        'suggestion': 'code',
    },
    # RCA-239: TDD skeleton expansion, test stub creation, and test file
    # writing must NEVER be delegated to frontend. The frontend agent is a
    # designer — it produces design tokens, mockups, and component specs.
    # TDD work is always the code agent's responsibility.
    'tdd_test_write': {
        'patterns': [
            r'\bTDD\b',
            r'\btest\s+(?:stub|skeleton|expansion|file|suite)',
            r'\b(?:expand|generate|create|write)\s+(?:the\s+)?(?:test|tdd|spec)',
            r'\bPhase\s+2\.8\b',  # Phase 2.8 = TDD Skeleton Expansion
            r'\bskeleton\s+expan',
        ],
        'required_capability': 'file_write',
        'valid_profiles': {'code', 'hacker', 'security_auditor', 'mcp_builder'},
        'suggestion': 'code',
    },
    'code_execution': {
        'patterns': [
            r'\b(?:run|execute|test|install|build|deploy|start|launch)\b',
            r'\bnpm\s+(?:run|test|install|build)',
            r'\bpython\s+-m\s+pytest',
        ],
        'required_capability': 'code_execution',
        'valid_profiles': {'code', 'hacker', 'debug', 'e2e', 'security_auditor', 'mcp_builder'},
        'suggestion': 'code',
    },
}

# Negation patterns that appear BEFORE an intent phrase — e.g.
# "do NOT write any files" should NOT trigger file_write intent.
_NEGATION_BEFORE_INTENT_RE = re.compile(
    r'(?:do\s+not|don\'t|cannot|can\'t|should\s+not|shouldn\'t|'
    r'must\s+not|mustn\'t|never|avoid|without|no)\s+',
    re.IGNORECASE,
)

# Design/research counter-signals — tasks that LOOK like they need
# file_write or code_execution but are actually design or research work.
_DESIGN_RESEARCH_COUNTER_RE = re.compile(
    r'\b(?:design|mockup|wireframe|prototype|layout|style\s+guide|'
    r'ui\s+spec|ux\s+flow|visual|sketch|research|investigate|study|'
    r'analyze\s+(?:versions|patterns|docs)|documentation)\b',
    re.IGNORECASE,
)

# Collect all profiles mentioned across all intent configs. Profiles NOT in
# this set are unknown — we pass them through rather than falsely blocking
# custom or future profiles.
_ALL_KNOWN_PROFILES: set[str] = set()
for _cfg in _TASK_INTENT_PATTERNS.values():
    _ALL_KNOWN_PROFILES.update(_cfg['valid_profiles'])

def validate_profile_for_task(
    profile: str, message: str
) -> Optional[dict]:
    """F-10: Pre-validate that the profile can handle the task.

    Layer 1 (fast, regex-based) check that runs BEFORE SS-5 (ontology
    capability guard). Detects task INTENT — what capability the task
    requires — and checks whether the selected profile has it.

    Args:
        profile: The target profile name (e.g. 'frontend', 'code').
        message: The task message being delegated.

    Returns:
        None if profile is valid for the task (or analysis is impossible).
        Dict with keys:
        - 'mismatch': description of the capability mismatch
        - 'suggested_profile': recommended profile to use instead
        - 'detected_intent': which capability the task requires
    """
    if not profile or not message:
        return None

    # Unknown profiles: if the profile isn't in ANY intent's valid_profiles
    # set AND isn't a known restricted profile, pass through. We only flag
    # profiles we KNOW lack capabilities — don't block custom/future profiles.
    # Known restricted profiles are those NOT in _ALL_KNOWN_PROFILES but ARE
    # known to the system (e.g. frontend, architect, ask, researcher).
    # Truly unknown profiles (not in any config) → pass through.
    _KNOWN_RESTRICTED = {'frontend', 'architect', 'ask', 'researcher'}
    if profile not in _ALL_KNOWN_PROFILES and profile not in _KNOWN_RESTRICTED:
        return None

    message_lower = message.lower()

    # Check for design/research counter-signals — if present, the task
    # is likely design/research work, not file writing or code execution.
    counter_matches = _DESIGN_RESEARCH_COUNTER_RE.findall(message)
    if counter_matches:
        return None  # Design/research task — don't flag

    for intent_name, intent_config in _TASK_INTENT_PATTERNS.items():
        for pattern in intent_config['patterns']:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                # Check negation context — look backwards from match
                # for negation keywords to avoid false positives.
                match_start = match.start()
                window_start = max(0, match_start - 40)
                preceding = message_lower[window_start:match_start]
                if _NEGATION_BEFORE_INTENT_RE.search(preceding):
                    continue  # Negated — skip this match

                # We have a valid intent match — check if profile can do it
                valid_profiles = intent_config['valid_profiles']
                if profile not in valid_profiles:
                    return {
                        'mismatch': (
                            f"Task requires '{intent_name}' capability "
                            f"but profile '{profile}' does not have it. "
                            f"Valid profiles: {', '.join(sorted(valid_profiles))}"
                        ),
                        'suggested_profile': intent_config['suggestion'],
                        'detected_intent': intent_name,
                    }
                else:
                    # Profile is valid for this intent — no mismatch
                    return None

    # No intent patterns matched — can't determine, pass through
    return None

