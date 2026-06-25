"""
Delegation message enrichment extracted from call_subordinate.py (P1.1 modularization).

Contains all the message injection/enrichment logic that prepends context,
instructions, and metadata to the delegation message before sending it
to the subordinate agent.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.subordinate")

# ──────────────────────────────────────────────────────────────────────
# F-13 (RCA-357): Profile → Available Tools mapping
# Tells subordinates which tools they can use, preventing wasted iterations
# from profile enforcement blocking unavailable tool calls.
# ──────────────────────────────────────────────────────────────────────
PROFILE_TOOLS = {
    'code': [
        'code_execution_tool', 'write_to_file', 'replace_in_file', 'apply_diff',
        'read_file', 'list_dir', 'sequential_thinking',
        'secret_get', 'secret_set', 'frontend_kb', 'response',
        'resolve_literals',
    ],
    'architect': [
        'sequential_thinking', 'read_file', 'list_dir', 'save_deliverable',
        'response', 'generate_guid', 'requirements',
    ],
    'researcher': [
        'search', 'browser', 'read_file', 'list_dir', 'response',
        'sequential_thinking',
    ],
    'frontend': [
        'sequential_thinking', 'read_file', 'list_dir', 'generate_image',
        'save_deliverable', 'response',
    ],
    'frontend_designer': [
        'sequential_thinking', 'read_file', 'list_dir', 'generate_image',
        'save_deliverable', 'response',
    ],
    'review': [
        'read_file', 'list_dir', 'sequential_thinking', 'response',
    ],
    'debug': [
        'code_execution_tool', 'read_file', 'list_dir', 'sequential_thinking',
        'response',
    ],
    'e2e': [
        'browser', 'read_file', 'response', 'sequential_thinking',
    ],
    'multiagentdev': [
        'call_subordinate', 'call_subordinate_batch', 'sequential_thinking',
        'read_file', 'requirements', 'generate_guid', 'response',
        'maintain_memory_bank', 'save_deliverable', 'fan_out_subordinates',
    ],
}


def inject_available_tools(message: str, profile: str) -> str:
    """Inject available tool list into delegation message.

    Tells the subordinate exactly which tools it can use, preventing
    wasted iterations from profile enforcement blocking unavailable
    tool calls.

    Args:
        message: The delegation task message
        profile: The subordinate agent profile name (e.g., 'code', 'researcher')

    Returns:
        Message with tool list prepended if profile is known, else unchanged.
    """
    if not profile:
        return message
    tools = PROFILE_TOOLS.get(profile.lower(), [])
    if not tools:
        return message
    tool_list = ', '.join(f'`{t}`' for t in tools)
    injection = (
        f"\n## Available Tools\n"
        f"Your profile ({profile}) has access to these tools: {tool_list}\n"
        f"Do NOT attempt to use any other tools — they will be blocked.\n\n"
    )

    # SS-1/SS-8: Mandatory resolve_literals mandate for code profile
    # Root cause: Code agents use training-data model slugs (e.g. "claude-3-5-sonnet")
    # instead of the exact API slug from content_manifest.json. This mandate ensures
    # they always call resolve_literals first.
    if profile.lower() == 'code':
        injection += (
            "## ⚡ MANDATORY: resolve_literals for AI Model Slugs\n"
            "For every AI model referenced in content_manifest.json, you MUST call "
            "`resolve_literals` with category:'llm_model' BEFORE writing any model "
            "slug in code. Do NOT use training data for model slugs — always resolve "
            "the exact API slug (e.g., 'anthropic/claude-sonnet-4') via resolve_literals.\n\n"
        )

    return injection + message



def inject_swarm_instructions(
    message: str,
    swarm_instructions: str | None,
) -> str:
    """Prepend swarm-level instructions to the delegation message."""
    if swarm_instructions:
        message = f"## Swarm Instructions (Mandatory)\n{swarm_instructions}\n\n---\n\n## Your Task\n{message}"
    return message


def inject_project_scope(message: str, agent: "Agent") -> str:
    """Inject active project scope header into message.

    Only injects the project name/path header. Codebase state injection
    is handled exclusively by build_delegation_package() which is the
    single source of truth for all context injection (ISS-C fix).

    Previously, codebase state was dual-injected: once here (ungated)
    and once via build_delegation_package (gated), causing Phase 5/6
    agents to receive full blueprints/manifests and interpret them as
    'build everything from scratch' (RCA-ITR36 RC-1).
    """
    from python.helpers import projects
    project_name = projects.get_context_project_name(agent.context)
    if not project_name:
        return message

    project_path = projects.get_project_folder(project_name)
    message = (
        f"## Active Project Scope\n"
        f"**Project:** `{project_name}`\n"
        f"**Path:** `{project_path}`\n"
        f"All work, secrets, and parameters MUST be scoped to this project.\n"
        f"**Temp Dir:** `{project_path}/tmp/` — NEVER use system `/tmp/`. "
        f"Run `mkdir -p {project_path}/tmp/` before use.\n\n---\n\n"
        + message
    )

    return message


def inject_error_relay_context(message: str, agent_data: dict) -> str:
    """Inject cross-subordinate error history into message.

    Two sources of error context:
    1. In-memory: build_error_injection(agent_data) from subordinate_error_relay
    2. File-based: memory-bank/delegation-error-log.md written by
       write_error_tracking_log() — survives context window truncation.

    F-13c: File content is capped at 2000 chars to avoid context bloat.
    """
    try:
        from python.helpers.subordinate_error_relay import build_error_injection
        error_injection = build_error_injection(agent_data)
        if error_injection:
            message = error_injection + message
            logger.info(
                f"ERROR RELAY: Injected cross-subordinate error context "
                f"({len(error_injection)} chars) into delegation message"
            )
    except Exception as e:
        logger.warning(f"Error relay injection failed (non-fatal): {e}")

    # F-13c: File-based error log injection
    _FILE_ERROR_LOG_CAP = 2000
    try:
        import os
        project_dir = agent_data.get("_active_project_dir", "")
        if project_dir:
            log_path = os.path.join(project_dir, "memory-bank", "delegation-error-log.md")
            if os.path.isfile(log_path):
                with open(log_path, "r") as f:
                    file_content = f.read()
                if file_content.strip():
                    # Cap at 2000 chars to avoid context bloat
                    if len(file_content) > _FILE_ERROR_LOG_CAP:
                        file_content = file_content[:_FILE_ERROR_LOG_CAP] + "\n...(truncated)"
                    file_injection = (
                        "\n## 📋 Persistent Error Log (from file)\n"
                        f"{file_content}\n\n"
                    )
                    message = file_injection + message
                    logger.info(
                        f"ERROR RELAY FILE: Injected file-based error log "
                        f"({len(file_content)} chars) from {log_path}"
                    )
    except Exception as e:
        logger.warning(f"File-based error log injection failed (non-fatal): {e}")

    return message


def inject_fidelity_violations(message: str, agent_data: dict) -> str:
    """Inject parent's manifest fidelity violations (RCA-251 §9.4)."""
    try:
        fidelity_violations = agent_data.get("_pending_fidelity_violations", [])
        if not fidelity_violations:
            return message

        fidelity_lines = [
            "\n⚠️ FIDELITY WARNING — PARENT MANIFEST VIOLATIONS:\n"
            "The following fidelity violations were detected in the parent's "
            "Phase 0 artifacts. You MUST ensure your implementation uses the "
            "EXACT values from the user's original prompt:\n"
        ]
        for v in fidelity_violations:
            vtype = v.get("type", "unknown")
            if vtype == "substitution":
                fidelity_lines.append(
                    f"  🔴 SUBSTITUTION: Expected '{v.get('expected_value', '?')}' "
                    f"but found '{v.get('found_value', '?')}'"
                )
            elif vtype == "missing_scenario":
                fidelity_lines.append(
                    f"  🔴 MISSING SCENARIO: {v.get('scenario_name', v.get('scenario_id', '?'))}"
                )
            elif vtype == "missing_url":
                fidelity_lines.append(f"  🔴 MISSING URL: {v.get('url', '?')}")
            elif vtype == "missing_price":
                fidelity_lines.append(f"  🔴 MISSING PRICE: {v.get('price', '?')}")
            else:
                fidelity_lines.append(f"  🔴 {vtype.upper()}: {v.get('detail', '?')}")
        fidelity_lines.append(
            "\nDo NOT use substituted values. Use the EXACT values above.\n"
        )
        fidelity_injection = "\n".join(fidelity_lines)
        message = fidelity_injection + message
        logger.info(
            f"FIDELITY PROPAGATION: Injected {len(fidelity_violations)} "
            f"violation(s) into delegation message"
        )
    except Exception as e:
        logger.warning(f"Fidelity violation injection failed (non-fatal): {e}")
    return message


