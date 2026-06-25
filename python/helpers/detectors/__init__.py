"""
Unified Pattern Detection System for AGIX Supervisor

This module provides comprehensive pattern detection capabilities to identify when agents
are stuck, looping, or experiencing issues that require intervention.

Contains all 35 pattern detectors covering the 100 human intervention patterns
organized into 12 categories:

1. Base Classes: PatternDetector, AgentState, DetectedPattern
2. Core Detectors (9): Context overflow, response loops, tool failures, etc.
3. Context & Token Management (5): Premature termination, excessive reading, etc.
4. Research & Decision Making (4): Premature escalation, theory-free debugging, etc.
5. Task Completion (4): Premature completion, untested completion, etc.
6. File Operations (3): File truncation, sequential reading, syntax corruption
7. Service & Process Management (2): No service restart, port conflicts
8. API & Network Issues (2): Timeout cascade, auth token expiry
9. Code Generation (3): Circular imports, merge conflicts, version conflicts
10. Output Quality (2): Hallucinated references, hardcoded credentials
11. Agent Coordination (2): Task ambiguity, over-delegation
12. Environment & Installation (3): Inefficient installation, venv unawareness
13. Progress Velocity (1): Velocity-based stagnation detection

Usage:
    from python.helpers.detectors import (
        AgentState,
        DetectedPattern,
        PatternDetector,
        PatternDetectorRegistry,
        create_default_detector_registry,
        create_extended_detector_registry,
    )
"""

import logging
from typing import Any, Dict, List, Optional

# Base classes and types
from .base import (
    AgentState,
    DetectedPattern,
    PatternDetector,
    RE_TIMESTAMP,
    RE_UUID,
    RE_WHITESPACE,
    RE_DIGITS,
)

# Core detectors
from .core import (
    ContextWindowOverflowDetector,
    ResponseLoopDetector,
    ToolFailureLoopDetector,
    ProgressStallDetector,
    RateLimitDetector,
    InfiniteRecursionDetector,
    OutputDegradationDetector,
    StuckApproachDetector,
    RepetitiveActionDetector,
    MisroutedToolDetector,
    VerdictPatternDetector,  # Issue #1093: Verdict-pattern loop detection
)

# Tool Frequency detectors (Memory Bank Loop Fix)
from .tool_frequency import (
    ToolFrequencyDetector,
)

# Context & Token Management detectors
from .context import (
    PrematureTerminationDetector,
    ExcessiveContextReadingDetector,
    MemoryBankObsessionDetector,
    VerboseResponseDetector,
    TokenAnxietyDetector,
)

# Research & Decision Making detectors
from .research import (
    PrematureEscalationDetector,
    SingleAttemptAbandonmentDetector,
    TheoryFreeDebuggingDetector,
    AnalysisParalysisDetector,
)

# Task Completion detectors
from .task_completion import (
    PrematureCompletionDetector,
    UntestedCompletionDetector,
    EvidenceFreeSuccessDetector,
    AsyncAbandonmentDetector,
)

# File Operations detectors
from .file_ops import (
    FileWriteTruncationDetector,
    SequentialFileReadingDetector,
    SyntaxCorruptionDetector,
)

# Service & Process Management detectors
from .service import (
    NoServiceRestartDetector,
    PortConflictDetector,
)

# API & Network detectors
from .api import (
    TimeoutCascadeDetector,
    AuthTokenExpiryDetector,
)

# Code Generation detectors
from .code_gen import (
    CircularImportDetector,
    MergeConflictDetector,
    VersionConflictDetector,
)

# Output Quality detectors
from .output import (
    HallucinatedReferencesDetector,
    HardcodedCredentialsDetector,
)

# Agent Coordination detectors
from .coordination import (
    TaskAmbiguityDetector,
    OverDelegationDetector,
)

# Environment & Installation detectors
from .environment import (
    InefficientInstallationDetector,
    RedundantDependencyInstallDetector,
    VenvUnawarenessDetector,
)

# Progress Velocity detectors
from .velocity import (
    ProgressVelocityDetector,
)

