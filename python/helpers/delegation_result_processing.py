"""
Delegation result processing extracted from call_subordinate.py (P1.1 modularization).

Contains all post-execution result handling: None/failure handling, data
propagation from subordinate to parent, delegation result envelope construction,
quality gates, failure classification, and error relay recording.

G-02: Also provides parse_e2e_verdict() to extract structured _quality_evaluation
data from E2E agent verdict text ("Overall Verdict: PASS/NEEDS WORK/FAIL").
"""
from __future__ import annotations
import logging
import re
from typing import TYPE_CHECKING, Optional, Dict

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.delegation_result import DelegationResult

logger = logging.getLogger("agix.subordinate")


# ─── G-02: E2E Verdict Patterns ──────────────────────────────────────────
# Match "Overall Verdict: PASS", "Overall Verdict: NEEDS WORK", "Overall Verdict: FAIL"
# Handles markdown bold (**) around both the label and value, optional colons,
# and case-insensitive matching.
# Examples:
#   - **Overall Verdict**: PASS
#   - **Overall Verdict**: **NEEDS WORK** (reason...)
#   - Overall Verdict: FAIL
_VERDICT_PATTERNS = {
    "PASS": re.compile(
        r"\*{0,2}Overall\s+Verdict\*{0,2}[:\s]*\*{0,2}\s*PASS\b", re.IGNORECASE
    ),
    "NEEDS_WORK": re.compile(
        r"\*{0,2}Overall\s+Verdict\*{0,2}[:\s]*\*{0,2}\s*NEEDS\s*WORK\b", re.IGNORECASE
    ),
    "FAIL": re.compile(
        r"\*{0,2}Overall\s+Verdict\*{0,2}[:\s]*\*{0,2}\s*FAIL\b", re.IGNORECASE
    ),
}


def parse_e2e_verdict(result_text: str) -> Optional[Dict]:
    """Parse E2E agent verdict from response text.

    Extracts "Overall Verdict: PASS/NEEDS WORK/FAIL" from the E2E agent's
    response text and converts it into a structured _quality_evaluation dict
    that the quality eval gate can consume.

    Args:
        result_text: The E2E agent's response text.

    Returns:
        Dict with {passed: bool, source: str, response: str} if verdict found,
        None if no verdict pattern detected.
    """
    if not result_text:
        return None

    # Check each verdict pattern (most restrictive first to avoid false matches)
    # NEEDS_WORK must be checked before PASS (since "PASS" could match a substring)
    for verdict_name, pattern in [
        ("NEEDS_WORK", _VERDICT_PATTERNS["NEEDS_WORK"]),
        ("FAIL", _VERDICT_PATTERNS["FAIL"]),
        ("PASS", _VERDICT_PATTERNS["PASS"]),
    ]:
        if pattern.search(result_text):
            passed = verdict_name == "PASS"
            return {
                "passed": passed,
                "source": "e2e_verdict_parser",
                "response": result_text[:2000],
                "verdict": verdict_name,
            }

    return None


def handle_none_result(subordinate: "Agent") -> str:
    """Generate a result string when subordinate returns None."""
    try:
        from python.helpers.death_summary import generate_death_summary
        progress = generate_death_summary(subordinate)
        return (
            f"Subordinate agent returned no result (returned None).\n\n"
            f"## Progress Summary\n{progress}\n\n"
            f"## Handoff Instructions\n"
            f"Pass this progress summary to any replacement subordinate so it can "
            f"continue from where this agent left off."
        )
    except Exception as e:
        logger.warning(f"Failed to generate death summary for None result: {e}")
        return "Subordinate agent failed to produce a response (returned None)."


def _classify_failure_type(result: str) -> tuple:
    """Classify the failure type from limit tags in the result string.

    RCA-316c C-4: Each tag type has a distinct cause. Using the wrong
    diagnostic message (e.g., "stuck in a loop" for budget exhaustion)
    misleads the parent orchestrator's re-delegation strategy.

    Returns:
        (tag_name, termination_verb, cause_description) tuple.
    """
    # Order matters — check most specific first
    # U-13 Fix: Use centralized sentinel_registry instead of hardcoded list
    from python.helpers.sentinel_registry import get_tag_diagnostics
    _TAG_DIAGNOSTICS = get_tag_diagnostics()

    for tag, (verb, cause) in _TAG_DIAGNOSTICS.items():
        if tag in result:
            return (tag, verb, cause)

    # Fallback for unrecognized tags
    return (
        "UNKNOWN",
        "was forcibly terminated",
        "was forcibly terminated for an unspecified reason",
    )


def handle_limit_tags(result: str, subordinate: "Agent") -> str:
    """Append structured error relay when subordinate hits limits/hard-stop.

    RCA-316c C-4: Produces failure-type-specific diagnostics so the parent
    orchestrator can make informed re-delegation decisions. An agent that
    ran out of budget (ITERATION_LIMIT) is treated differently from one
    that was stuck in a loop (HARD_STOP).
    """
    from python.tools.call_subordinate import _extract_last_error

    logger.warning(f"Subordinate hit limit/hard-stop: {result[:200]}")

    # ── Classify the failure type ──
    tag_name, termination_verb, cause_description = _classify_failure_type(result)
    logger.info(f"Failure classification: {tag_name} → {termination_verb}")

    progress_section = ""
    try:
        from python.helpers.death_summary import generate_death_summary
        progress = generate_death_summary(subordinate)
        if progress and progress.strip():
            progress_section = f"\n### What Was Accomplished\n{progress}\n"
    except Exception as e:
        logger.warning(f"Failed to generate death summary for failure relay: {e}")

    last_error = _extract_last_error(subordinate)
    last_error_section = ""
    if last_error:
        last_error_section = f"\n### Last Error\n```\n{last_error}\n```\n"

    error_relay = (
        f"\n\n## ⚠️ Subordinate Failure Relay\n"
        f"The subordinate agent **{subordinate.agent_name}** {termination_verb}. "
        f"This means the agent {cause_description}.\n\n"
        f"{progress_section}\n"
        f"{last_error_section}\n"
        f"**DO NOT re-delegate the exact same task without modifications.** Instead:\n"
        f"1. Read the progress summary above to identify what was COMPLETED vs what REMAINS\n"
        f"2. Write a NEW, DIFFERENT delegation message targeting ONLY the remaining work\n"
        f"3. Include specific error context from the failure to help the next agent avoid the same loop\n"
        f"4. If the task is truly blocked, report the blocker to the user\n"
    )
    return result + error_relay