def inject_contract_assertions(message: str, agent: "Agent") -> str:
    """Inject proactive contract assertions (U-2 Fix)."""
    try:
        from python.helpers.boomerang_context import get_original_user_message
        from python.helpers.prompt_contract_parser import build_contract

        original_msg = get_original_user_message(agent)
        if not original_msg or len(original_msg) <= 50:
            return message

        # Cache contract on agent.data to avoid re-parsing per delegation
        if "_prompt_contract" not in agent.data:
            agent.data["_prompt_contract"] = build_contract(original_msg)

        contract = agent.data["_prompt_contract"]
        assertions = contract.get("assertions", [])

        # Only inject high-confidence assertions
        critical_assertions = [
            a for a in assertions
            if a.get("confidence", 0) >= 0.8
        ]

        if critical_assertions:
            assertion_lines = [
                "\n## ⚡ VERBATIM VALUES FROM USER PROMPT (Use these EXACTLY)\n"
                "The following values were extracted from the original user prompt. "
                "You MUST use these EXACT strings in your code — do NOT substitute "
                "from your training data:\n"
            ]
            for a in critical_assertions:
                atype = a.get("type", "").replace("_", " ").title()
                # Build assertion display line with resolved_slug when available
                if a.get('resolved_slug', ''):
                    assertion_lines.append(f"  - **{a['id']}** [{atype}]: `{a['value']}` → API slug: `{a['resolved_slug']}`")
                else:
                    assertion_lines.append(f"  - **{a['id']}** [{atype}]: `{a['value']}`")
            assertion_lines.append(
                "\n⚠️ Any substitution of the above values will be caught by "
                "the fidelity gate and your work will be rejected. Use them verbatim.\n"
            )
            assertion_injection = "\n".join(assertion_lines)
            message = assertion_injection + message
            logger.info(
                f"CONTRACT INJECTION: {len(critical_assertions)} assertions "
                f"injected into delegation message"
            )
    except Exception as e:
        logger.warning(f"Contract assertion injection failed (non-fatal): {e}")
    return message


def inject_prompt_passthrough(
    message: str,
    agent: "Agent",
    agent_profile: str,
    kwargs: dict,
) -> str:
    """Inject original user prompt for orchestrators and specialists."""
    if not agent_profile:
        return message

    try:
        from python.helpers.boomerang_context import ORCHESTRATOR_PROFILES, get_original_user_message
        original_msg = get_original_user_message(agent)

        if agent_profile.lower() in ORCHESTRATOR_PROFILES:
            # Orchestrator: full prompt as primary source of truth (prepended)
            if original_msg and original_msg not in message:
                message = (
                    f"## FULL ORIGINAL USER REQUEST\n"
                    f"You are an orchestrator. Below is the COMPLETE original user request. "
                    f"You MUST use this as your source of truth, not the summary above.\n\n"
                    f"{original_msg}\n\n---\n\n"
                    f"## Parent Agent's Delegation Notes\n{message}"
                )
        else:
            # Specialist: assigned requirements + full prompt as reference (appended)
            # 1. Inject assigned requirements block if available
            requirement_ids = kwargs.get("requirement_ids", [])
            if requirement_ids:
                try:
                    from python.helpers.requirements_ledger import _ensure_ledger
                    ledger = _ensure_ledger(agent.data)
                    from python.helpers.req_id_normalizer import build_normalized_req_map
                    req_map = build_normalized_req_map(ledger.get("requirements", []))
                    req_lines = ["## Assigned Requirements (from original user prompt)"]
                    req_lines.append("You MUST implement ALL of these. Each has a tracking ID.\n")
                    for req_id in requirement_ids:
                        req = req_map.get(req_id, {})
                        if req:
                            line = f"- **{req_id}**: {req.get('text', 'Unknown')}"
                            # Surface partial/failed status so subordinate
                            # knows not to waste effort on accepted-as-partial reqs
                            status = req.get("status", "")
                            if status in ("partial", "failed"):
                                reason = req.get("partial_reason", req.get("failed_reason", ""))
                                line += f"\n  ⚠️ STATUS: **{status}**"
                                if reason:
                                    line += f" — {reason[:200]}"
                            req_lines.append(line)
                    req_lines.append("\nWhen complete, confirm each requirement by ID in your response.")
                    req_lines.append("---\n")
                    message = "\n".join(req_lines) + "\n" + message
                except Exception as req_err:
                    logger.warning(f"Requirements block injection failed (non-fatal): {req_err}")

            # ISS-4 FIX: TDD mandate is now ONLY injected by inject_tdd_mandate()
            # in call_subordinate.py (single source). Removed duplicate
            # build_tdd_mandate() call that was here previously.

            # 2. Append full original prompt as reference
            if original_msg and len(original_msg) > 100 and original_msg not in message:
                message += (
                    f"\n\n---\n## Original User Prompt (Reference)\n"
                    f"For complete context on what the user requested:\n\n"
                    f"{original_msg}\n\n---\n"
                )
                logger.info(
                    f"PROMPT PASSTHROUGH: Injected {len(original_msg)} chars of original "
                    f"prompt into {agent_profile} subordinate message"
                )
    except Exception as e:
        logger.warning(f"Prompt passthrough injection failed (non-fatal): {e}")
    return message


