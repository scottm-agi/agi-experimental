"""PhaseCompletionEvidence — Structured evidence model for phase completions.

Captures structured proof of what a delegated phase actually produced,
enabling the orchestrator gate to make category-aware pass/fail decisions
instead of relying on delegation status alone.

Used by:
- _22_multiagentdev_completion_gate.py (evidence-based gating)
- orchestrator_gate_common.py (evidence accumulation)
- call_subordinate.py (populating evidence post-delegation)

Category-aware pass/fail logic:
- PLANNING/DESIGN: delegation succeeded (status not failed/partial/escalated/cancelled)
- IMPLEMENTATION: delegation succeeded AND new_file_count > 0
- INTEGRATION: delegation succeeded AND build_passed
- VERIFICATION: delegation succeeded AND verification_matrix_score >= 0.8
- DELIVERY: delegation succeeded

Follows the dataclass patterns in agent_data_state.py (to_dict/from_dict).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger("agent.phase_completion_evidence")

# Delegation statuses that indicate failure — evidence cannot pass
# if the delegation itself was not successful.
_FAILED_STATUSES: frozenset = frozenset({
    "failed",
    "partial",
    "escalated",
    "cancelled",
    "error",
})


@dataclass
class PhaseCompletionEvidence:
    """Structured evidence of what a delegated phase produced.

    Required fields (no defaults):
        phase_seq:         Phase sequence identifier (e.g., "3.1")
        category:          Phase category (planning/design/implementation/integration/verification/delivery)
        delegation_status: Status from delegation result (completed/failed/partial/etc.)

    Evidence fields (all default to empty/zero/false):
        artifacts_found:         List of artifact paths found on disk
        artifact_sizes:          Map of artifact path → size in bytes
        new_files_created:       List of new file paths created by the phase
        new_file_count:          Count of new files created
        total_lines_added:       Total lines of code added
        tdd_tests_passed:        Whether TDD tests passed
        build_passed:            Whether the build succeeded
        routes_wired:            Number of routes wired
        fetch_route_alignment:   Ratio of fetch calls to matched routes (0.0–1.0)
        verification_matrix_score: Overall verification matrix score (0.0–1.0)
        e2e_pass_rate:           End-to-end test pass rate (0.0–1.0)
        contract_assertions_passed: Whether contract assertions passed
        timestamp:               UTC ISO timestamp (auto-set if not provided)
        chat_id:                 Chat/conversation ID for traceability
        delegation_hash:         Hash of the delegation for correlation
    """

    # ── Required fields ──
    phase_seq: str
    category: str
    delegation_status: str

    # ── Evidence: artifacts ──
    artifacts_found: List[str] = field(default_factory=list)
    artifact_sizes: Dict[str, int] = field(default_factory=dict)

    # ── Evidence: file creation ──
    new_files_created: List[str] = field(default_factory=list)
    new_file_count: int = 0
    total_lines_added: int = 0

    # ── Evidence: quality signals ──
    tdd_tests_passed: bool = False
    build_passed: bool = False
    routes_wired: int = 0
    fetch_route_alignment: float = 0.0
    verification_matrix_score: float = 0.0
    e2e_pass_rate: float = 0.0
    contract_assertions_passed: bool = False

    # ── Metadata ──
    timestamp: str = ""
    chat_id: str = ""
    delegation_hash: str = ""

    def __post_init__(self) -> None:
        """Auto-set timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    # ── Serialization ──

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict.

        Follows the same pattern as agent_data_state._to_dict but
        inlined here to avoid circular dependency with that module.
        """
        result: Dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if isinstance(val, set):
                result[f.name] = sorted(val)
            else:
                result[f.name] = val
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PhaseCompletionEvidence:
        """Create instance from dict, using defaults for missing keys.

        Extra keys not in the dataclass are silently ignored.
        Missing keys get the field's default value.
        """
        valid_field_names = {f.name for f in fields(cls)}
        kwargs: Dict[str, Any] = {}
        for name in valid_field_names:
            if name in d:
                kwargs[name] = d[name]
        return cls(**kwargs)

    # ── Pass/Fail Logic ──

    @property
    def passed(self) -> bool:
        """Category-aware pass/fail determination.

        Returns True only if:
        1. Delegation itself succeeded (not in _FAILED_STATUSES), AND
        2. Category-specific evidence thresholds are met.
        """
        # Gate 1: delegation must have succeeded
        if self.delegation_status in _FAILED_STATUSES:
            return False

        cat = self.category.lower()

        if cat == "implementation":
            return self.new_file_count > 0
        elif cat == "integration":
            return self.build_passed
        elif cat == "verification":
            return self.verification_matrix_score >= 0.8
        else:
            # planning, design, delivery, and any unknown category:
            # delegation success is sufficient
            return True

    # ── Summary ──

    @property
    def summary(self) -> str:
        """Human-readable 1-line summary of the evidence."""
        status = "PASS" if self.passed else "FAIL"
        parts = [
            f"Phase {self.phase_seq} [{self.category}]: {status}",
            f"delegation={self.delegation_status}",
        ]
        if self.new_file_count > 0:
            parts.append(f"files={self.new_file_count}")
        if self.total_lines_added > 0:
            parts.append(f"lines={self.total_lines_added}")
        if self.artifacts_found:
            parts.append(f"artifacts={len(self.artifacts_found)}")
        if self.build_passed:
            parts.append("build=ok")
        if self.tdd_tests_passed:
            parts.append("tdd=ok")
        if self.verification_matrix_score > 0:
            parts.append(f"vmatrix={self.verification_matrix_score:.1%}")
        return " | ".join(parts)