def propagate_subordinate_data(agent: "Agent", subordinate: "Agent") -> None:
    """Propagate all relevant data fields from subordinate to parent agent.

    Includes: tool_data_anchors, _browser_agent_calls, _browser_screenshots,
    _quality_evaluation, _dev_server_started, _services_mgt_dev_server,
    _dev_server_port, _code_execution_commands, _active_project_dir,
    _verification_delegated, _lit_tests_executed.
    """
    # tool_data_anchors — only propagate if subordinate FAILED fidelity
    sub_anchors = subordinate.data.get("tool_data_anchors", [])
    sub_fidelity_warned = subordinate.data.get("_fidelity_warned_this_turn", False)
    if sub_anchors and sub_fidelity_warned:
        if "tool_data_anchors" not in agent.data:
            agent.data["tool_data_anchors"] = []
        agent.data["tool_data_anchors"].extend(sub_anchors)
        agent.data["tool_data_anchors"] = agent.data["tool_data_anchors"][-20:]
        logger.info(
            f"Propagated {len(sub_anchors)} tool_data_anchors from "
            f"subordinate to parent (sub FAILED fidelity; total: {len(agent.data['tool_data_anchors'])})"
        )
    elif sub_anchors:
        logger.info(
            f"NOT propagating {len(sub_anchors)} tool_data_anchors from "
            f"subordinate (sub PASSED fidelity — data verified at edge)"
        )

    # _browser_agent_calls
    sub_browser_calls = subordinate.data.get("_browser_agent_calls", 0)
    if sub_browser_calls > 0:
        parent_calls = agent.data.get("_browser_agent_calls", 0)
        agent.data["_browser_agent_calls"] = parent_calls + sub_browser_calls
        logger.info(
            f"Propagated {sub_browser_calls} _browser_agent_calls from "
            f"subordinate {subordinate.agent_name} to parent "
            f"(total: {agent.data['_browser_agent_calls']})"
        )

    # _browser_screenshots
    sub_screenshots = subordinate.data.get("_browser_screenshots", [])
    if sub_screenshots:
        if "_browser_screenshots" not in agent.data:
            agent.data["_browser_screenshots"] = []
        agent.data["_browser_screenshots"].extend(sub_screenshots)
        logger.info(
            f"Propagated {len(sub_screenshots)} _browser_screenshots from "
            f"subordinate {subordinate.agent_name} to parent "
            f"(total: {len(agent.data['_browser_screenshots'])})"
        )

    # _quality_evaluation
    sub_quality = subordinate.data.get("_quality_evaluation")
    if sub_quality:
        agent.data["_quality_evaluation"] = sub_quality
        logger.info(
            f"Propagated _quality_evaluation from subordinate "
            f"{subordinate.agent_name}: "
            f"passed={sub_quality.get('passed', 'unknown')}"
        )

    # _dev_server_started
    if subordinate.data.get("_dev_server_started", False):
        agent.data["_dev_server_started"] = True
        logger.info(
            f"Propagated _dev_server_started=True from subordinate "
            f"{subordinate.agent_name} to parent"
        )

    # RCA-ITR32-C: _services_mgt_dev_server — specific flag distinguishing
    # services_mgt tool usage from raw code_execution_tool. This flag was
    # previously a dead signal (written but never propagated or consumed).
    if subordinate.data.get("_services_mgt_dev_server", False):
        agent.data["_services_mgt_dev_server"] = True
        logger.info(
            f"Propagated _services_mgt_dev_server=True from subordinate "
            f"{subordinate.agent_name} to parent"
        )

    # _dev_server_port
    sub_port = subordinate.data.get("_dev_server_port", "")
    if sub_port:
        agent.data["_dev_server_port"] = sub_port
        logger.info(
            f"Propagated _dev_server_port={sub_port} from subordinate "
            f"{subordinate.agent_name} to parent"
        )

    # _code_execution_commands
    sub_cmds = subordinate.data.get("_code_execution_commands", [])
    if sub_cmds:
        parent_cmds = agent.data.get("_code_execution_commands", [])
        agent.data["_code_execution_commands"] = parent_cmds + sub_cmds
        logger.info(
            f"Propagated {len(sub_cmds)} _code_execution_commands from "
            f"subordinate {subordinate.agent_name} to parent "
            f"(total: {len(agent.data['_code_execution_commands'])})"
        )

    # _active_project_dir
    sub_project = subordinate.data.get("_active_project_dir", "")
    if sub_project and not agent.data.get("_active_project_dir"):
        agent.data["_active_project_dir"] = sub_project
        logger.info(
            f"Propagated _active_project_dir='{sub_project}' from "
            f"subordinate {subordinate.agent_name} to parent"
        )
    elif sub_project and agent.data.get("_active_project_dir") != sub_project:
        # System 7 (ITR-44): Project changed via subordinate — clear stale state
        from python.helpers.agent_data_keys import invalidate_project_scoped_keys
        cleared = invalidate_project_scoped_keys(agent.data, sub_project)
        if cleared:
            logger.info(
                f"System 7: Cleared {len(cleared)} project-scoped keys on "
                f"project change via subordinate {subordinate.agent_name}"
            )

    # _verification_delegated
    if subordinate.data.get("_verification_delegated", False):
        agent.data["_verification_delegated"] = True
        logger.info(
            f"Propagated _verification_delegated=True from subordinate "
            f"{subordinate.agent_name} to parent"
        )

    # _lit_tests_executed
    if subordinate.data.get("_lit_tests_executed", False):
        agent.data["_lit_tests_executed"] = True
        logger.info(
            f"Propagated _lit_tests_executed=True from subordinate "
            f"{subordinate.agent_name} to parent"
        )

    # SS-2: MCP HEALTH REGISTRY MERGE (ITR-344)
    # Merge subordinate's MCP failure registry back into parent.
    # Takes MAX of failure counts per tool, so parent inherits any new
    # bad-tool knowledge discovered during subordinate execution.
    sub_mcp_reg = subordinate.data.get("_mcp_health_registry", {})
    if sub_mcp_reg:
        if "_mcp_health_registry" not in agent.data:
            agent.data["_mcp_health_registry"] = {}
        parent_reg = agent.data["_mcp_health_registry"]
        for tool_key, sub_entry in sub_mcp_reg.items():
            parent_entry = parent_reg.get(tool_key, {"failures": 0, "last_error": ""})
            # Take the higher failure count (child may have discovered more failures)
            if sub_entry.get("failures", 0) > parent_entry.get("failures", 0):
                parent_reg[tool_key] = {
                    "failures": sub_entry["failures"],
                    "last_error": sub_entry.get("last_error", parent_entry.get("last_error", "")),
                }
            elif tool_key not in parent_reg:
                parent_reg[tool_key] = sub_entry
        logger.info(
            f"Merged _mcp_health_registry from subordinate "
            f"{subordinate.agent_name} → parent ({len(sub_mcp_reg)} tools)"
        )

    # SS-1/SS-4: TOOL FAILURE STATE MERGE (ITR-344)
    # Merge subordinate's tool failure tracking state back into parent.
    # Uses UNION for blocked tools (any tool blocked by child is also blocked
    # for parent), and MAX for failure counts (child may have accumulated more).

    # _tracker_blocked_tools — UNION merge
    sub_blocked = subordinate.data.get("_tracker_blocked_tools", set())
    if sub_blocked:
        if "_tracker_blocked_tools" not in agent.data:
            agent.data["_tracker_blocked_tools"] = set()
        agent.data["_tracker_blocked_tools"] = agent.data["_tracker_blocked_tools"] | sub_blocked
        logger.info(
            f"Merged _tracker_blocked_tools from subordinate "
            f"{subordinate.agent_name} → parent "
            f"({len(sub_blocked)} child tools, "
            f"{len(agent.data['_tracker_blocked_tools'])} total)"
        )

    # _tool_failure_counts — MAX merge
    sub_counts = subordinate.data.get("_tool_failure_counts", {})
    if sub_counts:
        if "_tool_failure_counts" not in agent.data:
            agent.data["_tool_failure_counts"] = {}
        parent_counts = agent.data["_tool_failure_counts"]
        for tool_key, sub_count in sub_counts.items():
            parent_count = parent_counts.get(tool_key, 0)
            parent_counts[tool_key] = max(parent_count, sub_count)
        logger.info(
            f"Merged _tool_failure_counts from subordinate "
            f"{subordinate.agent_name} → parent ({len(sub_counts)} tools)"
        )

    # _session_hint_counts — MAX merge
    sub_hints = subordinate.data.get("_session_hint_counts", {})
    if sub_hints:
        if "_session_hint_counts" not in agent.data:
            agent.data["_session_hint_counts"] = {}
        parent_hints = agent.data["_session_hint_counts"]
        for tool_key, sub_count in sub_hints.items():
            parent_count = parent_hints.get(tool_key, 0)
            parent_hints[tool_key] = max(parent_count, sub_count)
        logger.info(
            f"Merged _session_hint_counts from subordinate "
            f"{subordinate.agent_name} → parent ({len(sub_hints)} entries)"
        )

    # ── SS-7: Delegation Health Ledger — track subordinate outcomes ──
    # Records every delegation outcome (success/failure) in the parent's
    # _delegation_health_ledger. Failed delegations include error fingerprints
    # computed via F-11's _compute_error_fingerprint() so the L1 detector
    # (DETECTOR 10: cross_delegation_spiral) can detect when the same root
    # cause repeats across multiple subordinates.
    sub_status = ""
    sub_failure = ""
    if hasattr(subordinate, "context") and subordinate.context:
        sub_status = getattr(subordinate.context, "_execution_status", "") or ""
        sub_failure = getattr(subordinate.context, "_failure_reason", "") or ""

    health_entry = {
        "profile": getattr(subordinate.config, "profile", "unknown") if hasattr(subordinate, "config") else "unknown",
        "turn": getattr(agent, "_absolute_turns", 0),
        "status": "FAILED" if sub_status == "FAILED" else "OK",
        "agent_name": getattr(subordinate, "agent_name", "unknown"),
    }

    if sub_status == "FAILED" and sub_failure:
        from python.extensions.message_loop_start._10_structural_guards import (
            _compute_error_fingerprint,
        )
        health_entry["failure_fingerprint"] = _compute_error_fingerprint(sub_failure)
        health_entry["failure_reason"] = sub_failure[:200]

        # Track consecutive failures
        consec = agent.data.get("_consecutive_failed_delegations", 0) + 1
        agent.data["_consecutive_failed_delegations"] = consec
        logger.info(
            f"SS-7: Subordinate {subordinate.agent_name} FAILED "
            f"(consecutive={consec}): {sub_failure[:100]}"
        )
    else:
        # Reset consecutive counter on success
        agent.data["_consecutive_failed_delegations"] = 0

    health_ledger = agent.data.get("_delegation_health_ledger", [])
    health_ledger.append(health_entry)
    if len(health_ledger) > 30:
        health_ledger = health_ledger[-30:]
    agent.data["_delegation_health_ledger"] = health_ledger

    # ── RCA-MSR-BuildLoop: Propagate build failure counts ──
    # When a subordinate finishes, propagate its BuildLoopDetector state
    # back to the orchestrator. This breaks the "island state" pattern
    # where each new subordinate starts with failure_count=0, making
    # Tier 2 (7) and Tier 3 (12) unreachable across delegation retries.
    try:
        from python.helpers.build_loop_detector import get_propagatable_build_state
        build_state = get_propagatable_build_state(subordinate)
        if build_state:
            # Store on orchestrator for next subordinate to inherit
            existing = agent.data.get("_build_failure_propagated", {})
            # Merge: keep the higher count for each project
            for proj, count in build_state.items():
                existing[proj] = max(existing.get(proj, 0), count)
            agent.data["_build_failure_propagated"] = existing
            logger.info(
                f"RCA-MSR-BuildLoop: Propagated build failure state from "
                f"{subordinate.agent_name}: {build_state}"
            )
    except Exception as e:
        logger.warning(f"Build state propagation failed (non-fatal): {e}")


