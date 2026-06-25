from __future__ import annotations
from python.agent import Agent, UserMessage
from python.helpers.tool import Tool, Response
from python.helpers.agent_tracer import AgentTracer
from python.helpers.rate_limiter import RateLimiter, coordinate_agent_wait
from python.helpers.delegation_result import DelegationResult
from python.helpers.output_truncation import truncate_output_middle_out
from python.helpers.delegation_guards import (
    check_terminal_profile_guard,
    check_same_profile_guard,
    check_circuit_breaker,
    check_redelegation_guard_wrapper,
    check_planning_only_guard,
    check_rework_cycle_guard,
    check_ontology_capability_guard,
    validate_profile_exists,
)
from python.helpers.delegation_message import (
    inject_swarm_instructions,
    inject_project_scope,
    propagate_data_to_subordinate,
    write_error_tracking_log,
)
from python.helpers.delegation_result_processing import (
    handle_none_result,
    handle_limit_tags,
    propagate_subordinate_data,
    build_delegation_result,
    run_post_delegation_gates,
    record_delegation_failure,
    append_to_result_ledger,
)
from python.helpers.delegation_sanitizer import sanitize_delegation_message
from python.helpers.manifest_assignment import assign_items_to_delegation, get_unassigned_items
from python.initialize import initialize_agent
from python.extensions.hist_add_tool_result import _90_save_tool_call_file as save_tool_call_file
import asyncio
import logging
import os

logger = logging.getLogger("agix.subordinate")

# Cache swarm instructions to avoid re-reading the file on every delegation
_swarm_instructions_cache: dict[str, str | None] = {}

import re as _re

# ── SS1: Deterministic last-error extraction ───────────────────────
# Patterns that indicate an error line in agent output
_ERROR_PATTERNS = [
    _re.compile(r"(?:Error|ERROR|error)[:：]\s*.+"),
    _re.compile(r"(?:FAIL|FAILED|Traceback|Exception|TypeError|SyntaxError|ImportError|ModuleNotFoundError).*"),
    _re.compile(r"npm ERR!.*"),
    _re.compile(r"(?:Build|Compile|Lint) (?:error|failed).*", _re.IGNORECASE),
    _re.compile(r"exit (?:code|status) [1-9]\d*.*", _re.IGNORECASE),
    _re.compile(r"\[ERROR\].*"),
    _re.compile(r"Command failed.*", _re.IGNORECASE),
]
# Lazy import to avoid circular dependency

def _get_boomerang_context(agent, calling_agent_name: str = "", all_tasks_succeeded: bool = True):
    from python.helpers.boomerang_context import get_boomerang_context
    return get_boomerang_context(agent, calling_agent_name=calling_agent_name, all_tasks_succeeded=all_tasks_succeeded)

def _inject_e2e_fail_routing(result, quality_eval: dict | None) -> str:
    """
    Inject a routing hint when E2E quality evaluation fails.
    
    When a subordinate returns QUALITY: FAIL (either via structured
    _quality_evaluation data or text pattern), append a strong routing
    directive telling the parent orchestrator to delegate fixes to the
    Code agent before re-running E2E.
    
    This addresses the iter73 gap where the orchestrator kept re-running
    E2E tests instead of routing to Code agent for fixes.
    
    Args:
        result: The subordinate's result text (may be str, Response, or None)
        quality_eval: Structured quality evaluation dict, or None
        
    Returns:
        Result with routing hint appended if QUALITY: FAIL, else unchanged
    """
    # RCA-347: Coerce non-string results. monologue() may return Response
    # objects (post F-1 fix). Extract .message before string operations.
    if result is None:
        result = ""
    elif not isinstance(result, str):
        result = str(getattr(result, 'message', result) or "")
    # Case 1: Structured quality evaluation with passed=False
    if quality_eval and not quality_eval.get('passed', True):
        issues = quality_eval.get('issues', [])
        if isinstance(issues, list):
            issues_text = "\n".join(f"- {issue}" for issue in issues) if issues else result[:500]
        else:
            issues_text = str(issues)
        
        routing_hint = (
            "\n\n---\n"
            "## ⚠️ E2E QUALITY FAILED — MANDATORY FIX ROUTING\n\n"
            "The E2E verification returned **QUALITY: FAIL**. "
            "You MUST delegate a fix task to the `code` agent (profile='code') with these issues:\n\n"
            f"{issues_text}\n\n"
            "**DO NOT:**\n"
            "- Re-run E2E testing (the issues won't fix themselves)\n"
            "- Use browser_agent to re-test (you can't fix code via browser)\n"
            "- Skip the fixes and declare done\n\n"
            "**DO:** Create a targeted `call_subordinate` with profile='code' listing each issue to fix, "
            "then re-run E2E after the Code agent completes.\n"
        )
        return result + routing_hint
    
    # Case 2: No structured eval but QUALITY: FAIL in text
    if quality_eval is None and "QUALITY: FAIL" in result:
        routing_hint = (
            "\n\n---\n"
            "## ⚠️ E2E QUALITY FAILED — MANDATORY FIX ROUTING\n\n"
            "The subordinate's response contains **QUALITY: FAIL**. "
            "You MUST delegate fixes to the `code` agent (profile='code') "
            "before re-running any E2E testing. Do NOT Re-run E2E without fixing first.\n"
        )
        return result + routing_hint
    
    # No QUALITY: FAIL signal — return unchanged
    return result

