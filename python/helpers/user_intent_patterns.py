"""
User Intent Patterns — 2-Layer Detection Architecture
======================================================

Layer 1: Fast regex signals with confidence scores (deterministic, cheap).
Layer 2: LLM makes the final decision using L1 signals as context.

This is the ONE AND ONLY location for stop/redirect pattern definitions.
Both context.py and _02_user_stop_directive.py MUST import from here.

Architecture (ITR-48 redesign):
  - Regex patterns NEVER auto-kill work. They produce weighted signals.
  - The LLM always has the final say on whether a user message is a stop
    directive vs. a feature description that happens to contain "stop/cancel".
  - High-confidence signals (>=0.9) mean the LLM will almost certainly agree
    (rubber-stamp). Low confidence (<0.5) means the LLM must actually think.
  - The extension (_02_user_stop_directive.py) injects L1 signals into the
    system prompt so the LLM can make an informed decision.

Design principles:
  1. One pattern set, not two divergent regex sets
  2. Regex = weighted signals, LLM = final decision
  3. Feature context reduces confidence, doesn't hard-block
  4. User messages ALWAYS take priority over gates
"""

from __future__ import annotations

import re
from typing import Optional


# ── STOP PATTERNS WITH WEIGHTS ────────────────────────────────────────
# Each pattern has a base confidence weight (0.0 - 1.0).
# Higher weight = more likely a genuine stop directive.
# Format: (compiled_regex, weight, name)

_WEIGHTED_STOP_PATTERNS = [
    # ── Explicit multi-word stop commands (HIGH confidence) ──
    (re.compile(r"\bstop\s+(?:all\s+)?(?:work|tasks?|agents?|delegations?|execution|operations?)\b", re.IGNORECASE),
     1.0, "stop_work_explicit"),
    (re.compile(r"\bhalt\s+(?:all\s+)?(?:work|tasks?|execution|operations?)\b", re.IGNORECASE),
     1.0, "halt_work_explicit"),
    (re.compile(r"\bcease\s+(?:all\s+)?(?:work|tasks?|execution|operations?)\b", re.IGNORECASE),
     1.0, "cease_work_explicit"),
    (re.compile(r"\babort\s+(?:all\s+)?(?:work|tasks?|execution|operations?|mission)\b", re.IGNORECASE),
     1.0, "abort_work_explicit"),
    (re.compile(r"\bcancel\s+(?:all\s+)?(?:work|tasks?|execution|operations?|everything)\b", re.IGNORECASE),
     1.0, "cancel_work_explicit"),

    # ── "stop everything/now/immediately" (HIGH confidence) ──
    (re.compile(r"\bstop\s+(?:everything|immediately|now|right\s+now)\b", re.IGNORECASE),
     0.95, "stop_everything"),

    # ── "stop what you're doing" / "drop everything" (HIGH confidence) ──
    (re.compile(r"\bstop\s+what\s+you(?:'re|\s+are)\s+doing\b", re.IGNORECASE),
     0.95, "stop_what_doing"),
    (re.compile(r"\bdrop\s+everything\b", re.IGNORECASE),
     0.90, "drop_everything"),

    # ── "stop working/coding/building" (HIGH confidence) ──
    (re.compile(r"\bstop\s+(?:working|coding|building|developing|programming|running)\b", re.IGNORECASE),
     0.90, "stop_activity"),

    # ── "wrap it up and stop" (MEDIUM-HIGH confidence) ──
    (re.compile(r"\b(?:wrap|finish)\s+(?:it\s+)?up\b.*\bstop\b", re.IGNORECASE),
     0.80, "wrap_up_stop"),
    (re.compile(r"\bstop\b.*\b(?:wrap|finish)\s+(?:it\s+)?up\b", re.IGNORECASE),
     0.80, "stop_wrap_up"),

    # ── "that's enough" / standalone "enough" (MEDIUM confidence) ──
    (re.compile(r"\bthat(?:'s|\s+is)\s+enough\b", re.IGNORECASE),
     0.75, "thats_enough"),
    (re.compile(r"^\s*enough\s*[.!]?\s*$", re.IGNORECASE),
     0.80, "bare_enough"),

    # ── "just stop" / "please stop" / frustrated variants (HIGH confidence) ──
    (re.compile(r"\b(?:just|please|fucking?|ffs)\s+stop\b", re.IGNORECASE),
     0.90, "frustrated_stop"),
    (re.compile(r"\bstop\s*[,!.]\s*(?:please|now|immediately)?\s*$", re.IGNORECASE),
     0.85, "stop_terminal"),

    # ── Bare "stop" / "STOP" — only standalone messages (HIGH confidence) ──
    (re.compile(r"^\s*(?:fuck[,.]?\s*)?stop\s*[.!]?\s*$", re.IGNORECASE),
     0.95, "bare_stop"),

    # ── "stop/cancel/halt all <stop-context-noun>" (HIGH confidence) ──
    (re.compile(
        r"\b(?:stop|cancel|halt)\s+all\s+"
        r"(?:work|tasks?|agents?|delegations?|execution|operations?|activity|activities)"
        r"\b",
        re.IGNORECASE,
    ), 1.0, "stop_all_explicit"),

    # ── Bare "stop/cancel/halt all" without stop-context noun (LOW confidence) ──
    # This is the pattern that false-positived on "view/cancel all queued emails".
    # Low confidence = LLM decides whether this is a feature description or stop.
    (re.compile(r"\b(?:stop|cancel|halt)\s+all\b", re.IGNORECASE),
     0.3, "bare_stop_all"),
]

