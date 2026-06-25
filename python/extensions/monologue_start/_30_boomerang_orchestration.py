from __future__ import annotations
"""
Boomerang Orchestration Extension

Auto-invokes Boomerang orchestration for complex tasks at the start
of a monologue. This implements RooCode's automatic task decomposition
and delegation workflow.

When a complex task is detected:
1. Analyzes task complexity
2. Decomposes into subtasks
3. Adds orchestration context to agent's prompt
4. Guides agent to delegate appropriately
"""

from python.helpers.extension import Extension
from python.agent import LoopData
import logging

logger = logging.getLogger("agix.boomerang_ext")

# Try to import Boomerang orchestrator
try:
    from python.helpers.boomerang import (
        get_boomerang_orchestrator,
        should_use_boomerang,
        TaskComplexity,
    )
    BOOMERANG_SUPPORT = True
except ImportError:
    BOOMERANG_SUPPORT = False


class BoomerangOrchestration(Extension):
    """
    Extension that auto-invokes Boomerang orchestration for complex tasks.
    
    Runs at the start of each monologue to:
    1. Check if the task is complex enough for orchestration
    2. Decompose the task into subtasks
    3. Add orchestration guidance to the agent's context
    """
    
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        """
        Check task complexity and start orchestration if needed.
        
        Args:
            loop_data: Loop data containing message history
            **kwargs: Additional arguments
        """
        if not BOOMERANG_SUPPORT:
            return
        
        # Only run for agent 0 (top-level agent)
        if self.agent.number != 0:
            return
        
        # ─── Delegation-aware guard ──
        # If the parent agent already has a delegation result in its history
        # (i.e., call_subordinate returned with break_loop=False and the result
        # is in context), the LLM will naturally see it and decide. No need
        # for boomerang to re-inject — the agent already has context.
        
        # Check if orchestration is already active (only if no delegation happened yet)
        orchestrator = get_boomerang_orchestrator(self.agent)
        if orchestrator.is_active:
            # Add orchestration context to prompt
            self._add_orchestration_context(orchestrator)
            return

        # Get the latest user message
        user_message = self._get_latest_user_message()
        if not user_message:
            return
        
        # Check if this task should use Boomerang
        if not should_use_boomerang(self.agent, user_message):
            logger.debug(f"Task not complex enough for Boomerang: {user_message[:50]}...")
            return
        
        # Start orchestration
        logger.info(f"Starting Boomerang orchestration for: {user_message[:100]}...")
        
        try:
            state = await orchestrator.start_orchestration(user_message)
            
            # Store orchestration state in agent data
            self.agent.set_data("boomerang_state", {
                "task_id": state.task_id,
                "complexity": state.complexity.value,
                "subtasks": state.subtasks,
                "phase": state.phase.value,
            })
            
            # Add orchestration guidance to agent's context
            self._add_orchestration_context(orchestrator)
            
            # Log the decomposition
            logger.info(
                f"Boomerang decomposed task into {len(state.subtasks)} subtasks: "
                f"{[s['id'] for s in state.subtasks]}"
            )
            
        except Exception as e:
            logger.error(f"Failed to start Boomerang orchestration: {e}")
    
    def _get_latest_user_message(self) -> str:
        """
        Get the latest user message from python.history.
        
        Returns:
            User message text or empty string
        """
        # Try to get from python.agent's history
        history = self.agent.history
        if not history:
            return ""
        
        # History object has an output() method that returns list[OutputMessage]
        messages = []
        
        # Method 1: Try to call output() method (History class)
        if hasattr(history, 'output') and callable(history.output):
            try:
                messages = history.output()
            except Exception:
                pass
        
        # Method 2: If it's already a list
        if not messages and isinstance(history, list):
            messages = history
        
        if not messages:
            return ""
        
        # Find the last user message (iterate from end)
        # OutputMessage is a TypedDict with 'role' and 'content' keys
        for msg in reversed(messages):
            # Handle TypedDict/dict style access
            if isinstance(msg, dict):
                if msg.get('role') == 'user':
                    return str(msg.get('content', ''))
            # Handle object-style access (fallback)
            elif hasattr(msg, 'role') and getattr(msg, 'role', None) == 'user':
                content = getattr(msg, 'content', '')
                return str(content) if content else ''
        
        return ""
    
    def _add_orchestration_context(self, orchestrator):
        """
        Add orchestration context to agent's prompt.
        
        Args:
            orchestrator: BoomerangOrchestrator instance
        """
        # Get orchestration prompt
        prompt_addition = orchestrator.get_orchestration_prompt()
        if not prompt_addition:
            return
        
        # Store in agent data for prompt building
        self.agent.set_data("boomerang_prompt", prompt_addition)
        
        # Also add guidance for delegation
        guidance = self._generate_delegation_guidance(orchestrator)
        self.agent.set_data("boomerang_guidance", guidance)
    
    def _generate_delegation_guidance(self, orchestrator) -> str:
        """
        Generate guidance for how to delegate subtasks.
        
        Args:
            orchestrator: BoomerangOrchestrator instance
            
        Returns:
            Guidance text
        """
        if not orchestrator._state:
            return ""
        
        state = orchestrator._state
        pending_subtasks = [
            s for s in state.subtasks
            if not any(r.subtask_id == s["id"] for r in state.results)
        ]
        
        if not pending_subtasks:
            return "All subtasks completed. Synthesize the results and provide final response."
        
        # Find next subtask (respecting dependencies)
        next_subtask = None
        for subtask in pending_subtasks:
            deps = subtask.get("dependencies", [])
            if all(
                any(r.subtask_id == dep and r.success for r in state.results)
                for dep in deps
            ):
                next_subtask = subtask
                break
        
        if not next_subtask:
            return "Waiting for dependencies to complete."
        
        guidance = f"""
## Delegation Guidance

**Next subtask:** {next_subtask['description']}
**Recommended mode:** {next_subtask.get('mode', 'code')}

To delegate this subtask, use:
```
call_subordinate(
    message="{next_subtask['description']}",
    mode="{next_subtask.get('mode', 'code')}"
)
```

After receiving the result, continue with the next subtask or synthesize if all complete.
"""
        return guidance
