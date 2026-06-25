"""
Signal Quality Scorer — P1-3 Supervisor Denoising Phase 1.

Single-pass quality scoring layer that replaces 6 overlapping noise suppressors.
Each signal gets a quality score in [0.0, 1.0] computed from 4 factors:

1. Source Reliability: Weight assigned to the detector that produced the signal.
2. Severity Multiplier: How severe the signal is (critical > high > warning > info).
3. Recency Decay: Signals older than DECAY_WINDOW_SEC are discounted.
4. Nudge Effectiveness: If the last nudge for this detector was ineffective, suppress.
5. Corroboration Boost: Multiple independent detectors agreeing boosts the score.

The quality score determines whether a signal fires (>= FIRE_THRESHOLD) or
is suppressed. Critical severity signals always fire regardless of score.

Exports:
- ScoredSignal: Dataclass with quality, should_fire, and suppression_reason.
- score_signal: Main scoring function.
- count_corroborating_signals: Count independent detectors in agent_data.
- find_last_nudge_for_detector: Find most recent nudge for a detector.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from python.helpers.nudge_tracker import NudgeRecord

logger = logging.getLogger("agix.signal_quality")

# ─── CONFIGURATION CONSTANTS ────────────────────────────────────────
# Quality score threshold: signals below this are suppressed.
FIRE_THRESHOLD: float = 0.40

# Recency decay window in seconds (5 minutes).
DECAY_WINDOW_SEC: float = 300.0

# Maximum corroboration boost.
MAX_CORROBORATION_BOOST: float = 0.20

# ─── SOURCE RELIABILITY WEIGHTS ─────────────────────────────────────
# How trustworthy each signal source is.
# Higher = more reliable = higher base quality.
SOURCE_WEIGHTS: Dict[str, float] = {
    "structural_guards": 0.9,       # Deterministic detectors — very reliable
    "same_message_repeat": 0.7,     # MD5 hash — reliable but can be noisy
    "circuit_breaker": 0.8,         # Threshold-based — reliable
    "tool_failure_tracker": 0.5,    # Tool errors can be transient
    "error_supervisor_trigger": 0.6, # Error cascade — medium reliability
    "delegation_stall": 0.7,        # Progress tracking — reliable
    "gate_circuit_breaker_warning": 0.8,  # Gate system — reliable
    "critical_check_warning": 0.8,  # Gate system — reliable
    "advisory_quality_gap": 0.5,    # Advisory — can be noisy
    "error_cascade": 0.7,           # Threshold-based — medium-high
    "tool_failure_loop": 0.7,       # Loop detection — medium-high
    "rapid_delegation": 0.6,        # Can be normal behavior
    "monologue_loop": 0.6,          # Can be normal for thinking
    "tool_call_repetition": 0.7,    # Pattern-based — reliable
    "progress_stagnation": 0.6,     # Can be slow but progressing
    "oscillation_cycle": 0.8,       # Clear A→B→A pattern — reliable
    "low_write_ratio": 0.4,         # Research may be read-heavy
}

# Default weight for unknown detectors.
DEFAULT_SOURCE_WEIGHT: float = 0.5

# ─── SEVERITY MULTIPLIERS ───────────────────────────────────────────
SEVERITY_MULTIPLIERS: Dict[str, float] = {
    "critical": 1.5,
    "high": 1.2,
    "warning": 1.0,
    "medium": 1.0,
    "info": 0.5,
    "low": 0.5,
}

DEFAULT_SEVERITY_MULTIPLIER: float = 1.0

# Penalty multiplier when the last nudge for this detector was ineffective.
INEFFECTIVE_NUDGE_PENALTY: float = 0.4


@dataclass
class ScoredSignal:
    """A signal with its computed quality score and firing decision."""
    original: Dict[str, Any]
    quality: float
    should_fire: bool
    suppression_reason: Optional[str] = None


def score_signal(
    signal: Dict[str, Any],
    agent_data: Dict[str, Any],
    nudge_history: List[NudgeRecord],
) -> ScoredSignal:
    """Score a signal's quality and decide if it should fire.

    Computes quality = source_weight * severity_multiplier * recency_factor
                       * nudge_penalty * (1 + corroboration_boost)
    Then clamps to [0.0, 1.0].

    Critical severity signals always fire regardless of computed score.

    Args:
        signal: The raw signal dict with 'detector', 'severity', optional 'ts'.
        agent_data: The agent's data dict (for corroboration check).
        nudge_history: List of past NudgeRecord entries.

    Returns:
        ScoredSignal with quality score, fire decision, and optional reason.
    """
    detector = signal.get("detector", "unknown")
    severity = signal.get("severity", "warning")
    ts = signal.get("ts", time.time())

    # 1. Source reliability
    source_weight = SOURCE_WEIGHTS.get(detector, DEFAULT_SOURCE_WEIGHT)

    # 2. Severity multiplier
    severity_mult = SEVERITY_MULTIPLIERS.get(severity, DEFAULT_SEVERITY_MULTIPLIER)

    # 3. Recency decay
    recency = _compute_recency_factor(ts)

    # 4. Nudge effectiveness penalty
    nudge_factor = 1.0
    last_nudge = find_last_nudge_for_detector(detector, nudge_history)
    if last_nudge is not None and last_nudge.was_effective is False:
        nudge_factor = INEFFECTIVE_NUDGE_PENALTY

    # 5. Corroboration boost
    corroboration_count = count_corroborating_signals(signal, agent_data)
    corroboration_boost = min(
        corroboration_count * 0.1,
        MAX_CORROBORATION_BOOST,
    )

    # Compute final quality
    raw_quality = (
        source_weight
        * severity_mult
        * recency
        * nudge_factor
        * (1.0 + corroboration_boost)
    )
    quality = max(0.0, min(1.0, raw_quality))

    # Determine firing decision
    is_critical = severity == "critical"
    should_fire = is_critical or quality >= FIRE_THRESHOLD

    suppression_reason = None
    if not should_fire:
        suppression_reason = (
            f"quality={quality:.2f} < threshold={FIRE_THRESHOLD:.2f}"
        )

    return ScoredSignal(
        original=signal,
        quality=quality,
        should_fire=should_fire,
        suppression_reason=suppression_reason,
    )


def count_corroborating_signals(
    signal: Dict[str, Any],
    agent_data: Dict[str, Any],
) -> int:
    """Count distinct detectors in agent_data that corroborate this signal.

    Only counts OTHER detectors (same detector is not corroboration).
    Counts unique detector names only (no duplicates).

    Args:
        signal: The signal being evaluated.
        agent_data: Agent's data dict containing _l2_escalation_signals.

    Returns:
        Number of distinct corroborating detectors.
    """
    detector = signal.get("detector", "")
    active_signals = agent_data.get("_l2_escalation_signals", [])

    unique_detectors = set()
    for s in active_signals:
        if isinstance(s, dict):
            d = s.get("detector", "")
            if d and d != detector:
                unique_detectors.add(d)

    return len(unique_detectors)


def find_last_nudge_for_detector(
    detector: str,
    nudge_history: List[NudgeRecord],
) -> Optional[NudgeRecord]:
    """Find the most recent nudge record for a specific detector.

    Args:
        detector: The detector name to search for.
        nudge_history: List of NudgeRecord entries to search.

    Returns:
        The most recent NudgeRecord for the detector, or None.
    """
    matches = [nr for nr in nudge_history if nr.detector == detector]
    if not matches:
        return None
    return max(matches, key=lambda nr: nr.timestamp)


def _compute_recency_factor(ts: float) -> float:
    """Compute recency decay factor for a signal timestamp.

    Returns 1.0 for fresh signals, decaying linearly to 0.3 at DECAY_WINDOW_SEC,
    and capped at 0.3 for anything older.

    Args:
        ts: Unix timestamp of the signal.

    Returns:
        Decay factor in [0.3, 1.0].
    """
    now = time.time()
    age = max(0.0, now - ts)  # Negative age (future) → 0 age → factor 1.0

    if age <= 0:
        return 1.0
    if age >= DECAY_WINDOW_SEC:
        return 0.3

    # Linear decay from 1.0 to 0.3 over DECAY_WINDOW_SEC
    return 1.0 - (0.7 * age / DECAY_WINDOW_SEC)
