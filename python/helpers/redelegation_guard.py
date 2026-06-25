"""
Hard Re-Delegation Guard — structural prevention of re-delegation loops.

When the orchestrator's quality gate blocks a response due to a failing check,
the LLM may re-delegate to the same agent profile to "fix" the issue. If
the subordinate can't fix it (e.g., it's a fundamental architecture issue),
this creates an infinite loop: gate blocks → re-delegate → gate blocks → ...

This module provides a STRUCTURAL guard (code-level enforcement, not
behavioral LLM instructions) that:

1. Tracks which profile+check combos have been attempted
2. Allows the first delegation + one retry (2 attempts total)
3. Refuses the 3rd+ attempt at the tool level — the LLM has no choice
4. When ALL delegation paths are exhausted, forces `response` (Iteration 159)

This mirrors Roo-Code's approach where re-delegation is architecturally
impossible (parent is disposed), adapted for AGIX's in-process model.

See: docs/architecture/roocode-vs-agix-orchestrator-audit.md §4.2, §10 P1 #3

Data keys used (all on agent.data):
  - _last_gate_failing_check: str — name of the check that blocked
  - _gate_redelegation_tracker: dict[str, int] — "profile::check" → attempt count
"""

import logging
from typing import Optional

logger = logging.getLogger("agix.redelegation_guard")

# Maximum re-delegation attempts for same profile + same failing check
MAX_REDELEGATION_ATTEMPTS = 2

# RCA-316: Minimum distinct profiles that must be tried before declaring
# "all delegation paths exhausted". Prevents the guard from trivially
# exhausting after 2 attempts on ONE profile (e.g., code::Manifest fidelity)
# while browser_agent and e2e have never been tried.
MIN_PROFILES_BEFORE_EXHAUSTION = 2

# RCA-338: Verification/infrastructure profiles that are NEVER blocked by
# the redelegation guard. These profiles exist to VERIFY, not to retry fixes.
# Blocking them creates a deadlock: gate demands verification → guard blocks
# the delegation → gate blocks again → force-deliver without verification.
INFRASTRUCTURE_EXEMPT_PROFILES = frozenset({"e2e", "browser_agent", "qa", "tester"})

# RCA-339: Soft ceiling for exempt profiles — advisory, NEVER blocking.
# Emits supervisor signal when an infrastructure profile loops excessively.
# The supervisor decides the appropriate intervention strategy.
MAX_EXEMPT_ATTEMPTS = 5

# SS-7b: Profile recommendations per failing gate check.
# Maps check name keywords → recommended subordinate profile.
# When a redelegation is refused, suggest the RIGHT profile to fix the issue.
# Ordered by specificity — first match wins.
_CHECK_KEYWORD_TO_PROFILE = [
    # Build / compilation issues → code agent
    ("build", "code"),
    ("compile", "code"),
    ("type check", "code"),
    ("typescript", "code"),
    # Test issues → code agent
    ("test", "code"),
    ("tdd", "code"),
    ("assertion", "code"),
    ("coverage", "code"),
    # Content / UI issues → code agent (code writes pages, frontend designs)
    ("content", "code"),
    ("page", "code"),
    ("route", "code"),
    ("nav", "code"),
    ("stub", "code"),
    ("fidelity", "code"),
    ("component", "code"),
    ("manifest", "code"),
    # Design / style issues → frontend designer profile
    ("design", "frontend"),
    ("token", "frontend"),
    ("css", "frontend"),
    ("style", "frontend"),
    # BDD / requirements → code agent (BDD is a code artifact)
    ("bdd", "code"),
    ("requirement", "code"),
    ("scenario", "code"),
    # Verification → e2e or browser_agent
    ("e2e", "e2e"),
    ("browser", "browser_agent"),
    ("screenshot", "browser_agent"),
    ("uat", "browser_agent"),
    # Security → code agent
    ("secret", "code"),
    ("env", "code"),
    # Deployment → code agent
    ("deploy", "code"),
    ("server", "code"),
    ("health", "code"),
]


