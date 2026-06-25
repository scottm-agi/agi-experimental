"""Canonical registry of all agent.data signal keys.

Every underscore-prefixed key MUST be declared here. The ValidatedAgentData
wrapper emits a warning when an undeclared key is written, catching typos
and undocumented state mutations at runtime.

Architecture:
- ALL_KEYS: dict[str, DataKeyMeta] — the canonical registry
- PERSIST_WHITELIST: set[str] — keys that survive serialization/restart
- ValidatedAgentData: dict subclass that validates writes against the registry

Usage:
    from python.helpers.agent_data_keys import ALL_KEYS, PERSIST_WHITELIST

    # Check if a key is known:
    if key in ALL_KEYS: ...

    # Get metadata:
    meta = ALL_KEYS["_current_phase"]
    print(meta.type_hint, meta.owner, meta.description)
"""

import logging
from typing import Any, NamedTuple

logger = logging.getLogger("agix.agent_data_schema")


class DataKeyMeta(NamedTuple):
    """Metadata for a registered agent.data key."""
    key: str             # The key string (e.g., "_current_phase")
    type_hint: str       # Python type hint as string (e.g., "int", "bool", "dict")
    default: Any         # Default value when not set
    persist: bool        # True = survives restart (in serialization whitelist)
    owner: str           # Primary file that owns/writes this key
    description: str     # Human-readable description
    project_scoped: bool = False  # True = cleared when _active_project_dir changes (System 7 ITR-44)


# =============================================================================
# KEY DEFINITIONS — Grouped by functional area
# =============================================================================

