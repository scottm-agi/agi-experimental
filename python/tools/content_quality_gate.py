"""
Content Quality Gate — score deliverable content across 5 dimensions.

Scoring dimensions:
- structure:     Headings, tables, lists, visual hierarchy
- depth:         Word count, section depth, paragraph density
- evidence:      Data points, citations, statistics, source references
- actionability: Action items, recommendations, timelines, next steps
- specificity:   Named entities, dollar figures, percentages, dates

Grade thresholds: A (85+), B (70+), C (55+), D (<55)
"""

from __future__ import annotations

import json
import re
from typing import Any

from python.helpers.tool import Tool, Response


# ─── Pure function for testability ──────────────────────────────────────


def score_content(content: str) -> dict:
    """Score content quality across 5 dimensions.

    Returns:
        {
            "total_score": int (0-100),
            "grade": str (A/B/C/D),
            "pass": bool (True if grade >= B),
            "dimensions": {
                "structure": int,
                "depth": int,
                "evidence": int,
                "actionability": int,
                "specificity": int,
            },
            "issues": [str],
            "recommendations": [str],
        }
    """
    if not content or not content.strip():
        return {
            "total_score": 0,
            "grade": "D",
            "pass": False,
            "dimensions": {
                "structure": 0,
                "depth": 0,
                "evidence": 0,
                "actionability": 0,
                "specificity": 0,
            },
            "issues": ["Content is empty"],
            "recommendations": ["Provide substantive content with data, structure, and recommendations"],
        }

    dims = {
        "structure": _score_structure(content),
        "depth": _score_depth(content),
        "evidence": _score_evidence(content),
        "actionability": _score_actionability(content),
        "specificity": _score_specificity(content),
    }

    total = sum(dims.values()) // len(dims)
    grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D"

    issues = []
    recommendations = []
    for name, score in dims.items():
        if score < 50:
            issues.append(f"{name} is weak ({score}/100)")
            recommendations.append(_RECS.get(name, f"Improve {name}"))

    return {
        "total_score": total,
        "grade": grade,
        "pass": total >= 70,
        "dimensions": dims,
        "issues": issues,
        "recommendations": recommendations,
    }


# ─── Dimension scorers ──────────────────────────────────────────────────

def _score_structure(content: str) -> int:
    """Score 0-100 based on heading hierarchy, tables, lists."""
    h1 = len(re.findall(r"^# ", content, re.MULTILINE))
    h2 = len(re.findall(r"^## ", content, re.MULTILINE))
    h3 = len(re.findall(r"^### ", content, re.MULTILINE))
    tables = content.count("|---|")
    lists = len(re.findall(r"^\s*[-*]\s", content, re.MULTILINE))
    numbered = len(re.findall(r"^\s*\d+\.\s", content, re.MULTILINE))

    score = (h1 * 5 + h2 * 10 + h3 * 8 + tables * 15 + lists * 2 + numbered * 3)
    return min(100, score)