def recommend_profile_for_check(failing_check: str, exclude_profile: str = "") -> str:
    """Recommend the best profile to fix a failing gate check.

    Args:
        failing_check: Name of the check that blocked (e.g. "Build cache health")
        exclude_profile: Profile to exclude from recommendations (the one that failed)

    Returns:
        Recommended profile name, or "code" as default.
    """
    check_lower = failing_check.lower()
    for keyword, profile in _CHECK_KEYWORD_TO_PROFILE:
        if keyword in check_lower and profile != exclude_profile:
            return profile
    # Default: code agent can fix most things
    return "code" if exclude_profile != "code" else "frontend"


def is_all_delegation_exhausted(agent_data: dict) -> bool:
    """Check if ALL tracked delegation paths are exhausted.

    Returns True when every entry in the re-delegation tracker has
    reached or exceeded MAX_REDELEGATION_ATTEMPTS AND at least
    MIN_PROFILES_BEFORE_EXHAUSTION distinct profiles have been tried.

    RCA-316: Previously, `all()` over a single-entry dict ({"code::X": 2})
    was trivially True, causing the guard to declare exhaustion after 2
    attempts on ONE profile. The orchestrator never tried browser_agent,
    e2e, or other verification profiles before the escape hatch opened.

    Args:
        agent_data: The orchestrator agent's data dict

    Returns:
        True if all delegation paths are exhausted, False otherwise
    """
    tracker = agent_data.get("_gate_redelegation_tracker", {})
    if not tracker:
        return False

    # RCA-338: Filter out exempt profiles — they can always be delegated to,
    # so they don't count toward "all paths exhausted".
    non_exempt_entries = {
        k: v for k, v in tracker.items()
        if k.split("::")[0] not in INFRASTRUCTURE_EXEMPT_PROFILES
    }
    if not non_exempt_entries:
        return False  # Only exempt profiles in tracker — not exhausted

    # U-3 (RCA-339): When _relevant_delegation_profiles is set, only those
    # profiles count toward exhaustion. This allows single-profile exhaustion
    # for code-fix tasks (e.g., only "code" is relevant for an npm_install fix)
    # without requiring MIN_PROFILES_BEFORE_EXHAUSTION different profiles.
    relevant_profiles = agent_data.get("_relevant_delegation_profiles")
    if relevant_profiles:
        # Filter tracker to only relevant profiles
        relevant_entries = {
            k: v for k, v in non_exempt_entries.items()
            if k.split("::")[0] in relevant_profiles
        }
        # All relevant profiles must be present AND exhausted
        profiles_in_tracker = {k.split("::")[0] for k in relevant_entries.keys()}
        if len(profiles_in_tracker) < len(relevant_profiles):
            logger.info(
                f"[REDELEGATION_GUARD] Relevant profiles: {relevant_profiles}, "
                f"only {profiles_in_tracker} in tracker — not exhausted"
            )
            return False
        return all(
            attempts >= MAX_REDELEGATION_ATTEMPTS
            for attempts in relevant_entries.values()
        )

    # Default path: RCA-316 — require MIN_PROFILES_BEFORE_EXHAUSTION
    profiles_attempted = {key.split("::")[0] for key in non_exempt_entries.keys()}
    if len(profiles_attempted) < MIN_PROFILES_BEFORE_EXHAUSTION:
        logger.info(
            f"[REDELEGATION_GUARD] Only {len(profiles_attempted)} non-exempt profile(s) tried "
            f"({', '.join(profiles_attempted)}), need {MIN_PROFILES_BEFORE_EXHAUSTION} "
            f"before declaring all paths exhausted"
        )
        return False

    return all(
        attempts >= MAX_REDELEGATION_ATTEMPTS
        for attempts in non_exempt_entries.values()
    )