def _check_phase_order_before_delegation(
    detected_phase: int | None,
    project_dir: str,
    agent_data: dict,
) -> list[str]:
    """P1-4 + RCA-ITR49: Pre-delegation phase ordering check with soft-block.

    Wires the EXISTING check_decomposition_completeness() as a PRE-DELEGATION
    check. When a higher-numbered phase is delegated while lower-numbered
    phases are still pending:

    1. First MAX_PHASE_ORDER_RETRIES attempts: Returns a warning AND sets
       _phase_order_retry_guidance in agent_data with structured instructions
       telling the orchestrator to complete the pending phase first. The
       calling code prepends this guidance to the delegation message.
    2. After MAX_PHASE_ORDER_RETRIES: Warning becomes advisory-only (no
       guidance injection) to prevent deadlocks when a phase genuinely
       cannot complete.

    This is a SOFT BLOCK (nudge), NOT a hard block. The delegation always
    proceeds — the guidance just steers the orchestrator's next decision.

    Args:
        detected_phase: The phase number detected from the delegation message,
                        or None if detection failed.
        project_dir: Absolute path to the project directory.
        agent_data: The agent's mutable data dict.

    Returns:
        List of warning strings (empty if no ordering issue detected).
    """
    MAX_PHASE_ORDER_RETRIES = 2

    if detected_phase is None or not project_dir:
        return []

    try:
        from python.helpers.projects import check_decomposition_completeness
        decomp_result = check_decomposition_completeness(project_dir)
    except Exception:
        return []

    pending_phases = decomp_result.get("pending_phases", [])
    if not pending_phases:
        return []

    # F-3 FIX: Detect artifact gaps in completed phases.
    # When a phase was marked "completed" but its output artifact is missing,
    # this gives the orchestrator actionable re-delegation guidance.
    artifact_gap_guidance = ""
    try:
        from python.tools.requirements import detect_artifact_gaps
        all_phases = decomp_result.get("all_phases", pending_phases)
        gaps = detect_artifact_gaps(all_phases, project_dir)
        if gaps:
            agent_data["_artifact_gaps"] = gaps
            gap_lines = []
            for g in gaps[:3]:  # Limit to 3 gaps max
                gap_lines.append(
                    f"  • Phase {g['phase_seq']} ({g['phase_title']}): "
                    f"missing {', '.join(g['missing_artifacts'])}"
                )
            artifact_gap_guidance = (
                "\n🔴 ARTIFACT GAPS DETECTED — completed phases with missing outputs:\n"
                + "\n".join(gap_lines)
                + "\nRe-delegate the EARLIEST gap phase to produce the missing artifact."
            )
            logger.warning(
                f"[PHASE ORDER] F-3: {len(gaps)} artifact gap(s) detected: "
                f"{[g['phase_seq'] for g in gaps]}"
            )
    except Exception as e:
        logger.debug(f"[PHASE ORDER] F-3 artifact gap check skipped: {e}")

    # Find all pending phases with seq LESS THAN the detected phase
    # ISSUE-7 FIX: Use parse_phase_seq() tuple comparison instead of float().
    # Root cause: float("2.2.0") raises ValueError → fallback truncated to 2.0 →
    # Phase 2.2.0 incorrectly appeared as predecessor of Phase 2.1 (2.0 < 2.1).
    # Tuple comparison: (2,2,0) < (2,1,0) = False — correct ordering.
    # Supersedes RCA-455 float() fix which still broke on three-part seq values.
    from python.helpers.phase_parser import parse_phase_seq
    detected_phase_tuple = parse_phase_seq(detected_phase)
    predecessor_pending = []
    min_pending_seq = None
    min_pending_seq_raw = None
    for pp in pending_phases:
        try:
            seq_val = parse_phase_seq(pp.get("seq", "0"))
        except Exception:
            continue
        if seq_val < detected_phase_tuple:
            predecessor_pending.append(pp)
        if min_pending_seq is None or seq_val < min_pending_seq:
            min_pending_seq = seq_val
            min_pending_seq_raw = pp.get("seq", "?")

    if min_pending_seq is None:
        return []

    # Only warn if the current phase is strictly greater than the min pending
    if detected_phase_tuple <= min_pending_seq:
        return []


    # Track retry count PER PHASE (F-7 fix: was global, causing cross-phase exhaustion)
    # Migrate old global key if present
    if "_phase_order_retry_count" in agent_data:
        del agent_data["_phase_order_retry_count"]
    retry_counts = agent_data.get("_phase_order_retry_counts", {})
    phase_key = str(detected_phase)
    retry_count = retry_counts.get(phase_key, 0) + 1
    retry_counts[phase_key] = retry_count
    agent_data["_phase_order_retry_counts"] = retry_counts

    warning_msg = (
        f"[PHASE_ORDER_WARNING] Phase {detected_phase} is being delegated "
        f"while Phase {min_pending_seq_raw} is still pending."
    )
    logger.warning(warning_msg)

    # Store in agent_data for gate/audit consumption
    if "_phase_order_warnings" not in agent_data:
        agent_data["_phase_order_warnings"] = []
    agent_data["_phase_order_warnings"].append(warning_msg)

    # RCA-ITR49: Generate structured retry guidance (soft-block)
    if retry_count <= MAX_PHASE_ORDER_RETRIES:
        # Build list of pending predecessor phases
        pending_list = "; ".join(
            f"Phase {p['seq']}: {p.get('title', 'Untitled')} ({p.get('status', 'pending')})"
            for p in predecessor_pending[:5]
        )
        if not pending_list:
            pending_list = f"Phase {min_pending_seq_raw}"

        guidance = (
            f"⚠️ PHASE ORDER: You are attempting Phase {detected_phase} but "
            f"earlier phases are still pending: {pending_list}. "
            f"Please delegate the EARLIEST pending phase first "
            f"(Phase {min_pending_seq_raw}) before proceeding to Phase "
            f"{detected_phase}. Complete phases in order to ensure "
            f"prerequisites are met. (retry {retry_count}/{MAX_PHASE_ORDER_RETRIES})"
            f"{artifact_gap_guidance}"
        )
        agent_data["_phase_order_retry_guidance"] = guidance
        logger.info(
            f"[PHASE ORDER] Soft-block guidance injected "
            f"(retry {retry_count}/{MAX_PHASE_ORDER_RETRIES}): "
            f"finish Phase {min_pending_seq_raw} before Phase {detected_phase}"
        )
    else:
        # After max retries, become advisory-only — clear guidance
        agent_data["_phase_order_retry_guidance"] = ""
        logger.warning(
            f"[PHASE ORDER] Max retries ({MAX_PHASE_ORDER_RETRIES}) exceeded — "
            f"advisory-only mode, allowing Phase {detected_phase} "
            f"despite Phase {min_pending_seq_raw} pending"
        )

    return [warning_msg]

