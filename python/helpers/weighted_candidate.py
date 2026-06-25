"""
WeightedCandidate — Confidence-scored extraction signals for the Hybrid Pipeline.

Extends the deterministic LineItem with confidence scoring and structural
metadata so that regex extraction signals can feed into the LLM classifier
and Layer 3 validator.

Part of RCA-340 Phase 2: Married Pipeline Architecture.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("agix.weighted_candidate")


@dataclass
class WeightedCandidate:
    """A confidence-scored extraction signal from the deterministic regex scanner.

    Attributes:
        text: The raw matched text from the prompt.
        category: Inferred category (url, integration, page, feature, config, copy, deployment).
        confidence: 0.0–1.0 — how confident the regex match is.
        source_line: Line number in the original prompt (for traceability).
        structural_markers: Metadata about the match context, e.g.:
            {"is_bullet": True, "indent_depth": 1}
            {"is_header": True, "header_level": 2}
            {"is_checklist": True, "checked": False}
            {"is_url": True}
            {"near_keywords": ["dashboard", "page"]}
        qualifier: Optional priority qualifier (e.g., 'must-have', 'nice-to-have').
                   Parsed from checklist markers like 'MUST:', 'NICE-TO-HAVE:'.
    """
    text: str
    category: str
    confidence: float
    source_line: int
    structural_markers: Dict = field(default_factory=dict)
    qualifier: Optional[str] = None

    @classmethod
    def from_line_item(cls, line_item, confidence: float = 0.5,
                       structural_markers: Optional[Dict] = None) -> "WeightedCandidate":
        """Convert a LineItem to a WeightedCandidate.

        Args:
            line_item: A LineItem instance from prompt_line_item_extractor.
            confidence: Confidence score to assign.
            structural_markers: Optional structural metadata.
        """
        return cls(
            text=line_item.text,
            category=line_item.category,
            confidence=confidence,
            source_line=line_item.source_line,
            structural_markers=structural_markers or {},
        )

    def to_line_item(self, item_id: str):
        """Convert back to a LineItem (for backward-compat merge into ledger).

        Args:
            item_id: The LineItem ID to assign (e.g., "LI-042").
        """
        from python.helpers.prompt_line_item_extractor import LineItem
        return LineItem(
            id=item_id,
            text=self.text,
            category=self.category,
            source_line=self.source_line,
        )


def format_signal_annotations(prompt: str, candidates: List[WeightedCandidate]) -> str:
    """Format a prompt with regex signal annotations for LLM consumption.

    Produces a marked-up version of the prompt where regex-identified
    candidates are highlighted with their confidence scores and categories.
    Includes a summary section at the end listing all signals.

    Args:
        prompt: The raw user prompt text.
        candidates: List of WeightedCandidate objects from Layer 1.

    Returns:
        The annotated prompt string with [REGEX_SIGNAL] markers
        and a [REGEX_SIGNALS_SUMMARY] section.
    """
    if not candidates:
        return prompt

    # Build per-line annotation map
    # Group candidates by source_line for efficient annotation
    line_annotations: Dict[int, List[WeightedCandidate]] = {}
    for c in candidates:
        line_annotations.setdefault(c.source_line, []).append(c)

    # Annotate each line that has candidates
    lines = prompt.split("\n")
    annotated_lines = []
    for i, line in enumerate(lines):
        if i in line_annotations:
            for c in line_annotations[i]:
                annotation = (
                    f"[REGEX_SIGNAL confidence={c.confidence} "
                    f"category={c.category}] "
                    f"{c.text[:120]} [/REGEX_SIGNAL]"
                )
                annotated_lines.append(annotation)
        annotated_lines.append(line)

    # Add summary section
    annotated_lines.append("")
    annotated_lines.append("[REGEX_SIGNALS_SUMMARY]")
    annotated_lines.append(f"Total signals: {len(candidates)}")

    # Group by category
    category_counts: Dict[str, int] = {}
    for c in candidates:
        category_counts[c.category] = category_counts.get(c.category, 0) + 1
    for cat, count in sorted(category_counts.items()):
        annotated_lines.append(f"  {cat}: {count}")

    # High-confidence items (>= 0.85) — these should be accepted directly
    high_conf = [c for c in candidates if c.confidence >= 0.85]
    if high_conf:
        annotated_lines.append(f"High-confidence (≥0.85): {len(high_conf)} items")
        for c in high_conf:
            annotated_lines.append(f"  ✓ [{c.category}] {c.text[:80]}")

    # Low-confidence items (< 0.7) — these need LLM classification
    low_conf = [c for c in candidates if c.confidence < 0.7]
    if low_conf:
        annotated_lines.append(f"Needs classification (<0.70): {len(low_conf)} items")
        for c in low_conf:
            annotated_lines.append(f"  ? [{c.category}] {c.text[:80]}")

    annotated_lines.append("[/REGEX_SIGNALS_SUMMARY]")

    return "\n".join(annotated_lines)
