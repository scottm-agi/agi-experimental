"""
Layer 1: Structural Guards — message_loop_start Extension

Deterministic structural checks that run every turn at ZERO cost (no LLM call).
This is the foundation of the dual-layer supervisor architecture.

Checks (in order):
1. Absolute turn limit → hard stop (non-bypassable)
2. Error budget exhausted → hard stop
3. Delegation depth exceeded → block further delegation
4. Deterministic detectors → escalate to Layer 2 (intelligent supervisor)

The 98% case: all checks pass → CONTINUE with zero overhead.
The ~2% case: a detector fires → set escalation signals for Layer 2.

This replaces _11_loop_limiter.py which was vulnerable to outer loop restarts
resetting the iteration counter.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from python.helpers.extension import Extension
from python.helpers.agent_core.base import AgentContextType

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("agix.extensions.structural_guards")

# ─── F-11: Error Fingerprinting ────────────────────────────────────
import hashlib
import re as _fp_re

# Patterns stripped from error messages before fingerprinting
_FP_LINE_NUMBER_RE = _fp_re.compile(r'(?:line |:)\d+')
_FP_TIMESTAMP_RE = _fp_re.compile(
    r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'
)
_FP_PROCESS_ID_RE = _fp_re.compile(r'(?:Process |pid[= ]?|PID[= ]?)\d+')
_FP_ABSOLUTE_PATH_RE = _fp_re.compile(r'(?:/[\w./-]+/)')  # /home/user/project/ → /


def _compute_error_fingerprint(error_context) -> str:
    """Compute a STABLE fingerprint for error identity tracking.

    Accepts either a dict with keys {error_message, file_path, error_type}
    or a raw error string. Strips variable parts (line numbers, timestamps,
    process IDs, absolute path prefixes) so that the same logical error
    produces the same fingerprint across runs and environments.

    F-11 (RCA-352): Used by L1 structural guards, build error advisor,
    and the intelligent supervisor to detect when the agent is fighting
    the SAME error across multiple redirect attempts.

    Args:
        error_context: Either a dict with error details or a raw error string.

    Returns:
        A hex digest string (MD5) that is stable for the same logical error.
    """
    if isinstance(error_context, str):
        raw = error_context
        error_type = ""
        file_path = ""
    elif isinstance(error_context, dict):
        raw = error_context.get("error_message", "")
        error_type = error_context.get("error_type", "")
        file_path = error_context.get("file_path", "")
    else:
        raw = str(error_context)
        error_type = ""
        file_path = ""

    # Normalize the error message
    normalized = raw
    # IMPORTANT: Strip timestamps BEFORE line numbers. The line number regex
    # (:digits) would eat parts of ISO timestamps (e.g., :00 in T09:00:00Z),
    # mangling them so the timestamp regex can't match.
    normalized = _FP_TIMESTAMP_RE.sub("", normalized)
    normalized = _FP_LINE_NUMBER_RE.sub("", normalized)
    normalized = _FP_PROCESS_ID_RE.sub("", normalized)
    normalized = _FP_ABSOLUTE_PATH_RE.sub("/", normalized)
    # Collapse whitespace
    normalized = " ".join(normalized.split()).strip().lower()

    # Normalize the file path — strip absolute prefix, keep relative
    if file_path:
        # Strip common absolute prefixes
        file_path = _FP_ABSOLUTE_PATH_RE.sub("/", file_path)
        file_path = file_path.strip("/").lower()

    # Build the fingerprint input
    fp_input = f"{error_type.lower()}|{file_path}|{normalized}"
    return hashlib.md5(fp_input.encode("utf-8")).hexdigest()


# Minimum turns between Layer 2 escalations to prevent rapid-fire LLM calls
SUPERVISOR_COOLDOWN_TURNS = 5

# U-12 (RCA-313): Shorter cooldown for critical-severity signals.
# Critical signals (e.g., tool_call_repetition at critical severity) use
# this reduced cooldown instead of SUPERVISOR_COOLDOWN_TURNS, cutting
# escalation latency from 3-5 iterations to 1-2.
CRITICAL_COOLDOWN_TURNS = 2


class StructuralGuards(Extension):
    """
    Layer 1: Deterministic structural guards.

    Runs every message_loop_start turn. Zero LLM calls. Checks:
    1. Absolute turn limit (hard stop, no bypass)
    2. Error budget (consecutive errors → hard stop)
    3. Delegation depth (prevent infinite sub-spawning)
    4. Deterministic detectors → escalate to Layer 2 (if present)

    This extension REPLACES _11_loop_limiter.py.
    """

    async def execute(self, loop_data: Optional["LoopData"] = None, **kwargs) -> None:
        if not loop_data:
            return

        agent = self.agent
        if not agent:
            return

        # Increment the non-resettable turn counter
        agent._absolute_turns += 1



        # Record wall-clock timestamp for L2 supervisor stall detection.
        # This allows the supervisor to detect agents stuck mid-turn
        # (hung LLM call, orphaned subordinate) by checking if this
        # timestamp is stale.
        agent.data["_last_turn_timestamp"] = time.time()

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ CHECK 0: Monologue Loop Detection & Correction           ║
        # ║ Detects N consecutive turns where agent produced text    ║
        # ║ but no valid tool call. Auto-corrects by injecting hint  ║
        # ║ or forcing a response tool call.                         ║
        # ╚═══════════════════════════════════════════════════════════╝
        await self._check_monologue_loop(agent, loop_data)

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ CHECK 1: Absolute Turn Limit → HARD STOP                 ║
        # ╚═══════════════════════════════════════════════════════════╝
        max_turns = agent.get_max_turns()
        if agent._absolute_turns >= max_turns:
            logger.error(
                f"[STRUCTURAL GUARD] Agent {agent.agent_name} hit absolute turn "
                f"limit ({agent._absolute_turns}/{max_turns}). Hard stopping."
            )
            agent.log(
                type="error",
                heading="🛑 Turn Limit Reached",
                content=(
                    f"Agent hit absolute turn limit ({max_turns}). "
                    f"Delivering current results. This counter resets only "
                    f"when you send a new message."
                ),
            )
            loop_data.is_done = True
            loop_data.stop_reason = f"Absolute turn limit ({max_turns}) reached"
            # Signal failure to parent (DO NOT set context.paused — it creates
            # an infinite spin loop with no timeout. is_done=True is sufficient
            # to break the monologue loop. See rca_asyncio_blocking_coe.md)
            if hasattr(agent, "context") and agent.context:
                agent.context._execution_status = "FAILED"
                agent.context._failure_reason = f"Absolute turn limit ({max_turns}) reached"
            return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ CHECK 2: Error Budget → HARD STOP                        ║
        # ╚═══════════════════════════════════════════════════════════╝
        error_budget = 20 if self._is_task_context(agent) else 10
        if agent._error_count >= error_budget:
            logger.error(
                f"[STRUCTURAL GUARD] Agent {agent.agent_name} exhausted error "
                f"budget ({agent._error_count}/{error_budget}). Hard stopping."
            )
            agent.log(
                type="error",
                heading="🛑 Error Budget Exhausted",
                content=(
                    f"Agent hit {agent._error_count} errors (budget: {error_budget}). "
                    f"Delivering best-effort results."
                ),
            )
            loop_data.is_done = True
            loop_data.stop_reason = f"Error budget ({error_budget}) exhausted"
            # Signal failure to parent (DO NOT set context.paused — it creates
            # an infinite spin loop with no timeout. is_done=True is sufficient.
            # See rca_asyncio_blocking_coe.md)
            if hasattr(agent, "context") and agent.context:
                agent.context._execution_status = "FAILED"
                agent.context._failure_reason = f"Error budget ({error_budget}) exhausted after {agent._error_count} errors"
            return

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ CHECK 3: Delegation Depth → BLOCK FURTHER DELEGATION      ║
        # ╚═══════════════════════════════════════════════════════════╝
        max_depth = 4
        current_depth = agent.data.get("_delegation_depth", 0)
        if current_depth < max_depth:
            # Compute depth from superior chain if not cached
            current_depth = self._compute_delegation_depth(agent)
            agent.data["_delegation_depth"] = current_depth

        if current_depth >= max_depth:
            agent.data["_delegation_blocked"] = True
            logger.warning(
                f"[STRUCTURAL GUARD] Agent {agent.agent_name} at max delegation "
                f"depth ({current_depth}/{max_depth}). Blocking further delegation."
            )
        else:
            agent.data["_delegation_blocked"] = False

        # ╔═══════════════════════════════════════════════════════════╗
        # ║ CHECK 4: Deterministic Detectors → ESCALATE TO LAYER 2   ║
        # ║                                                           ║
        # ║ CRITICAL BYPASS (Iteration 149 RCA): Always run detectors ║
        # ║ — critical-severity signals (e.g. tool_call_repetition)   ║
        # ║ bypass cooldown for immediate L2 escalation. Non-critical ║
        # ║ signals respect the cooldown to prevent rapid-fire LLM.   ║
        # ╚═══════════════════════════════════════════════════════════╝
        last_escalation_turn = agent.data.get("_last_l2_escalation_turn", 0)
        standard_in_cooldown = (agent._absolute_turns - last_escalation_turn) < SUPERVISOR_COOLDOWN_TURNS
        # U-12: Critical signals use a SHORTER cooldown (2 vs 5 turns)
        critical_in_cooldown = (agent._absolute_turns - last_escalation_turn) < CRITICAL_COOLDOWN_TURNS

        # Always run detectors (even during cooldown) to check for critical signals
        signals = self._run_deterministic_detectors(agent)
        has_critical = any(s.get("severity") == "critical" for s in (signals or []))

        # U-12: Critical signals respect the shorter CRITICAL_COOLDOWN_TURNS;
        # non-critical signals respect the standard SUPERVISOR_COOLDOWN_TURNS.
        in_cooldown = critical_in_cooldown if has_critical else standard_in_cooldown

        if signals and (not in_cooldown or has_critical):
            # Critical signals use reduced cooldown — escalate faster
            if has_critical and standard_in_cooldown and not critical_in_cooldown:
                logger.warning(
                    f"[STRUCTURAL GUARD] Critical signal detected during cooldown — "
                    f"bypassing cooldown for immediate L2 escalation: "
                    f"{[s['detector'] for s in signals if s.get('severity') == 'critical']}"
                )
            agent.data["_l2_escalation_signals"] = signals
            agent.data["_last_l2_escalation_turn"] = agent._absolute_turns
            logger.info(
                f"[STRUCTURAL GUARD] Agent {agent.agent_name} — {len(signals)} "
                f"detector(s) fired → escalating to Layer 2: "
                f"{[s['detector'] for s in signals]}"
            )
        elif not signals:
            # Clear any stale signals
            agent.data.pop("_l2_escalation_signals", None)
        else:
            # Non-critical signals during cooldown — suppress
            agent.data["_cooldown_suppressed"] = True

        # 98% case: all checks pass, zero cost, CONTINUE

    # ------------------------------------------------------------------
    # Deterministic Detectors
    # ------------------------------------------------------------------

    def _run_deterministic_detectors(self, agent: "Agent") -> List[Dict[str, Any]]:
        """Run lightweight deterministic detectors. Zero LLM calls.

        Returns a list of signal dicts for any detectors that fire.
        Each signal has: {"detector": str, "severity": str, "detail": str}
        """
        signals: List[Dict[str, Any]] = []

        # DETECTOR 1: MD5 Repetition — same fingerprint 3+ times in last 20
        md5_signal = self._detect_md5_repetition(agent)
        if md5_signal:
            signals.append(md5_signal)

        # DETECTOR 2: Error Cascade — threshold depends on context
        error_cascade_threshold = self._get_error_cascade_threshold(agent)
        if agent._error_count >= error_cascade_threshold:
            signals.append({
                "detector": "error_cascade",
                "severity": "high",
                "detail": f"{agent._error_count} consecutive errors (threshold: {error_cascade_threshold})",
            })

        # DETECTOR 3: Tool Failure — same tool failing repeatedly (from MD5 log)
        tool_signal = self._detect_tool_failure_loop(agent)
        if tool_signal:
            signals.append(tool_signal)

        # DETECTOR 4: Rapid Delegation — 5+ subordinates in 10 turns
        delegation_signal = self._detect_rapid_delegation(agent)
        if delegation_signal:
            signals.append(delegation_signal)

        # DETECTOR 5: Monologue Loop — consecutive turns without tool call
        monologue_signal = self._detect_monologue_loop(agent)
        if monologue_signal:
            signals.append(monologue_signal)

        # DETECTOR 6: Tool Call Repetition — same tool called 5+ times consecutively
        # 5-Why RCA (2026-04-22): Catches the "tool success loop" gap where agents
        # call the same tool repeatedly with slightly different args but make zero
        # progress. Uses live recent_tool_calls data (not the dead _md5_action_log).
        tool_rep_signal = self._detect_tool_call_repetition(agent)
        if tool_rep_signal:
            signals.append(tool_rep_signal)

        # DETECTOR 7: Progress Stagnation — argument diversity bridge (Fix F5)
        # RCA Iteration 158, Issue E: ProgressVelocityDetector existed as deep
        # async detector but was never wired to L1. Agents escaped all existing
        # detectors by calling same tool with subtly different arguments.
        # This lightweight synchronous bridge checks argument diversity.
        stagnation_signal = self._detect_progress_stagnation(agent)
        if stagnation_signal:
            signals.append(stagnation_signal)

        # DETECTOR 8: Multi-tool Oscillation Cycle — A→B→A→B pattern
        # RCA-299: Catches agents stuck in read-only diagnostic loops
        # (e.g., ls→cat→ls→cat or read_file→sequential_thinking→read_file).
        oscillation_signal = self._detect_oscillation_cycle(agent)
        if oscillation_signal:
            signals.append(oscillation_signal)

        # DETECTOR 9: Write-Progress Ratio — reads vs writes ratio
        # RCA-299: After N turns, if agent has zero write operations,
        # escalate. Excludes code_execution_tool (legitimate data work).
        write_ratio_signal = self._detect_low_write_ratio(agent)
        if write_ratio_signal:
            signals.append(write_ratio_signal)

        # DETECTOR 10: Cross-Delegation Spiral — same failure from 3+ subordinates
        # SS-7 (P0-3): Detects when the orchestrator is re-delegating the same
        # failing task without addressing the root cause. Reads the
        # _delegation_health_ledger written by propagate_subordinate_data().
        spiral_signal = self._detect_cross_delegation_spiral(agent)
        if spiral_signal:
            signals.append(spiral_signal)

        # DETECTOR 11: Excessive Restarts — checkpoint.json restart_count
        # SS-1/5: Detects when the container has restarted too many times,
        # indicating a possible crash loop. Reads .agix.proj/checkpoint.json.
        restart_signal = self._detect_excessive_restarts(agent)
        if restart_signal:
            signals.append(restart_signal)

        return signals

    def _detect_md5_repetition(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Check for repeated MD5 fingerprints in recent actions."""
        log = agent._md5_action_log
        threshold = self._get_md5_repetition_threshold(agent)
        if len(log) < threshold:
            return None

        # Check last 20 actions for repeated fingerprints
        recent = log[-20:]
        fp_counts = Counter(entry["fingerprint"] for entry in recent)

        for fp, count in fp_counts.most_common(1):
            if count >= threshold:
                return {
                    "detector": "md5_repetition",
                    "severity": "high",
                    "detail": (
                        f"Fingerprint {fp[:8]}... appeared {count}x in last "
                        f"{len(recent)} actions"
                    ),
                }
        return None

    def _detect_tool_failure_loop(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Check for the same tool failing repeatedly."""
        failed_count = getattr(agent, "_failed_tool_count", 0)
        threshold = self._get_tool_failure_threshold(agent)
        if failed_count >= threshold:
            return {
                "detector": "tool_failure_loop",
                "severity": "high",
                "detail": f"{failed_count} consecutive tool failures",
            }
        return None

    def _detect_rapid_delegation(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Check for too many delegations in a short window."""
        sub_count = agent.data.get("_subordinate_call_count", 0)
        if sub_count >= 5 and agent._absolute_turns <= 10:
            return {
                "detector": "rapid_delegation",
                "severity": "medium",
                "detail": (
                    f"{sub_count} subordinates spawned in first "
                    f"{agent._absolute_turns} turns"
                ),
            }
        return None

    # Tool Call Repetition Threshold — consecutive same-tool calls
    TOOL_REPETITION_THRESHOLD = 8  # Raised from 2 (RCA-231): sequential-thinking needs many consecutive calls
    # Elevated threshold during gate-driven retries (fix→resubmit cycles)
    GATE_RETRY_REPETITION_THRESHOLD = 5
    # Per-tool overrides — productive tools that legitimately run many consecutive
    # times (e.g., code agent writing 10 files in a row). Without this, the detector
    # false-positives on normal implementation work (RCA-356 Issue 2a).
    TOOL_REPETITION_OVERRIDES = {
        "write_to_file": 15,
        "replace_in_file": 15,
        "save_file": 15,
        "create_file": 15,
        "code_execution_tool": 12,
        "call_subordinate": 12,
    }

    def _detect_tool_call_repetition(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect consecutive calls to the same tool from recent_tool_calls.

        Unlike the MD5 repetition detector (which checks exact fingerprint matches),
        this detector checks for the same tool_name regardless of argument differences.
        This catches the 'tool success loop' pattern where agents call the same tool
        with slightly varied arguments but make zero forward progress.

        Gate-Aware (Stability Fix): When _gate_retry_active is True, the threshold
        is elevated from 2→5 to allow the orchestrator to fix and re-submit without
        triggering false-positive loop detection. This prevents structural guards
        from killing legitimate gate-driven retry cycles.

        Tool-Specific (RCA-356 Issue 2a): Productive tools like write_to_file get
        elevated thresholds because consecutive calls with different file paths is
        normal implementation workflow, not a loop.

        5-Why RCA (2026-04-22): This is the gap that let the MainStreet agent call
        code_execution_tool 8+ times without intervention — the monologue detector
        didn't fire because tools WERE being called, and the MD5 detector was dead
        code (fingerprint_action() never called).
        """
        recent_calls = agent.data.get("recent_tool_calls", [])
        if not recent_calls:
            return None

        # Count consecutive same-tool calls from the tail
        streak = 1
        last_tool = recent_calls[-1].get("tool_name", "")
        if not last_tool:
            return None

        for i in range(len(recent_calls) - 2, -1, -1):
            if recent_calls[i].get("tool_name") == last_tool:
                streak += 1
            else:
                break

        # Gate retry no longer exists — gate system uses gate_router.py
        gate_retry = False

        # Priority: gate retry override > per-tool override > base threshold
        if gate_retry:
            threshold = self.GATE_RETRY_REPETITION_THRESHOLD
        elif last_tool in self.TOOL_REPETITION_OVERRIDES:
            threshold = self.TOOL_REPETITION_OVERRIDES[last_tool]
        else:
            threshold = self.TOOL_REPETITION_THRESHOLD

        if streak >= threshold:
            return {
                "detector": "tool_call_repetition",
                "severity": "critical",
                "detail": (
                    f"'{last_tool}' called {streak} consecutive times with "
                    f"different arguments — agent is stuck in a tool success loop"
                    + (f" (gate retry threshold: {threshold})" if gate_retry else "")
                ),
            }
        return None

    # Progress Stagnation — minimum turns before detector fires
    STAGNATION_MIN_TURNS = 10
    STAGNATION_MIN_CALLS = 3
    STAGNATION_DIVERSITY_THRESHOLD = 0.3

    def _detect_progress_stagnation(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect low argument diversity in recent tool calls.

        Fix F5 (RCA Iteration 158): Bridge from ProgressVelocityDetector's
        velocity metrics into the L1 deterministic detector pipeline. This
        catches the pattern where agents call tools with near-identical
        arguments — subtly different enough to escape DETECTOR 6 (which only
        checks tool NAME repetition) but making zero forward progress.

        The metric is argument diversity: how many unique argument sets exist
        among recent calls to the same tool. A diversity score below 0.3
        means the agent is essentially repeating the same operation.
        """
        # Guard: don't fire in early turns (legitimate setup phase)
        if agent._absolute_turns < self.STAGNATION_MIN_TURNS:
            return None

        recent_calls = agent.data.get("recent_tool_calls", [])
        if len(recent_calls) < self.STAGNATION_MIN_CALLS:
            return None

        # Compute argument diversity (same algorithm as ProgressVelocityDetector)
        window = recent_calls[-10:]
        by_tool: Dict[str, list] = {}
        for tc in window:
            tool_name = tc.get("tool_name", "")
            if not tool_name:
                continue
            if tool_name not in by_tool:
                by_tool[tool_name] = []
            by_tool[tool_name].append(json.dumps(tc.get("arguments", {}), sort_keys=True))

        # Check each tool's argument diversity
        for tool_name, arg_strings in by_tool.items():
            if len(arg_strings) < self.STAGNATION_MIN_CALLS:
                continue
            unique_args = len(set(arg_strings))
            diversity = unique_args / len(arg_strings)
            if diversity < self.STAGNATION_DIVERSITY_THRESHOLD:
                return {
                    "detector": "progress_stagnation",
                    "severity": "high",
                    "detail": (
                        f"'{tool_name}' called {len(arg_strings)}x with "
                        f"argument diversity {diversity:.2f} (threshold: "
                        f"{self.STAGNATION_DIVERSITY_THRESHOLD}). Agent is "
                        f"repeating near-identical operations without progress."
                    ),
                }
        return None

    # Oscillation cycle detection — minimum pattern length
    OSCILLATION_WINDOW = 8
    OSCILLATION_MIN_REPEATS = 3

    def _detect_oscillation_cycle(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect multi-tool oscillation patterns (A→B→A→B or A→B→C→A→B→C).

        DETECTOR 8 (RCA-299): Catches agents stuck in read-only diagnostic
        loops like read_file→sequential_thinking→read_file. Looks for
        repeating subsequences of length 2 or 3 in the recent tool call
        history. Fires as L2 escalation hint, NOT force-stop.
        """
        recent_calls = agent.data.get("recent_tool_calls", [])
        if len(recent_calls) < self.OSCILLATION_WINDOW:
            return None

        # Extract tool names from the last N calls
        tool_names = [
            tc.get("tool_name", "") for tc in recent_calls[-self.OSCILLATION_WINDOW:]
        ]
        tool_names = [t for t in tool_names if t]
        if len(tool_names) < self.OSCILLATION_WINDOW:
            return None

        # Check for length-2 cycles: A→B→A→B→A→B
        for cycle_len in (2, 3):
            if len(tool_names) < cycle_len * self.OSCILLATION_MIN_REPEATS:
                continue
            # Extract the candidate cycle from the tail
            candidate = tool_names[-cycle_len:]
            # Count how many times this cycle repeats going backward
            repeats = 0
            for i in range(len(tool_names) - cycle_len, -1, -cycle_len):
                window = tool_names[i:i + cycle_len]
                if window == candidate:
                    repeats += 1
                else:
                    break
            if repeats >= self.OSCILLATION_MIN_REPEATS:
                cycle_str = " → ".join(candidate)
                return {
                    "detector": "oscillation_cycle",
                    "severity": "high",
                    "detail": (
                        f"Tool oscillation detected: [{cycle_str}] repeated "
                        f"{repeats}x in last {self.OSCILLATION_WINDOW} calls. "
                        f"Agent is stuck in a diagnostic loop — WRITE CODE instead."
                    ),
                }
        return None

    # Write-Progress Ratio — minimum turns before checking
    WRITE_RATIO_MIN_TURNS = 15  # Don't fire early — give agents time to research
    WRITE_RATIO_MIN_CALLS = 8
    WRITE_RATIO_THRESHOLD = 0.05  # Less than 5% productive ops → escalate

    # Tools that count as "productive" operations — anything that
    # produces output, executes code, delegates work, or advances the task.
    # This is deliberately broad to avoid false positives during legitimate
    # data processing, research, or multi-agent coordination work.
    PRODUCTIVE_TOOLS = frozenset({
        # File writes
        "write_to_file", "replace_in_file", "save_file", "create_file",
        # Code execution (builds, data processing, installs)
        "code_execution_tool",
        # Delegation (coordinating subordinates IS progress)
        "call_subordinate", "call_sub",
        # Task completion
        "response",
        # Knowledge/search (actively gathering — not just reading files)
        "knowledge_tool", "web_search",
    })

    def _detect_low_write_ratio(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect agents stuck in pure read-only diagnostic loops.

        DETECTOR 9 (RCA-299): After 15+ turns, checks the ratio of
        productive operations vs total operations. Only fires when the
        agent has done ZERO productive work — pure read_file + 
        sequential_thinking loops.

        Productive tools include code execution (data work), delegation
        (multi-agent coordination), and knowledge tools (research).
        This avoids false positives during legitimate big-data or
        research-heavy workflows.

        Fires as L2 escalation hint (injected via intelligent supervisor),
        NOT force-stop.
        """
        if agent._absolute_turns < self.WRITE_RATIO_MIN_TURNS:
            return None

        recent_calls = agent.data.get("recent_tool_calls", [])
        if len(recent_calls) < self.WRITE_RATIO_MIN_CALLS:
            return None

        # Check last 15 calls for any productive work
        window = recent_calls[-15:]
        total = len(window)
        productive = sum(
            1 for tc in window
            if tc.get("tool_name", "") in self.PRODUCTIVE_TOOLS
        )

        if total == 0:
            return None

        ratio = productive / total
        if ratio < self.WRITE_RATIO_THRESHOLD:
            return {
                "detector": "low_write_ratio",
                "severity": "high",
                "detail": (
                    f"Productivity ratio: {productive}/{total} ({ratio:.0%}) "
                    f"productive operations in last {total} calls. Agent has "
                    f"spent {total - productive} calls on read/diagnostic "
                    f"tools without writing code, executing, or delegating. "
                    f"Stop diagnosing and START BUILDING."
                ),
            }
        return None

    # ------------------------------------------------------------------
    # Cross-Delegation Spiral Detection (SS-7)
    # ------------------------------------------------------------------

    CROSS_DELEGATION_SPIRAL_THRESHOLD = 3  # Same fingerprint from 3+ subordinates
    CROSS_DELEGATION_CONSECUTIVE_THRESHOLD = 5  # Any 5+ consecutive failures

    def _detect_cross_delegation_spiral(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect when multiple subordinates fail with the same root cause.

        SS-7 (P0-3): Reads _delegation_health_ledger and checks for repeated
        failure fingerprints. When 3+ subordinates fail with the same error
        fingerprint, the orchestrator is in a delegation spiral — re-delegating
        the same failing task without addressing the root cause.

        Also fires on 5+ consecutive failures (any fingerprint) — something
        is fundamentally broken at the orchestrator level.

        ZERO-COST: Counter operations only, no LLM call.
        """
        ledger = agent.data.get("_delegation_health_ledger", [])
        if not ledger:
            return None

        # Check 1: Consecutive failed delegations (any fingerprint)
        consec = agent.data.get("_consecutive_failed_delegations", 0)
        if consec >= self.CROSS_DELEGATION_CONSECUTIVE_THRESHOLD:
            recent_failure = next(
                (e for e in reversed(ledger) if e.get("status") == "FAILED"), {}
            )
            return {
                "detector": "cross_delegation_spiral",
                "severity": "critical",
                "detail": (
                    f"{consec} CONSECUTIVE delegation failures. Last failure: "
                    f"'{recent_failure.get('failure_reason', '?')[:100]}'. "
                    f"Re-delegating will not fix this."
                ),
                "spiral_count": consec,
                "spiral_type": "consecutive",
            }

        # Check 2: Same fingerprint from 3+ subordinates
        failed = [e for e in ledger[-20:] if e.get("status") == "FAILED"]
        if len(failed) < self.CROSS_DELEGATION_SPIRAL_THRESHOLD:
            return None

        fp_counts = Counter(
            e.get("failure_fingerprint", "")
            for e in failed
            if e.get("failure_fingerprint")
        )
        for fp, count in fp_counts.most_common(1):
            if count >= self.CROSS_DELEGATION_SPIRAL_THRESHOLD and fp:
                sample = next(
                    (e for e in failed if e.get("failure_fingerprint") == fp), {}
                )
                profiles = [
                    e.get("profile", "?")
                    for e in failed
                    if e.get("failure_fingerprint") == fp
                ]
                return {
                    "detector": "cross_delegation_spiral",
                    "severity": "critical",
                    "detail": (
                        f"{count} subordinates ({', '.join(profiles[:5])}) failed "
                        f"with the SAME root cause: "
                        f"'{sample.get('failure_reason', '?')[:100]}'. "
                        f"Re-delegating will not fix this."
                    ),
                    "spiral_count": count,
                    "spiral_type": "same_fingerprint",
                    "failure_fingerprint": fp,
                }
        return None

    # ------------------------------------------------------------------
    # DETECTOR 11: Excessive Restarts
    # ------------------------------------------------------------------

    def _detect_excessive_restarts(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect excessive container restarts from checkpoint.json.

        SS-1/5: Reads .agix.proj/checkpoint.json restart_count.
        Thresholds: 3+ → advisory, 6+ → high severity escalation.

        Fail-open: returns None if project_dir is unavailable or on any error.
        ZERO-COST: Single file read, no LLM call.
        """
        project_dir = agent.data.get("_active_project_dir", "")
        if not project_dir:
            return None

        try:
            from python.helpers.phase_checkpoint import detect_excessive_restarts
            return detect_excessive_restarts(project_dir)
        except Exception as e:
            logger.debug(
                f"[STRUCTURAL GUARD] Excessive restart detection failed "
                f"(non-fatal): {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Monologue Loop Detection & Correction
    # ------------------------------------------------------------------

    # Default thresholds (interactive chat)
    MONOLOGUE_WARN_THRESHOLD = 3   # Warn and inject hint after 3 no-tool turns
    MONOLOGUE_FORCE_THRESHOLD = 5  # Force response after 5 no-tool turns
    # TASK context thresholds (long-running automated work)
    TASK_MONOLOGUE_WARN_THRESHOLD = 8
    TASK_MONOLOGUE_FORCE_THRESHOLD = 15

    def _detect_monologue_loop(self, agent: "Agent") -> Optional[Dict[str, Any]]:
        """Detect consecutive turns where agent produced no valid tool call."""
        consecutive = agent.data.get("_monologue_consecutive_count", 0)
        warn_threshold, force_threshold = self._get_monologue_thresholds(agent)
        if consecutive >= warn_threshold:
            return {
                "detector": "monologue_loop",
                "severity": "high" if consecutive >= force_threshold else "medium",
                "detail": (
                    f"{consecutive} consecutive turns without a valid tool call. "
                    f"Agent is stuck in reasoning loop."
                ),
            }
        return None

    async def _check_monologue_loop(self, agent: "Agent", loop_data: "LoopData") -> None:
        """Check and correct monologue loops.

        Called at the START of each turn. Checks the PREVIOUS turn's outcome:
        - If last turn produced a valid tool call → reset counter to 0
        - If last turn produced no tool call → increment counter
        - At WARN threshold → inject hint into context
        - At FORCE threshold → inject forced response tool + hard break
        """
        # Initialize counter on first run
        if "_monologue_consecutive_count" not in agent.data:
            agent.data["_monologue_consecutive_count"] = 0

        # Check: did last turn execute a tool?
        # We detect this via last_tool_executed, which is set by agent_process_tools
        last_tool = agent.get_data("last_tool_executed")
        last_tool_time = agent.get_data("last_tool_time") or 0
        monologue_last_check_time = agent.data.get("_monologue_last_check_time", 0)

        if last_tool and last_tool_time > monologue_last_check_time:
            # Tool was executed since last check → reset counter
            agent.data["_monologue_consecutive_count"] = 0
            agent.data["_monologue_last_check_time"] = last_tool_time
            return

        # No tool executed since last check → increment
        agent.data["_monologue_consecutive_count"] = (
            agent.data.get("_monologue_consecutive_count", 0) + 1
        )
        agent.data["_monologue_last_check_time"] = time.time()
        consecutive = agent.data["_monologue_consecutive_count"]
        warn_threshold, force_threshold = self._get_monologue_thresholds(agent)

        if consecutive >= force_threshold:
            # FORCE CORRECTION: Inject forced response to break the spiral
            logger.error(
                f"[STRUCTURAL GUARD] Agent {agent.agent_name} stuck in monologue "
                f"loop ({consecutive} turns without tool call, threshold: {force_threshold}). Force-breaking."
            )
            agent.log(
                type="error",
                heading="🔴 Monologue Loop Force-Break",
                content=(
                    f"Agent produced {consecutive} consecutive turns of reasoning "
                    f"without executing any tool. Force-breaking with current state."
                ),
            )
            # Force the loop to end — the agent has been spinning without progress
            loop_data.is_done = True
            loop_data.stop_reason = (
                f"Monologue loop force-break after {consecutive} turns without "
                f"a valid tool call. Deliver best-effort results."
            )
            # ChainLimitFailureSignal: explicit failure status for parent
            if hasattr(agent, "context") and agent.context:
                agent.context._execution_status = "FAILED"
                agent.context._failure_reason = (
                    f"Monologue loop force-break after {consecutive} turns "
                    f"without a valid tool call"
                )
            agent.data["_monologue_consecutive_count"] = 0
            return

        elif consecutive >= warn_threshold:
            # WARN + CORRECT: Inject a hint to force tool usage
            logger.warning(
                f"[STRUCTURAL GUARD] Agent {agent.agent_name} monologue loop "
                f"detected ({consecutive} turns without tool call). Injecting hint."
            )
            agent.log(
                type="warning",
                heading="⚠️ Monologue Loop Detected",
                content=(
                    f"Agent produced {consecutive} consecutive turns of reasoning "
                    f"without a tool call. Injecting correction hint."
                ),
            )
            # Inject a short correction into the agent's context
            correction = (
                f"⚠️ MONOLOGUE LOOP ({consecutive} turns): You have been reasoning "
                f"without calling any tool for {consecutive} turns. You MUST call "
                f"a tool NOW. If you cannot decide which tool, use the 'response' "
                f"tool to deliver your current analysis. DO NOT produce more "
                f"reasoning — execute a tool call immediately."
            )
            await agent.hist_add_warning(message=correction)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_task_context(self, agent: "Agent") -> bool:
        """Check if agent is running in TASK context (scheduled/automated)."""
        try:
            if hasattr(agent, 'context') and agent.context:
                ctx_type = getattr(agent.context, 'type', None)
                return ctx_type == AgentContextType.TASK
        except Exception as e:
            logger.warning(f"[STRUCTURAL GUARD] Task context detection failed: {e}")
        return False

    def _get_monologue_thresholds(self, agent: "Agent") -> tuple:
        """Get (warn, force) monologue thresholds based on context."""
        if self._is_task_context(agent):
            return (self.TASK_MONOLOGUE_WARN_THRESHOLD, self.TASK_MONOLOGUE_FORCE_THRESHOLD)
        return (self.MONOLOGUE_WARN_THRESHOLD, self.MONOLOGUE_FORCE_THRESHOLD)

    def _get_md5_repetition_threshold(self, agent: "Agent") -> int:
        """Get MD5 repetition threshold based on context."""
        return 6 if self._is_task_context(agent) else 3

    def _get_tool_failure_threshold(self, agent: "Agent") -> int:
        """Get tool failure loop threshold based on context."""
        return 6 if self._is_task_context(agent) else 3

    def _get_error_cascade_threshold(self, agent: "Agent") -> int:
        """Get error cascade threshold based on context."""
        return 6 if self._is_task_context(agent) else 3

    def _compute_delegation_depth(self, agent: "Agent") -> int:
        """Walk the superior chain to compute delegation depth."""
        depth = 0
        current = agent
        while True:
            superior = current.data.get("_superior") if hasattr(current, "data") else None
            if not superior:
                break
            depth += 1
            current = superior
            if depth > 20:  # Safety cap
                break
        return depth
