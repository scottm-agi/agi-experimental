"""
Depth Classifier — regex-weighted depth detection for user messages.

Ported from agix-saas-dec5-25 andy.py _get_intent_weights() pattern.
Classifies user messages into depth levels: brief, standard, comprehensive.
Used by _80_depth_instructions extension to inject depth-appropriate prompts.
"""
import re
from dataclasses import dataclass


@dataclass
class DepthResult:
    """Result of depth classification."""
    level: str       # 'brief', 'standard', 'comprehensive'
    score: float     # 0.0 (brief) to 1.0 (comprehensive)
    instructions: str  # Prompt injection text for agents


# ── Regex pattern groups with weights ────────────────────────────────────────
# Pattern: (compiled_regex, weight_delta)
# Positive weight = deeper output, negative = shallower

HIGH_DEPTH_PATTERNS = [
    (re.compile(r'\bcomprehensive\b', re.I), 0.4),
    (re.compile(r'\bdetailed\b', re.I), 0.35),
    (re.compile(r'\bworld[- ]class\b', re.I), 0.4),
    (re.compile(r'\bin[- ]depth\b', re.I), 0.35),
    (re.compile(r'\bthorough\b', re.I), 0.35),
    (re.compile(r'\bexhaustive\b', re.I), 0.4),
    (re.compile(r'\bdeep\s+dive\b', re.I), 0.35),
    (re.compile(r'\bextensive\b', re.I), 0.35),
    (re.compile(r'\bfull\b(?:\s+(?:account\s+)?plan|\s+report|\s+analysis|\s+details|\s+strategy)', re.I), 0.35),
    (re.compile(r'\bend[- ]to[- ]end\b', re.I), 0.3),
    (re.compile(r'\bholistic\b', re.I), 0.3),
    (re.compile(r'\bmaster\s+plan\b', re.I), 0.3),
    (re.compile(r'\bstrategic\s+plan\b', re.I), 0.25),
    (re.compile(r'\bexecutive[- ]ready\b', re.I), 0.3),
]

LOW_DEPTH_PATTERNS = [
    (re.compile(r'\bquick\b', re.I), -0.35),
    (re.compile(r'\bbrief\b', re.I), -0.35),
    (re.compile(r'\bshort\b', re.I), -0.3),
    (re.compile(r'\btl;?dr\b', re.I), -0.4),
    (re.compile(r'\bconcise\b', re.I), -0.35),
    (re.compile(r'\boverview\b', re.I), -0.15),
    (re.compile(r'\bsummary\b', re.I), -0.15),
    (re.compile(r'\bjust\s+(?:tell|give|show)\b', re.I), -0.2),
]

# Booster patterns that signal multi-section depth expectations
BOOSTER_PATTERNS = [
    (re.compile(r'\d\)\s', re.I), 0.05),           # each numbered item
    (re.compile(r'\beverything\b', re.I), 0.15),
    (re.compile(r'\ball\s+aspects\b', re.I), 0.15),
    (re.compile(r'\bincluding\b', re.I), 0.1),
    (re.compile(r'\ball\s+details\b', re.I), 0.15),
    (re.compile(r'\baccount\s+plan\b', re.I), 0.1),
    (re.compile(r'\bplaybook\b', re.I), 0.1),
    (re.compile(r'\bstrategy\b', re.I), 0.05),
]

# ── Prompt injection templates ───────────────────────────────────────────────

COMPREHENSIVE_INSTRUCTIONS = """[DEPTH: COMPREHENSIVE — User expects exhaustive, deeply detailed output]

You MUST produce an exhaustive, publication-ready deliverable. Follow these depth rules:

1. **MINIMUM 500 lines** per major section — each section is a full narrative, not a summary
2. **Full prose narratives** — write flowing paragraphs with analysis, not just bullet lists
3. **Every section must have 3+ sub-sections** — drill into every dimension with specifics
4. **Include specific data points** — real numbers, real company names, market sizing, revenue figures, pricing tiers
5. **Include frameworks** — MEDDIC, BANT, SWOT, Porter's Five Forces, Value Chain Analysis as applicable
6. **Include matrices and tables** — competitor comparison tables, feature matrices, RACI charts, timeline Gantt representations
7. **Never truncate, summarize away, or use placeholders** — expand EVERY point fully
8. **Include all sources** with full context paragraphs explaining why they matter
9. **Cross-reference insights** — connect research findings to strategy to tactics to outreach
10. **Write as an expert practitioner** — the reader should feel this was written by a world-class consultant

This deliverable will be reviewed by C-level executives. Shallow content is unacceptable."""

BRIEF_INSTRUCTIONS = """[DEPTH: BRIEF — User wants concise output]

Keep your response concise and focused:
- Maximum 50 lines total
- Key takeaways only — no lengthy explanations
- Use bullet points over prose
- Skip frameworks and matrices unless explicitly requested"""


def classify_depth(message: str) -> DepthResult:
    """
    Classify the expected output depth from a user message.

    Uses regex-weighted scoring ported from agix _get_intent_weights():
    - Each matching pattern adds/subtracts from a base score of 0.5
    - Score is clamped to [0.0, 1.0]
    - Level is derived from score thresholds

    Args:
        message: The user's message text

    Returns:
        DepthResult with level, score, and injection instructions
    """
    if not message or not message.strip():
        return DepthResult(level="standard", score=0.5, instructions="")

    score = 0.5  # Base score = standard
    message_lower = message.lower()

    # Apply HIGH depth patterns
    for pattern, weight in HIGH_DEPTH_PATTERNS:
        if pattern.search(message_lower):
            score += weight

    # Apply LOW depth patterns
    for pattern, weight in LOW_DEPTH_PATTERNS:
        if pattern.search(message_lower):
            score += weight  # weight is already negative

    # Apply booster patterns
    for pattern, weight in BOOSTER_PATTERNS:
        matches = pattern.findall(message_lower)
        if matches:
            # For numbered items, each match adds weight
            if r'\d' in pattern.pattern:
                score += weight * len(matches)
            else:
                score += weight

    # Clamp to [0.0, 1.0]
    score = max(0.0, min(1.0, score))

    # Derive level from score
    if score > 0.7:
        level = "comprehensive"
        instructions = COMPREHENSIVE_INSTRUCTIONS
    elif score < 0.3:
        level = "brief"
        instructions = BRIEF_INSTRUCTIONS
    else:
        level = "standard"
        instructions = ""

    return DepthResult(level=level, score=score, instructions=instructions)
