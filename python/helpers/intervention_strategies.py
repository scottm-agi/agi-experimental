from __future__ import annotations
"""
Intervention Strategies for Master Agent Supervisor

This module provides intervention strategies that the supervisor uses
to help stuck agents recover. Each strategy is designed to address
a specific type of problem pattern.

Key Strategies:
- ContextCondensationStrategy: Summarizes and condenses context window
- LoopBreakingStrategy: Breaks response loops with new approaches
- ToolAlternativeStrategy: Suggests alternative tools/approaches
- TaskRedirectionStrategy: Redirects agent to different approach
- BackoffStrategy: Implements rate limit backoff

Mode-Aware Features:
- Strategies use mode-specific thresholds from ModeManager
- Different modes have different intervention sensitivity
- Architect/Review modes are more patient (higher thresholds)
- Ask mode has minimal intervention (Q&A focused)
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from python.helpers.loop_prevention import (
    PatternType, InterventionType, InterventionRecord
)
from python.helpers.pattern_detectors import DetectedPattern, AgentState

if TYPE_CHECKING:
    from python.agent import Agent, AgentContext, UserMessage

logger = logging.getLogger(__name__)


@dataclass
class InterventionPlan:
    """A planned intervention to be executed."""
    intervention_type: InterventionType
    target_agent_id: str
    context_id: str
    message: str
    priority: int = 5  # 1 = highest, 10 = lowest
    timeout: int = 300  # seconds
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Execution tracking
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intervention_type": self.intervention_type.value,
            "target_agent_id": self.target_agent_id,
            "context_id": self.context_id,
            "message": self.message[:500] + "..." if len(self.message) > 500 else self.message,
            "priority": self.priority,
            "timeout": self.timeout,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


class InterventionStrategy(ABC):
    """Abstract base class for intervention strategies."""
    
    @property
    @abstractmethod
    def intervention_type(self) -> InterventionType:
        """The type of intervention this strategy produces."""
        pass
    
    @property
    @abstractmethod
    def handles_patterns(self) -> List[PatternType]:
        """List of pattern types this strategy can handle."""
        pass
    
    @abstractmethod
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        """
        Create an intervention plan for the detected pattern.
        
        Args:
            pattern: The detected problematic pattern
            state: Current agent state
            failed_interventions: Previous failed interventions for this pattern
            
        Returns:
            InterventionPlan if intervention is possible, None otherwise
        """
        pass
    
    def _create_plan(
        self,
        state: AgentState,
        message: str,
        priority: int = 5,
        timeout: int = 300,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InterventionPlan:
        """Helper to create an InterventionPlan."""
        return InterventionPlan(
            intervention_type=self.intervention_type,
            target_agent_id=state.agent_id,
            context_id=state.context_id,
            message=message,
            priority=priority,
            timeout=timeout,
            metadata=metadata or {},
        )


class ContextCondensationStrategy(InterventionStrategy):
    """
    Strategy for handling context window overflow.
    
    Summarizes older conversation history to free up context space
    while preserving key information.
    """
    
    def __init__(
        self,
        preserve_recent_messages: int = 5,
        summary_max_tokens: int = 2000,
    ):
        self.preserve_recent_messages = preserve_recent_messages
        self.summary_max_tokens = summary_max_tokens
    
    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.CONTEXT_CONDENSATION
    
    @property
    def handles_patterns(self) -> List[PatternType]:
        return [PatternType.CONTEXT_OVERFLOW]
    
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        usage_ratio = pattern.metadata.get("usage_ratio", 0)
        current_tokens = pattern.metadata.get("current_tokens", 0)
        max_tokens = pattern.metadata.get("max_tokens", 0)
        
        # Determine severity of condensation needed
        if usage_ratio >= 0.95:
            condensation_level = "aggressive"
            priority = 1
        elif usage_ratio >= 0.85:
            condensation_level = "moderate"
            priority = 3
        else:
            condensation_level = "light"
            priority = 5
        
        # Check if we've already tried condensation
        condensation_attempts = len([
            i for i in failed_interventions
            if i.intervention_type == InterventionType.CONTEXT_CONDENSATION
        ])
        
        if condensation_attempts >= 2:
            # Escalate - condensation isn't working
            return None
        
        message = self._build_condensation_message(
            usage_ratio, current_tokens, max_tokens, condensation_level
        )
        
        return self._create_plan(
            state,
            message=message,
            priority=priority,
            metadata={
                "condensation_level": condensation_level,
                "usage_ratio": usage_ratio,
                "preserve_messages": self.preserve_recent_messages,
            },
        )
    
    def _build_condensation_message(
        self,
        usage_ratio: float,
        current_tokens: int,
        max_tokens: int,
        level: str,
    ) -> str:
        """Build the intervention message for context condensation.

        Includes a requirements ledger snapshot (Phase 5 — Prompt Fidelity Pipeline)
        to prevent feature loss during context condensation.
        """
        # ── Requirements Ledger Anchor (Phase 5) ──
        # After condensation, the agent loses old messages and may forget
        # uncompleted requirements. This snapshot survives the condensation
        # and keeps the agent aligned with the original prompt.
        ledger_anchor = ""
        try:
            # Access via the plan method's state, but we can't get agent.data here.
            # Instead, we add a static marker that the condensation handler
            # can look up when it has access to agent.data.
            ledger_anchor = (
                "\n\n## ⚠️ REQUIREMENTS ANCHOR — DO NOT LOSE\n"
                "After condensation, you MUST check the requirements ledger via:\n"
                "```\n"
                "agent.data.get('_requirements_ledger', {})\n"
                "```\n"
                "List ALL pending/in_progress requirements in your summary. "
                "Any requirement not mentioned in your summary will be lost.\n"
            )
        except Exception:
            pass

        if level == "aggressive":
            return f"""⚠️ CRITICAL: Context window at {usage_ratio:.1%} capacity ({current_tokens:,}/{max_tokens:,} tokens).