def _check_budget_before_first_delegation(agent_data: dict) -> str | None:
    """F-9 (ITR-49): One-time gate — enforce budget planning before first delegation.

    After decomposition, the orchestrator MUST call
    requirements(action='set_iteration_budget') to plan how many
    delegations it can afford within its turn budget. Without this,
    the orchestrator blindly delegates and can exhaust its budget
    before reaching implementation phases.

    This is a ONE-TIME nudge, not per-delegation overhead:
    - Fires on the first delegation when decomposition exists but budget isn't set
    - Sets _budget_nudge_fired flag so it never fires again
    - Skipped for subordinate agents (they don't plan budgets)

    Args:
        agent_data: The agent's mutable data dict.

    Returns:
        Guidance string if nudge should fire, None otherwise.
    """
    # Skip for subordinate agents — they don't plan budgets
    if agent_data.get("_superior"):
        return None

    # Skip if already nudged (one-time gate)
    if agent_data.get("_budget_nudge_fired"):
        return None

    # Skip if no decomposition yet (pre-planning phases)
    task_count = agent_data.get("_decomposition_task_count", 0)
    if not task_count or task_count <= 0:
        return None

    # Skip if budget already set (orchestrator planned ahead — good!)
    budget = agent_data.get("_llm_iteration_budget", 0)
    if isinstance(budget, (int, float)) and budget > 0:
        return None

    # NUDGE: Decomposition exists but no budget set before first delegation
    agent_data["_budget_nudge_fired"] = True

    guidance = (
        f"⚠️ BUDGET REQUIRED: You have decomposed {task_count} tasks but haven't "
        f"set a delegation budget. Before delegating, call:\n"
        f"  requirements(action='set_iteration_budget', "
        f"estimated_delegations=<number>)\n"
        f"This plans your turn budget so you don't exhaust iterations "
        f"before reaching implementation phases."
    )
    logger.warning(f"[BUDGET GATE] {guidance}")
    return guidance

