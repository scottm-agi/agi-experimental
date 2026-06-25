from __future__ import annotations
from python.agent import Agent, UserMessage
from python.helpers.tool import Tool, Response
from python.helpers.agent_tracer import AgentTracer
from python.helpers.rate_limiter import RateLimiter, coordinate_agent_wait
from python.helpers.delegation_result import DelegationResult
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
    inject_contract_assertions,
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
from python.helpers.output_truncation import truncate_output_middle_out
from python.helpers.manifest_assignment import assign_items_to_delegation, get_unassigned_items
from python.initialize import initialize_agent
from python.extensions.hist_add_tool_result import _90_save_tool_call_file as save_tool_call_file
import asyncio
import logging
import os

logger = logging.getLogger("agix.subordinate")

# Try to import mode manager for MultiAgentDev support
try:
    from python.helpers.mode_manager import get_mode_manager
    from python.extensions.agent_init._60_mode_init import set_agent_mode, get_agent_mode
    MODE_SUPPORT = True
except ImportError:
    MODE_SUPPORT = False

# Cache swarm instructions to avoid re-reading the file on every delegation
_swarm_instructions_cache: dict[str, str | None] = {}

# Lazy import to avoid circular dependency
from python.tools.call_subordinate_helpers import _get_boomerang_context, _inject_e2e_fail_routing, _check_phase_order_before_delegation, _check_budget_before_first_delegation, format_subordinate_context_brief, _get_subordinate_timeout, _build_attempt_record, _extract_last_error, _cleanup_subordinate_ports

