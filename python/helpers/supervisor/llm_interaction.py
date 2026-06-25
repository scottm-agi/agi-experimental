"""
LLM interaction module for supervisor.

Contains LLM calls, context building, system prompt construction, and tool call parsing.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .base import logger


def get_e2e_delegation_status(agent_data: Dict) -> str:
    """Check e2e delegation status from agent data.

    RCA-401 F-1: Extracted from _build_llm_context to fix NameError
    (was using undefined 'agent_data' local, and importing nonexistent
    'was_profile_delegated' function). Now uses agent_data dict directly.

    Args:
        agent_data: The agent's data dictionary (agent.data).

    Returns:
        Empty string if too early (<= 5 delegations), warning if e2e NOT
        delegated, or checkmark if e2e IS delegated.
    """
    try:
        from python.helpers.orchestrator_gate_common import get_total_delegation_count
        total_delegations = get_total_delegation_count(agent_data)

        # Check if e2e profile was delegated
        delegation_profiles = agent_data.get("_delegation_profiles", set())
        if isinstance(delegation_profiles, (list, tuple)):
            delegation_profiles = set(delegation_profiles)
        e2e_delegated = "e2e" in delegation_profiles
        verification_delegated = agent_data.get("_verification_delegated", False)

        if total_delegations > 5 and not e2e_delegated and not verification_delegated:
            return (
                "\n\n## \u26a0\ufe0f E2E Delegation Status\n"
                f"\u274c E2E verification NOT delegated after {total_delegations} delegations.\n"
                "The agent has done substantial work but has not delegated to the `e2e` profile "
                "for independent verification (build, test suites, browser UAT, API testing).\n"
                "**Recommend nudging the agent to delegate E2E verification before completing.**\n"
            )
        elif e2e_delegated or verification_delegated:
            return (
                "\n\n## E2E Delegation Status\n"
                "\u2705 E2E verification has been delegated.\n"
            )
    except Exception as e:
        logger.debug(f"E2E delegation status check failed: {e}")
    return ""

if TYPE_CHECKING:
    from python.agent import Agent
    from python.helpers.event_bus import AgentSignal


class LLMInteractionMixin:
    """
    Mixin class providing LLM interaction functionality for SupervisorAgent.
    
    This mixin handles:
    - LLM calls with fallback support
    - Context building for LLM
    - System prompt construction with lessons learned
    - Tool call parsing from LLM responses
    """
    
    # =========================================================================
    # LLM Interaction
    # =========================================================================
    
    async def _handle_agent_signals(
        self,
        agent_id: str,
        signals: List["AgentSignal"],
    ) -> None:
        """Use LLM to decide and execute intervention."""
        print(f"\n[SUPERVISOR] 🔍 Handling signals for agent {agent_id}", file=sys.stderr)
        
        # ROBUST LOOKUP:
        # 1. Try exact match (could be composite or base)
        agent = self._agent_refs.get(agent_id)
        
        # 2. FIX #742 + FIX #902: If not found, scan composite IDs for matching context
        #    Do NOT fall back to bare agent_name — that causes cross-chat bleeding
        #    MUST verify context_id matches exactly — never supervise wrong chat
        if not agent and "@" in agent_id:
            base_id = agent_id.split("@")[0]
            context_id = agent_id.split("@", 1)[1]
            print(f"[SUPERVISOR] ℹ️ Composite ID {agent_id} not found, scanning for context-verified match", file=sys.stderr)
            # Search through registered agents for same base name AND matching context
            for ref_id, ref_agent in list(self._agent_refs.items()):
                if "@" not in ref_id:
                    continue
                ref_base, ref_context = ref_id.split("@", 1)
                # Must match BOTH base name AND context_id to prevent cross-chat bleeding
                if ref_base == base_id and ref_context == context_id:
                    agent = ref_agent
                    print(f"[SUPERVISOR] ✅ Found context-verified agent: {ref_id}", file=sys.stderr)
                    break
            if not agent:
                print(f"[SUPERVISOR] ⏭️ No agent found with matching context '{context_id}' — skipping supervision", file=sys.stderr)
                return
        
        if not agent:
            print(f"[SUPERVISOR] ℹ️ Agent {agent_id} not found in local registry - proceeding with remote analysis", file=sys.stderr)
            # continue anyway, we'll try to build context from signals
        
        # Gap 6: Generate signal fingerprint for issue-specific cooldown
        signal_fingerprint = await self._generate_signal_fingerprint(signals) if signals else None
        
        # Check intervention cooldown with fingerprint
        if not self._can_intervene(agent_id, signal_fingerprint):
            print(f"[SUPERVISOR] ⏳ Intervention cooldown active for {agent_id} (fingerprint: {signal_fingerprint})", file=sys.stderr)
            logger.debug(f"Intervention cooldown active for {agent_id}")
            return
        
        # Build context for LLM - include full chat from signals
        context = self._build_llm_context(agent, signals)
        
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[SUPERVISOR] 🤖 CALLING SUPERVISOR LLM", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"  Agent: {agent_id}", file=sys.stderr)
        print(f"  Signals: {len(signals)}", file=sys.stderr)
        print(f"  Context length: {len(context)} chars", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        
        # Call LLM with tools
        try:
            # ── N-ATTEMPT REDIRECT: Auto deep-dive + redirect for repeated task failures ──
            # When the same task fails N times, skip the generic LLM decision and
            # directly trigger deep-dive RCA + redirect with the failure context.
            from python.helpers.event_bus import SignalType
            repeated_failure_signals = [
                s for s in signals
                if s.signal_type == SignalType.REPEATED_TASK_FAILURE
            ]
            if repeated_failure_signals and agent:
                for rf_signal in repeated_failure_signals:
                    await self._handle_repeated_task_failure(agent, rf_signal)
                # Still continue with generic LLM call for any remaining signals
                remaining = [s for s in signals if s.signal_type != SignalType.REPEATED_TASK_FAILURE]
                if not remaining:
                    return  # All signals were repeated-task-failure, handled directly

            # Gap 6: Identify primary reason for intervention
            reason = None
            for s in signals:
                if s.signal_type.value == "response_loop":
                    reason = "hallucination"
                    break
            
            response, tool_calls = await self._call_supervisor_llm(context, agent_id, reason=reason)
            
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"[SUPERVISOR] 📝 LLM RESPONSE RECEIVED", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            print(f"  Response length: {len(response)} chars", file=sys.stderr)
            print(f"  Tool calls: {len(tool_calls)}", file=sys.stderr)
            if tool_calls:
                for tc in tool_calls:
                    print(f"    - {tc.get('name')}: {json.dumps(tc.get('arguments', {}))[:100]}", file=sys.stderr)
            print(f"\n  Response preview:\n{response[:500]}...", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            
            # Execute any tool calls
            if tool_calls:
                await self._execute_tool_calls(agent, tool_calls)
            else:
                print(f"[SUPERVISOR] ℹ️ No tool calls in response - supervisor chose not to intervene", file=sys.stderr)
            
        except Exception as e:
            print(f"[SUPERVISOR] ❌ Error in LLM supervisor call: {e}", file=sys.stderr)
            logger.error(f"Error in LLM supervisor call: {e}")
            import traceback
            traceback.print_exc()
    
    def _build_llm_context(
        self,
        agent: Optional["Agent"],
        signals: List["AgentSignal"],
    ) -> str:
        """Build context message for LLM with smart budget management."""
        # Ralph Loop: Check for completion promise in task context
        promise_context = ""
        try:
            if agent and hasattr(agent, 'context') and agent.context:
                from python.helpers.task_scheduler import TaskScheduler
                scheduler = TaskScheduler.get()
                task = scheduler.get_task_by_uuid(agent.context.id)
                if task and task.completion_promise:
                    promise_context = f"\n\n## Completion Promise\nThis agent MUST output the exact string `{task.completion_promise}` to be considered finished. If it hasn't, it is NOT done."
        except Exception:
            pass

        # Track character usage for budget management
        chars_used = 0
        
        # Get agent state
        summary = self._get_agent_summary(agent)
        chars_used += len(summary)
        
        # Format signals
        signal_text = ""
        if signals:
            signal_text = "\n\n## Recent Signals\n"
            for s in signals[-5:]:  # Last 5 signals
                signal_text += f"- **{s.signal_type.value}** (severity: {s.severity})\n"
                signal_text += f"  Details: {json.dumps(s.details)}\n"
                if s.error_message:
                    signal_text += f"  Error: {s.error_message}\n"
        
        # Add Argument Delta Summary (Issue #181)
        delta_summary = ""
        if len(signals) >= 2:
            all_details = [s.details for s in signals[-5:]]
            recent_tools = [d.get("tool_name") for d in all_details if "tool_name" in d]
            if len(set(recent_tools)) == 1 and recent_tools:
                # Same tool being used repeatedly - check arguments
                tool_name = recent_tools[0]
                delta_summary = f"\n\n## Argument Delta for {tool_name}\n"
                
                # Check for progress indicators (e.g., offsets, file paths)
                unique_arg_keys = set()
                for d in all_details:
                    args = d.get("arguments", {})
                    if isinstance(args, dict):
                        unique_arg_keys.update(args.keys())
                
                for key in unique_arg_keys:
                    values = []
                    for d in all_details:
                        val = d.get("arguments", {}).get(key)
                        if val is not None:
                            values.append(str(val))
                    
                    if len(set(values)) > 1:
                        delta_summary += f"- Key `{key}` is changing: { ' -> '.join(values[-3:]) }\n"
                
                if not delta_summary.strip().endswith("\n"):
                    delta_summary += "- Arguments appear STATIC or only ephemeral keys (IDs/times) are changing.\n"
        
        chars_used += len(signal_text) + len(delta_summary)
        
        # Assess remaining budget for memory banks
        budget = self._assess_content_budget(chars_used)
        
        # Load memory banks with smart budget management
        memory_context = ""
        if agent and (budget.get("global_memory", 0) > 0 or budget.get("project_memory", 0) > 0):
            print(f"[SUPERVISOR] 📊 Building context with budget: {chars_used} chars used, {self.config.max_context_chars - chars_used} remaining", file=sys.stderr)
            memory_content = self._load_memory_banks_smart(agent, budget)
            if memory_content:
                memory_context = f"\n\n# Memory Context\n{memory_content}"
        
        # Include chat context from signals if agent is missing or session is deep
        signal_chat = ""
        for s in reversed(signals):
            if "full_chat_context" in s.details:
                signal_chat = f"\n\n# Chat Preview from Signals\n{s.details['full_chat_context']}\n"
                break
        
        # NEW: Add goal state context (Gap 2)
        goal_context = ""
        try:
            from python.helpers.goal_state_manager import GoalStateManager
            gsm = GoalStateManager.get_instance()
            
            # Try to get goal from signals' context_id
            context_id = signals[0].context_id if signals else None
            if not context_id and agent and hasattr(agent, 'context'):
                context_id = agent.context.id if agent.context else None
            
            if context_id:
                goal = gsm.get_goal(context_id)
                if goal:
                    goal_context = f"""
