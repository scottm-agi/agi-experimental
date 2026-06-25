"""
Completion Detector Extension
Detects when an agent claims task completion and triggers verification.
Part of Supervisor Reliability Enhancement - Gap 3.
Priority: 30 (runs after heartbeat)
"""

from python.helpers.extension import Extension
from python.helpers.log import Log
import re
import time


class CompletionDetectorExtension(Extension):
    """Extension to detect completion claims and trigger verification."""
    
    # Patterns that indicate task completion claims
    COMPLETION_PATTERNS = [
        r"\btask\s+(is\s+)?complete[d]?\b",
        r"\ball\s+done\b",
        r"\bfinished\s+(the\s+)?task\b",
        r"\bsuccessfully\s+completed\b",
        r"\bhere['']?s?\s+the\s+(final\s+)?result\b",
        r"\bi['']?ve\s+finished\b",
        r"\bthe\s+work\s+is\s+complete\b",
        r"\bimplementation\s+(is\s+)?complete\b",
        r"\bchanges\s+(have\s+been\s+)?committed\b",
        r"\bready\s+for\s+review\b"
    ]
    
    # Track which contexts have had completion detected (avoid duplicate signals)
    # FIX-023 (G-15): Must be instance-scoped, NOT class-level.
    # Class-level dict caused cross-agent contamination.
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._completion_detected: dict = {}
    
    async def execute(self, loop_data=None, **kwargs):
        """Execute completion detection on each message loop iteration."""
        agent = self.agent
        context = agent.context
        
        # Get the history
        history = context.history if hasattr(context, 'history') else []
        if not history:
            return loop_data
        
        # Get the last assistant message
        assistant_msgs = [m for m in history if m.get("role") == "assistant"]
        if not assistant_msgs:
            return loop_data
        
        last_response = assistant_msgs[-1].get("content", "")
        if not last_response:
            return loop_data
        
        # Check if we already detected completion for this context recently
        context_key = context.id
        last_detection = self._completion_detected.get(context_key, 0)
        now = time.time()
        
        # Only check once per 60 seconds per context
        if now - last_detection < 60:
            return loop_data
        
        # Check for completion indicators
        for pattern in self.COMPLETION_PATTERNS:
            if re.search(pattern, last_response, re.IGNORECASE):
                await self._handle_completion_claim(agent, context, last_response, pattern)
                self._completion_detected[context_key] = now
                break
        
        return loop_data
    
    async def _handle_completion_claim(self, agent, context, response: str, matched_pattern: str):
        """Handle a detected completion claim."""
        Log.info(f"[CompletionDetector] Completion claim detected (pattern: {matched_pattern})")
        
        # Import here to avoid circular imports
        try:
            from python.helpers.goal_state_manager import GoalStateManager
            from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
        except ImportError as e:
            Log.debug(f"[CompletionDetector] Import error: {e}")
            return
        
        # Record in goal state
        try:
            gsm = GoalStateManager.get_instance()
            gsm.record_completion_claim(context.id)
        except Exception as e:
            Log.debug(f"[CompletionDetector] Could not record completion claim: {e}")
        
        # Publish signal for supervisor verification
        try:
            # Check if TASK_CLAIMS_COMPLETE exists
            if hasattr(SignalType, 'TASK_CLAIMS_COMPLETE'):
                signal_type = SignalType.TASK_CLAIMS_COMPLETE
            else:
                # Use a fallback signal type
                Log.debug("[CompletionDetector] TASK_CLAIMS_COMPLETE not in SignalType, skipping signal")
                return
            
            signal = AgentSignal(
                signal_type=signal_type,
                agent_id=str(agent.number),
                context_id=context.id,
                severity="info",
                error_message=f"Agent claims completion: {response[:200]}",
                timestamp=time.time()
            )
            
            event_bus = get_event_bus()
            await event_bus.publish(signal)
            
            Log.info(f"[CompletionDetector] Published completion claim signal for agent {agent.number}")
            
        except Exception as e:
            Log.debug(f"[CompletionDetector] Failed to publish signal: {e}")