Your context is nearly full. To continue effectively:

1. **Summarize your progress so far** in a concise paragraph
2. **List only the essential information** needed to complete the current task
3. **Discard detailed logs and intermediate outputs** - keep only conclusions
4. **Focus on the immediate next step** rather than the full plan
5. **CRITICAL: List ALL pending requirements by ID** (REQ-001, REQ-002, etc.)

Please provide a brief summary of:
- What you've accomplished
- What remains to be done (include ALL requirement IDs)
- Key information you need to retain

Then continue with your task using this condensed context.{ledger_anchor}"""

        elif level == "moderate":
            return f"""⚠️ Context window at {usage_ratio:.1%} capacity ({current_tokens:,}/{max_tokens:,} tokens).

Your context is getting full. To maintain effectiveness:

1. **Summarize verbose outputs** - keep conclusions, not raw data
2. **Focus on current task** - earlier exploration can be summarized
3. **Be concise** in your responses
4. **Preserve requirement IDs** (REQ-XXX) in your summary

What's the most important information to retain for completing your current task?{ledger_anchor}"""

        else:
            return f"""ℹ️ Context window at {usage_ratio:.1%} capacity.

Consider being more concise in your responses to preserve context space for the task ahead."""


# LoopBreakingStrategy — originally extracted per Issue #778, inlined back since
# intervention_strategies.py is the sole consumer.