## Original Goal & Progress
{goal.get_progress_summary()}

**Interventions So Far**: {goal.intervention_count}
**Original Prompt**: {goal.original_prompt[:300]}...
"""
        except Exception as e:
            logger.debug(f"Could not load goal state: {e}")
        
        agent_id = getattr(agent, 'agent_name', "Remote/Unknown") if agent else "Remote Agent"
        
        # Escalation ramp (#366): inject escalation directive if agent has failed interventions
        escalation_directive = ""
        try:
            escalation_directive = self.get_escalation_directive(agent_id)
        except Exception as e:
            logger.debug(f"Could not get escalation directive: {e}")
        
        # Build tool frequency summary from recent signals
        tool_freq_section = ""
        try:
            tool_counts = {}
            for s in signals:
                tool_name = s.details.get("tool_name")
                if tool_name:
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            
            if tool_counts:
                # Import thresholds from ToolFrequencyDetector
                try:
                    from python.helpers.detectors.tool_frequency import ToolFrequencyDetector
                    thresholds = ToolFrequencyDetector.TOOL_THRESHOLDS
                    default_threshold = ToolFrequencyDetector.DEFAULT_THRESHOLD
                except ImportError:
                    thresholds = {"maintain_memory_bank": 4, "scheduler": 3}
                    default_threshold = 5
                
                tool_freq_section = "\n\n## Tool Call Frequency (from signals)\n"
                for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                    threshold = thresholds.get(tool, default_threshold)
                    marker = " ⚠️ EXCESSIVE" if count >= threshold else ""
                    tool_freq_section += f"- {tool}: {count} calls{marker}\n"
                
                if any(c >= thresholds.get(t, default_threshold) for t, c in tool_counts.items()):
                    tool_freq_section += "\n> **WARNING**: One or more tools are being called excessively. Consider redirecting the agent away from repetitive tool usage.\n"
        except Exception:
            pass
        
        # Inject dev server port-check ground truth (prevents LLM hallucination)
        dev_server_status = ""
        try:
            for port in range(5100, 5200):
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/", method="HEAD")
                    with urllib.request.urlopen(req, timeout=1) as resp:
                        dev_server_status = (
                            f"\n\n## Dev Server Status (VERIFIED)\n"
                            f"✅ Dev server is RUNNING on port {port} (HTTP {resp.status})\n"
                        )
                        break
                except Exception:
                    continue
            if not dev_server_status:
                dev_server_status = (
                    "\n\n## Dev Server Status (VERIFIED)\n"
                    "❌ No dev server detected on ports 5100-5199\n"
                )
        except Exception:
            dev_server_status = (
                "\n\n## Dev Server Status (VERIFIED)\n"
                "⚠️ Port check unavailable\n"
            )

        # ── E2E Delegation Status (Layer 4 of 5-Layer E2E Enforcement) ──
        # RCA-401 F-1: Extracted to get_e2e_delegation_status() to fix
        # NameError (was using undefined 'agent_data' instead of agent.data)
        # and importing nonexistent 'was_profile_delegated' function.
        e2e_delegation_status = ""
        if agent and hasattr(agent, 'data'):
            e2e_delegation_status = get_e2e_delegation_status(agent.data)

        return f"""# Agent Status Report

