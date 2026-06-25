"""
Canonical Status Constants — Single Source of Truth for All Status Sets.

F-8 Fix (ITR-42): Eliminates the 7 separate, inconsistent _DONE_STATUSES
definitions scattered across phase_parser.py, requirements.py,
gate_quality.py, orchestrator_gate_common.py, and checks/verification.py.

There are 3 domain-specific status concepts:

1. PHASE_DONE_STATUSES — Decomposition phases (e.g., "Phase 3: Build").
   Used by: phase_parser.py, requirements.py, gate_quality.py, orchestrator_gate_common.py.

2. REQ_DONE_STATUSES — Requirement ledger entries (e.g., "REQ-001").
   Used by: gate_quality.py:79, orchestrator_gate_common.py:1101.
   Identical to PHASE_DONE_STATUSES — there's no semantic reason for divergence.

3. DELEGATION_DONE_STATUSES — Delegation result ledger entries.
   Used by: requirements.py:1820 (_reconcile_decomp_statuses).
   Different domain: these come from call_subordinate return values,
   not from the decomposition phase model.

History of the bug (from ast-grep + ripgrep triple-check):
  - phase_parser.py:119 defined {"completed", "verified", "done", "complete"}
    → EXCLUDED partially_completed, but L127 WRITES it. Infinite re-marking.
  - requirements.py:1225 defined {"completed", "skipped", "done", "complete"}
    → EXCLUDED partially_completed AND verified.
  - gate_quality.py:260 defined {"completed", "skipped", "done", "complete", "partially_completed"}
    → Most complete, but EXCLUDED verified.
  - requirements.py:1820 defined {"success", "completed", "partial"}
    → Completely different domain (delegation results, not phases).

The canonical union is the set of ALL statuses that ANY definition
used, because each had a valid reason for including its values.
"""
from __future__ import annotations

# ── Phase Completion Statuses ──
# A decomposition phase is "done" if it has any of these statuses.
# Union of all 4 prior definitions + the missing values each excluded.
PHASE_DONE_STATUSES: frozenset = frozenset({
    "completed",               # Standard completion
    "done",                    # Alias (used by some LLM outputs)
    "complete",                # Alias (used by some LLM outputs)
    "verified",                # Post-verification (phase_parser.py used this)
    "skipped",                 # Intentionally skipped phases (gate_quality.py)
    "partially_completed",     # Force-accepted delegations (phase_parser.py:127 writes this)
    "blocked",                 # RCA-460: Explicitly blocked phases (e.g., topic loop hard block)
    "deferred",                # RCA-460: Deferred phases should not block forward progress
})

# ── Requirement Completion Statuses ──
# A requirement in the ledger is "done" if it has any of these statuses.
# Superset of PHASE_DONE_STATUSES: requirements can also be "partial"
# (tried 3× but not fully met) or "failed" (structurally impossible).
# Both mean "we're done trying" for lifecycle purposes.
REQ_DONE_STATUSES: frozenset = PHASE_DONE_STATUSES | frozenset({
    "partial",                 # Tried 3× at gate level, some checks still fail
    "failed",                  # Structurally impossible (e.g., missing API creds)
})

# ── Delegation Result Statuses ──
# A delegation ledger entry (from call_subordinate) is "done" if it has
# any of these statuses. This is a DIFFERENT domain — these are return
# statuses from the delegation lifecycle, not phase/requirement statuses.
DELEGATION_DONE_STATUSES: frozenset = frozenset({
    "success",                 # Normal successful completion
    "completed",               # Alternative completion marker
    "partial",                 # Force-accepted partial completion
})


# ── Stage-Keyed Status Constants (ADR-086) ──
# The PDV pipeline has 3 stages: BDD → TDD → Code.
# Each requirement tracks independent status per stage.
# The overall `status` field is a computed MIN of all stage statuses.

VALID_STAGES: frozenset = frozenset({"bdd", "tdd", "code"})

STAGE_DONE_STATUSES: frozenset = frozenset({
    "completed",               # Standard completion
    "verified",                # Post-verification
    "done",                    # Alias (LLM outputs)
    "complete",                # Alias (LLM outputs)
    "skipped",                 # Stage not needed for this req
    "partial",                 # Tried 3× but not fully met — done trying
    "failed",                  # Structurally impossible — done trying
})

STAGE_STATUS_PRIORITY: dict = {
    "pending": 0,
    "assigned": 1,
    "delegation_returned": 2,
    "partial": 2.5,            # Tried 3× but not fully met — between assigned and completed
    "failed": 2.5,             # Structurally impossible — same priority as partial
    "completed": 3,
    "verified": 4,
    "skipped": 3,              # Equivalent to completed for overall calc
    "regressed": 0,            # Equivalent to pending (needs redo)
    "unverified": 2,           # Equivalent to delegation_returned (needs proof)
}
