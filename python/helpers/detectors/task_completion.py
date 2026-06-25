"""
Task Completion detectors (TSK-001 to TSK-010).

Detects issues with task completion claims:
- PrematureCompletionDetector (TSK-001)
- UntestedCompletionDetector (TSK-002)
- EvidenceFreeSuccessDetector (TSK-003)
- AsyncAbandonmentDetector (TSK-006)
"""

from typing import Optional
from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class PrematureCompletionDetector(PatternDetector):
    """TSK-001: Detects when agent claims completion prematurely."""
    
    COMPLETION_SIGNALS = [
        "done", "complete", "finished", "implemented", "ready",
        "task complete", "all done", "that's it", "successfully",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PREMATURE_COMPLETION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        has_completion = any(signal in response_lower for signal in self.COMPLETION_SIGNALS)
        
        if not has_completion:
            return None
            
        is_complex = len(state.initial_prompt) > 100 or any(x in state.initial_prompt.lower() for x in ["implement", "fix", "create", "debug"])
        
        if is_complex and state.iteration < 3:
            return self._create_pattern(
                state, confidence=0.80, severity="high",
                description=f"Agent claimed completion after only {state.iteration} iterations for a complex task",
                metadata={"pattern_id": "TSK-001", "iteration": state.iteration, "reason": "low_iteration_count"},
            )
            
        has_critical_tools = any(tc.get("tool_name") in ["write_to_file", "execute_command", "replace_in_file"] 
                                 for tc in state.recent_tool_calls)
        needs_actions = any(x in state.initial_prompt.lower() for x in ["create", "write", "fix", "implement", "update", "modify"])
        
        if needs_actions and not has_critical_tools:
            return self._create_pattern(
                state, confidence=0.85, severity="critical",
                description="Agent claimed completion without performing any file modifications or commands",
                metadata={"pattern_id": "TSK-001", "reason": "no_actions_performed"},
            )
        return None


class UntestedCompletionDetector(PatternDetector):
    """TSK-002: Detects when agent claims completion without testing."""
    
    COMPLETION_SIGNALS = ["done", "complete", "finished", "implemented", "ready", "task complete", "all done", "that's it"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PREMATURE_COMPLETION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        has_completion = any(signal in response_lower for signal in self.COMPLETION_SIGNALS)
        
        if not has_completion:
            return None
        
        test_patterns = ["test", "pytest", "npm test", "jest", "mocha"]
        tests_run = any(
            tc.get("tool_name") == "execute_command" and
            any(p in str(tc.get("arguments", {})).lower() for p in test_patterns)
            for tc in state.recent_tool_calls[-5:]
        )
        
        if has_completion and not tests_run:
            return self._create_pattern(
                state, confidence=0.85, severity="critical",
                description="Agent claiming completion without running tests",
                metadata={"pattern_id": "TSK-002"},
            )
        return None


class EvidenceFreeSuccessDetector(PatternDetector):
    """TSK-003: Detects when agent claims success without providing evidence."""
    
    SUCCESS_SIGNALS = ["it works", "working now", "fixed", "resolved", "success", "should work", "will work"]
    EVIDENCE_INDICATORS = ["output:", "result:", "log:", "response:", "```", "shows:", "returns:", "prints:"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PREMATURE_COMPLETION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        has_success = any(signal in response_lower for signal in self.SUCCESS_SIGNALS)
        has_evidence = any(indicator in response_lower for indicator in self.EVIDENCE_INDICATORS)
        
        if has_success and not has_evidence:
            return self._create_pattern(
                state, confidence=0.80, severity="high",
                description="Agent claiming success without providing evidence",
                metadata={"pattern_id": "TSK-003"},
            )
        return None


class AsyncAbandonmentDetector(PatternDetector):
    """TSK-006: Detects when agent starts async operations without monitoring."""
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for tc in state.recent_tool_calls[-5:]:
            if tc.get("tool_name") == "execute_command":
                cmd = str(tc.get("arguments", {}).get("command", ""))
                if "&" in cmd or "nohup" in cmd or "background" in cmd.lower():
                    has_status_check = any(
                        "status" in str(tc2.get("arguments", {})).lower() or
                        "ps" in str(tc2.get("arguments", {})).lower()
                        for tc2 in state.recent_tool_calls[-3:]
                    )
                    if not has_status_check:
                        return self._create_pattern(
                            state, confidence=0.75, severity="high",
                            description="Agent started background process without monitoring",
                            metadata={"pattern_id": "TSK-006", "command": cmd[:100]},
                        )
        return None