def build_delegation_result_envelope(
    result: str | None,
    subordinate: "Agent",
    kwargs: dict,
) -> "DelegationResult":
    """Build a structured DelegationResult envelope from raw subordinate output."""
    from python.helpers.delegation_result import DelegationResult

    delegation_status = "success"
    delegation_errors: list[str] = []

    if result is None or (isinstance(result, str) and not result.strip()):
        delegation_status = "failed"
        delegation_errors.append("Subordinate returned empty/None response")
    elif isinstance(result, str):
        # Check for iteration/chain limit tags
        # U-13 Fix: Use centralized sentinel_registry
        from python.helpers.sentinel_registry import get_limit_tags
        limit_tags = get_limit_tags()
        if any(tag in result for tag in limit_tags):
            delegation_status = "partial"
            delegation_errors.append("Subordinate hit iteration/chain limit")

        # ESCALATION DETECTION (RCA-284b)
        if "[ESCALATE]" in result:
            delegation_status = "escalated"
            delegation_errors.append("Subordinate escalated — requires debug agent RCA")
            try:
                from python.helpers.escalation_protocol import (
                    parse_escalation_report,
                    record_escalation,
                    build_debug_routing_guidance,
                )
                from python.helpers.requirements_ledger import mark_delegation_escalated

                esc_report = parse_escalation_report(result)
                if esc_report:
                    esc_profile = kwargs.get("profile", "unknown")
                    record_escalation(
                        subordinate.get_data("__superior_data_ref") if hasattr(subordinate, "get_data") else {},
                        profile=esc_profile,
                        report=esc_report,
                    )
            except Exception as esc_err:
                logger.warning(f"Escalation detection failed (non-fatal): {esc_err}")

        # Quality evaluation failure
        sub_quality = subordinate.data.get("_quality_evaluation")
        if sub_quality and not sub_quality.get("passed", True):
            delegation_status = "partial"
            quality_issues = sub_quality.get("issues", [])
            if isinstance(quality_issues, list):
                delegation_errors.extend(quality_issues[:5])

    # Collect artifacts
    delegation_artifacts: list[str] = []
    sub_screenshots = subordinate.data.get("_browser_screenshots", [])
    if sub_screenshots:
        delegation_artifacts.extend(sub_screenshots[:10])

    # ── F-4A + F-ERR-3: Enrich with UEM error context & retry classification ──
    delegation_next_steps: list[str] = []
    try:
        from python.helpers.universal_error_manager import UniversalErrorManager
        from python.helpers.retry_strategy import classify_error

        uem = UniversalErrorManager(subordinate)
        error_context = uem.build_delegation_error_context()

        # F-4A: Populate next_steps from UEM analysis
        if error_context.get("next_steps"):
            delegation_next_steps.extend(error_context["next_steps"])

        # F-ERR-3: Add categorized error summaries from UEM
        for err_info in error_context.get("recent_errors", []):
            category = err_info.get("category", "unknown")
            summary = err_info.get("summary", "")
            domain = err_info.get("domain", "")
            if summary:
                categorized_entry = f"[{category}]"
                if domain and domain != "unknown":
                    categorized_entry += f" ({domain})"
                categorized_entry += f" {summary}"
                if categorized_entry not in delegation_errors:
                    delegation_errors.append(categorized_entry)

        # F-ERR-3: Wire retry_strategy classification for dominant error
        if delegation_status in ("failed", "partial") and error_context.get("recent_errors"):
            dominant = error_context.get("dominant_category", "unknown")
            first_error = error_context["recent_errors"][0].get("summary", "")
            if first_error:
                retry_category = classify_error(first_error)
                if retry_category.startswith("permanent_"):
                    delegation_next_steps.append(
                        f"Error classified as {retry_category} — do NOT retry same approach"
                    )
    except Exception as uem_err:
        logger.debug(f"UEM error context enrichment failed (non-fatal): {uem_err}")

    return DelegationResult(
        status=delegation_status,
        result=result or "",
        profile=kwargs.get("profile", getattr(subordinate.config, "name", "")),
        artifacts=delegation_artifacts,
        errors=delegation_errors,
        iterations=getattr(subordinate.context, '_chain_monologue_iterations', 0),
        next_steps=delegation_next_steps,
        task_hash=kwargs.get("_task_hash", ""),
        sequence_id=kwargs.get("_task_seq_id", 0),
        task_guid=kwargs.get("task_guid", ""),
    )


