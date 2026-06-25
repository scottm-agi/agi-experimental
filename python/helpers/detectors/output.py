"""
Output Quality Detectors (OUT-001 to OUT-010)

Detects issues with agent output quality:
- HallucinatedReferencesDetector: References to non-existent files/functions
- HardcodedCredentialsDetector: Hardcoded credentials in code
"""

import re
from typing import Optional

from python.helpers.loop_prevention import PatternType
from .base import PatternDetector, AgentState, DetectedPattern


class HallucinatedReferencesDetector(PatternDetector):
    """
    OUT-001: Detects references to non-existent files/functions.
    """
    
    NOT_FOUND_PATTERNS = [
        "file not found", "no such file", "module not found",
        "cannot find", "does not exist", "undefined", "not defined",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        not_found_count = 0
        for error in state.recent_errors:
            error_lower = error.lower()
            if any(pattern in error_lower for pattern in self.NOT_FOUND_PATTERNS):
                not_found_count += 1
        
        if not_found_count >= 2:
            return self._create_pattern(
                state,
                confidence=0.80,
                severity="high",
                description=f"Multiple 'not found' errors ({not_found_count}) - possible hallucinated references",
                metadata={
                    "pattern_id": "OUT-001",
                    "not_found_count": not_found_count,
                },
            )
        
        return None


class HardcodedCredentialsDetector(PatternDetector):
    """
    OUT-006: Detects hardcoded credentials in code.
    """
    
    CREDENTIAL_PATTERNS = [
        r'password\s*=\s*["\'][^"\']+["\']',
        r'api_key\s*=\s*["\'][^"\']+["\']',
        r'secret\s*=\s*["\'][^"\']+["\']',
        r'token\s*=\s*["\'][^"\']+["\']',
    ]
    
    _CREDENTIAL_RE = [re.compile(p, re.IGNORECASE) for p in CREDENTIAL_PATTERNS]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.OUTPUT_DEGRADATION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Check recent responses for credential patterns
        for response in state.recent_responses[-3:]:
            for cre_re in self._CREDENTIAL_RE:
                if cre_re.search(response):
                    return self._create_pattern(
                        state,
                        confidence=0.90,
                        severity="critical",
                        description="Possible hardcoded credentials detected",
                        metadata={
                            "pattern_id": "OUT-006",
                        },
                    )
        
        return None


__all__ = [
    "HallucinatedReferencesDetector",
    "HardcodedCredentialsDetector",
]