def detect_compound_deadlock(agent_data: dict) -> dict:
    """Detect compound deadlock: gate block + redeleg refusal cycle.

    U-2 (RCA-339): A compound deadlock occurs when:
    1. A gate check has blocked the response (gate_block signal)
    2. The redelegation guard has refused re-delegation (redeleg_refused signal)
    These two conditions together mean the agent is trapped: it can't deliver
    (gate blocks) and it can't delegate (guard blocks) — a deadlock.

    The optional near_dup signal (near-duplicate response) makes the deadlock
    more severe but is not required for detection.

    Args:
        agent_data: The orchestrator agent's data dict.

    Returns:
        Dict with keys:
        - detected: bool — whether a compound deadlock was detected
        - failing_check: str — the check causing the deadlock (if detected)
        - escalation_message: str — message for supervisor (if detected)
    """
    signals = agent_data.get("_compound_deadlock_signals", {})
    if not signals:
        return {"detected": False}

    gate_block = signals.get("gate_block", False)
    redeleg_refused = signals.get("redeleg_refused", False)

    # Compound deadlock requires at least 2 signals: gate_block + redeleg_refused
    if not (gate_block and redeleg_refused):
        return {"detected": False}

    # Extract the failing check for context
    block_details = agent_data.get("_last_gate_block_details", {})
    failing_check = block_details.get("check_name", "unknown")
    block_message = block_details.get("block_message", "")

    # Build supervisor escalation message
    near_dup = signals.get("near_dup", False)
    severity = "CRITICAL" if near_dup else "HIGH"
    escalation = (
        f"🔒 COMPOUND DEADLOCK DETECTED [{severity}]\n"
        f"Failing check: '{failing_check}'\n"
        f"Signals: gate_block={gate_block}, redeleg_refused={redeleg_refused}, "
        f"near_dup={near_dup}\n"
        f"Detail: {block_message}\n"
        f"The agent is trapped — cannot deliver (gate blocks) and cannot "
        f"delegate (guard blocks). Supervisor intervention required."
    )

    logger.warning(
        f"[REDELEGATION_GUARD] COMPOUND DEADLOCK: "
        f"check='{failing_check}', severity={severity}"
    )

    return {
        "detected": True,
        "failing_check": failing_check,
        "escalation_message": escalation,
        "severity": severity,
    }


def get_completion_fallback_message() -> str:
    """Generate a strong directive for the orchestrator to call `response`.

    Used when all re-delegation paths are exhausted: the orchestrator
    must respond with its current results instead of trying more delegations.

    Returns:
        A clear, forceful message directing the agent to call `response`.
    """
    return (
        "\n\n🚨 **ALL DELEGATION PATHS EXHAUSTED — FORCE RESPOND NOW**\n"
        "Every delegation attempt for the current failing gate check has been refused. "
        "DO NOT delegate further — no more subordinate agents will be spawned.\n\n"
        "**You MUST call `response` immediately** with your current results. "
        "Include any caveats about incomplete checks, but deliver what you have. "
        "DO NOT attempt another `call_subordinate` or `call_subordinate_batch`. "
        "The only allowed next action is `response`."
    )