def format_subordinate_context_brief(
    task_description: str,
    project_path: str = "",
    working_files: list[str] | None = None,
    do_not_touch: list[str] | None = None,
) -> str:
    """
    Generate a structured context brief for subordinate agents.
    
    This ensures subordinates receive clear boundaries and cannot
    autonomously expand their scope beyond the assigned task.
    
    Args:
        task_description: What the subordinate should do
        project_path: Absolute path to the project sandbox
        working_files: Optional list of files the subordinate may modify
        do_not_touch: Optional list of files/patterns to leave untouched
        
    Returns:
        Formatted context brief string
    """
    sections = []
    
    # Task assignment
    sections.append(f"## Task Assignment\n{task_description}")
    
    # Working boundaries
    boundary_lines = []
    if project_path:
        boundary_lines.append(f"- Project path: `{project_path}`")
        boundary_lines.append("- You may ONLY modify files under this project path")
    boundary_lines.append("- Do NOT modify framework files, configs, or other projects")
    if working_files:
        files_str = ", ".join(f"`{f}`" for f in working_files)
        boundary_lines.append(f"- Focus on these files: {files_str}")
    if do_not_touch:
        dont_touch_str = ", ".join(f"`{f}`" for f in do_not_touch)
        boundary_lines.append(f"- Do NOT touch: {dont_touch_str}")
    sections.append("## Working Boundaries\n" + "\n".join(boundary_lines))
    
    # Forbidden operations
    forbidden_lines = [
        "- Do NOT use `call_subordinate` or `call_subordinate_batch` — you do not have delegate access",
        "- Do NOT delete build directories (`.next`, `dist`, `node_modules`) while services are running",
        "- Do NOT modify files outside your assigned scope",
        "- Do NOT attempt to spawn additional agents — report back to the orchestrator instead",
    ]
    sections.append("## Forbidden Operations\n" + "\n".join(forbidden_lines))
    
    # Completion criteria
    completion_lines = [
        "- Complete the assigned task and report back via `response` tool",
        "- If you cannot complete the task, report what you tried and what failed",
        "- Do NOT attempt to fix unrelated issues — stay focused on your assignment",
    ]
    sections.append("## Completion Criteria\n" + "\n".join(completion_lines))
    
    return "\n\n".join(sections)