## Agent: {agent_id}

{summary}
{signal_text}
{delta_summary}
{tool_freq_section}
{dev_server_status}
{e2e_delegation_status}
{promise_context}
{goal_context}
{memory_context}
{signal_chat}
{escalation_directive}

## Your Task
Based on this information, decide if and how to help this agent.
Use the available tools to investigate further or intervene.

If no escalation directive is present above, use the gentlest effective approach.

## CRITICAL: Hallucination Loop Detected
If the reason is "hallucination", the agent is trapped in a repetition cycle (e.g., repeating the same phrase over and over). 
1. Use `redirect_approach` or `provide_guidance` to FORCE the agent to change its thought pattern.
2. Tell the agent EXPLICITLY to "forget the previous repetitive phrase and restart the current step with a fresh perspective."
3. Identify exactly which phrase is repeating and tell the agent to stop generating it.
"""
    
    async def _call_supervisor_llm(self, context: str, agent_id: str = "unknown", reason: str = None) -> Tuple[str, List[Dict[str, Any]]]:
        """Call the supervisor LLM with tools, with fallback support."""
        import python.models as models
        from python.helpers.settings import get_settings
        from python.helpers.supervisor_logging import log_intervention
        
        system_prompt = self._build_system_prompt()
        
        # Add hallucination-specific system context if needed
        if reason == "hallucination":
            system_prompt += "\n\n# CRITICAL: HALLUCINATION ALERT\nThe agent is currently trapped in a repetitive thought loop. You MUST intervene aggressively to break this cycle. Do not just provide encouragement; provide a STRATEGIC REDIRECT that ignores the poisoned context."
            
        full_context = system_prompt + "\n\n" + context
        
        # Determine candidates to try (up to length of candidates or a fixed number)
        max_attempts = len(self.model_candidates) if self.model_candidates else 1
        last_error = None

        for i, candidate in enumerate(self.model_candidates):
            provider, name = candidate
            try:
                # Get model
                model_config = models.ModelConfig(
                    type=models.ModelType.CHAT,
                    provider=provider,
                    name=name,
                    max_tokens=self.config.model_max_tokens,
                    thinking=self.config.model_thinking,
                    thinking_tokens=self.config.model_thinking_tokens,
                    privacy=get_settings().get("privacy_mode", False)
                )
                model = models.get_chat_model(
                    provider,
                    name,
                    model_config=model_config,
                    **self.config.model_kwargs,
                )
                
                # Call model
                is_last = (i == max_attempts - 1)
                response, reasoning, _model, _provider = await model.unified_call(
                    system_message=system_prompt,
                    user_message=context,
                    agix_silent_failover=not is_last
                )
                
                # Parse tool calls
                tool_calls = self._parse_tool_calls(response)
                
                # Issue #391: Dedicated Supervisor Log
                # We log even if no tool calls, to record the "tuning" rationale
                log_data = {
                    "supervisor_type": "llm",
                    "agent_id": agent_id,
                    "model": f"{provider}/{name}",
                    "reasoning": reasoning or response,
                    "tool_calls": tool_calls,
                    "context_length": len(full_context)
                }
                log_intervention(log_data)

                return response, tool_calls

            except Exception as e:
                last_error = e
                logger.warning(f"Supervisor call failed with {provider}/{name} (attempt {i+1}/{max_attempts}): {str(e)}")
                
                # Continue loop to next candidate
                continue

        # If we get here, all attempts failed
        logger.error(f"All supervisor LLM attempts failed. Last error: {str(last_error)}")
        raise last_error if last_error else RuntimeError("Supervisor LLM call failed")
    
    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """Parse tool calls from LLM response."""
        tool_calls = []
        
        # Look for tool call patterns in response
        # Format: <tool_call name="tool_name">{"arg": "value"}</tool_call>
        pattern = r'<tool_call\s+name="([^"]+)">(.*?)</tool_call>'
        matches = re.findall(pattern, response, re.DOTALL)
        
        for name, args_str in matches:
            try:
                args = json.loads(args_str.strip())
                tool_calls.append({"name": name, "arguments": args})
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse tool arguments: {args_str}")
        
        # Also look for JSON-style tool calls
        # Format: {"tool": "name", "arguments": {...}}
        json_pattern = r'\{[^{}]*"tool"\s*:\s*"([^"]+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}'
        json_matches = re.findall(json_pattern, response, re.DOTALL)
        
        for name, args_str in json_matches:
            try:
                args = json.loads(args_str)
                tool_calls.append({"name": name, "arguments": args})
            except json.JSONDecodeError:
                pass
        
        return tool_calls
    
    def _build_system_prompt(self) -> str:
        """Build system prompt with lessons learned."""
        # Load lessons
        lessons = self._load_lessons()
        
        tools_description = self._format_tools_for_prompt()
        
        return f"""# Supervisor Agent

