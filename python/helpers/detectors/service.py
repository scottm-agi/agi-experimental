"""Service & Process Management detectors (SVC-001 to SVC-010)."""
from typing import Optional
from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class NoServiceRestartDetector(PatternDetector):
    """SVC-001: Detects when code changes are made without service restart."""
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        code_changes = [tc for tc in state.recent_tool_calls[-10:] if tc.get("tool_name") in ["write_to_file", "replace_in_file"]]
        if not code_changes:
            return None
        
        restart_patterns = ["restart", "kill", "stop", "start", "npm run", "python run"]
        has_restart = any(
            tc.get("tool_name") == "execute_command" and any(p in str(tc.get("arguments", {})).lower() for p in restart_patterns)
            for tc in state.recent_tool_calls[-5:]
        )
        
        if not has_restart:
            return self._create_pattern(state, confidence=0.80, severity="critical",
                description="Code changes made without service restart",
                metadata={"pattern_id": "SVC-001", "code_changes": len(code_changes)})
        return None


class PortConflictDetector(PatternDetector):
    """SVC-005: Detects port conflict errors."""
    
    PORT_ERROR_PATTERNS = ["address already in use", "eaddrinuse", "port already", "bind failed", "port is already", "cannot bind"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for error in state.recent_errors:
            if any(pattern in error.lower() for pattern in self.PORT_ERROR_PATTERNS):
                return self._create_pattern(state, confidence=0.95, severity="high",
                    description="Port conflict detected", metadata={"pattern_id": "SVC-005", "error": error[:200]})
        return None