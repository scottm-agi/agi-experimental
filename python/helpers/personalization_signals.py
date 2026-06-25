"""
Personalization Signal Collector — Implicit and explicit signal extraction.

Collects behavioral signals from user messages to update personality profiles.
Signals are classified by type (implicit/explicit), weighted, and tagged with
detected personality dimensions.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Pattern Definitions — Keyword patterns that indicate personality dimensions
# ═══════════════════════════════════════════════════════════════════

DIMENSION_PATTERNS: dict[str, list[dict[str, Any]]] = {
    "conscientiousness": {
        "increase": [
            r"\btdd\b", r"\btest[- ]driven\b", r"\bunit test", r"\bregression",
            r"\bmodular", r"\brefactor", r"\bcode review", r"\blint",
            r"\bverif", r"\bvalidat", r"\bmake sure", r"\bdouble[- ]check",
            r"\bcommit\b", r"\bgit\b", r"\bpush\b", r"\bbranch\b",
            r"\bdocumentation\b", r"\bstructure\b", r"\borgatiz", r"\bstandard\b",
            r"\bfix\b", r"\bfinish\b", r"\bcomplete\b", r"\bdone\b",
        ],
        "decrease": [
            r"\bjust do it\b", r"\bskip test", r"\bdon'?t bother\b",
            r"\bhack\b", r"\bquick fix\b", r"\bmessy\b", r"\bignore\b",
        ]
    },
    "need_for_cognition": {
        "increase": [
            r"\bdeeply analyz", r"\bcomprehensiv", r"\bresearch",
            r"\bexplain why\b", r"\bhow does .+ work", r"\barchitect",
            r"\bdesign\b", r"\bstrateg", r"\blong[- ]term",
            r"\bmathematic", r"\brigorous", r"\btheor",
            r"\bwhy\b", r"\bhow\b", r"\bunderstand\b", r"\bcomplex\b",
            r"\bdetails\b", r"\bspecifics\b", r"\blogic\b",
        ],
        "decrease": [
            r"\bjust tell me\b", r"\bquick answer", r"\btl;?dr",
            r"\bdon'?t explain\b", r"\bsimple\b", r"\bbrief\b",
        ]
    },
    "openness": {
        "increase": [
            r"\bnew feature\b", r"\bnew approach\b", r"\bexplore\b",
            r"\binnovati", r"\bexperiment", r"\bcreativ",
            r"\bwhat if\b", r"\bnovel\b", r"\barchitect",
            r"\bmise\b", r"\bproject.*structure\b",
            r"\bdifferent\b", r"\balternative\b", r"\btry\b",
            r"\bidea\b", r"\bpossibil",
        ],
        "decrease": [
            r"\bkeep it simple\b", r"\bstick with\b", r"\bdon'?t change\b",
            r"\bas.?is\b", r"\bstandard\b", r"\bold\b", r"\blegacy\b",
        ]
    },
    "agreeableness": {
        "increase": [
            r"\bplease\b", r"\bthanks\b", r"\bgood job\b", r"\bnice\b",
            r"\bhelp\b", r"\bcollaborate\b", r"\bagree\b", r"\bperfect\b",
            r"\bawesome\b", r"\bgreat\b",
        ],
        "decrease": [
            r"\bsucks\b", r"\bterrible\b", r"\bbad\b", r"\bwrong\b",
            r"\bidiot\b", r"\bstupid\b", r"\bno\b", r"\bstop\b",
            r"\bfail\b", r"\berror\b", r"\bnot working\b",
        ]
    },
    "autonomy": {
        "increase": [
            r"\bautonomous", r"\bdo this yourself\b", r"\bhandle it\b",
            r"\bthen get started\b", r"\bproceed\b", r"\bgo ahead\b",
            r"\bjust do\b", r"\btake the lead\b",
        ],
        "decrease": [
            r"\bwait for me\b", r"\bdon'?t start\b", r"\blet me review\b",
            r"\bstop\b", r"\bpause\b", r"\bask me\b",
        ]
    },
    "risk_tolerance": {
        "increase": [
            r"\bdeploy\b", r"\bpush to prod", r"\bship it\b",
            r"\blaunch\b", r"\btry it\b", r"\brisk\b", r"\bchance\b",
            r"\bfast\b", r"\bspeed\b",
        ],
        "decrease": [
            r"\bcareful\b", r"\bsafe\b", r"\brollback\b",
            r"\bstaging\b", r"\btest.*first\b", r"\bwarning\b",
            r"\bdanger\b", r"\bprotect\b",
        ]
    },
    "time_orientation": {
        "increase": [
            r"\blong[- ]term\b", r"\bfuture\b", r"\bstrateg",
            r"\bplan\b", r"\broadmap\b", r"\bvision\b",
            r"\bnext steps\b", r"\bdirection\b", r"\bsustainable\b",
        ],
        "decrease": [
            r"\bfix now\b", r"\burgent\b", r"\basap\b",
            r"\bimmediately\b", r"\bhot ?fix\b", r"\btoday\b",
        ]
    },
}

# Flatten increase/decrease properly
COMPILED_PATTERNS: dict[str, dict[str, list[re.Pattern]]] = {}
for dim, directions in DIMENSION_PATTERNS.items():
    COMPILED_PATTERNS[dim] = {}
    for direction in ["increase", "decrease"]:
        patterns = directions.get(direction, []) if isinstance(directions, dict) else []
        COMPILED_PATTERNS[dim][direction] = [
            re.compile(p, re.IGNORECASE) for p in patterns
        ]

# Default weights
IMPLICIT_WEIGHT = 1.0
EXPLICIT_WEIGHT = 3.0

# Decay schedule (days → weight multiplier)
DECAY_SCHEDULE = [
    (7, 1.0),
    (30, 0.8),
    (60, 0.5),
    (90, 0.3),
    (float("inf"), 0.1),
]


# ═══════════════════════════════════════════════════════════════════
#  SignalCollector
# ═══════════════════════════════════════════════════════════════════

class SignalCollector:
    """
    Collects implicit (behavioral) and explicit (stated) signals from user
    messages and stores them for personality analysis.
    """

    def __init__(self, user_id: str, data_dir: Optional[str] = None):
        self.user_id = user_id
        self.data_dir = data_dir
        self._signal_buffer: list[dict] = []

    def collect_implicit_signal(
        self,
        message: str,
        context: str,
    ) -> dict[str, Any]:
        """
        Analyze a user message for implicit personality signals.

        Returns a signal dict with detected dimensions and direction.
        """
        detected_dimensions = self._detect_dimensions(message)

        signal = {
            "type": "implicit",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": message,
            "context": context,
            "weight": IMPLICIT_WEIGHT,
            "detected_dimensions": detected_dimensions,
        }

        self._signal_buffer.append(signal)
        self._persist_signal(signal)
        return signal

    def collect_explicit_signal(
        self,
        preference: str,
        value: str,
        source: str = "user_stated",
    ) -> dict[str, Any]:
        """
        Record an explicit user preference (e.g., "I prefer concise responses").

        Explicit signals have higher weight than implicit ones.
        """
        signal = {
            "type": "explicit",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": f"{preference}: {value}",
            "context": source,
            "weight": EXPLICIT_WEIGHT,
            "preference": preference,
            "value": value,
            "detected_dimensions": [],
        }

        self._signal_buffer.append(signal)
        self._persist_signal(signal)
        return signal

    def get_signal_history(self) -> list[dict]:
        """Load all persisted signals for this user."""
        if not self.data_dir:
            return list(self._signal_buffer)

        signals_path = os.path.join(self.data_dir, self.user_id, "signals.jsonl")
        if not os.path.exists(signals_path):
            return []

        signals = []
        with open(signals_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    signals.append(json.loads(line))
        return signals

    def calculate_decayed_weight(self, signal: dict) -> float:
        """
        Calculate the effective weight of a signal based on its age.

        Uses the decay schedule: recent signals have full weight,
        older signals decay towards zero.
        """
        ts_str = signal.get("timestamp", "")
        try:
            signal_time = datetime.fromisoformat(ts_str)
            # RC-14: If signal_time is naive (no timezone), assume UTC.
            # Older JSONL signals may have been stored without TZ info.
            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return signal.get("weight", IMPLICIT_WEIGHT)

        age_days = (datetime.now(timezone.utc) - signal_time).days
        base_weight = signal.get("weight", IMPLICIT_WEIGHT)

        for threshold_days, multiplier in DECAY_SCHEDULE:
            if age_days <= threshold_days:
                return base_weight * multiplier

        return base_weight * 0.1  # Fallback

    def _detect_dimensions(self, message: str) -> list[dict[str, str]]:
        """Detect personality dimensions signaled by the message."""
        detected = []
        for dim, directions in COMPILED_PATTERNS.items():
            for direction, patterns in directions.items():
                for pattern in patterns:
                    if pattern.search(message):
                        detected.append({
                            "dimension": dim,
                            "direction": direction,
                            "pattern": pattern.pattern,
                        })
                        break  # One match per dimension+direction is enough
        return detected

    def _persist_signal(self, signal: dict) -> None:
        """Append signal to JSONL file if data_dir is set."""
        if not self.data_dir:
            return

        user_dir = os.path.join(self.data_dir, self.user_id)
        os.makedirs(user_dir, exist_ok=True)
        signals_path = os.path.join(user_dir, "signals.jsonl")

        with open(signals_path, "a") as f:
            f.write(json.dumps(signal) + "\n")
