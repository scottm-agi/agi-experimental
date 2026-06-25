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

# Lazy import to avoid circular dependency
from python.tools.call_subordinate_helpers import (
    _get_boomerang_context,
    _inject_e2e_fail_routing,
    _check_phase_order_before_delegation,
    _check_budget_before_first_delegation,
    format_subordinate_context_brief,
    _get_subordinate_timeout,
    _build_attempt_record,
    _extract_last_error,
    _cleanup_subordinate_ports,
)









# Try to import mode manager for MultiAgentDev support
try:
    from python.helpers.mode_manager import get_mode_manager
    from python.extensions.agent_init._60_mode_init import set_agent_mode, get_agent_mode
    MODE_SUPPORT = True
except ImportError:
    MODE_SUPPORT = False








class Delegation(Tool):

    """
    Tool for delegating tasks to subordinate agents.
    
    Features:
    - Creates and manages subordinate agents
    - Coordinates rate limiting between master and subordinate agents
    - Traces subordinate creation and completion
    - Handles rate limit backoff with master agent waiting
    """

    async def execute(self, message="", reset="", mode="", relay_response="", **kwargs):
        from python.tools.call_subordinate_execute import execute_delegation
        return await execute_delegation(self, message=message, reset=reset, mode=mode, relay_response=relay_response, **kwargs)

    async def _run_subordinate_with_coordination(self, subordinate: Agent, provider_key: str) -> str:
        from python.tools.call_subordinate_execute import run_subordinate_with_coordination
        return await run_subordinate_with_coordination(self, subordinate, provider_key)

    def _store_rate_limit_stats(self, retries: int, total_wait: float):
        """
        Store rate limit statistics for potential summary display.
        This allows the master to show a summary without cluttering the UI.
        """
        rate_limit_info = self.agent.get_data("_rate_limit_info") or {
            "total_retries": 0,
            "total_wait": 0.0,
            "events": 0
        }
        rate_limit_info["total_retries"] = rate_limit_info.get("total_retries", 0) + retries
        rate_limit_info["total_wait"] = rate_limit_info.get("total_wait", 0.0) + total_wait
        rate_limit_info["events"] = rate_limit_info.get("events", 0) + 1
        rate_limit_info["last_retries"] = retries
        rate_limit_info["last_wait"] = total_wait
        self.agent.set_data("_rate_limit_info", rate_limit_info)

    def _should_force_profile_reset(self, existing_subordinate, requested_profile: str) -> bool:
        """
        Detect profile mismatch between existing subordinate and the requested profile.

        ROOT CAUSE FIX (RCA 215): When the orchestrator delegates first to profile=A,
        then to profile=B, the subordinate slot still holds profile=A. Without this
        check, the profile= argument is silently ignored and the stale agent runs —
        hitting PROFILE_ENFORCEMENT blocks, timing out, and wasting ~600s per cycle.

        Returns True if the existing subordinate should be destroyed and replaced.
        """
        if existing_subordinate is None:
            return False
        if not requested_profile:
            return False

        existing_profile = getattr(
            getattr(existing_subordinate, "config", None), "profile", "default"
        ) or "default"

        if existing_profile != requested_profile:
            logger.info(
                f"PROFILE SWITCH: existing subordinate has profile='{existing_profile}' "
                f"but requested profile='{requested_profile}'. Automatically replacing "
                f"subordinate to support multi-phase delegation. (RCA 215)"
            )
            return True

        return False

    def _determine_subordinate_mode(self, explicit_mode: str, message: str, profile: str = "") -> str:
        """
        Determine the mode for the subordinate agent.
        
        Priority:
        1. Explicitly specified mode parameter
        2. Profile-derived mode (when profile is explicitly passed and not "default")
        3. Mode suggested by task content
        4. Inherit from parent agent
        5. Default mode
        
        Args:
            explicit_mode: Mode explicitly specified in tool call
            message: Task message (used for auto-suggestion)
            profile: Agent profile (e.g., "code", "debug") — used to derive
                     mode when no explicit mode is provided. Prevents keyword
                     suggestion from overriding profile intent.
            
        Returns:
            Mode slug to use for subordinate
        """
        if not MODE_SUPPORT:
            return ""
        
        # 1. Use explicit mode if provided
        if explicit_mode and explicit_mode.strip():
            manager = get_mode_manager()
            if manager.get_mode(explicit_mode.strip()):
                return explicit_mode.strip()
            else:
                logger.warning(f"Unknown mode '{explicit_mode}', will use default")
        
        # 2. Hub profile → orchestrator mode mapping
        #    RCA-2026-04-28 MSR Smoke 1777396305: Hub profiles (multiagentdev,
        #    default, alex, etc.) that have 'orchestration' in their ontology
        #    categories should ALWAYS get 'orchestrator' mode. Without this,
        #    they fall through to keyword suggestion or inherit parent's 'code'
        #    mode, causing the mode-profile deadlock.
        if profile and profile.strip():
            profile_slug = profile.strip()
            try:
                from python.helpers.tool_selector import ToolSelector
                selector = ToolSelector.get_instance()
                profile_cats = selector._ontology.get("profiles", {}).get(
                    profile_slug,
                    selector._ontology.get("profiles", {}).get("default", [])
                )
                if "orchestration" in profile_cats:
                    manager = get_mode_manager()
                    if manager.get_mode("orchestrator"):
                        logger.info(
                            f"Hub profile '{profile_slug}' mapped to 'orchestrator' mode "
                            f"(ontology has 'orchestration' category)"
                        )
                        return "orchestrator"
            except Exception as e:
                logger.debug(f"Hub profile check failed: {e}")
        
        # 3. Profile-derived mode: when profile is explicitly passed (not "default"),
        #    use it as the mode. This prevents keyword suggestion from overriding
        #    an explicit profile delegation (Fix F3, Iteration 159).
        #    E.g., profile="code" + message="design the architecture" → mode="code"
        if profile and profile.strip() and profile.strip() != "default":
            manager = get_mode_manager()
            profile_slug = profile.strip()
            if manager.get_mode(profile_slug):
                logger.debug(
                    f"Using profile-derived mode '{profile_slug}' "
                    f"(overrides keyword suggestion)"
                )
                return profile_slug
        
        # 4. Try to suggest mode from task content
        manager = get_mode_manager()
        suggested = manager.suggest_mode_for_task(message)
        if suggested:
            logger.debug(f"Auto-suggested mode '{suggested}' for task")
            return suggested
        
        # 5. Inherit from parent agent
        parent_mode = self.agent.get_data("multiagentdev_mode")
        if parent_mode:
            return parent_mode
        
        # 6. Use default mode
        return manager.default_mode
    
    def _apply_mode_to_subordinate(self, subordinate: Agent, mode_slug: str, profile: str = ""):
        """
        Apply mode configuration to a subordinate agent.
        
        Args:
            subordinate: The subordinate agent
            mode_slug: Mode slug to apply
            profile: The agent profile name (stored as _delegated_profile
                     for the escape hatch in _20_mode_tool_filter)
        """
        if not MODE_SUPPORT:
            return
        
        try:
            success = set_agent_mode(subordinate, mode_slug)
            if success:
                # Lock the mode so accidental mode changes don't override delegation intent
                # based on keyword matching in the delegation message
                subordinate.set_data("_mode_locked_by_delegation", True)
                # Store the delegated PROFILE name (not mode slug) so the escape
                # hatch in _20_mode_tool_filter can correctly identify mode drift.
                # RCA-2026-04-28: Previously stored mode_slug, which meant
                # _delegated_profile == current_mode → escape hatch never fired.
                delegated_name = profile.strip() if profile and profile.strip() else mode_slug
                subordinate.set_data("_delegated_profile", delegated_name)
                logger.info(f"Applied mode '{mode_slug}' to subordinate {subordinate.agent_name} (locked)")
            else:
                logger.warning(f"Failed to apply mode '{mode_slug}' to subordinate")
        except Exception as e:
            logger.error(f"Error applying mode to subordinate: {e}")

    def _load_swarm_instructions(self) -> str | None:
        """
        Load swarm-level instructions from the orchestrator's profile directory.
        
        Walks up the agent hierarchy to find the orchestrator (the topmost agent
        with a non-default profile). Reads _swarm_instructions.md from that
        profile's agents/ directory.
        
        Results are cached per orchestrator profile to avoid repeated file reads.
        
        Returns:
            Swarm instructions content, or None if not found.
        """
        global _swarm_instructions_cache
        
        # Walk up the hierarchy to find the orchestrator profile
        orchestrator_profile = None
        walker = self.agent
        while walker is not None:
            profile = getattr(walker.config, "profile", "default") or "default"
            if profile != "default":
                orchestrator_profile = profile
            superior = walker.get_data(Agent.DATA_NAME_SUPERIOR)
            if superior is None:
                break
            walker = superior
        
        # Also check the current agent's profile chain — if the current agent
        # IS the orchestrator (e.g., multiagentdev delegating), use its profile
        if not orchestrator_profile:
            current_profile = getattr(self.agent.config, "profile", "default") or "default"
            if current_profile != "default":
                orchestrator_profile = current_profile
        
        if not orchestrator_profile:
            return None
        
        # Check cache
        if orchestrator_profile in _swarm_instructions_cache:
            return _swarm_instructions_cache[orchestrator_profile]
        
        # Try to read _swarm_instructions.md from the orchestrator's profile dir
        try:
            from python.helpers import files
            agents_dir = files.get_abs_path("agents", orchestrator_profile)
            swarm_path = os.path.join(agents_dir, "_swarm_instructions.md")
            
            if os.path.exists(swarm_path):
                with open(swarm_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    logger.info(f"Loaded swarm instructions from {orchestrator_profile}/_swarm_instructions.md ({len(content)} chars)")
                    _swarm_instructions_cache[orchestrator_profile] = content
                    return content
            
            _swarm_instructions_cache[orchestrator_profile] = None
            return None
            
        except Exception as e:
            logger.warning(f"Failed to load swarm instructions for {orchestrator_profile}: {e}")
            _swarm_instructions_cache[orchestrator_profile] = None
            return None

    # ── Attachment Forwarding (Forgejo #977) ──

    _ATTACHMENT_MAX_BYTES = 50_000  # 50KB cap per delegation

    def _collect_root_attachments(self) -> str:
        """
        Walk up the agent hierarchy to find the root agent's stored attachment
        paths. Read each file and return concatenated content.

        Attachments are stored in agent.data["_root_attachments"] by the API
        handler when processing the original user message.

        Returns:
            Concatenated attachment content, or empty string if none.
        """
        # Walk up to find root agent (the one with no superior)
        root = self.agent
        while True:
            superior = root.get_data(Agent.DATA_NAME_SUPERIOR)
            if superior is None:
                break
            root = superior

        # Check for stored attachment paths
        attachment_paths = root.data.get("_root_attachments", [])
        if not attachment_paths:
            return ""

        parts = []
        total_size = 0

        for path in attachment_paths:
            try:
                if not os.path.exists(path):
                    logger.warning(f"Attachment file not found: {path}")
                    continue

                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                remaining = self._ATTACHMENT_MAX_BYTES - total_size
                if remaining <= 0:
                    break

                if len(content) > remaining:
                    content = content[:remaining] + f"\n\n[TRUNCATED — file was {len(content)} chars, cap is {self._ATTACHMENT_MAX_BYTES}]"

                filename = os.path.basename(path)
                parts.append(f"### Attachment: `{filename}`\n\n{content}")
                total_size += len(content)

            except Exception as e:
                logger.warning(f"Failed to read attachment {path}: {e}")
                continue

        return "\n\n---\n\n".join(parts)

    def _inject_attachment_context(self, message: str) -> str:
        """
        If the root agent has attachments, prepend their content to the
        delegation message so subordinates receive the full user context.

        Args:
            message: Original delegation message

        Returns:
            Message with attachment content prepended, or original if none.
        """
        attachment_content = self._collect_root_attachments()
        if not attachment_content:
            return message

        return (
            f"## Original User Attachments\n"
            f"The user attached the following document(s). You MUST read and follow ALL requirements:\n\n"
            f"{attachment_content}\n\n---\n\n"
            + message
        )

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://communication {self.agent.agent_name}: Calling Subordinate Agent",
            content="",
            kvps=self.args,
        )