def _get_subordinate_timeout(args: dict, profile: str = "") -> float:
    """Compute subordinate timeout from tool args, env var, profile default, and max ceiling.

    RCA-365 F-6a: Accept dynamic budget from orchestrator.
    RCA hard-timeout-loop: Profile-aware defaults — code agents need more
    time for build+test cycles than browser agents.

    Priority:
        1. args["timeout_seconds"] (explicit from orchestrator)
        2. AGIX_SUBORDINATE_TIMEOUT env var (deployment default)
        3. Profile-specific default from _PROFILE_TIMEOUT_DEFAULTS
        4. Hardcoded 900s fallback

    A max ceiling (AGIX_SUBORDINATE_MAX_TIMEOUT, default 3600s) is enforced
    to prevent runaway subordinates.

    Args:
        args: Tool args dict (may contain timeout_seconds).
        profile: Agent profile name (e.g. "code", "browser", "e2e").
            Case-insensitive. Defaults to "" which uses 900s fallback.

    Returns:
        Timeout in seconds, clamped to the max ceiling.
    """
    # Profile-aware default timeouts (seconds)
    # Code agents run build+test cycles (~36s per iteration) and need 30 min.
    # Browser agents are typically quick. Architect is LLM-heavy, not tool-heavy.
    _PROFILE_TIMEOUT_DEFAULTS = {
        "code": 1800,        # 30 min — build+test cycles are expensive
        "architect": 600,    # 10 min — planning is LLM-heavy, not tool-heavy
        "e2e": 1200,         # 20 min — E2E tests take time
        "browser": 600,      # 10 min — browsing is usually quick
        "frontend": 1200,    # 20 min — design + code
        "debug": 900,        # 15 min — debugging is variable
        "review": 600,       # 10 min — code review
        "researcher": 600,   # 10 min — research tasks
    }
    profile_key = (profile or "").lower()
    profile_default = _PROFILE_TIMEOUT_DEFAULTS.get(profile_key, 900)

    # Env var overrides profile default (deployment-level config)
    env_override = os.environ.get("AGIX_SUBORDINATE_TIMEOUT")
    if env_override is not None:
        fallback = env_override
    else:
        fallback = str(profile_default)

    raw = float(args.get("timeout_seconds", fallback))
    max_ceiling = float(os.environ.get("AGIX_SUBORDINATE_MAX_TIMEOUT", "3600"))
    return min(raw, max_ceiling)

def _build_attempt_record(
    detected_phase,
    delegation_result,
    completion_check,
    pre_delegation_files,
    project_dir,
):
    """Build a structured attempt record for the phase attempt ledger.

    Called after a subordinate returns. Assembles data from the delegation
    result, completion validation, and file snapshot diff into a single
    record suitable for ``record_attempt()``.

    Args:
        detected_phase: Phase seq string (e.g. "3.1") or None.
        delegation_result: DelegationResult with status, errors, profile, etc.
        completion_check: PhaseCompletionResult with should_skip, evidence, etc.
        pre_delegation_files: Set of file paths captured before delegation, or None.
        project_dir: Absolute path to the project directory, or "".

    Returns:
        Attempt dict ready for ``record_attempt()``, or ``None`` if
        ``detected_phase`` is ``None``.
    """
    if detected_phase is None:
        return None

    from datetime import datetime

    # ── Determine status ──
    if completion_check and completion_check.should_skip:
        status = "partial"
    elif delegation_result.status in ("failed",):
        status = "failed"
    else:
        status = "complete"

    attempt = {
        "delegation_id": getattr(delegation_result, "task_guid", "") or getattr(delegation_result, "task_hash", ""),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "profile": getattr(delegation_result, "profile", ""),
        "files_created": [],
        "files_modified": [],
        "stubs_remaining": [],
        "build_errors": [],
        "test_failures": [],
        "status": status,
    }

    # ── Diff files to find what was created ──
    if project_dir and pre_delegation_files is not None:
        try:
            current_files = set()
            src_dir = os.path.join(project_dir, "src")
            if os.path.isdir(src_dir):
                for root, dirs, files in os.walk(src_dir):
                    # Skip __tests__ and node_modules
                    if "__tests__" in root or "node_modules" in root:
                        continue
                    for f in files:
                        current_files.add(
                            os.path.relpath(os.path.join(root, f), project_dir)
                        )
            new_files = current_files - set(pre_delegation_files or [])
            attempt["files_created"] = sorted(new_files)
        except Exception:
            pass  # Non-fatal — file scan failure shouldn't block recording

    # ── Extract stubs from completion evidence ──
    if completion_check and getattr(completion_check, "evidence", None):
        try:
            evidence = completion_check.evidence
            if hasattr(evidence, "to_dict"):
                evidence_dict = evidence.to_dict()
            elif isinstance(evidence, dict):
                evidence_dict = evidence
            else:
                evidence_dict = {}
            stubs = evidence_dict.get("stubs", []) or evidence_dict.get("stub_locations", [])
            attempt["stubs_remaining"] = [
                {"type": "stub", "detail": str(s)} for s in stubs[:10]
            ]
        except Exception:
            pass

    # ── Extract build errors from delegation_result ──
    if getattr(delegation_result, "errors", None):
        for err in delegation_result.errors[:5]:
            attempt["build_errors"].append({"type": "build_error", "detail": str(err)})

    return attempt