class LoopBreakingStrategy(InterventionStrategy):
    """
    Strategy for breaking response loops.

    Interrupts repetitive patterns and suggests new approaches.

    ENHANCED (2026-01-02): Added actionable commands for scheduled tasks.
    Generic suggestions like "try a different approach" were ineffective.
    Now includes specific commands to clear cache, verify live state, etc.
    """

    def __init__(self):
        self._alternative_approaches = [
            "Break the problem into smaller sub-tasks",
            "Try a completely different approach",
            "Ask clarifying questions about the requirements",
            "Review what information you're missing",
            "Consider if the task is actually completable",
        ]

        # ACTIONABLE commands for stall interventions (added 2026-01-02)
        # These provide concrete steps instead of generic suggestions
        # Keep generic to work across all agent types and use cases
        self._actionable_commands = [
            {
                "command": "VERIFY_CURRENT_STATE",
                "instruction": "Check the actual current state using available tools. Do not rely on previous assumptions or cached information.",
            },
            {
                "command": "RESET_AND_RETRY",
                "instruction": "Clear any local assumptions and start fresh. Re-read requirements and verify your understanding.",
            },
            {
                "command": "ATOMIC_ACTIONS",
                "instruction": "Break your work into smaller steps. Complete ONE action fully before starting the next.",
            },
            {
                "command": "EXPLICIT_PROGRESS",
                "instruction": "State your progress explicitly: what you completed, what remains, and your exact next step.",
            },
            {
                "command": "VERIFY_TOOLS",
                "instruction": "List the tools you need and verify each one is available before proceeding.",
            },
        ]

    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.LOOP_BREAKING

    @property
    def handles_patterns(self) -> List[PatternType]:
        return [PatternType.RESPONSE_LOOP, PatternType.PROGRESS_STALL, PatternType.REPETITIVE_ACTION]

    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        loop_type = pattern.metadata.get("loop_type", "unknown")

        # Check previous attempts
        loop_breaking_attempts = len([
            i for i in failed_interventions
            if i.intervention_type == InterventionType.LOOP_BREAKING
        ])

        if loop_breaking_attempts >= 3:
            # Too many attempts, need to escalate
            return None

        # Select approach based on attempt number
        approach_index = loop_breaking_attempts % len(self._alternative_approaches)
        suggested_approach = self._alternative_approaches[approach_index]

        message = self._build_loop_breaking_message(
            pattern, loop_type, suggested_approach, loop_breaking_attempts, state
        )

        return self._create_plan(
            state,
            message=message,
            priority=2,
            metadata={
                "loop_type": loop_type,
                "attempt_number": loop_breaking_attempts + 1,
                "suggested_approach": suggested_approach,
            },
        )

    def _build_loop_breaking_message(
        self,
        pattern: DetectedPattern,
        loop_type: str,
        suggested_approach: str,
        attempt_number: int,
        state: Optional[AgentState] = None,
    ) -> str:
        """Build the intervention message for loop breaking."""
        # Get actionable command for this attempt (cycles through available commands)
        actionable_cmd = self._actionable_commands[attempt_number % len(self._actionable_commands)]

        if loop_type == "exact_duplicate":
            repeated_content = pattern.metadata.get("repeated_content", "")
            return f"""🔄 LOOP DETECTED: You've repeated the same response {pattern.metadata.get('response_count', 'multiple')} times.

This indicates you may be stuck. The repeated content was:
"{repeated_content[:150]}..."

**Stop and reconsider:**
1. Why isn't your approach working?
2. What's blocking progress?
3. {suggested_approach}

---
## 🎯 ACTIONABLE COMMAND: {actionable_cmd['command']}
{actionable_cmd['instruction']}

Please try a different approach or explain what obstacle you're facing."""

        elif loop_type == "high_similarity":
            similarity = pattern.metadata.get("average_similarity", 0)
            return f"""🔄 PATTERN DETECTED: Your responses are {similarity:.1%} similar to each other.

This suggests you're not making meaningful progress. 

**Try something different:**
- {suggested_approach}
- If you're waiting for something, explicitly state what
- If you're stuck, explain the specific obstacle

---
## 🎯 ACTIONABLE COMMAND: {actionable_cmd['command']}
{actionable_cmd['instruction']}

What's preventing you from moving forward?"""

        elif pattern.pattern_type == PatternType.REPETITIVE_ACTION:
            repeated_action = pattern.metadata.get("repeated_action", "unknown")
            repeat_type = pattern.metadata.get("repeat_type", "unknown")
            repeat_count = pattern.metadata.get("repeat_count", 0)

            if repeat_type == "exact_consecutive":
                details = f"You've performed the same exact action '{repeated_action}' {repeat_count} times in a row."
            else:
                details = f"You've repeated a sequence of actions {repeat_count} times."

            return f"""🔄 REPETITIVE ACTION DETECTED: {details}

Performing the same successful actions repeatedly indicates you may be in a logic loop.

**Stop and reconsider:**
1. Why are you repeating this action?
2. Has the state changed as expected?
3. {suggested_approach}

---
## 🎯 ACTIONABLE COMMAND: {actionable_cmd['command']}
{actionable_cmd['instruction']}

Please break this loop and take a different step."""

        else:
            # Progress stall - most common for scheduled tasks
            # Include MULTIPLE actionable commands for scheduled tasks
            iteration = pattern.metadata.get('iteration', 'unknown')

            # Issue #168: Monitoring task awareness
            is_monitoring = state.is_monitoring_task if state else False
            task_name = state.task_name if state else "Unknown Task"

            if is_monitoring:
                return f"""🔄 MONITORING CYCLE CHECK: You are running the monitoring task '{task_name}' at iteration {iteration}.

The supervisor has detected a potential stall because your responses or actions are highly repetitive.

**If this is intentional repetition (e.g., polling for changes):**
1. Briefly state: "PROGRESS: Monitoring cycle [N] - no changes detected."
2. Continue your next cycle as planned.

**If you are actually stuck or encountering errors:**
1. {suggested_approach}
2. Use `VERIFY_CURRENT_STATE` to check if your local cache is stale.

What is the current status of your monitoring cycle?"""

            # For scheduled task stalls, provide more aggressive intervention
            actionable_section = self._get_scheduled_task_intervention(attempt_number, iteration)

            return f"""🔄 STALL DETECTED: You appear to be stuck at iteration {iteration}.

**Suggested approach:** {suggested_approach}

{actionable_section}

Please either:
1. Execute the MANDATORY ACTION above
2. Explain what specific obstacle is blocking you
3. If tools are unavailable, list which tools you need

What would help you make progress?"""

    def _get_scheduled_task_intervention(self, attempt_number: int, iteration: Any) -> str:
        """
        Generate progressive intervention for stalls.

        Different attempts get progressively more aggressive interventions:
        - Attempt 0: Verify current state
        - Attempt 1: Reset assumptions  
        - Attempt 2+: Atomic actions with explicit progress logging
        """
        if attempt_number == 0:
            return """---
## 🎯 MANDATORY ACTION: VERIFY_CURRENT_STATE

**DO NOT rely on cached assumptions or local state.**

Execute these steps IMMEDIATELY:
1. Use available tools to check the ACTUAL current state
2. Compare with what you believe the state to be
3. Report any discrepancies found

**General Pattern:**
- If monitoring something: Query the live API, not local files
- If building something: Verify what's actually built vs your assumption
- If processing items: Check which items actually need processing

Local files and assumptions may be STALE or INCORRECT."""

        elif attempt_number == 1:
            return """---
## 🎯 MANDATORY ACTION: RESET_AND_RETRY

Your assumptions or local state tracking appears to be incorrect.

Execute these steps IMMEDIATELY:
1. **Ignore** previous assumptions about what's done/not done
2. **Clear** any local state tracking you've been maintaining  
3. **Start fresh** by verifying the actual state using tools
4. **Process ONE item** completely before checking overall status

After resetting, focus on making concrete progress on a single action."""

        else:
            return f"""---
## 🎯 MANDATORY ACTION: ATOMIC_ACTIONS + EXPLICIT_PROGRESS

You have been stuck for {attempt_number + 1} interventions. Take ATOMIC actions only.

Execute these steps IMMEDIATELY:
1. **Pick ONE item** to process (not a batch, just one)
2. **Process it completely** - do not check overall status
3. **Log explicit progress**: "PROGRESS: Completed [specific item], action: [what you did]"
4. **Move to next item** - do not loop back to status checking

**Anti-Pattern (AVOID):**
```
❌ Check status → claim "complete" → update tracking → check status → repeat
```

**Required Pattern:**
```
✅ Pick item → process item → log "PROGRESS: item done" → pick next item
```

**CRITICAL:** If you find yourself checking overall "status" or "coverage" more than once, you are in a loop. Process items individually, don't monitor status."""