def check_redelegation_guard(
    agent_data: dict,
    target_profile: str,
    message: str,
) -> Optional[str]:
    """Check if a delegation should be refused due to re-delegation loop.

    Args:
        agent_data: The orchestrator agent's data dict
        target_profile: Profile being delegated to (e.g., "code", "architect")
        message: The delegation message (for logging only)

    Returns:
        None if delegation is ALLOWED
        Error message string if delegation is REFUSED
    """
    # RCA-338: Infrastructure-exempt profiles bypass the guard entirely.
    # These profiles exist to VERIFY, not to retry fixes — blocking them
    # creates a deadlock where the gate demands verification but the guard
    # prevents the delegation needed to satisfy it.
    if target_profile in INFRASTRUCTURE_EXEMPT_PROFILES:
        # RCA-339: Track attempts for exempt profiles and emit signal at ceiling
        failing_check = agent_data.get("_last_gate_failing_check", "unknown")
        tracker = agent_data.get("_gate_redelegation_tracker", {})
        key = f"{target_profile}::{failing_check}"
        attempts = tracker.get(key, 0)

        if attempts >= MAX_EXEMPT_ATTEMPTS:
            logger.warning(
                f"[REDELEGATION_GUARD] EXEMPT CEILING: '{target_profile}' "
                f"delegated {attempts}x for '{failing_check}' — "
                f"escalating to supervisor"
            )
            # Emit structured signal for intelligent supervisor consumption
            signals = agent_data.setdefault("_l2_escalation_signals", [])
            signals.append({
                "severity": "warning",
                "detector": "exempt_ceiling_exceeded",
                "detail": (
                    f"Exempt profile '{target_profile}' at {attempts} "
                    f"attempts for '{failing_check}'"
                ),
            })
        else:
            logger.info(
                f"[REDELEGATION_GUARD] EXEMPT: profile='{target_profile}' is "
                f"infrastructure-exempt — allowed (attempt {attempts + 1})"
            )
        return None  # ALWAYS allow — never block exempt profiles

    failing_check = agent_data.get("_last_gate_failing_check")
    if not failing_check:
        # No gate failure recorded — no guard needed
        return None

    tracker = agent_data.get("_gate_redelegation_tracker", {})
    key = f"{target_profile}::{failing_check}"
    attempts = tracker.get(key, 0)

    if attempts < MAX_REDELEGATION_ATTEMPTS:
        # Still within budget — allow
        return None

    # F-12: Check for compound deadlock BEFORE refusing.
    # If compound deadlock is detected (gate_block + redeleg_refused both set),
    # the agent is trapped — refusing delegation makes it worse.
    # Override: allow one more delegation to break the cycle.
    deadlock_result = detect_compound_deadlock(agent_data)
    if deadlock_result.get("detected"):
        logger.warning(
            f"[REDELEGATION_GUARD] COMPOUND DEADLOCK OVERRIDE: "
            f"Allowing redelegation to '{target_profile}' despite {attempts} "
            f"attempts — deadlock detected for '{deadlock_result.get('failing_check')}'"
        )
        # Set override flag so downstream consumers know this is a deadlock-break
        agent_data["_compound_deadlock_override"] = True
        # Clear the deadlock signals so we don't loop on the override
        agent_data.pop("_compound_deadlock_signals", None)
        return None  # ALLOW the delegation to break the deadlock

    # REFUSED — structural prevention
    logger.warning(
        f"[REDELEGATION_GUARD] REFUSED: profile='{target_profile}' "
        f"already attempted {attempts}x for failing check '{failing_check}'. "
        f"Message preview: {message[:100]}"
    )

    # F-1/F-7: Write redeleg_refused signal for compound deadlock detection.
    # Without this, detect_compound_deadlock() never fires because no one
    # sets the signal it reads.
    signals = agent_data.setdefault("_compound_deadlock_signals", {})
    signals["redeleg_refused"] = True
    agent_data["_compound_deadlock_signals"] = signals

    recommended = recommend_profile_for_check(failing_check, exclude_profile=target_profile)
    refusal = (
        f"⚠️ RE-DELEGATION REFUSED: You already delegated to '{target_profile}' "
        f"{attempts} times for the same failing gate check ('{failing_check}'). "
        f"The subordinate cannot fix this issue. You must either:\n"
        f"1. Delegate to profile '{recommended}' — best match for '{failing_check}'\n"
        f"2. Fix the issue yourself using code_execution tool directly\n"
        f"3. Respond with what you have (the gate will bypass after enough blocks)\n\n"
        f"Exhausted profile+check: {target_profile}::{failing_check}"
    )

    # RC-6.2 Fix: Clear stale failing check after refusal.
    # Without this, the _last_gate_failing_check persists and causes ALL
    # future delegations to the same profile to be refused — even if they
    # target a completely different task (e.g., email fidelity delegation
    # refused because gate's last failure was "Build cache health").
    agent_data.pop("_last_gate_failing_check", None)

    # Append completion fallback if ALL delegation paths are exhausted
    if is_all_delegation_exhausted(agent_data):
        refusal += get_completion_fallback_message()
        # RCA 220 FIX: STRUCTURAL bypass — not just behavioral text.
        # Without this, the "FORCE RESPOND NOW" message tells the LLM to
        # call response, but the completion gate blocks it again, causing
        # 4+ duplicate response messages. Setting _error_state_bypassed
        # makes the gate skip ALL quality checks on the next response.
        agent_data["_error_state_bypassed"] = True
        # R5 Fix: Also set _error_state_bypass_phase so the phase-transition
        # reset in orchestrator_gate_common.py doesn't immediately clear the
        # bypass (it compares current_phase > bypass_phase; without this,
        # bypass_phase defaults to 0 and any current_phase > 0 triggers reset).
        agent_data["_error_state_bypass_phase"] = agent_data.get("_current_phase", 0)
        # RC-6.3 Fix: Clear response state so the FIRST post-bypass
        # response attempt goes through cleanly without triggering
        # near-duplicate detection or stale content comparisons.
        agent_data.pop("_last_response_content", None)
        agent_data.pop("_last_blocked_response", None)
        agent_data["_consecutive_duplicate_responses"] = 0
        logger.warning(
            "[REDELEGATION_GUARD] ALL delegation paths exhausted — "
            "set _error_state_bypassed=True for structural gate bypass"
        )

    return refusal