def inject_task_tracking(
    message: str,
    subordinate: "Agent",
    kwargs: dict,
) -> str:
    """Inject task tracking metadata into message and subordinate.data."""
    task_hash = kwargs.get("_task_hash", "")
    task_seq_id = kwargs.get("_task_seq_id", 0)
    task_guid = kwargs.get("task_guid", "")

    if not (task_hash and task_seq_id):
        return message

    tracking_header = (
        f"## Task Tracking\n"
        f"**task_hash**: `{task_hash}` | **attempt**: #{task_seq_id}"
    )
    if task_guid:
        tracking_header += f" | **guid**: `{task_guid}`"
    tracking_header += (
        f"\nInclude `task_hash={task_hash}` and `attempt=#{task_seq_id}` in ALL your responses.\n\n---\n\n"
    )
    message = tracking_header + message

    # Propagate to subordinate.data for _task_list cross-referencing
    subordinate.data["_parent_task_hash"] = task_hash
    subordinate.data["_parent_task_seq_id"] = task_seq_id
    if task_guid:
        subordinate.data["_parent_task_guid"] = task_guid

    logger.info(
        f"TRACKING INJECTED: hash={task_hash}, seq={task_seq_id}, "
        f"guid={task_guid or 'none'} → {subordinate.agent_name}"
    )
    return message


def inject_turn_budget(message: str, agent: "Agent", subordinate: "Agent") -> str:
    """Inject turn budget notice so subordinates know their remaining budget."""
    try:
        from python.helpers.turn_budget import build_turn_budget_notice
        parent_turns = getattr(agent, '_absolute_turns', 0)
        parent_max = agent.get_max_turns() if hasattr(agent, 'get_max_turns') else 0
        budget_notice = build_turn_budget_notice(
            current_turn=parent_turns,
            max_turns=parent_max,
        )
        if budget_notice:
            message = budget_notice + "\n\n" + message
            logger.info(
                f"TURN BUDGET INJECTED: {parent_max - parent_turns} turns remaining "
                f"→ {subordinate.agent_name}"
            )
    except Exception as e:
        logger.warning(f"Turn budget injection failed (non-fatal): {e}")
    return message


# ═══════════════════════════════════════════════════════════════════════
# F-09: Port Stability Contract
# ═══════════════════════════════════════════════════════════════════════
#
# Root cause: Dev server port changes between LIT (5173) and E2E (5100)
# verification phases, invalidating earlier results. Port locking ensures
# the port stays consistent once LIT passes.

# Valid port range for dev servers (covers common ports: 3000, 5100-5500, etc.)
_PORT_RANGE_MIN = 1024
_PORT_RANGE_MAX = 65535


def lock_verification_port(agent_data: dict, port: int | str) -> bool:
    """Lock the verification port to prevent drift between phases.

    After LIT passes, call this to lock the port so subsequent
    E2E verification uses the same port. First lock wins —
    subsequent calls are no-ops.

    Args:
        agent_data: The agent's data dict.
        port: Port number to lock (int or string).

    Returns:
        True if port was locked, False if rejected (out of range
        or already locked to a different port).
    """
    # Convert string to int
    try:
        port_int = int(port)
    except (ValueError, TypeError):
        logger.warning(f"[PORT LOCK] Invalid port value: {port}")
        return False

    # Validate range
    if port_int < _PORT_RANGE_MIN or port_int > _PORT_RANGE_MAX:
        logger.warning(
            f"[PORT LOCK] Port {port_int} out of valid range "
            f"({_PORT_RANGE_MIN}-{_PORT_RANGE_MAX}), rejecting"
        )
        return False

    # First lock wins
    if agent_data.get("_verification_port_locked", False):
        existing_port = agent_data.get("_dev_server_port", "")
        logger.info(
            f"[PORT LOCK] Port already locked to {existing_port}, "
            f"ignoring new lock request for {port_int}"
        )
        return True  # Already locked — not an error

    agent_data["_verification_port_locked"] = True
    agent_data["_dev_server_port"] = str(port_int)
    logger.info(f"[PORT LOCK] Locked verification port to {port_int}")
    return True


def get_locked_port(agent_data: dict) -> int | None:
    """Retrieve the locked verification port.

    Args:
        agent_data: The agent's data dict.

    Returns:
        Locked port as int, or None if no port is locked.
    """
    if not agent_data.get("_verification_port_locked", False):
        return None
    port_str = agent_data.get("_dev_server_port", "")
    if not port_str:
        return None
    try:
        return int(port_str)
    except (ValueError, TypeError):
        return None