async def execute_delegation(self, message="", reset="", mode="", relay_response="", **kwargs):
    """
    Execute subordinate delegation.
    
    Args:
        message: Task message for the subordinate
        reset: If "true", create a new subordinate agent
        mode: (MultiAgentDev) Mode for the subordinate agent (code, architect, ask, debug, review)
        relay_response: If "true", emit subordinate's result as this agent's response log entry
        **kwargs: Additional arguments including 'profile' for agent profile
    """
    # ── FIX-017: Time budget enforcement (absolute last resort) ──
    try:
        from python.helpers.time_budget_enforcer import set_run_start, check_time_budget
        set_run_start(self.agent.data)
        exceeded, time_msg = check_time_budget(self.agent.data)
        if exceeded:
            logger.warning(f"[FIX-017] TIME BUDGET EXCEEDED — blocking delegation")
            return Response(message=time_msg, break_loop=False)
    except Exception as _tbe:
        logger.debug(f"[FIX-017] Time budget check failed (non-fatal): {_tbe}")

    # ── FIX-017: Delegation cycle detection (L1 — primary mechanism) ──
    try:
        from python.helpers.delegation_cycle_detector import check_delegation_stuck
        phase_key = kwargs.get("phase", "unknown")
        stuck, stuck_msg = check_delegation_stuck(self.agent.data, phase_key)
        if stuck:
            logger.warning(f"[FIX-017] DELEGATION CYCLE — blocking delegation for phase {phase_key}")
            return Response(message=stuck_msg, break_loop=False)
    except Exception as _dcd:
        logger.debug(f"[FIX-017] Delegation cycle check failed (non-fatal): {_dcd}")

    # ── DELEGATION GUARDS (delegated to delegation_guards module) ──
    current_profile = getattr(self.agent.config, "profile", "default")
    requested_profile = kwargs.get("profile", "")

    # Terminal profile guard
    guard_msg = check_terminal_profile_guard(current_profile)
    if guard_msg:
        return Response(message=guard_msg, break_loop=False)

    # Same-profile self-delegation guard
    guard_msg = check_same_profile_guard(current_profile, requested_profile, message)
    if guard_msg:
        return Response(message=guard_msg, break_loop=False)

    # Circuit breaker — delegation depth/loop detection
    guard_msg = check_circuit_breaker(self.agent, requested_profile, message)
    if guard_msg:
        return Response(message=guard_msg, break_loop=False)

    # Re-delegation guard — caps same profile+check to 2 attempts
    guard_msg = check_redelegation_guard_wrapper(self.agent.data, requested_profile, message)
    if guard_msg:
        return Response(message=guard_msg, break_loop=False)

    # Planning-only guard — block Phase 3+ delegations when the user
    # requested planning phases only. Retries with guidance directing
    # the agent to call response instead.
    # Escape hatch: STOP_AND_DELIVER escalation after 3 violations.
    guard_msg = check_planning_only_guard(self.agent.data, message)
    if guard_msg:
        return Response(message=guard_msg, break_loop=False)

    # Rework cycle guard — block delegation when Phase 5↔6 rework
    # budget is exhausted (ITR-31). Prevents infinite verification loops.
    guard_msg = check_rework_cycle_guard(self.agent)
    if guard_msg:
        return Response(message=guard_msg, break_loop=False)

    # ── F-10: REMOVED (RCA — MSR Phase 3 architect→code false positive) ──
    # F-10 was a regex-based pre-validator that detected task "intent"
    # (file_write, code_execution) and silently auto-corrected the profile.
    # REMOVED because: (1) regex patterns like \bPhase\s+3 and
    # \bimplementation\b matched contextual text, not actual task intent,
    # causing architect→code substitutions; (2) the override was SILENT —
    # the orchestrator never learned the delegation was changed; (3) SS-5
    # (ontology guard) already handles real capability mismatches with
    # proper ontology lookup instead of regex guessing.

    # SS-5: Ontology capability guard — block delegation when the
    # target profile lacks tools referenced in the task message.
    # Root cause (RCA-313, U-10): Advisory warnings were ignored by
    # LLMs under pressure, causing ~40% of delegations to waste
    # time on subordinates that couldn't execute the assigned task.
    #
    # SS-5b: Escalation auto-correction (MSR Phase 3 RCA).
    # After CAPABILITY_MISMATCH_AUTOCORRECT_THRESHOLD consecutive blocks
    # on the SAME profile, auto-correct to 'code' instead of blocking.
    # Root cause: orchestrator got blocked 12x on frontend because the
    # SS-5 blocker returns an error but the orchestrator doesn't learn.
    from python.helpers.delegation_guards import (
        should_autocorrect_capability_mismatch,
        increment_capability_mismatch_count,
        reset_capability_mismatch_count,
    )
    guard_msg = check_ontology_capability_guard(requested_profile, message)
    if guard_msg:
        # Check if we should escalate to auto-correction
        if should_autocorrect_capability_mismatch(self.agent.data, requested_profile):
            original_profile = requested_profile
            requested_profile = "code"
            kwargs["profile"] = "code"
            logger.warning(
                f"SS-5b ESCALATION: Auto-corrected '{original_profile}' → 'code' "
                f"after {self.agent.data.get('_capability_mismatch_counts', {}).get(original_profile, 0)} "
                f"consecutive blocks. Reason: {guard_msg[:200]}"
            )
            reset_capability_mismatch_count(self.agent.data, original_profile)
        else:
            increment_capability_mismatch_count(self.agent.data, requested_profile)
            return Response(message=guard_msg, break_loop=False)
    else:
        # No mismatch — reset counter for this profile
        reset_capability_mismatch_count(self.agent.data, requested_profile)

    # Check for global rate limit backoff before creating/running subordinate
    # This coordinates between agents to prevent overwhelming the API
    profile_model = getattr(self.agent.config, "chat_model", None)
    provider = getattr(profile_model, "provider", "unknown") if profile_model else "unknown"
    model_name = getattr(profile_model, "name", "unknown") if profile_model else "unknown"
    provider_key = f"{provider}\\{model_name}"
    
    # Wait if there's a global backoff in effect
    wait_time = await coordinate_agent_wait(provider, provider_key)
    if wait_time > 0:
        logger.info(f"Master agent {self.agent.agent_name} waited {wait_time:.1f}s for rate limit backoff before spawning subordinate")

    # Determine mode for subordinate — pass profile so it can be used
    # as priority 2 (above keyword suggestion) when mode is not explicit
    subordinate_mode = self._determine_subordinate_mode(
        mode, message, profile=kwargs.get("profile", "")
    )

    # ── PROFILE MISMATCH AUTO-RESET (RCA 215) ──
    # When the orchestrator delegates first to architect then to code,
    # the existing subordinate slot still holds the architect agent.
    # Without this check, the profile= argument is silently ignored
    # and the architect reruns — causing PROFILE_ENFORCEMENT blocks,
    # tool-loop timeouts, and wasted ~1200s per mismatch cycle.
    existing_sub = self.agent.get_data(Agent.DATA_NAME_SUBORDINATE)
    requested_profile = kwargs.get("profile", "")
    force_profile_reset = self._should_force_profile_reset(existing_sub, requested_profile)
    if force_profile_reset:
        # Nullify the stale subordinate so the creation block fires
        self.agent.set_data(Agent.DATA_NAME_SUBORDINATE, None)

    # create subordinate agent using the data object on this agent and set superior agent to his data object
    if (
        self.agent.get_data(Agent.DATA_NAME_SUBORDINATE) is None
        or str(reset).lower().strip() == "true"
    ):
        # initialize default config
        config = initialize_agent()

        # set subordinate prompt profile if provided, if not, keep original
        agent_profile = kwargs.get("profile")
        if agent_profile:
            # ── PROFILE VALIDATION: reject invalid/hallucinated profiles ──
            # MSR-2: Agents sometimes hallucinate profile names (e.g., "desygner").
            # Validate that the profile directory actually exists under agents/.
            from python.helpers import files
            profile_dir = files.get_abs_path("agents", agent_profile)
            if not os.path.isdir(profile_dir):
                valid_profiles = sorted([
                    d for d in os.listdir(files.get_abs_path("agents"))
                    if os.path.isdir(files.get_abs_path("agents", d))
                    and not d.startswith(".") and d != "_example"
                ])
                return Response(
                    message=f"⚠️ INVALID PROFILE: '{agent_profile}' does not exist. "
                            f"Valid profiles: {', '.join(valid_profiles)}. "
                            f"Check the profile name and try again.",
                    break_loop=False
                )
            # ── SWARM BOUNDARY ENFORCEMENT ──
            # After validating the profile exists on disk, check swarm
            # boundaries. Orchestrators (multiagentdev, alex) can only
            # delegate to their authorized swarm members.
            from python.helpers.swarm_registry import is_profile_allowed, get_allowed_profiles
            current_profile = getattr(self.agent.config, "profile", "default")
            if not is_profile_allowed(current_profile, agent_profile):
                allowed = get_allowed_profiles(current_profile) or set()
                logger.warning(
                    f"SWARM BOUNDARY VIOLATION: '{current_profile}' attempted "
                    f"to delegate to '{agent_profile}' (not in swarm). "
                    f"Allowed: {sorted(allowed)}"
                )
                return Response(
                    message=f"⛔ SWARM BOUNDARY VIOLATION: Profile '{current_profile}' cannot "
                            f"delegate to '{agent_profile}'. "
                            f"Allowed profiles: {', '.join(sorted(allowed))}. "
                            f"Use one of the authorized specialist profiles instead.",
                    break_loop=False
                )

            config.profile = agent_profile

        # ── PROFILE ROUTING VALIDATOR (v2 — Designer/Developer Separation) ──
        # Detect when a `code` delegation is actually UI/UX DESIGN work
        # (mockups, tokens, specs) that should go to the `frontend` (designer)
        # profile. ALL coding tasks (including frontend page implementation)
        # stay with `code`. See RCA-234 for the root cause analysis.
        from python.helpers.delegation_profile_router import should_use_designer_profile
        should_switch, switch_reason = should_use_designer_profile(message, config.profile or "")
        if should_switch:
            original_profile = config.profile
            config.profile = "frontend"
            logger.warning(
                f"PROFILE ROUTER: Auto-corrected '{original_profile}' → 'frontend' (designer). "
                f"Reason: {switch_reason}"
            )

        # ── DEBUG → CODE MISROUTING GUARD (RCA-346 F-4) ──
        # Detect when a file-editing task is being routed to the `debug`
        # profile, which has NO write_to_file. Auto-correct to `code`.
        from python.helpers.delegation_profile_router import should_correct_debug_to_code
        should_correct, correct_reason = should_correct_debug_to_code(message, config.profile or "")
        if should_correct:
            original_profile = config.profile
            config.profile = "code"
            logger.warning(
                f"PROFILE ROUTER: Auto-corrected '{original_profile}' → 'code'. "
                f"Reason: {correct_reason}"
            )

        # ── FRONTEND → CODE MISROUTING GUARD (ISS-4 P1) ──
        # Detect when a file-writing task is being routed to the `frontend`
        # profile, which has NO write_to_file/code_execution_tool.
        # The frontend is a pure UI/UX Designer — auto-correct to `code`.
        from python.helpers.delegation_profile_router import should_correct_frontend_to_code
        should_correct_fe, fe_correct_reason = should_correct_frontend_to_code(message, config.profile or "")
        if should_correct_fe:
            original_profile = config.profile
            config.profile = "code"
            logger.warning(
                f"PROFILE ROUTER (ISS-4): Auto-corrected '{original_profile}' → 'code'. "
                f"Reason: {fe_correct_reason}"
            )

        # ── SS-12: TOOL CAPABILITY MISMATCH GUARD ──
        # L3 gate: Detect when a delegation targets a profile that lacks
        # the required tools for the task (e.g., code-execution task → frontend).
        # Auto-corrects to the suggested profile if a mismatch is found.
        from python.helpers.delegation_tool_guard import check_tool_capability_mismatch
        tool_mismatch = check_tool_capability_mismatch(message, config.profile or "")
        if tool_mismatch:
            original_profile = config.profile
            if tool_mismatch.get("suggested_profile"):
                config.profile = tool_mismatch["suggested_profile"]
            logger.warning(
                f"TOOL GUARD (SS-12): Auto-corrected '{original_profile}' → "
                f"'{config.profile}'. Reason: {tool_mismatch['reason']}"
            )

        # ── Track delegation history for audit ──
        if "_delegation_history" not in self.agent.data:
            self.agent.data["_delegation_history"] = []
        self.agent.data["_delegation_history"].append({
            "profile": config.profile or "default",
            "message_preview": message[:200] if message else "",
        })
        sub = Agent(self.agent.number + 1, config, self.agent.context)
        # register superior/subordinate
        sub.set_data(Agent.DATA_NAME_SUPERIOR, self.agent)
        self.agent.set_data(Agent.DATA_NAME_SUBORDINATE, sub)

        # ── RCA-MSR-BuildLoop: Seed subordinate with inherited build failure counts ──
        # When the orchestrator retries a delegation, the new subordinate
        # inherits the build failure counter from the previous subordinate.
        # This allows Tier 2 (7) and Tier 3 (12) to fire across retries.
        try:
            propagated = self.agent.data.get("_build_failure_propagated")
            if propagated:
                from python.helpers.build_loop_detector import seed_build_loop_detector
                seed_build_loop_detector(sub, propagated)
        except Exception:
            pass  # Non-fatal
        
        # Apply mode to new subordinate if MultiAgentDev is enabled
        if MODE_SUPPORT and subordinate_mode:
            self._apply_mode_to_subordinate(sub, subordinate_mode, profile=config.profile or "default")

    # add user message to subordinate agent
    subordinate: Agent = self.agent.get_data(Agent.DATA_NAME_SUBORDINATE)  # type: ignore

    # ── DATA PROPAGATION & MESSAGE ENRICHMENT (delegated to delegation_message module) ──
    agent_profile = kwargs.get("profile", "")

    # ── F-9: One-time budget planning enforcement ──
    # On the first delegation after decomposition, nudge the orchestrator
    # to set an iteration budget if it hasn't already.
    budget_nudge = _check_budget_before_first_delegation(self.agent.data)
    if budget_nudge:
        self.agent.data["_delegation_budget_guidance"] = budget_nudge

    # ── FIX-C + F-4: Budget-aware delegation requirement cap ──
    # When a single delegation carries too many requirements, subordinate
    # build errors compound and exhaust the iteration budget.
    # F-4 (ITR-49): Replaced advisory-only warning with budget-aware
    # hard guidance injection using calculate_delegation_budget().
    _requirement_ids = kwargs.get("requirement_ids", [])
    if isinstance(_requirement_ids, list) and len(_requirement_ids) > 0:
        from python.helpers.gate_config import MAX_REQUIREMENTS_PER_DELEGATION
        if len(_requirement_ids) > MAX_REQUIREMENTS_PER_DELEGATION:
            # F-4: Budget-aware calculation using subordinate timeout
            try:
                _budget_timeout = _get_subordinate_timeout(
                    self.args, profile=kwargs.get("profile", "")
                )
                from python.helpers.budget_reserve import calculate_delegation_budget
                budget = calculate_delegation_budget(
                    timeout_seconds=_budget_timeout,
                    num_requirements=len(_requirement_ids),
                )
                if budget["over_budget"]:
                    _budget_guidance = (
                        f"⚠️ DELEGATION OVERLOAD: {len(_requirement_ids)} requirements "
                        f"exceeds budget-aware limit of {budget['max_requirements']} "
                        f"(based on {int(_budget_timeout)}s timeout, "
                        f"{budget['timeout_per_requirement']}s/requirement). "
                        f"Split into {budget['num_waves']} waves of "
                        f"{budget['recommended_wave_size']} requirements each. "
                        f"Complete wave 1 first, then delegate wave 2."
                    )
                    self.agent.data["_delegation_budget_guidance"] = _budget_guidance
                    logger.warning(
                        f"[DELEGATION CAP] {_budget_guidance}"
                    )
            except Exception as e:
                logger.warning(
                    f"[DELEGATION CAP] Budget calculation failed ({e}), "
                    f"falling back to static warning"
                )
                logger.warning(
                    f"[DELEGATION CAP] ⚠️ OVERLOAD: {len(_requirement_ids)} requirements "
                    f"exceeds limit of {MAX_REQUIREMENTS_PER_DELEGATION}. "
                    f"Split into page-centric waves for better success rate."
                )


    # ── RCA-298: requirement_ids enforcement ──
    # Primary enforcement is in ToolSchemaValidator (agent_process_tools.py L933+).
    # Schema validation runs BEFORE execute() and blocks missing requirement_ids
    # with a formatted error message. No redundant guard needed here.


    # ── ISS-D + ISS-G: Detect phase BEFORE propagation ──
    # Phase detection must run first so subordinate inherits the
    # correct phase via propagate_data_to_subordinate. Without this,
    # subordinates inherited stale _current_phase from previous delegations.
    try:
        from python.helpers.delegation_brief import detect_delegation_phase
        detected_phase = detect_delegation_phase(message, self.agent.data)
        if detected_phase is not None:
            # RCA-345 FIX-1: Store raw phase seq. Phase comparison is
            # done via parse_phase_seq() tuples. REPLACES broken R5
            # int(float()) that defaulted semver phases to 0.
            self.agent.data['_current_phase'] = detected_phase
            # F-9: Record that this phase was actually dispatched
            # (reconciler uses this to distinguish real executions from artifact side-effects)
            try:
                from python.tools.requirements import record_phase_dispatched
                record_phase_dispatched(self.agent.data, detected_phase)
            except Exception:
                pass  # Non-fatal — reconciler falls back to artifact-only
    except Exception:
        detected_phase = None

    # ── ITR-45: Phase cap enforcement ──
    # When the user prompt specifies a phase boundary (e.g., "Phase 0 through 3.5"),
    # block delegations that would exceed that boundary. This is a HARD BLOCK —
    # the orchestrator is told to use the response tool to finish instead.
    #
    # EXEMPTION: Build verification phases (3.8, 3.8.1, 3.9) are ALWAYS allowed
    # regardless of phase cap. These are quality checks, not new feature work.
    # Blocking them creates a deadlock: quality gate demands build evidence
    # that the phase cap prevents generating.
    _VERIFICATION_PHASES = {3.8, 3.9}  # 3.8.1 rounds to 3.8
    try:
        phase_cap = self.agent.data.get("_phase_cap")
        if phase_cap is not None and detected_phase is not None:
            dp = float(detected_phase)
            if dp not in _VERIFICATION_PHASES:
                from python.helpers.phase_cap import check_phase_cap_allows
                if not check_phase_cap_allows(dp, float(phase_cap)):
                    cap_msg = (
                        f"[PHASE CAP] 🛑 BLOCKED: Delegation to Phase {detected_phase} "
                        f"exceeds phase cap of {phase_cap}. The user prompt restricts "
                        f"execution to Phase {phase_cap} or below. Use the `response` "
                        f"tool to return your current results. Do NOT delegate further work."
                    )
                    logger.warning(cap_msg)
                    return Response(
                        message=cap_msg,
                        break_loop=False,
                    )
            else:
                logger.info(
                    f"[PHASE CAP] Phase {detected_phase} is a verification phase — "
                    f"exempt from cap {phase_cap}, allowing."
                )
    except Exception as _pc_err:
        logger.debug(f"[PHASE CAP] Check failed (non-fatal): {_pc_err}")

    # ── P1-4 + RCA-ITR49: Pre-delegation phase ordering soft-block ──
    # When a higher phase is delegated while lower phases are pending,
    # return a NUDGE response (not hard block) steering the orchestrator
    # to complete the pending phase first. After MAX retries, let through.
    try:
        _pd_project_dir = self.agent.data.get("_active_project_dir", "")
        if _pd_project_dir and detected_phase is not None:
            _check_phase_order_before_delegation(
                detected_phase=detected_phase,
                project_dir=_pd_project_dir,
                agent_data=self.agent.data,
            )
            # If guidance was generated, return it as a nudge response
            _po_guidance = self.agent.data.get("_phase_order_retry_guidance", "")
            if _po_guidance:
                logger.info(
                    f"[PHASE ORDER] Returning nudge response instead of "
                    f"proceeding with Phase {detected_phase} delegation"
                )
                return Response(
                    message=_po_guidance,
                    break_loop=False,
                )
    except Exception as _po_err:
        logger.debug(f"[P1-4] Phase order check failed (non-fatal): {_po_err}")

    # Propagate parent data flags to subordinate (now includes correct phase)
    propagate_data_to_subordinate(self.agent, subordinate, kwargs)

    # Swarm instructions — only for code-related profiles
    CODE_PROFILES = {"code", "frontend", "debug", "architect", "e2e", "review", "ask", "researcher"}
    should_inject_swarm = agent_profile in CODE_PROFILES
    swarm_instructions = self._load_swarm_instructions() if should_inject_swarm else None
    message = inject_swarm_instructions(message, swarm_instructions)

    # ISS-A FIX: Skip legacy scope injection when delegation brief
    # system will run (build_delegation_package is profile-aware and
    # produces scoped, non-redundant context). inject_project_scope
    # only runs as fallback when no project_dir is available.
    project_dir = self.agent.data.get("_active_project_dir", "")
    if not project_dir:
        message = inject_project_scope(message, self.agent)

    # ── DELEGATION PACKAGE: Universal context injection (RCA-Context-Loss) ──
    # Single source of truth for all context injection. The package reads
    # all sources (disk + agent.data + kwargs) and produces ONE structured
    # document per profile config. Scope guard is integrated as first section.
    try:
        project_dir = self.agent.data.get("_active_project_dir", "")
        if project_dir:
            from python.helpers.delegation_brief import build_delegation_package
            message = build_delegation_package(
                profile=agent_profile,
                message=message,
                kwargs=kwargs,
                project_dir=project_dir,
                agent=self.agent,
                subordinate=subordinate,
                phase=detected_phase,  # RCA-ITR36
            )
    except Exception as e:
        logger.warning(f"[DELEGATION PACKAGE] Injection failed (non-fatal): {e}")

    # ── PRE-DELEGATION SECRET EXTRACTION (F-6) ──
    # Extract API keys from the original user prompt and store them in the
    # vault BEFORE the env bridge runs. This ensures secrets provided inline
    # in user messages (e.g., "OPENROUTER_API_KEY=sk-or-v1-xxx") are captured
    # and available for materialization into .env.local files.
    try:
        project_dir = self.agent.data.get("_active_project_dir", "")
        project_name = os.path.basename(project_dir) if project_dir else ""
        if project_name and not self.agent.data.get("_prompt_secrets_extracted"):
            from python.helpers.boomerang_context import get_original_user_message
            from python.helpers.prompt_secret_extractor import (
                extract_secrets_from_text,
                store_extracted_secrets,
            )
            original_msg = get_original_user_message(self.agent)
            if original_msg:
                secrets = extract_secrets_from_text(original_msg)
                if secrets:
                    count = store_extracted_secrets(project_name, secrets)
                    if count > 0:
                        logger.info(
                            f"[PROMPT SECRET EXTRACTOR] Extracted {count} secrets "
                            f"from user prompt for project '{project_name}'"
                        )
            self.agent.data["_prompt_secrets_extracted"] = True
    except Exception as e:
        logger.warning(f"[PROMPT SECRET EXTRACTOR] Failed (non-fatal): {e}")

    # ── PRE-DELEGATION ENV BRIDGE (RCA-346 F-5, F-10 structured result) ──
    # Bridge vault secrets to .env.local before subordinate starts.
    try:
        project_dir = self.agent.data.get("_active_project_dir", "")
        project_name = os.path.basename(project_dir) if project_dir else ""
        if project_dir and project_name:
            from python.helpers.pre_delegation_env_bridge import ensure_env_before_delegation
            bridged = ensure_env_before_delegation(project_dir, project_name)
            if bridged:
                logger.info(f"[ENV BRIDGE] Bridged secrets to .env.local for {project_name}")

            # ── U-1 (ITR-29): Pre-delegation API key health check ──
            # After writing .env.local, validate known API keys with
            # lightweight HTTP health checks. Invalid keys are stored in
            # bridged.invalid_keys so build_env_var_section() warns the
            # subordinate to use mock/stub patterns instead.
            if bridged and bridged.written_keys:
                try:
                    from python.helpers.pre_delegation_env_bridge import validate_api_keys
                    # Read actual key values from .env.local (written_keys only has names)
                    env_path = os.path.join(project_dir, ".env.local")
                    env_vars = {}
                    if os.path.isfile(env_path):
                        with open(env_path, "r") as f:
                            for line in f:
                                line = line.strip()
                                if "=" in line and not line.startswith("#"):
                                    k, _, v = line.partition("=")
                                    k = k.strip()
                                    if k in bridged.written_keys:
                                        env_vars[k] = v.strip()
                    if env_vars:
                        health = validate_api_keys(env_vars)
                        invalid_keys = [
                            k for k, v in health.items()
                            if v.get("valid") is False
                        ]
                        if invalid_keys:
                            bridged.invalid_keys = invalid_keys
                            logger.warning(
                                f"[ENV BRIDGE U-1] Invalid API keys for "
                                f"{project_name}: {invalid_keys}"
                            )
                except Exception as health_err:
                    logger.debug(
                        f"[ENV BRIDGE U-1] Health check failed (non-fatal): {health_err}"
                    )

            if bridged.missing_keys:
                logger.warning(
                    f"[ENV BRIDGE] Missing secrets for {project_name}: "
                    f"{bridged.missing_keys}"
                )
            # SS-5 (ITR-23): Inject written_keys into delegation message
            # so the subordinate knows which env vars are available.
            # FIX-4: Also inject when missing_keys present (not just written_keys)
            if bridged and (bridged.written_keys or bridged.missing_keys):
                from python.helpers.pre_delegation_env_bridge import build_env_var_section
                env_section = build_env_var_section(bridged)
                if env_section:
                    message = message + "\n\n" + env_section

    except Exception as e:
        logger.warning(f"[ENV BRIDGE] Failed (non-fatal): {e}")


    # Attachment injection (Forgejo #977)
    message = self._inject_attachment_context(message)

    # Write error tracking log to memory bank (RCA-339 Part 4)
    try:
        project_dir = self.agent.data.get("_active_project_dir", "")
        if project_dir:
            write_error_tracking_log(self.agent.data, project_dir)
    except Exception as e:
        logger.warning(f"Error tracking log write failed (non-fatal): {e}")

    # ── C-1 (RCA-354): Bridge bdd_specs from tool kwargs → agent.data ──
    bdd_specs_from_kwargs = kwargs.get("bdd_specs", [])
    if bdd_specs_from_kwargs:
        self.agent.data["_test_specs"] = bdd_specs_from_kwargs
        logger.info(f"[C-1 BRIDGE] bdd_specs bridged to _test_specs: {len(bdd_specs_from_kwargs)} specs")

    # ── GAP-2 FIX: BDD fallback injection (defense-in-depth) ──
    # Root cause: inject_bdd_fallback existed in delegation_message.py but
    # was NEVER called from call_subordinate.py. When the orchestrator
    # forgot to pass bdd_specs in kwargs, code agents received ZERO BDD
    # context — even though bdd-scenarios.md existed on disk.
    # This fallback reads bdd-scenarios.md when bdd_specs aren't provided.
    if kwargs.get("profile", "") in ("code", "frontend", "e2e"):
        try:
            project_dir = self.agent.data.get("_active_project_dir", "")
            if project_dir and not bdd_specs_from_kwargs:
                from python.helpers.delegation_message import inject_bdd_fallback
                requirement_ids = kwargs.get("requirement_ids", [])
                message = inject_bdd_fallback(
                    message=message,
                    project_dir=project_dir,
                    bdd_specs=bdd_specs_from_kwargs or None,
                    requirement_ids=requirement_ids or None,
                )
        except Exception as e:
            logger.debug(f"[GAP-2] BDD fallback injection skipped: {e}")

    # H-3 (RCA-354): Generate test skeletons at delegation time
    if kwargs.get("profile", "") in ("code", "frontend"):
        # FIX-3: Warn if delegating to code agent without BDD scenarios
        if not self.agent.data.get("_bdd_skeleton_generated") and \
           not self.agent.data.get("_test_skeleton_generated"):
            logger.warning(
                "[H-3] DELEGATION WITHOUT BDD: Delegating to %s agent "
                "but neither BDD nor test skeleton has been generated. "
                "Code agent will operate without structured requirements.",
                kwargs.get("profile", ""),
            )
        try:
            project_dir = self.agent.data.get("_active_project_dir", "")
            if project_dir:
                from python.helpers.skeleton_generator import generate_bdd_skeleton
                # ITR-20 F-1: Check for enriched BDD before regenerating
                bdd_validation = os.path.join(project_dir, "docs", ".bdd_validation.json")
                if not os.path.isfile(bdd_validation):
                    bdd = generate_bdd_skeleton(project_dir)
                    if bdd:
                        self.agent.data["_bdd_skeleton_generated"] = True
                else:
                    logger.info("[H-3] Skipping BDD skeleton — .bdd_validation.json exists")
        except Exception as e:
            logger.debug(f"[H-3] Pre-delegation skeleton gen skipped: {e}")

    # ── FIX-1 (RCA-ITR49 SS-1): Contract assertion injection ──
    # Root cause: inject_contract_assertions existed in delegation_message.py:242
    # but was NEVER called from call_subordinate_execute.py — same bug class
    # as the BDD fallback fix at line 623 above. This caused $200/mo → $X/mo,
    # missing happy/unhappy routing, and 6 other prompt features to be lost.
    # The function extracts URLs, prices, model names, and behaviors from the
    # original prompt and injects them as VERBATIM VALUES into the delegation.
    try:
        message = inject_contract_assertions(message, self.agent)
    except Exception as e:
        logger.debug(f"[FIX-1] Contract assertion injection skipped: {e}")


    # Sanitize delegation message — strip tool refs outside target profile
    try:
        from python.helpers.delegation_sanitizer import sanitize_delegation_message
        message = sanitize_delegation_message(message, agent_profile)
    except Exception:
        pass  # Fail-open

    # ── ISS-A: Scope guard now integrated into build_delegation_package ──
    # as the FIRST section, ensuring fix-mode framing has primacy.
    # Standalone call removed to avoid double-injection.


    await subordinate.hist_add_user_message(
        UserMessage(message=message, attachments=[]),
        sender_type="agent",
        sender_id=self.agent.agent_name
    )

    # Trace subordinate creation
    AgentTracer.trace_subordinate_created(
        parent_agent=self.agent,
        subordinate_agent=subordinate,
        mission=message
    )

    # ── Isolate subordinate's chain counter from parent ──
    # The chain counter lives on the shared AgentContext. Without isolation,
    # a subordinate burning 75 iterations consumes the parent's entire budget,
    # causing every subsequent subordinate to immediately hit the limit.
    saved_chain_count = self.agent.context._chain_monologue_iterations

    # ── ARCH-RCSIG: Pre-delegation file snapshot for IMPLEMENTATION phases ──
    # Capture current source files BEFORE the subordinate runs so we can
    # compute a delta afterwards (what files were actually created?).
    # Only for implementation phases (3.x) to avoid unnecessary I/O.
    pre_delegation_files = None
    try:
        from python.helpers.phase_category import is_implementation_phase
        from python.helpers.implementation_completion_validator import take_file_snapshot
        project_dir = self.agent.data.get("_active_project_dir", "")
        if project_dir and detected_phase is not None and is_implementation_phase(detected_phase):
            pre_delegation_files = take_file_snapshot(project_dir)
            logger.info(
                f"[ARCH-RCSIG] Pre-delegation snapshot: {len(pre_delegation_files)} files "
                f"(phase {detected_phase}, project={project_dir})"
            )
    except Exception as snap_err:
        logger.debug(f"[ARCH-RCSIG] Pre-delegation snapshot failed (non-fatal): {snap_err}")

    # Run subordinate monologue with rate limit coordination
    # Wrap in ensure_future to get a Task handle for TaskRegistry
    subordinate_coro = self._run_subordinate_with_coordination(subordinate, provider_key)
    subordinate_task = asyncio.ensure_future(subordinate_coro)

    # Register in TaskRegistry so the supervisor IO-Breaker can target it
    try:
        from python.helpers.task_registry import TaskRegistry
        registry = TaskRegistry.instance()
        context_id = getattr(subordinate.context, 'id', 'unknown') if subordinate.context else 'unknown'
        composite_id = f"{subordinate.agent_name}@{context_id}"
        registry.register_task(composite_id, subordinate_task)
        logger.debug(f"[CALL SUBORDINATE] Registered task: {composite_id}")
    except Exception as e:
        logger.debug(f"[CALL SUBORDINATE] TaskRegistry registration skipped: {e}")

    # ── BLOCKED-IN-TOOL SIGNAL (RCA: MSR_Smoke_1777469132) ──
    # Signal to the supervisor that this agent is in a blocking wait.
    # detect_dead_agents() Gate 6 checks _blocked_in_tool (any truthy value)
    # and skips the agent, preventing false-positive "dead agent" detection.
    #
    # We provide structured context (not just True) so the supervisor knows
    # WHAT is running and can assess expected duration intelligently:
    # - "call_subordinate" with architect/e2e profiles → expect 3-10+ minutes
    # - "call_subordinate" with code profiles → expect 1-5 minutes
    # - generate_image, browser_agent → expect 30-120 seconds
    import time as _time
    self.agent.data["_blocked_in_tool"] = {
        "tool": "call_subordinate",
        "subordinate_profile": getattr(subordinate.config, "profile", "unknown"),
        "subordinate_name": subordinate.agent_name,
        "started_at": _time.time(),
        "complexity": "high",  # delegation is always high-complexity
    }
    try:
        result = await subordinate_task
    finally:
        self.agent.data["_blocked_in_tool"] = False

    # Cleanup from TaskRegistry
    try:
        from python.helpers.task_registry import TaskRegistry
        TaskRegistry.instance().cleanup_done()
    except Exception as e:
        logger.debug(f"[CALL SUBORDINATE] TaskRegistry cleanup failed: {e}")

    # Restore parent's chain counter — subordinate iterations shouldn't count
    self.agent.context._chain_monologue_iterations = saved_chain_count

    # ── U-4: Cleanup subordinate ports on PARTIAL/CANCELLED ──────────
    # When a subordinate fails, its dev server may still be running on an
    # allocated port. This prevents EADDRINUSE for replacement subordinates.
    _cleanup_subordinate_ports(subordinate, result)

    # ── RCA-347 + RCA-354 I-2: COERCE Response TO str AT BOUNDARY ──
    # F-1 fix (RCA-346) changed monologue() to return Response objects
    # for the 'response' tool. All downstream code expects str.
    # RCA-354 I-2: When break_loop=False, the subordinate's response was
    # internally rejected (near-dup, fidelity gate). The coerced text is
    # the rejection message, NOT the agent's work. Tag it with
    # [RESPONSE_REJECTED] so build_delegation_result classifies as "partial".
    if isinstance(result, Response):
        logger.info(
            f"[RCA-347] Coercing Response→str from subordinate "
            f"{subordinate.agent_name} (break_loop={result.break_loop})"
        )
        from python.helpers.response_coercion import coerce_subordinate_response
        result = coerce_subordinate_response(result)

    # ── RESULT HANDLING (delegated to delegation_result_processing module) ──
    if result is None:
        result = handle_none_result(subordinate)
        logger.warning(f"Subordinate {subordinate.agent_name} returned None result")
    elif isinstance(result, str):
        # U-13 Fix: Use centralized sentinel_registry instead of hardcoded list
        from python.helpers.sentinel_registry import get_limit_tags
        _limit_tags = get_limit_tags()
        if any(tag in result for tag in _limit_tags):
            result = handle_limit_tags(result, subordinate)

    # ── DATA PROPAGATION: subordinate → parent (delegated) ──
    propagate_subordinate_data(self.agent, subordinate)

    # ── F-ERR-4: Relay subordinate error context to parent ──
    try:
        from python.helpers.universal_error_manager import relay_subordinate_errors
        relay_subordinate_errors(self.agent, subordinate)
    except Exception as relay_err:
        logger.debug(f"relay_subordinate_errors failed (non-fatal): {relay_err}")

    # ── E2E FAIL → Code Fix Routing (#iter73) ──
    # Keep inline — tightly coupled with sub_quality reference
    sub_quality = subordinate.data.get("_quality_evaluation")
    result = _inject_e2e_fail_routing(result, sub_quality)
    if sub_quality and not sub_quality.get('passed', True):
        logger.info(
            f"[E2E_FAIL_ROUTER] Injected fix routing hint into result "
            f"(quality_evaluation.passed=False)"
        )
    elif "QUALITY: FAIL" in (result or "") and not sub_quality:
        logger.info(
            "[E2E_FAIL_ROUTER] Detected QUALITY: FAIL in result text, "
            "injected routing hint"
        )

    # Trace subordinate completion
    AgentTracer.trace_subordinate_completed(
        parent_agent=self.agent,
        subordinate_agent=subordinate,
        result=result
    )

    # ADR-83: Previously hinted agents to use §§include(<file>) for long responses,
    # but the file path points at /agix/tmp/chats/ (Zone 1: framework-internal).
    # Agents must NEVER reference Zone 1 paths (ADR-83 R-1).
    # The content is already returned inline in the delegation result — no hint needed.
    additional = None

    # If relay_response is set, emit the subordinate's result as this agent's response
    # This allows the parent agent to present subordinate output to the user without
    # needing an extra loop iteration (break_loop stays True for safety)
    if str(relay_response).lower().strip() == "true" and result:
        log_item = self.agent.context.log.log(
            type="response",
            heading=f"icon://chat {self.agent.agent_name}: Responding",
            content=result,
        )
        log_item.update(finished=True)

    # Append boomerang context — reminds parent of original user's
    # completion requirements (markers, format, sign-off)
    # DEDUP: Strip any existing boomerangs from child results first,
    # then append exactly one clean boomerang.
    #
    # RCA FIX: Detect subordinate failure BEFORE building the boomerang
    # so all_tasks_succeeded reflects reality. Without this, the boomerang
    # always said "SUCCESSFULLY" even when the subordinate hit limits or
    # errors — contradicting the DelegationResult "Status: PARTIAL" header
    # and causing the orchestrator to repeat the same failing delegation.
    try:
        from python.helpers.boomerang_context import strip_boomerang, has_boomerang, strip_completion_markers, is_error_result
        result = strip_boomerang(result)
        # Strip [[COMPLETION_MARKERS]] — these are for the ROOT agent's
        # final response only, not for subordinate→parent relay (#866)
        result = strip_completion_markers(result)
        # Detect if subordinate actually succeeded or failed
        subordinate_failed = is_error_result(result)
        boomerang = _get_boomerang_context(
            self.agent,
            calling_agent_name=getattr(self.agent, 'agent_name', ''),
            all_tasks_succeeded=not subordinate_failed,
        )
        if boomerang:
            result = result + boomerang
    except Exception as e:
        logger.warning(f"Failed to append boomerang context: {e}")


    # ── BUILD STRUCTURED DELEGATION RESULT (delegated) ──
    delegation_result = build_delegation_result(
        result=result,
        subordinate=subordinate,
        kwargs=kwargs,
    )
    self.agent.data["_last_delegation_result"] = delegation_result.to_dict()

    # ── FIX-4: Append to cumulative delegation result ledger ──
    # Unlike _last_delegation_result (which only keeps the LAST result),
    # this ledger accumulates ALL results so the fidelity gate and
    # completion gate can cross-reference orchestrator claims.
    append_to_result_ledger(self.agent.data, {
        "status": delegation_result.status,
        "profile": delegation_result.profile,
        "result_preview": (result or "")[:500],
        "result_length": len(result) if result else 0,
        "task_hash": delegation_result.task_hash,
        "task_guid": delegation_result.task_guid,
        "errors": delegation_result.errors[:3] if delegation_result.errors else [],
        # RCA-345 FIX-4: Include phase_seq so the delegation-based
        # fallback reconciler in _reconcile_decomp_statuses can match
        # ledger entries to decomposition phases.
        "phase_seq": str(detected_phase) if detected_phase is not None else "",
    })

    # ── POST-DELEGATION GATES: quality, failure classification, wrong-profile ──
    result = run_post_delegation_gates(
        delegation_result=delegation_result,
        result=result,
        subordinate=subordinate,
        agent=self.agent,
    )

    # ── ITR-14 R2-I1: DELIVERABLE RESCUE ──
    # When a subordinate returns content inline instead of using
    # save_deliverable, rescue the missing file by persisting the
    # response text to the expected deliverable path.
    try:
        project_dir = self.agent.data.get("_active_project_dir", "")
        sub_profile = getattr(subordinate.config, "profile", "")
        if project_dir and sub_profile:
            from python.helpers.deliverable_rescue import rescue_missing_deliverables
            rescued = rescue_missing_deliverables(
                project_dir=project_dir,
                profile=sub_profile,
                result_text=result if isinstance(result, str) else "",
                delegation_status=delegation_result.status,
            )
            if rescued:
                logger.info(
                    f"[DELIVERABLE RESCUE] Rescued {len(rescued)} missing deliverables: {rescued}"
                )
    except Exception as e:
        logger.warning(f"[DELIVERABLE RESCUE] Failed (non-fatal): {e}")

    # ── FAILURE RECORDING: error relay + n-attempt tracker ──
    if delegation_result.status in ("failed", "partial"):
        await record_delegation_failure(
            delegation_result=delegation_result,
            result=result,
            agent=self.agent,
            kwargs=kwargs,
            message=message,
        )

    # Log structured completion
    logger.info(
        f"DelegationResult: status={delegation_result.status}, "
        f"profile={delegation_result.profile}, "
        f"iterations={delegation_result.iterations}, "
        f"errors={len(delegation_result.errors)}, "
        f"artifacts={len(delegation_result.artifacts)}"
    )

    # ── FIX-017: Track delegation outcome for cycle detection ──
    try:
        from python.helpers.delegation_cycle_detector import track_delegation_outcome
        _phase_key = kwargs.get("phase", "unknown")
        track_delegation_outcome(
            self.agent.data,
            _phase_key,
            delegation_result.status,
        )
    except Exception as _track_err:
        logger.debug(f"[FIX-017] Outcome tracking failed (non-fatal): {_track_err}")

    # ── FIX 2.3b: Quarantine delegation output on cross-delegation spiral ──
    # When DETECTOR 10 (cross_delegation_spiral) has fired, the failed
    # delegation's text result should be quarantined so the orchestrator
    # LLM cannot use the poisoned output for downstream work.
    # NOTE: propagate_subordinate_data() already ran (line ~828) — data
    # propagation (tool failures, blocked tools, etc.) is preserved.
    # Only the TEXT RESULT seen by the orchestrator is replaced.
    try:
        l2_signals = self.agent.data.get("_l2_escalation_signals", [])
        spiral_active = any(
            s.get("detector") == "cross_delegation_spiral"
            for s in l2_signals if isinstance(s, dict)
        )
        if spiral_active and delegation_result.status in ("failed", "partial"):
            from python.helpers.delegation_result_processing import quarantine_delegation_result
            result = quarantine_delegation_result(
                agent_data=self.agent.data,
                delegation_result=delegation_result,
                result=result,
                reason="Cross-delegation spiral detected — same root cause repeated across subordinates",
            )
            # Update delegation_result.result so to_string() renders the
            # quarantine message, not the original poisoned output.
            delegation_result.result = result
            logger.warning(
                f"[QUARANTINE] Delegation output quarantined — "
                f"cross_delegation_spiral active, profile={delegation_result.profile}"
            )
    except Exception as e:
        logger.warning(f"[QUARANTINE] Failed (non-fatal): {e}")

    # Use to_string() for backward-compatible output
    structured_result = delegation_result.to_string()

    # Propagate subordinate's search capability to parent (moved from
    # _25_subordinate_continuation.py which is being removed).
    # The search grounding gate tracks _search_tools_used per-agent.
    # When the parent delegates via call_subordinate, the subordinate
    # performs the actual searching — mark the parent as grounded.
    parent_search = self.agent.data.get("_search_tools_used", set())
    parent_search.add("call_subordinate")
    self.agent.data["_search_tools_used"] = parent_search

    # ── RCA-345 FIX-2: DETERMINISTIC PHASE COMPLETION ────────────────
    # The delegation tool KNOWS which phase it delegated. When the
    # subordinate returns, deterministically mark the phase as completed
    # in decomposition_index.json. No LLM honor system, no reconciler.
    try:
        project_dir = self.agent.data.get("_active_project_dir", "")
        if project_dir and detected_phase is not None:
            import json as _json
            from python.helpers.phase_parser import mark_decomp_phase_completed
            from python.helpers.orchestrator_gate_common import detect_force_accepted_result
            from python.helpers.projects import get_decomp_index_path
            from python.helpers.phase_completion_guard import validate_phase_completion
            decomp_path = get_decomp_index_path(project_dir)
            if os.path.isfile(decomp_path):
                with open(decomp_path, "r", encoding="utf-8") as f:
                    decomp_data = _json.load(f)
                # Handle both list and dict formats
                phases_list = decomp_data
                if isinstance(decomp_data, dict):
                    phases_list = (
                        decomp_data.get("tasks")
                        or decomp_data.get("milestones")
                        or decomp_data.get("phases")
                        or []
                    )
                if isinstance(phases_list, list):
                    force_accepted = detect_force_accepted_result(result if isinstance(result, str) else "")
                    # ARCH-RCSIG: Category-aware phase completion validation.
                    # Replaces bare should_skip_phase_completion(status) with
                    # validate_phase_completion() that checks:
                    #   Layer 1: delegation status (same as before)
                    #   Layer 2: implementation phases must create files
                    completion_check = validate_phase_completion(
                        delegation_status=delegation_result.status,
                        phase_seq=str(detected_phase),
                        project_dir=project_dir,
                        pre_delegation_files=pre_delegation_files,  # from snapshot
                    )
                    if completion_check.should_skip:
                        logger.warning(
                            f"[ARCH-RCSIG] Phase {detected_phase} completion BLOCKED: "
                            f"{completion_check.reason} → status={completion_check.recommended_status}"
                        )
                        # Set the recommended status on the phase
                        for p in phases_list:
                            seq = p.get("phase_seq", p.get("sequence", ""))
                            if str(seq) == str(detected_phase):
                                p["status"] = completion_check.recommended_status
                                p["completion_evidence"] = completion_check.reason
                                break
                        # Write back even for blocked completions
                        if isinstance(decomp_data, dict):
                            for key in ("tasks", "milestones", "phases"):
                                if key in decomp_data:
                                    decomp_data[key] = phases_list
                                    break
                        else:
                            decomp_data = phases_list
                        with open(decomp_path, "w", encoding="utf-8") as f:
                            _json.dump(decomp_data, f, indent=2)
                    else:
                        # Validation passed — proceed with completion
                        completion_result = mark_decomp_phase_completed(
                            phases_list,
                            str(detected_phase),
                            evidence=f"delegation returned (status={delegation_result.status}, "
                                     f"profile={delegation_result.profile})",
                            force_accepted=force_accepted,
                            project_dir=self.agent.data.get("_active_project_dir") or "",
                        )
                        if completion_result["found"]:
                            # Write back to disk — single source of truth
                            if isinstance(decomp_data, dict):
                                # Preserve dict wrapper
                                for key in ("tasks", "milestones", "phases"):
                                    if key in decomp_data:
                                        decomp_data[key] = phases_list
                                        break
                            else:
                                decomp_data = phases_list
                            with open(decomp_path, "w", encoding="utf-8") as f:
                                _json.dump(decomp_data, f, indent=2)
                            logger.info(
                                f"[ARCH-RCSIG] Phase {completion_result['phase_seq']} "
                                f"auto-completed → {completion_result['new_status']} "
                                f"(delegation returned from {delegation_result.profile})"
                            )
                        # Store evidence on agent.data for gate consumption
                        if completion_check.evidence:
                            evidence_key = f"_phase_evidence_{detected_phase}"
                            self.agent.data[evidence_key] = completion_check.evidence.to_dict()

                        # RCA-461 P0 FIX: TDD generation fallback on delegation completion.
                        # generate_tdd_tests() was previously ONLY callable from the
                        # reconciler's BDD gate pass branch (requirements_sync.py:1013).
                        # When the monotonicity guard blocked Phase 2 auto-completion,
                        # TDD stubs were NEVER generated — leaving code agents without
                        # literal-assertion tests (e.g., no toContain('$200/mo')).
                        # This fallback fires when a Phase 2.x delegation completes
                        # and docs/tdd/ doesn't exist yet.
                        try:
                            phase_float = float(str(detected_phase).split(".")[0])
                            project_dir = self.agent.data.get("_active_project_dir", "")
                            tdd_dir = os.path.join(project_dir, "docs", "tdd") if project_dir else ""
                            if (
                                phase_float == 2
                                and project_dir
                                and not os.path.isdir(tdd_dir)
                            ):
                                from python.helpers.skeleton_generator import (
                                    generate_tdd_tests,
                                    generate_test_skeleton,
                                )
                                # Regenerate skeleton with manifest values first
                                try:
                                    generate_test_skeleton(project_dir)
                                    logger.info(
                                        "[ARCH-RCSIG] RCA-461: Regenerated test skeleton "
                                        "with manifest values (delegation completion fallback)"
                                    )
                                except Exception as skel_err:
                                    logger.debug(
                                        f"[ARCH-RCSIG] Skeleton re-gen skipped: {skel_err}"
                                    )
                                # Generate TDD stubs
                                tdd_results = generate_tdd_tests(project_dir)
                                if tdd_results:
                                    logger.info(
                                        f"[ARCH-RCSIG] RCA-461: Auto-generated "
                                        f"{len(tdd_results)} TDD test modules "
                                        f"(delegation completion fallback for Phase "
                                        f"{detected_phase})"
                                    )
                        except (ValueError, TypeError, ImportError) as tdd_err:
                            logger.debug(
                                f"[ARCH-RCSIG] TDD fallback skipped: {tdd_err}"
                            )
    except Exception as e:
        logger.warning(f"[ARCH-RCSIG] Auto-phase-completion failed (non-fatal): {e}")

    # ── T3: Record attempt in phase attempt ledger ─────────────────────
    # After completion validation, record what this delegation produced
    # so build_remediation_brief() can construct scoped fix delegations.
    try:
        if detected_phase is not None:
            # completion_check may not be set if ARCH-RCSIG block failed
            _t3_completion_check = locals().get("completion_check")
            _t3_attempt = _build_attempt_record(
                detected_phase=detected_phase,
                delegation_result=delegation_result,
                completion_check=_t3_completion_check,
                pre_delegation_files=pre_delegation_files,
                project_dir=self.agent.data.get("_active_project_dir", ""),
            )
            if _t3_attempt:
                from python.helpers.phase_attempt_ledger import record_attempt
                record_attempt(self.agent.data, str(detected_phase), _t3_attempt)
    except Exception as _t3_err:
        logger.debug(f"[T3] Phase attempt recording failed (non-fatal): {_t3_err}")

    # result — break_loop=False so the parent agent decides autonomously.

    # The parent has full context (chat history + this tool result) and
    # will call the `response` tool when it's ready, or delegate further.
    # This eliminates the need for the continuation/echo-suppression chain
    # that caused duplicate responses.
    return Response(message=structured_result, break_loop=False, additional=additional)

