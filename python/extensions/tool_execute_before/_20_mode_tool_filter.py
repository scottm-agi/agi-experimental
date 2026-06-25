from __future__ import annotations
"""
Mode-based Tool Filtering Extension

Filters tool execution based on the current agent mode.
If a tool is not allowed in the current mode, it returns an error
message instead of executing the tool.

This implements RooCode's tool group permissions at runtime.
"""

from typing import Any
from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check
import logging

logger = logging.getLogger("agix.mode_tool_filter")

# Try to import mode manager
try:
    from python.helpers.mode_manager import get_mode_manager
    from python.helpers.tool_groups import ToolGroupFilter, get_groups_for_tool
    MODE_SUPPORT = True
except ImportError:
    MODE_SUPPORT = False


class ModeToolFilter(Extension):
    """
    Extension that filters tool execution based on mode permissions.
    
    Runs before each tool execution to check if the tool is allowed
    in the current agent mode. If not allowed, blocks execution and
    returns an error message suggesting appropriate alternatives.
    """
    
    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs
    ):
        """
        Check if tool is allowed in current mode before execution.
        
        Args:
            tool_args: Arguments passed to the tool
            tool_name: Name of the tool being executed
            **kwargs: Additional arguments
            
        Returns:
            None if tool is allowed, or Response with error if blocked
        """
        if not MODE_SUPPORT:
            return None  # No mode support, allow all tools
        
        # Get current mode from python.agent data
        current_mode = self.agent.get_data("multiagentdev_mode")
        if not current_mode:
            return None  # No mode set, allow all tools
        
        # Get mode manager and check tool permissions
        manager = get_mode_manager()
        mode_config = manager.get_mode(current_mode)
        
        if not mode_config:
            return None  # Unknown mode, allow all tools
        
        # ── HUB PROFILE EXEMPTION: Orchestrators always retain delegation ──
        # RCA-2026-04-28 MSR Smoke 1777396305: Hub orchestrators (multiagentdev,
        # default, alex) get trapped in Code/Frontend Mode which blocks delegation
        # tools, creating a deadlock. The ontology profile is the source of truth
        # for capabilities — if the profile has 'orchestration' category, the mode
        # filter should NEVER block delegation tools. This lets the hub validate,
        # track, and dispatch work regardless of mode drift.
        # Dynamic: any profile added with 'orchestration' category auto-gets this.
        try:
            from python.helpers.tool_selector import ToolSelector
            from python.helpers.tool_groups import TOOL_GROUPS
            
            profile = getattr(self.agent.config, "profile", None) or "default"
            selector = ToolSelector.get_instance()
            profile_cats = selector._ontology.get("profiles", {}).get(
                profile, selector._ontology.get("profiles", {}).get("default", [])
            )
            
            if "orchestration" in profile_cats:
                delegation_tools = set(TOOL_GROUPS.get("delegate", []))
                if tool_name in delegation_tools:
                    logger.info(
                        f"[HUB_EXEMPT] Allowing '{tool_name}' for hub profile "
                        f"'{profile}' despite mode '{current_mode}' restriction. "
                        f"Hub orchestrators always retain delegation tools."
                    )
                    return None  # Hub profiles always keep delegation tools
        except Exception as e:
            logger.debug(f"[HUB_EXEMPT] Check failed (fail-open): {e}")
        
        # Create filter for current mode
        tool_filter = ToolGroupFilter.from_mode(current_mode)
        
        # Check if tool is allowed
        if tool_filter.is_tool_allowed(tool_name):
            # Tool is allowed, check file restrictions if applicable
            if tool_name == "code_execution_tool" and mode_config.restrictions:
                # Check if this is a file write operation
                blocked = self._check_file_restrictions(tool_args, mode_config)
                if blocked:
                    return blocked
            return None
        
        # Tool is NOT allowed in this mode
        # ── ESCAPE HATCH: Auto-revert delegation-locked agents ──
        # When a subordinate was delegated with a specific profile (e.g., "code")
        # but auto-mode-suggestion switched it to a different mode (e.g., "architect"),
        # the tool filter blocks tools the subordinate NEEDS. Instead of blocking
        # (which causes a death spiral), auto-revert to the delegated profile.
        # Root cause: MSR Smoke Test 1777237623, entries 228-260 — 5x same-message loop.
        delegated_profile = self.agent.get_data("_delegated_profile")
        if (self.agent.get_data("_mode_locked_by_delegation")
                and delegated_profile
                and delegated_profile != current_mode):
            try:
                from python.extensions.agent_init._60_mode_init import set_agent_mode
                success = set_agent_mode(self.agent, delegated_profile)
                if success:
                    logger.warning(
                        f"[ESCAPE_HATCH] Auto-reverted mode '{current_mode}' → "
                        f"'{delegated_profile}' for delegation-locked agent "
                        f"'{self.agent.agent_name}'. Tool '{tool_name}' was blocked "
                        f"but the agent was delegated as profile='{delegated_profile}'. "
                        f"Allowing tool to proceed."
                    )
                    # Tool is now allowed — return None to proceed
                    return None
            except Exception as e:
                logger.error(
                    f"[ESCAPE_HATCH] Failed to revert mode for "
                    f"'{self.agent.agent_name}': {e}"
                )

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "mode_tool_filter", suffix=tool_name):
            return None

        logger.warning(
            f"Tool '{tool_name}' blocked in mode '{current_mode}'. "
            f"Allowed groups: {mode_config.tool_groups}"
        )
        
        # Generate helpful error message
        error_msg = self._generate_blocked_message(
            tool_name, current_mode, mode_config
        )
        
        # Store blocked tool info for potential mode switch suggestion
        self._record_blocked_tool(tool_name, current_mode)
        
        # Return error response that will be shown to the agent

        return Response(
            message=error_msg,
            break_loop=False,
        )
    
    def _check_file_restrictions(
        self,
        tool_args: dict[str, Any] | None,
        mode_config
    ) -> Response | None:
        """
        Check file pattern restrictions for edit operations.
        
        Args:
            tool_args: Tool arguments (may contain file paths)
            mode_config: Current mode configuration
            
        Returns:
            Response with error if file is restricted, None otherwise
        """
        if not tool_args or not mode_config.restrictions:
            return None
        
        # Extract code from tool args
        code = tool_args.get("code", "") or tool_args.get("runtime", "")
        if not code:
            return None
        
        # Exempt commands running inside the project sandbox —
        # if the command cd's into usr/projects/, file writes there are safe
        import re
        project_cd_match = re.search(
            r'cd\s+(/agix/usr/projects/[^\s&;]+)', code
        )
        if project_cd_match:
            return None  # Operating within project sandbox, allow all writes
        
        # Check for file write patterns in code
        # Common file write patterns
        write_patterns = [
            r'open\(["\']([^"\']+)["\'].*["\']w["\']',  # Python open() with write
            r'with open\(["\']([^"\']+)["\'].*["\']w["\']',  # Python with open()
            r'writeFile\(["\']([^"\']+)["\']',  # Node.js writeFile
            # fs.write[A-Za-z]* constrains to method name (writeFile, writeFileSync)
            # avoiding greedy .* that matches across semicolons into console.log()
            r'fs\.write[A-Za-z]*\(["\']([^"\']+)["\']',  # Node.js fs.write*
            r'echo.*>\s*([^\s]+)',  # Shell redirect
            r'cat.*>\s*([^\s]+)',  # Shell cat redirect
        ]
        
        for pattern in write_patterns:
            matches = re.findall(pattern, code)
            for filepath in matches:
                if not mode_config.restrictions.is_file_allowed(filepath):
                    # Escape hatch — prevent infinite blocking loops
                    if gate_check(self.agent.data, "mode_file_restrict", suffix=filepath):
                        return None
                    return Response(
                        message=(
                            f"⚠️ **File Edit Blocked**\n\n"
                            f"Cannot write to `{filepath}` in **{mode_config.display_name}**.\n\n"
                            f"Allowed file patterns: {mode_config.restrictions.get_allowed_file_patterns()}\n\n"
                            f"**Options:**\n"
                            f"1. Switch to **Code mode** for full file access\n"
                            f"2. Delegate to a Code mode subordinate: "
                            f"`call_subordinate(message='...', mode='code')`"
                        ),
                        break_loop=False,
                    )
        
        return None
    
    def _generate_blocked_message(
        self,
        tool_name: str,
        current_mode: str,
        mode_config
    ) -> str:
        """
        Generate a helpful error message when a tool is blocked.
        
        Args:
            tool_name: Name of the blocked tool
            current_mode: Current mode slug
            mode_config: Current mode configuration
            
        Returns:
            Formatted error message with suggestions
        """
        # Get tool's groups
        tool_groups = get_groups_for_tool(tool_name)
        
        # Suggest modes that have this tool
        manager = get_mode_manager()
        suggested_modes = []
        for mode_slug in manager.list_mode_slugs():
            mode = manager.get_mode(mode_slug)
            if mode and any(g in mode.tool_groups for g in tool_groups):
                suggested_modes.append(mode.display_name)
        
        # Build message
        msg = f"⚠️ **Tool Not Available in {mode_config.display_name}**\n\n"
        msg += f"The tool `{tool_name}` is not available in the current mode.\n\n"
        msg += f"**Current mode:** {mode_config.display_name}\n"
        msg += f"**Available tool groups:** {', '.join(mode_config.tool_groups)}\n"
        msg += f"**Tool requires:** {', '.join(tool_groups) if tool_groups else 'unknown'}\n\n"
        
        if suggested_modes:
            msg += f"**Modes with this tool:** {', '.join(suggested_modes)}\n\n"
        
        # Only suggest delegation if the agent actually has orchestration tools
        has_orchestration = "orchestration" in (mode_config.tool_groups or [])
        msg += "**What to do:**\n"
        if has_orchestration:
            msg += "1. Use `call_subordinate` to delegate this task to the appropriate agent profile:\n"
            msg += f"   `call_subordinate(message='your task', profile='code')`\n"
        else:
            msg += "1. **Do NOT retry this tool** — it will be blocked again.\n"
            msg += "2. Complete your task using your available tools, or\n"
            msg += ("3. Report back to your parent with `response` and include this message: "
                    f"'Task requires `{tool_name}` which is not available in my profile. "
                    f"Please re-delegate to a profile with access to {', '.join(tool_groups) if tool_groups else 'this tool'}.'\n")
        
        return msg
    
    def _record_blocked_tool(self, tool_name: str, mode: str):
        """
        Record blocked tool for analytics and mode switch suggestions.
        
        Phase 2 Hardening (S3): After 3+ blocks, detect mode conflict and
        inject escalation guidance for the parent orchestrator.
        
        Args:
            tool_name: Name of the blocked tool
            mode: Mode that blocked the tool
        """
        blocked_tools = self.agent.get_data("_blocked_tools") or []
        blocked_tools.append({
            "tool": tool_name,
            "mode": mode,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        })
        # Keep last 10 blocked tools
        self.agent.set_data("_blocked_tools", blocked_tools[-10:])

        # ── Phase 2 Hardening: Mode-Conflict Escalation (S3) ──
        # If the same agent keeps hitting mode blocks (3+ unique tools),
        # the agent is stuck — escalate to parent with re-delegation advice.
        try:
            from python.helpers.mode_conflict_detector import detect_mode_conflict
            conflict = detect_mode_conflict(blocked_tools, mode)
            if conflict.get("is_conflict"):
                # Inject warning into agent history for parent visibility
                import asyncio
                escalation_msg = conflict["escalation"]
                asyncio.ensure_future(
                    self.agent.hist_add_warning(escalation_msg)
                )
                logger.warning(
                    f"[MODE CONFLICT] {conflict['unique_tools']} unique tools "
                    f"blocked in mode={mode} — escalation injected"
                )
        except Exception as mc_err:
            logger.debug(f"Mode conflict detection failed: {mc_err}")