You are the Supervisor Agent for AGIX. Your ONLY mission is to help stuck agents get unstuck.

## Your Role
- Monitor agents for signs of trouble
- Provide mentor-style guidance when needed
- Learn from past interventions to improve

## Intervention Philosophy
- Be helpful, not intrusive
- Guide like an experienced mentor would
- Prefer gentle nudges over forceful redirects
- Only escalate when truly necessary

## When to Intervene
- Agent is repeating the same response (response loop)
- Agent has multiple consecutive tool failures with SAME error
- Agent's context window is filling up (>85%)
- Agent appears stuck with NO change in tool arguments or progress markers
- Agent has a "Completion Promise" (e.g., "DONE", "SUCCESS") but finished without outputting it
- Agent delegates to the same test/E2E profile repeatedly without delegating to a code agent for fixes in between (delegation retest loop — the test keeps failing because the code hasn't been fixed)

## When NOT to Intervene
- Agent is making steady progress (even if repetitive)
- Agent is paging through results (e.g., offset/page increasing)
- Agent is processing a list of items (e.g., file paths changing)
- Agent is handling errors appropriately (retrying with a DIFFERENT approach)
- User explicitly requested a specific tool/protocol (e.g., "use MCP", "use GitHub MCP")
- MCP tools are successfully executing (even if results are partial)
- Agent is using available MCP tools as requested by user

## CRITICAL: Respect User's Explicit Tool Choices
If the user has explicitly requested a specific approach (MCP, GitHub, Forgejo, etc.):
1. DO NOT redirect to alternative protocols (e.g., A2A, call_subordinate)
2. Let the agent continue using the requested tools
3. Only intervene if the tool is completely unavailable or the agent is truly stuck
4. A few connection errors do NOT justify switching protocols
5. If a user says "use MCP" or "just use your tools", respect that choice

## CRITICAL: Verify Task Completion Claims (Issue #226 + Launchpad Iter1 Fix 8.2)
Before accepting that an agent has completed its task, you MUST verify with evidence:
- Agent explicitly says "done" / "complete" / "finished" — but this alone is NOT sufficient
- You must also see concrete evidence: test outputs, server logs, curl results, screenshots, or committed code
- An agent that sends the SAME completion message 2+ times is NOT done — it is LOOPING

### RESPONSE_LOOP Detection (Error Class 7):
If the agent sends identical or near-identical "DONE" messages multiple times:
1. This is a FALSE COMPLETION LOOP — the agent is pattern-repeating from poisoned context
2. DO NOT stand down — instead use `redirect_approach` with a direct correction
3. Tell the agent: "Your previous completion claim was not accepted because [specific evidence missing]. You must do [specific next action] before the task can be marked complete."
4. NEVER trust a DONE claim that lacks verifiable evidence (test results, curl output, git push logs)

### Genuine Completion:
Only accept completion when you see BOTH:
1. A completion statement from the agent AND
2. At least one piece of verifiable evidence (passing tests, HTTP 200 responses, deployed URLs, committed code)

## Nuanced Intervention Guidelines (Issue #181)
Prioritize progress over activity. An agent calling `read_file` 50 times for 50 different files is making progress. An agent calling `read_file` 3 times for the SAME file with no content change is looping.
Check the "Argument Delta" section in the report to see if the agent is iterating through a set or just repeating.

## CRITICAL: Goal-Alignment Audit (Expert Observer)
On EVERY check-in, act as an expert human observer. Ask yourself:
1. **What was the ORIGINAL goal?** (Check the initial_prompt / user request)
2. **What has the agent actually accomplished toward that goal?** (Review delegation history, tool calls, results)
3. **Is the agent CONVERGING toward the goal, or CHURNING?** (Same actions repeated = churning. New actions producing artifacts = converging.)

If the agent is churning (activity without forward progress toward the stated goal):
- Use `redirect_approach` with a SPECIFIC corrective instruction
- Reference the original goal explicitly: "Your goal was X. You've been doing Y repeatedly without progress. Instead, do Z."
- Do NOT use gentle nudges for churning — be direct and prescriptive

## Delegation Retest Loop Pattern (Iter73 Fix)
If you see "delegation_retest_loop" in the health report, or you observe the agent delegating to the same test/E2E/browser profile multiple times without a code-fix delegation in between:
1. This is a CLEAR routing error — the agent is retesting without fixing
2. Use `redirect_approach` to tell the agent: "Stop retesting. Delegate to the 'code' agent (profile='code') to fix the specific issues from the last test failure report. Then run E2E again ONLY after the fixes are applied."
3. Do NOT use gentle guidance for this pattern — it requires a direct redirect

## Intervention Styles
1. **Gentle Nudge**: Simple encouragement or question
2. **Redirect**: Suggest a different approach
3. **Simplify**: Break down complex tasks
4. **Hint**: Provide specific technical guidance
5. **Smart Nudge**: Use `nudge_agent` — reads the agent's chat history and composes the best possible corrective advice. Ideal when you can see the agent is stuck but want the intervention to be highly contextual.
6. **Escalate**: Flag for human review (last resort)

## Available Tools
{tools_description}

To use a tool, format your response like this:
<tool_call name="tool_name">{{"argument": "value"}}</tool_call>

## Lessons Learned
{lessons}

## Guidelines
1. Always investigate before intervening
2. Check if intervention is actually needed
3. Use the gentlest effective intervention UNLESS the report shows delegation_retest_loop or goal-alignment drift — then be direct
4. Record lessons from successful interventions
5. Escalate only after other options exhausted

Remember: Your goal is to help agents succeed, not to take over their tasks (forbidden).
"""
    
    def _format_tools_for_prompt(self) -> str:
        """Format tools for inclusion in system prompt."""
        lines = []
        for tool in self.tools:
            name = tool["name"]
            desc = tool["description"]
            params = tool.get("input_schema", {}).get("properties", {})
            
            lines.append(f"### {name}")
            lines.append(f"{desc}")
            if params:
                lines.append("Parameters:")
                for param_name, param_info in params.items():
                    param_desc = param_info.get("description", "")
                    lines.append(f"  - {param_name}: {param_desc}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _load_lessons(self) -> str:
        """Load lessons learned from file."""
        import os
        from python.helpers import files
        
        lessons_path = files.get_abs_path(self.config.lessons_file_path)
        
        if not os.path.exists(lessons_path):
            return "No lessons recorded yet."
        
        try:
            with open(lessons_path, 'r') as f:
                content = f.read()
            
            # Truncate if too long
            lines = content.split('\n')
            if len(lines) > self.config.lessons_chunk_size:
                content = '\n'.join(lines[-self.config.lessons_chunk_size:])
                content = f"[Showing last {self.config.lessons_chunk_size} lines]\n\n" + content
            
            return content
        except Exception as e:
            logger.error(f"Failed to load lessons: {e}")
            return "Failed to load lessons."

    async def _handle_repeated_task_failure(
        self,
        agent: "Agent",
        signal: "AgentSignal",
    ) -> None:
        """Handle REPEATED_TASK_FAILURE signal: deep-dive RCA + redirect.
        
        Automatically triggered when the same task (by MD5 hash) has failed
        N times (default 3). Bypasses generic LLM supervisor and directly:
        1. Runs 5-Why RCA on the agent's history + failure details
        2. Composes a specific new approach
        3. Injects it as a redirect intervention into the orchestrator
        
        This is the core of the supervisor-driven intelligent redirect.
        """
        agent_id = getattr(agent, 'agent_name', str(id(agent)))
        details = signal.details or {}
        task_hash = details.get("task_hash", "unknown")
        failure_count = details.get("failure_count", 0)
        error_summary = details.get("error_summary", [])
        task_preview = details.get("task_preview", "")

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[SUPERVISOR] 🔄 REPEATED TASK FAILURE — AUTO DEEP-DIVE", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"  Agent: {agent_id}", file=sys.stderr)
        print(f"  Task hash: {task_hash}", file=sys.stderr)
        print(f"  Failure count: {failure_count}", file=sys.stderr)
        print(f"  Task preview: {task_preview[:100]}", file=sys.stderr)
        print(f"  Errors ({len(error_summary)}):", file=sys.stderr)
        for err in error_summary[:10]:
            print(f"    - {err[:120]}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # ── Step 1: Deep-dive RCA ──
        rca_report = None
        try:
            from python.helpers.supervisor.deep_dive_rca import (
                deep_dive_analysis,
            )
            reason = (
                f"Task hash {task_hash} has FAILED {failure_count} times. "
                f"Errors: {'; '.join(error_summary[:5])}"
            )
            rca_report = await deep_dive_analysis(agent, reason=reason)
            
            print(f"[SUPERVISOR] 📊 Deep-dive RCA complete:", file=sys.stderr)
            print(f"  Classification: {rca_report.get('classification', 'UNKNOWN')}", file=sys.stderr)
            recovery = rca_report.get("recovery_plan", [])
            if recovery:
                print(f"  Recovery plan ({len(recovery)} steps):", file=sys.stderr)
                for step in recovery[:5]:
                    print(f"    → {str(step)[:100]}", file=sys.stderr)
        except Exception as e:
            logger.error(f"Deep-dive RCA failed for {agent_id}: {e}")
            print(f"[SUPERVISOR] ❌ Deep-dive RCA failed: {e}", file=sys.stderr)

        # ── Step 2: Compose new approach from RCA ──
        classification = rca_report.get("classification", "WRONG_APPROACH") if rca_report else "WRONG_APPROACH"
        recovery_plan = rca_report.get("recovery_plan", []) if rca_report else []
        why_chain = rca_report.get("why_chain", []) if rca_report else []

        # Format error history for the redirect
        error_history = "\n".join(
            f"  - {err[:200]}" for err in error_summary[:10]
        ) if error_summary else "  - (no error details captured)"

        # Format recovery steps
        if recovery_plan:
            recovery_text = "\n".join(
                f"  {i+1}. {str(step)[:200]}" for i, step in enumerate(recovery_plan[:5])
            )
        else:
            recovery_text = (
                "  1. Review the errors above carefully\n"
                "  2. Try a fundamentally different technical approach\n"
                "  3. Consider using different tools, frameworks, or methods\n"
                "  4. If the same approach keeps failing, skip it and move on"
            )

        # Format why-chain
        if why_chain:
            why_text = "\n".join(
                f"  Why-{i+1}: {str(why)[:200]}" for i, why in enumerate(why_chain[:5])
            )
        else:
            why_text = "  (RCA analysis unavailable)"

        redirect_message = (
            f"## 🔄 SUPERVISOR REDIRECT — Task Failed {failure_count} Times\n\n"
            f"**Task hash**: `{task_hash}`\n"
            f"**Classification**: {classification}\n"
            f"**Task**: {task_preview}\n\n"
            f"### Root Cause Analysis:\n{why_text}\n\n"
            f"### Error History (all {failure_count} attempts):\n{error_history}\n\n"
            f"### NEW APPROACH — You MUST try something different:\n{recovery_text}\n\n"
            f"### ⚠️ MANDATORY:\n"
            f"- DO NOT retry the same approach that failed {failure_count} times\n"
            f"- DO NOT delegate the same task with the same wording\n"
            f"- You MUST change your strategy fundamentally\n"
            f"- If the task cannot be completed with available tools, SKIP it and move on\n"
        )

        # ── Step 3: Inject redirect into the orchestrator ──
        try:
            if hasattr(agent, 'intervention'):
                from python.agent import UserMessage
                agent.intervention = UserMessage(
                    message=f"[SUPERVISOR REDIRECT — REPEATED TASK FAILURE]\n\n{redirect_message}",
                    system_message=["[SUPERVISOR REDIRECT — REPEATED TASK FAILURE]"],
                )
                print(
                    f"[SUPERVISOR] ✅ Injected redirect for {agent_id} "
                    f"(task_hash={task_hash}, classification={classification})",
                    file=sys.stderr,
                )
            
            # Record the intervention
            self._intervention_history.setdefault(agent_id, []).append(
                datetime.now(timezone.utc)
            )
            self._stats["repeated_task_redirects"] = (
                self._stats.get("repeated_task_redirects", 0) + 1
            )

            logger.warning(
                f"[SUPERVISOR] REPEATED_TASK_FAILURE redirect injected for {agent_id}: "
                f"task_hash={task_hash}, failures={failure_count}, "
                f"classification={classification}"
            )
        except Exception as e:
            logger.error(f"Failed to inject redirect for {agent_id}: {e}")
            print(f"[SUPERVISOR] ❌ Redirect injection failed: {e}", file=sys.stderr)