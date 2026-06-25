"""API & Network detectors (API-001 to API-010)."""
from typing import Optional
from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class TimeoutCascadeDetector(PatternDetector):
    """API-002: Detects cascading timeout errors."""
    
    TIMEOUT_PATTERNS = ["timeout", "timed out", "connection timeout", "read timeout", "request timeout", "deadline exceeded"]
    
    def __init__(self, timeout_threshold: int = 3):
        self.timeout_threshold = timeout_threshold
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.RATE_LIMIT
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        timeout_count = sum(1 for e in state.recent_errors if any(p in e.lower() for p in self.TIMEOUT_PATTERNS))
        if timeout_count >= self.timeout_threshold:
            return self._create_pattern(state, confidence=0.85, severity="high",
                description=f"Timeout cascade detected ({timeout_count} timeouts)",
                metadata={"pattern_id": "API-002", "timeout_count": timeout_count})
        return None


class AuthTokenExpiryDetector(PatternDetector):
    """API-003: Detects authentication token expiry."""
    
    AUTH_ERROR_PATTERNS = ["401", "unauthorized", "token expired", "invalid token", "authentication failed", "auth error", "forbidden"]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        for error in state.recent_errors:
            if any(pattern in error.lower() for pattern in self.AUTH_ERROR_PATTERNS):
                return self._create_pattern(state, confidence=0.90, severity="high",
                    description="Authentication error detected",
                    metadata={"pattern_id": "API-003", "error": error[:200]})
        return None