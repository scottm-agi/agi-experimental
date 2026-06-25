from __future__ import annotations
"""
Request Supervisor Tool

This tool allows agents to explicitly request supervisor guidance when they
feel stuck, uncertain, or need help with a decision.

Usage:
    The agent can call this tool when:
    - They're unsure how to proceed
    - They've tried multiple approaches without success
    - They need guidance on a complex decision
    - They want a second opinion on their approach
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from python.helpers.tool import Tool, Response
from python.helpers.output_truncation import truncate_output_middle_out

if TYPE_CHECKING:
    from python.agent import Agent


class RequestSupervisor(Tool):
    """
    Tool for agents to explicitly request supervisor help.
    
    This enables agents to proactively ask for guidance rather than
    waiting for the supervisor to detect they're stuck.
    """
    
    async def execute(self, **kwargs) -> Response:
        """
        Request supervisor intervention.
        
        Args:
            reason: Why the agent is requesting help
            context: Additional context about the situation
            question: Specific question for the supervisor (optional)
            approaches_tried: List of approaches already attempted (optional)
        
        Returns:
            Response indicating the request was sent
        """
        reason = kwargs.get("reason", "Agent requested supervisor guidance")
        context = kwargs.get("context", "")
        question = kwargs.get("question", "")
        approaches_tried = kwargs.get("approaches_tried", [])
        
        # Build the help request
        help_request = self._build_help_request(reason, context, question, approaches_tried)
        
        # Get agent info
        agent_id = str(getattr(self.agent, 'number', 0))
        context_id = self.agent.context.id if self.agent.context else "unknown"
        
        # Get full chat context for supervisor
        full_context = self._get_full_context()
        
        try:
            from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
            from python.helpers.supervisor_agent import get_llm_supervisor
            
            event_bus = get_event_bus()
            
            # Create intervention request signal
            signal = AgentSignal(
                signal_type=SignalType.INTERVENTION_NEEDED,
                agent_id=agent_id,
                context_id=context_id,
                timestamp=datetime.now(timezone.utc),
                severity="medium",  # Agent-requested, not critical
                details={
                    "reason": reason,
                    "context": context,
                    "question": question,
                    "approaches_tried": approaches_tried,
                    "help_request": help_request,
                    "full_chat_context": full_context,
                    "source": "agent_request",
                    "trigger": "explicit_request",
                },
                error_message=None,
            )
            
            # Publish to supervisor
            await event_bus.publish(signal)
            
            # Log the request
            self.agent.context.log.log(
                type="info",
                heading="🙋 Supervisor Help Requested",
                content=f"Reason: {reason}\n\nThe supervisor will analyze the situation and provide guidance."
            )
            
            # Check if supervisor is available
            supervisor = get_llm_supervisor()
            if supervisor and supervisor._running:
                return Response(
                    message=f"""Supervisor help requested successfully.

**Reason:** {reason}
{f"**Question:** {question}" if question else ""}

The supervisor is analyzing the chat context and will provide guidance shortly.

While waiting, you can:
1. Review your recent approaches
2. Consider alternative methods
3. Check for any errors you may have missed

The supervisor will inject guidance when ready.""",
                    break_loop=False,  # Don't break the loop, let supervisor inject guidance
                )
            else:
                # Supervisor not available, provide self-help guidance
                return Response(
                    message=self._get_self_help_guidance(reason, question, approaches_tried),
                    break_loop=False,
                )
                
        except Exception as e:
            # If request fails, provide fallback guidance
            self.agent.context.log.log(
                type="warning",
                heading="Supervisor Request Failed",
                content=f"Could not reach supervisor: {e}"
            )
            
            return Response(
                message=self._get_self_help_guidance(reason, question, approaches_tried),
                break_loop=False,
            )
    
    def _build_help_request(
        self,
        reason: str,
        context: str,
        question: str,
        approaches_tried: list
    ) -> str:
        """Build a structured help request for the supervisor."""
        parts = []
        
        parts.append("# Agent Help Request\n")
        parts.append(f"## Reason for Request\n{reason}\n")
        
        if context:
            parts.append(f"## Additional Context\n{context}\n")
        
        if question:
            parts.append(f"## Specific Question\n{question}\n")
        
        if approaches_tried:
            parts.append("## Approaches Already Tried")
            for i, approach in enumerate(approaches_tried, 1):
                parts.append(f"{i}. {approach}")
            parts.append("")
        
        parts.append("""
