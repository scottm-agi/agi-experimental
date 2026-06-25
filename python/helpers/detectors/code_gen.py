"""Code Generation & Modification detectors (CODE-001 to CODE-010)."""
from typing import Optional
from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class CircularImportDetector(PatternDetector):
    """CODE-002: Detects circular import errors."""
    
    CIRCULAR_PATTERNS = ["circular import", "importerror", "cannot import name", "partially initialized module", "most likely due to a circular"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for error in state.recent_errors:
            if any(pattern in error.lower() for pattern in self.CIRCULAR_PATTERNS):
                return self._create_pattern(state, confidence=0.90, severity="high",
                    description="Circular import detected", metadata={"pattern_id": "CODE-002", "error": error[:200]})
        return None


class MergeConflictDetector(PatternDetector):
    """CODE-005: Detects merge conflict markers in code."""
    
    CONFLICT_MARKERS = ["<<<<<<<", "=======", ">>>>>>>"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for response in state.recent_responses[-3:]:
            if any(marker in response for marker in self.CONFLICT_MARKERS):
                return self._create_pattern(state, confidence=0.95, severity="high",
                    description="Merge conflict markers detected in code", metadata={"pattern_id": "CODE-005"})
        return None


class VersionConflictDetector(PatternDetector):
    """CODE-010: Detects package version conflicts."""
    
    VERSION_PATTERNS = ["version conflict", "peer dependency", "incompatible", "requires", "but found", "version mismatch"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for error in state.recent_errors:
            if any(pattern in error.lower() for pattern in self.VERSION_PATTERNS):
                return self._create_pattern(state, confidence=0.85, severity="high",
                    description="Package version conflict detected", metadata={"pattern_id": "CODE-010", "error": error[:200]})
        return None