def record_redelegation_attempt(agent_data: dict, target_profile: str) -> None:
    """Record a delegation attempt for tracking.

    Called by call_subordinate AFTER the guard passes and BEFORE spawning
    the subordinate. Only records when a failing check is active.

    Args:
        agent_data: The orchestrator agent's data dict
        target_profile: Profile being delegated to
    """
    failing_check = agent_data.get("_last_gate_failing_check")
    if not failing_check:
        return

    tracker = agent_data.get("_gate_redelegation_tracker", {})
    key = f"{target_profile}::{failing_check}"
    tracker[key] = tracker.get(key, 0) + 1
    agent_data["_gate_redelegation_tracker"] = tracker

    logger.info(
        f"[REDELEGATION_GUARD] Recorded attempt: {key} = {tracker[key]}"
    )


def set_failing_check(agent_data: dict, check_name: str) -> None:
    """Record which check caused the gate to block.

    Called by run_gate_checks when a check blocks the response.

    RCA-337: When the failing check CHANGES (gate transitions from Check A
    to Check B), this is a "phase transition" — the old problem is implicitly
    resolved (or at least different). We clear stale tracker entries for the
    old check so fresh delegations are allowed for the new problem.

    Without this, the tracker carries forward attempt counts from the old
    check, causing the guard to refuse delegations for a completely new
    problem that the code agent has never tried to fix.

    Args:
        agent_data: The orchestrator agent's data dict
        check_name: Name of the failing check
    """
    old_check = agent_data.get("_last_gate_failing_check")
    agent_data["_last_gate_failing_check"] = check_name

    # RCA-337: Phase transition — clear stale entries for the OLD check
    if old_check and old_check != check_name:
        tracker = agent_data.get("_gate_redelegation_tracker", {})
        stale_keys = [k for k in tracker if k.endswith(f"::{old_check}")]
        if stale_keys:
            for k in stale_keys:
                del tracker[k]
            agent_data["_gate_redelegation_tracker"] = tracker
            logger.info(
                f"[REDELEGATION_GUARD] Phase transition: "
                f"'{old_check}' -> '{check_name}' — "
                f"cleared {len(stale_keys)} stale tracker entries: "
                f"{', '.join(stale_keys)}"
            )
        else:
            logger.info(
                f"[REDELEGATION_GUARD] Phase transition: "
                f"'{old_check}' -> '{check_name}' (no stale entries)"
            )
    else:
        logger.info(f"[REDELEGATION_GUARD] Failing check set: {check_name}")


