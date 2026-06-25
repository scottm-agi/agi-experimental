"""
Delegation Output Quality Gate — Signal-Based Validation

Issue: Cross-reference audit Gap #4 (P2)

Validates that a delegation result has sufficient SUBSTANCE to be considered
successful. This is a positive quality assertion — it checks FOR quality,
not just the ABSENCE of errors.

Architecture (2-layer):
    L1 (fast, deterministic): Signal keyword scan — does the result
        reference actionable artifacts (routes, files, components)?
        If clear signal → verdict immediately. If ambiguous → L2.
    L2 (structural analysis): Pattern-based inspection for actionable
        content — file paths, code blocks, URLs, structured sections.
        No LLM call; just deeper regex/structural analysis.

Unknown/undefined profiles pass unconditionally (no gate to apply).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger("agix.delegation_output_quality")


# ── Profile-Specific Signal Definitions ────────────────────────────
# L1 checks for these keywords. L2 checks for structural patterns.
# No length floors — a focused 50-char response with signals passes fine.
PROFILE_SIGNALS: Dict[str, Dict[str, Any]] = {
    "architect": {
        "l1_keywords": [
            "route", "page", "component", "tech", "stack", "schema",
            "database", "api", "layout", "section", "navigation",
            "file", "modify", "create", "update", "add", "implement",
            "endpoint", "model", "table", "auth",
        ],
        "min_keyword_hits": 2,
        "l2_patterns": [
            r"/[a-z][a-z0-9-]+",             # Route-like paths (/pricing, /dashboard)
            r"`[^`]+\.(tsx?|jsx?|py|css)`",   # File references in backticks
            r"#{2,3}\s+\w",                   # Markdown sections (## Heading)
            r"src/|app/|components/|pages/",   # Directory references
        ],
        "min_l2_hits": 1,
        "description": "Architecture guidance with actionable references",
    },
    "code": {
        "l1_keywords": [
            "file", "created", "modified", "import", "component",
            "function", "class", "export", "src/", "page.tsx",
            "added", "updated", "wrote", "implemented", "installed",
        ],
        "min_keyword_hits": 1,
        "l2_patterns": [
            r"`[^`]+\.(tsx?|jsx?|py|css|json)`",  # File references
            r"src/|app/|components/|lib/",          # Directory paths
            r"npm |yarn |pip |package\.json",       # Package operations
        ],
        "min_l2_hits": 1,
        "description": "Code changes with file or module references",
    },
    "e2e": {
        "l1_keywords": [
            "test", "pass", "fail", "screenshot", "quality",
            "verified", "route", "browser", "check", "QUALITY:",
            "assertion", "expected", "actual",
        ],
        "min_keyword_hits": 1,
        "l2_patterns": [
            r"(PASS|FAIL|pass|fail)",       # Test verdicts
            r"\d+/\d+",                      # Score ratios (5/5, 3/4)
            r"✅|❌|⚠️",                     # Status emojis
        ],
        "min_l2_hits": 1,
        "description": "Test results or quality evaluation with verdicts",
    },
    "researcher": {
        "l1_keywords": [
            "research", "finding", "analysis", "source", "documentation",
            "reference", "evidence", "recommend", "evaluate", "compare",
            "report", "survey", "data", "insight", "conclusion",
            "methodology", "literature", "review", "study", "assessment",
        ],
        "min_keyword_hits": 1,
        "l2_patterns": [
            r"https?://[^\s]+",              # URL references
            r"#{2,3}\s+\w",                  # Markdown sections (## Heading)
            r"\d+\.\s+\w",                   # Numbered list items
            r"(finding|result|conclusion|recommendation)s?:",  # Section labels
        ],
        "min_l2_hits": 1,
        "description": "Research findings with sources, evidence, or recommendations",
    },
    "content-writer": {
        "l1_keywords": [
            "content", "copy", "heading", "paragraph", "section",
            "title", "description", "text", "draft", "wrote",
            "landing", "hero", "cta", "tagline", "slogan",
            "tone", "brand", "message", "narrative", "storytelling",
        ],
        "min_keyword_hits": 1,
        "l2_patterns": [
            r"#{2,3}\s+\w",                  # Markdown sections (## Heading)
            r"\*\*[^*]+\*\*",                # Bold text (content emphasis)
            r"(heading|title|section|paragraph|copy):",  # Content labels
        ],
        "min_l2_hits": 1,
        "description": "Content copy with headings, sections, or structured text",
    },
    "frontend": {
        "l1_keywords": [
            "css", "style", "color", "layout", "design",
            "component", "responsive", "theme", "typography", "spacing",
            "breakpoint", "grid", "flexbox", "animation", "gradient",
            "mockup", "palette", "token", "font", "dark mode",
        ],
        "min_keyword_hits": 1,
        "l2_patterns": [
            r"--[a-z][\w-]+:",               # CSS custom properties (--color-primary:)
            r"#[0-9a-fA-F]{3,8}\b",          # Hex color values (#2563eb)
            r"\d+px|\d+rem|\d+em",            # Size values (16px, 1.5rem)
            r"#{2,3}\s+\w",                   # Markdown sections (## Heading)
            r"@media|@keyframes",             # CSS at-rules
        ],
        "min_l2_hits": 1,
        "description": "Frontend design output with CSS, layout, or visual design references",
    },
    "designer": {
        "l1_keywords": [
            "css", "style", "color", "layout", "design",
            "component", "responsive", "theme", "typography", "spacing",
            "breakpoint", "grid", "flexbox", "animation", "gradient",
            "mockup", "palette", "token", "font", "dark mode",
        ],
        "min_keyword_hits": 1,
        "l2_patterns": [
            r"--[a-z][\w-]+:",               # CSS custom properties (--color-primary:)
            r"#[0-9a-fA-F]{3,8}\b",          # Hex color values (#2563eb)
            r"\d+px|\d+rem|\d+em",            # Size values (16px, 1.5rem)
            r"#{2,3}\s+\w",                   # Markdown sections (## Heading)
            r"@media|@keyframes",             # CSS at-rules
        ],
        "min_l2_hits": 1,
        "description": "Visual design output with colors, typography, layout, or mockup references",
    },
}



def check_delegation_output_quality(
    result: str,
    profile: str,
    iterations: int,
) -> Dict[str, Any]:
    """2-layer quality check on delegation result substance.

    L1: Fast keyword scan — does the result reference real artifacts?
        Clear signal (>=min hits) → PASS immediately.
        Zero signals AND trivially short → FAIL immediately.
        Ambiguous → flow to L2.

    L2: Structural pattern analysis — regex scan for file paths,
        code blocks, route definitions, section headers.
        Any L2 hit → PASS. No hits → FAIL.

    Args:
        result: The raw result text from the subordinate.
        profile: Agent profile name (e.g., "architect", "code", "e2e").
        iterations: Number of monologue loop iterations used.

    Returns:
        dict with:
            passed: bool — True if result meets quality bar
            reason: str — explanation of pass/fail
            confidence: float — 0.0 to 1.0
    """
    if not profile or profile not in PROFILE_SIGNALS:
        return {
            "passed": True,
            "reason": f"No quality gate defined for profile '{profile}'",
            "confidence": 1.0,
        }

    signals = PROFILE_SIGNALS[profile]
    result_text = result or ""
    result_lower = result_text.lower()

    # ── Truly empty check (not a floor — literally nothing) ───────
    if not result_text.strip():
        return {
            "passed": False,
            "reason": f"Empty result from {profile} agent",
            "confidence": 0.0,
        }

    # ── Work evidence bypass (RCA-354 I-2) ────────────────────
    # If the subordinate ran many iterations, it clearly did substantial work.
    # The response text may be a prose summary rather than a file changelog.
    # Bypass L1/L2 keyword scanning when iteration count proves work was done.
    SUBSTANTIAL_WORK_THRESHOLD = 20
    if iterations >= SUBSTANTIAL_WORK_THRESHOLD:
        return {
            "passed": True,
            "reason": (
                f"Work evidence bypass: {iterations} iterations proves substantial "
                f"work was completed (threshold: {SUBSTANTIAL_WORK_THRESHOLD}). "
                f"Response text scanning skipped."
            ),
            "confidence": min(1.0, 0.7 + (iterations / 100)),
        }

    # ── L1: Signal Keyword Scan ───────────────────────────────────
    l1_keywords = signals["l1_keywords"]
    min_hits = signals["min_keyword_hits"]
    keyword_hits = sum(1 for kw in l1_keywords if kw.lower() in result_lower)

    if keyword_hits >= min_hits:
        # Clear signal — actionable content detected
        confidence = min(1.0, 0.6 + (keyword_hits / len(l1_keywords)))
        return {
            "passed": True,
            "reason": (
                f"L1 PASS: {keyword_hits}/{len(l1_keywords)} signal keywords "
                f"detected for {profile} profile"
            ),
            "confidence": round(confidence, 2),
        }

    # L1 found zero or insufficient signals — flow to L2
    l1_reason = (
        f"L1 inconclusive: {keyword_hits}/{len(l1_keywords)} signal keywords "
        f"(need {min_hits}) for {profile}"
    )

    # ── L2: Structural Pattern Analysis ───────────────────────────
    l2_patterns = signals["l2_patterns"]
    min_l2 = signals["min_l2_hits"]
    l2_hits = sum(
        1 for pat in l2_patterns
        if re.search(pat, result_text)
    )

    if l2_hits >= min_l2:
        # Structural evidence found — content is actionable despite
        # missing L1 keywords (e.g., used different terminology)
        confidence = min(1.0, 0.5 + (l2_hits * 0.15))
        return {
            "passed": True,
            "reason": (
                f"{l1_reason}; L2 PASS: {l2_hits} structural patterns detected "
                f"(file paths, routes, sections)"
            ),
            "confidence": round(confidence, 2),
        }

    # Both L1 and L2 found nothing actionable
    return {
        "passed": False,
        "reason": (
            f"{l1_reason}; L2 FAIL: 0 structural patterns detected. "
            f"Expected: {signals['description']}"
        ),
        "confidence": round(max(0.0, keyword_hits * 0.1), 2),
    }
