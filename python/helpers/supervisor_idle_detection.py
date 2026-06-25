"""
Supervisor Idle Agent Detection.

Detects agents that have gone idle ("Waiting for input") while their work
is incomplete (e.g., gate rejections pending, project not delivered).

Root Cause (5-WHY):
  WHY 1: Supervisor didn't detect the idle agent.
  WHY 2: Pattern detectors only check for ACTIVE anomalies (loops, failures).
  WHY 3: When agent loop ends, iteration/context metrics freeze.
  WHY 4: No detector checks time-since-last-activity.
  WHY 5: AgentState.last_response_time is set to NOW (snapshot time),
         not the agent's actual last activity time.

Fix: Track iteration changes across supervisor checks. If iteration hasn't
changed for IDLE_THRESHOLD_CHECKS consecutive checks AND the agent has
indicators of incomplete work, flag as idle and trigger a nudge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("agix.supervisor_idle_detection")

# ─── Constants ─────────────────────────────────────────────────────────
# At 5s check interval, 60 checks = 5 minutes of idle time
IDLE_THRESHOLD_CHECKS = 60

# After a nudge, wait this many checks before nudging again (2.5 minutes)
IDLE_NUDGE_COOLDOWN_CHECKS = 30

# Maximum nudges before giving up (prevent infinite nudge loops)
MAX_IDLE_NUDGES = 3


@dataclass
class IdleDetectionState:
    """Tracks idle state for a single monitored agent.
    
    Attached to MonitoredAgent to persist across supervisor check cycles.
    """
    last_seen_iteration: int = -1
    idle_check_count: int = 0
    nudge_count: int = 0
    checks_since_last_nudge: int = 0

    def update(self, iteration: int) -> None:
        """Update state with current agent iteration.
        
        If iteration changed, reset idle counter.
        If same, increment idle counter.
        """
        if iteration != self.last_seen_iteration:
            # Agent did work — reset ALL idle/nudge tracking (fresh sequence per stall)
            self.last_seen_iteration = iteration
            self.idle_check_count = 0
            self.nudge_count = 0
            self.checks_since_last_nudge = 0
        else:
            # Same iteration — agent is idle
            self.idle_check_count += 1
            self.checks_since_last_nudge += 1

    def record_nudge(self) -> None:
        """Record that a nudge was sent."""
        self.nudge_count += 1
        self.checks_since_last_nudge = 0


def check_agent_idle(
    state: IdleDetectionState,
    has_incomplete_work: bool,
) -> bool:
    """Check if an agent should be nudged for being idle.
    
    Returns True if ALL conditions are met:
    1. Agent has been idle for >= IDLE_THRESHOLD_CHECKS consecutive checks
    2. Agent has indicators of incomplete work
    3. Nudge count < MAX_IDLE_NUDGES (don't nudge forever)
    4. Cooldown has elapsed since last nudge
    
    Args:
        state: The agent's idle detection state.
        has_incomplete_work: Whether the agent has indicators of incomplete work
            (e.g., gate rejections > 0, active project context).
    
    Returns:
        True if the agent should be nudged.
    """
    if not has_incomplete_work:
        return False
    
    if state.idle_check_count < IDLE_THRESHOLD_CHECKS:
        return False
    
    if state.nudge_count >= MAX_IDLE_NUDGES:
        return False
    
    # Cooldown check: don't nudge too frequently
    if state.nudge_count > 0 and state.checks_since_last_nudge < IDLE_NUDGE_COOLDOWN_CHECKS:
        return False
    
    return True


def build_idle_nudge_message(nudge_count: int, idle_minutes: float) -> str:
    """Build a nudge message for an idle agent.
    
    Args:
        nudge_count: How many times this agent has been nudged (0-based).
        idle_minutes: How many minutes the agent has been idle.
    
    Returns:
        A formatted nudge message string.
    """
    if nudge_count == 0:
        return (
            f"⚠️ **SUPERVISOR IDLE DETECTION** — You have been idle for "
            f"{idle_minutes:.0f} minutes with incomplete work. Your message loop "
            f"ended but the task is not finished. Resume work by:\n"
            f"1. Reviewing the gate feedback from your last response attempt\n"
            f"2. Addressing the specific failing check\n"
            f"3. Calling `response` with updated content\n\n"
            f"If you believe the work IS complete, call `response` with a "
            f"summary of all completed phases."
        )
    elif nudge_count == 1:
        return (
            f"⚠️ **SUPERVISOR: SECOND IDLE NUDGE** — Still idle after "
            f"{idle_minutes:.0f} minutes. You MUST resume. If the quality gate "
            f"is blocking you, try responding with a simplified completion "
            f"summary. Do NOT wait for user input — the user expects autonomous "
            f"completion."
        )
    else:
        return (
            f"🚨 **SUPERVISOR: FINAL NUDGE** — Idle for {idle_minutes:.0f} "
            f"minutes. This is the last nudge. Call `response` NOW with "
            f"whatever results you have. Partial delivery is better than "
            f"no delivery."
        )
