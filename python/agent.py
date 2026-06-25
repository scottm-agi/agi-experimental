"""
Agent Module - Facade Pattern Implementation

This module serves as the main entry point for the agent infrastructure.
The core classes (AgentContext, AgentConfig, UserMessage, LoopData, etc.)
are implemented in the python.helpers.agent_core package and re-exported
here for backwards compatibility.

The Agent class remains in this file as it contains the main message loop
and is the primary interface for agent operations.

Package Structure:
    python/helpers/agent_core/
    ├── __init__.py    # Re-exports all modules
    ├── base.py        # Constants, enums, exceptions
    ├── config.py      # AgentConfig, UserMessage, LoopData
    └── context.py     # AgentContext class
"""
from __future__ import annotations

import asyncio
from python.helpers.hashing import content_hash
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Tuple,
)

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio is optional — e.g. code_execution venv may not have it

import python.models as models

# =============================================================================
# IMPORT FROM AGENT_CORE PACKAGE (Modularized Components)
# =============================================================================
from python.helpers.agent_core import (
    # Enums
    AgentContextType,
    # Classes
    AgentContext,
    AgentConfig,
    UserMessage,
    LoopData,
    # Exceptions
    HandledException,
    # Constants (re-exported for backwards compatibility on Agent class)
    DATA_NAME_SUPERIOR,
    DATA_NAME_SUBORDINATE,
    DATA_NAME_CTX_WINDOW,
    PROTECTION_MARKER,
    PROTECTION_MESSAGE,
)

# =============================================================================
# LOCAL IMPORTS
# =============================================================================
import python.history as history
from python.helpers import (
    extract_tools,
    files,
    errors,
    tokens,
    context as context_helper,
    redis_history,
    dirty_json,
    tool_registry,
    event_bus,
    prompt_router,
    settings,
)
from python.helpers.redis_history import get_redis_history_helper
from python.helpers.print_style import PrintStyle
from python.helpers.context_error_recovery import detect_context_error, get_recovery_handler
from python.helpers.model_wrappers.rate_limiting import is_transient_litellm_error
from python.helpers.same_message_bridge import (
    bridge_same_message_to_l1,
    bridge_semantic_repeat_to_l1,
    should_hard_stop_same_message,
    should_hard_stop_semantic_repeat,
    extract_tool_name_from_response,
    reset_same_message_counter,
    maybe_decay_cumulative_counter,
    # A-1 wiring: progress-aware test output tracking
    has_test_output_changed,
    is_test_command_output,
)

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, BaseMessage

import python.helpers.log as Log
from python.helpers.dirty_json import DirtyJson
from python.helpers.models_lazy import LazyModelWrapper
from python.helpers.settings import get_settings
from python.helpers.defer import DeferredTask
from python.helpers.localization import Localization
from python.helpers.extension import call_extensions
from python.helpers.errors import RepairableException, InterventionException, TruncationException
from python.models import _is_rate_limit_error, ProviderConfigurationError, get_model_context_window
from python.helpers.notification import NotificationManager, NotificationType, NotificationPriority
from python.helpers.crash_recovery import register_crash_handler, get_crash_recovery
from python.helpers.circuit_breaker import CircuitBreakerError
from python.helpers.observer_mesh import ObserverMesh

# Extracted modules (Issue #778 — line count refactor)
from python.helpers.agent_error_handler import handle_critical_exception_impl, format_rate_limit_message
from python.helpers.agent_process_tools import process_tools_impl
# Extracted modules (Issue #1200 P0.2 — agent.py modularization)
from python.helpers.agent_history import (
    hist_add_message_impl,
    hist_add_user_message_impl,
    hist_add_ai_response_impl,
    hist_add_warning_impl,
    hist_add_tool_result_impl,
    prepare_prompt_impl,
)
from python.helpers.agent_models import (
    call_utility_model_impl,
    call_chat_model_impl,
    rate_limiter_callback_impl,
)
from python.helpers.agent_flow import (
    attempt_supervisor_redirect_impl,
    log_summarizer_impl,
)
from python.helpers.agent_intervention import (
    handle_intervention_impl,
    wait_if_paused_impl,
)
from python.helpers.agent_null_ceiling import (
    _extract_middle_out_thoughts,
    update_null_iteration_counter,
    check_null_ceiling_escalation,
)

# Module logger
logger = logging.getLogger(__name__)

# Register global crash recovery handler at module load
register_crash_handler()

# Maximum consecutive truncation retries before giving up (Issue #1081)
MAX_TRUNCATION_RETRIES = 3

# Empty Response Circuit Breaker (5-Why RCA — 2026-04-25)
# Each "cycle" = MAX_EMPTY_RETRIES_PER_CYCLE retries + 1 corrective warning.
# After MAX_EMPTY_RESPONSE_CYCLES full cycles with no valid response,
# force a synthetic `response` tool call to break the loop.
# C-3 / Systems Audit: Lowered from 2×3=6 to 1×2=2 retries for faster circuit break.
from python.helpers.thresholds_registry import Thresholds as _Thresholds
MAX_EMPTY_RETRIES_PER_CYCLE = _Thresholds.MAX_EMPTY_RETRIES_PER_CYCLE
MAX_EMPTY_RESPONSE_CYCLES = _Thresholds.MAX_EMPTY_RESPONSE_CYCLES

# Blocked-Tools Circuit Breaker (RCA-325)
# Guards the gap where the LLM produces valid tool calls but ALL get blocked
# downstream (GitGuard, ProfileToolEnforcement, near-duplicate gate, etc.).
# The existing empty-response breaker doesn't catch this because
# agent_response IS non-empty — it contains valid tool call JSON.
# At WARN: inject error context telling the agent to change strategy.
# At ESCALATE: force a synthetic response to break the loop.
BLOCKED_TOOLS_WARN_THRESHOLD = 3
BLOCKED_TOOLS_ESCALATE_THRESHOLD = 5

# Null Response Ceiling (RCA-327)
# Tracks total unproductive iterations across the agent's lifetime.
# When process_tools returns None/empty or a truthy-but-tiny result (<10 chars),
# this counter increments. Unlike per-cycle counters, it is NEVER reset by
# empty-response cycle resets. At threshold, emits an L2 escalation signal
# with severity=critical so the supervisor can redirect or stop.
MAX_TOTAL_NULL_ITERATIONS = 30


