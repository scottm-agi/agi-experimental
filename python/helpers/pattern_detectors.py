"""
Unified Pattern Detection System for AGIX Supervisor

This module provides comprehensive pattern detection capabilities to identify when agents
are stuck, looping, or experiencing issues that require intervention.

Contains all 35 pattern detectors covering the 100 human intervention patterns
organized into 12 categories:

1. Context & Token Management (CTX-001 to CTX-010)
2. Research & Decision Making (RES-001 to RES-010)
3. Task Completion (TSK-001 to TSK-010)
4. File Operations (FILE-001 to FILE-010)
5. Service & Process Management (SVC-001 to SVC-010)
6. API & Network Issues (API-001 to API-010)
7. Code Generation & Modification (CODE-001 to CODE-010)
8. State Management (STATE-001 to STATE-010)
9. Output Quality (OUT-001 to OUT-010)
10. Agent Coordination (COORD-001 to COORD-010)
11. Environment & Installation (ENV-001 to ENV-010)
12. Progress Velocity (Gap 1: Supervisor Reliability)

MODULARIZED ARCHITECTURE:
This file is now a facade that re-exports all components from the
python.helpers.detectors package. The actual implementations are in:

- detectors/base.py: AgentState, DetectedPattern, PatternDetector ABC
- detectors/core.py: 9 core detectors (overflow, loops, failures, etc.)
- detectors/context.py: 5 context/token management detectors
- detectors/research.py: 4 research/decision making detectors
- detectors/task_completion.py: 4 task completion detectors
- detectors/file_ops.py: 3 file operations detectors
- detectors/service.py: 2 service/process management detectors
- detectors/api.py: 2 API/network detectors
- detectors/code_gen.py: 3 code generation detectors
- detectors/output.py: 2 output quality detectors
- detectors/coordination.py: 2 agent coordination detectors
- detectors/environment.py: 3 environment/installation detectors
- detectors/velocity.py: 1 progress velocity detector
- detectors/__init__.py: Registry, factory functions, re-exports

Usage:
    # All imports work the same as before (backwards compatible):
    from python.helpers.pattern_detectors import (
        AgentState,
        DetectedPattern,
        PatternDetector,
        PatternDetectorRegistry,
        create_default_detector_registry,
        create_extended_detector_registry,
        ContextWindowOverflowDetector,
        ResponseLoopDetector,
        # ... etc
    )
    
    # Or import from the new modular package:
    from python.helpers.detectors import (
        AgentState,
        DetectedPattern,
        create_extended_detector_registry,
    )
"""
from __future__ import annotations

# =============================================================================
# Re-export everything from the modular detectors package
# =============================================================================

# Base classes and types
from python.helpers.detectors import (
    AgentState,
    DetectedPattern,
    PatternDetector,
    PatternType,
    RE_TIMESTAMP,
    RE_UUID,
    RE_WHITESPACE,
    RE_DIGITS,
)

# Registry and factory functions
from python.helpers.detectors import (
    PatternDetectorRegistry,
    create_default_detector_registry,
    create_detector_registry,
    create_extended_detector_registry,
)

# Core detectors
from python.helpers.detectors import (
    ContextWindowOverflowDetector,
    ResponseLoopDetector,
    ToolFailureLoopDetector,
    ProgressStallDetector,
    RateLimitDetector,
    InfiniteRecursionDetector,
    OutputDegradationDetector,
    StuckApproachDetector,
    RepetitiveActionDetector,
)

# Context & Token Management detectors
from python.helpers.detectors import (
    PrematureTerminationDetector,
    ExcessiveContextReadingDetector,
    MemoryBankObsessionDetector,
    VerboseResponseDetector,
    TokenAnxietyDetector,
)

# Research & Decision Making detectors
from python.helpers.detectors import (
    PrematureEscalationDetector,
    SingleAttemptAbandonmentDetector,
    TheoryFreeDebuggingDetector,
    AnalysisParalysisDetector,
)

# Task Completion detectors
from python.helpers.detectors import (
    PrematureCompletionDetector,
    UntestedCompletionDetector,
    EvidenceFreeSuccessDetector,
    AsyncAbandonmentDetector,
)

# File Operations detectors
from python.helpers.detectors import (
    FileWriteTruncationDetector,
    SequentialFileReadingDetector,
    SyntaxCorruptionDetector,
)

# Service & Process Management detectors  
from python.helpers.detectors import (
    NoServiceRestartDetector,
    PortConflictDetector,
)

# API & Network detectors
from python.helpers.detectors import (
    TimeoutCascadeDetector,
    AuthTokenExpiryDetector,
)

# Code Generation detectors
from python.helpers.detectors import (
    CircularImportDetector,
    MergeConflictDetector,
    VersionConflictDetector,
)

# Output Quality detectors
from python.helpers.detectors import (
    HallucinatedReferencesDetector,
    HardcodedCredentialsDetector,
)

# Agent Coordination detectors
from python.helpers.detectors import (
    TaskAmbiguityDetector,
    OverDelegationDetector,
)

# Environment & Installation detectors
from python.helpers.detectors import (
    InefficientInstallationDetector,
    RedundantDependencyInstallDetector,
    VenvUnawarenessDetector,
)

# Progress Velocity detectors
from python.helpers.detectors import (
    ProgressVelocityDetector,
)


# =============================================================================
# Public API (for backwards compatibility)
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
]