_KEY_DEFS: list[DataKeyMeta] = [

    # ── Loop Control / Death Spiral Protection ──
    DataKeyMeta("_tool_call_dedup", "list", [], False,
                "agent.py", "Dedup tracker for tool calls in current turn"),
    DataKeyMeta("_same_message_repeat_count", "int", 0, False,
                "agent.py", "Consecutive same-message repeat counter"),
    DataKeyMeta("_same_message_cumulative_count", "int", 0, False,
                "agent.py", "Cumulative same-message count across conversation"),
    DataKeyMeta("_semantic_repeat_count", "int", 0, False,
                "agent.py", "Consecutive semantically-similar message counter"),
    DataKeyMeta("_semantic_cumulative_count", "int", 0, False,
                "agent.py", "Cumulative semantic repeat count"),
    DataKeyMeta("_escape_hatch", "dict", {}, False,
                "agent.py", "Escape hatch state for death spiral recovery"),
    DataKeyMeta("_truncation_retries", "int", 0, False,
                "agent.py", "Count of retries due to context truncation"),
    DataKeyMeta("_generate_image_count", "int", 0, False,
                "generate_image.py", "Per-session generate_image call count (RCA-454 hard cap)"),
    DataKeyMeta("_empty_pressure_condensed", "bool", False, False,
                "agent.py", "Whether empty-pressure condensation has been triggered"),
    DataKeyMeta("_empty_response_retries", "int", 0, False,
                "agent.py", "Count of empty response retries"),
    DataKeyMeta("_empty_response_cycles", "int", 0, False,
                "agent.py", "Count of empty response cycles"),
    DataKeyMeta("_consecutive_blocked_tools", "int", 0, False,
                "agent.py", "Count of consecutive blocked tool calls"),
    DataKeyMeta("_last_tool_was_blocked", "bool", False, False,
                "agent.py", "Whether the last tool call was blocked"),
    DataKeyMeta("_total_null_iterations", "int", 0, False,
                "agent.py", "Total null iterations (no tool call, no response)"),
    DataKeyMeta("_delivery_complete", "bool", False, False,
                "agent.py", "Whether delivery has been completed"),
    DataKeyMeta("_force_response", "bool", False, False,
                "_38_verification_spiral_guard.py", "Force agent to respond immediately"),

    # ── Unified Retry Budget (P0-1 Death Spiral Unification) ──
    DataKeyMeta("_retry_budget", "object", None, False,
                "retry_budget.py", "Unified RetryBudgetManager instance replacing 15+ scattered loop control counters"),

    # ── Tool Processing ──
    DataKeyMeta("_consecutive_misformat_count", "int", 0, False,
                "agent_process_tools.py", "Count of consecutive misformatted tool calls"),

    # ── Tool Failure Tracking ──
    DataKeyMeta("_tool_failed_in_current_turn", "bool", False, False,
                "_12_tool_failure_tracker.py", "Whether a tool failed in current turn"),
    DataKeyMeta("_consecutive_mistake_count", "int", 0, False,
                "_12_tool_failure_tracker.py", "Consecutive mistake counter for error state"),
    DataKeyMeta("_last_consecutive_fail_tool", "str", "", False,
                "_12_tool_failure_tracker.py", "Name of last consecutively-failing tool"),
    DataKeyMeta("_circuit_breaker_triggered", "bool", False, False,
                "_12_tool_failure_tracker.py", "Whether tool circuit breaker has triggered"),
    DataKeyMeta("_circuit_breaker_tool", "str", "", False,
                "_12_tool_failure_tracker.py", "Name of tool that triggered circuit breaker"),
    DataKeyMeta("_circuit_breaker_count", "int", 0, False,
                "_12_tool_failure_tracker.py", "Count of circuit breaker activations"),
    DataKeyMeta("_tool_failure_counts", "dict", {}, False,
                "_12_tool_failure_tracker.py", "Per-tool failure counters"),
    DataKeyMeta("_tracker_blocked_tools", "set", set(), False,
                "_12_tool_failure_tracker.py", "Set of TIER 3 blocked tool names. Writers: _12_tool_failure_tracker. Readers: _13_tool_block_enforcer, _06_tool_failure_reset, supervisor_redirect_cap. Propagation: delegation_message, delegation_result_processing."),
    DataKeyMeta("_block_cooldown_counter", "int", 0, False,
                "_12_tool_failure_tracker.py", "Cooldown counter for tool blocks"),
    DataKeyMeta("_timeout_command_counts", "dict", {}, False,
                "_12_tool_failure_tracker.py", "Per-command timeout counters"),
    DataKeyMeta("_tool_failure_error_context", "dict", {}, False,
                "_12_tool_failure_tracker.py", "Error context for tool failures"),
    DataKeyMeta("_auth_error_tracker", "dict", {}, False,
                "_12_tool_failure_tracker.py", "Authentication error tracking state"),
    DataKeyMeta("_session_hint_counts", "dict", {}, False,
                "_12_tool_failure_tracker.py", "Per-session hint delivery counts"),
    DataKeyMeta("_l2_escalation_signals", "list", [], False,
                "_12_tool_failure_tracker.py", "L2 supervisor escalation signals"),

    # ── Supervisor Signal Quality / Nudge Tracking (P1-3) ──
    DataKeyMeta("_nudge_burst_limiter", "object", None, False,
                "_45_intelligent_supervisor.py", "BurstLimiter instance for nudge rate limiting"),
    DataKeyMeta("_nudge_history", "list", [], False,
                "_45_intelligent_supervisor.py", "List of NudgeRecord entries for nudge effectiveness tracking"),

    # ── Delegation / Batch Execution ──
    DataKeyMeta("_parent_agent_number", "int", 0, False,
                "batch_execution.py", "Parent agent number for subordinates"),
    DataKeyMeta("_batch_task_id", "str", "", False,
                "batch_execution.py", "Current batch task identifier"),
    DataKeyMeta("_batch_task_message", "str", "", False,
                "batch_execution.py", "Current batch task message"),
    DataKeyMeta("_batch_task_timeout", "int", 0, False,
                "batch_execution.py", "Batch task timeout in seconds"),
    DataKeyMeta("_batch_task_start_time", "float", 0.0, False,
                "batch_execution.py", "Batch task start timestamp"),
    DataKeyMeta("_active_project_dir", "str", "", True,
                "batch_execution.py", "Active project directory path (System 7: persisted for auto-rehydration)",
                project_scoped=False),  # Sentinel key — NOT cleared; it IS the change detector
    DataKeyMeta("_prompt_secrets_extracted", "bool", False, False,
                "batch_execution.py", "Whether secrets have been extracted from prompt"),
    DataKeyMeta("_research_depth", "int", 0, False,
                "batch_execution.py", "Research depth level for delegation"),
    DataKeyMeta("_parent_task_hash", "str", "", False,
                "delegation_brief.py", "Hash of parent task for correlation"),
    DataKeyMeta("_parent_task_seq_id", "str", "", False,
                "delegation_brief.py", "Sequential ID of parent task"),
    DataKeyMeta("_parent_task_guid", "str", "", False,
                "delegation_brief.py", "GUID of parent task"),
    DataKeyMeta("_dev_server_started", "bool", False, False,
                "services_mgt.py", "Whether dev server has been started"),
    DataKeyMeta("_dev_server_port", "int", 0, False,
                "services_mgt.py", "Port number of running dev server"),
    DataKeyMeta("_services_mgt_dev_server", "bool", False, False,
                "services_mgt.py", "Whether services_mgt manages the dev server"),
    DataKeyMeta("_verification_port_locked", "bool", False, False,
                "delegation_message.py", "Whether verification port is locked"),
    DataKeyMeta("_test_specs", "list", [], False,
                "delegation_message.py", "Test specifications for delegation"),
    DataKeyMeta("_mcp_health_registry", "dict", {}, False,
                "mcp_handler.py", "MCP server health status registry"),
    DataKeyMeta("_prompt_contract", "dict", {}, True,
                "delegation_message.py", "Prompt contract for delegation fidelity (System 7: persisted)",
                project_scoped=True),
    DataKeyMeta("_delegation_history", "list", [], False,
                "call_subordinate.py", "History of delegation attempts"),
    DataKeyMeta("_last_delegation_result", "dict", {}, False,
                "call_subordinate.py", "Result of last delegation"),
    DataKeyMeta("_search_tools_used", "set", set(), False,
                "call_subordinate.py", "Set of search tools used in current session"),
    DataKeyMeta("_is_retrying", "bool", False, False,
                "call_subordinate.py", "Whether current delegation is a retry"),
    DataKeyMeta("_retry_attempt", "int", 0, False,
                "call_subordinate.py", "Current retry attempt number"),
    DataKeyMeta("_last_retry_info", "dict", {}, False,
                "call_subordinate.py", "Information about the last retry"),
    DataKeyMeta("_subordinate_call_count", "int", 0, True,
                "_21_tool_call_tracker.py", "Total delegation count (persisted for essential gate)"),
    DataKeyMeta("_delegation_profiles", "set", set(), False,
                "_21_tool_call_tracker.py", "Set of delegation profiles used"),
    DataKeyMeta("_last_delegation_id", "str", "", False,
                "_21_tool_call_tracker.py", "ID of last delegation"),
    DataKeyMeta("_delegation_progress", "dict", {}, True,
                "_21_tool_call_tracker.py", "Delegation progress tracker (persisted for stall detection)"),
    DataKeyMeta("_delegation_depth", "int", 0, False,
                "_10_structural_guards.py", "Current delegation depth"),
    DataKeyMeta("_delegation_blocked", "bool", False, False,
                "_10_structural_guards.py", "Whether delegation is blocked"),

    # ── Orchestrator / Completion Gates ──
    DataKeyMeta("_orchestrator_completion_blocks", "int", 0, False,
                "orchestrator_gate_common.py", "Cumulative gate block counter"),
    DataKeyMeta("_error_state_bypassed", "bool", False, False,
                "orchestrator_gate_common.py", "Whether error-state bypass is active"),
    DataKeyMeta("_error_state_bypass_phase", "int", 0, False,
                "orchestrator_gate_common.py", "Phase when error-state bypass was activated"),
    DataKeyMeta("_error_state_degraded", "bool", False, False,
                "_22_multiagentdev_completion_gate.py", "Whether response is degraded"),
    DataKeyMeta("_last_bypass_failing_check", "str", "", False,
                "orchestrator_gate_common.py", "Last check that failed during bypass"),
    DataKeyMeta("_last_gate_failing_check", "str", "", False,
                "orchestrator_gate_common.py", "Name of last failing gate check"),
    DataKeyMeta("_consecutive_duplicate_responses", "int", 0, False,
                "orchestrator_gate_common.py", "Count of consecutive duplicate responses"),
    DataKeyMeta("_last_blocked_response", "str", "", False,
                "orchestrator_gate_common.py", "Content of last blocked response"),
    DataKeyMeta("_last_response_attempt", "str", "", False,
                "orchestrator_gate_common.py", "Content of last response attempt"),
    DataKeyMeta("_current_phase", "int", 0, True,
                "call_subordinate.py", "Current orchestration phase number (System 7: persisted for phase-aware gates)",
                project_scoped=True),
    DataKeyMeta("_phase_cap", "float", None, True,
                "phase_cap.py", "Maximum phase allowed for delegation (ITR-45: persisted, project-scoped). Set by extract_phase_scope() from prompt text or user messages.",
                project_scoped=True),
    DataKeyMeta("_phase_cap_reached", "bool", False, True,
                "_02_user_stop_directive.py", "True when current phase >= phase cap — triggers forced completion (ITR-45). Gates bypass when set.",
                project_scoped=True),
    DataKeyMeta("_integration_block_count", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Integration check block counter"),
    DataKeyMeta("_critical_check_blocks", "dict", {}, False,
                "_45_intelligent_supervisor.py", "Per-check block counters for supervisor"),
    DataKeyMeta("_pending_fidelity_violations", "list", [], False,
                "_45_intelligent_supervisor.py", "Pending fidelity violation records"),
    DataKeyMeta("_last_contract_result", "dict", {}, False,
                "_45_intelligent_supervisor.py", "Result of last contract evaluation"),
    DataKeyMeta("_gate_bypass_report", "dict", {}, False,
                "_45_intelligent_supervisor.py", "Gate bypass report for supervisor"),
    DataKeyMeta("_post_delivery_exhaustion_count", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Post-delivery exhaustion counter"),
    DataKeyMeta("_delivery_attempted", "bool", False, False,
                "_21_tool_call_tracker.py", "Whether delivery has been attempted"),
    DataKeyMeta("_deliverable_save_count", "int", 0, False,
                "_21_tool_call_tracker.py", "Count of deliverable saves"),
    DataKeyMeta("_deliverable_titles", "list", [], False,
                "_21_tool_call_tracker.py", "List of deliverable titles"),
    DataKeyMeta("_content_writer_delegated", "bool", False, False,
                "gate_business_checks.py", "Whether content writer has been delegated"),
    DataKeyMeta("_user_stop_directive", "bool", False, False,
                "_22_multiagentdev_completion_gate.py", "Whether user sent a stop directive"),

    # ── ITR-29 Quality Gate Counters ──
    DataKeyMeta("_arch_compliance_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Architecture compliance gate block counter"),
    DataKeyMeta("_req_code_verify_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Requirements-to-code verification block counter"),
    DataKeyMeta("_env_completeness_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Environment variable completeness block counter"),
    DataKeyMeta("_mock_data_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Mock data detection block counter"),
    DataKeyMeta("_stub_scan_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Unresolved test stub block counter"),
    DataKeyMeta("_unused_dep_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Unused dependency block counter"),
    DataKeyMeta("_proof_gate_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Proof verification gate block counter"),
    DataKeyMeta("_remediation_gate_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Remediation gate block counter"),
    DataKeyMeta("_escalation_gate_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Escalation gate block counter"),
    DataKeyMeta("_matrix_gate_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Verification matrix block counter"),
    DataKeyMeta("_architect_plan_blocks", "int", 0, False,
                "_22_architect_plan_gate.py", "Architect plan gate block counter"),
    DataKeyMeta("_manifest_gate_blocks", "int", 0, False,
                "_21_requirements_manifest_gate.py", "Requirements manifest gate block counter"),
    DataKeyMeta("_decomp_phase_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Decomposition phase gate block counter"),
    DataKeyMeta("_design_artifact_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Design artifact gate block counter"),
    DataKeyMeta("_requirements_coverage_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Requirements coverage gate block counter"),
    DataKeyMeta("_incomplete_req_gate_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Incomplete requirements gate block counter"),
    DataKeyMeta("_essential_gate_blocks", "int", 0, False,
                "_22_multiagentdev_completion_gate.py", "Essential gate (zero delegations) block counter"),

    # ── Prompt / User Context ──
    DataKeyMeta("_original_user_prompt", "str", "", True,
                "_03_prompt_capture.py", "Original user prompt (persisted for fidelity)"),
    DataKeyMeta("_raw_user_prompt", "str", "", True,
                "_05_raw_prompt_capture.py", "Raw user prompt before processing (persisted)"),
    DataKeyMeta("_prompt_capture_attempts", "int", 0, False,
                "_03_prompt_capture.py", "Number of prompt capture attempts"),
    DataKeyMeta("_planning_only", "bool", False, True,
                "_03_prompt_capture.py", "Whether this is a planning-only session (persisted)"),

    # ── Structural Guards / Supervisor ──
    DataKeyMeta("_last_turn_timestamp", "float", 0.0, False,
                "_10_structural_guards.py", "Timestamp of last turn for stall detection"),
    DataKeyMeta("_last_l2_escalation_turn", "int", 0, False,
                "_10_structural_guards.py", "Turn number of last L2 escalation"),
    DataKeyMeta("_cooldown_suppressed", "bool", False, False,
                "_10_structural_guards.py", "Whether cooldown escalation is suppressed"),
    DataKeyMeta("_monologue_consecutive_count", "int", 0, False,
                "_10_structural_guards.py", "Count of consecutive monologue turns"),
    DataKeyMeta("_monologue_last_check_time", "float", 0.0, False,
                "_10_structural_guards.py", "Timestamp of last monologue check"),
    DataKeyMeta("_last_l2_llm_call_turn", "int", 0, False,
                "_45_intelligent_supervisor.py", "Turn of last L2 LLM call"),
    DataKeyMeta("_l2_last_periodic_turn", "int", 0, False,
                "_45_intelligent_supervisor.py", "Turn of last L2 periodic check"),
    DataKeyMeta("_l2_consecutive_failures", "int", 0, False,
                "_45_intelligent_supervisor.py", "Count of consecutive L2 failures"),
    DataKeyMeta("_l2_external_signals", "list", [], False,
                "_45_intelligent_supervisor.py", "External signals for L2 supervisor"),
    DataKeyMeta("_decomposition_plan", "dict", {}, True,
                "_45_intelligent_supervisor.py", "Current decomposition plan (System 7: persisted for supervisor context)",
                project_scoped=True),
    DataKeyMeta("_decomposition_task_count", "int", 0, True,
                "agent.py", "Number of tasks in decomposition plan (System 7: persisted for turn budgeting)",
                project_scoped=True),
    DataKeyMeta("_decomposition_index", "dict", {}, True,
                "_16_decomposition_coverage_gate.py", "Decomposition index data (System 7: persisted for coverage gate)",
                project_scoped=True),
    DataKeyMeta("_supervisor_unavailable", "str", "", False,
                "_40_remote_intervention.py", "Reason supervisor is unavailable"),
    DataKeyMeta("_processed_intervention_signals", "set", set(), False,
                "_40_remote_intervention.py", "Set of processed intervention signal IDs"),

    # ── Extension / Init Flags ──
    DataKeyMeta("_scaffold_identity_done", "bool", False, False,
                "_04_scaffold_identity.py", "Whether scaffold identity injection is done"),
    DataKeyMeta("_env_validated_project", "str", "", False,
                "_36_env_validator.py", "Project path for which env has been validated"),
    DataKeyMeta("_budget_reserve_advisory_count", "int", 0, False,
                "_37_budget_reserve_advisor.py", "Count of budget reserve advisories"),
    DataKeyMeta("_feature_registry", "list", [], False,
                "_10_goal_tracking.py", "Registry of features for goal tracking"),
    DataKeyMeta("_extraction_metadata", "dict", {}, False,
                "_10_goal_tracking.py", "Metadata from requirement extraction"),

    # ── Browser / Quality ──
    DataKeyMeta("_browser_agent_calls", "int", 0, False,
                "_21_tool_call_tracker.py", "Count of browser agent delegation calls"),
    DataKeyMeta("_browser_screenshots", "list", [], False,
                "browser_agent.py", "List of browser screenshots taken"),
    DataKeyMeta("_quality_evaluation", "dict", {}, False,
                "browser_agent.py", "Quality evaluation results from browser"),
    DataKeyMeta("_blocked_in_tool", "dict", {}, False,
                "browser_agent.py", "Tool blocking state for browser/code/subordinate"),

    # ── Build / Dev Server / Code Execution ──
    DataKeyMeta("_file_writes_since_restart", "int", 0, False,
                "services_mgt.py", "Count of file writes since last dev server restart"),
    DataKeyMeta("_build_retry_count", "int", 0, False,
                "node_project.py", "Count of build retries"),
    DataKeyMeta("_test_retry_count", "int", 0, False,
                "node_project.py", "Count of test retries"),
    DataKeyMeta("_same_build_error_count", "int", 0, False,
                "node_project.py", "Count of same build error repetitions"),
    DataKeyMeta("_last_build_error", "str", "", False,
                "node_project.py", "Last build error message"),
    DataKeyMeta("_attempted_fixes", "list", [], False,
                "node_project.py", "List of attempted fixes for build errors"),
    DataKeyMeta("_code_execution_commands", "list", [], False,
                "delegation_result_processing.py", "List of code execution commands run"),
    DataKeyMeta("_service_retry_tracker", "dict", {}, False,
                "services_mgt.py", "Service retry tracking state"),
    DataKeyMeta("_build_loop_detector", "BuildLoopDetector", None, False,
                "build_loop_detector.py",
                "Cached BuildLoopDetector instance per agent (runtime only)"),
    DataKeyMeta("_build_failure_seed", "dict", None, False,
                "build_loop_detector.py",
                "Propagated build failure counts from parent agent, consumed on detector creation"),

    # ── Fidelity / Quality Gates ──
    DataKeyMeta("_fidelity_warned_this_turn", "bool", False, False,
                "_20_response_fidelity_gate.py", "Whether fidelity warning issued this turn"),
    DataKeyMeta("_fidelity_block_count", "int", 0, False,
                "_20_response_fidelity_gate.py", "Fidelity gate block counter"),
    DataKeyMeta("_fidelity_last_blocked_hash", "str", "", False,
                "_20_response_fidelity_gate.py", "Hash of last fidelity-blocked response"),
    DataKeyMeta("_last_req_verification", "dict", {}, False,
                "_18_post_execution_req_verifier.py", "Result of last requirement verification"),
    DataKeyMeta("_last_deliverable_verification", "dict", {}, False,
                "_16_subordinate_deliverable_verifier.py", "Result of last deliverable verification"),
    DataKeyMeta("_mock_data_warnings", "dict", {}, False,
                "_19_mock_data_guard.py", "Mock data warning records"),
    DataKeyMeta("_prisma_guard_warned", "set", set(), False,
                "_15_prisma_schema_guard.py", "Set of Prisma guard warnings issued"),
    DataKeyMeta("_quality_gate_enabled", "bool", True, False,
                "quality_gate.py", "Whether quality gate is enabled"),

    # ── Guards / Enforcers ──
    DataKeyMeta("_curl_preflight", "dict", {}, False,
                "curl_preflight.py", "cURL preflight check state"),
    DataKeyMeta("_secret_guard_warnings", "list", [], False,
                "_23_secret_guard.py", "Secret guard warning records"),
    DataKeyMeta("_file_write_counts", "dict", {}, False,
                "_26_file_write_dedup_guard.py", "Per-file write counters for dedup"),
    DataKeyMeta("_file_write_total_blocks", "int", 0, False,
                "_26_file_write_dedup_guard.py", "Total file write dedup blocks"),
    DataKeyMeta("_file_write_conflicts", "list", [], False,
                "_45_intelligent_supervisor.py", "File write conflict records"),
    DataKeyMeta("_failed_replace_files", "dict", {}, False,
                "_15_lint_after_write.py", "Failed file replacement records"),
    DataKeyMeta("_blob_hard_blocks", "int", 0, False,
                "_16_decomposition_coverage_gate.py", "Blob hard block counter"),
    DataKeyMeta("_dep_hard_blocks", "int", 0, False,
                "_16_decomposition_coverage_gate.py", "Dependency hard block counter"),
    DataKeyMeta("_req_id_hard_blocks", "int", 0, False,
                "_16_decomposition_coverage_gate.py", "Requirement ID hard block counter"),
    DataKeyMeta("_coverage_gate_last_snapshot", "dict", {}, False,
                "_16_decomposition_coverage_gate.py", "Last coverage gate snapshot"),


    # ── Response / History ──
    DataKeyMeta("_consecutive_response_rejections", "int", 0, False,
                "response.py", "Count of consecutive response rejections"),
    DataKeyMeta("_last_response_content", "str", "", False,
                "response.py", "Content of last response for near-dup detection"),
    DataKeyMeta("_gate_retry_active", "bool", False, False,
                "response.py", "Whether gate retry is currently active"),
    DataKeyMeta("_task_list", "list", [], False,
                "update_task_list.py", "Task list state"),
    DataKeyMeta("_root_attachments", "list", [], False,
                "agent_history.py", "Root-level attachments"),
    DataKeyMeta("_organize_history_task", "asyncio.Task", None, False,
                "_10_organize_history.py",
                "Running asyncio.Task for history compression (RCA-471: was missing, caused 155 warnings/session)"),
    DataKeyMeta("_recently_modified_files", "list", [], False,
                "agent.py", "Recently modified file paths"),
    DataKeyMeta("_project_dir", "str", "", False,
                "agent.py", "Project directory path"),
    DataKeyMeta("_working_directory", "str", "", False,
                "_04_scaffold_identity.py", "Working directory path"),
    DataKeyMeta("_sandbox_dir", "str", "", False,
                "_16_subordinate_deliverable_verifier.py", "Sandbox directory path"),
    DataKeyMeta("_nextjs_project_detected", "bool", False, False,
                "_73_nextjs_rules_injection.py", "Whether Next.js project was detected"),
    DataKeyMeta("_rework_cycle_count", "int", 0, False,
                "delegation_guards.py", "Rework cycle counter"),
    DataKeyMeta("_verification_delegated", "bool", False, False,
                "delegation_result_processing.py", "Whether verification was delegated"),
    DataKeyMeta("_lit_tests_executed", "bool", False, False,
                "delegation_result_processing.py", "Whether LIT tests were executed"),
    DataKeyMeta("_test_skeleton_generated", "bool", False, True,
                "call_subordinate.py", "Whether test skeleton was generated (System 7: persisted to prevent re-gen)",
                project_scoped=True),
    DataKeyMeta("_bdd_skeleton_generated", "bool", False, True,
                "call_subordinate.py", "Whether BDD skeleton was generated (System 7: persisted to prevent re-gen)",
                project_scoped=True),

    # ── Persisted State (survive restart) ──
    DataKeyMeta("_delegated_profile", "str", "", True,
                "persist_chat.py", "Delegated profile name (persisted for RCA-261)"),
    DataKeyMeta("_requirements_ledger", "dict", {}, True,
                "requirements_ledger.py", "Requirements tracking ledger (persisted)"),
    DataKeyMeta("_topic_dedup_state", "dict", {}, True,
                "delegation_topic_dedup.py", "Topic dedup counts (persisted, RCA-451)"),

    # ── Redelegation Guard ──
    DataKeyMeta("_gate_redelegation_tracker", "dict", {}, False,
                "redelegation_guard.py", "Re-delegation attempt tracker per profile::check"),
    DataKeyMeta("_last_gate_block_details", "dict", {}, False,
                "redelegation_guard.py", "Details of the last gate block event"),
    DataKeyMeta("_gate_block_history", "list", [], False,
                "redelegation_guard.py", "Condensed history trail of gate blocks"),
    DataKeyMeta("_compound_deadlock_signals", "dict", {}, False,
                "redelegation_guard.py", "Compound deadlock detection signals"),
    DataKeyMeta("_compound_deadlock_override", "bool", False, False,
                "redelegation_guard.py", "Whether compound deadlock override is active"),
    DataKeyMeta("_relevant_delegation_profiles", "set", set(), False,
                "redelegation_guard.py", "Set of relevant profiles for exhaustion calc"),

    # ── Gate Rejection Cap ──
    DataKeyMeta("_total_gate_blocks", "int", 0, False,
                "gate_rejection_cap.py", "Total gate blocks across all checks"),
    DataKeyMeta("_response_blocked_this_cycle", "bool", False, False,
                "gate_rejection_cap.py", "Whether response was blocked in current cycle"),

    # ── P1-2 Consolidated State Objects ──
    # These structured keys replace ~58 individual keys via dataclass consolidation.
    # See plan_p1_2_agent_data_cleanup.md §7 for full mapping.
    DataKeyMeta("_loop_state", "LoopState", None, False,
                "agent.py", "Consolidated loop detection state (replaces 12 loop-control keys)"),
    DataKeyMeta("_termination_state", "TerminationState", None, False,
                "agent.py", "Consolidated termination/completion state (replaces 3 keys)"),
    DataKeyMeta("_tool_failure_state", "ToolFailureState", None, False,
                "_12_tool_failure_tracker.py", "Consolidated tool failure state (replaces 13 keys)"),
    DataKeyMeta("_gate_check_block_counts", "dict", {}, False,
                "_22_multiagentdev_completion_gate.py", "Consolidated ITR-29 gate block counters (replaces 17 keys)"),
    DataKeyMeta("_gate_state", "GateState", None, False,
                "orchestrator_gate_common.py", "Consolidated gate runtime state (replaces 8 keys)"),
    DataKeyMeta("_redelegation_state", "RedelegationState", None, False,
                "redelegation_guard.py", "Consolidated redelegation guard state (replaces 6 keys)"),
    DataKeyMeta("_build_state", "BuildState", None, False,
                "node_project.py", "Consolidated build error tracking state (replaces 5 keys)"),

    # ── GitGuard Escalation (ITR-45 F-7) ──
    DataKeyMeta("_git_guard_block_count", "int", 0, False,
                "_15_project_path_enforcer.py", "Consecutive GitGuard block counter for escalation (ITR-45 F-7)"),

    # ── Dynamic npm Version Guard ──
    DataKeyMeta("_researcher_versions", "dict", {}, False,
                "_25_npm_version_guard.py", "Pre-parsed researcher-found package versions (pkg → (ver, label))"),
    DataKeyMeta("_framework_research", "str", "", False,
                "_25_npm_version_guard.py", "Raw researcher output text for version extraction"),
    DataKeyMeta("_npm_version_guard_cache", "dict", None, False,
                "_25_npm_version_guard.py", "Cached effective pinned packages (merged researcher + hardcoded)"),
    DataKeyMeta("_npm_version_guard_cache_hash", "str", "", False,
                "_25_npm_version_guard.py", "Hash of inputs used to build version guard cache"),
]


# =============================================================================
# BUILD THE REGISTRIES
# =============================================================================

ALL_KEYS: dict[str, DataKeyMeta] = {m.key: m for m in _KEY_DEFS}

PERSIST_WHITELIST: set[str] = {k for k, m in ALL_KEYS.items() if m.persist}

# System 7 (ITR-44): Keys that must be cleared when _active_project_dir changes.
# These hold state tied to a specific project — stale values from an old project
# would pollute a new one after restart.
PROJECT_SCOPED_KEYS: frozenset[str] = frozenset(
    k for k, m in ALL_KEYS.items() if m.project_scoped
)


def invalidate_project_scoped_keys(
    agent_data: dict,
    new_project_dir: str,
) -> list[str]:
    """Clear all project-scoped keys when the active project changes.

    System 7 (ITR-44): When the agent switches from one project to another,
    keys like _current_phase, _decomposition_plan, _test_skeleton_generated,
    etc. hold stale values from the OLD project. This function clears them
    and updates _active_project_dir to the new project.

    Called from every location that sets _active_project_dir to a new value.

    Args:
        agent_data: The agent's mutable data dict.
        new_project_dir: The new project directory being switched to.

    Returns:
        List of key names that were cleared (empty if no change).
    """
    old_project = agent_data.get("_active_project_dir", "")

    # No-op: first-time set (no old project) or same project
    if not old_project or old_project == new_project_dir:
        return []

    cleared: list[str] = []
    for key in PROJECT_SCOPED_KEYS:
        if key in agent_data:
            agent_data.pop(key)
            cleared.append(key)

    if cleared:
        logger.warning(
            f"[SYSTEM 7] Project changed: {old_project} → {new_project_dir}. "
            f"Cleared {len(cleared)} project-scoped keys: {sorted(cleared)}"
        )

    # Update the sentinel to the new project
    agent_data["_active_project_dir"] = new_project_dir

    return cleared


# =============================================================================
# VALIDATED DICT WRAPPER
# =============================================================================

class ValidatedAgentData(dict):
    """Dict subclass that warns on writes to undeclared underscore-prefixed keys.

    In dev mode (AGIX_STRICT_DATA=1), raises KeyError instead of warning.
    Non-underscore keys are always allowed (they are framework/config keys).
    """

    import os as _os
    _STRICT = _os.environ.get("AGIX_STRICT_DATA", "0") == "1"

    def __setitem__(self, key, value):
        if isinstance(key, str) and key.startswith("_") and key not in ALL_KEYS:
            msg = (
                f"[DATA_SCHEMA] Undeclared agent.data key: '{key}'. "
                f"Add it to python/helpers/agent_data_keys.py to suppress this warning."
            )
            if self._STRICT:
                raise KeyError(msg)
            else:
                logger.warning(msg)
        super().__setitem__(key, value)

    def update(self, __m=(), **kwargs):  # type: ignore[override]
        """Override update to validate each key."""
        if isinstance(__m, dict):
            for k, v in __m.items():
                self[k] = v
        elif hasattr(__m, "items"):
            for k, v in __m.items():
                self[k] = v
        else:
            for k, v in __m:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, key, default=None):
        """Override setdefault to validate the key if it's new."""
        if key not in self:
            self[key] = default  # Triggers __setitem__ validation
        return self[key]
