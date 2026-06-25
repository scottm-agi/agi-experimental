"""
DelegationStateBundle — Formal state propagation for subordinate delegation.

FIX-004 (G-A1): Replaces ad-hoc state copying across call_subordinate.py,
delegation_message.py, and batch/fan-out paths. All detector states are
bundled and propagated to every new subordinate through a single, formal
data structure.

Key design decisions:
- Dataclass for type safety and serialization
- from_agent() extracts ALL relevant state from parent
- apply_to_agent() applies ALL state to subordinate
- Lifetime-scoped counters are preserved across wave resets
- Build counter is capped at threshold-1 (see FIX-011)

Architecture ref: §9.1, §13.1 of gates-escapehatches-loops-architecture.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Agent type only needed for type hints

logger = logging.getLogger("agix.delegation_state_bundle")


# Counters that should NOT reset on wave transitions.
# These track lifetime state that must persist across delegations.
LIFETIME_SCOPED_COUNTERS = frozenset({
    "rework_cycle_count",
    "proof_gate",
    "quality_audit",
})


@dataclass
class DelegationStateBundle:
    """Complete state to propagate to subordinate agents.

    This replaces the ad-hoc pattern where call_subordinate.py manually
    copies individual agent.data keys. All delegation paths (single,
    batch, fan-out) should use this bundle.

    Usage:
        # In the delegation path (call_subordinate, batch, fan-out):
        bundle = DelegationStateBundle.from_agent(parent_agent)
        bundle.apply_to_agent(subordinate_agent)
    """

    # ── Build Loop State ──
    build_failure_count: int = 0
    build_error_domains: Dict[str, int] = field(default_factory=dict)
    build_fix_exhausted: bool = False

    # ── Verification Spiral State ──
    verification_read_only_count: int = 0

    # ── Gate Block Counters (lifetime-scoped only) ──
    lifetime_gate_blocks: Dict[str, int] = field(default_factory=dict)
    quality_degraded: bool = False

    # ── Rework Cycle Counter ──
    rework_cycle_count: int = 0

    # ── Tool Failure State ──
    blocked_tools: List[str] = field(default_factory=list)
    tool_failure_counts: Dict[str, int] = field(default_factory=dict)

    # ── Phase Delegation Attempts ──
    phase_delegation_attempts: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_agent(cls, agent) -> "DelegationStateBundle":
        """Extract state from parent agent into a bundle.

        Reads all relevant detector states from agent.data and packages
        them into a single bundle for propagation to subordinates.

        Args:
            agent: The parent Agent instance.

        Returns:
            A populated DelegationStateBundle.
        """
        data = getattr(agent, "data", {})
        bundle = cls()

        # ── Build loop state ──
        try:
            from python.helpers.build_loop_detector import get_propagatable_build_state
            build_state = get_propagatable_build_state(agent)
            if build_state:
                # Sum all project failure counts for the aggregate
                total = sum(
                    v for v in build_state.values()
                    if isinstance(v, int)
                )
                bundle.build_failure_count = total
                bundle.build_error_domains = dict(build_state)
        except ImportError:
            logger.debug("build_loop_detector not available for state extraction")
        bundle.build_fix_exhausted = data.get("build_fix_exhausted", False)

        # ── Gate blocks (lifetime-scoped only) ──
        block_counts = data.get("_gate_check_block_counts", {})
        bundle.lifetime_gate_blocks = {
            k: v for k, v in block_counts.items()
            if k in LIFETIME_SCOPED_COUNTERS and isinstance(v, int)
        }
        bundle.quality_degraded = data.get("_quality_degraded", False)

        # ── Rework cycle ──
        bundle.rework_cycle_count = data.get("_rework_cycle_count", 0)

        # ── Tool failures ──
        blocked = data.get("_tracker_blocked_tools", [])
        bundle.blocked_tools = list(blocked) if isinstance(blocked, list) else []
        failures = data.get("_tool_failure_counts", {})
        bundle.tool_failure_counts = dict(failures) if isinstance(failures, dict) else {}

        # ── Phase delegation attempts ──
        phase_attempts = data.get("_phase_delegation_attempts", {})
        bundle.phase_delegation_attempts = dict(phase_attempts) if isinstance(phase_attempts, dict) else {}

        return bundle

    def apply_to_agent(self, agent) -> None:
        """Apply bundled state to a subordinate agent.

        Propagates all extracted state into the subordinate's agent.data
        so that detectors and guards start with inherited context
        rather than zero.

        Args:
            agent: The subordinate Agent instance.
        """
        data = getattr(agent, "data", {})

        # ── Build loop ──
        if self.build_error_domains:
            try:
                from python.helpers.build_loop_detector import seed_build_loop_detector
                seed_build_loop_detector(agent, self.build_error_domains)
            except ImportError:
                logger.debug("build_loop_detector not available for seeding")
        if self.build_fix_exhausted:
            data["build_fix_exhausted"] = True

        # ── Gate blocks (lifetime-scoped) ──
        block_counts = data.setdefault("_gate_check_block_counts", {})
        for k, v in self.lifetime_gate_blocks.items():
            # Use max() to not downgrade existing counts
            block_counts[k] = max(block_counts.get(k, 0), v)
        if self.quality_degraded:
            data["_quality_degraded"] = True

        # ── Rework cycle ──
        if self.rework_cycle_count > 0:
            data["_rework_cycle_count"] = max(
                data.get("_rework_cycle_count", 0),
                self.rework_cycle_count,
            )

        # ── Tool failures ──
        if self.blocked_tools:
            existing = data.get("_tracker_blocked_tools", [])
            merged = list(set(existing) | set(self.blocked_tools))
            data["_tracker_blocked_tools"] = merged
        if self.tool_failure_counts:
            existing = data.get("_tool_failure_counts", {})
            for tool, count in self.tool_failure_counts.items():
                existing[tool] = max(existing.get(tool, 0), count)
            data["_tool_failure_counts"] = existing

        # ── Phase delegation attempts ──
        if self.phase_delegation_attempts:
            existing = data.get("_phase_delegation_attempts", {})
            for phase, count in self.phase_delegation_attempts.items():
                existing[phase] = max(existing.get(phase, 0), count)
            data["_phase_delegation_attempts"] = existing

        logger.info(
            f"DelegationStateBundle: Applied to subordinate — "
            f"build_failures={self.build_failure_count}, "
            f"rework={self.rework_cycle_count}, "
            f"quality_degraded={self.quality_degraded}, "
            f"blocked_tools={len(self.blocked_tools)}"
        )