def apply_quality_gate(agent: "Agent", delegation_result: "DelegationResult") -> None:
    """Validate that 'success' result has sufficient substance."""
    if delegation_result.status != "success":
        return
    try:
        from python.helpers.delegation_output_quality import check_delegation_output_quality
        quality = check_delegation_output_quality(
            result=delegation_result.result,
            profile=delegation_result.profile,
            iterations=delegation_result.iterations,
        )
        if not quality["passed"]:
            logger.warning(
                f"QUALITY GATE: Downgrading delegation from 'success' "
                f"to 'partial' — {quality['reason']}"
            )
            delegation_result.status = "partial"
            delegation_result_dict = agent.data["_last_delegation_result"]
            delegation_result_dict["status"] = "partial"
            delegation_result_dict["quality_gate_reason"] = quality["reason"]
            delegation_result_dict["quality_gate_confidence"] = quality["confidence"]
            agent.data["_last_delegation_result"] = delegation_result_dict
        else:
            logger.debug(
                f"QUALITY GATE: Passed (confidence={quality['confidence']:.2f})"
            )
    except Exception as qg_err:
        logger.debug(f"Quality gate check failed (non-fatal): {qg_err}")


def classify_failure(agent: "Agent", delegation_result: "DelegationResult", result: str) -> None:
    """Tag failed/partial results with a structured failure category (S6)."""
    if delegation_result.status not in ("failed", "partial"):
        return
    try:
        from python.helpers.failure_classifier import classify_subordinate_failure
        classification = classify_subordinate_failure(
            delegation_result.errors,
            result or "",
        )
        delegation_result_dict = agent.data["_last_delegation_result"]
        delegation_result_dict["failure_category"] = classification["category"]
        delegation_result_dict["recovery_hint"] = classification["recovery"]
        agent.data["_last_delegation_result"] = delegation_result_dict
        logger.info(
            f"FAILURE CLASSIFIER: category={classification['category']}, "
            f"confidence={classification['confidence']}"
        )
    except Exception as fc_err:
        logger.debug(f"Failure classification failed (non-fatal): {fc_err}")