def propagate_data_to_subordinate(
    agent: "Agent",
    subordinate: "Agent",
    kwargs: dict,
) -> None:
    """Propagate various data flags from parent to subordinate.

    Includes: _active_project_dir, research_depth, _dev_server_started/port,
    _verification_port_locked (F-09).
    """
    # TOP-DOWN PROJECT DIR (RCA-300)
    parent_project = agent.data.get("_active_project_dir", "")
    if parent_project and not subordinate.data.get("_active_project_dir"):
        subordinate.data["_active_project_dir"] = parent_project
        logger.info(
            f"Propagated _active_project_dir='{parent_project}' "
            f"from {agent.agent_name} → {subordinate.agent_name}"
        )

    # ORIGINAL PROMPT DOWNWARD PROPAGATION (ITR-49)
    # Required for Code Agent self-checks (fidelity, compliance)
    if agent.data.get("_original_prompt"):
        subordinate.data["_original_prompt"] = agent.data["_original_prompt"]
    if agent.data.get("_user_prompt"):
        subordinate.data["_user_prompt"] = agent.data["_user_prompt"]

    # RESEARCH DEPTH PROPAGATION (Iteration 111)
    research_depth = kwargs.get("research_depth", "")
    if research_depth in ("shallow", "deep"):
        subordinate.data["_research_depth"] = research_depth
        logger.info(
            f"Propagated research_depth='{research_depth}' to subordinate "
            f"{subordinate.agent_name}"
        )

    # DEV SERVER FLAG DOWNWARD PROPAGATION (Iteration 111)
    if agent.data.get("_dev_server_started", False):
        subordinate.data["_dev_server_started"] = True
        subordinate.data["_dev_server_port"] = agent.data.get("_dev_server_port", "")
        # RCA-ITR32-C: Also propagate services_mgt-specific flag downward
        if agent.data.get("_services_mgt_dev_server", False):
            subordinate.data["_services_mgt_dev_server"] = True
        logger.info(
            f"Propagated _dev_server_started=True to subordinate "
            f"{subordinate.agent_name} (port={subordinate.data.get('_dev_server_port', '?')}, "
            f"via_services_mgt={subordinate.data.get('_services_mgt_dev_server', False)})"
        )

    # PORT LOCK PROPAGATION (F-09: Port Stability Contract)
    # When parent has locked a verification port, propagate the lock to
    # subordinate so they use the same port for all verification phases.
    if agent.data.get("_verification_port_locked", False):
        subordinate.data["_verification_port_locked"] = True
        # Ensure the locked port value is also propagated
        locked_port = agent.data.get("_dev_server_port", "")
        if locked_port:
            subordinate.data["_dev_server_port"] = locked_port
        logger.info(
            f"Propagated _verification_port_locked=True to subordinate "
            f"{subordinate.agent_name} (port={locked_port})"
        )

    # BDD SPECS PROPAGATION (RCA-354 C-1)
    # Propagate _test_specs so subordinate's completion gate can validate
    test_specs = agent.data.get("_test_specs", [])
    if test_specs:
        subordinate.data["_test_specs"] = test_specs
        logger.info(
            f"Propagated _test_specs ({len(test_specs)} specs) to subordinate "
            f"{subordinate.agent_name}"
        )

    # SS-2: MCP HEALTH REGISTRY PROPAGATION (ITR-344)
    # Copy the parent's MCP tool failure registry to the subordinate so it
    # inherits knowledge of known-bad tools. This prevents each subordinate
    # from wasting API calls on tools that already failed N times upstream.
    parent_mcp_registry = agent.data.get("_mcp_health_registry", {})
    if parent_mcp_registry:
        # Deep-copy to avoid shared mutation
        import copy
        subordinate.data["_mcp_health_registry"] = copy.deepcopy(parent_mcp_registry)
        logger.info(
            f"Propagated _mcp_health_registry ({len(parent_mcp_registry)} tools) "
            f"from {agent.agent_name} → {subordinate.agent_name}"
        )

    # SS-1/SS-4: TOOL FAILURE STATE PROPAGATION (ITR-344)
    # Copy the parent's tool failure tracking state to the subordinate so it
    # inherits knowledge of blocked tools and failure counts. Without this,
    # each new subordinate starts with a clean slate and repeats the same
    # failures (355 blocked attempts in the MainStreet run).
    import copy as _copy

    # _tracker_blocked_tools — Tier 3 blocked tools (set of tool names)
    parent_blocked = agent.data.get("_tracker_blocked_tools", set())
    if parent_blocked:
        subordinate.data["_tracker_blocked_tools"] = _copy.deepcopy(parent_blocked)
        logger.info(
            f"Propagated _tracker_blocked_tools ({len(parent_blocked)} tools) "
            f"from {agent.agent_name} → {subordinate.agent_name}"
        )

    # _tool_failure_counts — per-tool failure counts (dict)
    parent_counts = agent.data.get("_tool_failure_counts", {})
    if parent_counts:
        subordinate.data["_tool_failure_counts"] = _copy.deepcopy(parent_counts)
        logger.info(
            f"Propagated _tool_failure_counts ({len(parent_counts)} tools) "
            f"from {agent.agent_name} → {subordinate.agent_name}"
        )

    # _session_hint_counts — escalation ladder state (dict)
    parent_hints = agent.data.get("_session_hint_counts", {})
    if parent_hints:
        subordinate.data["_session_hint_counts"] = _copy.deepcopy(parent_hints)
        logger.info(
            f"Propagated _session_hint_counts ({len(parent_hints)} entries) "
            f"from {agent.agent_name} → {subordinate.agent_name}"
        )




