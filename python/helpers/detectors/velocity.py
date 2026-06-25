"""
Progress Velocity Detectors (Supervisor Reliability)

Gap 1: Proactive detection of slow stagnation using velocity metrics.

- ProgressVelocityDetector: Monitors message velocity, tool diversity, content novelty
"""

import json
from typing import Any, Dict, List, Optional

from python.helpers.loop_prevention import PatternType
from .base import PatternDetector, AgentState, DetectedPattern, RE_TIMESTAMP, RE_UUID, RE_DIGITS


class ProgressVelocityDetector(PatternDetector):
    """
    Gap 1: Proactive detection of slow stagnation using velocity metrics.
    
    Monitors:
    - Message velocity: Time between meaningful responses
    - Tool diversity: Are different tools being used?
    - Content novelty: Are responses substantially different?
    - Argument diversity: Are tool arguments changing?
    
    This enables early detection of subtle drift or stuck states
    before timeout-based supervisors would normally trigger.
    """
    
    def __init__(
        self,
        min_message_velocity: float = 0.5,  # Minimum messages per minute
        min_tool_diversity: float = 0.3,    # Minimum unique tools / total calls
        min_content_novelty: float = 0.4,   # Minimum novelty score (0-1)
        lookback_minutes: float = 5.0,      # Window to analyze
    ):
        self.min_message_velocity = min_message_velocity
        self.min_tool_diversity = min_tool_diversity
        self.min_content_novelty = min_content_novelty
        self.lookback_minutes = lookback_minutes
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    @property
    def is_deep(self) -> bool:
        return True  # This is a slower/more thorough detector
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Calculate metrics
        metrics = self._calculate_velocity_metrics(state)
        
        issues = []
        severity = "medium"
        confidence = 0.0
        
        # Check message velocity
        if metrics["message_velocity"] < self.min_message_velocity:
            issues.append(f"Low message velocity: {metrics['message_velocity']:.2f}/min")
            confidence += 0.3
            severity = "high"
        
        # Check tool diversity
        if metrics["tool_diversity"] < self.min_tool_diversity:
            issues.append(f"Low tool diversity: {metrics['tool_diversity']:.2f}")
            confidence += 0.3
        
        # Check content novelty
        if metrics["content_novelty"] < self.min_content_novelty:
            issues.append(f"Low content novelty: {metrics['content_novelty']:.2f}")
            confidence += 0.4
            severity = "high"
        
        if confidence >= 0.6 and issues:
            return self._create_pattern(
                state,
                confidence=min(confidence, 0.95),
                severity=severity,
                description="Slow progress detected: " + "; ".join(issues),
                metadata={
                    "pattern_id": "VELOCITY-001",
                    "metrics": metrics,
                    "issues": issues,
                },
            )
        
        return None
    
    def _calculate_velocity_metrics(self, state: AgentState) -> Dict[str, Any]:
        """Calculate velocity metrics from agent state."""
        # Message velocity (simplified - based on iteration count)
        iterations = state.iteration or 1
        # Assume ~30 seconds per iteration as baseline
        estimated_minutes = iterations * 0.5
        message_velocity = iterations / max(estimated_minutes, 1)
        
        # Tool diversity
        recent_tools = [tc.get("tool_name", "") for tc in state.recent_tool_calls[-10:]]
        unique_tools = len(set(recent_tools))
        total_tools = len(recent_tools)
        tool_diversity = unique_tools / max(total_tools, 1)
        
        # Content novelty (based on response similarity)
        content_novelty = 1.0
        if len(state.recent_responses) >= 2:
            # Compare consecutive responses
            novelties = []
            for i in range(1, len(state.recent_responses)):
                prev = state.recent_responses[i-1]
                curr = state.recent_responses[i]
                # Simple novelty: 1 - similarity
                if len(prev) > 0 and len(curr) > 0:
                    # Normalize both
                    prev_norm = self._normalize_for_novelty(prev)
                    curr_norm = self._normalize_for_novelty(curr)
                    # Calculate set-based similarity
                    prev_words = set(prev_norm.split()[:50])
                    curr_words = set(curr_norm.split()[:50])
                    if prev_words and curr_words:
                        overlap = len(prev_words & curr_words)
                        union = len(prev_words | curr_words)
                        similarity = overlap / max(union, 1)
                        novelties.append(1 - similarity)
            
            if novelties:
                content_novelty = sum(novelties) / len(novelties)
        
        # Argument diversity
        argument_diversity = self._calculate_argument_diversity(state.recent_tool_calls[-10:])
        
        return {
            "message_velocity": message_velocity,
            "tool_diversity": tool_diversity,
            "content_novelty": content_novelty,
            "argument_diversity": argument_diversity,
            "iteration": state.iteration,
        }
    
    def _normalize_for_novelty(self, text: str) -> str:
        """Normalize text for novelty calculation."""
        # Remove timestamps, UUIDs, and other ephemeral content
        text = RE_TIMESTAMP.sub("TIMESTAMP", text)
        text = RE_UUID.sub("UUID", text)
        text = RE_DIGITS.sub("N", text)
        return text.lower()
    
    def _calculate_argument_diversity(self, tool_calls: List[Dict[str, Any]]) -> float:
        """Calculate how diverse tool arguments are."""
        if not tool_calls:
            return 1.0
        
        # Group by tool name
        by_tool: Dict[str, List[Dict]] = {}
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            if tool_name not in by_tool:
                by_tool[tool_name] = []
            by_tool[tool_name].append(tc.get("arguments", {}))
        
        diversities = []
        for tool_name, args_list in by_tool.items():
            if len(args_list) < 2:
                continue
            
            # Check if arguments are changing
            arg_strings = [json.dumps(a, sort_keys=True) for a in args_list]
            unique_args = len(set(arg_strings))
            diversity = unique_args / len(arg_strings)
            diversities.append(diversity)
        
        return sum(diversities) / len(diversities) if diversities else 1.0


__all__ = [
    "ProgressVelocityDetector",
]