def detect_and_inject_wrong_profile(
    agent: "Agent",
    subordinate: "Agent",
    delegation_result: "DelegationResult",
    result: str,
) -> str:
    """Detect wrong-profile signals and inject remediation (S7).

    Returns potentially modified result string.
    """
    if delegation_result.status not in ("failed", "partial"):
        return result
    try:
        from python.helpers.wrong_profile_detector import detect_wrong_profile
        sub_messages = []
        if hasattr(subordinate, 'history') and subordinate.history:
            for msg in subordinate.history:
                content = getattr(msg, 'content', '') or getattr(msg, 'message', '') or ''
                role = getattr(msg, 'role', '')
                if role == 'assistant' and content:
                    sub_messages.append(content)
        elif isinstance(result, str) and result:
            sub_messages = [result]

        if sub_messages:
            wp_result = detect_wrong_profile(sub_messages)
            if wp_result.get("is_wrong_profile"):
                result = wp_result["remediation"] + "\n\n---\n\n" + (result or "")
                delegation_result_dict = agent.data["_last_delegation_result"]
                delegation_result_dict["wrong_profile"] = True
                delegation_result_dict["wrong_profile_signals"] = wp_result["signal_count"]
                agent.data["_last_delegation_result"] = delegation_result_dict
                logger.warning(
                    f"WRONG PROFILE: {wp_result['signal_count']} mismatch signals "
                    f"detected for {subordinate.agent_name}"
                )
    except Exception as wp_err:
        logger.debug(f"Wrong-profile detection failed (non-fatal): {wp_err}")
    return result


def record_error_relay(
    agent: "Agent",
    delegation_result: "DelegationResult",
    result: str,
) -> None:
    """Record error signatures for next delegation's error injection."""
    if delegation_result.status not in ("failed", "partial"):
        return
    if not (delegation_result.errors or result):
        return
    try:
        from python.helpers.subordinate_error_relay import (
            extract_error_signatures, record_subordinate_failure
        )
        all_sigs = list(delegation_result.errors)
        if result:
            raw_sigs = extract_error_signatures(result)
            for sig in raw_sigs:
                if sig not in all_sigs:
                    all_sigs.append(sig)
        record_subordinate_failure(
            agent.data,
            profile=delegation_result.profile,
            errors=all_sigs,
            result_preview=result[:1500] if result else "",
        )
        logger.info(
            f"ERROR RELAY: Recorded {len(all_sigs)} error signature(s) "
            f"from {delegation_result.profile} (status={delegation_result.status})"
        )
    except Exception as e:
        logger.warning(f"Error relay recording failed (non-fatal): {e}")


# ── Facade functions used by call_subordinate.py thin wrappers ──