def inject_verification_findings(message: str, agent_data: dict) -> str:
    """Inject structured verification findings from e2e/debug into delegation.

    When multiagentdev delegates to code after e2e found issues, this
    function systemically injects the structured findings so the code
    agent has complete context without relying on the LLM to copy/paste.

    Data sources:
    - _quality_evaluation: Structured e2e verdict with issues list
    - _subordinate_error_log: Build errors from previous code delegations

    This function fires ONLY when _quality_evaluation exists AND passed=False.
    After injection, it clears _quality_evaluation to prevent stale re-injection.

    RCA-339 Part 3: Verification findings propagation (e2e → code).

    Args:
        message: The delegation task message
        agent_data: The orchestrator agent's data dict

    Returns:
        Message with findings prepended if available, else unchanged
    """
    quality_eval = agent_data.get("_quality_evaluation")
    if not quality_eval:
        return message
    if quality_eval.get("passed", True):
        return message

    source = quality_eval.get("source", "verification_agent")
    verdict = quality_eval.get("verdict", "FAIL")
    issues = quality_eval.get("issues", [])
    response_text = quality_eval.get("response", "")

    lines = [
        "\n🔍 VERIFICATION FINDINGS — FIX THESE SPECIFIC ISSUES:",
        "The E2E verification agent found the following issues that YOU must fix.",
        "Do NOT re-run verification — fix the code first.",
        "",
        f"Source: {source}",
        f"Verdict: {verdict}",
    ]

    # Issues list
    if isinstance(issues, list) and issues:
        lines.append("")
        lines.append("Issues Found:")
        for i, issue in enumerate(issues, 1):
            lines.append(f"  {i}. {issue}")
    elif isinstance(issues, str) and issues:
        lines.append("")
        lines.append(f"Issues: {issues}")
    elif response_text:
        # Fallback: include response preview
        lines.append("")
        lines.append(f"Verification Response: {response_text[:300]}")

    # Cross-reference with error relay for previous attempt context
    error_log = agent_data.get("_subordinate_error_log", [])
    if error_log:
        blocking_errors = []
        for entry in error_log:
            errs = entry.get("errors", [])
            if entry.get("severity") == "blocking" and errs:
                blocking_errors.extend(errs[:3])  # Cap at 3 per entry
        if blocking_errors:
            lines.append("")
            lines.append("Previous attempts to fix these issues failed because:")
            for err in blocking_errors[:5]:  # Cap at 5 total
                lines.append(f"  - {err}")

    lines.extend([
        "",
        "🔴 Apply 5-Why analysis: WHY did the previous fix fail? Fix the ROOT CAUSE.",
        "✅ After fixing, the orchestrator will re-run verification automatically.\n",
    ])

    injection = "\n".join(lines)
    logger.info(
        f"VERIFICATION FINDINGS INJECTION: source='{source}', "
        f"verdict='{verdict}', issues={len(issues) if isinstance(issues, list) else 1}"
    )

    # Clear after injection to prevent stale re-injection
    del agent_data["_quality_evaluation"]

    return injection + message


def write_error_tracking_log(agent_data: dict, project_dir: str) -> str:
    """Write structured error tracking log to memory bank for persistence.

    Creates/updates a `delegation-error-log.md` file in the project's
    memory-bank/ directory. This provides:
    1. Persistent error context that survives context window truncation
    2. A file agents can be directed to read for full error history
    3. Automatic rotation — entries older than the last successful gate pass
       are archived under a "## Resolved" section

    RCA-339 Part 4: File-based error persistence.

    Args:
        agent_data: The orchestrator agent's data dict
        project_dir: Absolute path to the project directory

    Returns:
        Path to the error log file, or empty string if write failed
    """
    import os
    from datetime import datetime, timezone

    mb_dir = os.path.join(project_dir, "memory-bank")
    log_path = os.path.join(mb_dir, "delegation-error-log.md")

    try:
        os.makedirs(mb_dir, exist_ok=True)
    except Exception as e:
        logger.warning(f"Failed to create memory-bank dir: {e}")
        return ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Collect current error context
    sections = [f"# Delegation Error Log\n_Last updated: {now}_\n"]

    # Gate block details
    block_details = agent_data.get("_last_gate_block_details")
    if block_details:
        sections.append(
            f"## Current Gate Block\n"
            f"- **Check**: {block_details.get('check_name', '?')}\n"
            f"- **Message**: {block_details.get('block_message', '?')}\n"
            f"- **Count**: {block_details.get('block_count', 0)}\n"
        )

    # Gate block history
    history = agent_data.get("_gate_block_history", [])
    if history:
        sections.append("## Failure Trajectory")
        for i, entry in enumerate(history, 1):
            sections.append(f"{i}. [{entry.get('check', '?')}] {entry.get('summary', '?')}")
        sections.append("")

    # Verification findings
    quality_eval = agent_data.get("_quality_evaluation")
    if quality_eval and not quality_eval.get("passed", True):
        sections.append(
            f"## Verification Findings\n"
            f"- **Source**: {quality_eval.get('source', '?')}\n"
            f"- **Verdict**: {quality_eval.get('verdict', '?')}\n"
        )
        issues = quality_eval.get("issues", [])
        if isinstance(issues, list):
            for issue in issues:
                sections.append(f"- {issue}")
        sections.append("")

    # Error relay entries
    error_log = agent_data.get("_subordinate_error_log", [])
    if error_log:
        sections.append("## Subordinate Error History")
        for entry in error_log[-5:]:  # Last 5 entries
            profile = entry.get("profile", "?")
            errors = entry.get("errors", [])
            sections.append(f"### Profile: {profile}")
            for err in errors[:3]:
                sections.append(f"- {err}")
        sections.append("")

    # Redelegation tracker state
    tracker = agent_data.get("_gate_redelegation_tracker", {})
    if tracker:
        sections.append("## Redelegation Tracker")
        for key, count in tracker.items():
            sections.append(f"- `{key}`: {count} attempts")
        sections.append("")

    content = "\n".join(sections)

    try:
        with open(log_path, "w") as f:
            f.write(content)
        logger.info(f"ERROR TRACKING LOG: Written to {log_path} ({len(content)} chars)")
        return log_path
    except Exception as e:
        logger.warning(f"Failed to write error tracking log: {e}")
        return ""


# ─── WB-6: Anti-Tautology Guidance ──────────────────────────────────────
# Injected into build_tdd_mandate() output. Root cause: build_tdd_mandate
# had 5 rules about test existence/ordering but ZERO about assertion quality.
# expect('07:30').toBe('07:30') passed all gates.
_ANTI_TAUTOLOGY_GUIDANCE = (
    "\n"
    "6. **🚫 NO TAUTOLOGICAL ASSERTIONS.** A tautological test asserts a\n"
    "   literal equals itself — it tests NOTHING about your code.\n"
    "\n"
    "   ❌ BAD (tautological — tests nothing):\n"
    "   ```\n"
    "   expect('07:30').toBe('07:30');        // literal == same literal\n"
    "   expect(true).toBe(true);              // boolean == same boolean\n"
    "   expect('MainStreet').toContain('MainStreet');\n"
    "   ```\n"
    "\n"
    "   ✅ GOOD (behavioral — tests real code):\n"
    "   ```\n"
    "   const hours = await getBusinessHours('test-id');\n"
    "   expect(hours.open).toMatch(/^\\d{2}:\\d{2}$/);\n"
    "   render(<OpeningHours />);\n"
    "   expect(screen.getByText(/open/i)).toBeInTheDocument();\n"
    "   ```\n"
    "\n"
    "   Every assertion MUST call a function, render a component, query an API,\n"
    "   or read from state. If both sides of the assertion are hardcoded\n"
    "   literals, it is tautological and will be REJECTED.\n"
)