def _score_depth(content: str) -> int:
    """Score 0-100 based on word count, paragraph count, section depth."""
    words = len(content.split())
    paragraphs = len([p for p in content.split("\n\n") if p.strip()])
    sections = len(re.findall(r"^#{1,3} ", content, re.MULTILINE))

    # Word count component (0-50 points)
    word_score = min(50, words // 30)

    # Paragraph density component (0-25 points)
    para_score = min(25, paragraphs * 3)

    # Section count component (0-25 points)
    section_score = min(25, sections * 5)

    return min(100, word_score + para_score + section_score)


def _score_evidence(content: str) -> int:
    """Score 0-100 based on citations, data references, source mentions."""
    # Numeric data points ($X, N%, Nx)
    dollar_figures = len(re.findall(r"\$[\d,]+(?:\.\d+)?[BMKk]?", content))
    percentages = len(re.findall(r"\d+(?:\.\d+)?%", content))
    multipliers = len(re.findall(r"\d+(?:\.\d+)?[xX]\b", content))

    # Citation patterns
    citations = len(re.findall(
        r"(?:according to|source:|per |reported by|study|research|survey|gartner|forrester|mckinsey|IDC)",
        content, re.IGNORECASE,
    ))

    # Named numeric claims (e.g., "$2.4M ARR", "87% probability")
    specific_claims = len(re.findall(r"\d+\s*(?:month|year|week|day|hour|quarter)", content, re.IGNORECASE))

    score = (
        dollar_figures * 8
        + percentages * 6
        + multipliers * 7
        + citations * 12
        + specific_claims * 5
    )
    return min(100, score)


def _score_actionability(content: str) -> int:
    """Score 0-100 based on action items, recommendations, timelines."""
    # Action verbs at start of bullets
    action_items = len(re.findall(
        r"^\s*[-*\d.]+\s*(?:Schedule|Deliver|Conduct|Present|Target|Send|Build|Create|Develop|Launch|Deploy|Implement|Review|Prepare|Complete|Draft|Finalize|Publish|Submit|Negotiate|Follow)",
        content, re.MULTILINE | re.IGNORECASE,
    ))

    # Timeline references
    timelines = len(re.findall(
        r"(?:by\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)|\d{1,2}/\d{1,2}|Q[1-4]|sprint|week\s*\d|day\s*\d|30[/-]60[/-]90|timeline|milestone|deadline)",
        content, re.IGNORECASE,
    ))

    # Recommendation language
    recommendations = len(re.findall(
        r"(?:recommend|should|must|next step|action item|priority|deliverable|key takeaway)",
        content, re.IGNORECASE,
    ))

    score = (action_items * 8 + timelines * 10 + recommendations * 6)
    return min(100, score)


def _score_specificity(content: str) -> int:
    """Score 0-100 based on named entities, specific claims, concrete details."""
    words = len(content.split())
    if words == 0:
        return 0

    # Named entities (capitalized multi-word names)
    named_entities = len(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", content))

    # Specific dollar/number values
    numbers = len(re.findall(r"\$?[\d,]+(?:\.\d+)?[BMK%]?", content))

    # Company/product names (capitalized words followed by common business suffixes)
    companies = len(re.findall(r"\b(?:Inc|Corp|LLC|Ltd|Co|Group|Platform|Solutions)\b", content, re.IGNORECASE))

    # Specific role titles
    roles = len(re.findall(r"\b(?:CEO|CTO|CFO|CIO|VP|Director|Manager|SVP|EVP)\b", content))

    density = (named_entities + numbers + companies + roles) / (words / 100)
    return min(100, int(density * 10))


# ─── Recommendations map ────────────────────────────────────────────────

_RECS = {
    "structure": "Add H2/H3 headings, tables for comparisons, and bulleted lists for key points",
    "depth": "Expand sections with analysis, examples, and detailed explanations (target 1500+ words)",
    "evidence": "Include specific data points ($, %, Nx), cite sources (Gartner, Forrester), add case study metrics",
    "actionability": "Add numbered action items with owners and deadlines, include 30/60/90 day timeline",
    "specificity": "Replace generic language with specific names, numbers, dates, and company references",
}


# ─── Tool Class ──────────────────────────────────────────────────────────


class ContentQualityGate(Tool):
    """Score deliverable content quality across 5 dimensions.

    Use BEFORE final delivery to ensure content meets executive standards.
    Grade A (85+) = publication-ready, B (70+) = acceptable, C/D = needs rework.
    """

    async def execute(self, **kwargs) -> Response:
        content = self.args.get("content", "")
        doc_type = self.args.get("doc_type", "general")

        if not content or not content.strip():
            return Response(
                message="Error: No content provided to score.",
                break_loop=False,
            )

        result = score_content(content)

        return Response(
            message=json.dumps(result, indent=2),
            break_loop=False,
        )