def build_delegation_result(
    result: str | None,
    subordinate: "Agent",
    kwargs: dict,
) -> "DelegationResult":
    """Build a structured DelegationResult envelope.

    Facade over build_delegation_result_envelope with escalation handling.
    """
    from python.helpers.delegation_result import DelegationResult

    delegation_status = "success"
    delegation_errors: list[str] = []

    if result is None or (isinstance(result, str) and not result.strip()):
        delegation_status = "failed"
        delegation_errors.append("Subordinate returned empty/None response")
    elif isinstance(result, str):
        # Check for iteration/chain limit tags and response rejection sentinels
        # RCA-354 I-2: [RESPONSE_REJECTED] is added by coerce_subordinate_response()
        # when the subordinate's response was internally rejected (near-dup, fidelity gate).
        # The coerced text is the rejection message, not the agent's work output.
        # U-13 Fix: Use centralized sentinel_registry
        from python.helpers.sentinel_registry import get_limit_tags
        limit_tags = get_limit_tags()
        if any(tag in result for tag in limit_tags):
            delegation_status = "partial"
            delegation_errors.append("Subordinate hit iteration/chain limit or response was internally rejected")

        # ESCALATION DETECTION (RCA-284b)
        if "[ESCALATE]" in result:
            delegation_status = "escalated"
            delegation_errors.append("Subordinate escalated — requires debug agent RCA")
            try:
                from python.helpers.escalation_protocol import (
                    parse_escalation_report,
                    record_escalation,
                    build_debug_routing_guidance,
                )
                from python.helpers.requirements_ledger import mark_delegation_escalated

                esc_report = parse_escalation_report(result)
                if esc_report:
                    esc_profile = kwargs.get("profile", "unknown")
                    # Need parent agent.data for recording — use subordinate's superior ref
                    # Note: the parent stores _last_delegation_result after this returns
            except Exception as esc_err:
                logger.warning(f"Escalation detection failed (non-fatal): {esc_err}")

        # Quality evaluation failure
        sub_quality = subordinate.data.get("_quality_evaluation")
        if sub_quality and not sub_quality.get("passed", True):
            delegation_status = "partial"
            quality_issues = sub_quality.get("issues", [])
            if isinstance(quality_issues, list):
                delegation_errors.extend(quality_issues[:5])

    # Collect artifacts
    delegation_artifacts: list[str] = []
    sub_screenshots = subordinate.data.get("_browser_screenshots", [])
    if sub_screenshots:
        delegation_artifacts.extend(sub_screenshots[:10])

    # ── F-4A + F-ERR-3: Enrich with UEM error context & retry classification ──
    delegation_next_steps: list[str] = []
    try:
        from python.helpers.universal_error_manager import UniversalErrorManager
        from python.helpers.retry_strategy import classify_error

        uem = UniversalErrorManager(subordinate)
        error_context = uem.build_delegation_error_context()

        # F-4A: Populate next_steps from UEM analysis
        if error_context.get("next_steps"):
            delegation_next_steps.extend(error_context["next_steps"])

        # F-ERR-3: Add categorized error summaries from UEM
        # ╔═══════════════════════════════════════════════════════════════╗
        # ║ ITR-48 ROOT CAUSE FIX: Only include UEM errors when the      ║
        # ║ delegation FAILED or was PARTIAL. When status=success, the   ║
        # ║ subordinate FIXED these errors during execution. Reporting   ║
        # ║ resolved errors as "Errors: 4" causes the orchestrator LLM  ║
        # ║ to inject Recovery tasks for already-resolved problems,      ║
        # ║ wasting 30+ minutes re-doing completed work.                 ║
        # ║                                                              ║
        # ║ 5-WHY: LLM saw "Errors: 4" + "lucide-react not found" in    ║
        # ║ a SUCCESS result → hallucinated Recovery → re-delegated      ║
        # ║ Phase 3.3 from scratch → 40 minutes wasted.                  ║
        # ╚═══════════════════════════════════════════════════════════════╝
        if delegation_status != "success":
            for err_info in error_context.get("recent_errors", []):
                category = err_info.get("category", "unknown")
                summary = err_info.get("summary", "")
                domain = err_info.get("domain", "")
                if summary:
                    categorized_entry = f"[{category}]"
                    if domain and domain != "unknown":
                        categorized_entry += f" ({domain})"
                    categorized_entry += f" {summary}"
                    if categorized_entry not in delegation_errors:
                        delegation_errors.append(categorized_entry)

        # F-ERR-3: Wire retry_strategy classification for dominant error
        if delegation_status in ("failed", "partial") and error_context.get("recent_errors"):
            dominant = error_context.get("dominant_category", "unknown")
            first_error = error_context["recent_errors"][0].get("summary", "")
            if first_error:
                retry_category = classify_error(first_error)
                if retry_category.startswith("permanent_"):
                    delegation_next_steps.append(
                        f"Error classified as {retry_category} — do NOT retry same approach"
                    )
    except Exception as uem_err:
        logger.debug(f"UEM error context enrichment failed (non-fatal): {uem_err}")

    return DelegationResult(
        status=delegation_status,
        result=result or "",
        profile=kwargs.get("profile", getattr(subordinate.config, "name", "")),
        artifacts=delegation_artifacts,
        errors=delegation_errors,
        iterations=getattr(subordinate.context, '_chain_monologue_iterations', 0),
        next_steps=delegation_next_steps,
        task_hash=kwargs.get("_task_hash", ""),
        sequence_id=kwargs.get("_task_seq_id", 0),
        task_guid=kwargs.get("task_guid", ""),
    )


