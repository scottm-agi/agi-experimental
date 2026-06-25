"""
DelegationLoopDetector — Circuit breaker for same-delegation death spirals.
==========================================================================

Tracks delegation messages per agent. When an agent delegates the same task
N times (threshold), returns a soft warning suggesting alternatives. At 2N
(hard_limit), returns a hard block preventing further identical delegations.

Root cause (ADR-019, Iteration 151 RCA-1):
    Parent orchestrator delegated identical Phase 5 "verify build" task to
    4+ subordinates (Ask, E2e, Researcher, Frontend) sequentially. Each
    completed but parent re-delegated. 50+ minutes wasted. No detector existed.

Usage:
    detector = DelegationLoopDetector(threshold=3, hard_limit=6)
    warning = detector.check(agent_id, task_message)
    if warning:
        # Inject warning into agent context
        await agent.hist_add_warning(warning)
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional

import time

from python.helpers.task_hash import compute_task_hash

logger = logging.getLogger("agix.delegation_loop_detector")


def get_detector_key(agent) -> str:
    """Construct a context-scoped detector key for an agent.

    U-15 Fix: Prevents cross-context poisoning by including the context ID
    in the key. Two agents named 'Multiagentdev' in different conversations
    will have completely independent failure namespaces.

    Args:
        agent: Agent object with optional .context.id and .agent_name.

    Returns:
        String key like 'ctx-abc:Multiagentdev' or bare 'Multiagentdev' if no context.
    """
    context = getattr(agent, "context", None)
    ctx_id = getattr(context, "id", "") if context else ""
    agent_name = getattr(agent, "agent_name", "") or str(id(agent))
    return f"{ctx_id}:{agent_name}" if ctx_id else agent_name


class DelegationLoopDetector:
    """Tracks identical delegation messages per agent and triggers diagnostics.
    
    Two tracking dimensions:
    1. ALL delegations (existing) — threshold/hard_limit for delegation loops
    2. FAILED delegations (new) — failure_threshold for supervisor redirect
    
    After failure_threshold (default=2) FAILED attempts at the same task hash,
    emits a redirect diagnostic for the supervisor to consume.
    """

    def __init__(
        self,
        threshold: int = 3,
        hard_limit: int = 6,
        failure_threshold: int = 3,
    ):
        """
        Args:
            threshold: Number of identical delegations before soft warning.
            hard_limit: Number of identical delegations before hard block.
            failure_threshold: Number of FAILED delegations before redirect.
        """
        self.threshold = threshold
        self.hard_limit = hard_limit
        self.failure_threshold = failure_threshold
        # agent_id → {message_hash: count}
        self._counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # ── Failure tracking ──
        # agent_id → {message_hash: failure_count}
        self._failure_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # agent_id → {message_hash: [{"errors": [...], "attempt": N}, ...]}
        self._failure_details: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        # U-15: TTL timestamps for decay
        self._timestamps: dict[str, dict[str, float]] = defaultdict(dict)
        self.ttl_seconds: float = 1800.0  # 30 minutes default
        # ── Signal 7: Requirement-ID overlap tracking ──
        # agent_id → [{"req_set": frozenset, "profile": str, "timestamp": float}, ...]
        self._req_overlap_history: dict[str, list] = defaultdict(list)
        # agent_id → monotonic overlap hit counter
        self._req_overlap_counts: dict[str, int] = defaultdict(int)

    # ═══════════════════════════════════════════════════════════════════════
    # Signal 7: Requirement-ID Overlap Detection
    # ═══════════════════════════════════════════════════════════════════════

    def check_req_overlap(
        self,
        agent_id: str,
        requirement_ids: list,
        profile: str = "",
        *,
        overlap_threshold: float = 0.5,
    ) -> Optional[str]:
        """Signal 7: Detect delegations targeting overlapping requirement IDs.

        Two delegations with >50% Jaccard overlap on their requirement_ids
        are considered "same work" even if their message text is completely
        different. Catches LLM paraphrasing attacks that defeat both
        hash-based (Signal 1) and topic-based (Signal 2) detection.

        Args:
            agent_id: Context-scoped agent key.
            requirement_ids: The requirement IDs in the current delegation.
            profile: Agent profile being delegated to. Overlap is only
                checked against same-profile delegations (cross-profile
                fan-out is valid).
            overlap_threshold: Jaccard similarity threshold (default 0.5).

        Returns:
            None if no concern.
            Soft diagnostic string if threshold exceeded.
            Hard diagnostic string if hard_limit exceeded.
        """
        if not requirement_ids:
            return None

        current_set = frozenset(requirement_ids)
        self._expire_stale()

        history = self._req_overlap_history[agent_id]
        overlap_count = 0

        for entry in history:
            stored_set = entry["req_set"]
            stored_profile = entry.get("profile", "")

            # Only compare same-profile delegations
            if profile and stored_profile and profile != stored_profile:
                continue

            # Jaccard similarity: |A ∩ B| / |A ∪ B|
            intersection = current_set & stored_set
            union = current_set | stored_set
            if not union:
                continue
            jaccard = len(intersection) / len(union)

            if jaccard >= overlap_threshold:
                overlap_count += 1

        # Record the current delegation
        history.append({
            "req_set": current_set,
            "profile": profile,
            "timestamp": time.time(),
        })

        # Update monotonic counter
        if overlap_count > 0:
            self._req_overlap_counts[agent_id] += 1
        total_hits = self._req_overlap_counts[agent_id]

        if total_hits >= self.hard_limit:
            logger.error(
                f"DelegationLoopDetector: REQ OVERLAP HARD BLOCK — {agent_id} "
                f"delegated overlapping req_ids {total_hits} times"
            )
            return self._req_overlap_hard_diagnostic(total_hits, requirement_ids)
        elif total_hits >= self.threshold:
            logger.warning(
                f"DelegationLoopDetector: REQ OVERLAP WARNING — {agent_id} "
                f"delegated overlapping req_ids {total_hits} times"
            )
            return self._req_overlap_soft_diagnostic(total_hits, requirement_ids)

        return None

    def _req_overlap_soft_diagnostic(self, count: int, req_ids: list) -> str:
        req_preview = ", ".join(str(r) for r in req_ids[:5])
        if len(req_ids) > 5:
            req_preview += f" (+{len(req_ids) - 5} more)"
        return (
            f"## ⚠️ REQUIREMENT OVERLAP LOOP ({count} overlapping delegations)\n"
            f"\n"
            f"You have delegated overlapping requirement IDs {count} times:\n"
            f"  Current: [{req_preview}]\n"
            f"\n"
            f"This means you are re-delegating the SAME work with different wording.\n"
            f"\n"
            f"### Required: Choose ONE of these approaches\n"
            f"\n"
            f"1. **Check the status** of the previous delegation for these requirements\n"
            f"2. **Remove overlapping req_ids** and only delegate the NEW ones\n"
            f"3. **Accept the previous result** and move to the next phase\n"
        )

    def _req_overlap_hard_diagnostic(self, count: int, req_ids: list) -> str:
        req_preview = ", ".join(str(r) for r in req_ids[:5])
        if len(req_ids) > 5:
            req_preview += f" (+{len(req_ids) - 5} more)"
        return (
            f"## 🛑 REQUIREMENT OVERLAP HARD BLOCK ({count} overlapping delegations)\n"
            f"\n"
            f"Delegations with overlapping requirement IDs attempted {count} times.\n"
            f"  Current: [{req_preview}]\n"
            f"\n"
            f"Further delegations with these requirement IDs are BLOCKED.\n"
            f"You MUST report partial completion or work on different requirements.\n"
        )

    def check(self, agent_id: str, task_message: str, *, phase_id: Optional[str] = None, agent_data: Optional[dict] = None) -> Optional[str]:
        """Record a delegation and return diagnostic if loop detected.

        Args:
            agent_id: Unique identifier for the delegating agent.
            task_message: The task description being delegated.
            phase_id: Optional decomposition phase ID (e.g. 'phase-3.1').
                When provided, delegations are scoped per-phase so that
                similar messages in different phases are tracked independently.
                (F-6: Phase-Awareness fix)

        Returns:
            None if delegation is fine, warning string if loop detected.
        """
        h = self._hash_message(task_message, phase_id=phase_id)
        self._expire_stale()  # U-15: Prune stale entries before checking
        self._counts[agent_id][h] += 1
        self._timestamps[agent_id][h] = time.time()
        count = self._counts[agent_id][h]

        if count >= self.hard_limit:
            # Escape hatch: use centralized gate_check() when agent_data is
            # available, falling back to inline counter for backward compat.
            from python.helpers.universal_gate_budget import gate_check
            _ESCAPE_AFTER_HARD = 3
            if agent_data is not None and gate_check(
                agent_data, "delegation_loop_hard_block", threshold=_ESCAPE_AFTER_HARD
            ):
                logger.warning(
                    f"DelegationLoopDetector: Escape hatch (gate_check) — {agent_id} blocked "
                    f"{count}x past hard_limit={self.hard_limit}, "
                    f"allowing through (ADVISORY)"
                )
                return None  # Allow through
            elif agent_data is None:
                # Backward compat: inline counter when no agent_data provided
                blocks_past_hard = count - self.hard_limit
                if blocks_past_hard >= _ESCAPE_AFTER_HARD:
                    logger.warning(
                        f"DelegationLoopDetector: Escape hatch — {agent_id} blocked "
                        f"{count}x ({blocks_past_hard}x past hard_limit={self.hard_limit}), "
                        f"allowing through (ADVISORY)"
                    )
                    return None  # Allow through

            logger.error(
                f"DelegationLoopDetector: HARD BLOCK — {agent_id} delegated "
                f"same task {count} times"
            )
            return self._hard_block_diagnostic(count)
        elif count >= self.threshold:
            logger.warning(
                f"DelegationLoopDetector: WARNING — {agent_id} delegated "
                f"same task {count} times (threshold={self.threshold})"
            )
            return self._soft_warning_diagnostic(count)

        return None


    def record_attempt(self, agent_id: str, task_message: str, *, phase_id: Optional[str] = None) -> tuple[str, int]:
        """Record a delegation attempt and return (task_hash, sequence_id).

        Called BEFORE delegation starts to assign tracking metadata.
        The returned hash + sequence_id should be injected into the
        delegation message so subordinates can reference them.

        Args:
            agent_id: Unique identifier for the delegating agent.
            task_message: The task description being delegated.
            phase_id: Optional decomposition phase ID (F-6).

        Returns:
            (task_hash, sequence_id) — e.g. ("8a795dc6fcdb", 2)
        """
        h = self._hash_message(task_message, phase_id=phase_id)
        short_hash = self.get_task_hash(task_message)
        # Increment attempt counter (separate from delegation loop counter)
        if "_attempt_counts" not in self.__dict__:
            self._attempt_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._attempt_counts[agent_id][h] += 1
        seq = self._attempt_counts[agent_id][h]

        logger.info(
            f"DelegationLoopDetector: ATTEMPT #{seq} for {agent_id} "
            f"task_hash={short_hash}"
        )
        return short_hash, seq

    def get_attempt_count(self, agent_id: str, task_message: str, *, phase_id: Optional[str] = None) -> int:
        """Get total attempt count (successes + failures) for a task."""
        h = self._hash_message(task_message, phase_id=phase_id)
        if "_attempt_counts" not in self.__dict__:
            return 0
        return self._attempt_counts.get(agent_id, {}).get(h, 0)

    # ── Failure tracking ──────────────────────────────────────────────

    def record_failure(
        self,
        agent_id: str,
        task_message: str,
        errors: list[str] | None = None,
        *,
        phase_id: Optional[str] = None,
    ) -> Optional[str]:
        """Record a FAILED delegation attempt for a task.

        Args:
            agent_id: Unique identifier for the delegating agent.
            task_message: The task description that was delegated.
            errors: List of error strings from this attempt.
            phase_id: Optional decomposition phase ID (F-6).

        Returns:
            None if below failure threshold.
            Redirect diagnostic string if threshold reached (>= failure_threshold).
        """
        h = self._hash_message(task_message, phase_id=phase_id)
        self._expire_stale()  # U-15: Prune stale entries before recording
        self._failure_counts[agent_id][h] += 1
        self._timestamps[agent_id][h] = time.time()
        count = self._failure_counts[agent_id][h]
        short_hash = self.get_task_hash(task_message)

        # Record the error details for this attempt
        self._failure_details[agent_id][h].append({
            "errors": list(errors) if errors else [],
            "attempt": count,
        })

        logger.info(
            f"DelegationLoopDetector: FAILURE #{count} for {agent_id} "
            f"task_hash={short_hash}"
        )

        if count >= self.failure_threshold:
            logger.warning(
                f"DelegationLoopDetector: FAILURE THRESHOLD REACHED — "
                f"{agent_id} failed same task {count} times "
                f"(threshold={self.failure_threshold}, hash={short_hash})"
            )
            return self._failure_redirect_diagnostic(
                count, short_hash, self._failure_details[agent_id][h]
            )

        return None

    def get_failure_count(self, agent_id: str, task_message: str, *, phase_id: Optional[str] = None) -> int:
        """Get the failure count for a specific task message."""
        h = self._hash_message(task_message, phase_id=phase_id)
        return self._failure_counts.get(agent_id, {}).get(h, 0)

    def get_failure_details(
        self, agent_id: str, task_message: str, *, phase_id: Optional[str] = None
    ) -> list[dict]:
        """Get error details for all failed attempts at a task.

        Returns:
            List of dicts, each with 'errors' (list[str]) and 'attempt' (int).
        """
        h = self._hash_message(task_message, phase_id=phase_id)
        return list(self._failure_details.get(agent_id, {}).get(h, []))

    def get_task_hash(self, task_message: str) -> str:
        """Get the canonical 12-char hash for a task message.
        
        Uses the shared compute_task_hash utility for consistent
        hashing across all task tracking systems.
        """
        return compute_task_hash(task_message, length=12)

    # ── Existing methods ──────────────────────────────────────────────

    def reset(self, agent_id: str) -> None:
        """Clear all tracking (delegation counts + failure + req overlap) for an agent."""
        if agent_id in self._counts:
            del self._counts[agent_id]
        if agent_id in self._failure_counts:
            del self._failure_counts[agent_id]
        if agent_id in self._failure_details:
            del self._failure_details[agent_id]
        if agent_id in self._timestamps:
            del self._timestamps[agent_id]
        # Signal 7: clear req overlap tracking
        if agent_id in self._req_overlap_history:
            del self._req_overlap_history[agent_id]
        if agent_id in self._req_overlap_counts:
            del self._req_overlap_counts[agent_id]

    def reset_context(self, context_id: str) -> None:
        """Clear all entries for a specific context_id prefix.

        U-15 Fix: Called when a conversation ends to prevent stale state
        from poisoning future delegations in new contexts.

        Args:
            context_id: The context ID prefix to clear (e.g. 'ctx-abc').
        """
        prefix = f"{context_id}:"
        for store in (self._counts, self._failure_counts, self._failure_details, self._timestamps):
            for key in list(store.keys()):
                if key.startswith(prefix):
                    del store[key]
        # Signal 7: clear req overlap tracking for context
        for store in (self._req_overlap_history, self._req_overlap_counts):
            for key in list(store.keys()) if isinstance(store, dict) else []:
                if key.startswith(prefix):
                    del store[key]
        # Also clear _attempt_counts if it exists
        if hasattr(self, '_attempt_counts'):
            for key in list(self._attempt_counts.keys()):
                if key.startswith(prefix):
                    del self._attempt_counts[key]

    def _expire_stale(self) -> None:
        """Prune entries older than TTL.

        U-15 Fix: Prevents indefinite accumulation of failure state.
        Called automatically on check() and record_failure().
        """
        cutoff = time.time() - self.ttl_seconds
        for agent_id in list(self._timestamps):
            for h in list(self._timestamps[agent_id]):
                if self._timestamps[agent_id][h] < cutoff:
                    self._counts[agent_id].pop(h, None)
                    self._failure_counts[agent_id].pop(h, None)
                    self._failure_details[agent_id].pop(h, None)
                    del self._timestamps[agent_id][h]
            # Clean up empty agent entries
            if not self._timestamps[agent_id]:
                del self._timestamps[agent_id]
        # Signal 7: TTL prune req overlap history
        for agent_id in list(self._req_overlap_history):
            self._req_overlap_history[agent_id] = [
                entry for entry in self._req_overlap_history[agent_id]
                if entry.get("timestamp", 0) >= cutoff
            ]
            if not self._req_overlap_history[agent_id]:
                del self._req_overlap_history[agent_id]

    def get_delegation_count(self, agent_id: str, task_message: str, *, phase_id: Optional[str] = None) -> int:
        """Get current count for a specific delegation message."""
        h = self._hash_message(task_message, phase_id=phase_id)
        return self._counts.get(agent_id, {}).get(h, 0)

    def _hash_message(self, message: str, *, phase_id: Optional[str] = None) -> str:
        """Normalize and hash the delegation message.
        
        Uses full 32-char MD5 for internal dedup (more collision-resistant
        than the 12-char public hash). Same normalization pipeline.

        F-6 Phase-Awareness: When phase_id is provided, it is prepended
        to the message before hashing so that identical messages from
        different decomposition phases produce different hashes.
        """
        if phase_id:
            message = f"[phase:{phase_id}] {message}"
        return compute_task_hash(message, length=32)

    # ── Diagnostics ──────────────────────────────────────────────────

    def _failure_redirect_diagnostic(
        self,
        count: int,
        short_hash: str,
        details: list[dict],
    ) -> str:
        """Build a structured redirect diagnostic for the supervisor."""
        error_summary = []
        for detail in details:
            attempt_num = detail["attempt"]
            errs = detail.get("errors", [])
            if errs:
                error_summary.append(
                    f"  - Attempt {attempt_num}: {'; '.join(errs[:5])}"
                )

        errors_text = "\n".join(error_summary) if error_summary else "  - (no error details captured)"

        return (
            f"## 🔄 TASK FAILED {count} TIMES — SUPERVISOR REDIRECT REQUIRED\n"
            f"\n"
            f"**Task hash**: `{short_hash}`\n"
            f"This exact task (same MD5 hash) has failed {count} times.\n"
            f"The supervisor must perform deep-dive RCA and provide a NEW\n"
            f"solution approach before this task is re-attempted.\n"
            f"\n"
            f"### Error Summary (all attempts):\n"
            f"{errors_text}\n"
            f"\n"
            f"### Required Action:\n"
            f"- The supervisor will analyze these failures and compose a new approach\n"
            f"- DO NOT retry the same task without a fundamentally different strategy\n"
            f"- Wait for supervisor redirect with new instructions\n"
        )

    def _soft_warning_diagnostic(self, count: int) -> str:
        return (
            f"## ⚠️ DELEGATION LOOP DETECTED ({count} identical delegations)\n"
            f"\n"
            f"You have delegated this same task {count} times to different "
            f"subordinates. Each returned a similar result. This is not making "
            f"progress.\n"
            f"\n"
            f"### Required: Choose ONE of these approaches\n"
            f"\n"
            f"1. **Accept the current result** and move to the next phase\n"
            f"2. **Change your approach fundamentally** — write a different "
            f"task description with different goals\n"
            f"3. **Do the work yourself** instead of delegating\n"
            f"\n"
            f"Do NOT delegate the same task again with the same wording.\n"
        )

    def _hard_block_diagnostic(self, count: int) -> str:
        return (
            f"## 🛑 DELEGATION HARD_BLOCK ({count} identical delegations)\n"
            f"\n"
            f"You have delegated this exact task {count} times. Further "
            f"identical delegations are BLOCKED.\n"
            f"\n"
            f"You MUST either:\n"
            f"- Accept the current state and move on\n"
            f"- Write a fundamentally different task description\n"
            f"- Perform the work directly without delegation\n"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Loop Recovery Strategy (F-5 — ITR-49, enhanced F-7 — RCA-470)
# ═══════════════════════════════════════════════════════════════════════════


def analyze_failure_pattern(errors: list) -> dict:
    """Categorize a list of error strings into a structured failure pattern.

    F-7 (RCA-470): Before re-dispatching a failed delegation, extract the
    root cause category so the recovery strategy can include actionable
    diagnostic instructions.

    Args:
        errors: List of error strings from failed attempts.

    Returns:
        Dict with keys:
            - category: str — one of the error categories below
            - root_cause: str — human-readable summary of the root cause
            - suggested_fix: str — specific action to take
    """
    if not errors:
        return {
            "category": "unknown",
            "root_cause": "No error details captured",
            "suggested_fix": "Read the subordinate's full response for error context",
        }

    combined = " ".join(errors).lower()

    # ── Category detection (ordered by specificity) ──

    # Missing dependency / module not found
    if any(kw in combined for kw in [
        "module not found", "cannot resolve", "can't resolve",
        "cannot find module", "no such module", "missing dependency",
    ]):
        # Extract module name from error
        module_name = _extract_module_name(combined)
        return {
            "category": "missing_dependency",
            "root_cause": f"Missing dependency: {module_name}" if module_name else "Missing dependency",
            "suggested_fix": (
                f"Add '{module_name}' to package.json dependencies and run npm install"
                if module_name else
                "Check import statements against package.json dependencies"
            ),
        }

    # Missing environment variable
    if any(kw in combined for kw in [
        "environment variable", "env var", "missing.*env",
        "database_url", "api_key", ".env",
    ]):
        return {
            "category": "missing_env",
            "root_cause": "Missing or misconfigured environment variables",
            "suggested_fix": "Check .env.local exists in the correct directory and contains required keys",
        }

    # TypeScript / type errors
    if any(kw in combined for kw in [
        "ts2", "type error", "is not assignable", "type '",
        "expected", "but got", "typescript",
    ]):
        return {
            "category": "type_error",
            "root_cause": "TypeScript type mismatch",
            "suggested_fix": "Read the type definitions and fix type annotations",
        }

    # Import / export errors
    if any(kw in combined for kw in [
        "import", "export", "does not export",
        "is not exported", "named export",
    ]):
        return {
            "category": "import_error",
            "root_cause": "Import/export mismatch between modules",
            "suggested_fix": "Verify the exporting module actually exports the symbol being imported",
        }

    # Build errors (generic)
    if any(kw in combined for kw in [
        "build failed", "compilation error", "syntax error",
        "parse error", "unexpected token",
    ]):
        return {
            "category": "build_error",
            "root_cause": "Build/compilation failure",
            "suggested_fix": "Read the build output and fix syntax/compilation errors",
        }

    # Generic fallback
    return {
        "category": "unknown",
        "root_cause": errors[0][:200] if errors else "Unknown error",
        "suggested_fix": "Read the error output carefully and search for solutions",
    }


def _extract_module_name(error_text: str) -> str:
    """Extract module/package name from a 'module not found' error.

    Handles patterns like:
        - "Module not found: Can't resolve 'glob'"
        - "Cannot find module '@/components/Header'"
        - "Cannot resolve 'next/image'"
    """
    import re
    # Match quoted module names
    match = re.search(r"(?:resolve|find module|find package)\s+['\"]([^'\"]+)['\"]", error_text)
    if match:
        return match.group(1)
    # Match 'module not found: X'
    match = re.search(r"module not found:?\s*(?:can'?t resolve\s+)?['\"]?([^\s'\"]+)", error_text)
    if match:
        return match.group(1)
    return ""


def generate_loop_recovery_strategy(
    count: int,
    message: str,
    profile: str,
    agent_data: dict,
    threshold: int = 3,
    hard_limit: int = 5,
) -> Optional[str]:
    """Generate structured recovery strategy when delegation loop fires.

    F-5 (ITR-49) + F-7 (RCA-470): Progressive 4-level escalation strategy.

    Escalation levels:
        count < threshold: No strategy needed (no loop detected yet).
        count == threshold: SPLIT — break into smaller sub-tasks.
        count == threshold+1: CHANGE APPROACH — different profile/decomposition.
        count == threshold+2: 5-WHY SELF-DIAGNOSIS — agent gets search/read_file
            tools + error context to diagnose its own failure. After diagnosing,
            search for solutions using available search tools (context7, web search).
        count == threshold+3: BACKLOG + ADVANCE — park this phase, move to next.
        count >= hard_limit: None — hard block handles this.

    Args:
        count: Current delegation count for this task.
        message: The delegation message being repeated.
        profile: The agent profile (e.g. "code", "frontend").
        agent_data: The agent's mutable data dict (for context).
        threshold: Soft warning threshold (default 3).
        hard_limit: Hard block threshold (default 5).

    Returns:
        Structured guidance string, or None if no strategy needed.
    """
    # Below threshold — no loop detected yet
    if count < threshold:
        return None

    # At or above hard limit — hard block handles this
    if count >= hard_limit:
        return None

    # Extract requirement context if available
    req_context = ""
    ledger = agent_data.get("_requirements_ledger", {})
    delegations = ledger.get("delegations", [])
    if delegations:
        last_delegation = delegations[-1] if delegations else {}
        req_ids = last_delegation.get("requirement_ids", [])
        if req_ids:
            req_context = (
                f"\n\nThe last delegation included {len(req_ids)} requirements. "
                f"Consider splitting these into smaller batches of 3-4 requirements each."
            )

    # Extract failure details if available (F-7 RCA-470)
    failure_context = ""
    failure_details = agent_data.get("_delegation_failure_details", [])
    if failure_details:
        all_errors = []
        for detail in failure_details:
            all_errors.extend(detail.get("errors", []))
        if all_errors:
            analysis = analyze_failure_pattern(all_errors)
            failure_context = (
                f"\n\n**Error Analysis** (from {len(failure_details)} failed attempts):\n"
                f"- Category: `{analysis['category']}`\n"
                f"- Root cause: {analysis['root_cause']}\n"
                f"- Suggested fix: {analysis['suggested_fix']}\n"
            )

    # ── Level 1: SPLIT (count == threshold) ──
    if count == threshold:
        return (
            f"## 🔄 LOOP RECOVERY: SPLIT THIS TASK (attempt #{count})\n"
            f"\n"
            f"You have delegated this same task {count} times without success. "
            f"**Repeating the same delegation will not produce a different result.**\n"
            f"\n"
            f"### Required Strategy: SPLIT\n"
            f"\n"
            f"1. **Break this task into 2-3 smaller sub-tasks** — each focused on "
            f"a single page, feature, or component\n"
            f"2. **Delegate each sub-task separately** with specific, narrow instructions\n"
            f"3. **Verify each sub-task completes** before delegating the next\n"
            f"\n"
            f"Profile: `{profile}` | Task preview: `{message[:80]}...`"
            f"{req_context}{failure_context}\n"
        )

    # ── Level 2: CHANGE APPROACH (count == threshold + 1) ──
    if count == threshold + 1:
        return (
            f"## 🔄 LOOP RECOVERY: CHANGE APPROACH (attempt #{count})\n"
            f"\n"
            f"You have delegated this same task {count} times. Splitting alone "
            f"did not resolve the issue. **A fundamentally different approach is required.**\n"
            f"\n"
            f"### Required Strategy: CHANGE APPROACH\n"
            f"\n"
            f"1. **Try a different agent profile** — if using `{profile}`, consider "
            f"`{'frontend' if profile == 'code' else 'code'}` or `debug`\n"
            f"2. **Rewrite the task description** with different goals or constraints\n"
            f"3. **Decompose differently** — change the architectural approach, not just "
            f"the task size\n"
            f"4. **Do the work directly** without delegation if possible\n"
            f"\n"
            f"Profile: `{profile}` | Task preview: `{message[:80]}...`"
            f"{req_context}{failure_context}\n"
        )

    # ── Level 3: 5-WHY SELF-DIAGNOSIS + SEARCH (count == threshold + 2) ──
    if count == threshold + 2:
        return (
            f"## 🔍 LOOP RECOVERY: 5-WHY DIAGNOSTIC MODE (attempt #{count})\n"
            f"\n"
            f"You have delegated this same task {count} times. Both splitting AND "
            f"changing approach failed. **Before retrying, you MUST diagnose the root cause.**\n"
            f"\n"
            f"### Required Strategy: DIAGNOSE THEN SEARCH THEN FIX\n"
            f"\n"
            f"**Step 1 — Diagnose (5-WHY)**:\n"
            f"1. Read the subordinate's last error output carefully\n"
            f"2. Use `search` / `grep` / `read_file` to examine the failing code\n"
            f"3. Ask 5 WHYs: Why did it fail? → Why was that import wrong? → "
            f"Why wasn't it in package.json? → ...\n"
            f"4. Write down the ROOT CAUSE (not the symptom)\n"
            f"\n"
            f"**Step 2 — Search for solutions**:\n"
            f"5. Use your search tools (web search, context search) to find how "
            f"others have solved this exact error\n"
            f"6. Search the project codebase for similar patterns that work\n"
            f"7. Check package.json, tsconfig.json, and config files for mismatches\n"
            f"\n"
            f"**Step 3 — Fix with specific context**:\n"
            f"8. Include the SPECIFIC root cause and fix in the new delegation brief\n"
            f"9. Tell the agent EXACTLY what file to change and how\n"
            f"10. Do NOT send a generic 'fix the build' — send 'add glob to "
            f"package.json devDependencies'\n"
            f"\n"
            f"Profile: `{profile}` | Task preview: `{message[:80]}...`"
            f"{failure_context}\n"
        )

    # ── Level 4: BACKLOG + ADVANCE (count >= threshold + 3) ──
    return (
        f"## ⏭️ LOOP RECOVERY: BACKLOG AND ADVANCE (attempt #{count})\n"
        f"\n"
        f"You have delegated this same task {count} times. Splitting, changing "
        f"approach, AND diagnostic mode all failed. **This task is blocked. "
        f"Move on to remaining phases.**\n"
        f"\n"
        f"### Required Strategy: BACKLOG + ADVANCE\n"
        f"\n"
        f"1. **Mark this phase as 'backlogged'** — do NOT abandon it, but park it\n"
        f"2. **Advance to the next pending phase** — other phases may not depend "
        f"on this one\n"
        f"3. **After all other phases complete**, retry this backlogged phase with "
        f"enriched context from the completed work\n"
        f"4. **Report partial completion** — tell the orchestrator what succeeded "
        f"and what's backlogged\n"
        f"\n"
        f"**Why this works**: The failing task often lacks context that later phases "
        f"would provide. Building Phase 4 (verification) first gives diagnostic data "
        f"that makes Phase 3.9 (remediation) solvable on retry.\n"
        f"\n"
        f"Profile: `{profile}` | Task preview: `{message[:80]}...`"
        f"{failure_context}\n"
    )


