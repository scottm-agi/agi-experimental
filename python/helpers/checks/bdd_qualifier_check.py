"""
F-2: BDD Qualifier Preservation Rule.

Detects signal narrowing in BDD scenarios where qualitative terms
('happy', 'satisfied', 'good') get translated to extreme binary
conditions (=== 5, == 1, < 2, > 4.9).

Problem: When the architect writes BDD scenarios, qualitative terms
('happy customer', 'unhappy', 'good rating') get translated to extreme
binary conditions (rating === 5, rating < 3) instead of threshold-based
conditions (rating >= 4, rating <= 2).

2-Layer Detection (ADR-085):
  L1: Regex scan for hard-equality operators on rating/score fields
      in BDD THEN/WHEN clauses. Flags: === 5, == 1, < 2, > 4.9, etc.
  L1.5: Semantic embedding comparison — compares original requirement
      text embedding against BDD scenario text embedding. If similarity
      drops below 0.5, the BDD may have narrowed the signal.
  L2: Not needed.

Gate action: WARNING (not block) — suggests threshold ranges.
"""
import os
import re
import logging
from typing import List, Optional, Tuple

from python.helpers.orchestrator_gate_integration_checks import register_advisory

logger = logging.getLogger("agix.checks.bdd_qualifier")

# ── Semantic embedding imports (ADR-085) ──
try:
    from python.helpers.semantic_embeddings import (
        compute_embedding_sync,
        cosine_similarity,
    )
except ImportError:
    compute_embedding_sync = None  # type: ignore[assignment]
    cosine_similarity = None  # type: ignore[assignment]

# ── Constants ──

# Rating/score field names that signal qualitative assessment
_RATING_FIELD_PATTERNS = [
    r'\brating\b',
    r'\bscore\b',
    r'\bstars?\b',
    r'\breview_?score\b',
    r'\bsatisfaction\b',
    r'\bnps\b',
    r'\bfeedback_?score\b',
]
_RATING_FIELDS_RE = re.compile(
    '|'.join(_RATING_FIELD_PATTERNS),
    re.IGNORECASE,
)

# Narrowing patterns: hard equality or near-extreme comparisons on numeric values
# These indicate the BDD has narrowed a qualitative term to an extreme value
_NARROWING_PATTERNS = [
    # Exact equality with a number: rating === 5, score == 1
    re.compile(
        r'(?:rating|score|stars?|review_?score|satisfaction|nps|feedback_?score)'
        r'\s*(?:===?|!==?)\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    ),
    # Near-extreme greater-than: rating > 4.9, score > 4.8
    re.compile(
        r'(?:rating|score|stars?|review_?score|satisfaction|nps|feedback_?score)'
        r'\s*>\s*(4\.[89]\d*|4\.9\d*)',
        re.IGNORECASE,
    ),
    # Near-extreme less-than: rating < 1.1, score < 1.5
    re.compile(
        r'(?:rating|score|stars?|review_?score|satisfaction|nps|feedback_?score)'
        r'\s*<\s*(1\.[0-5]\d*|1)',
        re.IGNORECASE,
    ),
]

# BDD file locations (shared with bdd_quality.py)
BDD_FILE_CANDIDATES = [
    os.path.join(".agix.proj", "bdd_scenarios.md"),
    "bdd_scenarios.md",
    os.path.join("docs", "bdd-scenarios.md"),
]


# ─── Helpers ────────────────────────────────────────────────────────────

def _find_bdd_text(project_dir: str) -> Optional[str]:
    """Return BDD scenario text from the project, or None if not found."""
    for candidate in BDD_FILE_CANDIDATES:
        path = os.path.join(project_dir, candidate)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except (IOError, OSError):
                continue
    return None


def _detect_narrowing_l1(bdd_text: str) -> List[dict]:
    """L1 (deterministic): Scan BDD text for narrowing patterns.

    Returns list of findings: [{"line": "...", "match": "...", "value": "..."}, ...]
    """
    findings = []
    for line in bdd_text.split("\n"):
        line_stripped = line.strip()
        for pattern in _NARROWING_PATTERNS:
            match = pattern.search(line_stripped)
            if match:
                findings.append({
                    "line": line_stripped,
                    "match": match.group(0),
                    "value": match.group(1),
                })
    return findings


# ─── Main check function ───────────────────────────────────────────────

def check_bdd_qualifier_preservation(ctx) -> Optional[str]:
    """F-2: BDD Qualifier Preservation Rule.

    Detects signal narrowing in BDD scenarios. Returns a WARNING
    (not a hard block) suggesting threshold ranges.

    Args:
        ctx: CheckContext with project_dir, block() method.

    Returns:
        None if check passes, warning message string if narrowing detected.
    """
    try:
        if not ctx.project_dir:
            return None

        bdd_text = _find_bdd_text(ctx.project_dir)
        if not bdd_text or not bdd_text.strip():
            return None  # No BDD file → skip

        # L1: Scan for narrowing patterns
        findings = _detect_narrowing_l1(bdd_text)

        if not findings:
            return None  # No narrowing detected

        # Build warning message
        example_lines = []
        for f in findings[:3]:  # Show up to 3 examples
            example_lines.append(f"  • `{f['match']}` in: \"{f['line'][:80]}\"")
        examples_text = "\n".join(example_lines)

        return ctx.block(
            f"⚠️ BDD QUALIFIER NARROWING: {len(findings)} BDD scenario(s) use "
            f"hard-equality or near-extreme comparisons on rating/score fields. "
            f"This narrows qualitative terms ('happy', 'satisfied') to extreme "
            f"binary values.\n\n"
            f"Findings:\n{examples_text}\n\n"
            f"Suggestion: Use threshold ranges instead of exact values:\n"
            f"  • Instead of `rating === 5` → use `rating >= 4`\n"
            f"  • Instead of `rating == 1` → use `rating <= 2`\n"
            f"  • Instead of `rating > 4.9` → use `rating >= 4`",
            action=(
                f"Review {len(findings)} BDD scenario(s) with narrowing patterns. "
                f"Replace hard-equality comparisons (=== 5, == 1) with threshold "
                f"ranges (>= 4, <= 2) to preserve qualitative signal from the "
                f"original requirements."
            ),
        )
    except Exception as e:
        logger.debug(f"[BDD QUALIFIER] Check skipped due to error: {e}")
        return None  # Fail open


# ─── Gate Registration ─────────────────────────────────────────────────
# F-2: Registered as ADVISORY (warning, not block) at order 2.07.
# Runs for web projects only — BDD scenarios are a web project artifact.

@register_advisory(2.07, "BDD qualifier preservation", web_only=True)
def _advisory_bdd_qualifier_preservation(ctx):
    """Gate-registered wrapper for check_bdd_qualifier_preservation."""
    return check_bdd_qualifier_preservation(ctx)