# RED→GREEN enforcement warning (Integration Point 3)
# Appended to both branches of build_tdd_mandate() so code agents know
# the gate verifies per-test RED→GREEN transitions.
_RED_GREEN_ENFORCEMENT = (
    "\n\n"
    "🔴 RED→GREEN ENFORCEMENT:\n"
    "The completion gate WILL run your tests against the red-baseline.json "
    "recorded before implementation started. If tests that were RED (failing) "
    "are still RED after your implementation, your delivery will be REJECTED. "
    "If tests that were GREEN regress to RED, your delivery will be REJECTED. "
    "Do NOT use expect(true).toBe(true) or other auto-pass garbage — the gate "
    "checks per-test results."
    "\n\nAfter running tests, if ANY test fails:\n"
    "1. Read the failure output carefully\n"
    "2. Fix the failing code (NOT the test)\n"
    "3. Run tests again\n"
    "4. Repeat until ≥99% tests pass or you've iterated 5 times\n"
    "5. Report final pass/fail counts in your response"
)


def build_tdd_mandate(test_specs: list[dict] | None) -> str:
    """Build a TDD mandate section from architect-produced test_specs.

    Deep Dive §3.1: The architect produces test_specs in the decomposition plan.
    These specs are injected into delegation messages via this function so that
    subordinate code agents know which tests to write FIRST.

    RCA-354 Fix 2 (L4 gap): When test_specs is empty/None, returns a generic
    TDD mandate instead of empty string. This ensures code agents ALWAYS
    receive TDD instructions even when bdd_specs weren't produced.

    Args:
        test_specs: List of dicts with 'test_file', 'descriptions',
                    and 'content_assertions' keys. May be None/empty.

    Returns:
        Formatted markdown section for injection into delegation messages.
        Generic TDD mandate if no specs provided (never empty string).
    """
    if not test_specs:
        # RCA-354 Fix 2: Generic TDD fallback — ensure code agents always
        # receive TDD instructions even without architect-produced specs.
        return (
            "## 🧪 TDD MANDATE — Write Tests FIRST\n"
            "\n"
            "No specific test specs were provided by the architect, but the\n"
            "TDD-first mandate STILL APPLIES. You MUST:\n"
            "\n"
            "1. **Create test files BEFORE implementation code.** For every\n"
            "   business logic file you create (e.g., `src/lib/foo.ts`),\n"
            "   write `__tests__/foo.test.ts` first.\n"
            "2. **Verify tests fail** before writing the implementation.\n"
            "3. **Run `npm test`** (or equivalent) to confirm tests pass after.\n"
            "4. **API routes need tests.** Every `route.ts` handler must have\n"
            "   a corresponding test file.\n"
            "5. **Minimum coverage: 50%.** At least half of all non-trivial\n"
            "   source files must have corresponding test files.\n"
            + _ANTI_TAUTOLOGY_GUIDANCE
            + _RED_GREEN_ENFORCEMENT +
            "\n"
            "---\n"
            "\n"
        )

    lines = [
        "## 🧪 TDD MANDATE — Write Tests FIRST",
        "",
        "The architect has specified the following test requirements.",
        "You MUST write these test files BEFORE writing the implementation.",
        "",
    ]

    for i, spec in enumerate(test_specs, 1):
        test_file = spec.get("test_file", "unknown")
        descriptions = spec.get("descriptions", [])
        assertions = spec.get("content_assertions", [])

        lines.append(f"### Test {i}: `{test_file}`")
        if descriptions:
            lines.append("**Test cases:**")
            for desc in descriptions:
                lines.append(f"  - {desc}")
        if assertions:
            lines.append("**Must assert:**")
            for assertion in assertions:
                lines.append(f"  - Content contains: `{assertion}`")
        lines.append("")

    # WB-6: Anti-tautology guidance — test QUALITY, not just existence
    lines.append(_ANTI_TAUTOLOGY_GUIDANCE)
    # Integration Point 3: RED→GREEN enforcement warning
    lines.append(_RED_GREEN_ENFORCEMENT)
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def enrich_tdd_mandate_with_api_checks(
    mandate_text: str,
    manifest_data: dict | None,
) -> str:
    """Enrich TDD mandate with API-specific test requirements.

    F-4 Fix: When the manifest includes api_integrations, inject guidance
    requiring tests to mock HTTP clients and verify real HTTP calls were
    made — preventing stubs that return hardcoded data.

    Args:
        mandate_text: The TDD mandate text produced by build_tdd_mandate().
        manifest_data: The project manifest dict. May be None or missing
                       the 'api_integrations' key.

    Returns:
        Enriched mandate text with API test requirements, or the original
        mandate_text if no api_integrations are present.
    """
    if not manifest_data:
        return mandate_text

    api_integrations = manifest_data.get("api_integrations", [])
    if not api_integrations:
        return mandate_text

    # Idempotency: don't duplicate if already enriched
    if "API Integration Test Requirements" in mandate_text:
        return mandate_text

    lines = [
        "",
        "### 🌐 API Integration Test Requirements",
        "",
        "The project manifest includes API integrations. Each API-calling",
        "function MUST have tests that verify REAL HTTP behavior:",
        "",
    ]

    for api in api_integrations:
        api_name = api.get("name", "Unknown API")
        base_url = api.get("base_url", "")
        lines.append(f"**{api_name}**" + (f" (`{base_url}`)" if base_url else ""))
        lines.append(
            f"  - Test MUST mock the HTTP client (e.g., `jest.mock('node-fetch')`) "
            f"and verify the actual HTTP call was made with correct URL/headers"
        )
        lines.append(
            f"  - Test MUST fail if the function returns hardcoded data "
            f"without making an HTTP call"
        )
        lines.append("")

    lines.append(
        "⚠️ A mock function that returns static data without asserting "
        "the HTTP call was made is NOT a valid test."
    )
    lines.append("")

    api_section = "\n".join(lines)
    return mandate_text + api_section