# Delegation Retest Loop detector (Iter73 fix)
from .delegation_retest import (
    DelegationRetestDetector,
)

# Response Dedup detector (Launchpad Iter1 — Error Class 7)
from .response_dedup import (
    ResponseDedupDetector,
)

# Re-export PatternType from loop_prevention for convenience
from python.helpers.loop_prevention import PatternType

# Layer classification facades (dual-layer supervisor architecture)
from .deterministic import (
    DETERMINISTIC_DETECTORS,
    create_deterministic_detectors,
)
from .intelligence import (
    INTELLIGENCE_DETECTORS,
    create_intelligence_detectors,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Pattern Detector Registry
# =============================================================================

class PatternDetectorRegistry:
    """
    Registry for managing and running pattern detectors.
    
    Provides centralized detection across all registered detectors.
    """
    
    def __init__(self):
        self._detectors: List[PatternDetector] = []
        self._enabled: Dict[PatternType, bool] = {}
    
    def register(self, detector: PatternDetector) -> None:
        """Register a pattern detector."""
        self._detectors.append(detector)
        self._enabled[detector.pattern_type] = True
        logger.info(f"Registered pattern detector: {detector.pattern_type.value}")
    
    def enable(self, pattern_type: PatternType) -> None:
        """Enable a specific pattern type."""
        self._enabled[pattern_type] = True
    
    def disable(self, pattern_type: PatternType) -> None:
        """Disable a specific pattern type."""
        self._enabled[pattern_type] = False
    
    async def detect_all(self, state: AgentState, essential_only: bool = False) -> List[DetectedPattern]:
        """
        Run all enabled detectors and return detected patterns.
        
        Args:
            state: Current agent state
            essential_only: If True, only run essential (non-deep) detectors
            
        Returns:
            List of detected patterns, sorted by severity
        """
        patterns = []
        
        for detector in self._detectors:
            if not self._enabled.get(detector.pattern_type, True):
                continue
            
            # Skip deep detectors if essential_only is requested
            if essential_only and detector.is_deep:
                continue
            
            try:
                pattern = await detector.detect(state)
                if pattern:
                    patterns.append(pattern)
            except Exception as e:
                logger.error(f"Error in detector {detector.pattern_type.value}: {e}")
        
        # Sort by severity (critical > high > medium > low)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        patterns.sort(key=lambda p: severity_order.get(p.severity, 4))
        
        return patterns
    
    async def detect_first(self, state: AgentState) -> Optional[DetectedPattern]:
        """
        Run detectors and return the first (highest severity) pattern found.
        """
        patterns = await self.detect_all(state)
        return patterns[0] if patterns else None
    
    def get_detector(self, pattern_type: PatternType) -> Optional[PatternDetector]:
        """Get a specific detector by pattern type."""
        for detector in self._detectors:
            if detector.pattern_type == pattern_type:
                return detector
        return None


# =============================================================================
# Factory Functions
# =============================================================================

def create_default_detector_registry() -> PatternDetectorRegistry:
    """Create a registry with all 9 base detectors."""
    registry = PatternDetectorRegistry()
    
    # Register base/core detectors
    registry.register(ContextWindowOverflowDetector())
    registry.register(ResponseLoopDetector())
    registry.register(ToolFailureLoopDetector())
    registry.register(ProgressStallDetector())
    registry.register(RateLimitDetector())
    registry.register(InfiniteRecursionDetector())
    registry.register(OutputDegradationDetector())
    registry.register(RepetitiveActionDetector())
    # Issue #218: Detects stuck approach patterns (same failing method repeated)
    registry.register(StuckApproachDetector())
    # Issue #791: Detects agent using terminal grep on MCP-data instead of MCP tools
    registry.register(MisroutedToolDetector())
    # Memory Bank Loop Fix: Detects same tool called too frequently regardless of args
    registry.register(ToolFrequencyDetector())
    # Issue #1093: Detects repeated verdict patterns (QUALITY: FAIL loops)
    registry.register(VerdictPatternDetector())
    # Iter73: Detects delegation retest loops (E2E→E2E without code-fix)
    registry.register(DelegationRetestDetector())
    # Launchpad Iter1: Detects repeated identical response tool outputs (false DONE)
    registry.register(ResponseDedupDetector())
    
    return registry


def create_detector_registry(
    config: Optional[Dict[str, Any]] = None
) -> PatternDetectorRegistry:
    """Create a detector registry with optional configuration."""
    registry = PatternDetectorRegistry()
    
    config = config or {}
    
    # Context overflow detector
    ctx_config = config.get("context_overflow", {})
    registry.register(ContextWindowOverflowDetector(
        warning_threshold=ctx_config.get("warning_threshold", 0.70),
        high_threshold=ctx_config.get("high_threshold", 0.85),
        critical_threshold=ctx_config.get("critical_threshold", 0.95),
    ))
    
    # Response loop detector
    loop_config = config.get("response_loop", {})
    registry.register(ResponseLoopDetector(
        similarity_threshold=loop_config.get("similarity_threshold", 0.85),
        min_responses_for_detection=loop_config.get("min_responses", 3),
        lookback_count=loop_config.get("lookback_count", 5),
    ))
    
    # Tool failure detector
    tool_config = config.get("tool_failure", {})
    registry.register(ToolFailureLoopDetector(
        failure_threshold=tool_config.get("failure_threshold", 3),
        lookback_count=tool_config.get("lookback_count", 10),
    ))
    
    # Progress stall detector
    stall_config = config.get("progress_stall", {})
    registry.register(ProgressStallDetector(
        max_iterations_without_progress=stall_config.get("max_iterations", 10),
        stall_time_seconds=stall_config.get("stall_time_seconds", 300.0),
    ))
    
    # Rate limit detector
    rate_config = config.get("rate_limit", {})
    registry.register(RateLimitDetector(
        rate_limit_threshold=rate_config.get("threshold", 3),
        lookback_count=rate_config.get("lookback_count", 10),
    ))
    
    # Infinite recursion detector
    recursion_config = config.get("infinite_recursion", {})
    registry.register(InfiniteRecursionDetector(
        max_depth=recursion_config.get("max_depth", 5),
        max_subordinates=recursion_config.get("max_subordinates", 10),
    ))
    
    # Output degradation detector
    degradation_config = config.get("output_degradation", {})
    registry.register(OutputDegradationDetector(
        min_response_length=degradation_config.get("min_response_length", 50),
        degradation_threshold=degradation_config.get("degradation_threshold", 0.5),
    ))
    
    # Repetitive action detector
    repetitive_config = config.get("repetitive_action", {})
    registry.register(RepetitiveActionDetector(
        min_repeats=repetitive_config.get("min_repeats", 3),
        lookback_count=repetitive_config.get("lookback_count", 10),
    ))
    
    return registry


def create_extended_detector_registry() -> PatternDetectorRegistry:
    """Create a registry with all 35 detectors (9 core + 26 extended)."""
    # Start with base detectors
    registry = create_default_detector_registry()
    
    # Add extended detectors - Context & Token Management
    registry.register(PrematureTerminationDetector())
    registry.register(ExcessiveContextReadingDetector())
    registry.register(MemoryBankObsessionDetector())
    registry.register(VerboseResponseDetector())
    registry.register(TokenAnxietyDetector())
    
    # Research & Decision Making
    registry.register(PrematureEscalationDetector())
    registry.register(SingleAttemptAbandonmentDetector())
    registry.register(TheoryFreeDebuggingDetector())
    registry.register(AnalysisParalysisDetector())
    
    # Task Completion
    registry.register(PrematureCompletionDetector())
    registry.register(UntestedCompletionDetector())
    registry.register(EvidenceFreeSuccessDetector())
    registry.register(AsyncAbandonmentDetector())
    
    # File Operations
    registry.register(FileWriteTruncationDetector())
    registry.register(SequentialFileReadingDetector())
    registry.register(SyntaxCorruptionDetector())
    
    # Service & Process Management
    registry.register(NoServiceRestartDetector())
    registry.register(PortConflictDetector())
    
    # API & Network
    registry.register(TimeoutCascadeDetector())
    registry.register(AuthTokenExpiryDetector())
    
    # Code Generation
    registry.register(CircularImportDetector())
    registry.register(MergeConflictDetector())
    registry.register(VersionConflictDetector())
    
    # Output Quality
    registry.register(HallucinatedReferencesDetector())
    registry.register(HardcodedCredentialsDetector())
    
    # Agent Coordination
    registry.register(TaskAmbiguityDetector())
    registry.register(OverDelegationDetector())
    
    # Environment & Installation
    registry.register(InefficientInstallationDetector())
    registry.register(RedundantDependencyInstallDetector())
    registry.register(VenvUnawarenessDetector())
    
    # Supervisor Reliability (Gap 1)
    registry.register(ProgressVelocityDetector())
    
    return registry


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    # Base classes and types
    "AgentState",
    "DetectedPattern",
    "PatternDetector",
    "PatternType",
    "RE_TIMESTAMP",
    "RE_UUID",
    "RE_WHITESPACE",
    "RE_DIGITS",
    
    # Registry
    "PatternDetectorRegistry",
    
    # Factory functions
    "create_default_detector_registry",
    "create_detector_registry",
    "create_extended_detector_registry",
    
    # Layer classification facades
    "DETERMINISTIC_DETECTORS",
    "create_deterministic_detectors",
    "INTELLIGENCE_DETECTORS",
    "create_intelligence_detectors",
    
    # Core detectors
    "ContextWindowOverflowDetector",
    "ResponseLoopDetector",
    "ToolFailureLoopDetector",
    "ProgressStallDetector",
    "RateLimitDetector",
    "InfiniteRecursionDetector",
    "OutputDegradationDetector",
    "StuckApproachDetector",
    "RepetitiveActionDetector",
    
    # Context & Token Management detectors
    "PrematureTerminationDetector",
    "ExcessiveContextReadingDetector",
    "MemoryBankObsessionDetector",
    "VerboseResponseDetector",
    "TokenAnxietyDetector",
    
    # Research & Decision Making detectors
    "PrematureEscalationDetector",
    "SingleAttemptAbandonmentDetector",
    "TheoryFreeDebuggingDetector",
    "AnalysisParalysisDetector",
    
    # Task Completion detectors
    "PrematureCompletionDetector",
    "UntestedCompletionDetector",
    "EvidenceFreeSuccessDetector",
    "AsyncAbandonmentDetector",
    
    # File Operations detectors
    "FileWriteTruncationDetector",
    "SequentialFileReadingDetector",
    "SyntaxCorruptionDetector",
    
    # Service & Process Management detectors
    "NoServiceRestartDetector",
    "PortConflictDetector",
    
    # API & Network detectors
    "TimeoutCascadeDetector",
    "AuthTokenExpiryDetector",
    
    # Code Generation detectors
    "CircularImportDetector",
    "MergeConflictDetector",
    "VersionConflictDetector",
    
    # Output Quality detectors
    "HallucinatedReferencesDetector",
    "HardcodedCredentialsDetector",
    
    # Agent Coordination detectors
    "TaskAmbiguityDetector",
    "OverDelegationDetector",
    
    # Environment & Installation detectors
    "InefficientInstallationDetector",
    "RedundantDependencyInstallDetector",
    "VenvUnawarenessDetector",
    
    # Progress Velocity detectors
    "ProgressVelocityDetector",
    
    # MCP Tool Misrouting detectors (Issue #791)
    "MisroutedToolDetector",
    
    # Tool Frequency detectors (Memory Bank Loop Fix)
    "ToolFrequencyDetector",

    # Verdict Pattern detectors (Issue #1093)
    "VerdictPatternDetector",

    # Delegation Retest Loop detector (Iter73 fix)
    "DelegationRetestDetector",

    # Response Dedup detector (Launchpad Iter1 — Error Class 7)
    "ResponseDedupDetector",
]