async def run_subordinate_with_coordination(self, subordinate: Agent, provider_key: str) -> str:
    """
    Run subordinate monologue with rate limit coordination,
    per-subordinate timeout, and deterministic retry for
    non-rate-limit transient errors (#1161 GAP-6).
    
    Timeout: Controlled by AGIX_SUBORDINATE_TIMEOUT env var (default 600s).
    When a subordinate hangs (e.g., stuck shell heredoc, dead HTTP connection),
    the timeout fires and returns a graceful error message to the orchestrator.
    
    Retry strategy:
    - Rate limit errors: up to 10 retries with exponential backoff (existing)
    - Transient errors (timeout, 5xx, connection): up to 2 retries with error context
    - Logic errors: 1 retry with error context
    - Permanent errors (auth, loop, context): fail immediately
    """
    from python.helpers.retry_strategy import classify_error, should_retry, build_retry_prompt, wait_before_retry, calculate_rate_limit_backoff
    from python.helpers.subordinate_timeout import (
        _run_with_activity_timeout,
        _extract_completed_work,
        _build_timeout_message,
    )
    import time as _time
    
    # ── Per-subordinate timeout (prevents indefinite hangs) ──
    # RCA-365 F-6a: Accept dynamic budget from orchestrator via tool args.
    # RCA hard-timeout-loop: Profile-aware defaults for different agent roles.
    _subordinate_profile = getattr(subordinate.config, "profile", "") or ""
    _subordinate_timeout = _get_subordinate_timeout(self.args, profile=_subordinate_profile)
    _idle_timeout = float(os.environ.get("AGIX_SUBORDINATE_IDLE_TIMEOUT", "120"))
    
    # Increased max retries for better resilience
    max_rate_limit_retries = 10
    retry_count = 0
    total_wait_time = 0.0
    
    # GAP-6: Non-rate-limit retry state
    non_rl_attempt = 0
    original_message = None  # Captured on first non-RL retry
    
    # ── LIFECYCLE LOGGING: Track monologue start for observability ──
    _monologue_start = _time.time()
    logger.info(
        f"[SUBORDINATE LIFECYCLE] Starting monologue for {subordinate.agent_name} "
        f"(profile={getattr(subordinate.config, 'profile', '?')}, "
        f"timeout={_subordinate_timeout:.0f}s, "
        f"context={getattr(subordinate.context, 'id', '?')})"
    )
    
    while retry_count < max_rate_limit_retries:
        try:
            # Check for global backoff before each attempt
            wait_time = await RateLimiter.get_global_wait_time(provider_key)
            if wait_time > 0:
                # Silent wait - debug level logging only
                logger.debug(
                    f"Subordinate {subordinate.agent_name} waiting {wait_time:.1f}s "
                    f"for rate limit coordination"
                )
                await asyncio.sleep(wait_time)
                total_wait_time += wait_time
            
            # ── Run the subordinate monologue WITH ACTIVITY-AWARE TIMEOUT ──
            # RCA-330: Replaced blind asyncio.wait_for with activity-aware
            # timeout. The idle timer resets on every tool execution, so
            # legitimate long-running tools (npm build) don't burn budget.
            # Hard cap remains as safety net.
            try:
                result = await _run_with_activity_timeout(
                    subordinate,
                    timeout_seconds=_subordinate_timeout,
                    idle_timeout=_idle_timeout,
                )
            except asyncio.TimeoutError as _timeout_err:
                elapsed = _time.time() - _monologue_start
                timeout_reason = str(_timeout_err)
                logger.error(
                    f"[SUBORDINATE LIFECYCLE] ⏰ {subordinate.agent_name} TIMED OUT "
                    f"after {elapsed:.1f}s (hard_cap={_subordinate_timeout:.0f}s, "
                    f"idle_cap={_idle_timeout:.0f}s, reason={timeout_reason}, "
                    f"profile={getattr(subordinate.config, 'profile', '?')}, "
                    f"context={getattr(subordinate.context, 'id', '?')})"
                )
                # Log to UI for visibility
                if hasattr(self.agent, 'log'):
                    self.agent.log(
                        type="warning",
                        heading=f"⏰ Subordinate {subordinate.agent_name} Budget Exceeded",
                        content=(
                            f"Subordinate exceeded time budget after {elapsed:.0f}s. "
                            f"Reason: {timeout_reason}. "
                            f"Extracting completed work for re-delegation."
                        ),
                    )
                # ── GRACEFUL SHUTDOWN: Let finally blocks run ──
                await asyncio.sleep(0.5)
                
                # ── WORK PRESERVATION: Build informative timeout message ──
                completed_work = _extract_completed_work(subordinate)
                return _build_timeout_message(
                    subordinate_name=subordinate.agent_name,
                    timeout_seconds=_subordinate_timeout,
                    completed_work=completed_work,
                )
            
            # ── LIFECYCLE LOGGING: Track completion ──
            elapsed = _time.time() - _monologue_start
            logger.info(
                f"[SUBORDINATE LIFECYCLE] {subordinate.agent_name} completed "
                f"monologue in {elapsed:.1f}s "
                f"(profile={getattr(subordinate.config, 'profile', '?')})"
            )
            
            # Store rate limit stats if any retries occurred
            if retry_count > 0:
                self._store_rate_limit_stats(retry_count, total_wait_time)
            
            return result
            
        except Exception as e:
            # ── TRUNCATION RECOVERY (Issue #1139) ──
            # Catch TruncationException FIRST so partial data is preserved
            # and the parent agent gets actionable instructions instead of
            # a generic tool error that wastes 2-3 retry cycles.
            from python.helpers.errors import TruncationException as _TruncEx
            if isinstance(e, _TruncEx):
                partial = getattr(e, 'partial_response', '') or ''
                model_name = getattr(e, 'model', 'unknown')
                
                logger.warning(
                    f"Subordinate {subordinate.agent_name} truncated by {model_name}. "
                    f"Recovering {len(partial)} chars of partial data."
                )
                
                recovery_msg = (
                    f"⚠️ SUBORDINATE RESPONSE TRUNCATED by {model_name}.\n\n"
                    "The subordinate's output exceeded the model's output token limit. "
                    "This is a known Gemini model behavior — it stops early despite a 65K limit.\n\n"
                    "## MANDATORY RECOVERY STEPS:\n"
                    "1. Use `save_deliverable` to save ALL collected data to a file FIRST\n"
                    "2. Then use `response` with a SHORT summary (under 2000 chars) referencing the saved file\n"
                    "3. Do NOT try to include all data inline — always save to file for large datasets\n\n"
                    "## PARTIAL DATA RECOVERED FROM SUBORDINATE:\n"
                )
                
                if partial and len(partial) > 50:
                    # Include up to 3000 chars of partial data so agent can save it
                    recovery_msg += f"```\n{partial[:3000]}\n```\n"
                    if len(partial) > 3000:
                        recovery_msg += f"\n[...truncated, {len(partial)} total chars recovered]\n"
                else:
                    recovery_msg += "(No partial data recovered — subordinate must re-collect data in smaller chunks)\n"
                
                return recovery_msg

            # Check if this is a rate limit error
            from python.models import _is_rate_limit_error, _extract_retry_after
            
            if _is_rate_limit_error(e):
                retry_count += 1
                
                # SS-11: Use rate-limit-specific exponential backoff with
                # guaranteed minimum delays (equal jitter, not full jitter).
                # Old code used calculate_retry_delay(full jitter) which
                # could produce near-zero delays, causing retry storms.
                retry_after = _extract_retry_after(e)
                if retry_after:
                    delay = max(retry_after, 2.0)  # Floor at 2s even with retry-after
                else:
                    delay = calculate_rate_limit_backoff(retry_count - 1)  # 0-indexed
                
                total_wait_time += delay
                
                # Update global backoff state for coordination
                from python.helpers.rate_limiter import RateLimitState
                await RateLimiter._set_global_state(provider_key, RateLimitState.BACKING_OFF, delay)
                
                # Debug level logging - not shown in UI
                logger.debug(
                    f"Subordinate {subordinate.agent_name} rate limited, "
                    f"retry {retry_count}/{max_rate_limit_retries}, waiting {delay:.1f}s"
                )
                
                # Update stats silently
                self._store_rate_limit_stats(retry_count, total_wait_time)
                
                if retry_count >= max_rate_limit_retries:
                    # Raise with a cleaner error message
                    raise Exception(
                        f"Rate limit: waited {total_wait_time:.0f}s over {retry_count} retries"
                    )
                
                await asyncio.sleep(delay)
            else:
                # ── GAP-6: DETERMINISTIC RETRY FOR NON-RATE-LIMIT ERRORS (#1161) ──
                # Instead of immediately re-raising, classify the error and decide
                # whether to retry with error context injection.
                error_str = str(e)
                error_category = classify_error(error_str)
                
                if should_retry(error_str, non_rl_attempt):
                    non_rl_attempt += 1
                    logger.warning(
                        f"GAP-6 retry: subordinate {subordinate.agent_name} failed with "
                        f"{error_category} error (attempt {non_rl_attempt}): {error_str[:200]}"
                    )
                    
                    # Wait before retry (transient errors have delay, logic errors don't)
                    await wait_before_retry(error_str, non_rl_attempt - 1)
                    
                    # Inject error context into subordinate's history so it can
                    # take a different approach on retry
                    try:
                        from python.helpers.boomerang_context import get_original_user_message
                        _retry_prompt = get_original_user_message(self.agent) or "(see previous messages)"
                    except Exception:
                        _retry_prompt = "(see previous messages)"
                    retry_context = build_retry_prompt(
                        original_prompt=_retry_prompt,
                        error_summary=truncate_output_middle_out(error_str, max_chars=500, head_ratio=0.3),
                        error_context=f"Error category: {error_category}",
                    )
                    await subordinate.hist_add_user_message(
                        UserMessage(message=retry_context, attachments=[]),
                        sender_type="system",
                        sender_id="retry_strategy"
                    )
                    
                    # Tag subordinate as retrying for loop detector sensitivity
                    subordinate.data["_is_retrying"] = True
                    subordinate.data["_retry_attempt"] = non_rl_attempt
                    
                    # Log for tracing
                    self.agent.data["_last_retry_info"] = {
                        "error_category": error_category,
                        "attempt": non_rl_attempt,
                        "error_preview": error_str[:200],
                        "subordinate": subordinate.agent_name,
                    }
                    
                    # Continue the while loop to retry
                    continue
                else:
                    # Non-retryable or exhausted retries — log and re-raise
                    logger.warning(
                        f"GAP-6: NOT retrying subordinate {subordinate.agent_name} — "
                        f"error_category={error_category}, attempt={non_rl_attempt}: {error_str[:200]}"
                    )
                    raise
    
    # Should not reach here, but just in case
    raise Exception(f"Rate limit: waited {total_wait_time:.0f}s over {retry_count} retries")
