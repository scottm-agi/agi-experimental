"""
Agent Coordination Detectors (COORD-001 to COORD-010)

Detects issues with agent coordination:
- TaskAmbiguityDetector: Ambiguous task delegation
- OverDelegationDetector: Excessive delegation for simple tasks
"""

from typing import Optional

from python.helpers.loop_prevention import PatternType
from .base import PatternDetector, AgentState, DetectedPattern


class TaskAmbiguityDetector(PatternDetector):
    """
    COORD-001: Detects ambiguous task delegation.
    """
    
    AMBIGUITY_INDICATORS = [
        "what do you mean", "clarify", "unclear", "ambiguous",
        "not sure what", "which one", "please specify",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.INFINITE_RECURSION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Check if subordinate is asking for clarification
        for response in state.recent_responses[-3:]:
            response_lower = response.lower()
            if any(indicator in response_lower for indicator in self.AMBIGUITY_INDICATORS):
                return self._create_pattern(
                    state,
                    confidence=0.75,
                    severity="medium",
                    description="Task ambiguity detected - subordinate asking for clarification",
                    metadata={
                        "pattern_id": "COORD-001",
                    },
                )
        
        return None


class OverDelegationDetector(PatternDetector):
    """
    COORD-007: Detects excessive delegation for simple tasks.
    """
    
    def __init__(self, delegation_threshold: int = 3):
        self.delegation_threshold = delegation_threshold
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.INFINITE_RECURSION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Count subordinate calls
        subordinate_calls = sum(
            1 for tc in state.recent_tool_calls[-10:]
            if tc.get("tool_name") in ["call_subordinate", "call_subordinate_batch", "fan_out_subordinates"]
        )
        
        if subordinate_calls >= self.delegation_threshold:
            return self._create_pattern(
                state,
                confidence=0.70,
                severity="medium",
                description=f"Excessive delegation detected ({subordinate_calls} subordinate calls)",
                metadata={
                    "pattern_id": "COORD-007",
                    "subordinate_calls": subordinate_calls,
                },
            )
        
        return None


__all__ = [
    "TaskAmbiguityDetector",
    "OverDelegationDetector",
]