# ── FEATURE CONTEXT DETECTION ─────────────────────────────────────────
# If stop/cancel/halt appears adjacent to feature/object words, it's
# likely a feature request. This REDUCES confidence but doesn't hard-block.

_FEATURE_WORDS = (
    r"button|handler|spinner|animation|page|component|function|method|"
    r"endpoint|propagation|timer|interval|server|service|container|process|"
    r"watch|listener|polling|cron|event|order|orders|subscription|subscriptions|"
    r"email|emails|item|items|job|jobs|download|downloads|upload|uploads|"
    r"notification|notifications|alert|alerts|queue|queued|track|tracks|"
    r"recording|recordings|stream|streams|session|sessions|request|requests|"
    r"transfer|transfers|payment|payments|booking|bookings|reservation|reservations"
)
_STOP_VERBS = r"stop|cancel|halt"

_FEATURE_ADJACENT_PATTERN = re.compile(
    # stop/cancel/halt + (0-2 words) + feature noun
    rf"\b(?:{_STOP_VERBS})\s+(?:\w+\s+){{0,2}}(?:{_FEATURE_WORDS})\b"
    r"|"
    # feature noun + (0-2 words) + stop/cancel/halt
    rf"\b(?:{_FEATURE_WORDS})\s+(?:\w+\s+){{0,2}}(?:{_STOP_VERBS})\b"
    r"|"
    # build/create/add + ... + stop/cancel/halt (feature construction context)
    rf"\b(?:add|create|implement|build|design|view|show|display)\s+(?:\w+\s+){{0,3}}(?:{_STOP_VERBS})\b"
    r"|"
    # slash-separated verbs: view/cancel, play/stop, start/halt
    rf"\w/(?:{_STOP_VERBS})\s"
    r"|"
    r"\bevent\.stop\w+\b"  # event.stopPropagation, event.stopImmediatePropagation
    r"|"
    r"\b(?:graceful)\s+(?:stop|shutdown)\b",
    re.IGNORECASE
)

# Confidence penalty when feature context is detected
_FEATURE_CONTEXT_PENALTY = 0.6  # Multiplier (0.6 = 40% reduction)

# Confidence penalty for long messages (feature descriptions are long)
_LENGTH_PENALTY_THRESHOLD = 100  # chars
_LENGTH_PENALTY_FACTOR = 0.5  # Multiplier for messages > threshold


# ── REDIRECT PATTERNS ─────────────────────────────────────────────────
# These match user intent to change direction/requirements (not stop entirely).

_REDIRECT_PATTERNS = [
    re.compile(r"\b(?:actually|instead|rather)\b", re.IGNORECASE),
    re.compile(r"\bscratch\s+that\b", re.IGNORECASE),
    re.compile(r"\bchange\s+(?:the|this|that|it|your)\b", re.IGNORECASE),
    re.compile(r"\b(?:also|and)\s+(?:add|include|make)\b", re.IGNORECASE),
    re.compile(r"\bdifferent\s+(?:approach|way|method|strategy)\b", re.IGNORECASE),
    re.compile(r"\bnever\s*mind\b", re.IGNORECASE),
    re.compile(r"\bforget\s+(?:about\s+)?(?:that|it|this)\b", re.IGNORECASE),
    re.compile(r"\bdo\s+(?:it|this)\s+differently\b", re.IGNORECASE),
    re.compile(r"\bwait\b.*\binstead\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:that|what)\b.*\b(?:want|need|mean)\b", re.IGNORECASE),
]


# ══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════