def run_post_delegation_gates(
    delegation_result: "DelegationResult",
    result: str | None,
    subordinate: "Agent",
    agent: "Agent",
) -> str:
    """Run quality gate, failure classifier, and wrong-profile detector.

    Returns potentially modified result string.

    L3 (RCA-346 F-4): The detect_and_inject_wrong_profile() call below also
    serves as a post-execution gate for debug profile misrouting. If a debug
    agent fails because it tried to write files (PROFILE_ENFORCEMENT block),
    the wrong_profile_detector will detect the mismatch signals and inject
    remediation guidance. The L2 pre-execution guard in call_subordinate.py
    (should_correct_debug_to_code) prevents most cases from reaching L3.
    """
    apply_quality_gate(agent, delegation_result)
    classify_failure(agent, delegation_result, result or "")
    result = detect_and_inject_wrong_profile(
        agent, subordinate, delegation_result, result or ""
    )
    return result


async def record_delegation_failure(
    delegation_result: "DelegationResult",
    result: str | None,
    agent: "Agent",
    kwargs: dict,
    message: str,
) -> None:
    """Record error relay + n-attempt failure tracker for failed delegations."""
    import asyncio

    # Cross-subordinate error relay
    record_error_relay(agent, delegation_result, result or "")

    # N-attempt failure tracker
    try:
        from python.extensions.tool_execute_before._27_delegation_loop_hook import _global_detector
        agent_id = getattr(agent, "agent_name", "") or str(id(agent))
        original_task = kwargs.get("_original_task_message", message)
        redirect_diag = _global_detector.record_failure(
            agent_id, original_task, errors=delegation_result.errors
        )
        if redirect_diag:
            task_hash = _global_detector.get_task_hash(original_task)
            failure_count = _global_detector.get_failure_count(agent_id, original_task)
            all_errors = []
            for detail in _global_detector.get_failure_details(agent_id, original_task):
                all_errors.extend(detail.get("errors", []))

            try:
                from python.helpers.event_bus import emit_repeated_task_failure
                context_id = getattr(agent.context, "id", "unknown") if agent.context else "unknown"
                iteration = getattr(agent.loop_data, "iteration", 0) if hasattr(agent, "loop_data") and agent.loop_data else 0
                asyncio.ensure_future(emit_repeated_task_failure(
                    agent_id=agent_id,
                    context_id=context_id,
                    task_hash=task_hash,
                    failure_count=failure_count,
                    error_summary=all_errors,
                    task_preview=original_task[:200],
                    iteration=iteration,
                ))
                logger.warning(
                    f"N-ATTEMPT TRACKER: Task hash={task_hash} failed {failure_count}x — "
                    f"REPEATED_TASK_FAILURE signal emitted for supervisor redirect"
                )
            except Exception as sig_err:
                logger.warning(f"Failed to emit REPEATED_TASK_FAILURE signal: {sig_err}")

            await agent.hist_add_warning(redirect_diag)
    except Exception as e:
        logger.warning(f"N-attempt failure tracking failed (non-fatal): {e}")


# ── FIX-4: Cumulative Delegation Result Ledger ──────────────────────────────
# Root cause: _last_delegation_result only stores the LAST subordinate result,
# overwriting all previous results. The fidelity gate and completion gate need
# a cumulative ledger to cross-reference orchestrator claims against ALL
# subordinate outputs (not just the last one).
#
# Design:
#   1. append_to_result_ledger() — FIFO list in agent.data, capped at 50
#   2. build_delegation_result_with_ledger() — facade that builds DR + appends
#   3. build_orchestrator_anchors_from_ledger() — creates synthetic anchors
#      for the fidelity gate when no MCP anchors exist (orchestrator case)

LEDGER_MAX_ENTRIES = 50
PREVIEW_MAX_CHARS = 500

# Stopwords excluded from anchor key_values (common words that would
# cause false positives in fidelity matching)
_ANCHOR_STOPWORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "have", "been",
    "was", "were", "are", "has", "had", "but", "not", "all", "can", "will",
    "would", "should", "could", "into", "also", "its", "you", "your",
    "our", "any", "may", "each", "which", "when", "then", "than", "them",
    "they", "their", "there", "here", "some", "more", "most", "other",
    "been", "being", "does", "done", "make", "made", "only", "very",
    "just", "both", "well", "such", "over", "under", "after", "before",
    "about", "between", "through", "during", "while", "upon", "status",
    "success", "partial", "failed", "agent", "result", "built", "created",
    "implemented", "completed", "added", "updated", "fixed", "using",
})


def append_to_result_ledger(agent_data: dict, entry: dict) -> None:
    """Append a delegation result entry to the cumulative ledger.

    FIX-4: Creates _delegation_result_ledger if it doesn't exist.
    Caps at LEDGER_MAX_ENTRIES entries (FIFO — oldest evicted first).
    Truncates result_preview to PREVIEW_MAX_CHARS.

    Args:
        agent_data: The agent's data dict (self.agent.data).
        entry: Dict with keys: status, profile, result_preview, result_length.
    """
    # Validate/reinitialize if corrupted
    ledger = agent_data.get("_delegation_result_ledger")
    if not isinstance(ledger, list):
        ledger = []
        agent_data["_delegation_result_ledger"] = ledger

    # Truncate preview
    preview = entry.get("result_preview", "")
    if len(preview) > PREVIEW_MAX_CHARS:
        preview = preview[:PREVIEW_MAX_CHARS]
    entry_copy = dict(entry)
    entry_copy["result_preview"] = preview

    ledger.append(entry_copy)

    # FIFO eviction
    if len(ledger) > LEDGER_MAX_ENTRIES:
        agent_data["_delegation_result_ledger"] = ledger[-LEDGER_MAX_ENTRIES:]