class Agent:

    DATA_NAME_SUPERIOR = "_superior"
    DATA_NAME_SUBORDINATE = "_subordinate"
    DATA_NAME_CTX_WINDOW = "ctx_window"
    PROTECTION_MARKER = "[KEEP]"
    PROTECTION_MESSAGE = "<!-- [KEEP] -->"

    def __init__(
        self, number: int, config: AgentConfig, context: AgentContext | None = None,
        skip_init_extensions: bool = False,
        skip_model_loading: bool = False
    ):

        # agent config
        self.config = config

        # agent context
        self.context = context or AgentContext(config=config, agent0=self)

        # non-config vars
        self.number = number
        self.agent_name = (config.profile.capitalize() if config.profile else f"A{self.number}")

        # ── Agent UID System (RC-20) ──
        # Each agent gets a unique session UID (MD5-based) for deterministic
        # log correlation across parallel batch executions.
        _uid_raw = f"{number}:{time.time()}:{uuid.uuid4()}"
        self.session_uid: str = content_hash(_uid_raw)
        self.display_name: str = f"{self.agent_name} #{number} ({self.session_uid[:8]})"

        self.history = history.History(self)  # type: ignore[abstract]
        self.last_user_message: history.Message | None = None
        self.intervention: UserMessage | None = None
        # R1 Fix: Use ValidatedAgentData wrapper to catch undeclared key writes.
        # Warn-only in production; set AGIX_STRICT_DATA=1 for hard errors in dev.
        from python.helpers.agent_data_keys import ValidatedAgentData
        self.data: dict[str, Any] = ValidatedAgentData()  # validated data bus for all modules
        self.loop_data = LoopData()
        self._models_loaded = False
        self._total_monologue_iterations = 0  # Absolute counter — survives outer loop restarts
        self._failed_tool_count = 0  # Track consecutive failed tool calls

        # Initialize self-sanity properties (dual-layer supervisor architecture)
        self._init_self_sanity()

        # Load models from config (deferred if skip_model_loading for faster chat restore)
        if not skip_model_loading:
            self._load_models()
        
        # Initialize SkillsManager
        from python.helpers.skills_manager import SkillsManager
        self.skills_manager = SkillsManager(project_root=os.getcwd())
        self.skills_manager.discover_skills()
        
        if not skip_init_extensions:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    # If we are in an async context, we can't use run_until_complete or asyncio.run
                    # We must schedule the extensions to run in the loop.
                    # To ensure they run as soon as possible, we use create_task.
                    # We store the task to avoid 'never awaited' warnings.
                    PrintStyle().print(f"[AGENT] Scheduling agent_init extensions for {self.agent_name}")
                    self._init_task = loop.create_task(self.call_extensions("agent_init"))
                else:
                    PrintStyle().print(f"[AGENT] Running agent_init extensions for {self.agent_name} via asyncio.run")
                    asyncio.run(self.call_extensions("agent_init"))
            except RuntimeError:
                PrintStyle().print(f"[AGENT] Running agent_init extensions for {self.agent_name} via fallback asyncio.run")
                asyncio.run(self.call_extensions("agent_init"))
        
        # Register with crash recovery
        get_crash_recovery().register_agent(self)

    # =================================================================
    # SELF-SANITY TIER (Dual-Layer Supervisor Architecture)
    # Structural properties checked every turn by Layer 1 guards.
    # =================================================================

    # Per-profile max turn limits
    _PROFILE_MAX_TURNS = {
        "default": 1000,
        "multiagentdev": 1000,
        "alex": 500,
        "browser": 50,
    }
    _SUBORDINATE_MAX_TURNS = 200
    _FAN_OUT_MAX_TURNS = 100
    _DEFAULT_MAX_TURNS = 1000

    def _init_self_sanity(self):
        """Initialize self-sanity properties for dual-layer supervisor.

        These fields are checked every turn by Layer 1 structural guards
        at zero cost (no LLM call). They reset ONLY when the user sends
        a new message (re-prompt), NOT on outer loop restarts.
        """
        self._absolute_turns: int = 0
        self._md5_action_log: list = []
        self._error_count: int = 0
        self._has_attempted_compaction: bool = False

    def reset_for_user_message(self):
        """Reset self-sanity counters when user sends a new message.

        This is the ONLY legitimate reset point. Outer loop restarts,
        InterventionExceptions, and tool errors do NOT reset these.
        """
        self._absolute_turns = 0
        self._error_count = 0
        self._md5_action_log.clear()
        self._has_attempted_compaction = False

    def fingerprint_action(self, action_type: str, content: str) -> str:
        """Create MD5 fingerprint of an action for dedup/repetition detection.

        Appends to _md5_action_log which is checked by Layer 1's
        md5_repetition detector. Log is capped at 200 entries.

        Args:
            action_type: Category (e.g. 'tool_call', 'llm_response', 'mcp_call')
            content: The content to fingerprint (tool args, response text, etc.)

        Returns:
            The MD5 hex digest of the action.
        """
        fp = content_hash(f"{action_type}:{content}")
        self._md5_action_log.append({
            "turn": self._absolute_turns,
            "type": action_type,
            "fingerprint": fp,
            "timestamp": time.time(),
        })
        # Keep last 200 fingerprints to bound memory
        if len(self._md5_action_log) > 200:
            self._md5_action_log = self._md5_action_log[-200:]
        return fp

    def get_max_turns(self) -> int:
        """Get the maximum allowed turns for this agent based on profile.

        Priority:
        1. Profile-specific limit from _PROFILE_MAX_TURNS (if profile exists)
        2. Subordinate agent → min(subordinate_limit, profile_limit)
        3. LLM-set budget via set_iteration_budget (capped at profile limit)
        4. Dynamic budget based on decomposition size (R-4 fallback)
        5. Default: 1000
        
        5-Why RCA (Iteration 139): This is now the SINGLE SOURCE OF TRUTH
        for all iteration budgets. monologue() calls this instead of
        maintaining its own separate PROFILE_MAX_ITERATIONS dict.

        R-4 (RCA-362 — MainStreet budget exhaustion):
        For projects with many decomposition phases/tasks, the orchestrator
        needs more turns. Each decomposed task requires ~5 iterations
        (delegate + wait + gate + retry). The budget scales to
        max(profile_limit, task_count * 5) so large orchestrations don't
        exhaust budget prematurely. Subordinate agents are NOT affected.

        Smart Budget (RCA-362 Layer 1):
        When the orchestrator calls requirements(action='set_iteration_budget'),
        it stores _llm_iteration_budget in agent.data. This value takes
        priority over R-4 decomp scaling (the LLM made an explicit budget
        decision after seeing the full scope). The hard service limit
        (_PROFILE_MAX_TURNS) always caps the result.
        """
        profile = (self.config.profile or "").lower()
        profile_limit = self._PROFILE_MAX_TURNS.get(profile, self._DEFAULT_MAX_TURNS)
        
        # Subordinate agents get the tighter of (subordinate cap, profile limit)
        if self.data.get(Agent.DATA_NAME_SUPERIOR):
            return min(self._SUBORDINATE_MAX_TURNS, profile_limit)
        
        # Layer 1: LLM-set budget (orchestrator's explicit decision)
        # Takes priority over R-4 decomp scaling because the LLM has
        # seen the full scope and made a deliberate budget choice.
        # Edge cases: 0 or negative → fall through to R-4/profile default.
        llm_budget = self.data.get('_llm_iteration_budget', 0)
        if isinstance(llm_budget, (int, float)) and llm_budget > 0:
            # Layer 3: Hard service limit — LLM cannot exceed profile ceiling
            return min(int(llm_budget), profile_limit)
        
        # R-4: Dynamic budget — scale with decomposition size (fallback)
        # Each decomposed task needs ~5 iterations (delegate + wait + gate + retry)
        # C-6 / Systems Audit: Capped at ABSOLUTE_BUDGET_CEILING to prevent runaway scaling
        task_count = self.data.get('_decomposition_task_count', 0)
        if task_count > 0:
            decomp_budget = task_count * _Thresholds.BUDGET_PER_TASK
            profile_limit = min(max(profile_limit, decomp_budget), _Thresholds.ABSOLUTE_BUDGET_CEILING)
        
        return profile_limit

    def _load_models(self):
        """Load models from config. Called lazily if skip_model_loading was True."""
        if self._models_loaded:
            return
        
        from python.helpers.models_lazy import LazyModelWrapper

        # Load models from config
        # Resolve chat model: prioritize profile if it's set
        # This allows role-based model configurations and routing_rules to take effect
        config = self.config
        chat_provider = config.chat_model.provider
        chat_name = config.chat_model.name
        if config.profile:
             chat_provider = "role"
             chat_name = config.profile

        self.set_data("chat_model", LazyModelWrapper(models.get_chat_model, chat_provider, chat_name, config.chat_model))
        
        # Guard against None model configs
        util_provider = getattr(config.utility_model, "provider", None) if config.utility_model else None
        util_name = getattr(config.utility_model, "name", None) if config.utility_model else None
        self.set_data("utility_model", LazyModelWrapper(models.get_chat_model, util_provider, util_name, config.utility_model))
        
        embed_provider = getattr(config.embeddings_model, "provider", None) if config.embeddings_model else None
        embed_name = getattr(config.embeddings_model, "name", None) if config.embeddings_model else None
        self.set_data("embeddings_model", LazyModelWrapper(models.get_embedding_model, embed_provider, embed_name, config.embeddings_model))
        
        browser_provider = getattr(config.browser_model, "provider", None) if config.browser_model else None
        browser_name = getattr(config.browser_model, "name", None) if config.browser_model else None
        
        # Check if a specific browser role/profile is configured
        s = settings.get_settings()
        if browser_provider == "role":
            # browser_name already contains the model name from settings, 
            # which we use as the profile name if provider is 'role'
            pass

        self.set_data("browser_model", LazyModelWrapper(models.get_browser_model, browser_provider, browser_name, config.browser_model))
        
        self._models_loaded = True

    def log(self, *args, **kwargs):
        """Log a message with the agent's profile icon and sender attribution."""
        if "icon" not in kwargs and self.config.profile:
            kwargs["icon"] = self.config.profile
        
        # Inject agent's own attribution if not specified
        if "sender_type" not in kwargs:
            kwargs["sender_type"] = "agent"
        if "sender_id" not in kwargs:
            kwargs["sender_id"] = self.agent_name
            
        return self.context.log.log(*args, **kwargs)

    async def monologue(self):
        # =====================================================================
        # DEATH SPIRAL PROTECTION (Issue: $200 token burn)
        # Absolute limits that CANNOT be bypassed by exception handling.
        # These counters live on `self`, not on `loop_data`, so they survive
        # outer loop restarts that re-create LoopData.
        #
        # 5-Why RCA (Iteration 139): Previously used a SEPARATE
        # PROFILE_MAX_ITERATIONS dict here (browser=15, default=75) that was
        # independent of _PROFILE_MAX_TURNS in the structural guard, creating
        # a confusing dual-limit system. Now unified: get_max_turns() is the
        # single source of truth for all profile-based iteration budgets.
        # =====================================================================
        effective_max_iterations = self.get_max_turns()
        profile = getattr(self.config, "profile", "") or ""
        MAX_OUTER_RESTARTS = 10       # Max times outer loop can restart
        self._total_monologue_iterations = 0  # Reset at monologue entry
        _outer_restarts = 0

        # Chain-level death spiral protection (persists across _process_chain re-entries)
        if not hasattr(self.context, '_chain_monologue_iterations'):
            self.context._chain_monologue_iterations = 0
        if not hasattr(self.context, '_chain_monologue_entries'):
            self.context._chain_monologue_entries = 0
        self.context._chain_monologue_entries += 1

        while True:
            # --- OUTER LOOP: Death spiral check #1 ---
            if self.context.paused:
                self.log(type="warning", heading="⏸️ Agent Paused",
                         content="Agent is paused (likely by supervisor escalation). Stopping monologue.")
                return None  # Hard stop — no exception, just return

            if _outer_restarts >= MAX_OUTER_RESTARTS:
                self.log(type="error", heading="🛑 Outer Loop Limit",
                         content=f"Outer monologue loop restarted {_outer_restarts} times. Hard stopping to prevent death spiral.")
                return f"[RESTART_LIMIT] Agent {self.agent_name} hit outer restart limit ({MAX_OUTER_RESTARTS}). Stopping this agent only."

            try:
                # loop data dictionary to pass to extensions
                self.loop_data = LoopData(user_message=self.last_user_message)
                # Reset tool call dedup tracker only on FIRST monologue start (fresh user message),
                # NOT on outer restarts caused by InterventionException, so dedup tracking
                # persists across supervisor-triggered monologue restarts.
                if _outer_restarts == 0:
                    self.data["_tool_call_dedup"] = []
                    # Reset same-message counter on fresh monologue entry to prevent
                    # cross-monologue counter accumulation (Iteration 214 RCA)
                    self.data["_same_message_repeat_count"] = 0
                # call monologue_start extensions
                await self.call_extensions("monologue_start", loop_data=self.loop_data)

                printer = PrintStyle(italic=True, font_color="#b3ffd9", padding=False)

                # let the agent run message loop until he stops it with a response tool
                while True:
                    logger.warning(f"[LOOP_TRACE] {self.agent_name} INNER_LOOP_TOP iter={self.loop_data.iteration} is_done={self.loop_data.is_done} paused={self.context.paused if self.context else 'N/A'} abs_turns={self._absolute_turns}")

                    # === DETERMINISTIC STOP CHECK — FIRST IN LOOP (Fix 2) ===
                    # _user_stop_directive is the GROUND TRUTH flag. If set,
                    # nothing else matters — not gates, not extensions, not
                    # the LLM. Return immediately with summary.
                    # This is checked BEFORE paused, BEFORE escape hatch,
                    # BEFORE extensions, BEFORE the LLM call.
                    if self.data.get("_user_stop_directive"):
                        logger.warning(
                            f"[DETERMINISTIC_STOP] {self.agent_name}: "
                            f"_user_stop_directive=True at TOP of inner loop "
                            f"(iter={self.loop_data.iteration}). "
                            f"Returning stop summary immediately."
                        )
                        self.log(
                            type="warning",
                            heading="🛑 User Stop — Immediate Exit",
                            content=(
                                "User stop directive detected at loop top. "
                                "Exiting immediately — no further processing."
                            ),
                        )
                        return (
                            f"[USER_STOP] Agent {self.agent_name} stopped by user directive. "
                            f"Completed {self.loop_data.iteration} iterations. "
                            f"Progress has been saved to project files."
                        )

                    # --- INNER LOOP: Death spiral check #2 ---
                    if self.context.paused:
                        self.log(type="warning", heading="⏸️ Agent Paused",
                                 content="Agent paused mid-loop. Breaking inner loop.")
                        break

                    # === ESCAPE HATCH CHECK (RCA-252) ===
                    # If a previous iteration set _escape_hatch (via break instead
                    # of return), attempt L2 supervisor redirect before hard-stopping.
                    escape_hatch = self.data.pop("_escape_hatch", None)
                    if escape_hatch:
                        logger.warning(
                            f"[ESCAPE_HATCH] {self.agent_name}: Escape hatch triggered "
                            f"(type={escape_hatch.get('type')}, count={escape_hatch.get('repeat_count')}). "
                            f"Attempting supervisor redirect..."
                        )

                        # === Fix 3 (ISS-03): File Integrity Check ===
                        # After HARD_STOP, scan recently-modified files for corruption
                        # (duplicated code blocks from partial replace_in_file + re-apply).
                        try:
                            from python.helpers.hardstop_file_integrity import check_file_integrity_after_hardstop
                            _modified = list(self.data.get("_recently_modified_files", []))
                            _proj_dir = self.data.get("_project_dir", "")
                            if _modified and _proj_dir:
                                _corruption = check_file_integrity_after_hardstop(_proj_dir, _modified)
                                if _corruption:
                                    _files = [c["file"] for c in _corruption]
                                    logger.error(
                                        f"[FILE_INTEGRITY] CORRUPTION DETECTED after HARD_STOP in "
                                        f"{len(_corruption)} file(s): {_files}"
                                    )
                                    self.log(
                                        type="error",
                                        heading="⚠️ File Corruption Detected After HARD_STOP",
                                        content=(
                                            f"File integrity check found duplicated code blocks in "
                                            f"{len(_corruption)} file(s) after same-message HARD_STOP. "
                                            f"Files: {', '.join(os.path.basename(f) for f in _files)}. "
                                            f"This is caused by replace_in_file partial edits being "
                                            f"re-applied. Supervisor redirect should address this."
                                        ),
                                    )
                                    # Enrich escape_hatch context so supervisor knows about corruption
                                    escape_hatch["corrupted_files"] = _corruption
                        except asyncio.CancelledError:
                            raise  # Let cancellation propagate — don't swallow during cleanup
                        except Exception as _e:
                            logger.debug(f"[FILE_INTEGRITY] Check failed (non-fatal): {_e}")

                        redirect_prompt = await self._attempt_supervisor_redirect(escape_hatch)
                        if redirect_prompt == "CONTINUE_AS_IS":
                            # RCA-263: Supervisor decided this semantic repeat is legitimate work.
                            # Reset counters and continue WITHOUT injecting any redirect prompt.
                            logger.warning(
                                f"[ESCAPE_HATCH] {self.agent_name}: Supervisor decision = CONTINUE_AS_IS. "
                                f"Semantic repeat is legitimate work. Resetting counters, no redirect."
                            )
                            self.log(
                                type="info",
                                heading="✅ Supervisor: Continue (Legitimate Work)",
                                content=(
                                    f"Supervisor analyzed the semantic repeat and determined the agent "
                                    f"is doing legitimate work. Counters reset, agent continues."
                                ),
                            )
                            reset_same_message_counter(self.data)
                            # Also reset semantic-specific counters
                            self.data["_semantic_repeat_count"] = 0
                            self.data["_semantic_cumulative_count"] = max(
                                0, self.data.get("_semantic_cumulative_count", 0) - 5
                            )
                            continue  # Restart inner loop — no injection
                        elif redirect_prompt:
                            # Supervisor provided a redirect — inject it and continue
                            logger.warning(
                                f"[ESCAPE_HATCH] {self.agent_name}: Supervisor redirect received. "
                                f"Resetting counters and continuing loop."
                            )
                            self.log(
                                type="info",
                                heading="🔄 Supervisor Redirect (Escape Hatch)",
                                content=f"Supervisor provided corrective redirect after same-message loop:\n{redirect_prompt[:300]}",
                            )
                            await self.hist_add_warning(
                                message=f"[SUPERVISOR REDIRECT — ESCAPE HATCH]\n\n{redirect_prompt}"
                            )
                            # Reset same-message counter so the agent gets a fresh start
                            # NOTE: Using module-level import (line 99), NOT a local import here.
                            # A local import would shadow the module-level name for the ENTIRE
                            # function scope, causing UnboundLocalError at line 891 when the
                            # function is called before this branch executes.
                            reset_same_message_counter(self.data)
                            continue  # Restart inner loop with redirect context
                        else:
                            # No supervisor available or timeout — fall back to original hard-stop
                            logger.warning(
                                f"[ESCAPE_HATCH] {self.agent_name}: No supervisor redirect. "
                                f"Falling back to hard-stop."
                            )
                            return escape_hatch["reason"]

                    # === F-17: USER STOP DIRECTIVE — FORCE IMMEDIATE RESPONSE ===
                    # The _02_user_stop_directive extension sets _force_response
                    # AND _user_stop_directive. This MUST be checked BEFORE the
                    # verification spiral handler below. User stop overrides ALL
                    # other force-response reasons.
                    if self.data.get("_force_response") and self.data.get("_user_stop_directive"):
                        self.data.pop("_force_response", None)
                        logger.warning(
                            f"[USER_STOP_FORCE] {self.agent_name}: "
                            f"User stop directive active — forcing immediate "
                            f"response tool call. NO gate can block this."
                        )
                        self.log(
                            type="warning",
                            heading="🛑 User Stop — Forcing Completion",
                            content=(
                                "User requested all work to stop. Forcing "
                                "immediate response. All gates bypassed."
                            ),
                        )
                        synthetic_response = (
                            '{"thoughts": ["User explicitly requested stop. '
                            'Complying immediately — flushing state and responding."], '
                            '"tool_name": "response", '
                            '"tool_args": {"text": "[USER_STOP] Work stopped by user request. '
                            'Progress has been saved to project files. '
                            "Use 'Resume Agent' to continue where we left off.\"}}"
                        )
                        tools_result = await self.process_tools(synthetic_response)
                        if tools_result:
                            return tools_result
                        # If somehow blocked, force return anyway
                        return "[USER_STOP] Agent stopped by user directive."

                    # === ITR-42b: VERIFICATION SPIRAL FORCE RESPONSE ===
                    # The _38_verification_spiral_guard extension sets this flag
                    # when the agent has spent too many consecutive iterations
                    # reading/verifying without writing files. Previously this was
                    # dead code — the flag was set but never consumed. Now we
                    # inject a synthetic response tool call to force the agent
                    # to exit the loop and return to the orchestrator.
                    if self.data.pop("_force_response", False):
                        _spiral_counter = self.data.get("_iters_since_last_write", 0)
                        logger.error(
                            f"[VERIFICATION_SPIRAL_FORCE] {self.agent_name}: "
                            f"Force-response flag consumed after {_spiral_counter} "
                            f"read-only iterations. Injecting synthetic response."
                        )
                        self.log(
                            type="error",
                            heading="🛑 Verification Spiral — Forcing Exit",
                            content=(
                                f"Agent spent {_spiral_counter} consecutive iterations "
                                f"reading/verifying without writing files. Forcing exit "
                                f"to prevent token burn. Work completed so far will be "
                                f"preserved and remaining issues re-delegated."
                            ),
                        )
                        # Synthesize a response tool call (same pattern as blocked-tools breaker)
                        synthetic_response = (
                            '{"thoughts": ["Verification spiral circuit breaker activated: '
                            f'{_spiral_counter} consecutive read-only iterations. '
                            'Forcing exit to return progress to orchestrator."], '
                            '"tool_name": "response", '
                            '"tool_args": {"text": "[VERIFICATION_SPIRAL_EXIT] '
                            f'Agent was force-exited after {_spiral_counter} consecutive '
                            'iterations without writing any files. Work completed before '
                            'the spiral has been saved. Remaining issues should be '
                            're-delegated in a new subordinate."}}'
                        )
                        # Reset the spiral counter so next delegation starts fresh
                        self.data["_iters_since_last_write"] = 0
                        tools_result = await self.process_tools(synthetic_response)
                        if tools_result:
                            return tools_result
                        # If process_tools returned None (shouldn't happen for response),
                        # fall through to next iteration

                    self._total_monologue_iterations += 1
                    self._absolute_turns += 1  # RCA-452 F-2: Was dead code — initialized but never incremented
                    self.context._chain_monologue_iterations += 1
                    if self._total_monologue_iterations >= effective_max_iterations:
                        self.log(type="error", heading="🛑 Absolute Iteration Limit",
                                 content=f"Agent hit iteration limit ({effective_max_iterations}, profile={profile or 'default'}) across all loop restarts. "
                                         f"Hard stopping to prevent token burn death spiral.")
                        # Generate progress summary for context carryover (P1 Fix 4)
                        try:
                            from python.helpers.death_summary import generate_death_summary, format_iteration_limit_message
                            summary = generate_death_summary(self)
                            return format_iteration_limit_message(self.agent_name, effective_max_iterations, summary)
                        except Exception as e:
                            logger.warning(f"Failed to generate death summary: {e}")
                            return f"[ITERATION_LIMIT] Agent {self.agent_name} hit iteration limit ({effective_max_iterations}). Stopping this agent only."

                    # Chain-level check: catches loops across monologue re-entries from _process_chain
                    if self.context._chain_monologue_iterations >= effective_max_iterations:
                        self.log(type="error", heading="🛑 Chain Iteration Limit",
                                 content=f"Total iterations across monologue chain hit {self.context._chain_monologue_iterations}. "
                                         f"Hard stopping to prevent infinite re-entry loop.")
                        # Generate progress summary for context carryover (P1 Fix 4)
                        try:
                            from python.helpers.death_summary import generate_death_summary, format_iteration_limit_message
                            summary = generate_death_summary(self)
                            return format_iteration_limit_message(self.agent_name, effective_max_iterations, summary)
                        except Exception as e:
                            logger.warning(f"Failed to generate death summary: {e}")
                            return f"[CHAIN_LIMIT] Chain iteration limit ({effective_max_iterations}) reached for agent {self.agent_name}. Stopping this agent only."

                    self.context.streaming_agent = self  # mark self as current streamer
                    self.loop_data.iteration += 1
                    self.loop_data.params_temporary = {}  # clear temporary params

                    # P1-4: Reset hint coordinator for this turn
                    from python.helpers.hint_coordinator import get_hint_coordinator
                    get_hint_coordinator().reset_turn(self.agent_name)

                    # call message_loop_start extensions
                    await self.call_extensions(
                        "message_loop_start", loop_data=self.loop_data
                    )

                    if self.loop_data.is_done:
                        logger.warning(f"[LOOP_TRACE] {self.agent_name} IS_DONE_BREAK iter={self.loop_data.iteration} stop_reason={getattr(self.loop_data, 'stop_reason', 'UNKNOWN')}")
                        break

                    try:
                        # Prune history for scheduled tasks (Item #2)
                        # Relaxed pruning: keep up to 50 turns and only prune if over token limit
                        if self.context.type == AgentContextType.TASK:
                            if self.history.is_over_limit():
                                current_settings = settings.get_settings()
                                turns = current_settings.get("agent_history_max_turns", 150)
                                self.history.prune_to_turns(turns)
                            await self.context.log.prune_logs(500, summarizer=self._log_summarizer)

                        # prepare LLM chain (model, system, history)
                        render_start = time.perf_counter()
                        logger.info(f"[LOOP_TRACE] {self.agent_name} PREPARE_PROMPT_START iter={self.loop_data.iteration}")
                        try:
                            prompt = await self.prepare_prompt(loop_data=self.loop_data)
                        except Exception as e:
                            if "conversation history" in str(e).lower() or "doesn’t exist" in str(e).lower():
                                self.log(
                                    type="error",
                                    heading="History Desync Detected",
                                    content=f"Attempting history recovery due to error: {e}"
                                )
                                # Re-initialize history from scratch if it's missing
                                self.history = history.History(agent=self)
                                prompt = await self.prepare_prompt(loop_data=self.loop_data)
                            else:
                                raise e
                        render_duration = time.perf_counter() - render_start
                        logger.info(f"[LOOP_TRACE] {self.agent_name} PREPARE_PROMPT_DONE iter={self.loop_data.iteration} duration={render_duration:.1f}s")
                        
                        await ObserverMesh.get_instance().record_prompt_rendering(
                            duration=render_duration
                        )


                        # call before_main_llm_call extensions
                        await self.call_extensions("before_main_llm_call", loop_data=self.loop_data)

                        async def reasoning_callback(chunk: str, full: str):
                            await self.handle_intervention()
                            if chunk == full:
                                printer.print("Reasoning: ")  # start of reasoning
                            # Pass chunk and full data to extensions for processing
                            stream_data = {"chunk": chunk, "full": full}
                            await self.call_extensions(
                                "reasoning_stream_chunk", loop_data=self.loop_data, stream_data=stream_data
                            )
                            # Stream masked chunk after extensions processed it
                            if stream_data.get("chunk"):
                                printer.stream(stream_data["chunk"])
                            # Use the potentially modified full text for downstream processing
                            await self.handle_reasoning_stream(stream_data["full"])

                        async def stream_callback(chunk: str, full: str):
                            await self.handle_intervention()
                            # output the agent response stream
                            if chunk == full:
                                printer.print("Response: ")  # start of response
                            # Pass chunk and full data to extensions for processing
                            stream_data = {"chunk": chunk, "full": full}
                            await self.call_extensions(
                                "response_stream_chunk", loop_data=self.loop_data, stream_data=stream_data
                            )
                            # Stream masked chunk after extensions processed it
                            if stream_data.get("chunk"):
                                printer.stream(stream_data["chunk"])
                            # Use the potentially modified full text for downstream processing
                            await self.handle_response_stream(stream_data["full"])

                        # call main LLM
                        logger.info(f"[LOOP_TRACE] {self.agent_name} LLM_CALL_START iter={self.loop_data.iteration}")
                        try:
                            # [FIX] Agent-level deterministic safety-net retry loop (Issue #Recovery)
                            agent_retry_attempts = 2
                            agent_attempt = 0
                            while True:
                                try:
                                    # Gate 5: Mark activity before LLM call so supervisor
                                    # knows we're working (MSR_Smoke_1776891952)
                                    self.loop_data.last_activity_ts = time.time()
                                    # RCA-355: Stamp LLM heartbeat so subordinate idle timer
                                    # knows we're in an active LLM call (not idle). Without
                                    # this, the 120s idle timer fires during model thinking
                                    # (60-300s), killing agents that are actively working.
                                    try:
                                        from python.helpers.subordinate_timeout import stamp_llm_heartbeat
                                        stamp_llm_heartbeat(self, in_progress=True)
                                    except Exception as _hb_err:
                                        logger.debug(f"[HEARTBEAT] stamp_llm_heartbeat(True) failed: {_hb_err}")  # best-effort
                                    agent_response, _reasoning, model, provider = await self.call_chat_model(
                                        messages=prompt,
                                        response_callback=stream_callback,
                                        reasoning_callback=reasoning_callback,
                                    )
                                    # RCA-355: Clear LLM-in-progress flag after call completes
                                    try:
                                        from python.helpers.subordinate_timeout import stamp_llm_heartbeat
                                        stamp_llm_heartbeat(self, in_progress=False)
                                    except Exception as _hb_err:
                                        logger.debug(f"[HEARTBEAT] stamp_llm_heartbeat(False) failed: {_hb_err}")  # best-effort
                                    # Track successful LLM call and reset truncation counter (Issue #1081)
                                    self.loop_data.truncation_retries = 0
                                    self.data["_truncation_retries"] = 0  # persistent cross-monologue counter
                                    self.loop_data.last_successful_llm_ts = time.time()
                                    # Reset repetition recovery counter on successful LLM call (P0)
                                    try:
                                        from python.helpers.repetition_recovery import reset_attempt
                                        reset_attempt(self.data)
                                    except ImportError:
                                        pass  # Module not available — skip gracefully

                                    # P1 Fix: Empty-response guard (5-Why RCA — Iteration 139 + 152)
                                    # When model returns reasoning/thinking tokens but ZERO
                                    # response content, the empty string cascades into history
                                    # → output_langchain skips it → monologue loop fires →
                                    # agent spirals. 
                                    #
                                    # FIX (Iteration 152 RCA — 2026-04-25):
                                    # 1. Reasoning-only responses (reasoning>100, response=0) break
                                    #    immediately — retrying is pointless (same prompt → same result).
                                    # 2. Truly-empty responses retry with backoff.
                                    # 3. After exhausting retries per cycle, inject warning and BREAK
                                    #    to outer monologue loop (NOT continue — that was the 3b773ac5
                                    #    regression that amplified each event to 9 LLM calls).
                                    # 4. After MAX_EMPTY_RESPONSE_CYCLES, circuit break with synthetic
                                    #    response tool call.
                                    if not agent_response or not agent_response.strip():
                                        reasoning_len = len(_reasoning or '')

                                        # ── REASONING-ONLY CHECK ──
                                        # Model produced substantial reasoning but no tool call.
                                        # Retrying won't help — same prompt → same reasoning-only
                                        # result. Break to outer loop with a targeted warning so
                                        # the agent can self-correct on the next turn.
                                        if reasoning_len > 100:
                                            logger.warning(
                                                f"[REASONING_ONLY] model={model} provider={provider} | "
                                                f"reasoning={reasoning_len} chars, response=0. "
                                                f"Breaking to outer loop (retry won't fix this)."
                                            )
                                            await self.hist_add_warning(
                                                message=(
                                                    "⚠️ You produced extensive reasoning but no tool call. "
                                                    "You MUST output a JSON tool call on every turn. "
                                                    "Use the `response` tool if you are done, or call "
                                                    "the appropriate tool for your next action."
                                                )
                                            )
                                            break  # Exit inner loop — outer monologue loop recovers

                                        # ── CONTEXT PRESSURE RELIEF (Fix 1 — Iteration 209 RCA) ──
                                        # Empty responses are often caused by context bloat: the model
                                        # silently degrades when approaching 500-600K tokens instead of
                                        # raising ContextOverflow. Before retrying, check if context
                                        # pressure is high and trigger condensation to give the retry
                                        # a better chance of producing a real response.
                                        ctx_data = self.data.get(Agent.DATA_NAME_CTX_WINDOW, {})
                                        ctx_tokens = ctx_data.get("tokens", 0)
                                        if ctx_tokens > 0:
                                            from python.helpers import settings as _settings
                                            _conf = _settings.get_settings()
                                            _ctx_limit = _conf.get("chat_model_ctx_length", 128000)
                                            try:
                                                if hasattr(self.config, "chat_model") and self.config.chat_model:
                                                    if hasattr(self.config.chat_model, "ctx_length") and self.config.chat_model.ctx_length > 0:
                                                        _ctx_limit = self.config.chat_model.ctx_length
                                            except Exception as _ctx_err:
                                                logger.debug(f"[CTX_PRESSURE] ctx_length lookup failed: {_ctx_err}")
                                            pressure_ratio = ctx_tokens / _ctx_limit if _ctx_limit > 0 else 0
                                            if pressure_ratio > 0.50:
                                                _already_condensed = self.data.get("_empty_pressure_condensed", False)
                                                if not _already_condensed:
                                                    logger.warning(
                                                        f"[CONTEXT_PRESSURE] model={model} | "
                                                        f"tokens={ctx_tokens}, limit={_ctx_limit}, "
                                                        f"ratio={pressure_ratio:.2f}. "
                                                        f"Condensing before retry."
                                                    )
                                                    self.data["_empty_pressure_condensed"] = True
                                                    await self.force_history_condensation(
                                                        error=f"Empty response under context pressure ({pressure_ratio:.0%})"
                                                    )

                                        # ── TRULY-EMPTY RETRY LOGIC ──
                                        empty_retries = self.data.get("_empty_response_retries", 0)
                                        empty_cycles = self.data.get("_empty_response_cycles", 0)

                                        if empty_retries < MAX_EMPTY_RETRIES_PER_CYCLE:
                                            self.data["_empty_response_retries"] = empty_retries + 1
                                            delay = (empty_retries + 1) * 2
                                            logger.warning(
                                                f"[EMPTY_RESPONSE] model={model} provider={provider} | "
                                                f"reasoning={reasoning_len} chars | "
                                                f"attempt {empty_retries + 1}/{MAX_EMPTY_RETRIES_PER_CYCLE}, "
                                                f"cycle {empty_cycles + 1}/{MAX_EMPTY_RESPONSE_CYCLES}. "
                                                f"Retrying in {delay}s..."
                                            )
                                            await asyncio.sleep(delay)
                                            continue  # Retry the inner while loop
                                        else:
                                            # Exhausted retries for this cycle
                                            self.data["_empty_response_retries"] = 0
                                            self.data["_empty_response_cycles"] = empty_cycles + 1

                                            if empty_cycles + 1 >= MAX_EMPTY_RESPONSE_CYCLES:
                                                # ── CIRCUIT_BREAKER ──
                                                # Model has failed to produce a response after
                                                # MAX_EMPTY_RESPONSE_CYCLES full cycles. Force a
                                                # synthetic `response` tool call to cleanly exit.
                                                logger.error(
                                                    f"[CIRCUIT_BREAKER] Empty response circuit breaker "
                                                    f"activated after {empty_cycles + 1} cycles "
                                                    f"({(empty_cycles + 1) * MAX_EMPTY_RETRIES_PER_CYCLE} "
                                                    f"total retries). model={model} provider={provider}. "
                                                    f"Forcing synthetic response exit."
                                                )
                                                self.data["_empty_response_retries"] = 0
                                                self.data["_empty_response_cycles"] = 0
                                                agent_response = (
                                                    '{"thoughts": ["Circuit breaker activated — model returned '
                                                    'empty responses after multiple retry cycles. Exiting to '
                                                    'prevent infinite loop."], '
                                                    '"tool_name": "response", '
                                                    '"tool_args": {"text": "⚠️ Model returned empty responses '
                                                    'after multiple retry cycles. This agent is unable to '
                                                    'continue and is returning control. The task may need to '
                                                    'be retried or reassigned."}}'
                                                )
                                                break  # Exit inner retry loop with forced response
                                            else:
                                                # Still have cycles left — inject warning and break
                                                # to outer monologue loop. The warning is in history;
                                                # the outer loop re-runs extensions and gives the
                                                # model a fresh turn with full context.
                                                # FIX (2026-04-25): Using break NOT continue.
                                                # The old `continue` re-entered the inner retry
                                                # cascade, burning 9 LLM calls per event (3b773ac5
                                                # regression). `break` exits cleanly — no fake
                                                # tools, no amplification, no hallucination.
                                                logger.error(
                                                    f"[EMPTY_RESPONSE] model={model} provider={provider} | "
                                                    f"reasoning={reasoning_len} chars | "
                                                    f"after {MAX_EMPTY_RETRIES_PER_CYCLE} retries "
                                                    f"(cycle {empty_cycles + 1}/{MAX_EMPTY_RESPONSE_CYCLES}). "
                                                    f"Adding corrective warning — breaking to outer loop."
                                                )
                                                correction_msg = (
                                                    "⚠️ Your last response was empty (no tool call or text produced). "
                                                    "You MUST produce a valid JSON tool call on every turn. "
                                                    "Review your current task and determine the correct next action. "
                                                    "If you are done, use the `response` tool to deliver your final answer."
                                                )
                                                await self.hist_add_warning(message=correction_msg)
                                                break  # Exit inner loop — outer monologue loop recovers
                                    else:
                                        # Got a real response — reset all empty response counters
                                        self.data["_empty_response_retries"] = 0
                                        self.data["_empty_response_cycles"] = 0
                                        self.data["_empty_pressure_condensed"] = False

                                    break # Success
                                except asyncio.CancelledError:
                                    # FIX (Iteration 22 / RCA-22): Don't retry CancelledError.
                                    raise
                                except Exception as e:
                                    if isinstance(e, (RepairableException, InterventionException)): raise
                                    
                                    # Check for transient errors or rate limits
                                    is_transient = is_transient_litellm_error(e) or _is_rate_limit_error(e)
                                    if not is_transient or agent_attempt >= agent_retry_attempts:
                                        raise
                                    
                                    agent_attempt += 1
                                    delay = agent_attempt * 5  # Deterministic backoff
                                    logger.warning(f"[AGENT RECOVERY] LLM failure (attempt {agent_attempt}/{agent_retry_attempts}). Error: {str(e)[:200]}. Retrying in {delay}s...")
                                    await asyncio.sleep(delay)
                        except RepairableException as e:
                            # UX Fix: If the error was repaired/condensed, just continue the turn
                            self.log(
                                type="info",
                                heading="✓ Context Recovered",
                                content=f"{str(e)}. Retrying turn...",
                                # Skip notification for background tasks (Issue #RepairableSilence)
                                notify=False if self.context.type == AgentContextType.TASK else True
                            )
                            # call extensions for logic that needs to know about recovery
                            await self.call_extensions("context_recovered", loop_data=self.loop_data, error=e)
                            continue

                        # Notify extensions to finalize their stream filters
                        await self.call_extensions(
                            "reasoning_stream_end", loop_data=self.loop_data
                        )
                        await self.call_extensions(
                            "response_stream_end", loop_data=self.loop_data
                        )

                        # retroactive metadata update for logs
                        for key in ["log_item_generating", "log_item_response"]:
                            if key in self.loop_data.params_temporary:
                                self.loop_data.params_temporary[key].update(
                                    actual_model=model, actual_provider=provider
                                )

                        await self.handle_intervention(agent_response)

                        # === DIAGNOSTIC: Log what's being compared (Iteration 214 RCA) ===
                        _last_resp_preview = (self.loop_data.last_response or "")[:120]
                        _curr_resp_preview = (agent_response or "")[:120]
                        _is_exact = (self.loop_data.last_response == agent_response)
                        logger.warning(
                            f"[SAME_MSG_DIAG] agent={self.agent_name} iter={self.loop_data.iteration} "
                            f"exact_match={_is_exact} "
                            f"last_preview='{_last_resp_preview}' "
                            f"curr_preview='{_curr_resp_preview}'"
                        )

                        if self.loop_data.last_response == agent_response:  # if assistant_response is the same as last message in history, let him know
                            # RCA-270: Skip same-message warning if the agent has already
                            # completed delivery (gate passed, [[DONE]] injected). The identical
                            # response is expected — the agent is correctly done.
                            if self.data.get("_delivery_complete", False):
                                logger.debug(
                                    f"[SAME_MSG] Skipping same-message warning — "
                                    f"delivery complete for {self.agent_name}"
                                )
                                # Append the response and let the loop end naturally
                                log_id = ""
                                if "log_item_response" in self.loop_data.params_temporary:
                                    log_id = self.loop_data.params_temporary["log_item_response"].id or ""
                                await self.hist_add_ai_response(agent_response, model=model, provider=provider, id=log_id)
                                continue  # Skip the warning, proceed to next iteration (which should end naturally)

                            log_id = ""
                            if "log_item_response" in self.loop_data.params_temporary:
                                log_id = self.loop_data.params_temporary["log_item_response"].id or ""
                            # Append the assistant's response to the history
                            await self.hist_add_ai_response(agent_response, model=model, provider=provider, id=log_id)
                            # Append warning message to the history
                            warning_msg = self.read_prompt("fw.msg_repeat.md")
                            log_item = self.log(type="warning", content=warning_msg)
                            await self.hist_add_warning(message=warning_msg, id=log_item.id or "")

                            # === SAME-MESSAGE → L1 SIGNAL BRIDGE (Iteration 158 RCA) ===
                            # Increment persistent counter and feed into L2 supervisor pipeline
                            repeat_count = self.data.get("_same_message_repeat_count", 0) + 1
                            self.data["_same_message_repeat_count"] = repeat_count
                            bridge_same_message_to_l1(self.data, repeat_count=repeat_count, tool_name=extract_tool_name_from_response(agent_response))
                            # Hard-stop after SAME_MESSAGE_HARD_CAP (3) consecutive repeats
                            # A-1 wiring: exempt test commands with changing output (agent is making progress)
                            _a1_test_output_changed = None
                            if is_test_command_output(agent_response):
                                _a1_test_output_changed = has_test_output_changed(self.data)
                            if should_hard_stop_same_message(repeat_count, tool_name=extract_tool_name_from_response(agent_response), cumulative_count=self.data.get("_same_message_cumulative_count", 0), test_output_changed=_a1_test_output_changed):
                                stop_msg = (
                                    f"[HARD_STOP] {self.agent_name}: Hard-stopped after {repeat_count} "
                                    f"consecutive identical messages (same-message loop detected)."
                                )
                                self.log(type="error", content=stop_msg)
                                PrintStyle(font_color="red", bold=True, padding=True).print(stop_msg)
                                # === ESCAPE HATCH (RCA-252) ===
                                # Instead of `return stop_msg` (which kills the agent before
                                # the L2 supervisor can redirect), set the escape hatch flag
                                # and break the inner loop. The outer loop checks this flag
                                # and attempts a supervisor redirect before falling back.
                                from python.helpers.same_message_bridge import extract_tool_signature
                                from python.helpers.output_truncation import truncate_output_middle_out
                                self.data["_escape_hatch"] = {
                                    "reason": stop_msg,
                                    "type": "same_message_exact",
                                    "ts": time.time(),
                                    "repeat_count": repeat_count,
                                    # RCA-289: Enrich with tool error context for supervisor (middle-out preserves head+tail)
                                    "failed_tool": extract_tool_name_from_response(agent_response) or "",
                                    "failed_tool_sig": truncate_output_middle_out(str(extract_tool_signature(agent_response) or ""), max_chars=200, head_ratio=0.4),
                                    "last_tool_error": truncate_output_middle_out(str(getattr(self.loop_data, 'last_tool_result', '') or ''), max_chars=500, head_ratio=0.3),
                                }
                                break  # Exit inner loop → outer loop handles escape hatch

                        else:  # otherwise proceed with tool
                            log_id = ""
                            if "log_item_response" in self.loop_data.params_temporary:
                                log_id = self.loop_data.params_temporary["log_item_response"].id or ""

                            # === FIX (Iteration 214 RCA — Order-of-Operations Bug) ===
                            # MUST capture previous response BEFORE hist_add_ai_response
                            # mutates self.loop_data.last_response. The Iteration 213
                            # semantic check was placed AFTER the mutation, causing every
                            # tool call to be compared against ITSELF (always True).
                            _previous_response = self.loop_data.last_response

                            # Append the assistant's response to the history
                            # NOTE: This sets self.loop_data.last_response = agent_response
                            await self.hist_add_ai_response(agent_response, model=model, provider=provider, id=log_id)

                            # === SEMANTIC SAME-MESSAGE CHECK (Iteration 213 Fix) ===
                            # Even if the exact string differs (LLM changed "thoughts"),
                            # check if the tool_name + tool_args are identical. This
                            # prevents the LLM from gaming the detector by rephrasing
                            # its reasoning while issuing identical delegations.
                            # FIX: Use _previous_response (captured above), NOT
                            # self.loop_data.last_response (which now == agent_response).
                            from python.helpers.same_message_bridge import is_semantic_repeat, extract_tool_signature
                            from python.helpers.output_truncation import truncate_output_middle_out
                            _sig_last = extract_tool_signature(_previous_response or "")
                            _sig_curr = extract_tool_signature(agent_response or "")
                            _is_sem = is_semantic_repeat(_previous_response or "", agent_response)
                            logger.warning(
                                f"[SEMANTIC_DIAG] agent={self.agent_name} iter={self.loop_data.iteration} "
                                f"is_semantic_repeat={_is_sem} "
                                f"sig_prev='{(_sig_last or 'None')[:80]}' "
                                f"sig_curr='{(_sig_curr or 'None')[:80]}'"
                            )
                            if (_previous_response and _is_sem):
                                # RCA-263: Use SEMANTIC-specific counter, separate from exact-match
                                repeat_count = self.data.get("_semantic_repeat_count", 0) + 1
                                self.data["_semantic_repeat_count"] = repeat_count
                                self.log(
                                    type="warning",
                                    content=(
                                        f"Semantic same-message detected: tool signature identical "
                                        f"(count={repeat_count}), only thoughts/reasoning differ."
                                    )
                                )
                                # RCA-263: Use semantic-specific bridge (separate cumulative counter)
                                bridge_semantic_repeat_to_l1(self.data, repeat_count=repeat_count)
                                # A-1 wiring: exempt test commands with changing output
                                _a1_sem_test_changed = None
                                if is_test_command_output(agent_response):
                                    _a1_sem_test_changed = has_test_output_changed(self.data)
                                if should_hard_stop_semantic_repeat(repeat_count, tool_name=extract_tool_name_from_response(agent_response), cumulative_count=self.data.get("_semantic_cumulative_count", 0), test_output_changed=_a1_sem_test_changed):
                                    stop_msg = (
                                        f"[HARD_STOP] {self.agent_name}: Hard-stopped after {repeat_count} "
                                        f"semantically identical tool calls (thoughts rewording detected)."
                                    )
                                    self.log(type="error", content=stop_msg)
                                    PrintStyle(font_color="red", bold=True, padding=True).print(stop_msg)
                                    # === ESCAPE HATCH (RCA-252) ===
                                    # Same pattern as exact-match: flag + break, not return.
                                    self.data["_escape_hatch"] = {
                                        "reason": stop_msg,
                                        "type": "same_message_semantic",
                                        "ts": time.time(),
                                        "repeat_count": repeat_count,
                                        # RCA-289: Enrich with tool error context for supervisor (middle-out preserves head+tail)
                                        "failed_tool": extract_tool_name_from_response(agent_response) or "",
                                        "failed_tool_sig": truncate_output_middle_out(str(_sig_curr or ""), max_chars=200, head_ratio=0.4),
                                        "last_tool_error": truncate_output_middle_out(str(getattr(self.loop_data, 'last_tool_result', '') or ''), max_chars=500, head_ratio=0.3),
                                    }
                                    break  # Exit inner loop → outer loop handles escape hatch
                            else:
                                # Genuinely different message — reset counter
                                reset_same_message_counter(self.data)
                                # RCA-260: Decay cumulative counter when the agent
                                # demonstrates forward progress (distinct tool calls)
                                _current_tool = extract_tool_name_from_response(agent_response)
                                if _current_tool:
                                    maybe_decay_cumulative_counter(self.data, _current_tool)

                            # Gate 5: Mark activity before tool execution so supervisor
                            # knows we're working (MSR_Smoke_1776891952)
                            self.loop_data.last_activity_ts = time.time()
                            # process tools requested in agent message
                            tools_result = await self.process_tools(agent_response)
                            logger.warning(f"[LOOP_TRACE] {self.agent_name} PROCESS_TOOLS_RETURNED iter={self.loop_data.iteration} truthy={bool(tools_result)} type={type(tools_result).__name__} preview={str(tools_result)[:120] if tools_result else 'None'}")
                            if tools_result:  # final response of message loop available
                                # Mark the current AI response as completion for log pruning
                                if "log_item_response" in self.loop_data.params_temporary:
                                    self.loop_data.params_temporary["log_item_response"].update(completion=True)
                                logger.warning(f"[LOOP_TRACE] {self.agent_name} RETURNING_TOOLS_RESULT iter={self.loop_data.iteration} len={len(str(tools_result))}")
                                # SUCCESS: Reset blocked-tools counter
                                self.data["_consecutive_blocked_tools"] = 0
                                return tools_result  # break the execution if the task is done
                            else:
                                # ── RCA-325b: BLOCKED-TOOLS CIRCUIT BREAKER ──
                                # process_tools returned None/falsy. This happens in TWO cases:
                                # A) Tool was genuinely blocked by an extension (GitGuard,
                                #    ProfileToolEnforcement, etc.) → _last_tool_was_blocked=True
                                # B) Non-response tool executed SUCCESSFULLY (code_execution,
                                #    write_to_file, etc.) → normal flow, _last_tool_was_blocked=False
                                #
                                # Only case A should increment the blocked counter. Case B is
                                # the NORMAL operation of the monologue loop — most tool calls
                                # return None to continue the loop.
                                was_blocked = self.data.get("_last_tool_was_blocked", False)
                                if was_blocked:
                                    blocked_count = self.data.get("_consecutive_blocked_tools", 0) + 1
                                    self.data["_consecutive_blocked_tools"] = blocked_count
                                    logger.warning(
                                        f"[BLOCKED_TOOLS_BREAKER] {self.agent_name} iter={self.loop_data.iteration} "
                                        f"consecutive_blocked={blocked_count}/{BLOCKED_TOOLS_ESCALATE_THRESHOLD}"
                                    )
                                else:
                                    # Successful non-response tool — reset blocked counter
                                    self.data["_consecutive_blocked_tools"] = 0
                                    blocked_count = 0

                                if blocked_count >= BLOCKED_TOOLS_ESCALATE_THRESHOLD:
                                    # ── HARD ESCALATION: Force synthetic response ──
                                    # The agent has been stuck for BLOCKED_TOOLS_ESCALATE_THRESHOLD
                                    # consecutive iterations with ALL tools blocked. Force-exit
                                    # with a synthetic response to stop token burn.
                                    logger.error(
                                        f"[BLOCKED_TOOLS_BREAKER] {self.agent_name}: CIRCUIT BREAKER "
                                        f"ACTIVATED — {blocked_count} consecutive iterations with all "
                                        f"tools blocked. Forcing synthetic response exit."
                                    )
                                    self.data["_consecutive_blocked_tools"] = 0
                                    agent_response = (
                                        '{"thoughts": ["Circuit breaker activated: all tool calls '
                                        f'blocked for {blocked_count} consecutive iterations. '
                                        'Forcing exit to prevent token burn."], '
                                        '"tool_name": "response", '
                                        '"tool_args": {"text": "All tool calls were blocked for '
                                        f'{blocked_count} consecutive iterations (tools were rejected '
                                        'by guards like GitGuard or ProfileToolEnforcement). '
                                        'Returning control to report the blocker. The task may need '
                                        'a different approach or manual intervention."}}'
                                    )
                                    # Process the synthetic response to cleanly exit
                                    tools_result = await self.process_tools(agent_response)
                                    if tools_result:
                                        return tools_result

                                elif blocked_count >= BLOCKED_TOOLS_WARN_THRESHOLD:
                                    # ── WARNING: Inject error context so agent can adapt ──
                                    # Tell the agent WHY its tools are being blocked and what
                                    # to do differently. This follows the same hist_add_warning
                                    # pattern used by the tool failure tracker.
                                    await self.hist_add_warning(
                                        message=(
                                            f"\u26a0\ufe0f BLOCKED TOOLS WARNING: Your last {blocked_count} "
                                            f"tool calls were ALL blocked or produced no results. "
                                            f"You are stuck in a loop. STOP retrying the same approach. "
                                            f"If you cannot complete the task (e.g., git push blocked, "
                                            f"tool not available), use the `response` tool to report "
                                            f"the blocker and deliver your progress so far. "
                                            f"({BLOCKED_TOOLS_ESCALATE_THRESHOLD - blocked_count} more "
                                            f"blocked iterations will force an automatic exit.)"
                                        )
                                    )

                    # exceptions inside message loop:
                    except InterventionException as e:
                        # intervention message has been handled in handle_intervention(), proceed with conversation loop
                        content = str(e)
                        # Log it for tracing, but don't add to history again (already added in handle_intervention)
                        log_item = self.log(type="info", content=content)
                        PrintStyle(font_color="cyan", padding=True).print(content)
                    except TruncationException as e:
                        # Track truncation retries — both per-loop and persistent (Issue #1081)
                        self.loop_data.truncation_retries += 1
                        persistent_retries = self.data.get("_truncation_retries", 0) + 1
                        self.data["_truncation_retries"] = persistent_retries
                        
                        # Cap truncation retries using PERSISTENT counter to survive monologue re-entries
                        if persistent_retries >= MAX_TRUNCATION_RETRIES:
                            error_msg = (
                                f"{self.agent_name}: Truncation retry limit reached "
                                f"({persistent_retries}/{MAX_TRUNCATION_RETRIES} total truncations). "
                                f"Breaking out of loop. Consider increasing max_tokens."
                            )
                            PrintStyle(font_color="red", padding=True).print(error_msg)
                            self.log(
                                type="error",
                                heading="Truncation Retry Limit Exceeded",
                                content=error_msg,
                            )
                            # Add partial response so it's not lost
                            if e.partial_response:
                                log_id = ""
                                if "log_item_response" in self.loop_data.params_temporary:
                                    log_id = self.loop_data.params_temporary["log_item_response"].id or ""
                                await self.hist_add_ai_response(
                                    e.partial_response, model=e.model, provider=e.provider, id=log_id
                                )
                            # Return a final error response instead of just breaking
                            # This prevents the monologue from re-entering
                            return f"⚠️ Response was truncated {persistent_retries} times. max_tokens may be too low for this task. Stopping to prevent infinite loop."
                        
                        # Log truncation and add partial response to history (Issue #268)
                        PrintStyle(font_color="yellow", padding=True).print(
                            f"{self.agent_name}: Response truncated (retry {self.loop_data.truncation_retries}/{MAX_TRUNCATION_RETRIES}). Retrying turn to continue..."
                        )
                        self.log(
                            type="warning",
                            heading="Response Truncated",
                            content=f"LLM response was cut off (attempt {self.loop_data.truncation_retries}/{MAX_TRUNCATION_RETRIES}). Requesting continuation...",
                        )
                        
                        log_id = ""
                        if "log_item_response" in self.loop_data.params_temporary:
                            log_id = self.loop_data.params_temporary["log_item_response"].id or ""
                        
                        # Add partial AI response to history
                        await self.hist_add_ai_response(
                            e.partial_response, model=e.model, provider=e.provider, id=log_id
                        )
                        
                        # Add a "Continue" instruction for the LLM
                        continue_msg = self.read_prompt("fw.msg_continue.md")
                        await self.hist_add_warning(message=continue_msg)
                        
                        # Don't return, just let the loop continue to start the next turn
                        continue
                    except asyncio.CancelledError:
                        # FIX (Iteration 22 / RCA-22): In Python 3.9+, CancelledError is
                        # BaseException, NOT Exception. Without this explicit handler,
                        # CancelledError bypasses ALL except Exception clauses and silently
                        # kills the agent with zero logging. This was the root cause of
                        # agents dying silently during smoke tests.
                        #
                        # F-17b: On cancellation, ALWAYS flush state and kill subordinates.
                        # Previously this just returned a sentinel, leaving subordinates
                        # running and state unflushed.
                        logger.warning(
                            f"[AGENT] {self.agent_name}: asyncio.CancelledError caught in "
                            f"inner message loop (iteration {self.loop_data.iteration}). "
                            f"Flushing state and killing subordinates before exit."
                        )

                        # F-17b: Kill subordinate tasks immediately
                        try:
                            from python.helpers.agent_core.context import _kill_subordinate_tasks
                            _kill_subordinate_tasks(self)
                        except Exception as e:
                            logger.debug(f"[AGENT] Subordinate kill on cancel failed: {e}")

                        # F-17b: Flush state to disk
                        try:
                            from python.helpers.persist_chat import save_tmp_chat
                            save_tmp_chat(self.context)
                        except Exception as e:
                            logger.debug(f"[AGENT] State flush on cancel failed: {e}")

                        # F-17b: Flush requirements ledger if we have a project
                        try:
                            project_dir = self.data.get("_active_project_dir", "")
                            if project_dir:
                                from python.helpers.requirements_ledger import persist_ledger_to_project
                                persist_ledger_to_project(self.data, project_dir)
                        except Exception as e:
                            logger.debug(f"[AGENT] Ledger flush on cancel failed: {e}")

                        self.log(
                            type="warning",
                            heading="⚠️ Task Cancelled",
                            content=f"Agent {self.agent_name} was cancelled during message loop iteration {self.loop_data.iteration}. State flushed, subordinates killed.",
                        )
                        return f"[CANCELLED] Agent {self.agent_name} task was cancelled."
                    except Exception as e:
                        # Other exception kill the loop
                        await self.handle_critical_exception(e)

                    finally:
                        logger.warning(f"[LOOP_TRACE] {self.agent_name} FINALLY_BLOCK iter={self.loop_data.iteration} is_done={self.loop_data.is_done}")
                        if self.context and self.context.log:
                            # Increased baseline for log pruning to 2000 for better UI stability
                            await self.context.log.prune_logs(keep_last=2000, summarizer=self._log_summarizer)
                        # call message_loop_end extensions
                        await self.call_extensions(
                            "message_loop_end", loop_data=self.loop_data
                        )
                        logger.warning(f"[LOOP_TRACE] {self.agent_name} AFTER_MSG_LOOP_END iter={self.loop_data.iteration} is_done={self.loop_data.is_done}")

            # exceptions outside message loop:
            except asyncio.CancelledError:
                # FIX (Iteration 22 / RCA-22): Catch CancelledError in outer loop too.
                # If an InterventionException triggers a restart and CancelledError fires
                # during extension calls or prompt preparation, it must not escape silently.
                logger.warning(
                    f"[AGENT] {self.agent_name}: asyncio.CancelledError caught in "
                    f"outer monologue loop. Flushing state and killing subordinates."
                )
                # F-17b: Kill subordinates and flush state on outer cancel too
                try:
                    from python.helpers.agent_core.context import _kill_subordinate_tasks
                    _kill_subordinate_tasks(self)
                except Exception as _kill_err:
                    logger.debug(f"[CANCEL] Subordinate kill failed: {_kill_err}")
                try:
                    project_dir = self.data.get("_active_project_dir", "")
                    if project_dir:
                        from python.helpers.requirements_ledger import persist_ledger_to_project
                        persist_ledger_to_project(self.data, project_dir)
                except Exception as _ledger_err:
                    logger.debug(f"[CANCEL] Ledger persist failed: {_ledger_err}")
                return f"[CANCELLED] Agent {self.agent_name} task was cancelled."
            except InterventionException as e:
                _outer_restarts += 1  # Track restarts to enforce outer loop limit
                pass  # start over (but now with restart limit enforced at loop top)
            except Exception as e:
                await self.handle_critical_exception(e)
            finally:
                self.context.streaming_agent = None  # unset current streamer
                # Persist chat history at the end of monologue to ensure results are saved (Issue #140)
                try:
                    from python.helpers.persist_chat import save_tmp_chat
                    save_tmp_chat(self.context)
                except Exception as e:
                    logger.debug(f"Failed to auto-persist chat in monologue end: {e}")
                
                # call monologue_end extensions
                await self.call_extensions("monologue_end", loop_data=self.loop_data)  # type: ignore

    async def _attempt_supervisor_redirect(self, escape_context: dict) -> Optional[str]:
        """Delegated to agent_flow.py (Issue #1200 P0.2)."""
        return await attempt_supervisor_redirect_impl(self, escape_context)

    async def _log_summarizer(self, items: List[Log.LogItem]) -> str:
        """Delegated to agent_flow.py (Issue #1200 P0.2)."""
        return await log_summarizer_impl(self, items)

    async def prepare_prompt(self, loop_data: LoopData) -> list[BaseMessage]:
        """Delegated to agent_history.py (Issue #1200 P0.2)."""
        return await prepare_prompt_impl(self, loop_data)

    async def handle_critical_exception(self, exception: Exception):
        """Handle critical exceptions — delegated to agent_error_handler.py (Issue #778)."""
        await handle_critical_exception_impl(self, exception)


    async def force_history_condensation(self, error: Exception | str | None = None) -> bool:
        """
        Force immediate history pruning and condensation using the recovery handler
        without raising a RepairableException. Returns True if condensation occurred.
        """
        try:
            from python.helpers.context_error_recovery import get_recovery_handler
            # Call the internal condensation logic directly to avoid RepairableException bubbles
            await get_recovery_handler()._condense_for_recovery(self, error=error)
            return True
        except Exception as e:
            logger.error(f"Force history condensation failed: {str(e)}")
            return False

    def _format_rate_limit_message(self, exception: Exception) -> str:
        """Format a friendly rate limit message — delegated to agent_error_handler.py."""
        return format_rate_limit_message(exception)


    async def get_system_prompt(self, loop_data: LoopData) -> list[str]:
        system_prompt: list[str] = []
        await self.call_extensions(
            "system_prompt", system_prompt=system_prompt, loop_data=loop_data
        )

        # Add task hardening instructions for scheduled tasks (Item #4)
        if self.context.type == AgentContextType.TASK:
            hardening_prompt = self.read_prompt("agent.system.task_hardening.md")
            if hardening_prompt:
                system_prompt.append(hardening_prompt)

        # Add skills compact index (Phase 2A: Progressive Discovery)
        # Instead of injecting full skill instruction bodies (~24K tokens),
        # we inject a compact name+description index and agents use the
        # view_skill tool to load full instructions on demand.
        if hasattr(self, 'skills_manager'):
            mode = self.config.profile or "default"
            
            # Determine available tools for conditional filtering (Phase 2B)
            available_tool_names = []
            if hasattr(self.config, 'tools') and self.config.tools:
                available_tool_names = [
                    getattr(t, 'name', str(t)) for t in self.config.tools
                ]
            
            context_type_str = self.context.type.value if self.context and self.context.type else "user"
            
            compact_prompt = self.skills_manager.get_compact_index_prompt(
                mode=mode,
                available_tools=available_tool_names if available_tool_names else None,
                context_type=context_type_str
            )
            if compact_prompt:
                system_prompt.append(compact_prompt)

        return system_prompt

    def parse_prompt(self, _prompt_file: str, **kwargs):
        dirs = [files.get_abs_path("prompts")]
        if (
            self.config.profile
        ):  # if agent has custom folder, use it and use default as backup
            prompt_dir = files.get_abs_path("agents", self.config.profile, "prompts")
            dirs.insert(0, prompt_dir)
        prompt = files.parse_file(
            _prompt_file, _directories=dirs, **kwargs
        )
        return prompt

    def read_prompt(self, file: str, **kwargs) -> str:
        dirs = [files.get_abs_path("prompts")]
        if (
            self.config.profile
        ):  # if agent has custom folder, use it and use default as backup
            prompt_dir = files.get_abs_path("agents", self.config.profile, "prompts")
            dirs.insert(0, prompt_dir)
        prompt = files.read_prompt_file(
            file, _directories=dirs, agent=self, **kwargs
        )
        prompt = files.remove_code_fences(prompt)
        return prompt

    def get_data(self, field: str):
        return self.data.get(field, None)

    def set_data(self, field: str, value):
        self.data[field] = value

    async def hist_add_message(self, ai: bool, content: history.MessageContent, model: str = "", provider: str = "", id: str = "", protected: bool = False, sender_type: str = "", sender_id: str = "", **kwargs):
        """Delegated to agent_history.py (Issue #1200 P0.2)."""
        return await hist_add_message_impl(self, ai, content, model=model, provider=provider, id=id, protected=protected, sender_type=sender_type, sender_id=sender_id, **kwargs)

    async def hist_add_user_message(self, message: UserMessage, intervention: bool = False, protected: bool = False, sender_type: str = "", sender_id: str = ""):
        """Delegated to agent_history.py (Issue #1200 P0.2)."""
        return await hist_add_user_message_impl(self, message, intervention=intervention, protected=protected, sender_type=sender_type, sender_id=sender_id)

    async def hist_add_ai_response(self, message: str, model: str = "", provider: str = "", id: str = "", protected: bool = False, sender_type: str = "", sender_id: str = ""):
        """Delegated to agent_history.py (Issue #1200 P0.2)."""
        return await hist_add_ai_response_impl(self, message, model=model, provider=provider, id=id, protected=protected, sender_type=sender_type, sender_id=sender_id)

    async def hist_add_warning(self, message: history.MessageContent, id: str = ""):
        """Delegated to agent_history.py (Issue #1200 P0.2)."""
        return await hist_add_warning_impl(self, message, id=id)

    async def hist_add_tool_result(self, tool_name: str, tool_result: str, sender_type: str = "", sender_id: str = "", **kwargs):
        """Delegated to agent_history.py (Issue #1200 P0.2)."""
        return await hist_add_tool_result_impl(self, tool_name, tool_result, sender_type=sender_type, sender_id=sender_id, **kwargs)

    def concat_messages(
        self, messages
    ):  # TODO add param for message range, topic, history
        return self.history.output_text(human_label="user", ai_label="assistant")

    def get_chat_model(self):
        # Lazy load models if they haven't been loaded yet
        if not self._models_loaded:
            self._load_models()
        return self.get_data("chat_model")

    def get_utility_model(self):
        # Lazy load models if they haven't been loaded yet
        if not self._models_loaded:
            self._load_models()
        return self.get_data("utility_model")

    def get_browser_model(self):
        # Lazy load models if they haven't been loaded yet
        if not self._models_loaded:
            self._load_models()
        return self.get_data("browser_model")

    def get_embedding_model(self):
        # Lazy load models if they haven't been loaded yet
        if not self._models_loaded:
            self._load_models()
        return self.get_data("embeddings_model")

    def get_grok_fallback_model(self):
        """
        Returns a high-capacity model (Grok or Gemini) for context peak recovery.
        """
        if not self._models_loaded:
            self._load_models()
        
        # Check if Grok is explicitly enabled for fallback
        if settings.get_settings().get("grok_fallback_enabled", False):
            return models.get_chat_model("openai", settings.MODELS_DEFAULT_GROK)
        
        # Otherwise fallback to Gemini 3 Flash (1M) or core default
        return models.get_chat_model("google", settings.MODELS_DEFAULT_CORE)

    def refresh_models(self):
        """
        Fallback chain for unconfigured profiles:
        1. Explicitly configured active provider + agent0 model
        2. Explicitly configured active provider + grok-41-fast
        3. Hardcoded defaults (Venice)
        """
        _settings_helper = settings.get_settings()
        active_provider = _settings_helper.get("chat_model_provider")
        configured_providers = _settings_helper.get("providers", [])
        if active_provider and active_provider in configured_providers:
            first_provider = active_provider
        else:
            first_provider = configured_providers[0] if configured_providers else "venice"
        config = self.config
        
        # Resolve chat model: prioritize profile if it's set
        chat_provider = config.chat_model.provider
        chat_name = config.chat_model.name
        if config.profile:
             chat_provider = "role"
             chat_name = config.profile

        self.set_data("chat_model", models.get_chat_model(chat_provider, chat_name, config.chat_model))
        
        # Guard against None model configs
        util_provider = getattr(config.utility_model, "provider", None) if config.utility_model else None
        util_name = getattr(config.utility_model, "name", None) if config.utility_model else None
        self.set_data("utility_model", models.get_chat_model(util_provider, util_name, config.utility_model))
        
        embed_provider = getattr(config.embeddings_model, "provider", None) if config.embeddings_model else None
        embed_name = getattr(config.embeddings_model, "name", None) if config.embeddings_model else None
        self.set_data("embeddings_model", models.get_embedding_model(embed_provider, embed_name, config.embeddings_model))
        
        browser_provider = getattr(config.browser_model, "provider", None) if config.browser_model else None
        browser_name = getattr(config.browser_model, "name", None) if config.browser_model else None
        self.set_data("browser_model", models.get_browser_model(browser_provider, browser_name, config.browser_model))

    def refresh_config(self):
        """Refresh agent configuration and model wrappers from current context/project."""
        from python.initialize import initialize_agent
        # CRITICAL: Preserve the current agent_profile during refresh.
        # Without this, webhook-created agents (e.g., multiagentdev) lose their
        # profile when activate_project() → refresh_agents_config() → refresh_config()
        # calls initialize_agent() without overrides, falling back to the global
        # default profile from settings.json.
        override = {}
        if self.config and self.config.profile:
            override["agent_profile"] = self.config.profile
        self.config = initialize_agent(override_settings=override if override else None, context=self.context)
        self.refresh_models()
        
        # refresh subordinates if any
        sub = self.get_data(self.DATA_NAME_SUBORDINATE)
        if sub and hasattr(sub, "refresh_config"):
            sub.refresh_config()

    async def call_utility_model(
        self,
        system: str,
        message: str,
        callback: Callable[[str], Awaitable[None]] | None = None,
        background: bool = False,
    ):
        """Delegated to agent_models.py (Issue #1200 P0.2)."""
        return await call_utility_model_impl(self, system, message, callback=callback, background=background)
    async def call_chat_model(
        self,
        messages: list[BaseMessage],
        response_callback: Callable[[str, str], Awaitable[None]] | None = None,
        reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
        background: bool = False,
    ) -> Tuple[str, str, str, str]:
        """Delegated to agent_models.py (Issue #1200 P0.2)."""
        return await call_chat_model_impl(self, messages, response_callback=response_callback, reasoning_callback=reasoning_callback, background=background)

    async def rate_limiter_callback(
        self, message: str, key: str, total: int, limit: int
    ):
        """Delegated to agent_models.py (Issue #1200 P0.2)."""
        return await rate_limiter_callback_impl(self, message, key, total, limit)

    async def handle_intervention(self, progress: str = ""):
        """Delegated to agent_intervention.py (modularization)."""
        return await handle_intervention_impl(self, progress)

    async def wait_if_paused(self, timeout: float = 300):
        """Delegated to agent_intervention.py (modularization)."""
        return await wait_if_paused_impl(self, timeout)


    async def process_tools(self, msg: str):
        """Process tool requests — delegated to agent_process_tools.py (Issue #778)."""
        return await process_tools_impl(self, msg)

    async def handle_reasoning_stream(self, stream: str):
        await self.handle_intervention()
        await self.call_extensions(
            "reasoning_stream",
            loop_data=self.loop_data,
            text=stream,
        )

    async def handle_response_stream(self, stream: str):
        await self.handle_intervention()
        try:
            if len(stream) < 25:
                return  # no reason to try
            response = DirtyJson.parse_string(stream)
            if isinstance(response, dict):
                await self.call_extensions(
                    "response_stream",
                    loop_data=self.loop_data,
                    text=stream,
                    parsed=response,
                )

        except Exception as e:
            pass

    def get_tool(
        self, name: str, method: str | None = None, args: dict | None = None, message: str = "", loop_data: LoopData | None = None, **kwargs
    ):
        if args is None:
            args = {}
        from python.tools.unknown import Unknown
        from python.helpers.tool import Tool

        print(f"[DEBUG_AGENT] get_tool for: {name}")
        classes = []

        # try agent tools first
        if self.config.profile:
            try:
                classes = extract_tools.load_classes_from_file(
                    "agents/" + self.config.profile + "/tools/" + name + ".py", Tool  # type: ignore[arg-type]
                )
            except FileNotFoundError:
                pass  # Expected — most tools don't have profile-specific overrides
            except Exception as e:
                # RCA-webhook-20260612: Log non-FileNotFoundError exceptions.
                # SyntaxError here means a profile-specific tool file is broken.
                logger.warning(f"[get_tool] Failed to load profile tool '{name}' from agents/{self.config.profile}/tools/: {type(e).__name__}: {e}")

        # try default tools
        if not classes:
            try:
                classes = extract_tools.load_classes_from_file(
                    "python/tools/" + name + ".py", Tool  # type: ignore[arg-type]
                )
            except FileNotFoundError:
                pass  # Expected — Unknown tool will handle this
            except Exception as e:
                # RCA-webhook-20260612: CRITICAL — a SyntaxError or ImportError here
                # means a tool file exists but is broken. Previously this was silently
                # swallowed (bare except: pass), causing the tool to fall through to
                # Unknown, returning "Tool not found", and triggering delegation loops.
                logger.error(f"[get_tool] CRITICAL: Failed to load tool '{name}' from python/tools/: {type(e).__name__}: {e}")
        
        # Track and notify on new tool creation/discovery
        if classes:
            is_new = tool_registry.register_tool(name)
            if is_new:
                try:
                    # Log internally instead of sending UI notification to avoid recursion/deadlocks
                    self.log(
                        type="info",
                        heading=f"New Tool Created: {name}",
                        content=f"Agent has successfully instantiated a new tool: {name}",
                        verbose=True
                    )
                    PrintStyle(font_color="cyan", padding=True).print(f"New Tool Created: {name}")
                except Exception as e:
                    logger.debug(f"[get_tool] Non-critical: failed to log new tool '{name}': {e}")

        tool_class = classes[0] if classes else Unknown
        return tool_class(
            agent=self, name=name, method=method, args=args, message=message, loop_data=loop_data, **kwargs
        )

    async def call_extensions(self, extension_point: str, **kwargs) -> Any:
        return await call_extensions(extension_point=extension_point, agent=self, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# RCA-327: Null Response Ceiling — Module-level helpers
# Canonical implementation lives in python/helpers/agent_null_ceiling.py.
# Re-exported here for backward compatibility (tests import from python.agent).
# ──────────────────────────────────────────────────────────────────────────────
# _extract_middle_out_thoughts, update_null_iteration_counter, and
# check_null_ceiling_escalation are imported at module top from
# python.helpers.agent_null_ceiling and re-exported as module-level names.
