"""
Intelligence-Requiring Detectors — Layer 2 (Intelligent Supervisor)

These detectors require SEMANTIC UNDERSTANDING of agent behavior to detect
problematic patterns. They analyze intent, quality, reasoning depth, and
strategic decisions — things that can't be reduced to counters or hashes.

These should ONLY run when Layer 1 escalates, to avoid wasting LLM tokens
on the 98% of turns that are normal.

Classification criteria:
  ⚠️  May need context about the agent's goal to evaluate
  ⚠️  Requires understanding of "good enough" vs "premature"
  ⚠️  Involves judgment calls (is this escalation premature? is this test sufficient?)
  ⚠️  May need to evaluate response QUALITY, not just STRUCTURE
"""

from typing import Dict, List, Optional, Type

from python.helpers.detectors.base import PatternDetector

# ─── Intelligence-requiring detector classes ─────────────────────────────
# These need semantic reasoning — too expensive for every turn.

from python.helpers.detectors.context import (
    PrematureTerminationDetector,        # "Is the agent quitting too early?"
    ExcessiveContextReadingDetector,     # "Is reading actually useful here?"
    MemoryBankObsessionDetector,         # "Is memory bank access productive?"
    VerboseResponseDetector,             # "Is verbosity helping or hurting?"
    TokenAnxietyDetector,                # "Is token concern appropriate?"
)

from python.helpers.detectors.research import (
    PrematureEscalationDetector,         # "Should the agent try harder first?"
    SingleAttemptAbandonmentDetector,    # "Has the agent really tried?"
    TheoryFreeDebuggingDetector,         # "Is the agent debugging systematically?"
    AnalysisParalysisDetector,           # "Is the agent overthinking?"
)

from python.helpers.detectors.task_completion import (
    PrematureCompletionDetector,         # "Is the task actually done?"
    UntestedCompletionDetector,          # "Were results verified?"
    EvidenceFreeSuccessDetector,         # "Is there proof of success?"
    AsyncAbandonmentDetector,            # "Was async work abandoned?"
)

from python.helpers.detectors.output import (
    HallucinatedReferencesDetector,      # "Are references real?"
)

from python.helpers.detectors.coordination import (
    TaskAmbiguityDetector,               # "Is the task clear enough?"
    OverDelegationDetector,              # "Is delegation appropriate?"
)

from python.helpers.detectors.environment import (
    InefficientInstallationDetector,     # "Is installation strategy optimal?"
)

from python.helpers.detectors.core import (
    StuckApproachDetector,               # "Should the agent try a different approach?"
)

from python.helpers.detectors.velocity import (
    ProgressVelocityDetector,            # "Is progress velocity acceptable?"
)


# All intelligence-requiring detector classes
INTELLIGENCE_DETECTORS: List[Type[PatternDetector]] = [
    # Context assessment detectors
    PrematureTerminationDetector,
    ExcessiveContextReadingDetector,
    MemoryBankObsessionDetector,
    VerboseResponseDetector,
    TokenAnxietyDetector,
    # Research quality detectors
    PrematureEscalationDetector,
    SingleAttemptAbandonmentDetector,
    TheoryFreeDebuggingDetector,
    AnalysisParalysisDetector,
    # Completion quality detectors
    PrematureCompletionDetector,
    UntestedCompletionDetector,
    EvidenceFreeSuccessDetector,
    AsyncAbandonmentDetector,
    # Output quality detectors
    HallucinatedReferencesDetector,
    # Coordination detectors
    TaskAmbiguityDetector,
    OverDelegationDetector,
    # Strategy detectors
    InefficientInstallationDetector,
    StuckApproachDetector,
    ProgressVelocityDetector,
]


def create_intelligence_detectors() -> List[PatternDetector]:
    """Instantiate all intelligence-requiring detectors.
    
    These are for Layer 2 ONLY — they require semantic understanding
    and should not run on every turn. Run them when Layer 1 escalates.
    """
    return [cls() for cls in INTELLIGENCE_DETECTORS]