def store_gate_block_details(
    agent_data: dict,
    check_name: str,
    block_message: str,
) -> None:
    """Store structured gate block details for downstream context injection.

    Maintains two data structures on agent_data:
    1. _last_gate_block_details: Full details of the LATEST block (check name,
       message, cumulative count for this check).
    2. _gate_block_history: Condensed 1-liner trajectory of all blocks,
       capped at 10 entries. Each entry = {check, summary}.

    RCA-339 Part 2: Companion to set_failing_check.

    Args:
        agent_data: The orchestrator agent's data dict
        check_name: Name of the check that blocked
        block_message: The block/refusal message text
    """
    # Update current block details
    current = agent_data.get("_last_gate_block_details", {})
    old_check = current.get("check_name", "")
    old_count = current.get("block_count", 0)

    # Increment count if same check, reset if different
    new_count = old_count + 1 if old_check == check_name else 1

    agent_data["_last_gate_block_details"] = {
        "check_name": check_name,
        "block_message": block_message if block_message else "",
        "block_count": new_count,
    }

    # Append to condensed history trail (max 10 entries)
    history = agent_data.setdefault("_gate_block_history", [])
    # Create 1-liner summary from block message
    summary = (block_message or "").replace("\n", " ").strip()
    if not summary:
        summary = f"Check '{check_name}' blocked (no message)"
    history.append({"check": check_name, "summary": summary})

    # Cap at 10 — keep the most recent entries
    if len(history) > 10:
        agent_data["_gate_block_history"] = history[-10:]

    # F-1/F-7: Write gate_block signal for compound deadlock detection.
    # Without this, detect_compound_deadlock() never fires because no one
    # sets the gate_block signal it reads.
    signals = agent_data.setdefault("_compound_deadlock_signals", {})
    signals["gate_block"] = True
    agent_data["_compound_deadlock_signals"] = signals

    logger.info(
        f"[REDELEGATION_GUARD] Block details stored: "
        f"check='{check_name}', count={new_count}, "
        f"history={len(agent_data['_gate_block_history'])} entries"
    )


def clear_redelegation_tracker(agent_data: dict) -> None:
    """Clear the re-delegation tracker and failing check.

    Called when ALL gate checks pass — the issue is resolved,
    so the tracker should be reset for the next cycle.

    Args:
        agent_data: The orchestrator agent's data dict
    """
    agent_data["_gate_redelegation_tracker"] = {}
    agent_data.pop("_last_gate_failing_check", None)
    # RCA-339: Clear gate failure context (Part 2) to prevent stale injection
    agent_data.pop("_last_gate_block_details", None)
    agent_data.pop("_gate_block_history", None)
    # F-1/F-7: Clear compound deadlock signals when tracker is cleared.
    agent_data.pop("_compound_deadlock_signals", None)
    logger.info("[REDELEGATION_GUARD] Tracker cleared (all checks passed)")


def consult_ledger_for_check(project_dir: str, check_name: str) -> str:
    """Consult the VerificationLedger to decide if a check should block.

    F-RC4: The _19 subordinate gate uses simple validators while the _22
    orchestrator uses comprehensive ones. When the subordinate already
    exhausted fix attempts for a check (verdict='unfixable' or 'exhausted'),
    the orchestrator should downgrade to advisory instead of blocking.

    Args:
        project_dir: Path to the project root directory.
        check_name: Name of the check to consult (e.g. 'boilerplate').

    Returns:
        'advisory' — if ledger says passed/unfixable/exhausted (don't block)
        'block' — if ledger says fixable or has no entry (block normally)
    """
    try:
        from python.helpers.verification_ledger import VerificationLedger

        ledger = VerificationLedger(project_dir)
        checks = ledger.state.get("checks", {})
        check_state = checks.get(check_name, {})
        verdict = check_state.get("verdict", "unknown")

        # Passed, unfixable, or exhausted → subordinate already dealt with it
        if verdict in ("passed", "unfixable", "exhausted"):
            logger.info(
                f"[LEDGER_AWARE_GATE] Check '{check_name}' has ledger "
                f"verdict='{verdict}' → downgrading to advisory"
            )
            return "advisory"

        # Fixable or unknown → block normally
        return "block"
    except Exception as e:
        logger.warning(
            f"[LEDGER_AWARE_GATE] Failed to consult ledger for "
            f"'{check_name}': {e} → defaulting to block"
        )
        return "block"