def get_stop_signals(msg: Optional[str]) -> dict:
    """Layer 1: Produce weighted stop signals from regex patterns.

    Returns a dict with:
        confidence: float 0.0-1.0 (how likely this is a stop directive)
        matched_patterns: list of pattern names that matched
        has_feature_context: bool (stop word appears near feature words)
        recommendation: 'stop' | 'continue' | 'ask_llm'
        raw_message: the original message (for LLM context)

    The LLM uses this to make the final decision. Regex never auto-kills.
    """
    if not msg or not isinstance(msg, str):
        return {
            "confidence": 0.0,
            "matched_patterns": [],
            "has_feature_context": False,
            "recommendation": "continue",
            "raw_message": "",
        }

    text = msg.strip()
    if not text:
        return {
            "confidence": 0.0,
            "matched_patterns": [],
            "has_feature_context": False,
            "recommendation": "continue",
            "raw_message": "",
        }

    # Collect all matching patterns and their weights
    matched = []
    max_weight = 0.0
    for pattern, weight, name in _WEIGHTED_STOP_PATTERNS:
        if pattern.search(text):
            matched.append(name)
            max_weight = max(max_weight, weight)

    if not matched:
        return {
            "confidence": 0.0,
            "matched_patterns": [],
            "has_feature_context": False,
            "recommendation": "continue",
            "raw_message": text,
        }

    # Start with max weight as base confidence
    confidence = max_weight

    # Short message boost: very short messages (< 30 chars) with stop words
    # can't contain enough context to be feature descriptions.
    # "stop all" (8 chars) is almost certainly a stop command.
    if len(text) < 30 and "bare_stop_all" in matched:
        confidence = max(confidence, 0.85)

    # Apply feature context penalty
    has_feature_context = bool(_FEATURE_ADJACENT_PATTERN.search(text))
    if has_feature_context:
        confidence *= _FEATURE_CONTEXT_PENALTY

    # Apply length penalty (long messages = likely feature descriptions)
    if len(text) > _LENGTH_PENALTY_THRESHOLD:
        confidence *= _LENGTH_PENALTY_FACTOR

    # Clamp to [0.0, 1.0]
    confidence = max(0.0, min(1.0, confidence))

    # Determine recommendation for the LLM
    if confidence >= 0.8:
        recommendation = "stop"
    elif confidence >= 0.4:
        recommendation = "ask_llm"
    else:
        recommendation = "continue"

    return {
        "confidence": confidence,
        "matched_patterns": matched,
        "has_feature_context": has_feature_context,
        "recommendation": recommendation,
        "raw_message": text,
    }


def is_stop_directive(msg: Optional[str]) -> bool:
    """Backward-compatible wrapper — uses L1 signals with a high threshold.

    For the 2-layer architecture, callers should use get_stop_signals()
    and let the LLM decide. This function exists for backward compat
    and uses a HIGH confidence threshold (0.7) to minimize false positives.

    The _02_user_stop_directive extension should migrate to get_stop_signals()
    and inject the signals into the system prompt for LLM decision.
    """
    signals = get_stop_signals(msg)
    # Only auto-trigger on high confidence (0.7+)
    # Below that, the LLM should decide via the extension
    return signals["confidence"] >= 0.7


def is_user_redirection(msg: Optional[str]) -> bool:
    """Determine if a user message is a direction change / requirement update.

    When True, the system should:
    - Reset gate block counters (stale gates from old direction)
    - Allow the agent to pivot to the new direction
    - NOT stop work entirely (that's is_stop_directive)
    """
    if not msg or not isinstance(msg, str):
        return False

    text = msg.strip()
    if not text:
        return False

    return any(p.search(text) for p in _REDIRECT_PATTERNS)


def classify_user_intent(msg: Optional[str]) -> str:
    """Classify user message intent.

    Returns:
        'stop'      — user wants everything to halt (high confidence from L1)
        'redirect'  — user is changing direction/requirements
        'content'   — normal message (or low-confidence stop that needs LLM)
    """
    if is_stop_directive(msg):
        return "stop"
    if is_user_redirection(msg):
        return "redirect"
    return "content"


def format_signals_for_llm(signals: dict) -> str:
    """Format L1 signals into a system prompt injection for the LLM.

    The LLM reads this and makes the final decision on whether to stop.
    Used by _02_user_stop_directive.py when confidence is in the 'ask_llm' range.
    """
    if signals["confidence"] == 0.0:
        return ""

    lines = [
        "\n> [!NOTE]",
        "> **USER MESSAGE INTENT ANALYSIS** (regex signal — you make the final call)",
        ">",
        f"> Stop-intent confidence: **{signals['confidence']:.0%}**",
        f"> Matched patterns: {', '.join(signals['matched_patterns'])}",
        f"> Feature context detected: {'Yes' if signals['has_feature_context'] else 'No'}",
        f"> Regex recommendation: **{signals['recommendation'].upper()}**",
        ">",
    ]

    if signals["recommendation"] == "stop":
        lines.extend([
            "> The user's message appears to be a **stop directive**.",
            "> If you agree, call `response` with a summary of work done.",
            "> If this is actually a feature description, continue working.",
        ])
    elif signals["recommendation"] == "ask_llm":
        lines.extend([
            "> The user's message contains stop-like words but may be a **feature description**.",
            "> Read the full message carefully and decide:",
            "> - If the user wants YOU to stop working → call `response` with summary",
            "> - If the user is describing an app feature (e.g., 'cancel all orders') → continue working",
        ])

    return "\n".join(lines)