class ToolAlternativeStrategy(InterventionStrategy):
    """
    Strategy for handling repeated tool failures.
    
    Suggests alternative tools or approaches when a tool keeps failing.
    """
    
    def __init__(self):
        self._tool_alternatives = {
            "code_execution": [
                "Try breaking the code into smaller pieces",
                "Use a different programming approach",
                "Check for syntax errors or missing dependencies",
            ],
            "browser": [
                "Try a different URL or search query",
                "Use a simpler browser action",
                "Consider if the information is available elsewhere",
            ],
            "memory": [
                "Try different search keywords",
                "Check if the memory was saved correctly",
                "Consider if the information exists in memory",
            ],
            "call_subordinate": [
                "Try handling the task yourself",
                "Break the task into smaller pieces",
                "Provide more specific instructions",
            ],
        }
    
    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.TOOL_ALTERNATIVE
    
    @property
    def handles_patterns(self) -> List[PatternType]:
        return [PatternType.TOOL_FAILURE_LOOP]
    
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        tool_name = pattern.metadata.get("tool_name", "unknown")
        failure_count = pattern.metadata.get("failure_count", 0)
        error_pattern = pattern.metadata.get("error_pattern", "")
        
        # Get alternatives for this tool
        alternatives = self._tool_alternatives.get(
            tool_name,
            ["Try a different approach", "Consider if this tool is appropriate"]
        )
        
        # Check previous attempts
        tool_alt_attempts = len([
            i for i in failed_interventions
            if i.intervention_type == InterventionType.TOOL_ALTERNATIVE
        ])
        
        if tool_alt_attempts >= len(alternatives):
            return None
        
        suggested_alternative = alternatives[tool_alt_attempts % len(alternatives)]
        
        message = self._build_tool_alternative_message(
            tool_name, failure_count, error_pattern, suggested_alternative
        )
        
        return self._create_plan(
            state,
            message=message,
            priority=3,
            metadata={
                "tool_name": tool_name,
                "failure_count": failure_count,
                "suggested_alternative": suggested_alternative,
            },
        )
    
    def _build_tool_alternative_message(
        self,
        tool_name: str,
        failure_count: int,
        error_pattern: str,
        suggested_alternative: str,
    ) -> str:
        """Build the intervention message for tool alternatives."""
        return f"""🔧 TOOL FAILURE: The '{tool_name}' tool has failed {failure_count} times.

Error pattern: "{error_pattern[:150]}..."

**Suggested alternative:** {suggested_alternative}

Options:
1. Fix the underlying issue causing the failure
2. Use a different tool or approach
3. Skip this step if it's not critical

What would you like to try instead?"""


class TaskRedirectionStrategy(InterventionStrategy):
    """
    Strategy for redirecting agents to different approaches.
    
    Used when an agent is stuck and needs a fresh perspective.
    """
    
    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.TASK_REDIRECTION
    
    @property
    def handles_patterns(self) -> List[PatternType]:
        return [
            PatternType.PROGRESS_STALL,
            PatternType.INFINITE_RECURSION,
            PatternType.OUTPUT_DEGRADATION,
        ]
    
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        # Check if we've already tried redirection
        redirection_attempts = len([
            i for i in failed_interventions
            if i.intervention_type == InterventionType.TASK_REDIRECTION
        ])
        
        if redirection_attempts >= 2:
            return None
        
        message = self._build_redirection_message(pattern, state, redirection_attempts)
        
        return self._create_plan(
            state,
            message=message,
            priority=4,
            metadata={
                "pattern_type": pattern.pattern_type.value,
                "attempt_number": redirection_attempts + 1,
            },
        )
    
    def _build_redirection_message(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        attempt_number: int,
    ) -> str:
        """Build the intervention message for task redirection."""
        if pattern.pattern_type == PatternType.INFINITE_RECURSION:
            return f"""⚠️ RECURSION LIMIT: You've created {state.subordinate_depth} levels of subordinate agents.

This suggests the task decomposition isn't working effectively.

**Please:**
1. Stop creating more subordinates
2. Handle the current task directly
3. If the task is too complex, explain what makes it difficult

Can you complete this task without delegating further?"""

        elif pattern.pattern_type == PatternType.OUTPUT_DEGRADATION:
            return f"""📉 OUTPUT QUALITY: Your responses appear to be degrading in quality.

This might indicate:
- Context window pressure
- Confusion about the task
- Fatigue in the conversation

**Please:**
1. Take a step back and clarify the current goal
2. Provide a clear, complete response
3. If you're unsure, ask for clarification

What is the specific task you're trying to accomplish right now?"""

        else:
            return f"""🔀 REDIRECTION: You've been working on this for {state.iteration} iterations without completion.

**Let's try a fresh approach:**
1. What is the core goal you're trying to achieve?
2. What's the simplest way to accomplish it?
3. What's blocking you from completing it?

Please provide a brief status update and your plan for moving forward."""


