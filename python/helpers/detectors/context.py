"""
Context & Token Management detectors (CTX-001 to CTX-010).

Detects issues related to context window usage and token management:
- PrematureTerminationDetector (CTX-001)
- ExcessiveContextReadingDetector (CTX-002)
- MemoryBankObsessionDetector (CTX-004)
- VerboseResponseDetector (CTX-007)
- TokenAnxietyDetector (CTX-010)
"""

from typing import Any, Dict, List, Optional

from python.helpers.loop_prevention import PatternType
from .base import AgentState, DetectedPattern, PatternDetector


class PrematureTerminationDetector(PatternDetector):
    """
    CTX-001: Detects when agent mentions token limits or suggests stopping prematurely.
    """
    
    TERMINATION_SIGNALS = [
        "token budget", "token limit", "context limit", "running out of",
        "should i continue", "shall i stop", "i've used most of my context",
        "reaching my limit", "context window full", "out of tokens",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.CONTEXT_OVERFLOW
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        
        for signal in self.TERMINATION_SIGNALS:
            if signal in response_lower:
                return self._create_pattern(
                    state,
                    confidence=0.90,
                    severity="critical",
                    description=f"Agent mentioned token/context limits: '{signal}'",
                    metadata={
                        "pattern_id": "CTX-001",
                        "signal_detected": signal,
                        "response_excerpt": state.last_response[:200],
                    },
                )
        
        return None


class ExcessiveContextReadingDetector(PatternDetector):
    """
    CTX-002: Detects when agent reads too many files before starting work.
    """
    
    def __init__(self, max_reads_before_work: int = 6, context_time_threshold: float = 0.30):
        self.max_reads_before_work = max_reads_before_work
        self.context_time_threshold = context_time_threshold
        self._read_counts: Dict[str, int] = {}
        self._work_started: Dict[str, bool] = {}
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Count file reads from tool calls
        read_count = sum(
            1 for tc in state.recent_tool_calls
            if tc.get("tool_name") in ["read_file", "list_files", "search_files"]
        )
        
        # Check for work tools (write, execute, etc.)
        work_tools = ["write_to_file", "execute_command", "replace_in_file"]
        has_work = any(
            tc.get("tool_name") in work_tools
            for tc in state.recent_tool_calls
        )
        
        if not has_work and read_count > self.max_reads_before_work:
            return self._create_pattern(
                state,
                confidence=0.80,
                severity="medium",
                description=f"Agent read {read_count} files without starting work",
                metadata={
                    "pattern_id": "CTX-002",
                    "read_count": read_count,
                    "max_allowed": self.max_reads_before_work,
                },
            )
        
        return None


class MemoryBankObsessionDetector(PatternDetector):
    """
    CTX-004: Detects when agent reads memory-bank for standalone tasks.
    """
    
    STANDALONE_INDICATORS = [
        "simple script", "quick fix", "one-off", "standalone",
        "independent", "isolated", "single file",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Check if reading memory-bank files via read_file
        memory_bank_reads = sum(
            1 for tc in state.recent_tool_calls
            if tc.get("tool_name") == "read_file" and 
            "memory-bank" in str(tc.get("arguments", {}).get("path", ""))
        )
        
        # Also count maintain_memory_bank tool calls (any mode)
        maintain_mb_calls = sum(
            1 for tc in state.recent_tool_calls
            if tc.get("tool_name") == "maintain_memory_bank"
        )
        
        total_memory_bank_activity = memory_bank_reads + maintain_mb_calls
        
        if total_memory_bank_activity >= 2:
            return self._create_pattern(
                state,
                confidence=0.70,
                severity="medium",
                description=(
                    f"Agent obsessively accessing memory-bank "
                    f"({memory_bank_reads} read_file + {maintain_mb_calls} maintain_memory_bank "
                    f"= {total_memory_bank_activity} total)"
                ),
                metadata={
                    "pattern_id": "CTX-004",
                    "memory_bank_reads": memory_bank_reads,
                    "maintain_mb_calls": maintain_mb_calls,
                    "total_memory_bank_activity": total_memory_bank_activity,
                },
            )
        
        return None


class VerboseResponseDetector(PatternDetector):
    """
    CTX-007: Detects when agent produces consistently verbose responses.
    """
    
    def __init__(self, max_avg_length: int = 1000, min_responses: int = 3):
        self.max_avg_length = max_avg_length
        self.min_responses = min_responses
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.OUTPUT_DEGRADATION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if len(state.recent_responses) < self.min_responses:
            return None
        
        avg_length = sum(len(r) for r in state.recent_responses) / len(state.recent_responses)
        
        if avg_length > self.max_avg_length:
            return self._create_pattern(
                state,
                confidence=0.75,
                severity="medium",
                description=f"Agent responses averaging {avg_length:.0f} chars (threshold: {self.max_avg_length})",
                metadata={
                    "pattern_id": "CTX-007",
                    "average_length": avg_length,
                    "threshold": self.max_avg_length,
                },
            )
        
        return None


class TokenAnxietyDetector(PatternDetector):
    """
    CTX-010: Detects when agent expresses concern about tokens.
    """
    
    ANXIETY_SIGNALS = [
        "save tokens", "conserve tokens", "token count", "remaining tokens",
        "token usage", "how many tokens", "tokens left", "brief to save",
    ]
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.CONTEXT_OVERFLOW
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.last_response:
            return None
        
        response_lower = state.last_response.lower()
        
        for signal in self.ANXIETY_SIGNALS:
            if signal in response_lower:
                return self._create_pattern(
                    state,
                    confidence=0.85,
                    severity="low",
                    description=f"Agent expressing token anxiety: '{signal}'",
                    metadata={
                        "pattern_id": "CTX-010",
                        "signal_detected": signal,
                    },
                )
        
        return None