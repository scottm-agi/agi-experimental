"""Phase cap enforcement mechanism.

Prevents the orchestrator from delegating work past a specified phase
boundary. When a user prompt says "Phase 0 through 3.5", the system
sets _phase_cap=3.5 and blocks delegations past that phase.

Architecture (ITR-45 v2 — forced completion, not pause):
- extract_phase_scope() → parses prompt/message text for phase boundaries
- check_phase_cap_allows() → enforces phase cap on delegations
- check_phase_cap_reached() → detects when current phase exceeds cap
- _phase_cap key in agent.data → persisted, project-scoped
- _phase_cap_reached key in agent.data → triggers forced completion

Wire points:
1. _03_prompt_capture.py → calls extract_phase_scope() on initial prompt
2. _02_user_stop_directive.py → scans EVERY user message for phase cap updates
3. call_subordinate.py → calls check_phase_cap_allows() before delegation
4. _22_multiagentdev_completion_gate.py → force-allows response when cap reached

Phase cap is a FORCED COMPLETION — when hit, the agent is told to deliver
what it has via the response tool. It is NOT a pause (pauses are designed
to be un-paused by the system). It follows the same pattern as
_user_stop_directive: set flag → inject instruction → open gates.

Born from ITR-45 RCA: System had no mechanism to halt at a phase boundary.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("agix.phase_cap")


def extract_phase_scope(prompt_text: str) -> Optional[float]:
    """Extract phase cap from user prompt or message text.

    Understands BOTH:
    - Category names (how users/agents think): "stop after implementation"
    - Phase numbers (how the skill defines phases): "Phase 0 through 3.5"

    Category → max phase mapping (from SKILL.md canonical table):
      PLANNING       → 1.0  (covers 0, 0.1, 0.5, 0.5b, 1)
      DESIGN         → 2.8  (covers 2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8)
      IMPLEMENTATION → 3.9  (covers 3, 3.5, 3.8, 3.8.1, 3.9)
      VERIFICATION   → 5.3  (covers 4, 4.5, 4.7, 4.8, 4.9, 4.95, 5, 5.0.x, 5.1-5.3)
      DEPLOYMENT     → 7.0  (covers 5.5, 7)

    Priority order:
    1. Explicit stop directives (highest)
    2. Category-based scope (users/agents think in categories)
    3. Phase number ranges (skill instructions)

    Returns:
        Float phase cap, or None if no scope detected.
    """
    text = prompt_text.upper()

    # ── Category → max phase cap mapping ──
    # Each category maps to the LAST phase number in that category
    _CATEGORY_CAP = {
        "PLANNING": 1.0,
        "DESIGN": 2.8,
        "IMPLEMENTATION": 3.9,
        "VERIFICATION": 5.3,
        "DEPLOYMENT": 7.0,
    }

    # Also match natural language variations
    _CATEGORY_ALIASES = {
        "PLANNING": ("PLANNING", "PLAN"),
        "DESIGN": ("DESIGN", "ARCHITECT", "ARCHITECTURE"),
        "IMPLEMENTATION": ("IMPLEMENTATION", "IMPLEMENT", "CODING", "CODE", "BUILD", "TDD", "BDD"),
        "VERIFICATION": ("VERIFICATION", "VERIFY", "TESTING", "TEST", "E2E", "INTEGRATION"),
        "DEPLOYMENT": ("DEPLOYMENT", "DEPLOY", "PUBLISH", "PUSH"),
    }

    # ── Priority 1: Explicit stop directives with phase numbers ──

    # "STOP before Phase N" → cap at N - 0.5
    match = re.search(r'STOP\s+(?:AT\s+)?BEFORE\s+(?:START\s+OF\s+)?PHASE\s+(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(1)) - 0.5

    # "Do NOT proceed to Phase N" → cap at N - 0.5
    match = re.search(r'(?:DO\s+)?NOT\s+PROCEED\s+TO\s+PHASE\s+(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(1)) - 0.5

    # "stop at Phase N" → cap AT N (inclusive)
    match = re.search(r'STOP\s+AT\s+PHASE\s+(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(1))

    # ── Priority 2: Category-based scope ──

    # "stop after implementation" / "through implementation complete"
    # "only run planning and design" / "stop before verification"
    for category, aliases in _CATEGORY_ALIASES.items():
        alias_pattern = "|".join(aliases)

        # "stop after CATEGORY" / "through CATEGORY complete"
        if re.search(rf'(?:STOP\s+AFTER|THROUGH)\s+(?:{alias_pattern})\s*(?:COMPLETE|PHASE)?', text):
            return _CATEGORY_CAP[category]

        # "stop before CATEGORY" / "do not proceed to CATEGORY"
        if re.search(rf'(?:STOP\s+BEFORE|NOT\s+PROCEED\s+TO|BEFORE)\s+(?:{alias_pattern})', text):
            # Cap at the PREVIOUS category's max phase
            categories = list(_CATEGORY_CAP.keys())
            idx = categories.index(category)
            if idx > 0:
                prev_category = categories[idx - 1]
                return _CATEGORY_CAP[prev_category]
            return 0.0  # "before planning" = don't run anything

    # ── Priority 3: "through Phase N complete" ──
    match = re.search(r'THROUGH\s+PHASE\s+(\d+(?:\.\d+)?)\s*(?:COMPLETE(?:D)?)?', text)
    if match:
        return float(match.group(1))

    # ── Priority 4: Range patterns — return FIRST match ──

    # "PHASES ONLY (X through Y)" — test launcher format
    match = re.search(r'PHASES?\s+ONLY\s*\(\s*(\d+(?:\.\d+)?)\s+THROUGH\s+(\d+(?:\.\d+)?)\s*\)', text)
    if match:
        return float(match.group(2))

    # "Phase X through Y"
    match = re.search(r'PHASE\s+(\d+(?:\.\d+)?)\s+THROUGH\s+(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(2))

    # Bare "(X through Y)" — parenthetical range
    match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s+THROUGH\s+(\d+(?:\.\d+)?)\s*\)', text)
    if match:
        return float(match.group(2))

    # "Phase X-Y"
    match = re.search(r'PHASE\s+\d+(?:\.\d+)?\s*-\s*(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(1))

    # "only run through/up to Phase N"
    match = re.search(r'ONLY\s+(?:RUN\s+)?(?:THROUGH|UP\s+TO)\s+PHASE\s+(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(1))

    return None


def check_phase_cap_allows(current_phase: float, phase_cap: float) -> bool:
    """Check if a delegation is allowed given the current phase and cap.

    The phase cap uses MAJOR PHASE (integer part) semantics:
    - cap=3.5 means "complete Phase 3 but not Phase 4"
    - So 3.0, 3.1, ..., 3.9 are all allowed (same major phase = 3)
    - 4.0+ is blocked (major phase 4 > major phase 3)

    RCA-464: Previously used simple float comparison (3.7 <= 3.5 = False),
    which blocked Phase 3 sub-tasks when cap was 3.5. The decomposition
    skill uses sub-phases 3.6, 3.7, 3.8, 3.9 which are all WITHIN Phase 3
    but were incorrectly blocked by the float comparison.

    Args:
        current_phase: The phase number of the delegation being attempted.
        phase_cap: The maximum phase allowed.

    Returns:
        True if delegation is allowed, False if blocked.
    """
    # If the major phase (integer part) is within the cap's major phase, allow it.
    # This means cap=3.5 allows ALL of 3.x (3.0 through 3.9).
    if int(current_phase) <= int(phase_cap):
        return True
    # If major phase exceeds cap's major phase, use exact comparison
    return current_phase <= phase_cap


def check_phase_cap_reached(agent_data: dict) -> bool:
    """Check if the current phase has reached or exceeded the phase cap.

    This is the READER-side check — called by the completion gate and
    message loop to determine if forced completion should trigger.

    Args:
        agent_data: The agent's data dict.

    Returns:
        True if phase cap is set AND current phase >= cap, else False.
    """
    phase_cap = agent_data.get("_phase_cap")
    if phase_cap is None:
        return False

    # If _phase_cap_reached was already set, respect it
    if agent_data.get("_phase_cap_reached"):
        return True

    current_phase = agent_data.get("_current_phase")
    if current_phase is None:
        return False

    try:
        return float(current_phase) >= float(phase_cap)
    except (ValueError, TypeError):
        return False


# ── Forced completion instruction (injected like _user_stop_directive) ──
PHASE_CAP_COMPLETION_INSTRUCTION = (
    "\n\n> [!CAUTION]\n"
    "> **🛑 PHASE CAP REACHED — FORCED COMPLETION**\n"
    "> \n"
    "> The user specified a phase boundary and it has been reached. You MUST:\n"
    "> \n"
    "> 1. **DO NOT** start any new delegations, tasks, or phase transitions.\n"
    "> 2. **DELIVER** your current results immediately via the `response` tool.\n"
    "> 3. **SUMMARIZE** what was completed and what phases were executed.\n"
    "> 4. All quality gates have been opened — your response WILL be accepted.\n"
    "> \n"
    "> This directive overrides ALL other instructions and phase plans.\n"
    "> The user's phase boundary takes priority over skill instructions.\n"
    "> Comply NOW.\n"
)