def build_delegation_result_with_ledger(
    result: str | None,
    subordinate: "Agent",
    kwargs: dict,
    agent_data: dict,
) -> "DelegationResult":
    """Build a DelegationResult AND append to the cumulative ledger.

    FIX-4: This is the facade that call_subordinate.py should call instead
    of build_delegation_result() + manual _last_delegation_result assignment.
    It builds the DR, writes _last_delegation_result, AND appends to the
    cumulative _delegation_result_ledger.

    Args:
        result: Raw result string from subordinate.
        subordinate: The subordinate Agent instance.
        kwargs: The kwargs dict passed to call_subordinate.
        agent_data: The parent agent's data dict (self.agent.data).

    Returns:
        The built DelegationResult.
    """
    dr = build_delegation_result(result, subordinate, kwargs)

    # Build preview from the actual result text (first 500 chars)
    preview = ""
    if result:
        preview = result[:PREVIEW_MAX_CHARS]

    ledger_entry = {
        "status": dr.status,
        "profile": dr.profile,
        "result_preview": preview,
        "result_length": len(result) if result else 0,
        "task_hash": dr.task_hash,
        "task_guid": dr.task_guid,
        "errors": dr.errors[:3] if dr.errors else [],
    }
    append_to_result_ledger(agent_data, ledger_entry)

    return dr


def build_orchestrator_anchors_from_ledger(agent_data: dict) -> list:
    """Build synthetic fidelity anchors from the delegation result ledger.

    FIX-4: When an orchestrator has no MCP tool_data_anchors (because
    orchestrators delegate to subs, not call MCP tools directly), the
    fidelity gate silently skips. This function creates synthetic anchors
    from the cumulative delegation_result_ledger so the fidelity gate
    can cross-reference the orchestrator's response against what its
    subordinates actually reported.

    Each successful delegation becomes one anchor with key_values extracted
    from the result_preview (significant words >= 4 chars, no stopwords).

    Args:
        agent_data: The agent's data dict.

    Returns:
        List of anchor dicts compatible with tool_data_anchors format:
        [{tool_name, key_values, hash}]
    """
    ledger = agent_data.get("_delegation_result_ledger")
    if not isinstance(ledger, list) or not ledger:
        return []

    anchors = []
    for entry in ledger:
        # Only successful delegations produce reliable anchors
        status = entry.get("status", "")
        if status in ("failed", "escalated"):
            continue

        preview = entry.get("result_preview", "")
        if not preview or len(preview) < 20:
            continue

        profile = entry.get("profile", "unknown")

        # Extract significant words as key_values
        # Split on whitespace and punctuation, filter stopwords and short words
        import re as _re
        words = _re.findall(r"[a-zA-Z0-9_/.-]{4,}", preview)
        key_values = []
        seen = set()
        for word in words:
            word_lower = word.lower()
            if word_lower in _ANCHOR_STOPWORDS:
                continue
            if word_lower in seen:
                continue
            seen.add(word_lower)
            key_values.append(word)
            if len(key_values) >= 10:  # Cap at 10 key values per anchor
                break

        if not key_values:
            continue

        anchors.append({
            "tool_name": f"delegation:{profile}",
            "key_values": key_values,
            "hash": "",  # No hash for synthetic anchors
        })

    return anchors


# ─── FIX 2.3b: Delegation Output Quarantine ─────────────────────────────
# When the cross_delegation_spiral detector (DETECTOR 10) fires, failed
# delegation outputs should NOT be merged into the project. Instead, we
# replace the result text with a quarantine message so the orchestrator
# LLM cannot use the poisoned output for downstream work.
# See: _10_structural_guards.py:672-740 for the detector.

_QUARANTINE_LEDGER_MAX = 10


def quarantine_delegation_result(
    agent_data: dict,
    delegation_result: "DelegationResult",
    result: str,
    reason: str,
) -> str:
    """Quarantine a failed delegation's output instead of merging it.

    When the cross_delegation_spiral detector has fired, the delegation's
    output should NOT be merged into the project. Instead, store it in a
    quarantine ledger and return a sanitized summary.

    Args:
        agent_data: The parent agent's data dict (self.agent.data).
        delegation_result: The structured DelegationResult envelope.
        result: The raw result text from the subordinate.
        reason: Human-readable reason for quarantining.

    Returns:
        Sanitized result string that replaces the original.
    """
    quarantine = agent_data.get("_quarantined_delegations", [])
    quarantine.append({
        "profile": delegation_result.profile,
        "status": delegation_result.status,
        "task_hash": delegation_result.task_hash,
        "reason": reason,
        "result_preview": (result or "")[:500],
        "errors": delegation_result.errors[:5] if delegation_result.errors else [],
    })
    if len(quarantine) > _QUARANTINE_LEDGER_MAX:
        quarantine = quarantine[-_QUARANTINE_LEDGER_MAX:]
    agent_data["_quarantined_delegations"] = quarantine

    return (
        f"⚠️ DELEGATION OUTPUT QUARANTINED\n\n"
        f"Reason: {reason}\n"
        f"Profile: {delegation_result.profile}\n"
        f"Status: {delegation_result.status}\n"
        f"Errors: {', '.join(delegation_result.errors[:3]) if delegation_result.errors else 'none'}\n\n"
        f"The output from this delegation was NOT merged into the project because "
        f"the cross-delegation spiral detector identified repeated failures with "
        f"the same root cause. Re-delegating will produce the same failure.\n\n"
        f"ACTION REQUIRED: Fix the root cause at the orchestrator level, or "
        f"accept the limitation and deliver partial results via the response tool."
    )
