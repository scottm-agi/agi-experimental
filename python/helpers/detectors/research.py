"""
Research & Decision Making detectors (RES-001 to RES-010).

Detects issues with research approach and decision-making:
- PrematureEscalationDetector (RES-001)
- SingleAttemptAbandonmentDetector (RES-002)
- TheoryFreeDebuggingDetector (RES-006)
- AnalysisParalysisDetector (RES-010)
"""

from typing import Any, Dict, List, Optional

from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class PrematureEscalationDetector(PatternDetector):
    """RES-001: Detects when agent asks for help without researching first."""
    
    ESCALATION_SIGNALS = [
        "should i use", "which approach", "what do you think",
        "can you help", "i'm not sure", "what should i",
        "do you want me to", "shall i try",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        has_escalation = any(signal in response_lower for signal in self.ESCALATION_SIGNALS)
        
        if not has_escalation:
            return None
        
        research_tools = ["perplexity", "search", "get_documentation"]
        research_done = any(
            any(tool in str(tc.get("tool_name", "")).lower() for tool in research_tools)
            for tc in state.recent_tool_calls
        )
        
        if has_escalation and not research_done:
            return self._create_pattern(
                state,
                confidence=0.80,
                severity="high",
                description="Agent asking for direction without researching first",
                metadata={"pattern_id": "RES-001", "research_attempts": 0},
            )
        return None


class SingleAttemptAbandonmentDetector(PatternDetector):
    """RES-002: Detects when agent gives up after one attempt."""
    
    ABANDON_SIGNALS = [
        "didn't work", "doesn't work", "failed", "error",
        "what should i try", "any other ideas", "alternative",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        has_failure = any(signal in response_lower for signal in self.ABANDON_SIGNALS[:4])
        has_ask = any(signal in response_lower for signal in self.ABANDON_SIGNALS[4:])
        
        if has_failure and has_ask and state.iteration <= 3:
            return self._create_pattern(
                state,
                confidence=0.75,
                severity="high",
                description="Agent giving up after single attempt",
                metadata={"pattern_id": "RES-002", "iteration": state.iteration},
            )
        return None


class TheoryFreeDebuggingDetector(PatternDetector):
    """RES-006: Detects when agent makes changes without stating a hypothesis."""
    
    THEORY_INDICATORS = [
        "i believe", "my theory", "hypothesis", "i think the issue",
        "the root cause", "this is because", "the problem is",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.RESPONSE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        code_changes = any(
            tc.get("tool_name") in ["write_to_file", "replace_in_file"]
            for tc in state.recent_tool_calls[-3:]
        )
        
        if not code_changes:
            return None
        
        recent_text = " ".join(state.recent_responses[-3:]).lower()
        has_theory = any(indicator in recent_text for indicator in self.THEORY_INDICATORS)
        
        if not has_theory:
            return self._create_pattern(
                state,
                confidence=0.70,
                severity="high",
                description="Agent making code changes without stating hypothesis",
                metadata={"pattern_id": "RES-006"},
            )
        return None


class AnalysisParalysisDetector(PatternDetector):
    """RES-010: Detects when agent over-analyzes without deciding."""
    
    def __init__(self, max_analysis_iterations: int = 5):
        self.max_analysis_iterations = max_analysis_iterations
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        action_tools = ["write_to_file", "replace_in_file", "execute_command"]
        
        analysis_count = sum(
            1 for tc in state.recent_tool_calls[-10:]
            if tc.get("tool_name") not in action_tools
        )
        
        if analysis_count >= self.max_analysis_iterations:
            return self._create_pattern(
                state,
                confidence=0.75,
                severity="medium",
                description=f"Agent analyzing without action for {analysis_count} tool calls",
                metadata={"pattern_id": "RES-010", "analysis_count": analysis_count},
            )
        return None