class BackoffStrategy(InterventionStrategy):
    """
    Strategy for handling rate limits.
    
    Implements intelligent backoff when rate limits are encountered.
    """
    
    def __init__(
        self,
        initial_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 300.0,
    ):
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
    
    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.BACKOFF_WAIT
    
    @property
    def handles_patterns(self) -> List[PatternType]:
        return [PatternType.RATE_LIMIT]
    
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        rate_limit_count = pattern.metadata.get("rate_limit_count", 0)
        
        # Calculate backoff time
        backoff_attempts = len([
            i for i in failed_interventions
            if i.intervention_type == InterventionType.BACKOFF_WAIT
        ])
        
        backoff_seconds = min(
            self.initial_backoff_seconds * (2 ** backoff_attempts),
            self.max_backoff_seconds
        )
        
        message = self._build_backoff_message(rate_limit_count, backoff_seconds)
        
        return self._create_plan(
            state,
            message=message,
            priority=6,  # Lower priority - rate limits are usually temporary
            timeout=int(backoff_seconds) + 60,
            metadata={
                "rate_limit_count": rate_limit_count,
                "backoff_seconds": backoff_seconds,
                "attempt_number": backoff_attempts + 1,
            },
        )
    
    def _build_backoff_message(
        self,
        rate_limit_count: int,
        backoff_seconds: float,
    ) -> str:
        """Build the intervention message for backoff."""
        return f"""⏳ RATE LIMIT: You've encountered {rate_limit_count} rate limit errors.

The API is throttling requests. Please:
1. Wait approximately {backoff_seconds:.0f} seconds before continuing
2. Consider batching operations to reduce API calls
3. Use cached information when possible

The system will automatically retry after the backoff period."""


# EnvironmentGuidanceStrategy extracted to python/helpers/strategies/environment_guidance.py (Issue #778)
from python.helpers.strategies.environment_guidance import EnvironmentGuidanceStrategy  # noqa: F401