## Analysis Request

The agent has explicitly requested supervisor help. Please:

1. **Understand the Situation**: Review the full chat context
2. **Identify the Blocker**: What's preventing the agent from proceeding?
3. **Provide Guidance**: Give specific, actionable advice
4. **Consider Alternatives**: Suggest different approaches if needed

Use your tools:
- `provide_guidance` - For general direction
- `inject_hint` - For specific technical hints
- `redirect_approach` - If a different approach is needed
- `simplify_task` - If the task should be broken down
- Use Perplexity MCP search to research solutions if needed
""")
        
        return "\n".join(parts)
    
    def _get_full_context(self) -> str:
        """Get full chat context for supervisor analysis."""
        context_parts = []
        
        # Get agent state
        agent_id = str(getattr(self.agent, 'number', 0))
        context_parts.append(f"# Agent Context\n")
        context_parts.append(f"**Agent ID:** {agent_id}")
        
        # Get iteration info
        if hasattr(self.agent, 'loop_data') and self.agent.loop_data:
            iteration = getattr(self.agent.loop_data, 'iteration', 0)
            context_parts.append(f"**Current Iteration:** {iteration}")
        
        # Get context window usage
        if hasattr(self.agent, 'get_data'):
            ctx_window = self.agent.get_data("ctx_window") or {}
            tokens = ctx_window.get("tokens", 0)
            max_tokens = 128000
            if hasattr(self.agent, 'config') and hasattr(self.agent.config, 'chat_model'):
                max_tokens = getattr(self.agent.config.chat_model, 'ctx_length', 128000) or 128000
            usage_percent = (tokens / max_tokens * 100) if max_tokens else 0
            context_parts.append(f"**Context Usage:** {usage_percent:.1f}%")
        
        # Get full history
        if hasattr(self.agent, 'history') and self.agent.history:
            try:
                history = self.agent.history.output() if hasattr(self.agent.history, 'output') else []
                
                context_parts.append(f"\n## Chat History ({len(history)} messages)\n")
                
                for i, msg in enumerate(history):
                    role = msg.get("role", "unknown")
                    content = str(msg.get("content", ""))
                    
                    # Truncate very long messages
                    if len(content) > 1500:
                        content = truncate_output_middle_out(content, max_chars=1500, head_ratio=0.3)
                    
                    context_parts.append(f"### Message {i+1} [{role.upper()}]")
                    context_parts.append(content)
                    context_parts.append("")
                    
            except Exception as e:
                context_parts.append(f"Error getting history: {e}")
        
        return "\n".join(context_parts)
    
    def _get_self_help_guidance(
        self,
        reason: str,
        question: str,
        approaches_tried: list
    ) -> str:
        """Provide self-help guidance when supervisor is not available."""
        guidance = f"""The supervisor is currently unavailable, but here's some guidance based on your request:

**Your Reason:** {reason}
{f"**Your Question:** {question}" if question else ""}

## Self-Help Suggestions

1. **Break Down the Problem**
   - What's the smallest step you can take?
   - Can you isolate the specific issue?

2. **Review What You've Tried**
   - Why didn't previous approaches work?
   - What error messages or feedback did you get?

3. **Consider Alternatives**
   - Is there a different tool that could help?
   - Can you simplify the approach?

4. **Check Your Assumptions**
   - Are file paths correct?
   - Are you using the right parameters?
   - Is the environment set up correctly?

5. **Research the Issue**
   - Look up any error messages
   - Check documentation for the tools you're using

6. **Ask the User**
   - If you're truly stuck, ask the user for clarification
   - They may have context you're missing

"""
        
        if approaches_tried:
            guidance += "\n## Your Previous Approaches\n"
            for i, approach in enumerate(approaches_tried, 1):
                guidance += f"{i}. {approach}\n"
            guidance += "\nConsider why each approach didn't work and what you could try differently.\n"
        
        return guidance