def inject_tdd_mandate(message: str, agent_data: dict) -> str:
    """Inject TDD mandate from agent_data test_specs into delegation message.

    Reads test_specs from agent_data (set during architect planning) and
    prepends the TDD mandate section to the delegation message.
    """
    test_specs = agent_data.get("_test_specs", [])
    mandate = build_tdd_mandate(test_specs)
    if mandate:
        return f"{mandate}\n{message}"
    return message


def inject_skill_reference(message: str, agent: "Agent", agent_profile: str) -> str:
    """Inject activated skill reference into delegation message.

    When the orchestrator has auto-activated a skill (e.g., fullstack-dev),
    this function adds a concise skill reference to the delegation message
    so subordinate agents (architect, code) know which conventions to follow.

    The orchestrator gets the full skill body via auto-activation extension.
    Subordinates get a reference via this injection in the delegation message.

    Args:
        message: The delegation task message.
        agent: The parent (orchestrator) agent.
        agent_profile: The target subordinate profile.

    Returns:
        Message with skill reference prepended if applicable.
    """
    try:
        activated_skill = agent.get_data("_activated_skill_name")
        if not activated_skill:
            return message

        if not hasattr(agent, 'skills_manager'):
            return message

        # Determine what to inject based on target profile
        # Architect gets the conventions skill reference
        # Code agents get a brief reference
        skill_content = agent.skills_manager.get_skill_content(activated_skill)
        if not skill_content:
            return message

        # For architect and code profiles, inject conventions reference
        target = agent_profile.lower() if agent_profile else ""
        if target in ("architect", "code", "frontend_designer"):
            # Check if there's a conventions companion skill
            conventions_name = None
            if "fullstack" in activated_skill:
                conventions_name = "fullstack-conventions"

            conventions_content = None
            if conventions_name:
                conventions_content = agent.skills_manager.get_skill_content(conventions_name)

            ref_lines = [
                f"\n## 📋 ACTIVE SKILL CONVENTIONS: {activated_skill}",
                f"The orchestrator is following the **{activated_skill}** skill pipeline.",
                "Your work must comply with these conventions:\n",
            ]

            if conventions_content and conventions_content.instructions:
                # Inject first 100 lines of conventions
                conv_lines = conventions_content.instructions.split('\n')[:100]
                ref_lines.append('\n'.join(conv_lines))
            else:
                ref_lines.append(
                    f"*Use `view_skill` with name='{activated_skill}' for full details.*"
                )

            ref_lines.append("\n---\n")
            skill_ref = '\n'.join(ref_lines)
            message = skill_ref + message
            logger.info(
                f"SKILL REF INJECTED: {activated_skill} → {target} "
                f"(conventions={'yes' if conventions_content else 'no'})"
            )
    except Exception as e:
        logger.warning(f"Skill reference injection failed (non-fatal): {e}")
    return message


def inject_bdd_fallback(
    message: str,
    project_dir: str,
    bdd_specs: list | None = None,
    requirement_ids: list | None = None,
) -> str:
    """Auto-inject BDD scenarios from bdd-scenarios.md when bdd_specs not provided.

    F-0b (RCA ITR-10): Ensures code agents ALWAYS receive BDD context even
    when the orchestrator forgot to pass bdd_specs kwargs.
    """
    import os
    if bdd_specs:  # Specs already provided via kwargs — no fallback needed
        return message

    bdd_path = os.path.join(project_dir, 'docs', 'bdd-scenarios.md')
    if not os.path.isfile(bdd_path):
        return message

    try:
        with open(bdd_path, 'r') as f:
            bdd_content = f.read()
    except (IOError, OSError):
        return message

    if not bdd_content.strip():
        return message

    # Filter by requirement_ids if provided
    if requirement_ids:
        sections = []
        current_section: list[str] = []
        for line in bdd_content.splitlines():
            if line.startswith('Feature:'):
                if current_section:
                    sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        if current_section:
            sections.append('\n'.join(current_section))

        matching = [s for s in sections if any(rid in s for rid in requirement_ids)]
        if matching:
            bdd_content = '\n\n'.join(matching)
        # If no matches, inject all — better to over-provide than under-provide

    # Cap at 4000 chars to avoid context bloat
    if len(bdd_content) > 4000:
        bdd_content = bdd_content[:4000] + '\n...(truncated)'

    injection = (
        '\n## 📋 BDD Acceptance Criteria (Auto-Injected from bdd-scenarios.md)\n'
        '**You MUST read these BEFORE writing any code.** Each THEN clause defines\n'
        'what "done" means. Your tests must verify these behaviors.\n\n'
        f'{bdd_content}\n\n---\n\n'
    )
    logger.info(
        f'BDD INJECTION: Auto-injecting from bdd-scenarios.md — '
        f'{len(bdd_content)} chars from {bdd_path}'
    )
    return injection + message


def inject_researcher_api_docs(
    message: str,
    project_dir: str,
) -> str:
    """Auto-inject researcher API docs from docs/*research*.md files.

    F-0f (RCA ITR-10): Researcher deliverables contain exact API endpoints,
    SDK patterns, and env var names. Code agents need this context to
    implement real API calls instead of templates.
    """
    import os
    import glob

    docs_dir = os.path.join(project_dir, 'docs')
    if not os.path.isdir(docs_dir):
        return message

    research_files = glob.glob(os.path.join(docs_dir, '*research*.md'))
    if not research_files:
        return message

    research_content_parts = []
    for fpath in research_files[:3]:  # Cap at 3 files
        try:
            with open(fpath, 'r') as f:
                content = f.read()
            if content.strip():
                fname = os.path.basename(fpath)
                # Cap each file at 2000 chars
                if len(content) > 2000:
                    content = content[:2000] + '\n...(truncated)'
                research_content_parts.append(f'### {fname}\n{content}')
        except (IOError, OSError):
            continue

    if not research_content_parts:
        return message

    combined = '\n\n'.join(research_content_parts)
    injection = (
        '\n## 🔬 Researcher API Documentation (Auto-Injected)\n'
        'The researcher agent produced these API implementation details.\n'
        'Use these EXACT endpoints, SDK patterns, and env var names:\n\n'
        f'{combined}\n\n---\n\n'
    )
    logger.info(
        f'RESEARCHER DOCS INJECTION: {len(research_files)} research doc(s) '
        f'({len(combined)} chars) injected into delegation message'
    )
    return injection + message


