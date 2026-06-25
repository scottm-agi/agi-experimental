"""
Supervisor Tools — Escalation Ramp & Redirect.
===============================================
Extracted from tools.py during modularization.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

from .base import logger


# ═══════════════════════════════════════════════════════════════════════
# RCA-458: Profile-aware supervisor redirects.
# The supervisor LLM was suggesting write tools (replace_in_file) to
# ALL profiles, including E2E which doesn't have files_write. This
# caused deadlocks where E2E tried write tools → PROFILE_ENFORCEMENT
# blocked → supervisor redirect → try again → deadlock.
#
# Root cause (deepened): The supervisor didn't understand the agent's
# PURPOSE. Guards (PROFILE_ENFORCEMENT) are failsafes, not acceptable
# behavior. The fix must make the supervisor understand each profile's
# role so it gives appropriate guidance FIRST TIME.
# ═══════════════════════════════════════════════════════════════════════

# Full tool list available for supervisor redirects
_ALL_REDIRECT_TOOLS = {
    "read_file": "read a file's content",
    "replace_in_file": "targeted search/replace on specific sections of an existing file",
    "apply_diff": "SEARCH/REPLACE blocks for multi-section changes to existing files",
    "write_to_file": "create new files or full rewrites (only if replace_in_file fails 3x)",
    "code_execution_tool": "run shell commands (NOT for reading file content)",
    "call_subordinate": "delegate to another agent",
    "response": "return results to the orchestrator/user",
}

# Tools that require files_write ontology category
_WRITE_TOOLS = {"replace_in_file", "apply_diff", "write_to_file"}

# ── Profile role context for supervisor understanding ──
# The supervisor MUST understand what each profile's job is. This prevents
# it from suggesting inappropriate actions (e.g., telling E2E to write files).
_PROFILE_ROLE_CONTEXT = {
    "e2e": {
        "role": "E2E Testing Agent",
        "purpose": "Runs end-to-end tests, validates deployments, and verifies full-stack behavior. Does NOT create or modify source files.",
        "can_write": False,
        "can_exec": True,
        "when_stuck": "Report test results and findings via `response` or `save_deliverable`. If tests reveal bugs, describe them clearly — the orchestrator will delegate fixes to a code agent.",
    },
    "code": {
        "role": "Full-Stack Developer",
        "purpose": "Writes, edits, and debugs code. Creates new files, modifies existing ones, runs builds and tests.",
        "can_write": True,
        "can_exec": True,
        "when_stuck": "Try a different approach: read the file first with `read_file`, then use `replace_in_file` for surgical edits. If replace_in_file fails 3x, use `write_to_file`. Run `code_execution_tool` to test.",
    },
    "researcher": {
        "role": "Research Agent",
        "purpose": "Researches topics, reads documentation, gathers information. Does NOT write code or modify files.",
        "can_write": False,
        "can_exec": False,
        "when_stuck": "Summarize findings and report back via `response`. Include all sources, key findings, and recommendations.",
    },
    "architect": {
        "role": "System Architect",
        "purpose": "Designs system architecture, analyzes code structure, creates architectural plans. Does NOT write code directly.",
        "can_write": False,
        "can_exec": False,
        "when_stuck": "Report architectural analysis and design recommendations via `response`.",
    },
    "frontend": {
        "role": "UI/UX Designer",
        "purpose": "Designs user interfaces, creates design specifications, analyzes visual requirements. Does NOT write code directly.",
        "can_write": False,
        "can_exec": False,
        "when_stuck": "Report design specifications and recommendations via `response`.",
    },
    "debug": {
        "role": "Debug Agent",
        "purpose": "Diagnoses and debugs issues by reading code and running commands. Does NOT modify source files directly.",
        "can_write": False,
        "can_exec": True,
        "when_stuck": "Run diagnostic commands with `code_execution_tool`, read files with `read_file`, then report findings via `response`.",
    },
    "review": {
        "role": "Code Reviewer",
        "purpose": "Reviews code quality, identifies issues, suggests improvements. Does NOT modify source files.",
        "can_write": False,
        "can_exec": False,
        "when_stuck": "Complete your review findings and report via `response`.",
    },
    "hacker": {
        "role": "Full-Stack Developer (Extended)",
        "purpose": "Writes, edits, and debugs code with extended capabilities including web search.",
        "can_write": True,
        "can_exec": True,
        "when_stuck": "Try a different approach: read the file first, then use `replace_in_file` for surgical edits.",
    },
    "multiagentdev": {
        "role": "Orchestrator",
        "purpose": "Coordinates work across multiple agents. Delegates tasks, does NOT write code directly.",
        "can_write": False,
        "can_exec": False,
        "when_stuck": "Delegate the remaining work to appropriate subordinate agents via `call_subordinate`, or report status via `response`.",
    },
}


def get_profile_context(profile: Optional[str] = None) -> str:
    """Get a human-readable description of the agent's profile for supervisor context.

    Returns a string describing the agent's role, purpose, capabilities,
    and what it should do when stuck. This gives the supervisor LLM full
    understanding of the agent it's trying to help.
    """
    if not profile or profile not in _PROFILE_ROLE_CONTEXT:
        return f"Profile: {profile or 'unknown'} (no specific role context available)"

    ctx = _PROFILE_ROLE_CONTEXT[profile]
    lines = [
        f"**Agent Profile**: {profile} ({ctx['role']})",
        f"**Purpose**: {ctx['purpose']}",
        f"**Can write files**: {'YES' if ctx['can_write'] else 'NO — this agent CANNOT create or modify files'}",
        f"**Can execute commands**: {'YES' if ctx['can_exec'] else 'NO'}",
        f"**When stuck**: {ctx['when_stuck']}",
    ]
    return "\n".join(lines)


def get_profile_aware_tool_list(profile: Optional[str] = None) -> list:
    """Return the list of valid redirect tools filtered by agent profile.

    RCA-458: The supervisor redirect was profile-unaware — it suggested
    replace_in_file/write_to_file to ALL agents, including E2E which
    doesn't have files_write in its ontology categories.

    Args:
        profile: Agent profile name (e.g., "code", "e2e", "researcher").
                 None or unknown profiles get the full list.

    Returns:
        List of tool names that this profile can actually use.
    """
    if not profile:
        return list(_ALL_REDIRECT_TOOLS.keys())

    # Check if profile has files_write in ontology
    try:
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        allowed = selector.get_allowed_tools(profile)
        # Filter: only include tools that are in the profile's allowed set
        # For tools not in ontology (like response, call_subordinate), always include
        return [
            tool for tool in _ALL_REDIRECT_TOOLS
            if tool in allowed or tool not in _WRITE_TOOLS
        ]
    except Exception:
        # Fail open — if ontology lookup fails, return full list
        return list(_ALL_REDIRECT_TOOLS.keys())


def format_tool_list_for_prompt(profile: Optional[str] = None) -> str:
    """Format the profile-filtered tool list for inclusion in supervisor prompts.

    Returns a markdown-formatted tool list string with profile role context.
    """
    tools = get_profile_aware_tool_list(profile)
    lines = []
    for tool in tools:
        desc = _ALL_REDIRECT_TOOLS.get(tool, "")
        lines.append(f"- `{tool}` — {desc}")
    return "\n".join(lines)


class EscalationMixin:
    """Mixin providing escalation ramp and request_redirect for SupervisorAgent."""

    # Per-agent escalation dedup guard (class-level shared state)
    _escalation_history: Dict[str, List[datetime]] = {}
    MAX_ESCALATIONS_PER_AGENT = 2
    ESCALATION_WINDOW_SECONDS = 600  # 10 minutes

    # Escalation ramp (Forgejo #366)
    _escalation_levels: Dict[str, int] = {}
    MAX_ESCALATION_LEVEL = 5

    def get_escalation_level(self, agent_id: str) -> int:
        """Get current escalation level for an agent (0 = no escalation)."""
        return self._escalation_levels.get(agent_id, 0)
    
    def increment_escalation(self, agent_id: str) -> int:
        """Increment escalation level for an agent. Returns new level."""
        current = self._escalation_levels.get(agent_id, 0)
        new_level = min(current + 1, self.MAX_ESCALATION_LEVEL)
        self._escalation_levels[agent_id] = new_level
        logger.info(f"[SUPERVISOR] Escalation ramp: agent {agent_id} → level {new_level}/{self.MAX_ESCALATION_LEVEL}")
        return new_level
    
    def reset_escalation(self, agent_id: str) -> None:
        """Reset escalation level for an agent (called when agent makes progress)."""
        if agent_id in self._escalation_levels:
            old_level = self._escalation_levels[agent_id]
            if old_level > 0:
                logger.info(f"[SUPERVISOR] Escalation ramp reset: agent {agent_id} level {old_level} → 0 (progress detected)")
            del self._escalation_levels[agent_id]
    
    def get_escalation_directive(self, agent_id: str) -> str:
        """Get LLM prompt directive based on current escalation level.
        
        Returns empty string at level 0, progressively stronger directives
        at higher levels to force the LLM to pick stronger tools.
        """
        level = self.get_escalation_level(agent_id)
        
        if level == 0:
            return ""
        elif level == 1:
            return (
                "\n\n## ⚠️ ESCALATION LEVEL 1: Previous guidance was INEFFECTIVE\n"
                "Your previous `provide_guidance` intervention did NOT resolve the issue. "
                "The agent is STILL stuck. You MUST now use `redirect_approach` to suggest "
                "a completely different strategy. Do NOT use `provide_guidance` again — "
                "it has already failed.\n"
            )
        elif level == 2:
            return (
                "\n\n## 🔴 ESCALATION LEVEL 2: Two interventions have FAILED\n"
                "Both `provide_guidance` and `redirect_approach` have failed to unstick "
                "this agent. You MUST now use `simplify_task` to break the work into "
                "explicit atomic steps, or `inject_hint` with a specific, concrete "
                "technical solution. Do NOT use gentle approaches — they have failed "
                "twice already.\n"
            )
        elif level == 3:
            return (
                "\n\n## 🚨 ESCALATION LEVEL 3: Strong technical intervention needed\n"
                "Three supervisor interventions have failed. You MUST now use "
                "`nudge_agent` with a VERY SPECIFIC, concrete technical solution — "
                "not general advice. Read the agent's chat history, identify the "
                "EXACT error or stall point, and inject a step-by-step fix. "
                "Alternatively, use `inject_hint` with the precise command or "
                "code snippet the agent needs. Focus exclusively on autonomous "
                "guidance tools — exhaust all guidance options first.\n"
            )
        elif level == 4:
            return (
                "\n\n## 🔴 ESCALATION LEVEL 4: Maximum autonomous intervention\n"
                "Four interventions have failed. Use `nudge_agent` with an "
                "EXTREMELY directive message: tell the agent EXACTLY what to do "
                "step by step. If the agent is stuck on a tool error, tell it to "
                "try a completely different approach. If stuck on git auth, tell it to "
                "use `secret_get` to retrieve credentials, then run git commands directly. "
                "If stuck on tests, tell it to skip and "
                "move forward. Be maximally prescriptive.\n"
            )
        else:  # level >= 5
            return (
                "\n\n## ⛔ ESCALATION LEVEL 5: ABSOLUTE LAST RESORT\n"
                "Five consecutive interventions have all failed. All autonomous "
                "guidance attempts are exhausted. You may now use `escalate_human` "
                "to request human intervention. This is the absolute last resort — "
                "the 75-iteration LoopLimiter is the only other safety net.\n"
            )
    
    # =========================================================================
    # Escape Hatch — Fast-Path Redirect (RCA-252)
    # =========================================================================

    async def request_redirect(self, agent, escape_context: dict) -> Optional[str]:
        """
        Fast-path redirect for the escape hatch mechanism.

        Called by Agent._attempt_supervisor_redirect() when a hard-stop
        condition triggers. Generates a context-aware corrective instruction
        using the utility LLM, based on the agent's recent history and the
        escape hatch reason.

        RCA-263: For SEMANTIC repeats (same tool+args, different thoughts),
        the supervisor can now decide CONTINUE_AS_IS if the agent is doing
        legitimate work (e.g., file restoration, build recovery). For
        EXACT-MATCH repeats (truly identical messages), CONTINUE is NOT
        offered — the agent is genuinely stuck.

        Args:
            agent: The Agent instance requesting redirect
            escape_context: Dict with 'reason', 'type', 'repeat_count'

        Returns:
            - Redirect instruction string (supervisor wants to redirect)
            - "CONTINUE_AS_IS" (supervisor says agent is doing legitimate work)
            - None if generation fails
        """
        try:
            agent_name = getattr(agent, 'agent_name', 'unknown')
            # RCA-458: Get agent profile for profile-aware tool filtering
            agent_profile = getattr(getattr(agent, 'config', None), 'profile', None)
            reason = escape_context.get('reason', 'Unknown hard-stop reason')
            repeat_count = escape_context.get('repeat_count', 0)
            escape_type = escape_context.get('type', 'unknown')

            # RCA-458: Build profile-filtered tool list for prompts
            valid_tools_text = format_tool_list_for_prompt(agent_profile)
            valid_tools_names = ', '.join(
                f'`{t}`' for t in get_profile_aware_tool_list(agent_profile)
            )
            # RCA-458 deepened: Give supervisor full understanding of agent's role
            profile_context = get_profile_context(agent_profile)

            # RCA-289: Extract enriched tool error context (may not be present in legacy calls)
            failed_tool = escape_context.get('failed_tool', '')
            failed_tool_sig = escape_context.get('failed_tool_sig', '')
            last_tool_error = escape_context.get('last_tool_error', '')

            # RCA-289: Get escalation directive from the ramp (wires into pipeline)
            escalation_directive = ""
            try:
                escalation_directive = self.get_escalation_directive(agent_name)
            except Exception:
                pass  # Graceful fallback if escalation state not initialized

            # RCA-289: Build stuck tool details section (if available)
            tool_error_section = ""
            if failed_tool or last_tool_error:
                from python.helpers.output_truncation import truncate_output_middle_out
                _sig_display = truncate_output_middle_out(failed_tool_sig, max_chars=200, head_ratio=0.4) if failed_tool_sig else 'N/A'
                _err_display = truncate_output_middle_out(last_tool_error, max_chars=300, head_ratio=0.3) if last_tool_error else 'N/A'
                tool_error_section = f"""\n## Stuck Tool Details\n- **Tool**: {failed_tool or 'unknown'}\n- **Signature**: {_sig_display}\n- **Last Error**: {_err_display}\n"""

            # ITR-33: Build Intervention Brief — the supervisor equivalent of a
            # delegation brief. Gives the LLM actual build output, pattern match
            # history, and project context so it can diagnose intelligently instead
            # of producing generic "stop and read files" advice.
            intervention_brief = ""
            build_snippet = escape_context.get('build_output_snippet', '')
            exhausted_pats = escape_context.get('exhausted_patterns', [])
            fix_counts = escape_context.get('fix_attempt_counts', {})
            matched_pids = escape_context.get('matched_pattern_ids', [])
            project_dir = escape_context.get('project_dir', '')

            if build_snippet or exhausted_pats:
                brief_parts = ["\n## 📋 Intervention Brief (Build Diagnosis Context)"]

                if project_dir:
                    brief_parts.append(f"**Project**: `{project_dir}`")

                if matched_pids:
                    brief_parts.append(f"**Matched Patterns**: {', '.join(f'`{p}`' for p in matched_pids)}")

                if fix_counts:
                    fix_lines = [f"  - `{pid}`: {count} attempts" for pid, count in fix_counts.items()]
                    brief_parts.append(f"**Fix Attempts (all exhausted)**:\n" + "\n".join(fix_lines))

                if build_snippet:
                    # Truncate for prompt budget
                    snippet = build_snippet[:600]
                    brief_parts.append(
                        f"**Actual Build Error Output** (use this to diagnose):\n"
                        f"```\n{snippet}\n```"
                    )

                brief_parts.append(
                    "**Your job**: Read the ACTUAL build error above. Do NOT repeat "
                    "the same generic fix the agent already tried 4+ times. Diagnose "
                    "the SPECIFIC issue (e.g., is this an internal Next.js prerendering "
                    "error vs user code? Is it a dependency conflict? A missing config?) "
                    "and prescribe a TARGETED fix the agent hasn't tried yet."
                )
                intervention_brief = "\n".join(brief_parts) + "\n"

            # Get recent agent history for context
            recent_history = []
            if hasattr(agent, 'history'):
                try:
                    history_output = agent.history.output()
                    # Take last 6 messages for context
                    recent_history = history_output[-6:] if history_output else []
                except Exception:
                    pass

            # Format recent history for the prompt
            # RCA-289: Use middle-out truncation to preserve head (context) + tail (error root cause)
            from python.helpers.output_truncation import truncate_output_middle_out
            history_text = ""
            for msg in recent_history:
                role = msg.get("role", "unknown")
                content = truncate_output_middle_out(str(msg.get("content", "")), max_chars=500, head_ratio=0.3)
                history_text += f"  [{role}]: {content}\n"

            is_semantic = escape_type == "same_message_semantic"

            if is_semantic:
                # RCA-263: Semantic repeats may be legitimate recovery work.
                # The supervisor gets a 3-way decision: CONTINUE, REDIRECT, or STOP.
                prompt = f"""{escalation_directive}An agent's loop detector flagged a SEMANTIC repeat — the agent is calling the 
same tool with the same arguments but different reasoning/thoughts each time.

## Agent: {agent_name}
## Agent Profile Context:
{profile_context}
## Loop Type: {escape_type} (same tool signature, different thoughts)
## Repeat Count: {repeat_count}
## Detector Reason: {reason}
{tool_error_section}
{intervention_brief}
## Recent Agent History:
{history_text}

## Your Decision:
Analyze the agent's recent history and decide:

1. **CONTINUE_AS_IS** — The agent is doing LEGITIMATE work (e.g., restoring files after 
   corruption, re-running a build command after fixing errors, writing configuration files 
   as part of a multi-step setup). The tool calls look the same because the TASK requires 
   repeated similar actions. Reply with exactly: CONTINUE_AS_IS

2. **REDIRECT** — The agent IS stuck in a loop and needs corrective guidance. Generate a 
   SHORT, SPECIFIC corrective instruction (2-4 sentences) starting with "SUPERVISOR REDIRECT: "
   ONLY suggest tools available to this agent's profile ({agent_profile or 'default'}):
   {valid_tools_names}.
   Do NOT suggest non-existent tools like `edit_file`, `sed`, `awk`, or `cat -n`.

Choose CONTINUE_AS_IS when:
- The agent is writing different files or fixing build errors step by step
- The repeated tool is write_to_file, code_execution_tool, or call_subordinate with evolving context
- The thoughts show the agent is reasoning about NEW problems (even if using the same tool)

Choose REDIRECT when:
- The agent is truly stuck repeating the exact same fix without progress
- The agent's thoughts show no evolution or new reasoning
- Previous attempts have failed and the agent hasn't adapted its approach
"""
            else:
                # Exact-match repeats — truly identical messages, always stuck
                prompt = f"""{escalation_directive}An agent is stuck in a repetition loop and needs a corrective redirect.

## Agent: {agent_name}
## Agent Profile Context:
{profile_context}
## Loop Type: {escape_type}
## Repeat Count: {repeat_count}
## Hard-Stop Reason: {reason}
{tool_error_section}
{intervention_brief}
## Recent Agent History:
{history_text}

## Valid Tools for this agent (profile: {agent_profile or 'default'}):
{valid_tools_text}

⚠️ CRITICAL: This agent's profile is **{agent_profile or 'default'}**. Read the profile context
above carefully. Do NOT suggest tools or actions that are outside this agent's capabilities.
Suggesting unavailable tools (like write tools to a read-only agent) causes deadlocks.

## Your Task:
Generate a SHORT, SPECIFIC corrective instruction (2-4 sentences) that tells the agent:
1. WHAT it was doing wrong (repeating the same action)
2. A SPECIFIC alternative approach using ONLY tools from the valid list above
3. Consider the agent's ROLE — if it cannot write files, tell it to report findings via `response`

Be direct and prescriptive. Do NOT be vague. Start with "SUPERVISOR REDIRECT: "
"""

            # Use utility LLM (fast path — no tool calls, just text generation)
            if hasattr(agent, 'call_utility_model'):
                result = await agent.call_utility_model(
                    system="You are a supervisor deciding whether a flagged agent is genuinely stuck or doing legitimate work. Be analytical and precise.",
                    message=prompt,
                )
                if result and isinstance(result, str) and len(result.strip()) > 10:
                    cleaned = result.strip()
                    # RCA-263: Check for CONTINUE_AS_IS decision
                    if is_semantic and "CONTINUE_AS_IS" in cleaned:
                        logger.info(
                            f"[SUPERVISOR] request_redirect for {agent_name}: "
                            f"Decision = CONTINUE_AS_IS (semantic repeat is legitimate work)."
                        )
                        return "CONTINUE_AS_IS"
                    # RCA-289: Wire into escalation pipeline after successful redirect.
                    # This increments the escalation level so subsequent redirects get
                    # progressively stronger directives (L1→L2→...→L5).
                    try:
                        self.increment_escalation(agent_name)
                    except Exception as e:
                        logger.debug(f"Could not increment escalation for {agent_name}: {e}")
                    logger.info(
                        f"[SUPERVISOR] request_redirect for {agent_name}: "
                        f"Generated {len(cleaned)} char redirect. "
                        f"Escalation level: {self._escalation_levels.get(agent_name, 0)}"
                    )
                    return cleaned

            logger.warning(
                f"[SUPERVISOR] request_redirect for {agent_name}: "
                f"Utility model returned empty result."
            )
            return None

        except Exception as e:
            logger.warning(
                f"[SUPERVISOR] request_redirect failed for "
                f"{getattr(agent, 'agent_name', 'unknown')}: {e}"
            )
            return None