class EscalationStrategy(InterventionStrategy):
    """
    Strategy for escalating to human intervention.
    
    Used when automatic interventions have failed.
    """
    
    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.ESCALATE
    
    @property
    def handles_patterns(self) -> List[PatternType]:
        return list(PatternType)  # Can handle any pattern as last resort
    
    async def plan(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        # Summarize failed attempts
        failed_summary = self._summarize_failed_interventions(failed_interventions)
        
        message = self._build_escalation_message(pattern, state, failed_summary)
        
        return self._create_plan(
            state,
            message=message,
            priority=1,  # Highest priority
            metadata={
                "pattern_type": pattern.pattern_type.value,
                "failed_intervention_count": len(failed_interventions),
                "escalation_reason": "automatic_interventions_exhausted",
            },
        )
    
    def _summarize_failed_interventions(
        self,
        failed_interventions: List[InterventionRecord],
    ) -> str:
        """Summarize what interventions were tried."""
        if not failed_interventions:
            return "No previous interventions attempted."
        
        summary_parts = []
        for i in failed_interventions[-5:]:  # Last 5 attempts
            summary_parts.append(
                f"- {i.intervention_type.value}: {i.outcome.value}"
            )
        
        return "\n".join(summary_parts)
    
    def _build_escalation_message(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_summary: str,
    ) -> str:
        """Build the escalation message."""
        return f"""🚨 ESCALATION REQUIRED: Automatic interventions have not resolved the issue.

**Problem:** {pattern.description}
**Agent:** {state.agent_id}
**Iteration:** {state.iteration}

**Previous intervention attempts:**
{failed_summary}

**Recommended actions:**
1. Review the agent's task and context
2. Provide manual guidance or clarification
3. Consider restarting the task with different parameters

The agent is paused pending human review."""


# =============================================================================
# Strategy Registry
# =============================================================================

class InterventionStrategyRegistry:
    """
    Registry for managing intervention strategies.
    
    Maps pattern types to appropriate strategies and handles
    strategy selection based on context.
    """
    
    def __init__(self):
        self._strategies: Dict[PatternType, List[InterventionStrategy]] = {}
        self._escalation_strategy: Optional[InterventionStrategy] = None
    
    def register(self, strategy: InterventionStrategy) -> None:
        """Register a strategy for its handled patterns."""
        for pattern_type in strategy.handles_patterns:
            if pattern_type not in self._strategies:
                self._strategies[pattern_type] = []
            self._strategies[pattern_type].append(strategy)
        
        # Track escalation strategy separately
        if strategy.intervention_type == InterventionType.ESCALATE:
            self._escalation_strategy = strategy
        
        logger.info(
            f"Registered strategy {strategy.intervention_type.value} "
            f"for patterns: {[p.value for p in strategy.handles_patterns]}"
        )
    
    def get_strategies(self, pattern_type: PatternType) -> List[InterventionStrategy]:
        """Get all strategies that can handle a pattern type."""
        return self._strategies.get(pattern_type, [])
    
    def get_escalation_strategy(self) -> Optional[InterventionStrategy]:
        """Get the escalation strategy."""
        return self._escalation_strategy
    
    async def plan_intervention(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
    ) -> Optional[InterventionPlan]:
        """
        Plan an intervention for a detected pattern.
        
        Tries strategies in order until one produces a plan.
        Falls back to escalation if all strategies fail.
        """
        strategies = self.get_strategies(pattern.pattern_type)
        
        for strategy in strategies:
            try:
                plan = await strategy.plan(pattern, state, failed_interventions)
                if plan:
                    logger.info(
                        f"Strategy {strategy.intervention_type.value} "
                        f"produced plan for {pattern.pattern_type.value}"
                    )
                    return plan
            except Exception as e:
                logger.error(
                    f"Error in strategy {strategy.intervention_type.value}: {e}"
                )
        
        # Fall back to escalation
        if self._escalation_strategy:
            logger.warning(
                f"All strategies exhausted for {pattern.pattern_type.value}, escalating"
            )
            return await self._escalation_strategy.plan(
                pattern, state, failed_interventions
            )
        
        return None


# =============================================================================
# Factory Functions
# =============================================================================

def create_default_strategy_registry() -> InterventionStrategyRegistry:
    """Create a registry with all default strategies."""
    registry = InterventionStrategyRegistry()
    
    # Register all strategies (order matters — tried first to last)
    registry.register(ContextCondensationStrategy())
    registry.register(LoopBreakingStrategy())
    registry.register(ToolAlternativeStrategy())
    registry.register(TaskRedirectionStrategy())
    registry.register(BackoffStrategy())
    registry.register(EnvironmentGuidanceStrategy())  # ENV-* patterns
    registry.register(EscalationStrategy())            # Last resort: human escalation
    
    return registry


def create_strategy_registry(
    config: Optional[Dict[str, Any]] = None
) -> InterventionStrategyRegistry:
    """Create a strategy registry with optional configuration."""
    registry = InterventionStrategyRegistry()
    config = config or {}
    
    # Context condensation
    ctx_config = config.get("context_condensation", {})
    registry.register(ContextCondensationStrategy(
        preserve_recent_messages=ctx_config.get("preserve_recent_messages", 5),
        summary_max_tokens=ctx_config.get("summary_max_tokens", 2000),
    ))
    
    # Loop breaking
    registry.register(LoopBreakingStrategy())
    
    # Tool alternative
    registry.register(ToolAlternativeStrategy())
    
    # Task redirection
    registry.register(TaskRedirectionStrategy())
    
    # Backoff
    backoff_config = config.get("backoff", {})
    registry.register(BackoffStrategy(
        initial_backoff_seconds=backoff_config.get("initial_seconds", 30.0),
        max_backoff_seconds=backoff_config.get("max_seconds", 300.0),
    ))
    
    # Escalation (always last resort)
    registry.register(EscalationStrategy())
    
    return registry


# =============================================================================
# Mode-Aware Intervention System
# =============================================================================

@dataclass
class ModeAwareThresholds:
    """
    Thresholds for intervention triggers, adjusted by mode.
    
    Different modes have different tolerance levels:
    - code: Standard thresholds (default)
    - architect: More patient (design takes time)
    - ask: Very patient (Q&A focused, minimal intervention)
    - debug: Slightly more patient (debugging is iterative)
    - review: More patient (thorough review takes time)
    """
    max_iterations_without_progress: int = 5
    max_consecutive_tool_failures: int = 2
    response_loop_threshold: int = 3
    context_warning_threshold: float = 0.76
    
    # Mode name for reference
    mode: str = "default"
    
    @classmethod
    def from_mode_settings(cls, mode: str, settings: Any) -> "ModeAwareThresholds":
        """Create thresholds from mode supervisor settings."""
        return cls(
            max_iterations_without_progress=getattr(
                settings, "max_iterations_without_progress", 5
            ),
            max_consecutive_tool_failures=getattr(
                settings, "max_consecutive_tool_failures", 2
            ),
            response_loop_threshold=getattr(
                settings, "response_loop_threshold", 3
            ),
            context_warning_threshold=getattr(
                settings, "context_warning_threshold", 0.76
            ),
            mode=mode,
        )
    
    @classmethod
    def default(cls) -> "ModeAwareThresholds":
        """Get default thresholds (code mode equivalent)."""
        return cls()


def get_agent_mode(agent: Any) -> str:
    """
    Get the current mode for an agent.
    
    Args:
        agent: The agent to get mode for.
        
    Returns:
        Mode slug string, or "code" as default.
    """
    # Check if agent has mode attribute (set by _60_mode_init extension)
    if hasattr(agent, "current_mode"):
        return agent.current_mode
    
    # Check agent config for mode
    if hasattr(agent, "config") and hasattr(agent.config, "mode"):
        return agent.config.mode
    
    # Default to code mode
    return "code"


def get_mode_thresholds(agent: Any) -> ModeAwareThresholds:
    """
    Get mode-aware thresholds for an agent.
    
    Args:
        agent: The agent to get thresholds for.
        
    Returns:
        ModeAwareThresholds configured for the agent's mode.
    """
    mode = get_agent_mode(agent)
    
    try:
        # Try to import mode manager (may not be available)
        from python.helpers.mode_manager import get_mode_manager
        
        manager = get_mode_manager()
        settings = manager.get_supervisor_settings(mode)
        
        return ModeAwareThresholds.from_mode_settings(mode, settings)
        
    except ImportError:
        logger.debug("Mode manager not available, using default thresholds")
        return ModeAwareThresholds.default()
    except Exception as e:
        logger.warning(f"Error getting mode thresholds: {e}, using defaults")
        return ModeAwareThresholds.default()


def get_mode_thresholds_by_name(mode: str) -> ModeAwareThresholds:
    """
    Get mode-aware thresholds by mode name.
    
    Args:
        mode: Mode slug (code, architect, ask, debug, review).
        
    Returns:
        ModeAwareThresholds configured for the mode.
    """
    try:
        from python.helpers.mode_manager import get_mode_manager
        
        manager = get_mode_manager()
        settings = manager.get_supervisor_settings(mode)
        
        return ModeAwareThresholds.from_mode_settings(mode, settings)
        
    except ImportError:
        logger.debug("Mode manager not available, using default thresholds")
        return ModeAwareThresholds.default()
    except Exception as e:
        logger.warning(f"Error getting mode thresholds for {mode}: {e}")
        return ModeAwareThresholds.default()


class ModeAwareStrategyRegistry:
    """
    Mode-aware wrapper for InterventionStrategyRegistry.
    
    Applies mode-specific thresholds when planning interventions.
    Different modes have different tolerance levels for patterns.
    """
    
    def __init__(self, base_registry: Optional[InterventionStrategyRegistry] = None):
        """
        Initialize mode-aware registry.
        
        Args:
            base_registry: Base registry to wrap. Creates default if not provided.
        """
        self._base_registry = base_registry or create_default_strategy_registry()
        self._mode_thresholds_cache: Dict[str, ModeAwareThresholds] = {}
    
    def get_thresholds(self, mode: str) -> ModeAwareThresholds:
        """Get cached thresholds for a mode."""
        if mode not in self._mode_thresholds_cache:
            self._mode_thresholds_cache[mode] = get_mode_thresholds_by_name(mode)
        return self._mode_thresholds_cache[mode]
    
    def clear_cache(self):
        """Clear the thresholds cache (useful after config reload)."""
        self._mode_thresholds_cache.clear()
    
    def should_intervene(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        mode: str = "code",
    ) -> bool:
        """
        Check if intervention should be triggered based on mode thresholds.
        
        Args:
            pattern: The detected pattern.
            state: Current agent state.
            mode: Agent's current mode.
            
        Returns:
            True if intervention should be triggered.
        """
        thresholds = self.get_thresholds(mode)
        
        # Check pattern-specific thresholds
        if pattern.pattern_type == PatternType.PROGRESS_STALL:
            iterations = pattern.metadata.get("iterations_without_progress", 0)
            return iterations >= thresholds.max_iterations_without_progress
        
        elif pattern.pattern_type == PatternType.TOOL_FAILURE_LOOP:
            failures = pattern.metadata.get("failure_count", 0)
            return failures >= thresholds.max_consecutive_tool_failures
        
        elif pattern.pattern_type == PatternType.RESPONSE_LOOP:
            loop_count = pattern.metadata.get("response_count", 0)
            return loop_count >= thresholds.response_loop_threshold
        
        elif pattern.pattern_type == PatternType.CONTEXT_OVERFLOW:
            usage_ratio = pattern.metadata.get("usage_ratio", 0)
            return usage_ratio >= thresholds.context_warning_threshold
        
        # For other patterns, use default behavior (always intervene)
        return True
    
    async def plan_intervention(
        self,
        pattern: DetectedPattern,
        state: AgentState,
        failed_interventions: List[InterventionRecord],
        mode: Optional[str] = None,
    ) -> Optional[InterventionPlan]:
        """
        Plan an intervention with mode-aware thresholds.
        
        Args:
            pattern: The detected pattern.
            state: Current agent state.
            failed_interventions: Previous failed interventions.
            mode: Agent's current mode (auto-detected if not provided).
            
        Returns:
            InterventionPlan if intervention is needed, None otherwise.
        """
        # Auto-detect mode from state if not provided
        effective_mode: str = mode if mode is not None else "code"
        
        # Try to get mode from state extra if available
        if mode is None and state.extra:
            effective_mode = str(state.extra.get("mode", "code"))
        
        # Check if we should intervene based on mode thresholds
        if not self.should_intervene(pattern, state, effective_mode):
            logger.debug(
                f"Pattern {pattern.pattern_type.value} below threshold for mode {effective_mode}"
            )
            return None
        
        # Delegate to base registry for actual planning
        plan = await self._base_registry.plan_intervention(
            pattern, state, failed_interventions
        )
        
        # Add mode info to plan metadata
        if plan:
            thresholds = self.get_thresholds(effective_mode)
            plan.metadata["mode"] = effective_mode
            plan.metadata["thresholds"] = {
                "max_iterations": thresholds.max_iterations_without_progress,
                "max_tool_failures": thresholds.max_consecutive_tool_failures,
                "loop_threshold": thresholds.response_loop_threshold,
                "context_threshold": thresholds.context_warning_threshold,
            }
        
        return plan
    
    def register(self, strategy: InterventionStrategy) -> None:
        """Register a strategy in the base registry."""
        self._base_registry.register(strategy)
    
    def get_strategies(self, pattern_type: PatternType) -> List[InterventionStrategy]:
        """Get strategies from base registry."""
        return self._base_registry.get_strategies(pattern_type)


def create_mode_aware_registry(
    config: Optional[Dict[str, Any]] = None
) -> ModeAwareStrategyRegistry:
    """
    Create a mode-aware strategy registry.
    
    Args:
        config: Optional configuration for base strategies.
        
    Returns:
        ModeAwareStrategyRegistry instance.
    """
    base_registry = create_strategy_registry(config)
    return ModeAwareStrategyRegistry(base_registry)


# =============================================================================
# Mode-Specific Intervention Messages
# =============================================================================

def get_mode_specific_guidance(mode: str, pattern_type: PatternType) -> str:
    """
    Get mode-specific guidance text for interventions.
    
    Different modes get different guidance based on their focus.
    
    Args:
        mode: Agent's current mode.
        pattern_type: Type of pattern detected.
        
    Returns:
        Mode-specific guidance string.
    """
    guidance = {
        "architect": {
            PatternType.PROGRESS_STALL: (
                "As an architect, consider if you need to delegate implementation "
                "to a Code mode subordinate. Focus on high-level design decisions."
            ),
            PatternType.RESPONSE_LOOP: (
                "You may be over-analyzing. Document your current design decision "
                "and move forward. Delegate implementation details to Code mode."
            ),
            PatternType.TOOL_FAILURE_LOOP: (
                "Architect mode has limited tool access. Consider if you need to "
                "delegate this task to a Code or Debug mode subordinate."
            ),
        },
        "ask": {
            PatternType.PROGRESS_STALL: (
                "If the question requires implementation, suggest switching to "
                "Code mode. Focus on providing clear, helpful answers."
            ),
            PatternType.RESPONSE_LOOP: (
                "You may be over-explaining. Provide a concise summary and ask "
                "if the user needs more detail on any specific aspect."
            ),
            PatternType.TOOL_FAILURE_LOOP: (
                "Ask mode has minimal tool access. If you need to execute code "
                "or make changes, recommend switching to Code mode."
            ),
        },
        "debug": {
            PatternType.PROGRESS_STALL: (
                "Form a clear hypothesis about the bug. If you've tested multiple "
                "hypotheses without success, consider asking for more context."
            ),
            PatternType.RESPONSE_LOOP: (
                "You may be stuck in a debugging loop. Step back and reconsider "
                "your assumptions. Try a different debugging approach."
            ),
            PatternType.TOOL_FAILURE_LOOP: (
                "If a debugging tool keeps failing, try a different approach. "
                "Consider adding logging or using simpler test cases."
            ),
        },
        "review": {
            PatternType.PROGRESS_STALL: (
                "Focus on the most critical issues first. If fixes are needed, "
                "delegate to a Code mode subordinate with specific instructions."
            ),
            PatternType.RESPONSE_LOOP: (
                "You may be over-reviewing. Prioritize issues by severity and "
                "provide actionable feedback. Delegate fixes to Code mode."
            ),
            PatternType.TOOL_FAILURE_LOOP: (
                "Review mode has limited tool access. If you need to test changes, "
                "delegate to a Code or Debug mode subordinate."
            ),
        },
        "code": {
            PatternType.PROGRESS_STALL: (
                "Break the problem into smaller pieces. If you're stuck on design, "
                "consider consulting an Architect mode agent."
            ),
            PatternType.RESPONSE_LOOP: (
                "You may be overthinking. Write the simplest solution that works, "
                "then iterate. Test your code before claiming completion."
            ),
            PatternType.TOOL_FAILURE_LOOP: (
                "Check error messages carefully. If a tool keeps failing, try a "
                "different approach or check for missing dependencies."
            ),
        },
    }
    
    mode_guidance = guidance.get(mode, guidance["code"])
    return mode_guidance.get(pattern_type, "")


def enhance_intervention_message(
    base_message: str,
    mode: str,
    pattern_type: PatternType,
) -> str:
    """
    Enhance an intervention message with mode-specific guidance.
    
    Args:
        base_message: The base intervention message.
        mode: Agent's current mode.
        pattern_type: Type of pattern detected.
        
    Returns:
        Enhanced message with mode-specific guidance.
    """
    guidance = get_mode_specific_guidance(mode, pattern_type)
    
    if guidance:
        return f"{base_message}\n\n**Mode-specific guidance ({mode}):**\n{guidance}"
    
    return base_message