def inject_route_map(
    message: str,
    project_dir: str,
) -> str:
    """Auto-inject app route map from architect_plan.json / decomposition_index.json.

    F1-d / F4-a (RCA-15): Subordinate agents need awareness of ALL routes
    in the application so shared navigation components link to every page.
    Without this, agents build navbars that only link to the page they're
    implementing, missing all other routes.

    Sources (checked in order):
    1. docs/architect_plan.json → pages[].route + pages[].name
    2. architect_plan.json (project root fallback)
    3. decomposition_index.json → phases[].route + phases[].name

    Routes are deduplicated and merged from all available sources.
    """
    import os
    import json
    from python.helpers.planning_paths import get_path as _planning_path

    if not os.path.isdir(project_dir):
        return message

    # ── Collect routes from architect_plan.json (canonical path) ──
    routes: dict[str, str] = {}  # route -> name
    plan_path = _planning_path(project_dir, "architect_plan")
    if os.path.isfile(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan = json.load(f)
            if isinstance(plan, dict):
                # Extract from pages array
                pages = plan.get('pages', [])
                if isinstance(pages, list):
                    for page in pages:
                        if isinstance(page, dict):
                            route = page.get('route', '')
                            name = page.get('name', '')
                            if route and route not in routes:
                                routes[route] = name
                # Also check route_map
                route_map = plan.get('route_map', plan.get('routes', []))
                if isinstance(route_map, list):
                    for entry in route_map:
                        if isinstance(entry, dict):
                            route = entry.get('route', entry.get('path', ''))
                            name = entry.get('name', entry.get('label', ''))
                            if route and route not in routes:
                                routes[route] = name
                elif isinstance(route_map, dict):
                    for route, name in route_map.items():
                        if route not in routes:
                            routes[route] = name if isinstance(name, str) else str(name)
        except (json.JSONDecodeError, IOError, OSError):
            pass

    # ── Collect routes from decomposition_index.json ──
    decomp_path = _planning_path(project_dir, "decomposition_index")
    if os.path.isfile(decomp_path):
        try:
            with open(decomp_path, 'r', encoding='utf-8') as f:
                decomp = json.load(f)
            if isinstance(decomp, dict):
                phases = decomp.get('phases', [])
                if isinstance(phases, list):
                    for phase in phases:
                        if isinstance(phase, dict):
                            route = phase.get('route', '')
                            name = phase.get('name', '')
                            if route and route not in routes:
                                routes[route] = name
        except (json.JSONDecodeError, IOError, OSError):
            pass

    if not routes:
        return message

    # ── Build injection ──
    route_lines = []
    for route in sorted(routes.keys()):
        name = routes[route]
        if name:
            route_lines.append(f'- `{route}` ({name})')
        else:
            route_lines.append(f'- `{route}`')

    injection = (
        '\n## 🗺️ App Route Map (RCA-15 — Auto-injected)\n\n'
        'This application serves the following routes. '
        'Your implementation MUST be aware of ALL routes:\n'
        + '\n'.join(route_lines) + '\n\n'
        'Shared navigation component MUST link to ALL routes listed above.\n\n'
        '---\n\n'
    )
    logger.info(
        f'ROUTE MAP INJECTION: {len(routes)} routes injected into delegation message'
    )
    return injection + message



def inject_component_spec(
    message: str,
    project_dir: str,
) -> str:
    """Auto-inject component-spec.md content into delegation messages.

    Upstream Testability Audit Item 3: component-spec.md is produced by the
    frontend designer in Phase 2.3 but was NEVER injected into code agent
    delegations. This caused code agents to build components from scratch
    instead of following the designer's specifications.
    """
    import os

    if not os.path.isdir(project_dir):
        return message

    from python.helpers.planning_paths import get_path as _planning_path
    spec_path = _planning_path(project_dir, 'component_spec')
    if not os.path.isfile(spec_path):
        return message

    try:
        with open(spec_path, 'r', encoding='utf-8') as f:
            spec_content = f.read()
    except (IOError, OSError):
        return message

    if not spec_content.strip():
        return message

    # Truncate at 3000 chars to avoid context bloat
    if len(spec_content) > 3000:
        spec_content = spec_content[:3000] + '\n...(truncated)'

    injection = (
        '\n## 📋 Component Specification (Auto-injected, MANDATORY)\n\n'
        'You MUST follow this component spec produced by the frontend designer.\n'
        'Do NOT invent your own component structure — use the spec below.\n\n'
        f'{spec_content}\n\n---\n\n'
    )
    logger.info(
        f'COMPONENT SPEC INJECTION: {len(spec_content)} chars injected '
        f'into delegation message from {spec_path}'
    )
    return injection + message


def inject_mockup_refs(
    message: str,
    project_dir: str,
) -> str:
    """Auto-inject mockup PNG file references into delegation messages.

    Upstream Testability Audit Item 4: docs/design-mockups/*.png files are
    produced by the frontend designer but NEVER injected. Code agents build
    pages from training defaults instead of reading the actual mockups.
    """
    import os
    import glob

    if not os.path.isdir(project_dir):
        return message

    mockup_dir = os.path.join(project_dir, 'docs', 'design-mockups')
    if not os.path.isdir(mockup_dir):
        return message

    png_files = sorted(glob.glob(os.path.join(mockup_dir, '*.png')))
    if not png_files:
        return message

    # Build mockup file listing
    mockup_lines = []
    for fpath in png_files:
        mockup_lines.append(f'- `{fpath}`')

    injection = (
        '\n## 🖼️ Design Mockups (Auto-injected — READ BEFORE CODING)\n\n'
        'The frontend designer produced the following mockup files.\n'
        'Use `read_file` to examine each mockup BEFORE writing the corresponding '
        'page component. Your implementation MUST match the mockup layout.\n\n'
        + '\n'.join(mockup_lines) + '\n\n---\n\n'
    )
    logger.info(
        f'MOCKUP REFS INJECTION: {len(png_files)} mockup(s) injected '
        f'into delegation message'
    )
    return injection + message
