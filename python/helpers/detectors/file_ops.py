"""
File Operations detectors (FILE-001 to FILE-010).
"""
from typing import Optional
from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class FileWriteTruncationDetector(PatternDetector):
    """FILE-001: Detects potential file write truncation."""
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for i, tc in enumerate(state.recent_tool_calls[:-1]):
            if tc.get("tool_name") == "write_to_file":
                if i + 1 < len(state.recent_tool_results):
                    result_str = str(state.recent_tool_results[i + 1]).lower()
                    if "syntax error" in result_str or "unexpected end" in result_str:
                        return self._create_pattern(
                            state, confidence=0.85, severity="critical",
                            description="Possible file truncation detected (syntax error after write)",
                            metadata={"pattern_id": "FILE-001"},
                        )
        return None


class SequentialFileReadingDetector(PatternDetector):
    """FILE-002: Detects inefficient sequential file reading."""
    
    def __init__(self, sequential_threshold: int = 3):
        self.sequential_threshold = sequential_threshold
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        consecutive_reads = 0
        for tc in state.recent_tool_calls[-10:]:
            if tc.get("tool_name") == "read_file":
                consecutive_reads += 1
            else:
                consecutive_reads = 0
        
        if consecutive_reads >= self.sequential_threshold:
            return self._create_pattern(
                state, confidence=0.70, severity="medium",
                description=f"Agent reading files sequentially ({consecutive_reads} consecutive reads)",
                metadata={"pattern_id": "FILE-002", "consecutive_reads": consecutive_reads},
            )
        return None


class SyntaxCorruptionDetector(PatternDetector):
    """FILE-004: Detects syntax corruption after file edits."""
    
    SYNTAX_ERROR_PATTERNS = ["syntaxerror", "syntax error", "unexpected token", "unexpected end", "missing", "unclosed", "invalid syntax"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for error in state.recent_errors:
            error_lower = error.lower()
            if any(pattern in error_lower for pattern in self.SYNTAX_ERROR_PATTERNS):
                return self._create_pattern(
                    state, confidence=0.90, severity="high",
                    description="Syntax error detected after file operation",
                    metadata={"pattern_id": "FILE-004", "error": error[:200]},
                )
        return None