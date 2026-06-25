"""
Deterministic Detectors — Layer 1 (Structural Guards)

These detectors are lightweight, zero-LLM-call pattern matchers that run
on EVERY turn with negligible overhead. They detect structural problems
(loops, failures, repetition) using counters, hashes, and thresholds.

When a deterministic detector fires, it escalates to Layer 2 (intelligent
supervisor) via the _l2_escalation_signals agent data key.

Classification criteria:
  ✅ No LLM calls
  ✅ O(1) or O(n) where n = recent_tool_calls (small)
  ✅ Deterministic: same input → same output
  ✅ Can be evaluated from counters, hashes, or short lookback windows
"""

from typing import Dict, List, Optional, Type

from python.helpers.detectors.base import PatternDetector

# ─── Deterministic detector classes ──────────────────────────────────────
# These are safe to run every turn — zero LLM cost.

from python.helpers.detectors.core import (
    ContextWindowOverflowDetector,   # Token counter threshold
    ResponseLoopDetector,            # Similarity ratio on short lookback
    ToolFailureLoopDetector,         # Consecutive failure counter
    ProgressStallDetector,           # Timer + iteration counter
    RateLimitDetector,               # Rate-limit error counter
    InfiniteRecursionDetector,       # Depth counter
    OutputDegradationDetector,       # Response length ratio
    RepetitiveActionDetector,        # Tool call fingerprint dedup
    MisroutedToolDetector,           # Regex pattern match
    VerdictPatternDetector,          # Repeated verdict string match
)

from python.helpers.detectors.tool_frequency import (
    ToolFrequencyDetector,           # Tool call frequency counter
)

from python.helpers.detectors.service import (
    NoServiceRestartDetector,        # Config change without restart
    PortConflictDetector,            # Port conflict in error messages
)

from python.helpers.detectors.api import (
    TimeoutCascadeDetector,          # Timeout error counter
    AuthTokenExpiryDetector,         # Auth error pattern match
)

from python.helpers.detectors.code_gen import (
    CircularImportDetector,          # Import error pattern match
    MergeConflictDetector,           # Merge conflict markers
    VersionConflictDetector,         # Version mismatch pattern
)

from python.helpers.detectors.file_ops import (
    FileWriteTruncationDetector,     # File size decrease detection
    SequentialFileReadingDetector,    # Sequential read pattern
    SyntaxCorruptionDetector,        # Syntax error after write
)

from python.helpers.detectors.output import (
    HardcodedCredentialsDetector,    # Regex for credential patterns
)

from python.helpers.detectors.environment import (
    RedundantDependencyInstallDetector,  # Repeated pip install counter
    VenvUnawarenessDetector,             # Venv error pattern match
)

from python.helpers.detectors.delegation_retest import (
    DelegationRetestDetector,            # Consecutive test delegation without code-fix
)

from python.helpers.detectors.response_dedup import (
    ResponseDedupDetector,               # MD5 hash dedup on response tool outputs
)


# All deterministic detector classes
DETERMINISTIC_DETECTORS: List[Type[PatternDetector]] = [
    # Core loop/stall detectors
    ContextWindowOverflowDetector,
    ResponseLoopDetector,
    ToolFailureLoopDetector,
    ProgressStallDetector,
    RateLimitDetector,
    InfiniteRecursionDetector,
    OutputDegradationDetector,
    RepetitiveActionDetector,
    MisroutedToolDetector,
    VerdictPatternDetector,
    ToolFrequencyDetector,
    # Infrastructure detectors
    NoServiceRestartDetector,
    PortConflictDetector,
    TimeoutCascadeDetector,
    AuthTokenExpiryDetector,
    CircularImportDetector,
    MergeConflictDetector,
    VersionConflictDetector,
    # File safety detectors
    FileWriteTruncationDetector,
    SequentialFileReadingDetector,
    SyntaxCorruptionDetector,
    # Security detectors
    HardcodedCredentialsDetector,
    # Environment detectors
    RedundantDependencyInstallDetector,
    VenvUnawarenessDetector,
    # Delegation pattern detectors (Iter73 fix)
    DelegationRetestDetector,
    # Response dedup detector (Launchpad Iter1 — Error Class 7)
    ResponseDedupDetector,
]


def create_deterministic_detectors() -> List[PatternDetector]:
    """Instantiate all deterministic detectors.
    
    These are safe for Layer 1 — zero LLM calls, fast, deterministic.
    """
    return [cls() for cls in DETERMINISTIC_DETECTORS]