def _extract_last_error(subordinate) -> str:
    """Extract the last error from a subordinate's history — deterministic, no LLM.

    Walks the subordinate's message history in reverse, searching for lines
    that match common error patterns. Returns the first (most recent) match
    with up to 2 lines of surrounding context, capped at 500 chars.

    Returns empty string if no error pattern found.
    """
    try:
        history = subordinate.history
        if not history:
            return ""

        # Walk messages in reverse (most recent first)
        for msg in reversed(history):
            content = getattr(msg, "content", "") or ""
            if isinstance(content, list):
                # Handle multi-part messages
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            if not content:
                continue

            lines = content.splitlines()
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                for pattern in _ERROR_PATTERNS:
                    if pattern.search(line):
                        # Extract the matched line + 1 line above and below for context
                        start = max(0, i - 1)
                        end = min(len(lines), i + 2)
                        context_lines = lines[start:end]
                        excerpt = "\n".join(context_lines)
                        # Cap at 500 chars to prevent bloated relay messages
                        return truncate_output_middle_out(excerpt, max_chars=500, head_ratio=0.3)
    except Exception as e:
        logger.debug(f"_extract_last_error failed: {e}")
    return ""

def _cleanup_subordinate_ports(subordinate, result):
    """U-4: Cleanup dev server ports when subordinate fails/returns partial.

    When a subordinate returns None, PARTIAL, CANCELLED, or any failure sentinel,
    its dev server may still be occupying an allocated port. This function
    detects and kills those orphaned servers to prevent EADDRINUSE for the
    next subordinate.

    Args:
        subordinate: The subordinate Agent that completed.
        result: The string result returned by the subordinate.
    """
    # Only clean up if the result indicates failure
    # U-13 Fix: Use centralized sentinel_registry
    from python.helpers.sentinel_registry import get_limit_tags
    is_failure = (
        result is None
        or (isinstance(result, str) and any(
            tag in result for tag in get_limit_tags()
        ))
    )
    if not is_failure:
        return

    # Check if the subordinate had a dev server port allocated
    dev_port = subordinate.data.get("_dev_server_port")
    if not dev_port:
        return

    try:
        import subprocess
        import signal as _signal

        # Find and kill the process on the port
        cmd = f"lsof -ti :{dev_port}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        pids_raw = proc.stdout.strip()
        if not pids_raw:
            logger.debug(f"[U-4 CLEANUP] Port {dev_port} already free after subordinate failure")
            return

        pids = list(set(pids_raw.split("\n")))
        killed = []
        for pid_str in pids:
            try:
                pid = int(pid_str.strip())
                try:
                    os.killpg(os.getpgid(pid), _signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    os.kill(pid, _signal.SIGKILL)
                killed.append(pid)
            except (ProcessLookupError, OSError, ValueError):
                pass

        if killed:
            logger.info(
                f"[U-4 CLEANUP] Freed port {dev_port} after subordinate "
                f"{subordinate.agent_name} failure (killed PIDs: {killed})"
            )
    except Exception as e:
        logger.debug(f"[U-4 CLEANUP] Port cleanup failed for {dev_port}: {e}")
